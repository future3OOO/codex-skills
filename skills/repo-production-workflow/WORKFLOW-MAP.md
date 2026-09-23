# Production workflow map

The ordered step registry in `hooks/lib/workflow_state.py` owns sequencing and
completion.

## State Interface

One repository-scoped SQLite event ledger records accepted transitions, logical evidence, review manifests, and complete canonical resulting state. A disposable projection names the active workflow and latest event; reads repair it from the ledger when it is missing, dangling, or stale. See [Workflow state root](https://github.com/future3OOO/claude-skills/blob/main/README.md#workflow-state-root) for which root holds it.

### `workflow status` contract

`status` returns the active semantic workflow state and logical evidence IDs as
JSON. It exposes no database internals. Missing state returns exit 2 without
creating one. Accepted transitions, evidence, and current state are committed
together; a failed mutation leaves them unchanged. A producer may record pending
evidence and return exit 2, so inspect the receipt rather than inferring no write
from the exit code. Missing or corrupt producer evidence keeps completion pending.

The ledger provides continuity across restarts and compaction. It never
authorizes Git. `complete` reads the ordered step registry and current Behavior
Map; `workflow summary` reports missing requirements.

## Edit invalidation and approval freshness

A production edit marks implementation in progress and resets verification,
code review, and final review. Governance-document edits reset only the
review chain. Plain docs and scratch edits leave the pass alone. Invalidation
precedes quality feedback, so a failing gate cannot leave stale readiness.

The lead review records raw working-tree path, mode, and content hashes,
including symlink targets and submodule commits. It does not trust Git's staged
blob or clean-filter output. Final checkpoint, advisor recording, and completion
compare that manifest against the current tree; drift names changed paths and
requires a fresh review and final consult. A missing manifest is pending.
The state lock serializes workflow writers, not concurrent filesystem edits.

## Hook roles

This section is the canonical operational documentation for hook behavior.
`~/.codex/config.toml` and the hook scripts remain the executable Interface:
where they disagree with this table, the code is correct and the table is the
defect. `AGENTS.md` — GitNexus keeps only the facts that change lead action each
session and defers the rest here.

| Hook | Role |
|---|---|
| `PreToolUse(Edit\|Write\|apply_patch)` | Advise, never refuse: name what the pass has not recorded and admit the edit; docs, scratch, and non-repository paths are silent; test-like paths skip only the RED advice |
| `PostToolUse(Edit\|Write\|apply_patch)` | Invalidate downstream readiness, record the session's repository association where a pass exists, then return quality feedback — the gate run carries the pass's recorded base OID as `--base-ref` when bootstrap recorded one, so growth warnings read branch-cumulative per edit; with no recorded base the hook derives nothing and the gate reports the base-binding gap |
| `SessionStart(compact)` | Restore the full workflow chain and bounded current summary from committed SQLite state |
| `PostCompact` | Reset advisory dedup for this session so unchanged obligations can be shown again in the new context window |

PreToolUse and PostToolUse advisories emit when their text changes for a
repository in the current session. Identical repeats stay silent until
PostCompact starts a new compaction epoch.

Session associations let compact recovery find repositories edited in this
session. Anonymous payloads create none; storage failure cannot change an edit
outcome. The association and advisory cache are disposable and never authorize a
transition.
