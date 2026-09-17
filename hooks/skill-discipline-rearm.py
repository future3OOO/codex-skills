#!/usr/bin/env python3
"""SessionStart(resume|compact): restore workflow rules and bounded pass state."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import content_digest, recorded_reads  # noqa: E402
from hooks.lib.workflow_state import WorkflowError, read_workflow, summary  # noqa: E402

DISCIPLINE = """Discipline re-arm: resume the active repo-production-workflow pass at its next unmet requirement, using retained contract, context and bound evidence. Load only missing or changed context. Production edits reopen verification and review; independent review remains required. The mock ban, demonstrated-risk and root-cause rules still apply. State records proof, never Git authorization; missing evidence is pending."""
LISTED = 60
READS_CHARS = 1500


def _reads_context(identity: RepoIdentity) -> str:
    """What this pass already read, split by whether the file still matches the hash
    it was read at: the answer to 'load only missing or changed context'."""
    try:
        state = read_workflow(identity)
    except (WorkflowError, LedgerError, ValueError, sqlite3.Error, OSError):
        return ""
    if state is None or not isinstance(state.get("workflowId"), str):
        return ""
    unchanged: list[str] = []
    changed: list[str] = []
    # Oldest first from the store; the line shows the newest LISTED of each group.
    for key, digest in recorded_reads(identity, str(state["workflowId"])).items():
        path = Path(key) if Path(key).is_absolute() else Path(identity.root) / key
        try:
            current = content_digest(path)
        except OSError:
            changed.append(key)
            continue
        (unchanged if current == digest else changed).append(key)

    def line(label: str, keys: list[str]) -> str:
        if not keys:
            return ""
        shown = ", ".join(keys[-LISTED:])
        if len(shown) > READS_CHARS:
            shown = shown[:READS_CHARS].rsplit(", ", 1)[0]
        listed = shown.count(", ") + 1
        return f"\n{label} ({len(keys)}): {shown}" + (f", +{len(keys) - listed} more" if len(keys) > listed else "")

    # "Inspected", not "read": a sed range or an rg match counts, and unchanged means the
    # whole file still matches the hash taken then, not that every line was seen.
    return line("Inspected this pass, unchanged since", unchanged) + line("Changed since inspected", changed)


def main() -> int:
    identity = try_resolve_repo_identity(working_directory(read_hook_payload()))
    context = DISCIPLINE
    if identity is None:
        context += "\nWorkflow state unavailable; do not infer that any workflow step passed."
    else:
        context += "\n" + summary(identity) + _reads_context(identity)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
