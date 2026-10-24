"""What a candidate changes in its code: the functions it edits, the test files named for them, and the
Python condition operands it removes, with whether a traced original run decided each one."""
from __future__ import annotations

import ast
import difflib
import os
import re
from collections import Counter

from .repo_identity import RepoIdentity
from .state_store import _git, is_test_path

JsonObject = dict[str, object]
# A declaration line opens the function every following changed line belongs to, until the next one.
DECLARATIONS = {
    ".go": (re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)"), "{}_test.go"),
    ".ts": (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"
                       r"|^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)"
                       r"|^\s+(?:(?:public|private|protected|static|async|override)\s+)*"
                       r"(?!(?:if|for|while|switch|catch|with)\b)([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^{]*)?\{"),
            "{}.test.ts"),
}
DECLARATIONS[".tsx"] = DECLARATIONS[".ts"]


def _functions(source: str) -> dict[str, ast.AST]:
    """Each Python function definition by its runtime qualified name; an unparsable source has none."""
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


def _declared(before: str, after: str, pattern: re.Pattern[str]) -> set[str]:
    """Functions owning a changed line on either side, by their nearest preceding declaration."""
    names: set[str] = set()
    old, new = before.splitlines(), after.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for lines, start, end in ((old, i1, i2), (new, j1, j2)):
            for line in range(start, end) if tag != "equal" else ():
                owner = next((match for index in range(line, -1, -1) if (match := pattern.match(lines[index]))), None)
                if owner:
                    names.add(next(group for group in owner.groups() if group))
    return names


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


def _removed(path: str, qualname: str, before: str, after: str, old: ast.AST, new: ast.AST) -> list[JsonObject]:
    """Each original decision losing an operand. It pairs with the candidate decision, on the lines the
    diff aligns with its own, that shares most operands; an operand that decision no longer contains,
    outside a partly kept group, is removed and decides its group at False under `and`, True under `or`.
    An operand the original repeats more often than that decision pairs no single occurrence: unmeasured."""
    aligned: dict[int, set[int]] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).get_opcodes():
        for line in range(i1, i2):
            aligned[line + 1] = {j1 + 1 + line - i1} if tag == "equal" else set(range(j1 + 1, j2 + 1))
    candidates = _decisions(new)
    found = []
    for test in _decisions(old):
        lines = set().union(*(aligned.get(line, set()) for line in range(test.lineno, test.end_lineno + 1)))
        shared = _operands(test)
        match = max((other for other in candidates if other.lineno in lines), default=None,
                    key=lambda other: len(_operands(other) & shared))
        paired = _operands(match) if match is not None else Counter()
        unpaired = {text for text, count in shared.items() if count > max(1, paired[text])}
        kept = set(paired & shared)
        if ast.unparse(test) in kept:
            continue
        groups = [node for node in ast.walk(test) if isinstance(node, ast.BoolOp)]
        nested = {id(inner) for group in groups for value in group.values for inner in ast.walk(value)}
        pending, removed = [group for group in groups if id(group) not in nested], []
        while pending:
            group = pending.pop()
            for operand in group.values:
                inner = operand.operand if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.Not) else operand
                if (text := ast.unparse(operand)) in kept and text not in unpaired:
                    continue
                if isinstance(inner, ast.BoolOp) and any(ast.unparse(value) in kept for value in inner.values):
                    pending.append(inner)
                else:
                    removed.append({"label": f"`{text}` ({path}:{operand.lineno})", "operand": text,
                                    "value": isinstance(group.op, ast.Or), "unpaired": text in unpaired})
        removed = list({entry["label"]: entry for entry in removed}.values())
        if removed:
            found.append({"file": path, "qualname": qualname, "test": ast.unparse(test),
                          "match": ast.unparse(match) if kept else None, "removed": removed, "unpaired": sorted(unpaired),
                          "span": [test.lineno, test.end_lineno, test.col_offset, test.end_col_offset],
                          "names": sorted({node.id for node in ast.walk(test) if isinstance(node, ast.Name)}),
                          "nodes": [[node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(node),
                                     isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Is)]
                                    for node in ast.walk(test) if isinstance(node, ast.expr)]})
    return found


def _blob(identity: RepoIdentity, tree: str, path: str) -> str:
    try:
        return _git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
    except RuntimeError:  # the path is absent on that side
        return ""


def analyse(identity: RepoIdentity, original: str, candidate: str) -> tuple[dict[str, str], list[JsonObject]]:
    """The test files on both trees named for a changed function, each with that function, and the
    original's decisions whose operands the candidate removes."""
    changed = [os.fsdecode(name) for name in _git(identity, "diff", "--name-only", "-z", original, candidate).split(b"\0")
               if name and not is_test_path(os.fsdecode(name))]
    functions: dict[str, set[str]] = {}
    decisions: list[JsonObject] = []
    for path in changed:
        suffix = os.path.splitext(path)[1]
        if suffix != ".py" and suffix not in DECLARATIONS:
            continue
        before, after = _blob(identity, original, path), _blob(identity, candidate, path)
        if suffix == ".py":
            old, new = _functions(before), _functions(after)
            for name in old.keys() | new.keys():
                if name not in old or name not in new or ast.dump(old[name]) != ast.dump(new[name]):
                    functions.setdefault("test_{}.py", set()).add(name.rsplit(".", 1)[-1])
                    if name in old and name in new:
                        decisions += _removed(path, name, before, after, old[name], new[name])
        else:
            pattern, form = DECLARATIONS[suffix]
            functions.setdefault(form, set()).update(_declared(before, after, pattern))
    wanted = {form.format(name): name for form, names in functions.items() for name in names}
    existing = [{os.fsdecode(path) for path in _git(identity, "ls-tree", "-r", "--name-only", "-z", tree).split(b"\0")
                 if path and os.path.basename(os.fsdecode(path)) in wanted} for tree in (original, candidate)]
    named = {path: wanted[os.path.basename(path)] for path in existing[0] & existing[1]}
    return dict(sorted(named.items())), decisions


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


def judge(decisions: list[JsonObject], traced: dict[str, list[JsonObject]] | None) -> list[JsonObject]:
    """Each removed operand: decided True when some traced evaluation showed it at its deciding value while
    the original and paired candidate decisions differ; False when every evaluation shows it did not (or
    none reached it); None when unmeasured: no trace, no paired decision, an unpaired repeated operand, a
    chained comparison (its jumps share one position), or a truth left unknown, including a repeated
    operand's whose occurrences the trace cannot tell apart."""
    results = []
    for index, decision in enumerate(decisions):
        test = ast.parse(str(decision["test"]), mode="eval").body
        match = ast.parse(str(decision["match"]), mode="eval").body if decision["match"] else None
        spans = {tuple(node[:4]): (node[4], node[5]) for node in decision["nodes"]}
        evaluations = (traced or {}).get(str(index), [])
        for removed in decision["removed"]:
            if removed["unpaired"] or any(isinstance(node, ast.Compare) and len(node.ops) > 1 for node in ast.walk(test)):
                results.append({"label": removed["label"], "decided": None})
                continue
            operand = ast.parse(str(removed["operand"]), mode="eval").body
            outcomes = set()
            for evaluation in evaluations if match is not None else ():
                known = dict(evaluation["names"])
                # Jumps of a repeated operand's occurrences share one text; only a traced name's truth is exact.
                ambiguous = any(_truth(ast.parse(text, mode="eval").body, dict(evaluation["names"])) is None
                                for text in decision["unpaired"])
                for *span, kind, jumped in evaluation["jumps"]:
                    text, is_none = spans.get(tuple(span), (None, False))
                    if text is not None and kind in ("true", "false"):
                        known[text] = jumped is (kind == "true")
                    elif text is not None:
                        known[text] = (jumped is (kind == "none")) is is_none
                value = _truth(operand, known)
                before, after = (None, None) if ambiguous else (_truth(test, known), _truth(match, known))
                outcomes.add(False if value is not None and value is not removed["value"]
                             or before is not None and before is after else
                             None if None in (value, before, after) else True)
            decided = True if True in outcomes else None if traced is None or match is None or None in outcomes else False
            results.append({"label": removed["label"], "decided": decided})
    return results
