#!/usr/bin/env python3
"""SessionStart(resume|compact): restore workflow rules and bounded pass state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import summary  # noqa: E402

DISCIPLINE = """Discipline re-arm: each production pass runs Repo Context Forge, which records its packet-scoped graph result as workflow evidence, then diagnosis when applicable, advisor preflight, production preflight, real-seam TDD when required, production-code before implementation edits, implementation and verification, code-review delegate review when non-trivial, final Claude Advisor review, then workflow completion, followed by delivery when integration is intended. A production edit after review makes code review and final review pending again. The mock ban, demonstrated-risk rule, and root-cause-first rule remain hard. Compacted state is continuity context, never Git authorization or proof that an unrecorded step passed."""


def main() -> int:
    identity = try_resolve_repo_identity(working_directory(read_hook_payload()))
    context = DISCIPLINE
    if identity is None:
        context += "\nWorkflow state unavailable; do not infer that any workflow step passed."
    else:
        context += "\n" + summary(identity, 1200)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
