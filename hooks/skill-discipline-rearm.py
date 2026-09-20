#!/usr/bin/env python3
"""SessionStart(compact): restore workflow rules and bounded pass state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import read_hook_payload, workflow_identity  # noqa: E402
from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError  # noqa: E402
from hooks.lib.workflow_state import WorkflowError, summary  # noqa: E402

DISCIPLINE = """Discipline re-arm: resume the active repo-production-workflow pass at its next unmet requirement, using retained contract, context and bound evidence. Load only missing or changed context. Production edits reopen verification and review; independent review remains required. The mock ban, demonstrated-risk and root-cause rules still apply. State records proof, never Git authorization; missing evidence is pending."""


def main() -> int:
    context = DISCIPLINE
    try:
        identity = workflow_identity(read_hook_payload())
        if identity is None:
            context += "\nWorkflow state unavailable; do not infer that any workflow step passed."
        else:
            context += f"\nTask worktree: {identity.root}\n" + summary(identity)
    except (RepoIdentityError, WorkflowError, LedgerError, OSError, ValueError) as exc:
        context += f"\nWorkflow target unresolved: {exc}; do not resume another task's pass."
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
