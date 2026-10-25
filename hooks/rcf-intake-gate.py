#!/usr/bin/env python3
"""PreToolUse: advise on edits; gate turn-starting delegation on the lead's current proof.

For edits, it names what the pass has not recorded yet and lets the edit
through; comparisons run on recorded source trees, so edit order needs no gate.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import advise, edited_path, emit, is_explorer_continuation, read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    read_workflow,
    ready_for_edit,
    review_blockers,
)


def main() -> int:
    payload = read_hook_payload()
    tool_name = payload.get("tool_name")
    tool_name = tool_name.removeprefix("collaboration") if isinstance(tool_name, str) else ""
    inputs = payload.get("tool_input")
    if tool_name in {"Agent", "spawn_agent", "followup_task", "send_input", "resume_agent"}:
        missing: list[str] = []
        try:
            identity = try_resolve_repo_identity(working_directory(payload))
            if identity is not None:
                state = read_workflow(identity)
                if state is None or state.get("phase") == "complete" and not state.get("revalidation"):
                    return 0
                if not state.get("preflightEvidence"):
                    if tool_name in {"Agent", "spawn_agent"}:
                        if isinstance(inputs, dict) and inputs.get("agent_type") == "explorer":
                            return 0
                    elif is_explorer_continuation(payload):
                        return 0
                missing = review_blockers(identity, state)
                if missing:
                    missing = [f"{identity.root} [{state['slug']}/{state['workflowId']}]: " + ", ".join(missing)]

        except (RepoIdentityError, WorkflowError, LedgerError, OSError, ValueError, sqlite3.Error) as exc:
            missing.append(str(exc))
        if missing:
            emit("PreToolUse", permissionDecision="deny", permissionDecisionReason=
                 "Lead investigation and current behavioral verification must precede reviewer dispatch. Pending: "
                 + ", ".join(dict.fromkeys(missing)))
        return 0
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
        ready, missing = ready_for_edit(identity, relative)
        if ready and not is_test_path(relative):
            missing = edit_blockers(identity, read_workflow(identity), reminders=reminders)
    except (WorkflowError, LedgerError, ValueError) as exc:
        missing, reminders = [], [f"workflow intake: workflow evidence is unreadable: {exc}. "
                                  "Admitted; nothing records this edit until it is repaired."]
    advise("PreToolUse", payload.get("session_id"), {
        f"{identity.key}:intake": "workflow intake: missing before this production edit: " + ", ".join(missing)
        + ". Admitted." if missing else "",
        f"{identity.key}:obligations": "\n".join(reminders)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
