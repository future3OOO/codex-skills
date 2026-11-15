#!/usr/bin/env python3
"""Rearm compacted context and hold a lead Stop once per unchanged open pass."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import read_hook_payload, reset_advisories, working_directory  # noqa: E402
from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree, _comparison_scope, atomic_write_json, read_json, state_root  # noqa: E402
from hooks.lib.workflow_state import STEPS, WorkflowError, read_workflow, summary  # noqa: E402

DISCIPLINE = """Discipline re-arm: resume the active repo-production-workflow pass at its next unmet requirement, using retained contract, context and bound evidence. Load only missing or changed context. The mock ban, demonstrated-risk and root-cause rules still apply. State records proof, never Git authorization; missing evidence is pending."""


def main() -> int:
    payload = read_hook_payload()
    if payload.get("hook_event_name") == "PostCompact":
        reset_advisories(payload.get("session_id"))
        return 0
    stopping = payload.get("hook_event_name") == "Stop"
    if payload.get("hook_event_name") == "SubagentStop" or stopping and (
        os.environ.get("ADVISOR_ACTIVE") or os.environ.get("CODEX_ADVISOR_ACTIVE")
    ):
        return 0
    context = DISCIPLINE
    try:
        with _comparison_scope():
            identity = try_resolve_repo_identity(working_directory(payload))
            if stopping:
                state = read_workflow(identity) if identity else None
                session = payload.get("session_id")
                if not state or state.get("phase") == "complete" or not isinstance(session, str) or not session:
                    return 0
                if STEPS["final-review"][1](state):
                    return 0
                candidate = _active_candidate_tree(identity)
                # Summary is deliberately truncated; the complete state and live candidate decide sameness.
                fingerprint = hashlib.sha256(json.dumps([state, candidate], sort_keys=True).encode()).hexdigest()
                record = state_root() / "_stops" / (hashlib.sha256(session.encode()).hexdigest() + ".json")
                if read_json(record) == {"fingerprint": fingerprint}:
                    return 0
                context = summary(identity, labels=False)
                atomic_write_json(record, {"fingerprint": fingerprint})
                print(json.dumps({"decision": "block", "reason": context + "\nCode is truth: wording about behavior the request keeps summarizes the original code, so the original's result stands."}))
                return 0
            if identity is None:
                context += "\nWorkflow state unavailable; do not infer that any workflow step passed."
            else:
                context += f"\nTask worktree: {identity.root}\n" + summary(identity, labels=False)
    except (RepoIdentityError, WorkflowError, LedgerError, OSError, ValueError) as exc:
        if stopping:
            print(f"Workflow Stop state unavailable: {exc}", file=sys.stderr)
            return 0
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
