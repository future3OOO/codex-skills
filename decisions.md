# Codex Skills Decisions

Project decision record, tracked in Git and kept out of the installed estate.
Record consequential choices and their reasons, not a transcript or per-edit log.
Issue bodies own implementation scope; this record preserves decisions and their
status. New decisions supersede earlier ones explicitly; observations and open
acceptance gaps are not completed delivery.

## 2026-09-14 — Require preflight advice

**Decision:** Require advisor advice before the initial production preflight. Supersedes the
optional scope/design exception that allowed the [#29](https://github.com/future3OOO/codex-skills/issues/29)
continuation to omit advice. The workflow and advisor instructions own this rule.

**Status:** Proposed in [PR #51](https://github.com/future3OOO/codex-skills/pull/51);
not merged or installed in the shared estate.

**Private development estate:** The maintainer requires candidate skills/runtime
to run from a private installation during development. Seed N and N+1 from one
global snapshot, keeping N's installation fixed and their runtime state separate.
Use Bubblewrap process bindings and a task-local refresh script for candidate
installation. PR50 exposed stale parent hooks despite updated files; native
restart under the private bindings and loaded-consumer verification are part of
testing when consumers retain old code.
README owns the installation guidance; shared delivery still requires authorization.

**Comparison ownership:** Production Code owns real N/N+1 behavioral verification
and conditional A/B cost comparison at equivalent work and correctness; TDD links
to that rule. Reuse the same operations rather than adding another test stage.
TDD uses the real N/N+1 operations as its proof and retains the probe where practical;
additional checks must earn their place and cannot replace production acceptance.
Keep the explicit N/N+1 and conditional A/B pointer in tests.md for local visibility.
The recorder reference separates ordinary recording from recovery, retaining command
binding and attribution limits while removing duplicated lifecycle and parser detail.
Repository target resolution applies to pytest/unittest, not direct installed probes.
Included in PR #51; wording review does not establish measured agent improvement.

**Execution setup:** PR #51 requires verified baseline/candidate execution before
measurements or production edits, reusing project environments and refresh commands.
This generalizes private development to downstream projects; README owns only
Codex-specific setup and links to Production Code's comparison rules.
Independent instruction review and diff checks passed; PR51 skills and TDD references
are installed and hash-verified privately. The interrupted experiment does not establish
PR51 agent improvement.

## 2026-09-14 — Issue 29 candidate-estate continuation

**Decision:** Continue [#29](https://github.com/future3OOO/codex-skills/issues/29)
on [PR #50](https://github.com/future3OOO/codex-skills/pull/50), reusing the
[disposition audit](https://github.com/future3OOO/codex-skills/pull/50#issuecomment-5659789835)
and applicable proof. The maintainer's rejection supersedes the earlier broad
readiness claim; a completed ledger or advisor verdict is not acceptance.
Keep implementation and proof with the lead, independent review fresh initially
and reusable on return, and dispatch enforcement at the existing hook. Preserve
executed verification and dispositions while unfinished proof still blocks review
and completion. Do not restore obsolete early-verification refusals.

**Decision:** Repair the reproduced routing failures at the hook: retain session
associations outside Git and filter unrelated repositories before reading their
ledgers. Refuse explicit reconsult without an existing SID at the wrapper's
session-selection owner. Add the observed flattened native tool names to the
existing matcher: the shipped matcher missed real native calls. Keep ordinary final-review session creation. Extend
existing tests for held-disposition continuation; no additional recorder repair
was needed. Preserve the previous lead's worktree and uncommitted record.

**Decision:** At the existing invalidation transaction, preserve accepted proof
when its recorded tree still matches disk. The actual external review handoff
was misread as a shell edit and cleared valid receipts. Reuse the existing tree
binding comparison; broader shell parsing remains with #42. A captured-command
A/B through the installed hook now retains proof while real content, mode and
deletion changes still invalidate it. The existing lifecycle test now makes an
actual file edit before asserting invalidation.

**Observed:** This continuation runs in the private candidate estate based on
`c76f43b`, with verified hook/CLI/skill bindings and isolated native/workflow state.
Routing and missing-SID checks have RED/GREEN proof; held-disposition continuation
and corrected CI assertions pass. Dispatch preservation was baselined after the
routing edit and is explicitly late. A test-driver setup error did not count as
proof of the defect; its failed attempt remains in history.

**Status:** Installed A/B proof, current verification, retained independent return
review and final advisor review passed. The parent restart corrected its stale
matcher: actual pending dispatch was denied and verified return review admitted.
The actual external handoff retained verification; real edits still invalidate it.
The captured 18-case native identity corpus has zero regressions. The independent
reviewer confirmed both original live findings corrected; final advisor returned
commit-ready. Current-head CI and remote reviewer completion remain pending.
Private-estate refresh is authorized; shared installation and merge are not.
This is separate from PR #45, merged at `dfb0159` with its own installation receipt.
No PR #50 merge or shared installation has occurred.

## 2026-09-14 — Revert issue 30 pending third-party review

**Decision:** At the maintainer's explicit request, revert
[PR #43](https://github.com/future3OOO/codex-skills/pull/43) in the repository
and open a new, unmerged GitHub PR for the project's third-party reviewers.
Agent review is not the requested independent review. This supersedes the
prior delivery decision for [#30](https://github.com/future3OOO/codex-skills/issues/30).

**Status:** The mechanical revert restores the exact pre-PR tree before this
record. The installed estate is deliberately unchanged, as instructed; no
installation or backup restoration is part of this recovery. The replacement
PR must remain unmerged pending the maintainer's review and authorization.
Original implementation and evidence remain available from PR #43.

### PR #45 receipt-attribution repair

**Decision:** Correct independently reproduced SPEC-1/2/3 at the existing
unittest parser: require effective verbose mode, accept native docstring reports,
and apply execution/marker checks to the selected terminal failure after report
consistency checks. Accumulated counterfeit and description failures supersede
the intervening row-search guards: consume native progress without skipping
unrecognized text and reuse the existing terminal reader's framed block starts
for attribution and failure counts. Leave genuinely ambiguous merged output to
the existing direct execution route. The executor and receipt format stay unchanged.
Direct whole-batch RED keeps its existing requirements.
Measured flushed-output and class-fixture cases extend the same coverage.
Later independent CLI reproductions establish two manifest-sampling exceptions
and lost baseline-command recovery after supersession. These supersede the
initial report's unconfirmed sampling assessment: use the existing refusal path
and retain the baseline command in its existing producer proof, without retries
or another store.
The delivery skill now states explicit maintainer merge authorization separately
from review completion, addressing the prior unauthorized merge.
The later workflow correction keeps outcome and verification rules in
`production-code`, applied independently by `code-review`. It removes the
production-code instruction to commit/push directly. Code review is a prerequisite
for the final advisor’s push/open readiness decision; the owner authorizes merge.
Self-cleanup and later GitHub review cannot replace the code-review step.
Candidate-loaded lead behavior and return review must validate these reusable
instructions; demonstrations prompted by the maintainer alone do not establish
that agents follow them.

**Status:** Consolidated parser verification and return review remain in progress;
earlier passing examples did not establish the attribution correction. The real
N/N+1 direct-route comparison retains pending ambiguous claims and then completes
their RED/GREEN with two additional executions on each runtime. Unambiguous native
receipt reuse remains a preservation requirement of
[PR #45](https://github.com/future3OOO/codex-skills/pull/45). Issue #30 remains open;
neither merge nor installation is authorized. This repair has not changed the
installed estate; another agent owns its triage changes. This does not supersede
the rollback/review decision above.

## 2026-09-13 — Same-item RED command correction

**Decision:** [#35](https://github.com/future3OOO/codex-skills/issues/35) extends
the existing pending disposition for a RED contract item. Release its current
proof and matching active-cycle binding in the existing annotation transaction;
retain contract identity, finding references, immutable observations and other
items. Corrected proof still uses ordinary RED/GREEN validation. No new command,
schema, store or workflow reset is needed.

The original issue and agent brief remain the recorded workflow contract. The
maintainer authorized a focused code intake after Repo Context Forge mistook
fixture names and document fields in that brief for required source symbols.

**Status:** Implemented on `fix/issue35-red-command-correction` in a separate
worktree. Public CLI tests cover recovery through completion, proof refusals,
history, neighboring cycles and stale-run rejection. The issue's delivery PR
carries final verification, scoped installation and reviewer completion results;
no Claude backport or other #30 work is included.

## 2026-09-13 — Selected Claude PR254 capacity port

**Decision:** Port only merged Claude PR254 (`b3e4648288b66d510beb8a29f0349817e9ec05da`):
`skills/repo-context-forge/scripts/bootstrap.py` and its existing
`hooks/tests/test_repoforge_workflow.py`, using `scripts/estate_xform.py to-codex`.
Both Codex files matched the transformed upstream parent exactly. Keep
`.upstream-sync` unchanged because this is a selected port, not a full sync.
Preserve native Codex delegates, the runner and workflow evidence owners.

The account-scoped capacity slots are shared by Claude and Codex on this host;
per-HOME registry locks remain separate. The producer-death/indexer residual
remains owned by GitNexus #25, as documented in PR254.

**Status:** Ported on `port/claude-254-intake-capacity`; the delivery PR carries
measured source and installed-entrypoint acceptance. Tests and this decision
record remain repository-only; scoped installation changes only the adapter.

PR36 review reproduced higher-index reservations being ignored after capacity
shrink, early test-coordinator release, and memory tests skipped without the
canonical producer. The Codex repair counts all held slots under a short
admission lock, releases the coordinator after test cleanups, and guards only
tests that require the missing producer. The stronger count-and-claim protocol
requires updated participants: older Claude adapters in separate HOMEs need a
later backport before sharing that guarantee. This pass updates only Codex.

## 2026-09-13 — Native Codex delegation defaults

**Decision:** [#32](https://github.com/future3OOO/codex-skills/issues/32) uses
native `explorer` specialists and fresh `default` code-review delegates with
normal native model selection. Preserve review, recording and continuation
requirements; explicit router definitions and provider configuration stay outside
this change.

**Status:** Implemented on `fix/issue32-native-delegates`, based on the decision
record branch. Live separate-worktree acceptance, review and scoped installation
are required before delivery; the issue PR carries their measured results.

## 2026-09-13 — Implement in Codex first

**Decision:** Complete the workflow improvements in codex-skills, then fold the
reviewed changes into claude-skills. The maintainer explicitly selected this
order. Existing Claude issues and open PRs provide reusable designs,
implementations and evidence; their merge is not a prerequisite for Codex work.

**Status:** Reflected in [Codex #30](https://github.com/future3OOO/codex-skills/issues/30).
Triage is complete; implementation and installation are not. This supersedes any
earlier assumption that these changes must first ship in Claude.

## 2026-09-13 — Separate correctness from workflow friction

**Decision:** [#29](https://github.com/future3OOO/codex-skills/issues/29) owns
contract-grounded expectations, actual N/N+1 Seam attacks, affected-domain
coverage, preservation and repair-lineage judgments.
[#30](https://github.com/future3OOO/codex-skills/issues/30) owns evidence handling,
recorder correction, unnecessary execution and transcription, and recovery/context
overhead. Coordinate their shared workflow-skill edits without duplicating rules.

Correct production behavior is the objective; green workflow state follows its
proof. Reuse applicable executed receipts and drivers, preserving target binding,
unique assertions, honest failures and independent review. Do not reduce proof to
reduce runtime or findings. No new stages, generic framework, duplicate suite,
mandatory full skill rereads or test/map/finding quotas.

**Status:** #30's implementation brief is published with `enhancement` and
`ready-for-agent` labels. The brief includes real N/N+1 acceptance operations;
those operations have not been executed against a revised implementation.

## 2026-09-13 — Runtime source and installation provenance

**Observed:** Codex `main` at `e2e8a79759c3faccb2847afc0fdf855616dd271c` is a
skills mirror without the hooks/ledger runtime. Published `port/estate-parity` at
`315278f833f7ea46b3e64247e04cf916610a5c86` contains that source, introduced by
`dbb76ec`; `.upstream-sync` identifies Claude
`972411ab0dc76cc297ee94efe8380abcda558136`.

All 15 tracked `hooks/lib` files matched both installed `~/.codex` and CX1X's
`codex-resume-home` byte-for-byte during triage. AGENTS, code-review and workflow
skills also matched. The branch's `install.sh` defines project-to-estate copying,
backups, test exclusions and managed hook merging. Byte equality and this route
do not establish the exact historical installer invocation; its receipt was not
located. No parity PR was open when checked.

**Decision:** Reuse the existing parity lineage and land its reviewed foundation
before dependent runtime changes target main. Reconcile intervening edits; do not
reconstruct the port or overwrite the estate wholesale. Later scoped installation
must name its source revision, preserve unrelated installed changes and verify
the installed behavior. `scripts/sync-to-upstream.sh` is the existing selected-path
route for a later Claude backport, not a command run during this triage.

## 2026-09-13 — Reuse existing Claude work

**Decision:** Reconcile existing owners and candidates instead of building
competing mechanisms. The following statuses were checked during triage:

| Work | Contribution and remaining boundary |
| --- | --- |
| [#245](https://github.com/future3OOO/claude-skills/issues/245) | Existing design for obligation resets, verification recovery, receipt references, producer-owned facts and multi-item execution. Its separate scheduling work is not automatically #30 scope. |
| [PR #254](https://github.com/future3OOO/claude-skills/pull/254), open at `26706957` | Implements part of #245 item 4: memory-bounded intake admission across isolated HOMEs at the bootstrap resource owner. Reuse the candidate and capacity/lifecycle attacks. It does not deliver the remaining recorder/evidence work or portable scheduling. The duplicate runner-side memory cap was removed. |
| [PR #248](https://github.com/future3OOO/claude-skills/pull/248), open at `341b20c4` | Implements bounded reviewer continuation in two skills, addressing [#238](https://github.com/future3OOO/claude-skills/issues/238) on top of [#232](https://github.com/future3OOO/claude-skills/issues/232). Current source assigns TDD recording to the lead and only assigned targeted verification to the delegate. Reuse the actual diff, not stale model/ownership wording. Native acceptance remains partial. |
| [#252](https://github.com/future3OOO/claude-skills/issues/252) | Owns existing summary, selected-status and compact-receipt improvements. Reuse those owners without another projection/store. |
| [#250](https://github.com/future3OOO/claude-skills/issues/250) | Separate admission/edit-hook gaps. Verification or continued review with open findings is not automatically a defect; completion must remain blocked where required. |

Claude PRs #241 and #247 are already inherited by the parity source: preserve
their governance, identity, interleaving and intake-lock behavior. Same-registry
native concurrency still requires the separately owned GitNexus #25 correction
to be installed and verified. PR-reported results are not fresh Codex acceptance
or evidence that an open candidate is installed.

Adapt reused changes to Codex's actual harness permissions and recording ownership.
Do not copy Claude model/frontmatter assumptions or change providers as a side
effect. Later backports should carry only the remaining Codex delta.

## 2026-09-13 — Evidence boundaries

**Observed:** CX1X's audited resumed window is physical JSONL lines 2958–7416,
2026-09-13 02:50:22–04:59:52 UTC. Earlier records are inherited CX1 history;
later activity was not covered. G5, G6 and D8 provide comparisons; D3 stopped
before review-return repair and does not.

Real failing tests often preceded repairs. Missing repeated skill loads does not
establish missing TDD. Passing examples failed to establish domain coverage or
preservation; some expectations were narrowed to the implementation. Recorder
traps caused undo/reapply and obsolete competing obligations, while real product
defects remained. Counts and cached-token usage do not establish a waste fraction.
Graph-cleanup invalidation and the annotation episode need mechanism reproduction
before a runtime fix can be justified.

Retained evidence root: `/home/prop_/dswe-run-issue95`.
The primary transcript is
`armCX1X/codex-resume-home/sessions/2026/09/12/rollout-2026-09-12T23-42-32-01a0956d-2212-70e0-b6a5-baa75d0ea1da.jsonl`.
`cx1x-review-lineage.py` and `cx1x-review-lineage-results.json` contain the retained
N/N+1 comparison. The two issues carry measured outcomes so implementation need
not reconstruct the benchmark. The host's `codex-production-proof-workflow-prd.md`,
`codex-review-contract-issue.md` and
`/home/prop_/projects/claude-skills/decisions.md` supplied additional context.

**Decision:** Keep unavailable evidence explicit. Triage does not authorize
benchmark repairs, changes/stops to arms, ledger rewriting, credential inspection,
installation or Claude backporting. Creating this Codex decision record is a
separate documentation request; it does not start #29/#30 implementation.

## 2026-09-13 — Preserve historical calibration across the port

**Observed:** PR #31's CI failed because a fresh Codex clone lacked the original
Claude calibration commits. Fetching the history exposed a second failure: the
vendor transform had changed P1's captured shell literal while retaining its
original digest. Both failures also predate the decision-record changes.

**Decision:** Fetch the original case-G commit into a calibration ref before both
CI lanes; its ancestry includes every required corpus and classifier oracle.
Restore the original literal and protect the calibration test through the existing
manual-sync list. Preserve all pinned digests, result assertions and honest failures.
The maintainer authorized this CI repair separately from #29/#30 implementation.

**Status:** The maintainer explicitly requested merging PR #33 first; it is merged
into `main` at `c969291`. PR #31 now targets `main` and carries this repair. All 90
quality-gate tests pass after deleting five obsolete modules retained from the
initial mirror, absent upstream and unreferenced outside their own set. The original
2,825-line ceiling remains unchanged. The integrated runner also exposed an offline
Claude-provider prune test invoking the new Codex default; explicitly select its
intended provider and preserve its pointer assertions. Independent review and CI
completion remain pending. No estate installation or benchmark-arm changes are
part of this repair.

## 2026-09-13 — Reuse identical pending advisor obligations

**Decision:** [#37](https://github.com/future3OOO/codex-skills/issues/37) reconciles
validated findings inside the existing recorder transaction, using current pending
state and referenced intakes. Compare all four finding fields within the
workflow/stage/producer/verdict context. Retain canonical lifecycle references,
ownership and progress while recording each accepted observation. Mixed intakes
register only new obligations; dispositions require a canonical registered pair.
No schema, hash, CLI, approval or workflow-stage changes are needed.

**Observed:** The captured preflight response produced pending counts 1, 2, 3
through the public CLI on main, even when the three observations shared one
evidence ID. Timestamp variation is not required for the defect.

**Status:** Implemented on `fix/issue37-pending-findings` from fresh `origin/main`
`04aa084`. Public CLI RED/GREEN covers repeated and concurrent retries, mixed
findings, preserved ownership/progress, and refreshed-final completion. Both appeal-read regressions have CLI RED/GREEN proof. All eleven acceptance
tests, the full integrated suite, lint and typed gate pass. Fresh native review
and final Codex Advisor review have no findings; earlier measured findings are
fixed. Scoped installation and PR delivery accompany this verified change; the
linked issue/PR owns remote check and reviewer status.

## 2026-09-15 — Issue 54 closure authority at the TDD recorder

**Decision:** Correct [#54](https://github.com/future3OOO/codex-skills/issues/54)
at the existing evidence-to-status owners, not with a new stage, terminal state
or semantic-inference subsystem. `tdd_surface.evaluate_red` records what a runner
RED observed apart from its marker (`redProof.observation`, object addresses dropped;
`redProof.site`, the last test-side frame `path:line` plus that source line, or
the non-runner command).
`tdd_workflow._run_tdd` refuses a RED whose observation another item's RED already
recorded (an explained assertion anywhere; an unexplained one at the same site)
and refuses a pending contract item's passing RED-phase run once any production
path changed in the pass; preservation keeps its executed candidate-observation
routes (late baseline, flagged revalidation), labelled late, while a prose
`already-satisfied` preservation item is unresolved until an executed run records
its baseline (`--not-required` and `complete` name it); `tdd-map` refuses
`revalidate` on a never-settled pending item; a reopened item's retained RED is
history, not ownership, so another item's identical initial probe is admitted. `summary` names items whose
REDs rendered the same failure at different sites. Root `conftest.py` is test-like
for the shared path classifier. Skill text (tdd, recorder, tests, preflight,
production-code, workflow step 6, code-review) carries the one-entrypoint-absence
rule, the closed late-baseline route and implementation-derived revalidation.

**Reason:** The captured CX2 ledger shows ten items admitted RED through one
helper assertion at `tests/test_safe_import.py:37`, two CLI items admitted on the
absent route's exit code, and three items plus one re-entry closed
`already-satisfied` from passing runs after `sqlite_utils/db.py` and `cli.py`
changed. Replaying the corrected admission over those 22 distinct valid REDs
refuses 11 of the 12 inherited ones (BM_CLI_SAFE remains the CLI route's own
first RED) plus the non-runner pair's second item (same script, identical
observation) and none of the 10 honest runner REDs; the baseline rule refuses the
contract item BM_SQLITE_INTERRUPTION and admits the preservation items late.
Rendering-only identity was rejected because this repository's own unittest
probes for independent attributes render identically (`1 != 2`); location-only
identity misses ten of twelve; independent review added address normalisation,
test-side sites, full explanation levels with prefix matching and non-runner sites.

**Observed limits:** The runtime cannot certify semantic reach. Two escapes stay
with the skill rule and review: an entrypoint-absence RED re-taken at a different
unexplained site (labelled by `summary`), and the recorder cannot judge whether a
changed production path affects the baselined behavior, so any changed code path
refuses. A non-runner RED's site is its command, so the same command observing the same
failure cannot open a second item (CX2's rewritten repro script pair is refused).
A unittest helper living in a non-test module gives each caller its own site, so
such REDs are admitted and only labelled; the CLI pair (one explanation extending
the other) is likewise labelled, not refused.
The entrypoint-absence allowance is first-recorded, not contract-based: no field
in the item shape names the entrypoint owner (`kind` is contract/preservation), so
the recorder admits whichever item records that observation first and refuses the
rest; `skills/tdd/SKILL.md` says "exactly one atomic initial behavior that requires
it", which the recorder does not enforce (in the replay the winner was
`BM_SAFE_RESULTS`, a results item). The counterfactual rule (a guarantee that could
fail while the initial behavior passes cannot inherit) is stated to the agent by the
skill; the recorder enforces only its mechanical subset, observation equality, so
REDs stopping at distinct preconditions are admitted. Neither shape occurred in the
corpus or N1; both stay recorded limits rather than new state (#54 follow-up).

**Decision (acceptance claim, Done-when #2):** "11/12 refused, one labelled"
satisfies only the recorder subcriterion. The `Shared RED observation` label is
diagnostic; it does not itself prevent GREEN or completion. Semantic reach is
decided by review through the existing finding lifecycle: `code-review` records
the label as a Spec finding, `BM_CLI_SAFE` stays pending until re-driven through
safe insert/upsert/bulk behavior, and `BM_USERS`/`BM_AUDIT` both stand because
their tests observe distinct calls. That reviewer behavior is not demonstrated in
the installed replay, so PR #56 references #54 without closing it. No
entrypoint-ownership field is added: agent-authored state would not establish
reach; a structured review acknowledgment is a separate correction if replay
shows reviewers miss surfaced pairs.
In this pass the installed (N) recorder recorded seven items `already-satisfied`
from passing runs after `hooks/lib/*.py` had changed: contract
`BM_REFUSED_RED_CLOSES_NOTHING` and preservation `BM_FIRST_RED_OPENS_SLICE`,
`BM_OWN_RERUN_AND_DISTINCT_SITES`, `BM_FLAGGED_REVALIDATION_BASELINE`,
`BM_CLEAN_BASELINE_ADMITTED`, `BM_NONRUNNER_RED_UNCHANGED`, `BM_REUSED_RED_UNCHANGED`.
The contract closure is the mechanism the correction closes (the candidate
recorder would refuse it); its guarantees (GREEN refused without RED, refusal
durable, completion blocked) are pre-existing behavior the test exercises. The
six preservation items' executed proof is the full suite green on the final tree
(verification evidence-9e0dea73f257c61172e5ffb79590223d).

**Status:** Implemented on `fix/issue54-closure-authority` in the isolated
worktree with RED/GREEN attacks through the workflow CLI, the CX2 corpus replay
and the real N/N+1 operation on the private estates (`~/.local/share/codex-estates/issue54`:
one snapshot seeded into N and N+1, bwrap-bound at `~/.codex`, global estate
read-only, separate sessions/caches/workflow state; `refresh.sh` installs the
worktree into N+1 only; `verify-loaded.py` receipts show N loads the snapshot
modules and N+1 the candidate). The installed-estate agent replay from the issue's ordinal-326
checkpoint ran one pair through `launch.sh` (3600 s per arm, gpt-5.6-sol,
receipts under `~/.local/share/codex-estates/issue54/replay/RESULT.md`). The N
arm is void: the app clone still carried CX2's completed branch and the lead
switched to it. The N+1 arm, which never consulted that branch, established the
recorder-level criteria: one entrypoint-absence RED per entrypoint, five
inherited REDs and the CLI pair refused, a post-change contract baseline refused
and not manufactured, real semantic REDs afterwards, twelve contract items GREEN
through their own RED. It was stopped before review because the lead invoked
the claude-advisor precommit wrapper instead of the codex-advisor wrapper, so
reviewer-claim and final-advisor behavior are not established; Done-when #5
remains open until a rerun with the leaked branch pruned and the codex-advisor
wrapper pinned. No shared-estate installation or merge.

Final advisor review (PR #56, intake evidence-14755bfb884c4d3151a6a65753eda291)
returned five material findings. Fixed with owning attacks: SPEC-5 (reopened
history claimed ownership) and the prose-settlement half of SPEC-3. Rejected with
measurement: SPEC-1 (the CLI pair and the SPEC-11 pair are structurally
indistinguishable renderings with opposite correct outcomes, so the recorder
cannot refuse one without the other; the label and the skill's one-entrypoint
rule own it), SPEC-2 (no owner attributes a changed production path to a
behavior; refusing every changed path is fail-closed and admits nothing, and the
corpus shows zero false refusals), the candidate-pass half of SPEC-3 (a
preservation item's executed candidate observation is the evidence preservation
means; it is labelled late, not hidden). SPEC-4 (the replay's post-recorder
criterion) is deferred by the maintainer on the issue; the retired extra
`claude-advisor` skill was the replay's misroute, not the candidate.

## 2026-09-14 — Consolidate workflow consumer corrections

**Decision:** [#42](https://github.com/future3OOO/codex-skills/issues/42) owns
consumer reconciliation: valid native advisor/reviewer calls, bound RCF refresh,
compact continuation, accurate edit feedback and merge-before-install. Reuse
#29's repair judgments, #30's evidence/runtime capabilities and #46's graph
handoff work. Correct existing instructions instead of adding workflow stages.

**Observed:** PR #45's omitted workflow slug caused an unbound refresh and an
avoidable advisor retry. Correcting that invocation did not establish recurrence
prevention. Real parser findings remain correctness work, not removable ceremony.

**Status:** Updated #42's authoritative brief, retaining `bug`/`ready-for-agent`.
Acceptance requires actual lead N/N+1 behavior and measured overhead reductions
before delivery. Triage only; no implementation, installation or ledger changes.

**Related decision:** [#29](https://github.com/future3OOO/codex-skills/issues/29)
now explicitly owns lead investigation and N/N+1 assessment before initial and
return code-review handoff. Related follow-ups require revisiting the demonstrated
mechanism using accumulated evidence before another edit, within existing steps.
Its brief and actual-agent acceptance were updated; no new stage or runtime change.
The existing execution-reuse clause also requires any separately derived regression
test to detect the real probe's same behavioral defect on N and pass on N+1;
retaining the probe itself is preferred. #30's active implementation is unchanged.

**Residual recovery finding:** [#49](https://github.com/future3OOO/codex-skills/issues/49)
tracks failed-batch recovery after a narrow assertion correction. The lead reran
33 checks after 32 had passed; the second batch took 37.380 seconds. A separate
real CLI reproduction confirmed targeted success stays pending and explicit
replacement refuses the changed tree. Safe narrower recovery remains to be
designed at the existing owner, so the issue is `bug`/`needs-triage` after #30;
this does not authorize weaker binding or arbitrary test-edit reuse. Audit:
`/home/prop_/.local/state/codex-proof/workflow-audit-20260913/continuation-20260914.md`.

**PR45 review:** Reviewed clean `346a317` / tree `b7909d1` against `36679d7`.
The existing-owner changes support #30's core behavior and bounded receipt/output
savings; no new material defect was confirmed. Targeted real-CLI baseline recovery
passed; retained current-tree and applicable earlier evidence supplied preservation.
Five current review threads still need disposition; no merge, installation or full
issue closure is claimed. Report:
`/home/prop_/.local/state/codex-proof/pr45-independent-review/346a317-root-review.md`.

**Review status update:** At unchanged PR45 head `346a317`, all inline threads
are now resolved with dispositions; CodeRabbit also withdrew its baseline
hardening finding. One confirmed delivery-instruction gap remains: step 13
requires owned-path installation but references README's full-estate installer.
Document the existing scoped backup/copy-or-merge operation and verify it against
an isolated destination; no new installer framework or repeated parser suite is
warranted by this finding. The fleet reports incomplete coverage, not a clean
full review. No candidate edits, installation or merge were performed here.

**Delivery documentation:** The maintainer assigned PR45's scoped-installation
correction to [PR48](https://github.com/future3OOO/codex-skills/pull/48). Keep the
procedure in README beside installation, using selected-file rsync with backups
and existing installed-entrypoint probes. The exact documented copy commands
passed in an isolated destination: two selected files matched source, the prior
file was backed up, and six unrelated files were preserved. No live installation;
PR45 can consume this README correction once integrated.

**PR48 dispositions:** At `15a5b30`, resolved the two existing CodeRabbit threads
as reported-not-actioned: inherited alternate-HOME installer portability (the
configured host's MCP path exists), and optional expansion of update commands
(the named checkouts and clean-main requirement are explicit). No fixes claimed;
the dirty RCF checkout remains preserved. Cubic's later backup-directory finding
was rejected using the exact isolated rsync execution: an absent backup tree was
created and the original bytes preserved. All three threads are resolved; fleet,
contracts, CodeRabbit and cubic are complete/successful on unchanged `15a5b30`.
No additional source edits, installation or merge were performed.

**Delivery update:** With explicit maintainer authorization, merged PR48 as
`30af550` and fast-forwarded local main to the same remote commit, preserving
local decision records. PR45's installation-documentation finding is addressed
by the merged README. #47 records this delivery and its completed N/N+1 probe;
it remains open for outstanding active-client reconnect/build verification.
#30, #42 and #46 retain their separate unfinished scope. No estate installation.

**PR45 delivery supersedes the pending #30 status:** The maintainer authorized
merge, local sync and installation. PR45 merged as `dfb0159`; local main matches
remote. Installed its 12 owned files (seven updated, five already matching),
preserving 162 other estate files. Backup:
`/home/prop_/.codex-backups/pr45-20260914-124010`; receipt:
`/home/prop_/.local/state/codex-proof/pr45-install/install.json`.
Five existing real CLI probes against installed runtime passed in 29.595 seconds,
using temporary repositories/ledgers; tests and this record remain repo-only.
An initial module-path diagnostic encountered a lazily unimported module; explicit
imports subsequently verified all five runtime module paths in the estate.

Closed #30 with retained N/N+1/native proof and installed verification. Closed #47
on delivered installation/new-launch evidence after the maintainer waived migration
of already-running MCP sessions; no claim those old processes reloaded or were
killed. Updated #29/#42/#46/#49 to consume the delivered baseline and retain only
their remaining work. No Claude backport or benchmark/live-ledger mutation.

**Subsequent #47 process cleanup:** The maintainer explicitly requested closing
all stale GitNexus processes. Process memory mappings identified 12 servers on
the former `e4e227e` pin and five on the obsolete issue94 runtime. All 17 exited
on SIGTERM; no forced kills or parent-agent termination. Two servers remain,
both mapped to `18f6d913`; zero stale processes remained at the final audit.
Combined pre-stop RSS was 2,886,224 KiB, not a measured unique-memory saving.
This supersedes deferral of the stale-server cleanup, without claiming old
clients reconnected. Receipt: `~/.local/state/codex-proof/gitnexus-stale-process-cleanup.json`.

**#29 handover consolidation:** At the maintainer's request, replaced the
3,003-word issue body with one 712-word authoritative brief. Current installed
owners already express much of the desired behavior: require a demonstrated
remaining gap before adding instructions, allow a verified no-change outcome,
and retain production-code as the outcome-rule owner. Preserve actual N/N+1
and native-lead acceptance before delivery, independent review and applicable
evidence reuse; use this repo's workflow work without requesting an external
application. Historical drivers remain references, not benchmark work. Labels
remain `bug`/`ready-for-agent`; triage only, no implementation or installation.

On the maintainer's preservation check, compared the original acceptance and
responsibilities with the shortened brief. Restored explicit requirement citation,
historical-receipt applicability, honest nondeterminism handling and reportable
unexplored/missing-acceptance gaps where compression had left them implicit.
Core N/N+1, actual-lead, before-review investigation and no-redundant-work
requirements remain; the no-change route requires demonstrated behavior.

**#29 sensitivity and review enforcement:** The maintainer requires this behavior
by default, not after prompting. The brief now requires relevant regression
checks to detect their claimed defect and existing review intake to report
missing material outcome/coverage/sensitivity proof as a Spec finding. Reuse
real N failure where available; targeted fault injection in disposable state
is sensitivity evidence, never fabricated historical N. Repair/replace ineffective
checks; remove only those without distinct useful coverage, preserving legitimate
checks that pass on both versions. No unrelated test purge or mutation framework.
Actual lead/reviewer acceptance must distinguish false-green claims from valid
reusable proof. Skills speak generically about the active project's behavior;
codex-skills is this issue's application target, not a special runtime branch.

**#29 affected-module review:** Clarified that the same general reviewer traces
related modules, callers/callees, shared-state writers and competing implementations
using existing evidence/source; no explorer delegates or added review stage.
Require the responsible-owner correction and removal of superseded guards, with
actual-agent acceptance covering related interactions. A recorder refusal of
applicable proof is reported to its owner, never grounds for redundant repair
execution or fabricated green state. Production behavior remains the objective.

**JSON size investigation:** Using `o200k_base`, whitespace-only compact JSON
serialization of `issue30-parser/explicit-worktree-graph.json` reduced tokens
10,833 → 7,389 (31.8%) with equal decoded data; the same-reviewer report reduced
1,165 → 991 (14.9%). Already-minified bounded/framed graph envelopes saved zero
tokens because their bulky content is inside strings. Advisor prompt producers
still use `indent=2` for projection/finding-ledger/late-RED JSON and then concatenate
those files. Prefer existing-producer compact output and unchanged schemas over
another format/conversion stage if implemented; preserve string contents and
immutable historical receipts. These are sample tokenizer counts, not billed
session savings. Investigation only; no producer or issue-scope changes.

Further inspection: `_workflow_db._json` already serializes stored ledger JSON
compactly; the advisor receives a finding/attack projection, not the entire
SQLite database. `_finding_ledger` repeats owning attack details per finding,
and the wrapper pretty-prints that projection into every phased prompt. Its
phased advisor cannot read external evidence files, so references alone cannot
replace required supplied proof. Retained preflight JSON measured 1,038 → 911
tokens (12.2%) with minification; thirteen text sections plus the map dominate.
Prioritize existing compact/selected consumers and producer serialization, then
prove safe removal of duplicated content at existing owners. No ledger rewrite,
schema change or implementation is authorized by this investigation.

**Actual advisor-prompt measurement:** Recovered the 117,777-byte final prompt
from `issue30-parser/framed-final-advisor.out`, verifying the three JSON section
hashes against wrapper metadata. Compact serialization alone reduces its
`o200k_base` estimate 26,820 → 25,507 tokens: 1,313 (4.9%), comprising finding
ledger 777, projection 510 and late-RED 26. This replaces graph-sample percentages
as the relevant bounded estimate for this prompt, not billed session/cost savings.
Measurement: `~/.local/state/codex-proof/advisor-minification-measurement.json`.

**#29 lead audit:** Its checkout remains clean at `dfb0159`. The lead reused the
historical docstring RED, then demonstrated that the existing pending-state
assertion catches a disposable "exit2 but stores green" fault. The copy was
removed; no extra regression test or runtime fix was needed. A one-owner instruction
proposal remains subject to actual-agent acceptance; this sensitivity observation
alone does not prove a default workflow improvement. The lead also acknowledged
an advisor consult before its concrete proposal and corrected course after user
direction, so that sequence cannot establish unprompted compliance.

**Compaction override (maintainer-authorized):** Global Codex config now sets
`model_auto_compact_token_limit = 258000` and scope `body_after_prefix`, avoiding
the native total-scope 90% clamp while preserving the current 258,400 usable-window
guard. Actual Codex 0.154.0 app-server config/read verified both values; other
parsed settings unchanged, backup retained. Existing sessions were not restarted
or verified reloaded. Trigger accounting can include estimated retained reasoning
and pending tool output; measured compaction-request input alone does not establish
the trigger count. Receipt: `~/.local/state/codex-proof/context-compaction-override.json`.
No #29 implementation, ledger or repository runtime changes.

**#29 behavioral follow-up:** At maintainer request, posted consolidated clarification
https://github.com/future3OOO/codex-skills/issues/29#issuecomment-5659452705.
Explicitly supersedes this triage’s erroneous instruction-only/runtime exclusions;
requires demonstrated corrections at existing instruction/runtime owners. Records
premature/inherited-context reviewer dispatch, evidence-retention/reconciliation
failures, and refusal recovery without final-review bypass or fabricated proof.
Preserves real N/N+1, affected-path preservation and actual-agent acceptance.
Comment verified against published body. No source, installation or lead-ledger
changes; the in-progress candidate is not declared complete.

**#29 authoritative body corrected:** Consolidated the maintainer clarification
and observed failure/recovery requirements into the issue body itself; removed
the obsolete instruction-only and runtime/dispatch exclusions. Supersedes the
preceding comment-only scope correction: comment 5659452705 is now only a pointer,
not a competing brief. Published body/comment verified. Existing proof, preservation,
independent review and minimal implementation requirements remain. No implementation
or installed-estate changes by this triage.

**PR50 private estate (maintainer-authorized):** Started fresh Herder agent
`pr50-live` in pane `w1:p69`, using worktree `codex-skills-pr50-live` at
`c76f43b` on `fix/issue29-live-estate`. Reused DSWE mount isolation so all
home-relative Codex entrypoints resolve to the private candidate estate, while
normal tools/projects remain accessible and the shared estate has a read-only
comparison path. Candidate state is separate; source refresh is authorized only
for this private estate. Actual process file/configuration and native-state
bindings verified in `~/.local/share/codex-estates/pr50/launch-verification.json`.
No shared installation, merge, benchmark changes or prior-lead state mutations.
This setup establishes environment binding, not completion of #29 acceptance.

**PR50 shared delivery (maintainer-authorized):** Supersedes the private-only
delivery status above. [PR #50](https://github.com/future3OOO/codex-skills/pull/50)
merged at `7198082628f63e13ce4fd7d7fe8c1d2441f66fdd`; local main fast-forwarded,
retaining uncommitted decision notes. Installed its ten owned source files and
merged native hook matcher into the global estate, preserving unrelated hooks
and skills. Backup and exact paths/hashes: `~/.local/state/codex-proof/pr50-global-install.json`.
Existing real installed-entrypoint probes passed: pending review denied inside
and outside Git, unrelated state ignored, unchanged handoff proof retained, and
real content/mode/deletion edits invalidated proof. Existing native sessions were
not restarted; cached consumers are not claimed reloaded. PR51 remains separate.

**PR51 integration status:** At maintainer request, rebased PR51 onto merged
PR50 (`7198082`) and pushed with an exact-head lease at `614654a`. Both decision
records and PR50 runtime/verification changes are preserved; diff checks pass.
PR51 remains open, unmerged and uninstalled; review on the new head is pending.

**PR51 TDD review at `20b48c9`:** Reviewed the last two commits and cumulative
TDD references without changing candidate files. One wording correction remains:
recorder.md must scope repository target resolution to pytest/unittest; the real
resolver accepts the retained external direct-probe command. No downstream agent
refusal was observed. Recorder prose fell from 1,266 to 862 words; retained proof
and lifecycle rules remain present. Report: `/tmp/pr51-tdd-review.json`.

**PR51 reviewer dispositions at `5228f21`:** Supersedes the earlier target-scope
wording finding, now corrected. Posted measured dispositions and resolved seven
false-premise, optional or duplicate threads. The outdated advisor thread remains
open for the missing initial-preflight prerequisite; the outside-diff readiness
comment shares that owner. Explicitly rejected running preflight at final review.
Provider-marker wording mismatch is reported without an observed real failure.
Current checks passed; no implementation, installation, native restart or merge.
GitHub PR51 carries the complete dispositions; `/tmp/pr51-disposition-results.json`
retains reply links. Future repair needs actual before/after acceptance.

**PR51 advisor-disposition correction:** Supersedes the preceding required-gate
recommendation. Step 4 and the advisor skill already require advice before initial
production preflight. The observed skip used the old optional instructions; it
does not demonstrate a candidate failure requiring new runtime gating. Corrected
the original GitHub thread disposition and summary, and resolved the thread as
reported-not-actioned. No runtime gate or late preflight is required by this evidence.

**PR51 delivery (maintainer-authorized):** Supersedes PR51’s unmerged/private-only
status above. Merged at `c772ad1d834b22a04a829075886a48f61bf83b9c` after all
review threads were dispositioned and current checks passed. Local main was
fast-forwarded, preserving uncommitted notes. Installed the seven changed skill
and TDD reference files globally, with backups; README and decisions remain
repository-only. Installed bytes, reference targets, instruction ordering and
receipt-reuse CLI options verified. Receipt: `~/.local/state/codex-proof/pr51-global-install.json`.
No native processes restarted, respecting the maintainer’s cancellation. This
installation verification does not claim measured agent adoption or efficiency.

**Repo Context Forge duplicate-skill audit:** Both global RCF and
`repo-context-forge@local-codex-plugins` are enabled. The global skill/adapter
matches codex-skills and calls the clean external producer `db114d20`. The
plugin calls its own bundled producer at `abde7363` plus six modified tracked
files, bypassing the global adapter; its producer hash differs. Its manifest
exports one skill, with no separate MCP/hooks configuration found. Recommend
disabling the duplicate plugin registration while preserving its dirty checkout
and the external producer. No plugin/config/source/process changes made by this
audit; duplicate exposure does not prove double execution or measured waste.

**Repo Context Forge plugin disabled (maintainer-authorized, 2026-09-14):**
Supersedes the enabled status and pending recommendation above. Set only
`plugins."repo-context-forge@local-codex-plugins".enabled` to false in global
Codex config; parsed before/after comparison verified the single change.
Backup: `~/.codex-backups/disable-rcf-plugin-20260914T113006Z/config.toml`.
Preserved the global workflow adapter, external producer and plugin cache;
no processes restarted. Existing session catalogs may retain their prior entries.
The six modified tracked plugin files all have August 24 timestamps (about
21 days old). Five exactly match historical committed blobs; the test file
has no matching blob event in retained history, so its provenance remains
unresolved. These are observations of a stale/divergent cache, not evidence
of recent development or measured execution/token waste. No files deleted.

## 2026-09-15 — Issue 54 installed-estate replay baseline

**Decision:** Use the captured CX2 lead session through ordinal 326 as the
primary actual-agent N/N+1 replay checkpoint for
[#54](https://github.com/future3OOO/codex-skills/issues/54). At that point the
advisor findings exist, source is unchanged at base `8d74ffc` / tree `d563862`,
and the old preflight/TDD/production guidance has not yet been loaded. Use the
ordinal-385 checkpoint only for a focused transition-owner probe: it follows
the honest missing-Interface RED but already contains the old skill text, so a
skill reload there is a reconstructed handoff rather than native resume.

**Reason:** Starting before the skill reads lets the installed candidate affect
proof selection as well as recorder behavior. The paired replay must distinguish
runtime terminal-authority enforcement from actual lead/reviewer investigation,
bind loaded estate bytes, keep mutable state isolated, and withhold later CX2
defect hints from the agents. Passing requires availability evidence to permit
progress without authorizing independent semantic obligations or unsupported
`already-satisfied` closure.

**Status:** Published the exact session-prefix hashes, source/workflow bindings,
original chronology, private installed-estate setup, and bounded pass/fail
criteria in
[issue comment 5673564790](https://github.com/future3OOO/codex-skills/issues/54#issuecomment-5673564790).
No #54 implementation, installation, session replay, workflow-state mutation,
merge, or shared-estate change was performed.

## 2026-09-16 — PR36/PR56 deliveries and record catch-up

**PR36 delivery (supersedes the Sep-13 pre-merge status):**
[PR #36](https://github.com/future3OOO/codex-skills/pull/36) merged at
`a3f544e76bb2918f819211fe49cf5f888edb879c` on 2026-09-13 after the
`port254-review` pass completed commit-ready (workflow
`ebbd64eedacf428fbab4e2447456aaea`: advisor preflight with SPEC-1
rejected-with-evidence and SPEC-2/SPEC-3 fixed, four TDD cycles, typed
verification, independent code review with no findings, final advisor
commit-ready). Scoped installation changed only
`skills/repo-context-forge/scripts/bootstrap.py` (sha256 `6f8928c1…`,
backup `~/.codex-backups/pr36-20260913-092935`, 107 other managed files
unchanged; receipt `~/.local/state/codex-proof/port254-review/install-repaired.json`).
All review threads on the merged head are resolved and local main carries the
merge. Older Claude adapters in separate HOMEs still need the later backport
before sharing the stronger count-and-claim guarantee, as recorded on Sep-13.

**PR56 delivery (supersedes the earlier "PR #56 references #54 without closing
it" and "No shared-estate installation or merge" statements):**
[PR #56](https://github.com/future3OOO/codex-skills/pull/56) merged at
`f088c144cafedff58c887eebf6cbeb662a5a89a8` on 2026-09-15 and
[#54](https://github.com/future3OOO/codex-skills/issues/54) closed COMPLETED at
the merge. The issue closure is the delivery fact; it does not claim the
recorded acceptance gaps were delivered — the void N arm, the partial N+1
replay stopped before review, unestablished reviewer-claim/final-advisor
behavior (Done-when #5), and the maintainer-deferred SPEC-4 remain recorded
limits, not satisfied criteria. The shared estate was refreshed afterward
(pre-refresh backup `~/.codex-backups/20260915-220853`, 199 files): sampled
PR56-owned paths
(`hooks/lib/tdd_workflow.py`, `skills/tdd/SKILL.md`, `skills/tdd/recorder.md`,
`skills/production-code/SKILL.md`) sha256-match the merged HEAD. The extra
`claude-advisor` skill — the replay's misroute, not the candidate — was retired
from the shared and issue54 estates on 2026-09-15 (backup
`~/.codex-backups/retire-claude-advisor-20260915T070615Z`).

**PR34 delivery (supersedes the Sep-13 pre-merge status):**
[PR #34](https://github.com/future3OOO/codex-skills/pull/34) merged at
`04aa084d2939e23b031e6ff07c09f657837fc399` on 2026-09-13, delivering #32's
native-delegate defaults. Its two owned files —
`skills/repo-context-forge/SKILL.md` and
`skills/repo-production-workflow/SKILL.md` — sha256-match the shared estate
copies against the `f088c14` blobs, verified 2026-09-16 (pre-install backup
`~/.codex-backups/issue32-20260913-073511`, containing exactly those two
files). No install receipt or installed-entrypoint probe was recorded, so the
installation path itself is unverified; only installed-content equality is
claimed.

**Docs-edit waiver (maintainer, 2026-09-16):** Governed workflow passes are not
required for `decisions.md`-only edits; the record update ships through the
normal PR review path. This pass's ledger (workflow `e79cb5350b16429ba8bf3136013e39db`)
was paused at code-review by the override; its retained proof — a real
RED/GREEN that caught the missing PR34 entry, the quality gate, and the
final-head check state — stands as evidence, not authorization.

**Record state:** This commit lands the carried-forward Sep-14/Sep-15 notes
(the #42 consolidation, PR45/PR48/PR50/PR51 deliveries, #30/#47 closures, #29
brief updates, RCF plugin disable, and #54 replay baseline) that had stayed
uncommitted across the PR50/51/56 fast-forwards; the pre-PR56-ff stash
snapshot of the same notes is redundant once this lands. Every merged PR
through #56 now has a delivered or explicitly superseded status. Open PRs
#40, #52 and #53 carry no delivery claims.

## 2026-09-16 — Context-efficiency passes: repo-context-forge #30 and codex-skills #60/#61

**Decision (maintainer-directed):** work #55/#59/#46 by measurement, ship only
structural changes at existing owners, no new hook, ledger, stage, or flag.
Re-derived the CX2 lead independently of the earlier issue comments (o200k,
canonical `response_item` text): 953,787 tokens; 30 truncations discarding
192,095; 117 repeat reads of already-read paths costing 226,822; workflow CLI
168 calls / 114,278 (`history` 8 / 23,019); 11 post-compaction rollout
self-greps / 73,564 searching for `BM_*` ids and `phase red|green`; JSONL
searches only 3 calls. Script and per-call tables retained under the session
scratchpad (`cx2/measure.json`, `cx2/report.md`).

**repo-context-forge [PR #30](https://github.com/future3OOO/repo-context-forge/pull/30)
(open, not merged):** SoulForge 2.13.2 records every Python import statement
(`refs.import_source`, `external_imports.package`) and resolves none of them,
so `SoulForgeMap` dependents came only from identifier-matched `edges`;
`workflow_cli.py` reported `risk=high direct_dependents=0` with seven real
importers. The consumer now parses those statements against the `files`
table and unions them with edges at weight 1.0 (graph built once per
instance). Live: `workflow_cli.py` 0→7, `tdd_workflow.py` 0→7, matching `rg`.
The `refs.name`/`calls` swap proposed in #59 overcounts (36 vs 7) and was not
taken; replacing edges outright would zero non-Python repos. Default packet
budget 16k→8k: Codex delivers ~10k tokens of one result and cut the middle of
every 16k packet in CX2 (`gitnexus_analysis` body, `scope_rules`), not the
targets; at 8k the codex-skills packet arrives whole at 6,493 tokens in the
compact form, dropping per-file `soulforge_impact` lists and symbol lines (no
budget renders the full form under the cap; recorded as the accepted trade).
`targets[:5]` digest cap unchanged: the compact `<targets>` block lists all 20.
Reviewer round (fleet + cubic) fixed in `c394568`: beyond-package relative
imports link nothing, package beats a same-named module, backslash
continuations normalized, README 8k. Occurrence of all three shapes across 25
captured indexes / 11,158 statements: zero.

**codex-skills [#60](https://github.com/future3OOO/codex-skills/issues/60) /
[PR #61](https://github.com/future3OOO/codex-skills/pull/61) (open, not
merged):** `history` embedded the full state projection per event (426,596
tokens on one real pass); rows are still validated, no longer published
(2,976 active; bare slot 191,549→15,898). `status` and mutation receipts drop
`intent` (10,839→1,283); `begin` prints the compact receipt. `summary` (the
compaction re-arm line) lists the map grouped by status after the closing
invariant plus the latest valid generic verification command, cap 3,000 sized
to the largest recorded real map (38 ids). Deviation recorded on #60: bare
`history` keeps every workflow (prune reports count across workflows).

**Not built, by decision:** #59's read ledger C/D (behavioural effect
unproven; needs the paired pass), any output-budget hook, by-reference packet
emission. Expected effect on the CX2 lead from what shipped: ~7–8k tokens from
whole packet delivery, ~24k deterministic from the CLI shapes, up to ~74k if
the summary line stops the post-compaction rollout greps (unproven).
**Observed, not changed:** pr mode ranks the changed file above the intent
(`decisions.md` at 1513 topped a hooks-review packet); the final advisor
appeal on PR #30 was skipped and neither workflow was run to `complete` at
the maintainer's direction.

**PR #30 / PR #61 delivery (2026-09-17):**
[repo-context-forge #30](https://github.com/future3OOO/repo-context-forge/pull/30)
merged at `d3a50cf`; installed as snapshot
`~/.local/share/repo-context-forge/d3a50cf…` behind `current`, which the
Codex, Claude and repo wrappers all resolve; live smoke through the Codex
wrapper on this checkout: budget 8,000, packet 7,755 tokens whole, 20 targets,
`workflow_cli.py`/`tdd_workflow.py` 7 dependents each.
[codex-skills #61](https://github.com/future3OOO/codex-skills/pull/61) merged
at `3415c12` after the reviewer loop closed (two credential threads
report-not-actioned on 0/131 commands in this slot and 0/19,747 across 161
ledgers; non-list `runs` report-only on the `isinstance` writer guard; the
`Verified by` ordering taken). Scoped install of
`hooks/lib/_workflow_db.py`, `hooks/lib/workflow_cli.py`,
`hooks/lib/workflow_state.py`, `hooks/skill-discipline-rearm.py` into
`~/.codex` from `main` `3415c12`, backup `~/.codex-backups/20260917-093328-pr61`;
installed probe: `status` carries no intent, `summary` carries the `Map:` line.
One suite flake on the merge candidate
(`test_higher_slots_count_during_competing_admissions`, slot-admission timing)
passed 18/18 when its class ran alone; unrelated to the change. The
claude-skills mirror of the CLI still carries the old output shapes and needs
its own port.

**#59 change C delivered as [PR #62](https://github.com/future3OOO/codex-skills/pull/62)
(open, not merged, 2026-09-17):** read capture at the existing PostToolUse hook
(path plus whole-file sha256 into a per-workflow sidecar under the repository
slot; runs before the write branch because `_BASH_WRITE` claims any redirect,
which 27.6% of the CX2 corpus's reads carry), emitted at the SessionStart
re-arm as "Inspected this pass, unchanged since" / "Changed since inspected",
each capped at 60 entries and 1,500 chars (3,907 chars at 120 reads). The
matcher claims only the corpus's verbs (sed, rg, inline python, cat, wc, jq,
nl, tail, awk, `<`, head); the offline replay over the committed 350-command
fixture reproduces all 239 labelled read paths at zero context cost and is
pinned by a test. Delegate review over two rounds: gate escape, alphabetical
eviction, unreadable-file crash, redirect-suppressed capture and unbounded
section all fixed; fragment-read wording taken as the honest "inspected"
label rather than dropping sed ranges (163 of 239 events). Behavioural effect
is unproven by construction: acceptance is the seeded cut-resume of the CX2
rollout at ordinal 840 in an isolated codex-home (store keyed by repository
and workflow id, so the sidecar can be seeded from the replay's path set),
baseline 117 repeat reads / 226,822 tokens, prediction ≥50% fewer second-read
tokens, inconclusive if compaction counts differ; requires a maintainer token
cap before launch. D (RCF consuming the sidecar) remains open.
Reviewer-fix round (maintainer findings, 2026-09-17): read paths are resolved
before any repository or ledger lookup; one `content_digest` owner (sha256 at
or under 8 MiB, `size:mtime_ns` above) serves hook and re-arm; the frozen
350-command fixture and its test were dropped because the transcript is not in
the repo and the fixture could never be relabelled. The replay run is the
recorded evidence: 350 commands, 239 labelled reads, 0 misses, 1 extra
(`decisions.md` behind an `if [ -f ]` guard the matcher cannot evaluate;
`is_file()` filters it before the ledger; transcript ordinal 17 shows it was
not printed). The corpus's lasting shapes live in `ReadCandidateTests`.
Review round 2 (26 threads, eight roots, all dispositioned on-thread): substitution
is prefix/escape/separator-safe; python write forms never record; the re-arm lists
the newest paths; malformed sidecars read as empty; `content_digest` refuses
non-regular files; the pruner retires `reads/<wid>.json` with its workflow (measured:
no slot-root prefix convention existed, only `_advisor-sessions/<key>-<wid>.sid`);
euid-0 skip; replay argv guard and boundary-safe normalisation. Report-not-actioned:
the >8 MiB same-size-and-mtime swap. Six RED→GREEN cycles, suite, gate and the
239/239 replay green on the pushed head.
Review round 3 (four threads): `_sqlite_entries` now guards `reads/` with `_walkable`
(symlinked directory no longer followed by `prune --apply`; RED reproduced the outside
deletion at the real Seam). Cubic P2 on the mode-000 test rejected: premise inverted, live
non-root run OK. CodeRabbit reassignment ordering report-not-actioned: reproduced,
under-records only, zero corpus reads fed by a reassigned variable. Final advisor ran on
swe-2-max through a temporary PATH shim because the gateway's gpt-6-astra credit was
exhausted (429 usage_limit_reached); verdict commit-ready, envelope recorded by hand
because the model fenced it.
