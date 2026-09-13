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

## 2026-09-13 — Minimal triage contracts

**Decision:** Every issue and agent brief produced by triage leads with the
observable target objective and a code-grounded smallest sufficient change.
Verification covers that objective and affected existing behavior. Replace the
long brief template and repeated examples with one compact contract.

**Status:** Documentation change on `docs/triage-minimum-change`; global
installation is not part of this editing pass. The delivery PR owns review status.

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
