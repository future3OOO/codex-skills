#!/usr/bin/env python3
"""PostToolUse: invalidate review readiness, then return cheap local feedback.

Per-edit work is deliberately limited to the freshness/invalidation transition
and genuinely local signals (changed-file ruff lint and, in an active pass, the
issue #212 map-ownership advisory), each emitted only when it changed for the
session. Full quality-gate analysis and its warnings surface at typed
quality-gate verify (issue #182 — per-edit gate runs were measured as ~90%
redundant context with zero acted-on repetitions).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import advise, edited_path, read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path  # noqa: E402
from hooks.lib.workflow_state import invalidate_after_edit  # noqa: E402


def _ruff_lines(paths: list[Path]) -> list[str]:
    """Lint actual changed Python files with a fixed rule set in one invocation."""
    paths = [path for path in paths if path.suffix.lower() == ".py" and path.is_file()]
    if not paths:
        return []
    try:
        result = subprocess.run(
            ["ruff", "check", "--isolated", "--select", "E9,F", "--quiet",
             "--output-format", "concise", *map(str, paths)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        # Three measured launch-failure causes (absent, non-executable,
        # malformed) prove the class; ruff's own nonzero exits stay ordinary
        # results under check=False and are never caught here.
        return ["ruff could not run: python lint skipped"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    payload = read_hook_payload()
    path = edited_path(payload)
    if path is None and payload.get("tool_name") != "Bash":
        return 0
    try:
        identity = resolve_repo_identity(path.parent if path else working_directory(payload))
        relative = path.relative_to(identity.root).as_posix() if path else None
    except (RepoIdentityError, ValueError):
        return 0

    state, changed = invalidate_after_edit(identity, relative)

    lint = _ruff_lines([identity.root / path for path in changed])
    advisories = {f"lint:{identity.key}": "python lint findings:\n" + "\n".join(f"- {line}" for line in lint)
                  if lint else ""}
    # Issue #212's single automatic trigger: after a successful production edit
    # in an active pass, the map-ownership advisory runs once here and its
    # bounded notice rides this same PostToolUse additionalContext. Eligibility
    # follows ready_for_edit's complete/revalidation exclusions and
    # production_changes' test exclusion. map_advisory never raises and never
    # changes the edit outcome or the workflow state.
    if (state is not None and state.get("phase") != "complete"
            and not state.get("revalidation")
            and any(is_reviewable_path(path) and not is_test_path(path) for path in changed)):
        from hooks.lib.tdd_workflow import map_advisory
        advisories[f"{identity.key}:map:{state.get('workflowId')}"] = map_advisory(identity, state) or ""
    advise("PostToolUse", payload.get("session_id"), advisories)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
