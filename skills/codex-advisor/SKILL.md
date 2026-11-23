---
name: codex-advisor
description: Consult the Codex advisor at the workflow preflight and final-review checkpoints through the sole local wrapper; a governed pass's next step runs each consult.
---

# Codex advisor

A governed pass's next step runs every consult and supplies its arguments, sessions and evidence. The lead decides only
what follows.

## `preflight-advice`

- Write the draft, with its operation, and run the next step. An operation that does not run on the pass-start tree
  comes back as its error before any consult: correct it and run the step again. The first consult may replace it;
  its replacement stands.
- After the first consult, check the advisor's source claims against the code and correct the draft; a removal it
  designed leaves the draft only with a measurement showing it costs more than it saves.
- The second consult finalises and records the draft: preflight is exactly these two consults.
- A planned stack's governing design is a falsifiable hypothesis: deepen it append-only and correct the draft from the
  smallest real-Seam measurement that resolves an unverified prediction, whoever made it.

## `final-review`

- The advisor records each cause through the workflow, which runs its test and repair; recording its verdict applies the
  tested repair while the checkout still is the reviewed tree.
- A finding closes when its repair passes or, having none, at the advisor's return review once the proof has changed;
  reject one only with `tdd --reject ID 'COMMAND'`, a command whose output shows it does not hold.
- `context-mismatch` is reserved for a candidate or projection identity mismatch and advances nothing.

## Failure

If transport is genuinely unavailable, record the preflight result as `unavailable` with the measured reason; it cannot
approve recording. Final review has no unavailable exception, and no nonce, skip file, stamp or attestation authorizes
completion. State never authorizes Git.
