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
The checkpoint sends the exact artifact, including its Behavior Map. The advisor is the
designer: it compares the draft's architecture with at least one alternative by cheap probes
(normal-path cost, reuse, estimated production lines) and keeps the cheapest that holds the
request; each load-bearing request promise has a contract item quoting the request sentence it
tests, its expected value from executing an existing operation of the original, the engine or a
literal request example; a benchmark is a contract
case with its workload, reference, repetitions, budget and noise tolerance fixed now.

Return `approved` when the draft has no material gap, otherwise
`changes-required`. The lead revises once and uses `--reconsult` in the same session;
no per-cycle user permission is needed. That second consult is the last: it runs with write access, the advisor
writes the measured architecture into the wrapper's draft copy, closes any remaining gap and cuts
what no request sentence needs, checks its answer with the recorder's dry run, and returns `approved`; `record
preflight` records that edited copy. The checkpoint sends the delta from the
last recorded draft with base and target content identities. A failed result does
not advance that base; retries may repeat the delta. With no recorded base, the
retry carries the full artifact. Draft findings stay in their consult intake;
approval gates the single `record preflight`.
After recording, initial preflight consultation is closed.

Preflight records the governing-design declaration: `--design-file` with the
durable design artifact of a planned stack, or `--design-absent` with a reason; a
single-PR pass passes neither and records absence.
Final review uses that recorded declaration and resumes the existing consult;
there is no need to repeat the declaration or supply `--reconsult`. Do not manufacture a
design document for a trivial pass — declare its absence; the declaration
travels verbatim to the delegate (reasons over 2000 bytes are refused, never
truncated), and for work proposing a new Module, public
Seam, or an architecture-family choice, the phase prompt makes an absent
design a top-ranked finding. The prompt frames the artifact as the decided
design under falsification: the advisor may recommend a different
architecture family, and the decision is settled by measurement, not by the
consult.

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

Code is proof. The prompt carries the shared objective and acceptance order: only a regression
(a `probe` that passes on the original and fails or times out on the candidate) or a `change`
(a diff that only deletes code or replaces it with one call to an existing function) that every
recorded batch still passes binds; anything else is a report line. The runner re-executes each
open finding's probe itself; the advisor raises only what they do not already show:

```json
{"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"...","probe":{"path":"tests/test_spec1.py","content":"...","command":"python3 -m pytest tests/test_spec1.py"}}],"verdict":"fix-before-commit"}
```

`record advisor-result` executes every finding as the repo-production-workflow describes,
replaces each `material` with its executed result and derives the verdict: `fix-before-commit`
when a finding binds, else `commit-ready`. Semantic disagreement is settled by execution, never
by a verdict: `context-mismatch` is reserved for a candidate or projection identity mismatch; it
advances nothing and must be re-consulted. The wrapper stores
the executed findings and the response SHA-256 once; the digest prints each finding's
execution, then the recorded `next`. This is workflow state, not permission to run Git.

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
