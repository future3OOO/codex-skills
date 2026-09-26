# Responsibility-owner calibration (issue #77)

Reproducible calibration for `QG54-OWNER-COMPETITION-PRODUCTION`, which is
warning-only and promotion-ineligible; this file is evidence for parent #54, not a severity
switch, and encodes no promotion decision.

The corpus is the production cases of the owner manifest a human decision
pinned on parent #54 (comment 5251048442), over the captured PR #68 round-six
corpus the target architecture already pins. Issue #107 deleted the test-owner
rule, so the manifest's test-only cases R and G no longer replay.
`test_owner_manifest_calibration_is_reproducible` replays every case through
the real `code_quality_gate.py` CLI in detached worktrees, re-derives the
diff SHA-256 identities with the canonical diff options, places each pinned
disposition record on the fixed out-of-tree carrier, and asserts the
per-case states and volumes published here, so a stale number cannot pass.

## Pinned cases

| Case | Base | Candidate | Canonical diff SHA-256 | Role | Adjudicated result |
|---|---|---|---|---|---|
| P1 | `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` | `28cf04e63fa6eb598b938d3a78d782969538d9a9` | `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a` | partial-consolidation positive | `confirmed-unresolved` |
| P2 | `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` | `28cf04e63fa6eb598b938d3a78d782969538d9a9` | `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a` | materially distinct-authority negative | `resolved` telemetry only |

## Per-case result

Candidate volume counts active `QG54-OWNER-COMPETITION-PRODUCTION` findings in the
replay; the adjudicated finding is the one the manifest pins, and every other
mechanically generated candidate is outside-pinned-scope evidence reported to
parent #54 — never silently adjudicated, added to the corpus, or treated as
an implementation failure.

| Case | Rule | Active volume | Adjudicated outcome |
|---|---|---|---|
| P1/P2 | PRODUCTION | 6 | P1 `confirmed-unresolved` for `workflow-state-root-location` (`state_root` + the advisor shell resolver line); P2 `resolved` telemetry for `session-association-marker-consumption` |

Outside-pinned-scope candidate counts by case, for parent #54:

| Case | Outside-pinned-scope candidates |
|---|---|
| P1/P2 | 5 |

## Parent-pinned record digests

The parent decision of 2026-08-12 binds every state-changing disposition
record by the SHA-256 of its canonical content (sorted-key JSON,
`schemaVersion: 1` stamped, `validationRoot` excluded). The gate never
queries GitHub: it validates a supplied record against its declared digest,
the replay asserts these parent-pinned values verbatim, and external review
establishes the parent binding.

| Case | Canonical record SHA-256 |
|---|---|
| P1 | `d7bda52e9bff988face173e92467cc2db78d159c1564f2817075b4cd1c195de8` |
| P2 | `3e96fd97af71111fc5e724f457ca5b3f32ef79fdd4d0a7a25e635ce600a0b39c` |

The same decision chose 1b for graph evidence: an absent graph input cannot
establish complete caller/callee scope and the snapshot index is not a
substitute, so every replay above supplies exact, snapshot-bound graph
evidence — `--gitnexus-context-json` declaring the replay's base and
candidate and carrying caller/callee symbol results for the range's changed
source files (a bare declaration is not evidence). Without it the owner
rule reads incomplete and nothing resolves. Records reach each replay
through the fixed out-of-tree carrier (`$GIT_DIR/qg54-dispositions.json` in
the throwaway worktree), and only records matching the shipped parent-pinned
identifier and digest table may resolve; self-issued records leave findings
active with rule incompleteness, which the negative tests prove.

`unexaminedCount = 0`: every pinned anchor above is adjudicated by its
manifest record, and every additional candidate is enumerated in the count
above.

## Evidence-class completeness

The owner rule evaluates all seven mechanical evidence classes on every run
(`state-writers`, `invariant-validators`, `interface-overlap`,
`lifecycle-coordinators`, `parallel-entry-points`, `forwarding-surfaces`,
`exact-retained`) and serializes one ledger entry per class. Over the
round-six replay it reports `completeness: {complete: true}`.
