---
name: diagnose
description: Use for bugs, failures, flaky behavior and performance regressions; require real reproduction and a cause witness.
---

# Diagnose

`AGENTS.md` owns entry to a fix. This skill owns causal investigation;
[Production Code](../production-code/SKILL.md#minimum-implementation-decision)
and [TDD](../tdd/SKILL.md#required-probe-loop) own comparison and execution.
Reuse their existing batch and workflow review; add no approval stage.

## Reproduce and distinguish causes

Record required outcomes, affected preserved behavior and a finite time/step
budget. Reproduce through the real production Interface with real collaborators
and the caller's setup. Prefer a direct, fast probe or captured replay. When only
a human can act, specify exact steps and retain captured evidence. Missing
observations, setup failures and harness timeouts are UNKNOWN; an observed breach
of a specified product deadline is FAIL. Without reproduction, report unresolved.

Control inputs, state, seeds and schedules where possible; wait on conditions,
not guessed sleeps. For intermittent failures, declare faithful exposure and
repetition or statistical criteria. For performance defects, fix workload,
measurement and noise criteria before comparing. One clean run proves neither
elimination nor improvement. Save raw evidence outside context.

Trace the symptom to the violated invariant and responsible owner, using relevant
source, changes, working paths and architectural decisions. Reuse graph evidence
under [Repo Context Forge](../repo-context-forge/SKILL.md); graphs do not prove cause.
Before removing a shared producer or condition, trace every decision it controls
and execute original cases that distinguish the affected preserved outcomes.
A purpose-qualified deletion does not authorize deleting its other uses.

Admit a hypothesis only with a mechanism, distinguishing prediction and concrete
probe. Run the cheapest probe separating live explanations that require different
repairs; do not manufacture a hypothesis quota or bundle speculative fixes.
Include interacting causes when evidence supports them. A symptom disappearing
does not distinguish removal from concealment or displacement.

Use the existing independent review to challenge the diagnosis when confirmation
did not distinguish alternatives, only one explanation was considered, two leading
hypotheses were refuted, or shared state changes. Reuse its evidence and budget;
do not add a review round. Unprobeable speculation is uncertainty, not a new gate.

## Freeze the mechanism witness

Observe the earliest established defect-generating event before recovery through
its responsible real Interface and effects. Temporary internal traces support
diagnosis; they do not replace production proof. Keep the outcome check too.
Show the detector fires on the original, then freeze its elimination criterion
and relevant scenarios in the existing Behavior Map. A catch, retry, default or
guard leaving that event intact must not satisfy the criterion.

Validate equivalent event coverage on the candidate, including relocated work.
Reuse detector sensitivity evidence only while its observation path and bindings
remain valid; otherwise plant the relevant defect in a disposable candidate under
Production Code's sensitivity rules. An unrelated error or setup failure proves
nothing. A positive control establishes detection, not the causal explanation.
An untestable redesign mapping remains unresolved.

## Repair and compare

Correct the responsible ownership, state transition, representation or dependency.
Remove unnecessary work before adding compensation. Necessary architectural
change belongs in the repair; unrelated redesign does not.
Compare the least sufficient resulting architecture, including operation,
maintenance and justified complexity costs, rather than the smallest patch. Label estimates and incomparable
alternatives instead of inventing an optimum.

Put the mechanism witness and required outcomes in the existing real N/N+1 batch.
Keep preservation expectations unchanged. Advisor approval alone
cannot authorize a new meaning for behavior the request keeps. Run the minimized
and original scenarios plus affected preservation cases. For related regressions,
use accumulated inputs to trace the shared mechanism before another repair;
do not accumulate narrower substitutes for the removed responsibility.

Conclude solved only when required outcomes, unchanged preservation, the frozen
elimination criterion and valid detector sensitivity pass. Record known practical repair-distinguishing
probes in the existing map and batch; missing evidence or budget exhaustion stays unresolved.
Bound claims to executed scenarios: artifact/emission checks prove delivery, not model behavior.

Retain probes under TDD's retention rule. Complete cleanup of temporary traces,
prototypes and disposable mutations, then rerun invalidated checks on the cleaned
candidate. Report mechanism evidence, repair, tested scope, burden comparison and
unresolved obligations.
