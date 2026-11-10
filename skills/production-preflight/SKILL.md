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
no-change surfaces and real test surface for the design judgment. Invoke
[codebase-design](../codebase-design/SKILL.md) for a new Module or public Seam,
or a changed public Interface. Deepen the existing owner; a new Module must earn
its Interface. Absorb touched shallow helpers or name a concrete blocker.

For transaction-sensitive work apply the [transaction doctrine](../production-code/references/transaction-doctrine.md).
Name the authoritative records, mutation and recovery paths, interleavings,
invariants and proof surface; an unnamed material part blocks recording.

## What To Produce

Two fields: `authoritativeContract` (concise prose for the reviewers who read it;
the recorder checks only that it is non-empty text) and `behaviorMap`. `behaviorMap` is authoritative for the obligations TDD must reconcile, not for
choosing the architecture; a plan may reference it but owns no copy. Record required
outcomes, not provisional implementation choices. Keep design reasoning in the
existing advisor context. Keep ordinary local work short; transaction-sensitive
work names the full surrounding surface.

### `authoritativeContract`

State the request, preserved behavior and responsible production path. Include
source facts and proposed reuse as revisable reasoning.
The [preflight advisor](../codex-advisor/SKILL.md#preflight-advice)
independently challenges the lead's cost model and architecture; the lead checks the source
claims before the same advisor finalizes the draft. Keep unresolved predictions
visible. Preserve required outcomes rather than a proposed mechanism's intermediate
state. TDD revisits those responsibilities against the actual diff.

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
and concrete discriminating inputs in its `boundaryInputs`. Investigation is complete when each
decision's meaning is established over its reachable values or its uncertainty
is explicit. Unambiguous predicates need no additional fields or inventory.

### Unknowns

Decide every material unknown from the packet, repository, runtime, governing
artifact or verified source and record the decision in one line; stop only before
a destructive, irreversible or outside-the-worktree action. A settled choice still needing falsification, or unsettled
readings of one behavior, is a pending `behaviorMap` item, not an unknown.

### `behaviorMap`

Quote each load-bearing promise in TDD's [Behavior Map](../tdd/recorder.md#behavior-map)
and name its responsible production Interface and real setup. Preservation uses the
original's result, including other decisions controlled by shared logic the edit
removes. New terms use the existing owner's result; use request-grounded properties
only when no owner exists. A round-trip must also establish the requested effect.
The advisor's deciding work and runtime assertions supplement functional proof.

Describe concrete inputs and outcomes. Bind a command after its executable probe
exists; the runner captures it. Do not create obligations from proposed test names.

Remove every element whose deletion preserves required behavior and distinct necessary proof without increasing whole-system cost. Use real collaborators and observe results and state effects.

Record materially different readings as the owning item's `interpretations` with concrete discriminating `boundaryInputs`; settle them from authority before dependent code, or leave them visibly unsettled, which keeps TDD incomplete. The existing advisor challenges the expectation itself against the request. No authored proof status exists; the runner derives it.

Update changed items by id with `record tdd-map` when obligations change. The runner supplies change or preservation results, freshness and completion. An empty list is appropriate only when there is no behavior claim.

## Recording

Submit the exact artifact through [Codex Advisor's two-round preflight](../codex-advisor/SKILL.md#preflight-advice) before tracked edits.
Record it once the advisor returns `approved` for that content.

The runtime continuation supplies the recording command. Record the approved draft
once; later corrections use the same map and pass. Changed command formatting is
not a changed contract. Resolve interpretations before dependent implementation;
response prose is not execution evidence.
