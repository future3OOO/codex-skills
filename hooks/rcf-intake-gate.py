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
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import edited_path, is_explorer_continuation, read_hook_payload, session_key, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import advisory_changed, is_reviewable_path, is_test_path  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    _finding_unresolved,
    read_workflow,
    ready_for_edit,
    review_blockers,
)


def observed_test(payload: dict[str, object]) -> None:
    inputs = payload.get("tool_input")
    if not isinstance(inputs, dict):
        return
    field = "command" if isinstance(inputs.get("command"), str) else "cmd"
    command = inputs.get(field)
    if not isinstance(command, str) or not command or any(char in command for char in ("`", "$", "#", "\r", "\n", "*", "?", "[", "]", "{", "}", "~", ";", "&", "|", "<", ">")):
        return
    if not re.match(r"^\s*(?:\S*/)?(?:pytest|py\.test|python[\d.]*\s+-m\s+(?:pytest|unittest))(?=\s|$)", command):
        return
    if re.search(r"(?:^|\s)(?:--help|--version|-h|-V|--(?:co(?:llect-only)?|fixtures(?:-per-test)?|markers|setup-(?:plan|only)))(?=\s|$)", command):
        return
    cwd = inputs.get("workdir") or working_directory(payload)
    if not isinstance(cwd, str):
        return
    cwd = str(Path(cwd).resolve())
    identity = try_resolve_repo_identity(cwd)
    if identity is None:
        return
    try:
        state = read_workflow(identity)
    except (WorkflowError, LedgerError, OSError, ValueError, sqlite3.Error):
        return
    if state is None or state.get("phase") == "complete":
        return
    wrapped = [
        sys.executable,
        str(ROOT / "skills/repo-production-workflow/scripts/workflow.py"),
        "verify", "--repo", str(identity.root), "--slug", str(state["slug"]),
        "--observed", "--run-cwd", cwd, "--", "bash", "-lc", command,
    ]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": {**inputs, field: shlex.join(wrapped)},
    }}))


def advise(context: str, payload: dict[str, object], repo_key: str) -> None:
    if advisory_changed(session_key(payload), repo_key, "PreToolUse", context):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))


def main() -> int:
    payload = read_hook_payload()
    tool_name = payload.get("tool_name")
    tool_name = tool_name.removeprefix("collaboration") if isinstance(tool_name, str) else ""
    if tool_name in {"Bash", "exec_command"}:
        observed_test(payload)
        if tool_name == "exec_command":
            return 0
    if tool_name in {"Agent", "spawn_agent", "followup_task", "send_input", "send_message", "resume_agent"}:
        missing: list[str] = []
        try:
            identity = try_resolve_repo_identity(working_directory(payload))
            if identity is not None:
                state = read_workflow(identity)
                if state is None or state.get("phase") == "complete" and not state.get("revalidation"):
                    return 0
                inputs = payload.get("tool_input")
                if not state.get("preflightEvidence"):
                    if tool_name in {"Agent", "spawn_agent"}:
                        if isinstance(inputs, dict) and inputs.get("agent_type") == "explorer":
                            return 0
                    elif is_explorer_continuation(payload):
                        return 0
                raw_session = payload.get("session_id")
                session = raw_session if isinstance(raw_session, str) and raw_session.strip() else None
                target = inputs.get("target") or inputs.get("id") if isinstance(inputs, dict) else None
                repairs = [owner for entry in state.get("findingStates", [])
                           if isinstance(entry, dict) and _finding_unresolved(entry)
                           and int(entry.get("recurrence", 0)) >= 2 and (owner := entry.get("repairOwner"))
                           and owner.get("implementerContextId") and owner.get("reviewerContextId")
                           and owner["implementerContextId"] != owner["reviewerContextId"]]
                if repairs:
                    if not (tool_name in {"followup_task", "send_input", "send_message", "resume_agent"}
                            and any(target is not None and target == owner.get("implementerContextId")
                                    and session is not None and session == owner.get("reviewerContextId") for owner in repairs)):
                        missing.append("second recurrence requires continuation of the retained reviewer for repair")
                else:
                    missing = review_blockers(identity, state)
                if missing:
                    missing = [f"{identity.root} [{state['slug']}/{state['workflowId']}]: " + ", ".join(missing)]

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
        advise(f"workflow intake: workflow evidence is unreadable: {exc}. Admitted; nothing records this edit until it is repaired.", payload, identity.key)
        return 0
    if missing:
        reminders.insert(0, "workflow intake: missing before this production edit: " + ", ".join(missing)
                         + ". Admitted; a RED taken after it is recorded as late.")
    if reminders:
        advise("\n".join(reminders), payload, identity.key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
