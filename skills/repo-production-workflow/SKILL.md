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
printf '%s' "$request_text" | python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" begin \
  --repo "$PWD" --slug "<task>" --intent -
# or, when the caller already has the request in a file:
#   ... begin --repo "$PWD" --slug "<task>" --intent-file "<path>"
```

Pass the request text, not a summary: build `$request_text` in a file from the
message, append the verbatim body of any issue or spec it names, and feed that
file — shell quoting mangles a long request passed inline. The recorded intent
is the contract the rest of the pass is answerable to, so it is stored exactly as
given (valid UTF-8; U+0000 refused) and read back at the plan-commit gate and in
every advisor consult; a paraphrase written here is the paraphrase those steps
will enforce. `--intent "<text>"` still takes a literal argument, and
`--intent`/`--intent-file` are mutually exclusive.

The repository-scoped SQLite event ledger remembers accepted transitions, logical evidence, phase, and next action across process restarts. Its disposable active projection is repaired from that history. It is agent-writable workflow continuity, not an attestation, approval, audit credential, or Git boundary.

## Continuation

Follow the operation result's `next.command`; `next.input` names any judgment or
document still needed. Consult `next.help` for an unfamiliar input and retain
the supported invocation and returned evidence IDs. On resume or after edits,
use `workflow.py summary --repo <checkout>` for current recovery guidance;
`status --fields <fields>` supplies missing facts. Resume the same pass.
See [State Interface](WORKFLOW-MAP.md#state-interface) for receipt and status fields.

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run its adapter with the same slug and intent:

```bash
python3 "$HOME/.codex/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<task>" --intent "<user request>"
```

Stop on packet blockers. The packet fixes the initial target and coverage
surface. When the packet resolves a real base, the adapter also records its
fork-point commit as the pass's immutable base OID (`baseOid` in the status
projection); the per-edit gate hook passes it as `--base-ref` so growth reads
branch-cumulative throughout implementation.

### 2. Task contract and diagnosis

Derive verification from the user's intended production behavior using
[Production Code's outcome verification](../production-code/SKILL.md#minimum-implementation-decision).
The lead owns that investigation through implementation and repair; the independent
reviewer challenges it. Use the packet to trace affected paths, state skipped and
preserved surfaces, and establish review-budget fit. Apply `diagnose` to bugs,
regressions and performance failures before choosing a correction.

### 3. Packet-scoped GitNexus

Repo Context Forge executes the packet's required context/impact checks and its
adapter records that resolved graph result as `repo-context-forge` evidence, in
the same transaction as the step. There is no separate transition to record, and
no separate graph step exists. Read the packet's graph
result; run further MCP checks when they widen the surface the packet fixed.

### 4. Draft and advisor review

Invoke `production-preflight` and draft its two-field artifact before consulting.
Its map follows TDD's [Behavior Map](../tdd/recorder.md#behavior-map): each item's
`kind`, its required boundary cases and any competing readings. Invoke
`codebase-design` when changing a Module, public Interface or Seam. The draft owns
the contract and planned attacks.

Submit the exact draft through [Codex Advisor's preflight loop](../codex-advisor/SKILL.md#preflight-advice),
with the governing-design declaration. Continue to recording only after `approved`.

### 5. Record approved preflight once

Record the exact approved contract and probe list with `record preflight --input FILE`. Keep this workflow pass when scope changes; update changed items by identity through `record tdd-map`; untouched items and valid proof remain.

### 6. Select the probe batch

Invoke `tdd` before editing, then select the direct batch each map item will run through, under
AGENTS.md's Real-Seam proof invariant: reuse existing cases, fixtures and assertions;
add a probe only for behavior no existing case covers. Entrypoint absence does not
prove independent guarantees. The comparison itself runs after the edit (step 8).

### 7. Production code

Invoke `production-code` for its standards and baseline gate. Reuse the existing Module and remove the machinery the change replaces.

### 8. Implement and compare

Make the smallest change, then execute TDD's
[required probe loop](../tdd/SKILL.md#required-probe-loop): record the edit's decisive
contexts and boundary cases as map items, extend the existing direct batch, and compare
the identical expanded batch on the recorded original and candidate sources:

```text
workflow.py tdd --behavior-id BM_X -- COMMAND [ARG...]
```

The receipt's `open` lines are the questions the comparison leaves: answer each by
repairing the code or the probe and rerunning the same batch. Readiness is that one
result; `summary`, `verify`, reviewer dispatch and `complete` report it identically.

Source and probe edits invalidate affected proof and reopen required verification/review.

### 9. Verification

After coherent repair and cleanup, assess the intended outcome against the
verification derived in step 2. Run the typed quality gate; it runs no comparison. A
source edit leaves recorded comparisons stale, and every reader then routes to `tdd`
until the lead reruns them on the current candidate. Before another test command, identify the
assertion those comparisons cannot establish. Extend the existing targeted batch
for that gap and keep enclosing-operation checks only for their own assertions.
Never run existing test classes, modules or suites; CI owns them.
Reuse current comparisons; use generic verification for required lint/typecheck/build,
with graph reanalysis when required. Follow AGENTS.md's attack-probe and verification rules.
CI's `contracts` job owns the full runner here and step 12 waits for it.
Verification records only through the unified CLI runner, which executes the command it records and derives status
per-command-latest for explicit verification. Failed explicit checks need a passing rerun
or valid `--replaces` correction. Overlapping runs record in completion order; a run whose
reviewable tree drifts stays invalid and names the changed paths:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" verify --kind quality-gate
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" verify -- <verification command>
```

`verify --from-evidence <evidenceId>:<runIndex>` binds a captured execution receipt without replay.

Typed verification needs no generic acknowledgement or dummy command. Correct a
failed generic invocation with `verify --replaces <evidenceId>:<runIndex> --reason
"<correction>" -- <command>`; only a valid current success retires that particular
active failure. Other failures, stale/concurrent results and drift stay effective.
Which commands suffice remains review judgment. Completion requires the typed `quality-gate` run over the current reviewable tree.

The typed runner uses the recorded graph input; reuse it when its binding and
scope match the candidate. Refresh Repo Context Forge after relevant edits or
when evidence is absent/stale. The gate's binding check adjudicates applicability;
unchanged source alone does not establish coverage for a broadened contract.

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
Standards/Spec review and a findings intake. Verify every finding and
disposition each one. A disposition is invalid
without its measurement; advisor agreement is not authorization; historical behavior
is contextual evidence only — a current Interface claim needs current documentation,
callers, tests, or another active authority. In this governed workflow `workflow.py record review` is the required producer for non-trivial review state; outside the governed
workflow it stays optional. For a genuinely trivial change, record
`set-phase --phase code-review --status not-required --findings none`.

Retain its agent and intake IDs. Resume it via `followup_task` with the correction
delta, finding IDs and changed/missing evidence.
Do not reload unchanged skills or repeat execution solely for handoff. Keep the
reviewer read-only and assign each needed operation once; the lead owns repairs,
TDD/verification recording and dispositions. Use a fresh reviewer when context is
unavailable or changed scope/architecture makes it unusable, naming that reason.
Historical receipts retain their original identity. Every review must describe
the current candidate.

Record the delegate's JSON intake (`{"findings":[{"id","claim","material","kind"}]}`),
then, when it has findings, dispositions against the returned `summaryId`:
`{"intakeEvidenceId":"<summaryId>","dispositions":[{"finding_id":"R-1","status":"fixed","reason":"..."}]}`.
`record review --help` prints both shapes; a document carrying both refuses.
Pass `--review-context-id <agent-id>` with the delegate's review: a second
recurrence hands its repair to the first reviewer a review names.

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record review --input <review.json> --review-context-id <agent-id>
```

A no-finding intake binds the reviewed tree and passes immediately. A finding
intake stays pending until its appended dispositions resolve every material
finding. Dispositions may cover any subset of an intake; every material finding still
needs a terminal disposition before completion; a `material:false` note needs none. Verification, the typed gate, and a new review all run while findings
are open; open findings block completion only. A false premise records normalized `result`
exactly `false`; otherwise
rejection requires zero occurrence on a complete domain. `report-only` resolves
completion without authorizing an edit and cannot later become `fixed`. A
behavioral finding is fixed by owning it: add the attack item with its finding
`sourceRefs` through `record tdd-map`, run the owning comparison, then record the
`fixed` receipt above (`finding_id`, `status`, `reason`) or, for an advisor finding,
`record advisor-disposition --finding <ID> --fixed --behavior-id <BM_ID>`; the
recorder binds the owning comparison's current run. Nonbehavioral
corrections record their current-tree evidence directly. A later map update
that would leave a fixed finding without its owning attack refuses.

#### Recurring behavioral repairs

Findings and map items batch by owner. A finding names one owning Module and violated invariant and lists every demonstrated input as its cases; a later input to the same owner and invariant is a case of that finding through `priorFinding`, and its repair is the owner's class-wide correction. The map carries one contract and one preservation item per owning Interface, each decisive context a case of it. Observations, recurrence and dispositions stay immutable.

Before another repair, explain the missed cause, affected inputs/paths, invariants,
class-wide correction and latest counterexample; include reachable states/shared
writers where relevant. Record the diagnosis once in the existing finding reason,
or disposition `mechanism` prose/reference (`{"evidenceId":"...","id":"..."}`).
References preserve workflow/finding ownership. Review requires an adequate
explanation and current owning attacks; instance-only repairs remain
`accepted-follow-up` with the in-pass obligation open.

Both intake paths reconcile retained namespace/ID and exact behavioral claims
(outer whitespace only). Changed wording/identity can use an owned
`priorFinding: {"evidenceId":"...","id":"..."}`; ambiguous matches require one.
Distinct findings need distinct IDs. Pending retries preserve progress; only `fixed` → re-intake increments
recurrence. Observations, dispositions, mechanism evidence and proof stay immutable.

At recurrence two or later, the retained reviewer implements and the lead reviews. This
exception overrides step 10's read-only and repair-before-dispatch rules: continue
its recorded context, without a fresh/nested reviewer or interim advisor. Certify
through `record review` using the actual lead context and `implementationContextId`
for the repair author. Require current-candidate, independent certification and
product verification, then the mandatory final advisor. Ordinary assessment without
`implementationContextId` may proceed while ownership is pending; it neither
certifies the repair nor changes owners.

For unusable/wrong-checkout contexts, follow step 10's rooted fallback. Record
authorized succession in the lead-review intake as
`repairSuccession: {context, findings, previousOwner, evidence}`: current
workflow/candidate, affected `{evidenceId, id}` references, exact previous
implementer/reviewer pair, and authorization/native-checkout evidence. Author/reviewer
fields name actual successors. Only named pending recurring repairs transfer;
predecessor history remains and self-certification is forbidden.

After compaction, recover mechanism references and ownership from summary/checkpoint;
retrieve long evidence with `workflow evidence`. Report missing evidence; prove
resumed-agent use, not merely availability.

### 11. Final Codex Advisor review

Before the consult, reconcile known material obligations using step 2's verification
and the delegate's findings. Reference the applicable observations and unresolved
acceptance gaps; load only missing evidence, not the verification history.

The final Codex Advisor judges readiness to push/open the PR from the candidate,
the delegate review, and the lead's dispositions. Invoke it against the live diff
with wrapper phase `final-review` and the same slug; the checkpoint supplies the
diff anchors. It applies
[Production Code's outcome verification](../production-code/SKILL.md#minimum-implementation-decision)
to the original objective before judging implementation and dispositions. Missing material
acceptance evidence forbids `commit-ready`. Address and disposition material findings. The
wrapper leaves final findings pending; the lead explicitly records `none` or
`addressed` only after validating the output. After the first final verdict, a correction
needs only the `tdd` rerun of its items and the final advisor's re-check: code review,
verification and context revalidation are not repeated. Reuse applicable evidence. Once every
final finding is dispositioned (`nextAction` `complete-workflow`), a requested
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

`complete` refuses, from inside its transaction, unless every current probe has fresh successful comparison evidence and no proof gap remains, required phases are ready, material code-review findings are dispositioned, and the context-matched final `codex-advisor` intake has only effective terminal findings. The immutable raw verdict remains evidence but is not an indefinite veto after closure; `context-mismatch` or a pending one-response rejection appeal still blocks; a material re-raise reopens the finding as pending until the lead dispositions it once more against the new measurement; that second measured disposition stands. The reviewable working tree must match the manifest recorded by the lead review, and every evidence phase must carry its producer's evidence reference — a passed phase without one is a bare claim and reads pending, including legacy in-flight state at upgrade time. It changes workflow state only. It does not inspect, intercept, authorize, or execute Git.

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
