# Production workflow map

The ordered step registry in `hooks/lib/workflow_state.py` owns sequencing,
completion, and the next action. `workflow.py status` exposes the active semantic
state and logical evidence IDs; missing state exits 2. The SQLite ledger keeps
event metadata, immutable evidence, and one current state per workflow. Its
projection can be repaired from that state. It provides continuity, never Git
authorization.

A production edit invalidates verification and review; a governance-doc edit
invalidates the review chain. Plain docs and scratch edits leave the pass alone.
The review manifest binds working-tree paths, modes, and content hashes. Final
review and completion refuse drift from the reviewed tree.

| Hook | Role |
|---|---|
| `PreToolUse(Bash\|apply_patch)` | Capture a lone test command or advise before a reviewable edit; admit the edit. |
| `PreToolUse(Agent\|delegation tools)` | Refuse delegation while review blockers remain. |
| `PostToolUse(Bash\|apply_patch)` | Invalidate stale readiness and return local quality feedback. |
| `SessionStart(compact)` | Restore workflow instructions and bounded status. |
| `PostCompact` | Reset this session's advisory deduplication. |

Advisories emit each distinct text once per session and repository within a
compaction epoch. PostCompact starts a new epoch so held reminders return.
