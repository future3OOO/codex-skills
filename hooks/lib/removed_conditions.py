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
            names = [{node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} for tree in (test, match)]
            parameters = [{arg.arg for arg in ast.iter_child_nodes(function.args) if isinstance(arg, ast.arg)}
                          for function in (old, new)]
            found.append({"id": f"{path}:{test.lineno}:{test.col_offset}", "file": path, "qualname": qualname,
                          "test": whole, "match": candidates[match], "removed": list(removed.values()),
                          "repeated": sorted(repeated), "names": sorted(names[0] | names[1]),
                          "inputs": sorted((names[0] & names[1]) | ((names[0] | names[1]) & parameters[0] & parameters[1])),
                          "sides": [{"id": f"{path}:{test.lineno}:{test.col_offset}:{side}", "file": path, "qualname": qualname,
                                     "span": [expr.lineno, expr.end_lineno, expr.col_offset, expr.end_col_offset],
                                     "names": sorted(names[side] | parameters[side]),
                                     "nodes": [[node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(node),
                                                isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Is)]
                                               for node in ast.walk(expr) if isinstance(node, ast.expr)]}
                                    for side, expr in enumerate((test, match))]})
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
    """Whether a case that decides a removed operand clears it: it passes on both versions, or its item is a change the
    approved preflight states (`approved`, that preflight's items), with the same behavior, Seam and expected result."""
    return behavior_map.original_outcome(case["comparison"]) == "passed" == case["comparison"]["arms"][-1]["outcome"] or any(
        item["id"] == case["id"] and item.get("kind") != "preservation" and behavior_map.obligation(item)[1:] == behavior_map.obligation(case)[1:]
        for item in approved)


def unsettled(decisions: list[JsonObject], traces: list[JsonObject], cases: list[JsonObject],
              approved: list[JsonObject]) -> tuple[list[str], list[str]]:
    """Return undecided and uncleared operands from independently cached source observations.
    A deciding input needs an observed difference; clears() governs clearance. Wholly unobservable runs open no line."""
    unobservable = cases and all(traces[side].get(case["comparison"]["arms"][index]["key"], {}) is None
                                for case in cases for side, index in enumerate((0, -1)))
    undecided, declared = [], []
    cut = lambda text: text if len(text) <= 160 else text[:159] + "…"
    for decision in decisions:
        test, match = (ast.parse(str(decision[key]), mode="eval").body for key in ("test", "match"))
        spans = [{tuple(entry[:4]): (entry[4], entry[5]) for entry in side["nodes"]} for side in decision["sides"]]
        for operand in decision["removed"]:
            node, deciders = ast.parse(str(operand["operand"]), mode="eval").body, []
            for case in cases:
                observations = [(traces[side].get(case["comparison"]["arms"][index]["key"]) or {}).get(decision["id"], [])
                                for side, index in enumerate((0, -1))]
                sides = [{}, {}]
                for groups, rows, locations, expression in zip(sides, observations, spans, (test, match)):
                    for evaluation, count in rows:
                        known = {name: evaluation["names"][name] for name in decision["names"] if name in evaluation["names"]}
                        for *span, kind, jumped in evaluation["jumps"]:
                            text, is_none = locations.get(tuple(span), (None, False))
                            if text is not None:
                                known[text] = jumped is (kind == "true") if kind in ("true", "false") else (jumped is (kind == "none")) is is_none
                        inputs = tuple((name, evaluation["input"][name]) for name in decision["inputs"] if name in evaluation["input"])
                        groups.setdefault(inputs, []).append((known, _truth(expression, known), count))
                decides = False
                for key, originals in sides[0].items():
                    candidates = sides[1].get(key, [])
                    if not any(value is not None and _truth(node, known) is operand["value"] for known, value, _ in originals):
                        continue
                    outcomes = Counter()
                    for _, value, count in candidates:
                        outcomes[value] += count
                    total = sum(row[2] for row in originals)
                    unmatched = max(0, sum(outcomes.values()) - total)
                    for before in (False, True):
                        observed = outcomes[not before]
                        if observed <= unmatched:
                            continue
                        eligible = [(known, count) for known, value, count in originals
                                    if value is before and _truth(node, known) is operand["value"]]
                        if observed + sum(count for _, count in eligible) > total + unmatched and any(
                            value is (not before) and all(known[name] is values[name] for name in known.keys() & values.keys())
                            for known, _ in eligible for values, value, _ in candidates
                        ):
                            decides = True
                            break
                    if decides:
                        break
                if not decides:
                    continue  # agreeing or unmatched observations can account for this input
                if clears(case, approved):
                    break
                deciders.append(str(case["id"]))
            else:
                where = f"it {str(operand['value']).lower()} on the original where the candidate's `{cut(decision['match'])}` is observed to decide differently"
                line = (f"removed condition {decision['file']}:{operand['line']} `{cut(operand['operand'])}` of `{cut(decision['test'])}`: the change "
                        "assumes that removing it loses no result the request keeps. " + (f"Only {', '.join(dict.fromkeys(deciders))} makes {where}, "
                        "without passing on both versions, which settles nothing. " if deciders else "") + f"One case settles that: its input makes "
                        f"{where} (no other input counts) and it asserts the result on both versions. Passing on both, it closes this line; without "
                        "such a case, restore the condition.")
                if deciders:
                    declared.append(line)
                elif not unobservable:
                    undecided.append(line)
    return undecided, declared
