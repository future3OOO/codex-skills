#!/usr/bin/env python3
"""Fail on dead production Python: `unreferenced.py <repo>`.

Reports on stderr, and exits 1 for, each def or class, and each name the module binds
outside def, class, lambda and comprehension syntax, that no other word of the tracked
files' UTF-8 text (symlinks read through) names; and each dataclass field no text reads
as `.field` or a getattr literal, unless a module naming its class reads objects whole
(asdict, astuple, fields, vars, __dict__). A field only generated methods, match patterns
or aliased readers use is reported: read it to keep it. Python files are `.py` or
python-shebang files. Test-like paths neither define nor reference, so code only a test
calls is dead. Skipped: `__x__` methods and the `visit_<AST class>` methods of a class
with a base, which Python and ast.NodeVisitor call by name.
"""
import ast
import collections
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/production-code/scripts"))
from _quality_gate.path_policy import is_test_like_path  # noqa: E402

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
          ast.GeneratorExp)


def bound(node: ast.AST) -> list[ast.Name]:
    """The names a module binds in its own scope, through compound statements but not nested scopes."""
    if isinstance(node, ast.Name):
        return [node] if isinstance(node.ctx, ast.Store) else []
    return [] if isinstance(node, SCOPES) else [name for child in ast.iter_child_nodes(node) for name in bound(child)]


def unreferenced(repo: str) -> list[str]:
    listed = subprocess.run(["git", "-C", repo, "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    files = {path: data for path in map(os.fsdecode, filter(None, listed)) if not is_test_like_path(path)
             and os.path.isfile(full := os.path.join(repo, path)) and b"\0" not in (data := Path(full).read_bytes())}
    text = b"\n".join(files.values()).decode("utf-8", "replace")
    words, reads = collections.Counter(re.findall(r"\w+", text)), set(re.findall(r"\.(\w+)", text))
    trees = {path: ast.parse(data, path) for path, data in files.items()
             if path.endswith(".py") or re.match(rb"#!.*python", data)}
    reads |= {node.args[1].value for tree in trees.values() for node in ast.walk(tree) if isinstance(node, ast.Call)
              and getattr(node.func, "id", None) == "getattr" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)}
    whole = [set(re.findall(r"\w+", files[path].decode("utf-8", "replace"))) for path, tree in trees.items()
             if any(getattr(node.func, "id", getattr(node.func, "attr", None)) in {"asdict", "astuple", "fields", "vars"}
                    for node in ast.walk(tree) if isinstance(node, ast.Call))
             or any(isinstance(node, ast.Attribute) and node.attr == "__dict__" for node in ast.walk(tree))]
    dispatched = {f"visit_{name}" for name in dir(ast) if isinstance(getattr(ast, name), type)}
    found, fields = [], []
    for path, tree in trees.items():
        exempt = {id(item) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.bases
                  for item in node.body if getattr(item, "name", None) in dispatched}
        found += [(path, node.lineno, node.name) for node in ast.walk(tree) if id(node) not in exempt
                  and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        found += [(path, name.lineno, name.id) for name in bound(tree)]
        fields += [(path, field.lineno, f"{node.name}.{field.target.id}") for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef) and any("dataclass" in ast.unparse(d) for d in node.decorator_list)
                   and not any(node.name in names for names in whole)
                   for field in node.body if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
                   and field.target.id not in reads]
    defined = collections.Counter(name for _, _, name in found)
    return [f"{path}:{line} {name}" for path, line, name in sorted(fields + [
        entry for entry in found if words[entry[2]] <= defined[entry[2]] and not entry[2][:2] == entry[2][-2:] == "__"])]


if __name__ == "__main__":
    sys.exit("\n".join(unreferenced(sys.argv[1])) or None)
