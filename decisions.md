# Codex Skills Decisions

Project decision record, tracked in Git and kept out of the installed estate.
Record consequential choices and their reasons, not a transcript or per-edit log.
Issue bodies own implementation scope; this record preserves decisions and their
status. New decisions supersede earlier ones explicitly; observations and open
acceptance gaps are not completed delivery.

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
