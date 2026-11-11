---
name: repo-production-workflow
description: Orchestrate production repository changes from context through final review, delivery, reviewer closure, and workflow completion. State is continuity only and never authorizes Git.
---

# Repo production workflow

Use this skill only when production changes are required: code, configuration,
runtime, deploy, generated source, or production behavior. `AGENTS.md` owns the hard
invariants and GitNexus doctrine; [INVARIANT-OWNERSHIP.md](INVARIANT-OWNERSHIP.md)
maps the remaining owners.

Workflow governance and project verification have different responsibilities.
Repo Production Workflow establishes context, coordinates work, runs source
comparisons and delivers evidence. It does not prescribe bootstrap as the project's
test Interface. Apply the same rule when editing the workflow itself: reuse valid
source-bound evidence instead of repeating project verification at each stage.

## One stable workflow

Follow AGENTS.md's Production Repo Workflow section for isolation and pass reuse.
After the required relocation confirms the task checkout, use `begin` for a new
task. Reuse its slug and pass for related corrections, including after review.

In a fresh session `begin` records the opening message verbatim; add `--intent-file`
with the verbatim body of a document it names. Deep in a session, or outside Codex,
pass the task's exact requirements, in the user's words, with `--intent-file`.

The repository-scoped SQLite event ledger remembers accepted transitions, logical evidence, phase, and next action across process restarts. Its disposable active projection is repaired from that history. It is agent-writable workflow continuity, not an attestation, approval, audit credential, or Git boundary.

## Continuation

Follow the operation result's `next.command`; `next.input` names any judgment,
document or argument still needed and its form. Retain the returned evidence IDs.
On resume or after edits, `summary` gives current recovery guidance and
`status --fields` supplies missing facts. Resume the same pass.
The returned continuation carries each next command. The lead's own forms
(`workflow.py` is `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py"`;
[recorder.md](../tdd/recorder.md#record-a-case) has the `tdd` forms):

```text
workflow.py begin --repo "$PWD" --slug <task> --intent-file <request.md>
workflow.py summary --repo "$PWD"
workflow.py status --repo "$PWD" --fields nextAction,tdd,verification,codeReview,finalReview
workflow.py record preflight --repo "$PWD"
workflow.py verify --repo "$PWD"
workflow.py evidence --repo "$PWD" --evidence-id <id> --full
workflow.py complete --repo "$PWD"
```

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run the bootstrap command `begin` returned as `next.command`.
Stop on packet blockers. The packet fixes the initial target and coverage surface.

### 2. Task contract and diagnosis

Derive verification from the user's intended production behavior using
[Production Code's outcome verification](../production-code/SKILL.md#minimum-implementation-decision).
The lead owns correctness and optimization for every task, through implementation
and repair; the independent reviewer challenges it. Use the packet to trace affected paths, state skipped and
preserved surfaces, and establish review-budget fit. Apply `diagnose` to bugs,
regressions and performance failures before choosing a correction.

### 3. Packet-scoped GitNexus

The bootstrap already ran and recorded the packet's graph checks; there is no separate
graph step. Run further MCP checks only when they widen the surface the packet fixed.

### 4. Draft and advisor review

Invoke `production-preflight` and draft its two-field artifact before consulting.
Its map states required outcomes, concrete inputs and any competing readings. Invoke
`codebase-design` when changing a Module, public Interface or Seam. The draft owns
the contract and planned attacks.

Submit the exact draft through [Codex Advisor's two-round preflight](../codex-advisor/SKILL.md#preflight-advice).
The advisor investigates. The lead checks deletion claims against every original
outcome controlled by the affected logic and corrects false premises. The advisor
then finalises and approves the draft in the same session. Record only that approval.

### 5. Record approved preflight once

Record the approved draft through the returned continuation. Related corrections stay in this pass; reuse untouched proof.

### 6. Select the probe batch

Invoke `tdd` before editing, then select the direct batch each map item will run through under
AGENTS.md's Real-Seam proof and Attack probes first invariants. Record its original baseline before editing production.

### 7. Production code

Invoke `production-code` for its standards and baseline gate. Reuse the existing Module and remove the machinery the change replaces.

### 8. Implement and compare

Continue TDD's [required probe loop](../tdd/SKILL.md#required-probe-loop): implement the chosen
design against the recorded baseline, reconcile the actual diff, and compare the same
case commands on the candidate. Add commands for uncovered outcomes; reuse unchanged proof.

The lead resolves named-case obligations against the original and request, then verifies and obtains the existing independent review. After final review starts, targeted repair returns to that final advisor.

### 9. Verification

Assess the intended outcome against step 2's verification. Reuse current real
comparisons; run missing or invalidated operations and required lint, type checks
and build. The typed quality gate and retained findings must pass on the current
candidate. Follow the receipt for stale comparisons, failed checks and continuation.

### 10. Delegate code review

Do not spawn or task the reviewer until the lead has completed the investigation,
repair, real outcome assessment and verification in steps 2–9. Missing lead proof
is work for the lead before initial or return review.

Before final advisor review, obtain independent `code-review` of the original
objective and current candidate. Lead self-cleanup and later GitHub review do not replace this
step. For initial non-trivial review use a fresh native Codex background
delegate (`spawn_agent`, `agent_type=default`, `fork_turns="none"`, normal native
model selection) in the lead's native task checkout. Supply the target and correction delta; instruct it to apply `code-review`,
which loads the request, contract, comparison outcomes and diff itself.
Wait for it in one `wait_agent` call with `timeout_ms` 1800000, without editing the candidate; on timeout, wait again rather than re-tasking. The reviewer records executable findings and
its source-bound verdict through the existing workflow command, then returns its
Standards/Spec report and receipt references. Follow the runtime continuation.
For genuinely trivial work, use the runtime's existing not-required operation.

Retain the reviewer agent and finding identities. Resume that context for
corrections with the changed evidence. Do not reload unchanged skills or repeat
valid execution solely for handoff. The lead owns repairs, TDD and verification;
the reviewer owns its evidence and verdict. Start a fresh reviewer only when its
context is unavailable or scope/architecture makes it unusable, naming the reason.

Required behavior stays fixed. Seek the lowest justified whole-system cost in the
affected production path, including existing bloat. A replacement must preserve
frozen proof and required surviving checks; necessary growth must buy demonstrated
cost reduction. The runtime owns acceptance and rechecks retained findings after
repair, including reopening when waste returns. For a missing request promise,
reconcile the requirement and extend affected coverage before implementing it.
Every map item is closed before review. Verification and return review can run
with open reviewer findings; those block completion.

### 11. Final Codex Advisor review

Before the consult, reconcile known material obligations using step 2's verification
and the delegate's findings. Reference the applicable observations and unresolved
acceptance gaps; load only missing evidence, not the verification history.

The final Codex Advisor investigates in the lead checkout using the recorded project executable and original source. Invoke the wrapper's
`final-review` phase with the same slug and follow its runtime continuation. The
advisor records probes through the shared TDD command; the wrapper records its
semantic verdict. After the first final verdict, a correction
needs the affected `tdd` rerun, verification for the new tree, and the final advisor's re-check. Reuse independent review and applicable evidence. Retained findings run only at `verify` or `complete`; intermediate review calls skip them. Once every
final finding is settled (`nextAction` `complete-workflow`), a requested
reassessment of the unchanged candidate runs the same `final-review` phase and records
as a fresh final result.

### 12. Delivery and reviewer completion

After the final advisor finds the candidate ready, commit, push, and open/update
the PR when intended for integration. Run AGENTS.md's reviewer completion gate
(Reviewer Findings And Completion) on the current head. Merge only with explicit
maintainer authorization; passing checks and reviews do not authorize merge.
Keep the pass active through reviewer closure; corrections repeat only the
affected steps, including verification and independent review.

### 13. Complete the workflow

Complete after the current-head reviewer gate closes, or on the no-PR route below.

`complete` first checks uncached retained findings for its candidate tree. Verdict and withdrawal records only write state; they never run probes or acquire execution locks. A replacement that produces the candidate tree runs only its own probe; current candidate proof covers maintained cases. Other replacements replay every maintained case on the candidate plus the diff, once per candidate tree and resulting tree.

`complete` refuses, from inside its transaction, unless every current probe has fresh successful comparison evidence and no proof gap remains, required phases are ready, and no binding code-review or final finding is open. The immutable raw verdict remains evidence; `context-mismatch` still blocks. The reviewable working tree must match the manifest recorded by the lead review, and every evidence phase must carry its producer's evidence reference — a passed phase without one is a bare claim and reads pending, including legacy in-flight state at upgrade time. It changes workflow state only. It does not inspect, intercept, authorize, or execute Git.

When the completed work is intentionally not delivered as a PR — local-only
config, an estate sync, or work the user told you not to push — the no-PR
route is: complete the workflow, report the change and its verification in the
final response, and name why no PR exists. The completed state then simply
remains until the next `begin` replaces it; no reviewer gate applies.

## Failure semantics

Missing or corrupt workflow state is pending, never success. For an unavailable
advisor, follow [Failure and disposition](../codex-advisor/SKILL.md#failure-and-disposition).
Incoming findings can be recorded while verification is pending; that receipt
does not certify review. [Hook roles](WORKFLOW-MAP.md#hook-roles) owns edit observation
and documentation exceptions. Use the task repository as the tool workdir.

Locate the file or symbol first; read the returned path, never a filename inferred
from its concept. Retain that owner path with its supported command across resumes.
Keep searches separate from independent actions; handle expected no-match results
explicitly. Serialize ledger mutations and stop dependent batches on unexpected
failure (`set -euo pipefail`; Python `check=True`).
