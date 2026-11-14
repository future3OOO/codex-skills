"""The Python conditions a candidate removes, and the recorded cases that settle each one."""
from __future__ import annotations

import ast
import contextlib
import difflib
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import behavior_map
from .repo_identity import RepoIdentity
from .state_store import _git, blob_reader, is_test_path

JsonObject = dict[str, object]


def _functions(source: str) -> dict[str, ast.AST]:
    """Each function definition by its runtime qualified name; an unparsable source has none."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                continue
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
    candidate counterpart has as many operands lost none: its missing operands were rewritten in place, and a
    partly kept group among them is compared the same way. Such a nested group's counterpart is a candidate group
    of its operator that shares one of its operands, is not itself kept whole and is no other group's counterpart;
    without one, the operands it lost are removed. An operand the original repeats more often than that decision
    pairs no single occurrence and is not measured. An unchanged function AST lost none, even if its new layout
    aligns otherwise equal decisions differently."""
    candidates, tests = ({node: ast.unparse(node) for node in _decisions(function)} for function in (new, old))
    found = []
    for test, whole in tests.items():
        lines = set().union(*(aligned.get(line, set()) for line in range(test.lineno, test.end_lineno + 1)))
        if any(other.lineno in lines and text == whole for other, text in candidates.items()):
            continue  # unchanged
        shared = _operands(test)
        match = max((other for other in candidates if other.lineno in lines), default=None,
                    key=lambda other: len(_operands(other) & shared))
        paired = _operands(match) if match is not None else Counter()
        repeated = {text for text, count in shared.items() if count > max(1, paired[text])}
        kept = set(paired & shared)
        if match is None or not kept or whole in kept:
            continue  # unchanged, or no candidate decision to compare an evaluation with
        groups = [node for node in ast.walk(test) if isinstance(node, ast.BoolOp)]
        nested = {id(inner) for group in groups for value in group.values for inner in ast.walk(value)}
        pending, removed, paired = [group for group in groups if id(group) not in nested], {}, set()
        while pending:
            group = pending.pop()
            texts = {ast.unparse(value) for value in group.values}
            shared_with = lambda other: len(texts & {ast.unparse(value) for value in other.values})
            peer = max((other for other in ast.walk(match) if isinstance(other, ast.BoolOp) and type(other.op) is type(group.op)
                        and (id(group) not in nested or id(other) not in paired and ast.unparse(other) not in kept and shared_with(other))),
                       key=shared_with, default=None)
            paired.add(id(peer))
            rewritten = peer is not None and len(peer.values) >= len(group.values)
            for operand in group.values:
                inner = operand.operand if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.Not) else operand
                text = ast.unparse(operand)
                if text in kept or text in repeated:
                    continue
                if isinstance(inner, ast.BoolOp) and any(ast.unparse(value) in kept for value in inner.values):
                    pending.append(inner)
                elif not rewritten:
                    removed[text] = {"operand": text, "line": operand.lineno, "column": operand.col_offset, "value": isinstance(group.op, ast.Or)}
        if removed and not any(isinstance(node, ast.Compare) and len(node.ops) > 1 for node in ast.walk(test)):
            found.append({"id": f"{path}:{test.lineno}:{test.col_offset}", "file": path, "qualname": qualname,
                          "test": whole, "match": candidates[match], "removed": list(removed.values()),
                          "repeated": sorted(repeated),
                          "span": [test.lineno, test.end_lineno, test.col_offset, test.end_col_offset],
                          "names": sorted({node.id for node in ast.walk(test) if isinstance(node, ast.Name)}),
                          "nodes": [[node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(node),
                                     isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Is)]
                                    for node in ast.walk(test) if isinstance(node, ast.expr)]})
    if found and list(tests.values()) == list(candidates.values()) and ast.dump(old) == ast.dump(new):
        return []  # layout alone can misalign equal decisions
    return found


@lru_cache(maxsize=8)
def removed(identity: RepoIdentity, original: str, candidate: str) -> list[JsonObject]:
    """The original's decisions, in non-test Python files, that lose an operand a traced evaluation can judge."""
    decisions: list[JsonObject] = []
    raw = _git(identity, "diff", "--raw", "--no-abbrev", "--diff-filter=M", "-z", original, candidate, "--", "*.py").split(b"\0")
    changed = [(path, header.split()[2:4]) for header, raw_path in zip(raw[::2], raw[1::2]) if not is_test_path(path := os.fsdecode(raw_path))]
    with blob_reader(Path(identity.root)) if changed else contextlib.nullcontext() as read:
        for path, oids in changed:  # one file pair held at a time
            blobs = [read(oid.decode()) for oid in oids]
            if None in blobs:
                raise RuntimeError(f"source of {path} unavailable")
            before, after = (blob.decode("utf-8", errors="replace") for blob in blobs)
            old, new = _functions(before), _functions(after)
            lines = re.split(r"\r\n|\r|\n", before), re.split(r"\r\n|\r|\n", after)  # as Python numbers lines
            source = lambda side, node: lines[side][min(part.lineno for part in (node, *node.decorator_list)) - 1:node.end_lineno]
            aligned: dict[int, set[int]] = {}
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, *lines, autojunk=False).get_opcodes():
                for line in range(i1, i2):
                    aligned[line + 1] = {j1 + 1 + line - i1} if tag == "equal" else set(range(j1 + 1, j2 + 1))
            for name in old.keys() & new.keys():
                # A function whose source lines, decorators included, are the same on both sides is the same function; in the others,
                # _removed passes over each decision the candidate keeps as it is.
                if source(0, old[name]) != source(1, new[name]):
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


def clears(case: JsonObject, approved: list[JsonObject]) -> bool:
    """Whether a case that decides a removed operand clears it: it passes on the original, where its own line holds the
    candidate to that result, or its item is a change the approved preflight states (`approved`, that preflight's items),
    with the same behavior, Seam and expected result. Any other is a change declared after preflight."""
    return behavior_map.original_outcome(case["comparison"]) == "passed" or any(
        item["id"] == case["id"] and item.get("kind") != "preservation" and behavior_map.obligation(item)[1:] == behavior_map.obligation(case)[1:]
        for item in approved)


def unsettled(decisions: list[JsonObject], traces: list[JsonObject | None], cases: list[JsonObject],
              approved: list[JsonObject], rejected: set[str] | None = None) -> tuple[list[str], list[str]]:
    """The open lines of the removed operands no recorded case clears: those no case decides, then those only uncleared
    cases decide. A case decides an operand when its original run evaluates it at its deciding value and the paired
    candidate decision, on the values that run observed, is not known to agree with the original; it clears it by `clears`.
    `traces` holds the evaluations of each of `cases` by decision id; None is a run the tracer could not observe; a case
    not yet traced has no evaluations. An operand no case decides opens no line when every run was unobservable. One only
    uncleared cases decide is a change declared after preflight, which settles nothing. `rejected` is None before the
    pass's first final verdict; after it, the lines the lead rejected, by FILE:LINE:COLUMN of the operand, stay closed
    and an open line names its own."""
    undecided, declared = [], []
    cut = lambda text: text if len(text) <= 160 else text[:159] + "…"
    for decision in decisions:
        test, match = (ast.parse(str(decision[key]), mode="eval").body for key in ("test", "match"))
        spans = {tuple(node[:4]): (node[4], node[5]) for node in decision["nodes"]}
        for operand in decision["removed"]:
            name = f"{decision['file']}:{operand['line']}:{operand['column']}"
            if name in (rejected or ()):
                continue
            node, deciders = ast.parse(str(operand["operand"]), mode="eval").body, []
            for case, trace in zip(cases, traces):
                for evaluation in (trace or {}).get(decision["id"]) or ():
                    known = dict(evaluation["names"])
                    for *span, kind, jumped in evaluation["jumps"]:
                        text, is_none = spans.get(tuple(span), (None, False))
                        if text is not None:
                            known[text] = jumped is (kind == "true") if kind in ("true", "false") else (jumped is (kind == "none")) is is_none
                    before = _truth(test, known)
                    if _truth(node, known) is operand["value"] and (before is None or before is not _truth(match, known)):
                        break
                else:
                    continue  # this case does not decide it
                if clears(case, approved):
                    break
                deciders.append(str(case["id"]))
            else:
                where = f"it {str(operand['value']).lower()} on the original where the candidate's `{cut(decision['match'])}` is not known to decide the same"
                line = (f"removed condition {decision['file']}:{operand['line']} `{cut(operand['operand'])}`: the change assumes that removing it loses "
                        "no result the request keeps. " + (f"Only {', '.join(dict.fromkeys(deciders))}, declared after the approved preflight, makes {where}, "
                        "which settles nothing. " if deciders else "") + f"One case settles that: its input makes {where} (no other input counts) and it "
                        "asserts the result on both versions. Passing on both, it closes this line; passing on the original and failing on the candidate, "
                        "it shows a kept result lost: the premise is wrong and the condition goes back." + ("" if rejected is None else
                        " This line opened during a repair: such a lost result is the reason to reject the finding that asked for the removal; where no "
                        f"input can settle the line, --reject {name} 'COMMAND' closes it with that command's recorded output."))
                if deciders:
                    declared.append(line)
                elif not (traces and all(trace is None for trace in traces)):
                    undecided.append(line)
    return undecided, declared
