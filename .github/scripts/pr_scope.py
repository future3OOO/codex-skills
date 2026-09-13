#!/usr/bin/env python3
"""Print the CI lane for a delta: `pr_scope.py <repo> <base> [head]`.

The gate's path policy owns what each path is, dependency manifests included.
Bytes, because `-z` emits pathnames raw, unquoted and not necessarily UTF-8; a
failed diff or an empty delta is production, never an empty documentation lane.
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/production-code/scripts"))
_policy = importlib.import_module("_quality_gate.path_policy")


def lane(repo: str, base: str, head: str = "HEAD") -> str:
    listed = subprocess.run(
        ["git", "-C", repo, "diff", "-z", "--name-only", "--no-renames", f"{base}...{head}"],
        stdin=subprocess.DEVNULL, capture_output=True, check=False)
    paths = [os.fsdecode(path) for path in listed.stdout.split(b"\0") if path]
    if listed.returncode != 0 or not paths:
        return "production"
    return "docs" if all(_policy.classify_path(p).role == _policy.ROLE_DOCS for p in paths) else "production"


if __name__ == "__main__":
    print(f"lane={lane(*sys.argv[1:])}")
