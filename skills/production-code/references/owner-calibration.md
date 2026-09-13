# Responsibility-owner calibration (issue #77)

Reproducible calibration for `QG54-OWNER-COMPETITION-PRODUCTION` and
`QG54-OWNER-COMPETITION-TEST`. Both rules are warning-only and
promotion-ineligible; this file is evidence for parent #54, not a severity
switch, and encodes no promotion decision.

The corpus is the exact owner manifest a human decision pinned on parent #54
(comment 5251048442) plus the captured PR #68 round-six corpus the target
architecture already pins. The manifest supplements that replay and never
replaces it; the implementing slice neither selected nor broadened either.
`test_owner_manifest_calibration_is_reproducible` replays every case through
the real `code_quality_gate.py` CLI in detached worktrees, re-derives the
diff SHA-256 identities with the canonical diff options, places each pinned
disposition record on the fixed out-of-tree carrier, and asserts the
per-case states and volumes published here, so a stale number cannot pass.

## Pinned cases

| Case | Base | Candidate | Canonical diff SHA-256 | Role | Adjudicated result |
|---|---|---|---|---|---|
| R | `02ebe4c3a9163497f81d05364f2d1b5624477bd6` | `29e355ea3d73e5631914a1376c7ba68a64e5711e` | `40b6f27617e593a8b89e5b722982c834f348ed9e3eaf876ff9e47876814db830` | repeated-scaffolding RED | `confirmed-unresolved`, repair consolidate |
| P1 | `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` | `28cf04e63fa6eb598b938d3a78d782969538d9a9` | `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a` | partial-consolidation positive | `confirmed-unresolved` |
| P2 | `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` | `28cf04e63fa6eb598b938d3a78d782969538d9a9` | `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a` | materially distinct-authority negative | `resolved` telemetry only |
| G | `65f14318cb94d995dcfe961a09eb1e4dbe374dd1` | `08074c7e727d26ce62b0a3f80899de76e34818ef` | `854db8efcb9c9ffaf8efc26bb42475cf7bfde155567a3cbfca5a0e23919c5c0b` | resolved GREEN | `resolved` telemetry only |

## Per-case result

Candidate volume counts active `QG54-OWNER-COMPETITION-*` findings in the
replay; the adjudicated finding is the one the manifest pins, and every other
mechanically generated candidate is outside-pinned-scope evidence reported to
parent #54 — never silently adjudicated, added to the corpus, or treated as
an implementation failure.

| Case | Rule | Active volume | Adjudicated outcome |
|---|---|---|---|
| R (no records) | TEST | 4 | the five-region `fixture-lifecycle` group generates with no duplicate finding and no record required |
| R | TEST | 4 | one `confirmed-unresolved` for `quality-gate:cleanup-verdict-scenario-lifecycle` naming exactly the five pinned tests |
| P1/P2 | PRODUCTION | 6 | P1 `confirmed-unresolved` for `workflow-state-root-location` (`state_root` + the advisor shell resolver line); P2 `resolved` telemetry for `session-association-marker-consumption` |
| P1/P2 | TEST | 8 | none pinned; all eight are outside-pinned-scope scaffolding candidates in the corpus's own tests |
| G | TEST | 4 | one `resolved` telemetry entry: the five superseded scenario tests are absent and unreferenced, `_escape_row` is the one surviving lifecycle owner, and no active warning remains for the pinned responsibility key |

Outside-pinned-scope candidate counts by case, for parent #54:

| Case | Outside-pinned-scope candidates |
|---|---|
| R | 3 |
| P1/P2 | 13 |
| G | 4 |

## Parent-pinned record digests

The parent decision of 2026-08-12 binds every state-changing disposition
record by the SHA-256 of its canonical content (sorted-key JSON,
`schemaVersion: 1` stamped, `validationRoot` excluded). The gate never
queries GitHub: it validates a supplied record against its declared digest,
the replay asserts these parent-pinned values verbatim, and external review
establishes the parent binding.

| Case | Canonical record SHA-256 |
|---|---|
| R | `08f61bed0d5df8b9435a38b1fb1712530bebb063d7c9b457dbe85770f97a016e` |
| P1 | `d7bda52e9bff988face173e92467cc2db78d159c1564f2817075b4cd1c195de8` |
| P2 | `3e96fd97af71111fc5e724f457ca5b3f32ef79fdd4d0a7a25e635ce600a0b39c` |
| G | `6c2fdd01db924618efc9df048884b2ef64082d5d254657e6fae4d47c92d15575` |

The same decision chose 1b for graph evidence: an absent graph input cannot
establish complete caller/callee scope and the snapshot index is not a
substitute, so every replay above supplies exact, snapshot-bound graph
evidence — `--gitnexus-context-json` declaring the replay's base and
candidate and carrying caller/callee symbol results for the range's changed
source files (a bare declaration is not evidence). Without it the owner
rules read incomplete and nothing resolves. Records reach each replay
through the fixed out-of-tree carrier (`$GIT_DIR/qg54-dispositions.json` in
the throwaway worktree), and only records matching the shipped parent-pinned
identifier and digest table may resolve; self-issued records leave findings
active with rule incompleteness, which the negative tests prove.

`unexaminedCount = 0`: every pinned anchor above is adjudicated by its
manifest record, and every additional candidate is enumerated in the counts
above. The largest recurring outside-scope shapes are repeated
`state-writers`/`fixture-lifecycle` groups inside the corpus's own test
files and a real duplicated `git` test helper pair — the repeated-scaffolding
debt that motivated #87's absorption into #77.

## Evidence-class completeness

Both owner rules evaluate all eight mechanical evidence classes on every run
(`state-writers`, `invariant-validators`, `interface-overlap`,
`lifecycle-coordinators`, `parallel-entry-points`, `fixture-lifecycle`,
`forwarding-surfaces`, `exact-retained`) and serialize one ledger entry per
class. Over the round-six replay both rules report
`completeness: {complete: true}`; the case G replay restores complete owner
discovery by naming the whole base tree through the existing
`--repo-context-packet` input — widening discovery, the direction the
`QG54-ANALYSIS-INCOMPLETE` action prescribes, never an exclusion knob.
