---
name: tdd
description: TDD for production behavior changes through recorded original/candidate comparisons at real Seams. Use when changing production behavior or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

The original source is the baseline. The request decides which differences are correct. One direct batch proves both: the workflow runner executes it on the original and the edited source. Probe quality is in [tests.md](tests.md); the runner and the Behavior Map fields are in [recorder.md](recorder.md).

## Required probe loop

Do these steps in order for each coherent production edit. Prove named cases during TDD; the existing review checks completeness against the original and request.

1. **Select the direct batch.** Start from the preflight's [Behavior Map](recorder.md#behavior-map). Reuse existing tests, fixtures and assertions. Call the responsible Interface with the source and state its production caller supplies. Do not run bootstrap or workflow orchestration inside a probe. Use real collaborators; read [mocking.md](mocking.md) when you choose the runtime or the failure path for a remote, process, filesystem or concurrent Seam. If the Interface does not exist yet, create it, then drive each guarantee through it; an import failure proves nothing. Done when each map item names its cases in the batch.
2. **Make the edit.** Implement the best justified architecture found, then remove code it does not need.
3. **Reconcile the actual diff.** Read the diff against the original responsibilities. For each condition, guard, branch, write or return that the edit adds, removes, weakens, strengthens or rewrites, find its decisive contexts with [MC/DC](tests.md#mcdc). Find its value boundaries (empty, one, several; below, at, above a limit). Name each existing test whose asserted output the edit changes or that the diff removes or rewrites, the conditions it exercises and every other decision those conditions control; a decision the request keeps needs its own case. Investigate callees and decisions the runner cannot see. Done when each context and boundary is a named case of the map item owning its Interface (`record tdd-map`), or has an executed case that shows it cannot be reached.
4. **Extend the batch.** Add those cases to the existing direct batch. Each case asserts an existing assertion or the original's observed result, unless the request names the new result; check a fixture's assumption on the original before asserting it. Use case-local assertions, such as unittest subtests, so that one failure cannot skip later cases. Done when every named case is in the probe.
5. **Compare.** Run the batch through the runner: `workflow.py tdd --behavior-id BM_X -- COMMAND [ARG...]`. Done when the receipt shows every named case on both trees.
6. **Judge and repair.** Restore unrequested behavior or repair its owning design, extend the same direct batch, and rerun it. Inspect every original consumer of a removed producer or guard; no surviving candidate reads does not mean the original had no other job. A passing case proves only its observed behavior, never every responsibility in a diff hunk. The lead and existing reviewer judge coverage and authority. Never weaken a frozen expectation. First proved evaluations and later distinct cases remain retained when the current map narrows. After final review starts, continue with that final advisor.


Bound each claim to the inputs the batch executed. Equal failures, skips, setup errors and timeouts prove nothing.

## Review findings

A binding finding is a regression, a net-reducing change, or a change with a real cost probe that the candidate fails and the replacement passes while retained batches pass. Repair it or disprove the alternative with required behavior; each intake reevaluates retained bindings and reuses valid executions. Do not revert edits to manufacture a failure.

After coherent repair and cleanup, follow the [verification step](../repo-production-workflow/SKILL.md#9-verification).
