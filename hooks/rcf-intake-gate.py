#!/usr/bin/env python3
"""PreToolUse: require an active pass for production edits."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError, read_active  # noqa: E402
from hooks.lib.hook_input import advise, edited_path, emit, read_hook_payload  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path, repo_state_dir  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    ready_for_edit,
)


def main() -> int:
    payload = read_hook_payload()
    path = edited_path(payload)
    if path is None:
        return 0
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        identity = resolve_repo_identity(probe)
        relative = os.path.relpath(path, identity.root).replace("\\", "/")
    except (RepoIdentityError, ValueError):
        return 0
    if not is_reviewable_path(relative):
        return 0

    reminders: list[str] = []
    try:
        state = read_active(identity)
        if state is None or state.get("phase") == "complete" or state.get("revalidation"):
            emit("PreToolUse", permissionDecision="deny", permissionDecisionReason=
                 f"Production edit blocked: no active implementation workflow in {repo_state_dir(identity)}; bind the task pass before editing.")
            return 0
        missing = ready_for_edit(identity, state, reminders=reminders if not is_test_path(relative) else None)
    except (WorkflowError, LedgerError, OSError, ValueError) as exc:
        emit("PreToolUse", permissionDecision="deny", permissionDecisionReason=f"Production edit blocked: workflow authority is unreadable: {exc}")
        return 0
    advise("PreToolUse", payload.get("session_id"), {
        f"{identity.key}:intake": "workflow intake: missing before this production edit: " + ", ".join(missing)
        + ". Admitted." if missing else "",
        f"{identity.key}:obligations": "\n".join(reminders)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
