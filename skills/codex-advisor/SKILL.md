---
name: codex-advisor
description: Consult the Codex advisor at the workflow preflight and final-review checkpoints through the sole local wrapper.
---

# Codex advisor

Use `scripts/ask-codex-advisor.sh` as the sole production transport. Do not use
the plugin forwarder, Agent tool, or a second wrapper as a fallback.

Choose one short stable slug per production pass. Reuse it for both checkpoints;
phase belongs in `--phase`, not in the slug.

## Checkpoints

### `preflight-advice`

Review the drafted production preflight before it is recorded. Supply it with
`--preflight-file <draft.json>`; the wrapper snapshots it before consultation.
The advisor designs against [Production Preflight's frozen evaluation and architecture rules](../production-preflight/SKILL.md#behaviormap),
checking preservation against executed original results and challenging change expectations and costs.

Preflight allows two consults before recording.
After `changes-required`, revise once and use `--reconsult` in the same session.
The second round edits only the wrapper's draft copy, cuts unnecessary work,
dry-runs it and returns `approved`; `record preflight` records it.
The checkpoint sends the delta with both content identities; failed results keep
that base, and the first request sends it whole.

Preflight records the governing-design declaration: `--design-file` with the
durable design artifact of a planned stack, or `--design-absent` with a reason; a
single-PR pass passes neither and records absence.
Final review resumes the consult with that declaration; do not repeat it or
supply `--reconsult`. Declare absence rather than manufacture a trivial design
document. The declaration travels verbatim; reasons over 2000 bytes are refused.
An architecture choice is settled by evidence, not by the consult.

A design artifact carries: the chosen architecture and rationale; every
architecture family exploration or planning produced, with the technical
rejection reason for each rejected family; the verified exploration findings
that constrain the design and how each was measured; and every unverified
falsifiable prediction explicitly marked unresolved. The design is a falsifiable
hypothesis, not an immutable authority: deepen it append-only in the same
unpushed workflow and carry the current file to each consult — a changed
declaration records as new workflow evidence while the ledger keeps every prior
version. The wrapper sends the declaration and the complete design body as
framed evidence, and the advisor never owns dispositions.

The canonical imaginary-risk ban and the premise/occurrence checks in the
repo's `AGENTS.md` govern architecture-family decisions; this checkpoint adds
procedure, not new doctrine. A family selection or rejection resting on a
falsifiable prediction about existing behavior, tests, compatibility, or
runtime semantics stays unresolved — whoever made the prediction: planning,
advisor, or lead — until the smallest practical real-Seam measurement
resolves it. Correct the draft using that measurement before resubmission.

### `final-review`

Run after implementation, verification, and the required native delegate code review. This
independent checkpoint verifies the candidate by running code rather than trusting the lead
or delegate verdict. The wrapper sends the recorded original request once, the checkpoint's
retained advisor projection, the current governing-design declaration (a deepened design
records as new evidence), the finding ledger, and one direct `passStartOid^{tree} ->
activeCandidateTree` diff, each deleted file as its header and line count.
The consult runs in a disposable copy of the checkpoint candidate with workspace-write access,
the copy first on `PYTHONPATH` and the venv of the recorded comparisons' interpreter first on
`PATH`; the lead's checkout is not in it.

Findings follow the workflow's [executable evidence and acceptance order](../repo-production-workflow/SKILL.md#10-delegate-code-review).
The wrapper records the runner's derived verdict and prints each execution with
`next`. `context-mismatch` is reserved for candidate/projection identity mismatch;
it advances nothing. State never authorizes Git.

## Invocation

```bash
"$HOME/.codex/skills/codex-advisor/scripts/ask-codex-advisor.sh" \
  --slug "<task>" --phase preflight-advice --preflight-file "<draft.json>" \
  --cwd "$PWD" --design-file "<design-artifact>" \
  --budget 600 -- "<focused scope question>"

"$HOME/.codex/skills/codex-advisor/scripts/ask-codex-advisor.sh" \
  --slug "<task>" --phase final-review \
  --cwd "$PWD" --design-file "<design-artifact>" \
  --budget 600 -- "<focused completion question>"
```

For a long question, drop the `--` argument and feed it on stdin:
`< question.txt`.

## Failure and disposition

If transport is genuinely unavailable, record the preflight result as
`unavailable` with the measured reason; it cannot approve recording. There is no unavailable exception for the final
review. No nonce, skip file, stamp, attestation, or audited exception authorizes
completion.

A repaired final finding settles at the re-check that re-executes it, once its evidence no longer
binds; there is no disposition command.
