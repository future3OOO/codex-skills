#!/usr/bin/env python3
"""Print the CI lane for a delta: `pr_scope.py <repo> <base> [head]`.

The gate's path policy owns what each path is, dependency manifests included.
Bytes, because `-z` emits pathnames raw, unquoted and not necessarily UTF-8; a
failed diff or an empty delta is production, never an empty documentation lane.
"""
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/production-code/scripts"))
_policy = importlib.import_module("_quality_gate.path_policy")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import analysis_unchanged  # noqa: E402


def lane(repo: str, base: str, head: str = "HEAD") -> str:
    listed = subprocess.run(
        ["git", "-C", repo, "diff", "-z", "--name-only", "--no-renames", f"{base}...{head}"],
        stdin=subprocess.DEVNULL, capture_output=True, check=False)
    paths = [os.fsdecode(path) for path in listed.stdout.split(b"\0") if path]
    if listed.returncode != 0 or not paths:
        return "production"
    if all(_policy.classify_path(p).role == _policy.ROLE_DOCS for p in paths):
        return "docs"
    previous = os.environ.get("PR_PREVIOUS_HEAD", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[0-9a-f]{40}", previous) or not repository:
        return "production"
    ancestor = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", previous, head],
                              capture_output=True, check=False)
    if ancestor.returncode or not analysis_unchanged(resolve_repo_identity(repo), previous, head):
        return "production"
    try:
        result = subprocess.run(["gh", "api", "-X", "GET",
                                 f"repos/{repository}/actions/workflows/gate-suite.yml/runs",
                                 "-f", f"head_sha={previous}", "-f", "status=success", "-f", "per_page=1"],
                                capture_output=True, text=True, check=True, timeout=30)
        runs = json.loads(result.stdout).get("workflow_runs", [])
        if any(run.get("head_sha") == previous and run.get("conclusion") == "success"
               and run.get("path") == ".github/workflows/gate-suite.yml" for run in runs):
            return "correction"
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TypeError):
        return "production"
    return "production"


if __name__ == "__main__":
    print(f"lane={lane(*sys.argv[1:])}")
