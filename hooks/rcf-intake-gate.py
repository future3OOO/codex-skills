#!/usr/bin/env python3
"""PreToolUse: advise on edits; gate delegation on the lead's current proof.

For edits, it names what the pass has not recorded yet and lets
the edit through; the recorder binds every later RED to the tree it ran on,
so order of proof is evidence the reviews weigh, not a verdict on keystrokes.
"""
from __future__ import annotations

import json
import sqlite3
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import edited_path, is_explorer_continuation, read_hook_payload, session_key, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path, session_associations  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    read_workflow,
    ready_for_edit,
    review_blockers,
)


def advise(context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))


def main() -> int:
    payload = read_hook_payload()
    tool_name = payload.get("tool_name")
    tool_name = tool_name.removeprefix("collaboration") if isinstance(tool_name, str) else ""
    if tool_name in {"Agent", "spawn_agent", "followup_task", "send_input", "send_message", "resume_agent"}:
        missing: list[str] = []
        try:
            session = session_key(payload)
            identities = session_associations(session) if session else []
            current = try_resolve_repo_identity(working_directory(payload))
            if current is not None and current not in identities:
                identities.append(current)
            common_directory: str | None = None
            explorer_continuation: bool | None = None
            for identity in identities:
                if current is not None and identity != current:
                    if common_directory is None:
                        common_directory = current.common_git_directory()
                    try:
                        if identity.common_git_directory() != common_directory:
                            continue
                    except RepoIdentityError:
                        continue
                state = read_workflow(identity)
                if state is None or state.get("phase") == "complete" and not state.get("revalidation"):
                    continue
                inputs = payload.get("tool_input")
                if not state.get("preflightEvidence"):
                    if tool_name in {"Agent", "spawn_agent"}:
                        if isinstance(inputs, dict) and inputs.get("agent_type") == "explorer":
                            continue
                    else:
                        if explorer_continuation is None:
                            explorer_continuation = is_explorer_continuation(payload)
                        if explorer_continuation:
                            continue
                missing.extend(review_blockers(identity, state))
        except (RepoIdentityError, WorkflowError, LedgerError, OSError, ValueError, sqlite3.Error) as exc:
            missing.append(str(exc))
        if missing:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "deny", "permissionDecisionReason":
                "Lead investigation and current behavioral verification must precede reviewer dispatch. Pending: "
                + ", ".join(dict.fromkeys(missing))}}))
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
        advise(f"workflow intake: workflow evidence is unreadable: {exc}. Admitted; nothing records this edit until it is repaired.")
        return 0
    if missing:
        reminders.insert(0, "workflow intake: missing before this production edit: " + ", ".join(missing)
                         + ". Admitted; a RED taken after it is recorded as late.")
    if reminders:
        advise("\n".join(reminders))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
