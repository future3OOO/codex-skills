#!/usr/bin/env python3
"""PreToolUse(Edit|Write|apply_patch): advise on the before-edit workflow.

The hook never refuses. It names what the pass has not recorded yet and lets
the edit through; the recorder binds every later RED to the tree it ran on,
so order of proof is evidence the reviews weigh, not a verdict on keystrokes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import edited_path, read_hook_payload  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    read_workflow,
    ready_for_edit,
)


def advise(context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))


def main() -> int:
    path = edited_path(read_hook_payload())
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
