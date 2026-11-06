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

`d* = argmin C(d)` over designs satisfying the fixed request and preservation. The lead supplies the request, preservation requirements, source facts and candidate design. The advisor derives `C` analytically from the production path: repeated calls and scans, copies, retained state and how work grows with input size. Identify the responsible owners and system limits; distinguish observations from estimates. Compare the least sufficient change with a materially different architecture by necessary production work and code at fixed behavior. Explore the deciding feasibility and cost assumptions with small scratch prototypes or direct probes; do not build competing production implementations. Round 1 is the main investigation and returns its model, challenger design and its growth bound as findings; it may ask the lead to verify specific claims. The lead verifies the findings against code, corrects omissions or errors, adds ideas and incorporates them into the draft. Round 2 resumes the same advisor, investigates further as needed, edits the draft itself and returns approved; if no edit is needed it approves as is. It records the model, most efficient supported feasible design, deciding evidence and superseded code here. Derive preservation from required outcomes, not a chosen mechanism's intermediate state. TDD's [required probe loop](../tdd/SKILL.md#required-probe-loop) revisits affected decisions against the actual diff.

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
and the commands asserting discriminating inputs in its `boundaryInputs`. Investigation is complete when each
decision's meaning is established over its reachable values or its uncertainty
is explicit. Unambiguous predicates need no additional fields or inventory.

### Unknowns

Decide every material unknown from the packet, repository, runtime, governing
artifact or verified source and record the decision in one line; stop only before
a destructive, irreversible or outside-the-worktree action. A settled choice still needing falsification, or unsettled
readings of one behavior, is a pending `behaviorMap` item, not an unknown.

### `behaviorMap`

Freeze requirement meaning and scope in TDD's [Behavior Map](../tdd/recorder.md#behavior-map). Quote each load-bearing promise and select its `seam` under AGENTS.md's Real-Seam proof invariant: the responsible production Interface and real setup. Ground expectations in executed original operations, engine primitives, literal request examples, or request-grounded properties and relations. Preservation takes the original's result on touched behavior. A round-trip or idempotence law must also establish the requested effect. Use the recorder's kind and witness rules. A deciding production cost is an additional contract case, never a replacement for the whole design obligation. The lead proposes metric ideas; round 1 investigates a challenger and recommends the challenger design and its growth bound; the lead responds; round 2 freezes them. Prefer timing the real operation on real inputs at production size against an equivalent same-run reference; counts, memory and output quality may fit better. New behavior needs an equivalent reference or a justified resource target. Record workload, reference, repetitions, operation budget, tolerance and inclusive boundary in behavior and expected. Challenge the lead's design with the most efficient production design you can find for the same behaviour: the least work, or the fastest or leanest result, in code the change edits or its requested operation runs through, including existing inefficient machinery. Name its dominant cost term and how that term grows. Bind that growth as a growth case on the lead's build, measured inside the operation, never weaker than the original's. If you find nothing better, state why the dominant term cannot be reduced. The lead builds the challenger or refutes it with a failing comparison or measurement. If no production cost term can affect the outcome, say why in one line and freeze no performance case. Explicit user restrictions still apply.

Use the runtime form. Bind concrete discriminating case commands when known;
running a case captures its command without a placeholder.

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
Resolve outstanding questions before dependent implementation. After recording, `tdd-map` may add preservation items and boundary cases; contract/conformance kind, behavior and expected are fixed. A user scope change requiring a new contract starts a new pass. Response prose is not evidence.
