"""What a candidate changes in its code: the functions it edits, the test files named for them, and the
Python condition operands it removes."""
from __future__ import annotations

import ast
import difflib
import os
import re
from collections import Counter

from .repo_identity import RepoIdentity
from .state_store import _git, is_test_path

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


def _removed(path: str, before: str, after: str, old: ast.AST, new: ast.AST) -> list[str]:
    """The label of each operand an original decision loses. It pairs with the candidate decision, on the lines
    the diff aligns with its own, that shares most operands; an operand that decision no longer contains,
    outside a partly kept group, or repeats fewer times, is removed."""
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
                    removed.append(f"`{text}` ({path}:{operand.lineno})")
        found += dict.fromkeys(removed)
    return found


def _blob(identity: RepoIdentity, tree: str, path: str) -> str:
    try:
        return _git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
    except RuntimeError:  # the path is absent on that side
        return ""


def analyse(identity: RepoIdentity, original: str, candidate: str) -> tuple[dict[str, str], list[str]]:
    """The test files on both trees named for a changed function, each with that function, and the label of
    each original condition operand the candidate removes."""
    changed = [os.fsdecode(name) for name in _git(identity, "diff", "--name-only", "-z", original, candidate).split(b"\0")
               if name and not is_test_path(os.fsdecode(name))]
    functions: dict[str, set[str]] = {}
    removed: list[str] = []
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
                        removed += _removed(path, before, after, old[name], new[name])
        else:
            pattern, form = DECLARATIONS[suffix]
            functions.setdefault(form, set()).update(_declared(before, after, pattern))
    wanted = {form.format(name): name for form, names in functions.items() for name in names}
    existing = [{os.fsdecode(path) for path in _git(identity, "ls-tree", "-r", "--name-only", "-z", tree).split(b"\0")
                 if path and os.path.basename(os.fsdecode(path)) in wanted} for tree in (original, candidate)]
    named = {path: wanted[os.path.basename(path)] for path in existing[0] & existing[1]}
    return dict(sorted(named.items())), removed


def changed_tests(identity: RepoIdentity, original: str, candidate: str) -> dict[str, set[str]]:
    """Each existing Python test file the candidate modifies, with its candidate functions' qualified names."""
    return {path: set(_functions(_blob(identity, candidate, path)))
            for raw in _git(identity, "diff", "--name-only", "--diff-filter=M", "-z", original, candidate).split(b"\0")
            if raw and (path := os.fsdecode(raw)).endswith(".py") and is_test_path(path)}

