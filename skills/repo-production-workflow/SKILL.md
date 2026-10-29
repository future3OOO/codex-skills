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
After creating or selecting the task worktree, move this session's root into
it before `begin` — the session checkout is the pass's `--repo`, and only a
rooted session gives delegates, hooks, and advisors the same checkout:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/codex-relocate" "<task-worktree>"
```

The `<task-worktree>` must be an absolute, whitespace-free path. Run relocation
as the turn's last action. Native `codex`/`codexs` sessions resume the same thread
through their owned launcher; without it, follow the printed `resume -C` command.
An explicit app-server request remains pending until a subsequent turn confirms
the target cwd and hook repository. Continue only in that confirmed checkout.
For a new task, choose one short slug; `begin` creates and activates its state
for that worktree before bootstrap:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" begin --repo "$PWD" --slug "<task>"
```

In a fresh session `begin` records the opening message verbatim; add `--intent-file`
with the verbatim body of a document it names. Deep in a session, or outside Codex,
pass the task's exact requirements, in the user's words, with `--intent-file`.

The repository-scoped SQLite event ledger remembers accepted transitions, logical evidence, phase, and next action across process restarts. Its disposable active projection is repaired from that history. It is agent-writable workflow continuity, not an attestation, approval, audit credential, or Git boundary.

## Continuation

Follow the operation result's `next.command`; `next.input` names any judgment,
document or argument still needed and its form. Retain the returned evidence IDs.
On resume or after edits,
use `workflow.py summary --repo <checkout>` for current recovery guidance;
`status --fields <fields>` supplies missing facts. Resume the same pass.
See [State Interface](WORKFLOW-MAP.md#state-interface) for receipt and status fields.

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run the bootstrap command `begin` returned as `next.command`.
Stop on packet blockers. The packet fixes the initial target and coverage surface.

### 2. Task contract and diagnosis

Derive verification from the user's intended production behavior using
[Production Code's outcome verification](../production-code/SKILL.md#minimum-implementation-decision).
The lead owns that investigation through implementation and repair; the independent
reviewer challenges it. Use the packet to trace affected paths, state skipped and
preserved surfaces, and establish review-budget fit. Apply `diagnose` to bugs,
regressions and performance failures before choosing a correction.

### 3. Packet-scoped GitNexus

The bootstrap already ran and recorded the packet's graph checks; there is no separate
graph step. Run further MCP checks only when they widen the surface the packet fixed.

### 4. Draft and advisor review

Invoke `production-preflight` and draft its two-field artifact before consulting.
Its map follows TDD's [Behavior Map](../tdd/recorder.md#behavior-map): each item's
`kind`, its required boundary cases and any competing readings. Invoke
`codebase-design` when changing a Module, public Interface or Seam. The draft owns
the contract and planned attacks.

Submit the exact draft through [Codex Advisor's preflight loop](../codex-advisor/SKILL.md#preflight-advice).
Continue to recording only after `approved`.

### 5. Record approved preflight once

Record the approved contract and probe list with `record preflight` (no input: it records the advisor-approved draft). Keep this workflow pass when scope changes; update changed items by identity through `record tdd-map`; untouched items and valid proof remain.

### 6. Select the probe batch

Invoke `tdd` before editing, then select the direct batch each map item will run through under
AGENTS.md's Real-Seam proof and Attack probes first invariants. The comparison runs after the edit (step 8).

### 7. Production code

Invoke `production-code` for its standards and baseline gate. Reuse the existing Module and remove the machinery the change replaces.

### 8. Implement and compare

Make the smallest change, then execute TDD's
[required probe loop](../tdd/SKILL.md#required-probe-loop): record the edit's decisive
contexts and boundary cases as map items, extend the existing direct batch, and compare
the identical expanded batch on the recorded original and candidate sources.

The receipt's `open` lines are the questions the comparison leaves: answer each by
repairing the code or the probe and rerunning the same batch. Readiness is that one
result; `summary`, `verify`, reviewer dispatch and `complete` report it identically.

### 9. Verification

After coherent repair and cleanup, assess the intended outcome against the
verification derived in step 2. Run the typed quality gate; it runs no comparison. A
source edit leaves recorded comparisons stale, and every reader then routes to `tdd`
until the lead reruns them on the current candidate.
Reuse current comparisons; run lint, type checks and tests directly.
CI's `contracts` job owns the full runner here and step 12 waits for it. A gate run whose
reviewable tree drifts stays invalid and names the changed paths; on an unchanged tree
and base, `verify` returns the passed receipt:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" verify
```

Completion requires the typed `quality-gate` run over the current reviewable tree.

### 10. Delegate code review

Do not spawn or task the reviewer until the lead has completed the investigation,
repair, real outcome assessment and verification in steps 2–9. Missing lead proof
is work for the lead, not an investigation to offload to the reviewer. This also
applies before return review; preflight exploration is confined to before preflight.
The existing tool hook blocks governed delegation while prerequisites or the
verified tree are stale; readiness does not substitute for assessing real outcomes.

Before final advisor review, obtain independent `code-review` of the original
objective and current candidate. Lead self-cleanup and later GitHub review do not replace this
step. For initial non-trivial review use a fresh native Codex background
delegate (`spawn_agent`, `agent_type=default`, `fork_turns="none"`, normal native
model selection) in the lead's native task checkout. Supply the target and correction delta; instruct it to apply `code-review`,
which loads the request, contract, comparison outcomes and diff itself.
Wait without editing the candidate. It returns a
Standards/Spec review and a findings intake. Historical behavior
is contextual evidence only — a current Interface claim needs current documentation,
callers, tests, or another active authority. In this governed workflow `workflow.py record review` is the required producer for non-trivial review state; outside the governed
workflow it stays optional. For a genuinely trivial change, record
`set-phase --phase code-review --status not-required --findings none`.

Retain its agent and intake IDs. Resume it via `followup_task` with the correction
delta, finding IDs and changed/missing evidence.
Do not reload unchanged skills or repeat execution solely for handoff. Keep the
reviewer read-only and assign each needed operation once; the lead owns repairs,
TDD and verification recording. Use a fresh reviewer when context is
unavailable or changed scope/architecture makes it unusable, naming that reason.
Historical receipts retain their original identity. Every review must describe
the current candidate.

Record the delegate's intake. Code is proof: a finding binds only through evidence the
runner executes itself when it is recorded, and anything else is a report line. A `probe`
(`{"path","content","command"}`, a new standalone test file run by pytest or unittest) binds
when it fails on the candidate; it is run on the original too, so a pass there marks a
regression. A `change` (a unified diff that only deletes code or replaces it with one call to
an existing function) binds when every retained comparison batch still passes on the candidate
with it applied; only the changed copy runs.

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record review --input <review.json>
```

A no-finding intake binds the reviewed tree and passes immediately. Each later review intake
and final re-check re-executes the stage's open binding findings on the current candidate: a
finding settles when its evidence no longer binds. Answer a finding only by fixing the code,
or by an executed counter-probe mapped to it (`record tdd-map` `sourceRefs`): one that fails
with a change applied settles that change, and one proving a both-trees-failing probe's
expectation wrong settles it beside a re-check with no binding finding. A regression is never
countered. Prose dispositions of review and final findings refuse. Verification, the typed
gate, and a new review all run while findings are open; open findings block completion only.
A later map update that would leave a fixed finding without its owning attack refuses.

### 11. Final Codex Advisor review

Before the consult, reconcile known material obligations using step 2's verification
and the delegate's findings. Reference the applicable observations and unresolved
acceptance gaps; load only missing evidence, not the verification history.

The final Codex Advisor verifies the candidate by running code in a disposable copy of it,
with the project's interpreter. Invoke it against the live diff with wrapper phase
`final-review` and the same slug; the checkpoint supplies the diff anchors. It returns findings
in the same executable forms; the runner executes them and derives the verdict, and the wrapper
returns each finding's execution with the recorded `next`; follow it. After the first final verdict, a correction
needs only the `tdd` rerun of its items and the final advisor's re-check: code review,
verification and context revalidation are not repeated. Reuse applicable evidence. Once every
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

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  complete --repo "$PWD"
```

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
