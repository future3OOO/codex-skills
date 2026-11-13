"""The Python conditions a candidate removes, and what settles each one: a recorded case, or a review ruling."""
from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import textwrap
from collections import Counter
from functools import lru_cache

from . import behavior_map
from .repo_identity import RepoIdentity
from .state_store import _git, is_test_path

JsonObject = dict[str, object]
RULING = r"\W{1,8}the request (asks|does not ask) for this(?: change(?: of result)?)?(?![ \t]*\w)"  # not "asks for this condition to stay"


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
                    removed[text] = {"operand": text, "line": operand.lineno, "value": isinstance(group.op, ast.Or)}
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
    for raw in _git(identity, "diff", "--name-only", "--diff-filter=M", "-z", original, candidate, "--", "*.py").split(b"\0"):
        if not raw or is_test_path(path := os.fsdecode(raw)):
            continue
        before, after = (_git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
                         for tree in (original, candidate))
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


def _uncleared(decisions: list[JsonObject], traces: list[JsonObject | None], cases: list[JsonObject], approved: list[JsonObject] | None):
    """Each removed operand no recorded case clears, with the positions of the cases that decide it. A case decides it
    when its original run evaluates it at its deciding value and the paired candidate decision, on the values that run
    observed, is not known to agree with the original; it clears it by `clears`, and always when no `approved` is given.
    `traces` holds each recorded case's evaluations by decision id; None is a run the tracer could not observe; a case
    not yet traced has no evaluations."""
    for decision in decisions:
        test, match = (ast.parse(str(decision[key]), mode="eval").body for key in ("test", "match"))
        spans = {tuple(node[:4]): (node[4], node[5]) for node in decision["nodes"]}
        for operand in decision["removed"]:
            node, deciders = ast.parse(str(operand["operand"]), mode="eval").body, []
            for position, trace in enumerate(traces):
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
                if approved is None or clears(cases[position], approved):
                    break
                deciders.append(position)
            else:
                yield decision, operand, deciders


def unsettled(decisions: list[JsonObject], traces: list[JsonObject | None], cases: list[JsonObject] = (),
              approved: list[JsonObject] | None = None, rulings=list, judged: bool = False) -> tuple[list[str], list[str]]:
    """The open lines and the review questions of the removed operands no recorded case clears (see `_uncleared`; `cases`
    are the recorded cases the traces belong to). One that no case decides is an open line, unless every run was unobservable.
    One that only uncleared cases decide is a change declared after preflight: a question named RC-… for the reviewer
    and the final advisor, not an open line. `rulings()` gives each recorded finding, as its id and claim, with whether the
    final advisor made it, and one rules by following the name with "the request asks for this" or "the request does not
    ask for this". The second is binding from either author and opens the line. The first clears it: the final
    advisor's at once, the reviewer's when the final advisor has judged this tree (`judged`), having received the
    question with it. A final verdict with no ruling opens the line."""
    lines, questions, claims = [], [], None
    cut = lambda text: text if len(text) <= 160 else text[:159] + "…"
    for decision, operand, deciders in _uncleared(decisions, traces, cases, approved):
        head = f"removed condition {decision['file']}:{operand['line']} `{cut(operand['operand'])}`"
        if not deciders:
            if not (traces and all(trace is None for trace in traces)):
                lines.append(f"{head}: no recorded case makes it {str(operand['value']).lower()} on the original where the candidate's "
                             f"`{cut(decision['match'])}` then decides differently; reaching it otherwise does not count. "
                             "Restore it, or record a case with such an input: workflow.py tdd --case ITEM 'COMMAND'")
            continue
        name = "RC-" + hashlib.sha256(f"{decision['id']} {operand['operand']}".encode()).hexdigest()[:8]
        claims = rulings() if claims is None else claims
        said = [(final, found[1].lower() == "asks") for final, claim in claims
                if (found := re.search(re.escape(name) + RULING, claim, re.IGNORECASE))]
        if not all(asks for _, asks in said):
            lines.append(f"{head}: review ruled on {name} that the request does not ask for this change. Restore it.")
        elif judged and not said:
            lines.append(f"{head}: the final review ended with no ruling on {name}, so nothing shows the request asks for this change. Restore it.")
        elif not judged and not any(final for final, _ in said):
            shown = {(cases[position]["id"], cases[position]["comparison"]["command"]): cases[position] for position in deciders}  # an item's retained copy shows once
            questions.append("\n".join([
                f"{name}: {head}. Where it is {str(operand['value']).lower()} on the original, the candidate's `{cut(decision['match'])}` "
                "decides differently. The only recorded result of that difference was declared after the approved preflight:",
                *(f"{identifier} `{command}`\n" + "\n".join(
                    f"  {'candidate' if arm['source'] == 'current' else arm['source']}: {arm['outcome']} (exit {arm['exitCode']})"
                    + ("\n" + textwrap.indent(str(arm["outputTail"]).strip("\n"), "    ") if arm.get("outputTail") else "")
                    for arm in behavior_map.comparison_view(case["comparison"])["arms"]) for (identifier, command), case in shown.items()),
                *(["reviewer: the request asks for this"] if said else []),
                "Does the request ask for this change of result? Restore the condition to clear this now; otherwise a review ruling settles it."]))
    return lines, questions


def undecided(decisions: list[JsonObject], traces: list[JsonObject | None]) -> list[str]:
    """One line per removed operand no recorded case decides, for the tracing that stops once every operand is decided."""
    return unsettled(decisions, traces)[0]
