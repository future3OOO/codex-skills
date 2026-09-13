---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
---

# Code review

Initial review uses a fresh native context in the lead's checkout; return review
continues that context when usable. You own review, not implementation: read source and run tests or attacks, but never
edit candidate source, rewrite the contract, mutate the active workflow ledger,
merge, or install. Run every mutating operation against temporary state (for
this estate's recorder, a temporary `CODEX_WORKFLOW_STATE_ROOT`) and clean up.

## 1. Fix the review target

In a governed pass obtain missing contract and candidate identity (`intent`,
`workflowId`, `activeCandidateTree`, `baseOid`) from
`python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" status --repo "$PWD"`
and its recorded evidence; use `--fields` for only the missing facts. Otherwise
take them from the PR or request. Record
repository, branch, base and head SHAs, and dirty/staged state. Review the
actual diff and current files, not a prose summary; if the target changes, the
review is stale. Open your report with the checkout, workflow id, and tree
you reviewed.

## 2. Read the affected surface

Inspect changed files, direct callers and callees, governing artifacts, and
named no-change surfaces, using the Repo Context Forge packet and GitNexus
evidence already recorded. Do not begin a workflow, run the Repo Context Forge
bootstrap, or record anything: the lead's pass owns them. On continuation, inspect
the correction delta and affected preservation/interactions; reuse the unchanged
contract, instructions and applicable evidence. Report when the prior context is
no longer a usable basis.

## 3. Apply the owned rubrics

Use `code-quality` for the seven quality principles and `codebase-design` for
Module/Interface/Seam judgement. Apply the canonical mock, imaginary-risk, and
root-cause invariants from `AGENTS.md`.

Carry this smell baseline as judgement calls: Mysterious Name, Duplicated Code,
Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun
Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man,
and Refused Bequest.

## 4. Falsify the promises

Establish each expectation from the request, public Interface, callers, tests or
applicable standard before calling observed behavior a defect. Historical behavior
is evidence, not authority over an intentional change. Challenge the declared map:
which materially wrong behavior would its actual assertions fail to detect?

For repairs, judge original-attack success, affected-domain coverage and preservation
separately. Require equivalent operations and contract-grounded assertions on actual
N and N+1, with loaded target identities and relevant inputs/environment verified.
Cover the input forms, lifecycle paths and interactions the mechanism affects;
inspect returned values, persisted effects and cleanup. Reuse existing drivers and
applicable receipts. Another executor or handoff alone never requires a rerun;
changed bindings, unreliable evidence and missing coverage do. A passing example
beside a failing affected path remains incomplete. Missing historical proof stays
explicit; never undo/reapply a repair to manufacture RED.

On return review retain original finding identities/domains. Classify measured
follow-ups as incomplete original repair, inherited missed defect, introduced
regression or unresolved/unrelated concern. Attempt to falsify both expectation
and diagnosis before reporting. Retain useful passing and failing operations with
expected/observed results. Suite totals, helper-only assertions, substituted
collaborators and a green map cannot replace these judgments.

## 5. Review both axes

Run **Standards** and **Spec** independently:

- Standards: documented-standard violations, smell judgements, hard-invariant
  violations, tooling issues only when the tool was unavailable or skipped, and
  bloat: duplicated, ceremonial, or speculative code and tests to delete, with
  the net line reduction each removal buys.
- Spec: missing/partial requirements, unauthorized behavior, incorrect
  implementation, acceptance criteria without proof, and Interface claims
  contradicted by caveats or implementation limits.

Every finding states severity, whether it is material, the reproducing
command, expected versus observed effect, consequence, and the smallest
correction.

## 6. Return structured output

Return the reviewed checkout/workflow/tree and a Standards/Spec report with the
actual receipt references. Write the immutable intake directly as JSON for the
lead's `--input`; do not make the lead transcribe findings. Continuations report
original identities as corrected, still present or awaiting evidence, and intake
only new findings. An empty return cannot close an earlier unresolved finding:

```json
{"findings":[{"id":"SPEC-1","axis":"Spec","severity":"high","material":true,"kind":"behavioral","location":"path:line","claim":"...","evidence":"...","consequence":"...","smallest_action":"..."}]}
```

Material missing acceptance evidence is a Spec finding here, never prose
beside `{"findings":[]}`; harmless residual uncertainty is not material. The
lead verifies findings and owns dispositions.
