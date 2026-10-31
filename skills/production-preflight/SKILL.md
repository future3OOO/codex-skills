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
the recorder checks only that it is non-empty text) and `behaviorMap`. `behaviorMap` is authoritative for the obligations TDD must reconcile, not for
choosing the architecture; a plan may reference it but owns no copy. Keep ordinary
local work short; transaction-sensitive work names the full surrounding surface.

### `authoritativeContract`

`d* = argmin C(d)` over designs satisfying the fixed request and preservation. Derive `C` analytically from the production path before choosing a mechanism: repeated calls and scans, copies, retained state and how work grows with input size. Identify the responsible owners and system limits; distinguish observations from estimates. Compare the least sufficient change with a materially different architecture by necessary production work and code at fixed behavior. Explore the deciding feasibility and cost assumptions with small scratch prototypes or direct probes; do not build competing production implementations. Record the cheapest feasible design, deciding evidence and superseded code here; a missing cost model is a material gap. Derive preservation from required outcomes, not a chosen mechanism's intermediate state. TDD's [required probe loop](../tdd/SKILL.md#required-probe-loop) revisits affected decisions against the actual diff.

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

Decide every material unknown from the packet, repository, runtime, governing
artifact or verified source and record the decision in one line; stop only before
a destructive, irreversible or outside-the-worktree action. A settled choice still needing falsification, or unsettled
readings of one behavior, is a pending `behaviorMap` item, not an unknown.

### `behaviorMap`

Freeze requirement meaning and scope in TDD's [Behavior Map](../tdd/recorder.md#behavior-map). Quote each load-bearing promise and select its `seam` under AGENTS.md's Real-Seam proof invariant: the responsible production Interface and real setup. Ground expectations in executed original operations, engine primitives, literal request examples, or request-grounded properties and relations. Preservation takes the original's result on touched behavior. A round-trip or idempotence law must also establish the requested effect. Use the recorder's kind and witness rules. An authorized benchmark is a contract case beside the nearest existing operation; freeze its workload, repetitions, budget and noise rule.

```json
[{"id":"BM_EXPIRY","kind":"contract","basis":"the deadline is inclusive","behavior":"expires at the deadline","seam":"Expiry.is_expired with a real clock","expected":"now equal to expiresAt is expired","boundaryInputs":["test_expires_at_deadline","test_not_expired_before_deadline"],"interpretations":["now > expiresAt","now >= expiresAt"],"interpretation":"now >= expiresAt","authority":"the request's 'at the deadline'"}]
```

Remove every element whose deletion preserves required behavior and distinct necessary proof without increasing whole-system cost. Use real collaborators and observe results and state effects.

Record materially different readings as the owning item's `interpretations` with concrete discriminating `boundaryInputs`; settle them from authority before dependent code, or leave them visibly unsettled, which keeps TDD incomplete. The existing advisor challenges the expectation itself against the request. No authored proof status exists; the runner derives it.

Update changed items by id with `record tdd-map` when obligations change. The runner supplies change or preservation results, freshness and completion. An empty list is appropriate only when there is no behavior claim.

## Recording

Submit the exact artifact through [Codex Advisor's preflight loop](../codex-advisor/SKILL.md#preflight-advice) before tracked edits.
Record it once the advisor returns `approved` for that content.

In the governed workflow record it with `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record preflight`:
with no input it records the advisor-approved draft, including the advisor's last-round edits
(`--check` validates without recording). A refusal
names every violation at once and mutates nothing. Key order and JSON formatting
do not change content.
Resolve outstanding questions before dependent implementation; settle readings and update items through `tdd-map`, never a second preflight recording. Response prose is not evidence.
