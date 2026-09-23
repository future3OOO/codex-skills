---
name: repo-production-workflow
description: Orchestrate production repository changes from context through final review, delivery, reviewer closure, and workflow completion. State is continuity only and never authorizes Git.
---

# Repo production workflow

Use this skill only when production changes are required: code, configuration,
runtime, deploy, generated source, or production behavior. `AGENTS.md` owns the hard
invariants and GitNexus doctrine; [INVARIANT-OWNERSHIP.md](INVARIANT-OWNERSHIP.md)
maps the remaining owners.

## Baseline and candidate execution

For behavior changes, before baseline measurements or production edits, establish
unchanged N and candidate N+1 execution with isolated mutable state. Reuse project
environments and build/install/refresh commands to run worktree changes in N+1;
reload cached consumers. Setup is ready when both loaded implementations are
verified and the same real production operation can run against each. Keep candidate
bindings current and report unavailable comparisons under
[Production Code's verification rules](../production-code/SKILL.md#minimum-implementation-decision).

## One stable workflow

Follow [AGENTS.md](../../AGENTS.md#production-repo-workflow): select a task
worktree and branch, then relocate this session into it as the turn's last
action before `begin`:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/codex-relocate" "<absolute whitespace-free task-worktree>"
```

Begin with the exact request and any referenced issue body, not a paraphrase:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  begin --repo "$PWD" --slug "<task>" --intent -
```

The repository-scoped SQLite ledger is continuity across restarts and
compaction, never an attestation or Git approval. `status` exposes the
semantic workflow state and logical evidence IDs; missing state exits 2.

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run its adapter with the same slug and intent:

```bash
python3 "$HOME/.codex/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<task>" --intent "<user request>"
```

Stop on packet blockers and satisfy its coverage plan. The adapter records
graph evidence and the resolved base OID.

### 2. Task contract and diagnosis

Use [Production Code's outcome verification](../production-code/SKILL.md#minimum-implementation-decision)
to define the task contract, preserved surfaces, and review budget. Apply
`diagnose` before fixing bugs or regressions.

### 3. Packet-scoped GitNexus

Read the packet's executed context/impact checks; run only missing graph calls.
The adapter records their result. There is no `gitnexus` workflow step.

### 4. Advisor scope check

Invoke [codex-advisor](../codex-advisor/SKILL.md#preflight-advice) with phase
`preflight-advice`, supplying the contract, proof and no-change surfaces.
Invoke `codebase-design` first for a new or changed Module, public Interface,
or Seam. The wrapper records the consult; pending findings block completion,
not edits or verification.

### 5. Production preflight

Invoke [production-preflight](../production-preflight/SKILL.md#recording) before
tracked production edits. It owns the initial Behavior Map; the governing
artifact owns architecture and execution order. Resolve material unknowns
before dependent work. Transaction-sensitive work uses the
[transaction doctrine](../production-code/references/transaction-doctrine.md).

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  record preflight --repo "$PWD" --input <preflight.json>
```

### 6. Mapped TDD RED or not-required

Invoke `tdd` for each pending Behavior Map item. Its RED must exercise the
recorded real Seam and observe that item's `redFailure`; setup, collection,
missing-API, and inherited failures do not count. [TDD's recorder](../tdd/recorder.md)
owns admission and preservation rules.

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase red --behavior-id "BM_..." -- <targeted-command>
```

Use `tdd --not-required "<reason>"` only when every map item is proved
already-satisfied or validly omitted. Late RED is labelled for final review;
unresolved items block closure, not the edit hook.

### 7. Production code

Apply [production-code](../production-code/SKILL.md) after RED or a recorded
not-required decision. Run its typed gate on the pre-implementation tree.

### 8. Implementation

Make the smallest change; PostToolUse invalidates
verification and review after production edits. Drive GREEN on the same mapped
Seam, then use `record map --input -` for new or changed obligations under
[TDD's reassessment rules](../tdd/recorder.md). Reference-only updates preserve
completed downstream checks. There is no implementation acknowledgement.

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase green --behavior-id "BM_..." -- <same test surface>
```

### 9. Verification

Run affected real-Seam probes, lint/typecheck/build, and the typed quality
gate on the current tree. Rerun only missing or invalidated operations. Refresh
Repo Context Forge when its graph binding is stale; CI's `contracts` job owns
the full runner.

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  verify --repo "$PWD" --slug "<task>" -- <verification command>
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  verify --repo "$PWD" --slug "<task>" --kind quality-gate --base-ref "<base>"
```

A failed generic run remains pending until that command succeeds. Correct an
invocation with `verify --replaces <evidenceId>:<runIndex> --reason "<correction>"`
and a current successful rerun; another command cannot retire it. The typed
gate must cover the final reviewable tree.

### 10. Delegate code review

Obtain independent [code-review](../code-review/SKILL.md) of the current
candidate and original objective after verification. Record its actual intake
through `workflow.py record review --input <review.json>`. Ordinary review may
omit `--review-context-id`; recurring repair certification uses real context
identities. Reconcile each finding against source and measured proof. Material
findings block completion, while verification and review remain available.

For a recurring behavioral repair, keep the same finding identity. At recurrence
two the retained reviewer implements and the lead independently certifies;
[AGENTS.md](../../AGENTS.md#reviewer-findings-and-completion) owns reviewer
closure. Behavioral fixes require the owning GREEN-through-RED attack and
measured domain; nonbehavioral fixes require current-tree proof. Use the
[advisor disposition rules](../codex-advisor/SKILL.md#failure-and-disposition)
for measured shapes.

### 11. Final Codex Advisor review

Invoke [codex-advisor](../codex-advisor/SKILL.md#final-review) with phase
`final-review` on the current candidate after independent review. The wrapper's
checkpoint supplies the original objective and diff anchors. Reconcile known
material gaps before consulting; missing acceptance evidence forbids
`commit-ready`. Record effective finding dispositions, then repeat invalidated
verification and review after any production correction.

### 12. Delivery and reviewer completion

After final advice, commit, push, and open/update the PR when integration is
intended. Keep the pass active through the current-head reviewer gate in
[AGENTS.md](../../AGENTS.md#reviewer-findings-and-completion). Merge only with
explicit maintainer authorization.

### 13. Complete the workflow

Complete after current-head reviewer closure, or after final review on an
intentionally local-only route:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" complete --repo "$PWD"
```

`complete` checks the current map, effective findings, required producer
evidence, final advisor identity, and reviewed tree inside one transaction.
State completion never authorizes Git. For no-PR work, report why no PR exists.

## Failure semantics

Missing or corrupt state is pending, never success. `workflow.py summary`
restores bounded identity, evidence and next action; `status --fields` loads
specific facts. Resume the same pass after compaction. [WORKFLOW-MAP.md](WORKFLOW-MAP.md)
owns hook roles.
