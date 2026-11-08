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

Investigate the lead candidate design before production preflight is recorded. Supply it with
`--preflight-file <draft.json>`; the wrapper snapshots it before consultation.
The advisor designs against [Production Preflight's frozen evaluation and architecture rules](../production-preflight/SKILL.md#behaviormap),
checking preservation against executed original results and challenging change expectations and costs.

Preflight requires two consults in the same session.
Round 1 investigates with full access and returns its model and design as findings;
even `approved` cannot record preflight. Supply any response with `--reconsult`.
Round 2 incorporates that investigation and response into the existing draft copy,
cuts unnecessary work and code, and dry-runs the result.
It closes the remaining design gaps and returns `approved`; no third consult is allowed.
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
The consult runs in the lead checkout with full execution access and its installed estate.
The wrapper supplies and cleans an original snapshot at passStartOid and scratch for probes.
Use the supplied source prefixes and recorded project executable; check imported source paths.
Run new preservation probes on original and candidate; test new behavior against the request.
Leave candidate files, Git state and workflow state unchanged. This is trusted access;
preflight skips the candidate-staleness check, which neither restores nor confines writes.

Findings follow the workflow's [executable evidence and acceptance order](../repo-production-workflow/SKILL.md#10-delegate-code-review).
The wrapper records the verdict without running cases. Submit new executable findings through `tdd --as final` before returning the verdict; its payload contains only report lines or recorded references. Retained findings run at `verify` or `complete`, with results cached by candidate tree. `context-mismatch` is reserved for candidate/projection identity mismatch;
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

Rechecks settle repairs, passing applied changes and proposal-caused failures against a passing baseline; shared failures stay pending. There is no disposition command.
