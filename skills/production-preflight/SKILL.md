---
name: production-preflight
description: Produce the required before-edit proof for production code changes — the authoritative contract and the Behavior Map of attacks that falsify it. Use before writing code on implementation, refactor, bug-fix, or review-comment passes.
---

# Production Preflight

Use this skill before making tracked edits on preflight-required code turns.

The [repository workflow](../repo-production-workflow/SKILL.md) owns checkout
alignment, phase order, advisor consultation and recording. Reuse the active pass
and governing artifact; stay within its owner slice and verification scope.
Use [diagnose](../diagnose/SKILL.md) for bugs before choosing a correction.

Apply [Production Code's Minimum Implementation Decision](../production-code/SKILL.md#minimum-implementation-decision):
name the affected Interface, existing Module and reuse path, adjacent consumers,
no-change surfaces and real test surface in the contract. Invoke
[codebase-design](../codebase-design/SKILL.md) for a new Module or public Seam,
or a changed public Interface. Deepen the existing owner; a new Module must earn
its Interface. Absorb touched shallow helpers or name a concrete blocker.

For transaction-sensitive work apply the [transaction doctrine](../production-code/references/transaction-doctrine.md).
Name the authoritative records, mutation and recovery paths, interleavings,
invariants and proof surface; an unnamed material part blocks recording.

## What To Produce

Two fields: `authoritativeContract` (concise prose for the reviewers who read it;
the recorder checks only that it is present) and `behaviorMap`. `behaviorMap` is authoritative for the obligations TDD must reconcile, not for
choosing the architecture; a plan may reference it but owns no copy. Keep ordinary
local work short; transaction-sensitive work names the full surrounding surface.

### `authoritativeContract`

Before editing, trace the shared responsibilities visible in the original code and derive distinguishing inputs for affected preservation, reusing existing items and probes; where the request invalidates an assertion in an existing test, change only that assertion and keep the test. TDD's [required probe loop](../tdd/SKILL.md#required-probe-loop) revisits them against the actual diff.

Before choosing an implementation or writing tests, investigate each behavioral
predicate that decides an outcome:

1. **Define the decision over its reachable values.** Inspect the actual producer
   Interfaces and trace the values reaching the deciding consumer, including
   relevant types, representations and conversions. State the deciding operation
   and how it treats those values. Task examples and convenient fixtures do not
   bound the input domain. Describing how a value is obtained, or repeating the
   predicate's label, does not define how it is judged.
2. **Challenge the rule, even when it seems conventional.** Compare it with the
   task contract and the rules used by other Modules on the same path. Could a
   supported value receive different decisions under plausible readings? Apply
   [TDD's differential proof](../tdd/tests.md#what-the-batch-must-prove): derive a
   concrete counterexample, evaluate the competing rules on it, and compare the
   outcomes. Test the proposed meaning, not an implementation chosen in advance.
3. **Resolve from governing authority or expose the uncertainty.** The authority
   must distinguish the readings. Their shared wording, your proposed consumer,
   and a runtime difference do not authorize a choice. If both readings remain
   compatible with the governing evidence, keep the choice unsettled before
   dependent code.

State the reachable values and decision rules concisely in this contract and the
existing `behaviorMap`: competing readings go in the owning item's `interpretations`,
and the discriminating inputs in its `boundaryInputs`. Investigation is complete when each
decision's meaning is established over its reachable values or its uncertainty
is explicit. Unambiguous predicates need no additional fields or inventory.

### Unknowns

Decide every material unknown one of three ways: resolve it from the packet,
repository, runtime, governing artifact or verified source; interview with
`/grilling`, one question at a time, when it can change Module shape, public
Interface, Seam placement, data contract or irreversible scope; or block honestly
and do not record. A settled choice still needing falsification, or unsettled
readings of one behavior, is a pending `behaviorMap` item, not an unknown.

### `behaviorMap`

Record concrete falsifiers of the load-bearing public promises. TDD's [Behavior Map](../tdd/recorder.md#behavior-map) defines the fields and what readiness enforces by execution. IDs are stable uppercase identifiers. The basis ties the expectation to the original request or affected preservation; select the `seam` under AGENTS.md's Real-Seam proof invariant, naming the responsible production Interface and its real setup for the assertion being proved. For each condition of a decision the plan edits, kept conditions included, state each feasible decisive context ([MC/DC](../tdd/tests.md#mcdc)) with one input on each side of that occurrence of the condition (the same check in another branch is a separate context) and, only where the request changes the decision, its required result, in the item owning its Interface: one contract and one preservation item per owning Interface, each context a case of it. A kept decision's result is the original's, supplied by the comparison. Attach the executed cases to that item's `boundaryInputs` after implementation.

```json
[{"id":"BM_EXPIRY","kind":"contract","basis":"the deadline is inclusive","behavior":"expires at the deadline","seam":"Expiry.is_expired with a real clock","expected":"now equal to expiresAt is expired","boundaryInputs":["test_expires_at_deadline","test_not_expired_before_deadline"],"interpretations":["now > expiresAt","now >= expiresAt"],"interpretation":"now >= expiresAt","authority":"the request's 'at the deadline'"}]
```

Derive attacks from actual promises: atomicity needs supported failure and cancellation; persistence needs reopen and another connection; shared state needs material writer interleavings; parsers need decisive boundaries and captured production inputs, named in `boundaryInputs`. Use real collaborators and observe results and state effects. A missing entrypoint is not proof of its downstream guarantees.

Record materially different readings as the owning item's `interpretations` with concrete discriminating `boundaryInputs`; settle them from authority before dependent code, or leave them visibly unsettled, which keeps TDD incomplete. The existing advisor challenges the expectation itself against the request. No authored proof status exists; the runner derives it.

An owning probe links a finding with `sourceRefs: [{"type":"finding","evidenceId":"<intake>","id":"R-1"}]`. The runner executes it on the recorded reviewed source and current repair. Update changed items by id with `record tdd-map` when obligations change; this cannot silently discharge a finding. The runner supplies change or preservation results, freshness and completion. An empty list is appropriate only when there is no behavior claim.

## Recording

Submit the exact artifact through [Codex Advisor's preflight loop](../codex-advisor/SKILL.md#preflight-advice) before tracked edits.
Record it once the advisor returns `approved` for that content.

In the governed workflow record it with `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record preflight`:
with no input it records the advisor-approved draft, including the advisor's last-round edits
(`--check` validates without recording). A refusal
names every violation at once and mutates nothing. Key order and JSON formatting
do not change content.
Resolve outstanding questions before dependent implementation; settle readings and update items through `tdd-map`, never a second preflight recording. Response prose is not evidence.
