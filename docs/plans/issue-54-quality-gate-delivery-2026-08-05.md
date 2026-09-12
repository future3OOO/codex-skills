# Issue #54 quality-gate delivery plan

## Status

- current state: child issues published; governing plan merged in PR #78;
  target architecture under review in PR #79
- governing artifact: this document
- last updated: 2026-08-12

## Objective

Deliver the remaining issue #54 quality-gate work as three sequential AFK PRs:
the complete #75 canonical evaluation Module (captured evaluation plus typed
findings and schema v2, folded into one PR by the 2026-08-08 operator
directive), calibrated exact-duplicate findings, and calibrated
responsibility-owner findings. Each PR must be
independently verifiable and keep subjective rule promotion at the parent
issue's human gate.

Success means the gate evaluates one base-to-candidate snapshot through its
existing `runner.check` Interface, reports production, test, test-support, and
total human-authored evidence accurately, and emits only mechanically grounded
warning evidence until the parent explicitly authorizes a named promotion.

## Source of truth

- authority: GitHub issue #54 and approved child issues #75, #76, and #77
- trusted base: exact `origin/main` SHA recorded in the owning child at
  dispatch; PR B and PR C use the preceding slice's merge SHA
- related contract: issue #49 exclusively owns typed final-tree verification
  binding and workflow persistence; PR #74 is its active implementation and a
  no-change surface for #54
- captured evidence: PR #68 round-six corpus from
  `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` to
  `28cf04e63fa6eb598b938d3a78d782969538d9a9`
- captured diff SHA-256:
  `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a`
- captured human-authored code: `+1129/-8`, net `1121`; these are not the
  merged PR's final-head totals
- responsibility-owner calibration corpus: pinned at parent #54 comment
  5251048442 (cases R, P1, P2, and G, each naming exact base and candidate
  commits, diff SHA-256, role, and adjudication). An implementation agent
  consumes those entries verbatim and must not broaden this corpus.

If a child brief and the parent conflict, the parent controls scope and the
child controls its approved delivery slice. Any ambiguity or rule promotion
returns to parent #54 for human decision.

### Approved schema-v2 warning-promotion decision

`fail_on_warnings` evaluates typed active findings through each exact rule
ID's explicit eligibility. Every #75–#77 QG54 rule initially has eligibility
disabled until parent #54 approves that exact ID. Every surviving non-QG54
warning receives an exact rule ID and explicit eligibility preserving its
schema-v1 behavior; untyped strings, prefixes, families, roles, scores, and
free text never control promotion. CLI input-transport failures remain under
the separate CLI transport contract and outside `runner.check` promotion.
Eligibility is internal `findings.py` rule-policy metadata, not serialized or
caller supplied. #75 and #76 temporarily retain only
`QG-LEGACY-REUSE-ADVISORY` and `QG-LEGACY-GITNEXUS-CONTEXT`, both eligible to
preserve schema-v1 behavior. #77 deletes the former and replaces the latter,
when relevant, with disabled per-affected-rule
`QG54-ANALYSIS-INCOMPLETE` evidence. No non-QG54 warning survives the completed
#77 architecture.
Promotion leaves the source finding at `severity=warning` and its intrinsic
check at `passed=true`; it adds an `errors` projection bound to the eligible
exact ID and sets top-level `ok=false`.

## Conditional completed-state architecture

The following artifacts are review inputs, not implementation authority, until
PR #79 merges. Before that merge, active slices follow parent #54 and their
approved child briefs. On merge, the normative decision becomes the binding
completed-state architecture without replacing or re-sequencing this plan; the
visual remains non-normative.

- normative decision:
  [`docs/decisions/issue-54-quality-gate-target-architecture.md`](../decisions/issue-54-quality-gate-target-architecture.md)
- visual architecture map:
  [`docs/decisions/issue-54-quality-gate-target-architecture.html`](../decisions/issue-54-quality-gate-target-architecture.html)

## Affected surface

- changed boundary: quality-gate evaluation behind the existing
  `runner.check` Interface
- adjacent consumers: quality-gate context, checks, reuse analysis, result
  assembly, CLI and hook adapters, the production-code recorder, and issue
  #49's typed final-tree consumer
- shared classifier: `path_policy` remains the single role-classification
  authority; snapshot construction resolves that policy once per entry
- no-change surfaces: workflow state must not depend on `EvaluationSnapshot`;
  issue #49's CLI, event ledger, persistence, and completion binding remain its
  responsibility; immediate safety checks remain separate; edit-time
  duplicate, responsibility, and growth results remain warning-only

## Contract and proof model

- authoritativeContract: one immutable evaluation owns resolved file roles,
  base/current text, hunk boundaries, growth, completeness, and finding
  anchors for every detector behind `runner.check`
- invariants:
  - all quality-gate role consumers use snapshot-owned roles
  - `path_policy` remains the only classifier and remains directly reusable by
    workflow state without importing the evaluation Module
  - separate diff hunks never form one synthetic duplicate
  - incomplete required scope never reports a false clean result
  - calibration children produce evidence and make no subjective promotion
    decision
- proofPlan: public `runner.check` Interface tests, slice-specific historical
  replay, the captured PR #68 round-six corpus, focused negative cases, the
  integrated hook suite, and the repository quality gate; only #77's
  responsibility-owner replay requires the parent-pinned owner corpus

### Core reduction contract

Issue #77 must turn confirmed competing ownership into a deletion and
deepening loop, not another score. The deep Module behind `runner.check` owns
three explicit finding states: `candidate`, `confirmed-unresolved`, and
`resolved`. Only `candidate` and `confirmed-unresolved` appear in the active
warning collection. `resolved` evidence may remain in telemetry and calibration
with warning-grade provenance, but it must not keep the visible gate non-green.
No state in this slice blocks completion.

Candidate generation remains broad and mechanical. It independently evaluates
state/external-boundary writers, invariant validators, overlapping production
or test Interfaces, workflow/lifecycle coordinators, caller/callee parallel
entry points, fixture/builder/harness lifecycle, forwarding shape, and exact
implementation. These signals require disposition but cannot establish semantic
authority. An owner rule is incomplete if any required evidence class is
skipped, capped, truncated, or unimplemented.

`QG54-DUPLICATE-*` and `QG54-OWNER-COMPETITION-*` are separate rule families.
Responsibility-candidate generation must run independently of duplicate
detection: no duplicate finding is required, and its absence cannot suppress,
downgrade, or prevent an owner candidate. One evaluation may trigger either
family, both, or neither.

A candidate becomes `confirmed-unresolved` only when independently validated,
snapshot-bound evidence establishes the responsibility key and competing
anchors. Exact implementation and forwarding shape strengthen evidence but do
not mechanically infer semantic authority.

Against a complete evaluated snapshot, a `confirmed-unresolved` finding becomes
`resolved` only when one owner remains for that responsibility and role, every
declared superseded surface is absent, no affected candidate-tree caller, test,
or test-support reference resolves to a superseded anchor, and every affected
caller, test, and test-support surface has a mechanically resolved reference or
graph path to the surviving anchor. Required owner-discovery, caller, callee,
test, and test-support scope must all be complete; missing, ambiguous, skipped,
or truncated scope cannot report resolution.

Valid repairs deepen and absorb into the existing owner, replace the owner and
delete the superseded surface, or consolidate both paths behind one deeper
owner and delete the redundant surfaces. Partial deepening, renaming or moving
the competitor, layering a forwarding/orchestration surface over retained
owners without a distinct authority, invariant, external boundary, lifecycle,
failure policy, or runtime variation point, or adding prose, suppression, or
disposition evidence while the confirmed conflict remains does not resolve it.
Neither does shrinking the owner-discovery denominator through configuration,
allowlists, exclusions, or index limits.

Semantic disposition (`same-responsibility`, `distinct-authority`, or
`temporary-coexistence`) is separate from repair strategy (`deepen`, `replace`,
or `consolidate`). A validated `same-responsibility` decision moves a candidate
to `confirmed-unresolved`; declaring a repair strategy never changes state by
itself.

`distinct-authority` is evaluated while the finding is still `candidate`. An
accepted disposition rejects the same-responsibility premise and transitions
directly to `resolved` telemetry without entering `confirmed-unresolved` only
when every referenced anchor resolves in the evaluated snapshot, every owner
evidence class and required graph/test scope is complete, and the decision
matches a parent-bound immutable validation record outside the candidate tree.
A candidate-authored provenance field cannot satisfy that trust contract.
`temporary-coexistence` stays `confirmed-unresolved`: it must name the old and
new owners, surfaces to delete, tracked follow-up, and expiry slice while the
warning remains visible. Only a later parent-approved decision over named rule
IDs may change severity.

## Scope

In scope:

- canonical evaluation, structured findings, and cumulative growth
- exact added-to-added and added-to-baseline duplicate warnings
- mechanically generated responsibility-owner warnings and falsifiable
  dispositions
- checked-in, identity-pinned calibration evidence with per-rule results

Out of scope:

- issue #49 workflow CLI, ledger, verification binding, or persistence
- semantic authority inferred from names, tokens, or graph proximity
- any warning-to-blocker promotion without a later parent decision
- optional orphan/wrapper advisories, automatic rewriting, or a universal
  architecture score

## Delivery map

- plan type: sequential tracer-bullet PR program
- PR count: three — the 2026-08-08 operator directive folded the A1/A2 rebuild
  slices into one complete #75 delivery (PR #90) with PR #80 preserved as
  reference; B and C are unchanged
- active stack depth: one; do not start a child branch before its blocker
  merges
- regroup rule for PR A: the complete #75 behavior measured +1,414 net
  human-authored (+653 production) and package 1,926; the operator accepted
  these measurements and approved the 1,950 package ceiling on 2026-08-08,
  superseding the 650-net regroup trigger and the 1,800 ceiling for this PR
  only.
- regroup rule: keep runtime and its proof together. If a slice forecasts over
  650 net human-authored source lines, shrink it at preflight and return to
  parent #54 before inventing another child. No slice may approach the 1,000
  net split threshold without explicit operator approval.

## PR plan

| PR | Branch | Base | Owner slice | Commit structure | Budget | Entry | Exit |
|---|---|---|---|---|---:|---|---|
| A | `feat/issue-75-captured-evaluation` (PR #90) | current `origin/main` at takeover | complete #75: one scope collector with rename detection, frozen snapshot, stored classification, migrated detectors, bounded baseline capture, typed findings, warning-only cumulative growth, exact-ID `fail_on_warnings`, schema v2, hook warning surfacing | real-CLI RED proof; deep Module replacement plus deletion of superseded modules; detector-policy behavior and promotion proof | measured +1,414 net, package 1,926 of the operator-approved 1,950 ceiling | 2026-08-08 operator fold directive and ceiling approval | #75 acceptance complete, capture/decoder/corpus proof green, active warnings visible through the real hook with exit zero, required checks green |
| B | `feat/issue-76-exact-duplicates` | PR A merge SHA on `origin/main`, recorded in #76 | #76 exact duplicate warnings and calibration | detector behavior and adversarial proof; checked-in corpus evidence | about 500 net | #75 merged | exact findings calibrated and warning-only, required checks green |
| C | `feat/issue-54-responsibility-owners` | PR B merge SHA on `origin/main`, recorded in #77 | #77 responsibility-owner warnings and dispositions | candidate/disposition behavior; positive, negative, and corpus proof | about 500-600 net | #76 merged; parent #54 owner-corpus manifest pinned | every owner evidence class evaluated; no duplicate prerequisite; candidates calibrated and warning-only; required checks green |

PR C may approach 600 net because its calibrated positive and negative proof
must ship with the behavior; splitting that proof into its own PR would create
a horizontal administrative slice. It must still be reduced toward 500 during
preflight and must stop well below 1,000.

## Verification plan

Every implementation pass must run Repo Context Forge, packet-scoped GitNexus
caller/callee checks, production preflight, TDD where practical, code review,
and the Claude Advisor checkpoints required by the repository workflow.

Required proof includes:

- `python3 skills/production-code/scripts/test_code_quality_gate.py`
- `bash hooks/tests/run.sh`
- the quality gate over the branch's real base-to-candidate change
- captured corpus identity and SHA-256 verification
- slice-specific historical replay with no unexamined regressions
- an Interface-level negative where owner discovery sees one owner, truncates
  before the second, and therefore remains active and unresolved; it must use
  #75's real skipped/truncated-scope path, not a hand-built snapshot, stubbed
  discovery collaborator, or newly invented exclusion knob
- textually unrelated Modules writing the same state/external boundary,
  different validators deciding the same invariant, and separate fixture or
  harness owners each produce owner candidates without duplicate findings
- shared data with genuinely different authority or failure policy remains a
  negative owner case; exact duplicate code in genuinely different roles does
  not automatically confirm owner competition
- duplicate-only, owner-only, both-family, and neither-family public scenarios
- rename, facade, and partial-deepening scenarios remain unresolved; only
  complete rewiring and superseded-owner deletion resolve a confirmed conflict
- net-line measurement under delivery-governance rules
- the current-head PR reviewer completion gate before each merge

## Execution checklist

- [x] Three-slice structure approved by the operator (2026-08-05); PR A
  re-sliced into A1/A2 by the 2026-08-07 takeover directive, then folded back
  into one complete #75 PR by the 2026-08-08 operator directive.
- [x] Governing artifact created from `origin/main` without touching PR #74.
- [x] Child #75 published and linked to parent #54.
- [x] Child #76 published, linked, and blocked by #75.
- [x] Child #77 published, linked, and blocked by #76.
- [x] Parent #54 seven-slice list replaced by the approved structure.
- [x] Governing plan reviewed and merged in PR #78.
- [x] PR A (#90) merged with its checklist and reviewer loop complete,
  completing #75 and superseding PR #80 (closed unmerged, reference only).
- [x] PR B merged with its checklist and reviewer loop complete (PR #101,
  merged into `main` as `b391d30` on 2026-08-11, completing #76). Implemented
  on `feat/issue-76-exact-duplicates` from `origin/main`: the three
  warning-only `QG54-DUPLICATE-*` rules in the new `redundancy.py` owner,
  per-role baseline capture, and the superseded
  three-line-window/collapse/duplicate-blocker path deleted. The gate-package
  ceiling was raised 1,950 to 2,300 and then to 2,350 by explicit operator
  approval on 2026-08-10, after the complete slice measured 2,255 and then
  2,320 with every mandatory deletion already applied; the second raise
  carries three correctness fixes the independent review forced.
- [x] Parent #54 pinned the exact responsibility-owner calibration manifest
  before PR C was dispatched (#54 comment 5251048442, 2026-08-11: cases R,
  P1, P2, and G with exact bases, candidates, diff SHA-256s, roles, and
  adjudications).
- [x] PR C merged with its checklist and reviewer loop complete (PR #102,
  merge 9ac844c, 2026-08-12: eight reviewer-fix rounds, zero unresolved
  threads, final advisor commit-ready on the merged head).
- [ ] Parent #54 calibration evidence reviewed by a human.
- [x] Operator recorded the exact-rule schema-v2 `fail_on_warnings` meaning
  before #75 finalizes warning projection.
- [ ] Any later promotion recorded as a separate, explicitly approved decision.

## Change log

- 2026-08-05: created after operator approval and Claude Advisor challenge.
- 2026-08-05: linked published children #75, #76, and #77 and reconciled
  parent #54.
- 2026-08-05: made the one-owner deletion/deepening contract explicit after
  operator direction and Claude Advisor challenge; PR count and order remain
  unchanged.
- 2026-08-05: added complete owner/reference discovery, explicit finding
  states, mechanical rewiring proof, and the parent-owned PR C corpus gate
  after reviewer findings and codebase-design advisor challenge.
- 2026-08-05: made exact-duplicate and owner-competition discovery independent,
  added evidence-class completeness and adversarial public proof, and closed
  candidate-tree self-attestation.
- 2026-08-06: recorded the operator-approved exact-rule schema-v2
  `fail_on_warnings` policy and PR #74 as #49's active, separate implementation.
- 2026-08-07: PR #80 (PR A at `89d5ff2`, package 1956 vs the 1800 ceiling, net
  +1372) preserved as reference under operator direction; PR A is rebuilt from
  current `main` as two vertical slices through `runner.check`, each under
  1,000 net with the package ceiling green at every head. Slice 1 delivers the
  captured base-to-candidate evaluation, stored classification, migrated
  detectors, and growth accounting under the preserved schema-v1 contract;
  slice 2 completes #75 with typed findings, warning-only cumulative growth,
  exact-ID promotion, and schema v2.
- 2026-08-08: the operator's final directive folded the A2 behavior into the
  #75 delivery on PR #90. The complete behavior measured package 1,926 against
  the 1,800 ceiling with every behavior test green; the operator reviewed the
  measured contradiction and approved raising the ceiling to 1,950, keeping
  the measured net (+1,414 human-authored, +653 production). Fleet and cubic
  reviewer rounds on the delivery heads were dispositioned with premise and
  occurrence measurements.
- 2026-08-11: ticked PR B (merged as PR #101) and the parent-pinned owner
  manifest gate (#54 comment 5251048442); PR C dispatched on
  `feat/issue-54-responsibility-owners` from `b391d30`, delivering the two
  warning-only owner-competition rules, disposition records, the pinned
  manifest calibration, and the mandatory lexical-reuse deletion.
- 2026-08-12: PR #102 merged as 9ac844c after eight reviewer-fix rounds
  (fleet/CodeRabbit/cubic loops: signature partitions and roles, graph
  relationship typing and coverage, schema-v1 exactness, survivor and
  carrier trust boundaries, scope-owned facts, gate-driven dedup), every
  round independently reviewed commit-ready; issue #77 closed.
- 2026-08-12: PR #102 reviewer-fix round: five behavior fixes driven by the
  fleet/CodeRabbit/cubic loop (compound-statement signature headers and
  handler bodies, list-typed graph relationship values, closure-ambiguity
  gaps, pairwise state-writer anchors with single-binding dedup, and
  non-string ruleId rejection), each with its own real-CLI RED/GREEN cycle.
