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
    def api(endpoint: str) -> dict:
        result = subprocess.run(["gh", "api", f"repos/{repository}/actions/{endpoint}"],
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    try:
        for run in api("workflows/gate-suite.yml/runs?status=success&per_page=20")["workflow_runs"]:
            if run["path"] != ".github/workflows/gate-suite.yml" or run["conclusion"] != "success":
                continue
            ancestor = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", run["head_sha"], head],
                                      capture_output=True, check=False)
            if ancestor.returncode:
                continue
            jobs = api(f"runs/{run['id']}/jobs")["jobs"]
            if not any(step["name"] == "Integrated package contracts" and step["conclusion"] == "success"
                       for job in jobs for step in job["steps"]):
                continue
            for artifact in api(f"runs/{run['id']}/artifacts")["artifacts"]:
                match = re.fullmatch(r"pr138-source-([0-9a-f]{40})", artifact["name"])
                if not match or artifact["expired"]:
                    continue
                tested = match[1]
                subprocess.run(["git", "-C", repo, "fetch", "--no-tags", "origin", tested],
                               capture_output=True, check=True, timeout=30)
                if analysis_unchanged(resolve_repo_identity(repo), tested, head):
                    return "correction"
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return "production"
    return "production"


if __name__ == "__main__":
    print(f"lane={lane(*sys.argv[1:])}")
