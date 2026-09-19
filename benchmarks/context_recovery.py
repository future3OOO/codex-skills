#!/usr/bin/env python3
"""Mechanical N/N+1 hook cost on isolated fixtures. NOT an agent/compaction trial.

Both trusted checkout paths execute their real scripts with separate repositories
and state. No graph, approval, semantic coverage or global estate is fabricated.
Reports all measured samples and bytes, not token estimates or agent savings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(args: list[str], cwd: Path, env: dict, payload: dict | None = None) -> tuple[subprocess.CompletedProcess, float]:
    started = time.perf_counter()
    result = subprocess.run(args, cwd=cwd, env=env, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=30)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[:500]}")
    return result, elapsed


def source_identity(checkout: Path) -> dict:
    """Attribute measurements to all tracked source, not just the hook entrypoint."""
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("benchmark requires a clean checkout, including untracked files")
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}")}


def arm(checkout: Path, root: Path) -> dict:
    identity = source_identity(checkout)
    root.mkdir()
    env = {**os.environ, "CODEX_HOME": str(root / "home"), "CODEX_WORKFLOW_STATE_ROOT": str(root / "state"),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "PYTHONDONTWRITEBYTECODE": "1"}
    repo = root / "repo"
    repo.mkdir()
    for command in (["git", "init", "-q"], ["git", "config", "user.name", "Context Benchmark"],
                    ["git", "config", "user.email", "test@example.invalid"]):
        run(command, repo, env)
    # Real input text: 24 distinct 256 KiB files, not a reconstructed CX2 capture.
    names = [f"source{n:02d}.txt" for n in range(24)]
    for name in names:
        (repo / name).write_text("evidence line\n" * (256 * 1024 // 14))
    run(["git", "add", "."], repo, env)
    run(["git", "commit", "-qm", "fixture"], repo, env)
    cli = checkout / "skills/repo-production-workflow/scripts/workflow.py"
    run([sys.executable, str(cli), "begin", "--repo", str(repo), "--slug", "context-cost"], repo, env)
    command = "\n".join(f"sed -n '1p' {name}" for name in names)
    original, _ = run(["bash", "-c", command], repo, env)
    payload = {"tool_name": "Bash", "cwd": str(repo), "session_id": "cost", "tool_input": {"command": command}}
    hook = checkout / "hooks/code-quality-gate.py"
    rearm = checkout / "hooks/skill-discipline-rearm.py"
    hook_times, rearm_times, bytes_out = [], [], []
    for _ in range(5):
        _, elapsed = run([sys.executable, str(hook)], repo, env, payload)
        hook_times.append(elapsed)
        result, elapsed = run([sys.executable, str(rearm)], repo, env, {"cwd": str(repo), "source": "compact"})
        rearm_times.append(elapsed)
        bytes_out.append(len(result.stdout.encode()))
    snapshots = None
    snapshot_cli = checkout / "skills/repo-production-workflow/scripts/context.py"
    if snapshot_cli.is_file():
        result, initial_time = run([sys.executable, str(snapshot_cli), "read", "--repo", str(repo),
                                   "--path", names[0], "--start", "1", "--end", "1"], repo, env)
        first = json.loads(result.stdout)
        recovered, recover_time = run([sys.executable, str(snapshot_cli), "show", "--repo", str(repo),
                                       "--id", first["id"]], repo, env)
        if json.loads(recovered.stdout)["output"] != first["output"] or first["output"] != "evidence line\n":
            raise RuntimeError("snapshot recovery returned different content")
        result_with_data, window_time = run([sys.executable, str(rearm)], repo, env, {"cwd": str(repo), "source": "compact"})
        if "sourceData" not in json.loads(result_with_data.stdout)["hookSpecificOutput"]["additionalContext"]:
            raise RuntimeError("re-arm output omitted recovered source data")
        snapshots = {"readSeconds": initial_time, "recoverSeconds": recover_time, "rearmSeconds": window_time,
                     "readBytes": len(result.stdout.encode()), "recoverBytes": len(recovered.stdout.encode()),
                     "rearmBytes": len(result_with_data.stdout.encode()), "contentEqual": True}
    if source_identity(checkout) != identity:
        raise RuntimeError("benchmark source changed during execution")
    return {"checkout": str(checkout), **identity, "hookSha256": hashlib.sha256(hook.read_bytes()).hexdigest(),
            "command": command, "originalOutputBytes": len(original.stdout.encode()),
            "hookSeconds": hook_times, "rearmSeconds": rearm_times, "rearmBytes": bytes_out,
            "medianHookSeconds": statistics.median(hook_times), "medianRearmSeconds": statistics.median(rearm_times),
            "snapshotPath": snapshots}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="context-cost-") as directory:
        root = Path(directory)
        report = {"kind": "synthetic-fixture mechanical cost, not native compaction or CX2 replay",
                  "baseline": arm(args.baseline.resolve(), root / "N"),
                  "candidate": arm(args.candidate.resolve(), root / "N1"),
                  "agentTokenSavings": None, "nativeBoundaryVerified": False}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
