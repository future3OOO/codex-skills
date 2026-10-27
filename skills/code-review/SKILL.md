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

In a governed pass first load the review package and read every channel it lists:
`python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" checkpoint --repo "$PWD" --phase code-review --channel-dir "$(mktemp -d)"`.
It holds the original request, the recorded contract with each probe's bound
original/candidate outcomes, the finding ledger and the diff; judge
expectations against them, not a paraphrase. Otherwise take the target from the
PR or request. Record
repository, branch, base and head SHAs, and dirty/staged state. Review the
actual diff and current files, not a prose summary; if the target changes, the
review is stale. Open your report with the checkout, workflow id, and tree
you reviewed.

## 2. Read the affected surface

Trace the affected Seam through related modules, callers, callees, other writers
of shared state and competing implementations. Inspect governing artifacts and
named no-change surfaces using the recorded Repo Context Forge packet (the
package's `advisor-projection` channel), GitNexus evidence and source. Do not
begin a workflow, run the Repo Context Forge bootstrap, or mutate workflow
state: the lead's pass owns those operations.
Write the reviewer intake JSON required below. On continuation, inspect
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

Apply [Production Code's outcome and verification rules](../production-code/SKILL.md#minimum-implementation-decision)
independently to the original objective and current candidate. Challenge whether
that objective is fulfilled, including materially wrong behavior the declared
assertions would miss. Judge correction of the original failure, affected-domain
coverage and preservation separately. Bound every claim to exercised inputs. Inspect the runner's recorded original and candidate outcomes, including public results and durable state effects. Matching successful checks establish preservation; equal failures, nonexecution and setup failures prove neither preservation nor a requested change.

Challenge the expectation itself against the original request. Deleting implementation code does not authorize unrelated behavior changes. If an assertion expects an unrequested original-allow to candidate-block regression, reject the assertion and regression even when the comparison reports a clean failure-to-success change. Preserve original behavior where the request does not authorize a difference. Challenge omissions against changed predicates, callees, shared writers and mutation paths. Return findings through the existing workflow.



On return review retain original finding identities/domains. Classify measured
follow-ups as incomplete original repair, inherited missed defect, introduced
regression or unresolved/unrelated concern. Attempt to falsify both expectation
and diagnosis before reporting. Retain useful passing and failing operations with
expected/observed results under those same verification rules.

## 5. Review both axes

Look first for a regression the change introduced, then for code it added that a deletion removes with every recorded comparison still passing. Do not re-run the lead's recorded cases. Judge each listed contract change (a changed kind, a contract item added or re-worded, contract cases added, a case dropped) against the original request; a listing authorizes nothing. A check that finds words in the request or confirms the observed difference does not refute a finding that the difference is unauthorized. Reuse applicable proof; the packet's `open` and `contractChanges` lines are the readiness result, not a second inventory.

Report one finding per owning Module and violated invariant: list every demonstrated input as a case of that finding and name the owner's class-wide correction, not one instance; a further input to an already-reported owner and invariant is a case of that finding (priorFinding), not a new finding.

Run **Standards** and **Spec** independently:

- Standards: documented-standard violations, smell judgements, hard-invariant
  violations, tooling issues only when the tool was unavailable or skipped, and
  bloat: duplicated, ceremonial, or speculative code and tests to delete, with
  the net line reduction each removal buys.
- Spec: missing/partial requirements, unauthorized behavior, incorrect
  implementation, acceptance criteria without proof, and Interface claims
  contradicted by caveats or implementation limits.

Code is proof: the workflow runner executes each finding's evidence when the lead
records it. Objective: maximum performance and efficiency of the delivered code
with the requested behaviour held fixed. Acceptance order: (1) a regression, a
probe that passes on the original and fails or times out on the candidate, binds
and is repaired; (2) a request promise or benchmark binds only as a Behavior Map
contract case; (3) a deletion-only change that every recorded batch still passes
binds and is applied; (4) anything else is a report line. A `probe` is a new
standalone test file asserting a value taken from something executable (the
request's own examples, the original code's result, or the engine's answer),
never a reading. A `change` is a unified diff that only deletes code or replaces
it with one call to an existing function. Every finding cites its governing
requirement and states the expected versus observed effect and consequence.

## 6. Return structured output

Return the reviewed checkout/workflow/tree and a Standards/Spec report with the
actual receipt references in your reply, not in the intake. Write the immutable
intake as a JSON file for the lead's `--input`. Continuations report
original identities as corrected, still present or awaiting evidence, and intake
only new findings. An empty return cannot close an earlier unresolved finding:

```json
{"findings":[{"id":"SPEC-1","axis":"Spec","location":"path:line","claim":"...","probe":{"path":"tests/test_spec1.py","content":"...","command":"python3 -m pytest tests/test_spec1.py"}},
             {"id":"STD-1","axis":"Standards","location":"path:line","claim":"duplicate of existing()","change":"<unified diff>"}]}
```

A request sentence no contract case covers is a report line naming it, never
prose beside `{"findings":[]}`. The lead answers a binding finding only with a fix.
