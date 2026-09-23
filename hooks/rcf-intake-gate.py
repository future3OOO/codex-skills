#!/usr/bin/env python3
"""PreToolUse: advise on edits; gate delegation on the lead's current proof.

For edits, it names what the pass has not recorded yet and lets
the edit through; the recorder binds every later RED to the tree it ran on,
so order of proof is evidence the reviews weigh, not a verdict on keystrokes.
A shell command that is exactly one pytest/unittest invocation is rewritten to
run through `workflow verify --observed`, which keeps its receipt in the
checkout where it runs and leaves its output and exit code unchanged.
"""
from __future__ import annotations

import os
import re
import shlex
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
from hooks.lib.tdd_surface import identify  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    _finding_unresolved,
    read_workflow,
    ready_for_edit,
    review_blockers,
)


WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
SHELL_SYNTAX = re.compile(r"[;&|<>`$()\n\\]")
# Unquoted, these the shell would expand or drop; quoted, they reach the runner verbatim.
SHELL_EXPANSION = re.compile(r"[*?\[\]{}~#]")


def observed(command: object) -> str | None:
    """The receipt-keeping form of a lone pytest/unittest command, else None."""
    if not isinstance(command, str) or SHELL_SYNTAX.search(command):
        return None
    try:
        tokens = shlex.split(command)
        bare = shlex.split(re.sub(r"'[^']*'|\"[^\"]*\"", "''", command))
    except ValueError:
        return None
    if any(SHELL_EXPANSION.search(token) for token in bare):
        return None
    if identify(tokens).get("runner") not in {"pytest", "unittest"}:
        return None
    return shlex.join([sys.executable, str(WORKFLOW), "verify", "--observed", "--", *tokens])


def main() -> int:
    payload = read_hook_payload()
    tool_name = payload.get("tool_name")
    tool_name = tool_name.removeprefix("collaboration") if isinstance(tool_name, str) else ""
    inputs = payload.get("tool_input")
    if tool_name == "Bash" and isinstance(inputs, dict) and (rewritten := observed(inputs.get("command"))):
        emit("PreToolUse", permissionDecision="allow", updatedInput={**inputs, "command": rewritten})
        return 0
    if tool_name in {"Agent", "spawn_agent", "followup_task", "send_input", "send_message", "resume_agent"}:
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
        + ". Admitted; a RED taken after it is recorded as late." if missing else "",
        f"{identity.key}:obligations": "\n".join(reminders)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
