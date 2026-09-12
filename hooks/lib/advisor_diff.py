"""The current-pass diff the advisor wrapper sends, with test hunks inside their definitions.

Production paths keep git's ordinary three-line context. Test-classified paths
(the estate's single classifier) carry git function context, so a changed
assertion arrives with the definition that invokes the Seam. Every byte comes
from the two immutable trees.

`.py` context uses git's built-in python driver unless the repository's own
attributes name one, because the default funcname makes a method's context its
whole class (measured 189,425 bytes against 1,883 for one changed line).
"""
from __future__ import annotations

import subprocess
import tempfile

from .state_store import is_test_path

# git's default funcname makes a Python method's context the whole class
# (measured 189,425 bytes for one changed line in a 3,123-line test module);
# git's built-in python driver bounds it to the enclosing def (1,883 bytes).
# A repository's own .gitattributes still wins by git's attribute lookup order.
_ATTRIBUTES = "*.py diff=python\n"


def _git(root: str, *args: str | bytes) -> bytes:
    return subprocess.run(["git", "-C", root, *args], check=True, stdout=subprocess.PIPE).stdout


def current_pass_evidence(root: str, base_tree: str, candidate_tree: str) -> bytes:
    """The base_tree -> candidate_tree diff: production hunks ordinary, test hunks in function context."""
    diff_args = ("diff", "--no-ext-diff", "--binary", base_tree, candidate_tree)
    changed = _git(root, "diff", "--no-renames", "--name-only", "-z", base_tree, candidate_tree)
    test_paths = [
        path for path in changed.split(b"\0")
        if path and is_test_path(path.decode("utf-8", "surrogateescape"))
    ]
    if not test_paths:
        return _git(root, *diff_args)
    production = _git(root, *diff_args, "--", *(b":(exclude,literal)" + path for path in test_paths))
    with tempfile.NamedTemporaryFile("w", suffix=".gitattributes") as attributes:
        attributes.write(_ATTRIBUTES)
        attributes.flush()
        # --no-renames belongs to this test-path view only: the production and
        # no-test diffs stay exactly what git renders.
        tests = _git(root, "-c", f"core.attributesFile={attributes.name}", *diff_args,
                     "--no-renames", "--function-context", "--", *(b":(literal)" + path for path in test_paths))
    return production + tests
