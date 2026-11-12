"""The Python conditions a candidate removes, and whether a recorded case decides each one on the original."""
from __future__ import annotations

import ast
import difflib
import os
from collections import Counter
from functools import lru_cache

from .repo_identity import RepoIdentity
from .state_store import _git, is_test_path

JsonObject = dict[str, object]


def _functions(source: str) -> dict[str, ast.AST]:
    """Each function definition by its runtime qualified name; an unparsable source has none."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(child, ast.ClassDef):
                    found[prefix + child.name] = child
                visit(child, prefix + child.name + ("." if isinstance(child, ast.ClassDef) else ".<locals>."))
            else:
                visit(child, prefix)
    try:
        visit(ast.parse(source), "")
    except (SyntaxError, ValueError):
        return {}
    return found


def _decisions(function: ast.AST) -> list[ast.expr]:
    """The decisions of the function's own code object; nested definitions and generators are their own."""
    found, stack = [], list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.GeneratorExp)):
            continue
        found += [node.test] if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)) else (
            node.ifs if isinstance(node, ast.comprehension) else [])
        stack.extend(ast.iter_child_nodes(node))
    return found


def _operands(test: ast.expr) -> Counter[str]:
    found = Counter([ast.unparse(test)])
    for node in ast.walk(test):
        if isinstance(node, ast.BoolOp):
            found.update(ast.unparse(value) for value in node.values)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            found.update([ast.unparse(node.operand)])
    return found


def _removed(path: str, qualname: str, aligned: dict[int, set[int]], old: ast.AST, new: ast.AST) -> list[JsonObject]:
    """Each original decision losing an operand. It pairs with the candidate decision, on the lines the diff
    aligns with its own, that shares most operands; an operand that decision no longer contains, outside a
    partly kept group, is removed and decides its group at False under `and`, True under `or`. A group whose
    candidate counterpart has as many operands lost none: its missing operands were rewritten in place. An operand
    the original repeats more often than that decision pairs no single occurrence and is not measured."""
    candidates = _decisions(new)
    found = []
    for test in _decisions(old):
        lines = set().union(*(aligned.get(line, set()) for line in range(test.lineno, test.end_lineno + 1)))
        shared = _operands(test)
        match = max((other for other in candidates if other.lineno in lines), default=None,
                    key=lambda other: len(_operands(other) & shared))
        paired = _operands(match) if match is not None else Counter()
        repeated = {text for text, count in shared.items() if count > max(1, paired[text])}
        kept = set(paired & shared)
        if match is None or not kept or ast.unparse(test) in kept:
            continue  # unchanged, or no candidate decision to compare an evaluation with
        groups = [node for node in ast.walk(test) if isinstance(node, ast.BoolOp)]
        nested = {id(inner) for group in groups for value in group.values for inner in ast.walk(value)}
        pending, removed = [group for group in groups if id(group) not in nested], {}
        while pending:
            group = pending.pop()
            texts = {ast.unparse(value) for value in group.values}
            peer = max((other for other in ast.walk(match) if isinstance(other, ast.BoolOp) and type(other.op) is type(group.op)),
                       key=lambda other: len(texts & {ast.unparse(value) for value in other.values}), default=None)
            if peer is not None and len(peer.values) >= len(group.values):
                continue
            for operand in group.values:
                inner = operand.operand if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.Not) else operand
                text = ast.unparse(operand)
                if text in kept or text in repeated:
                    continue
                if isinstance(inner, ast.BoolOp) and any(ast.unparse(value) in kept for value in inner.values):
                    pending.append(inner)
                else:
                    removed[text] = {"operand": text, "line": operand.lineno, "value": isinstance(group.op, ast.Or)}
        if removed and not any(isinstance(node, ast.Compare) and len(node.ops) > 1 for node in ast.walk(test)):
            found.append({"id": f"{path}:{test.lineno}:{test.col_offset}", "file": path, "qualname": qualname,
                          "test": ast.unparse(test), "match": ast.unparse(match), "removed": list(removed.values()),
                          "repeated": sorted(repeated),
                          "span": [test.lineno, test.end_lineno, test.col_offset, test.end_col_offset],
                          "names": sorted({node.id for node in ast.walk(test) if isinstance(node, ast.Name)}),
                          "nodes": [[node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(node),
                                     isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Is)]
                                    for node in ast.walk(test) if isinstance(node, ast.expr)]})
    return found


@lru_cache(maxsize=8)
def removed(identity: RepoIdentity, original: str, candidate: str) -> list[JsonObject]:
    """The original's decisions, in non-test Python files, that lose an operand a traced evaluation can judge."""
    decisions: list[JsonObject] = []
    for raw in _git(identity, "diff", "--name-only", "--diff-filter=M", "-z", original, candidate, "--", "*.py").split(b"\0"):
        if not raw or is_test_path(path := os.fsdecode(raw)):
            continue
        before, after = (_git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
                         for tree in (original, candidate))
        old, new = _functions(before), _functions(after)
        aligned: dict[int, set[int]] = {}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).get_opcodes():
            for line in range(i1, i2):
                aligned[line + 1] = {j1 + 1 + line - i1} if tag == "equal" else set(range(j1 + 1, j2 + 1))
        for name in old.keys() & new.keys():
            if ast.dump(old[name]) != ast.dump(new[name]):
                decisions += _removed(path, name, aligned, old[name], new[name])
    return decisions


def _truth(node: ast.expr, known: dict[str, bool]) -> bool | None:
    if (text := ast.unparse(node)) in known:
        return known[text]
    if isinstance(node, ast.BoolOp):
        values = [_truth(value, known) for value in node.values]
        dominant = isinstance(node.op, ast.Or)
        return dominant if dominant in values else None if None in values else not dominant
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _truth(node.operand, known)
        return None if value is None else not value
    return None


def undecided(decisions: list[JsonObject], traces: list[JsonObject | None]) -> list[str]:
    """One line per removed operand no recorded case decides. A case decides it when its original run evaluates it
    at its deciding value and the paired candidate decision, on the values that run observed, is not known to agree
    with the original. `traces` holds each recorded case's evaluations by decision id; None is a run the tracer
    could not observe, and such runs alone open nothing; a case not yet traced has no evaluations."""
    lines, cut = [], lambda text: text if len(text) <= 160 else text[:159] + "…"
    for decision in decisions:
        test, match = (ast.parse(str(decision[key]), mode="eval").body for key in ("test", "match"))
        spans = {tuple(node[:4]): (node[4], node[5]) for node in decision["nodes"]}
        for operand in decision["removed"]:
            node, decided = ast.parse(str(operand["operand"]), mode="eval").body, False
            for evaluation in (found for trace in traces for found in (trace or {}).get(decision["id"]) or ()):
                known = dict(evaluation["names"])
                for *span, kind, jumped in evaluation["jumps"]:
                    text, is_none = spans.get(tuple(span), (None, False))
                    if text is not None:
                        known[text] = jumped is (kind == "true") if kind in ("true", "false") else (jumped is (kind == "none")) is is_none
                before = _truth(test, known)
                if _truth(node, known) is operand["value"] and (before is None or before is not _truth(match, known)):
                    decided = True
                    break
            if not decided and not (traces and all(trace is None for trace in traces)):
                lines.append(f"removed condition {decision['file']}:{operand['line']} `{cut(operand['operand'])}`: no recorded case makes it "
                             f"{str(operand['value']).lower()} on the original where the candidate's `{cut(decision['match'])}` then decides differently; "
                             "reaching it otherwise does not count. Restore it, or record a case with such an input: workflow.py tdd --case ITEM 'COMMAND'")
    return lines
