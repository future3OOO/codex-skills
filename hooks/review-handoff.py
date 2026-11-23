#!/usr/bin/env python3
"""PreToolUse: once verification passed on the current candidate and code review is next, the host runs no call before the lead delegates to the reviewer."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import emit, read_hook_payload, working_directory  # noqa: E402
from hooks.lib._workflow_db import LedgerError, read_active  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree, atomic_write_json, read_json, state_root  # noqa: E402

payload = read_hook_payload()
tool, call = str(payload.get("tool_name")), payload.get("tool_input")
try:
    identity = try_resolve_repo_identity(working_directory(payload))
    state = read_active(identity) if identity else None
    if not state or state.get("nextAction") != "code-review" or state.get("verification") != "passed":
        sys.exit()
    if state.get("findingStates"):  # a pass recorded before repair routing can still say code-review
        from hooks.lib.workflow_state import _review_repair_pending
        if _review_repair_pending(state):
            sys.exit()
    delegated = {"workflowId": state["workflowId"], "candidate": _active_candidate_tree(identity)}
    record = state_root() / "_handoff" / f"{identity.key}.json"
    if state.get("activeCandidateTree") != delegated["candidate"] or read_json(record) == delegated:
        sys.exit()  # an edited candidate is verified again first; a reviewer already tasked with this one is at work
    if tool.endswith(("spawn_agent", "followup_task")):
        atomic_write_json(record, delegated)
    if tool.startswith("collaboration") or isinstance(call, dict) and (call.get("args") or [None])[0] == "set-phase":
        sys.exit()
except (RepoIdentityError, LedgerError, OSError, ValueError):
    sys.exit()
emit("PreToolUse", permissionDecision="deny", permissionDecisionReason="Not run: verification passed on this candidate and code review is next. Delegate to "
     "the pass's reviewer now (spawn_agent with agent_type=reviewer, or followup_task to the reviewer you kept); it reads the recorded evidence itself.")
