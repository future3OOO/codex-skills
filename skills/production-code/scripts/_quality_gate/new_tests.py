"""New tests need a demonstrated gap: each new test entry point must be selected by, or fail in, a failing run the
pass recorded. Deterministic; reads only the frozen snapshot and the recorded runs."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .path_policy import language_for_path
from .snapshot import EvaluationSnapshot

_FILTERS = ("-k", "-run", "-t", "--testNamePattern", "--grep", "-f", "--filter")


def unproven(units: list[dict[str, object]], snapshot: EvaluationSnapshot, reds: list) -> list[dict[str, object]]:
    """New test entry points no recorded failing run proves, in any runner. A run proves a test when it scopes the
    test's file (or a directory holding it) and selects it by exact name or by its filter, or fails on a line only
    that test holds; a run that names no test proves nothing by name."""

    def names(ref: str, path: str) -> list[str] | None:
        """The test names a token selects in `path` ([] for all of it), or None when it scopes something else."""
        dotted, ref = str(Path(path).with_suffix("")).replace("/", "."), ref.strip("'\"")
        head, *rest = ref.split("::")
        rest = [part.split("[")[0] for part in rest]
        folder = head.removeprefix("./").removesuffix("...").rstrip("/")
        if head == path or head.endswith("/" + path):
            return rest
        if ref == dotted or ref.startswith(dotted + "."):
            return ref[len(dotted):].strip(".").split(".")
        return [] if head.startswith("./") and folder in ("", ".") or folder and path.startswith(folder + "/") else None

    def chosen(expression: str, flag: str, symbol: str) -> bool:
        if flag != "-k":  # -run, -t and the like: a pattern over the name
            return bool(re.search(expression, symbol)) if not re.search(r"[\\(\[]$", expression) else expression in symbol
        try:  # pytest -k: case-insensitive substring words combined with and/or/not
            return _truth(ast.parse(expression.replace("-", "_"), mode="eval").body, symbol.lower())
        except (SyntaxError, ValueError):
            return False

    def proven(unit: dict[str, object]) -> bool:
        path, symbol, line, code = str(unit["path"]), str(unit["symbol"]), int(unit["line"]), str(unit["code"])
        text = snapshot.sources.get(path, "").splitlines()
        rest = "\n".join(text).replace(code, "", 1).splitlines()
        for red in reds:
            site = re.match(r"(.+?):(\d+)\s*(.*)", str(red.get("site") or ""))
            failed = site.group(3).strip() if site else ""
            # The failing line proves this test when its number lands here with that text, or its text is here and nowhere else.
            if failed and names(site.group(1), path) is not None and failed in code and (
                    line <= int(site.group(2)) < line + len(code.splitlines()) and failed in text[int(site.group(2)) - 1]
                    or all(failed != other.strip() for other in rest)):
                return True
            tokens = [t.strip("'\"") for t in re.findall(r"[^\s'\"]+|'[^']*'|\"[^\"]*\"", str(red.get("command") or ""))]
            selected = [found for token in tokens if (found := names(token, path)) is not None]
            filters = [(tokens[i + 1], flag) for i, flag in enumerate(tokens[:-1]) if flag in _FILTERS]
            # A script test (a region) is proven by a run naming its file or a folder holding it.
            if selected and (symbol == "<region>" or any(symbol in found for found in selected) or any(chosen(e, f, symbol) for e, f in filters)):
                return True
        return False
    return [unit for unit in fresh(units, snapshot) if not proven(unit)]


def fresh(units: list[dict[str, object]], snapshot: EvaluationSnapshot) -> list[dict[str, object]]:
    """New test entry points: the definition line is added and the name is not in the file's base text (a moved test is not
    new); a region only as a whole new shell or bats file (a script test)."""
    added = {(entry.path, number) for entry in snapshot.entries for number, _ in entry.added_lines()}
    base = {entry.path: entry.base_text for entry in snapshot.entries}
    return [unit for unit in units if unit["kind"] == "test" and (unit["path"], unit["line"]) in added and (
        base.get(str(unit["path"])) is None and language_for_path(str(unit["path"])) in ("shell", "bats") if unit["symbol"] == "<region>"
        else not re.search(rf"(?<!\w){re.escape(str(unit['symbol']))}(?!\w)", base.get(str(unit["path"])) or ""))]


def _truth(node: ast.AST, symbol: str) -> bool:
    """A pytest -k expression over one test name; anything but names and and/or/not is refused."""
    if isinstance(node, ast.Name):
        return node.id.lower() in symbol
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _truth(node.operand, symbol)
    if isinstance(node, ast.BoolOp):
        return (all if isinstance(node.op, ast.And) else any)(_truth(value, symbol) for value in node.values)
    raise ValueError("unsupported -k expression")
