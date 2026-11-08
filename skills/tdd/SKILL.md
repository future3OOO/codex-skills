---
name: tdd
description: TDD for production behavior changes through recorded original/candidate comparisons at real Seams. Use when changing production behavior or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

The original source is the baseline. The request decides which differences are correct. One direct batch proves both: the workflow runner executes it on the original and the edited source. Probe quality is in [tests.md](tests.md); the runner and the Behavior Map fields are in [recorder.md](recorder.md).

## Required probe loop

Do these steps in order for each coherent production edit. Prove named cases during TDD; the existing review checks completeness against the original and request.

1. **Establish the original's responsibilities.** Start from the preflight's [Behavior Map](recorder.md#behavior-map) and the deciding original source. Trace every consumer of a producer or guard the edit will remove or rewrite. Find the affected decisions' decisive contexts with [MC/DC](tests.md#mcdc) and value boundaries (empty, one, several; below, at, above a limit). A plan's sole-use claim is not evidence. A decision the request keeps needs its own case.
2. **Record the baseline before editing production.** Reuse existing tests, fixtures and assertions in one direct batch; call the responsible Interface with real production inputs and collaborators. Each case asserts the original's observed result unless the request specifies a change. Name its cases in the owning map item (`record tdd-map`), then run `workflow.py tdd --behavior-id BM_X -- COMMAND [ARG...]`. Requested failures are expected here: continue implementing, not weakening checks. Passed preservation cases are retained even when the batch is red. If the Interface is new, create it, then drive its guarantees; an import failure proves nothing. Use [mocking.md](mocking.md) for runtime and failure-path selection; bootstrap is not the project's test Interface.
3. **Implement and compare.** Implement the best justified architecture and remove what it does not need. Run the same batch; the runner reuses the unchanged original execution. Restore unrequested differences or repair their owning design.
4. **Reconcile the actual diff.** Check every changed condition, branch, write and return against the original responsibilities, including callees. Add genuinely uncovered decisive contexts and boundaries to the same batch and owning item. Use case-local assertions so one failure cannot skip later cases. Supply the command when capturing edited probes. Done when the receipt shows every named case on both trees and the requested differences only.
5. **Judge and continue.** No surviving candidate reads does not mean an original producer had no other job. A passing case proves its observed behavior, not a whole hunk. The lead and existing reviewer judge coverage and authority. Never weaken frozen expectations; retained cases survive a narrowed current map. After final review starts, continue with that final advisor.


Bound each claim to the inputs the batch executed. Equal failures, skips, setup errors and timeouts prove nothing.

## Review findings

A binding finding is a regression, a net-reducing change, or a change with a real cost probe that the candidate fails and the replacement passes while retained batches pass. Repair it or disprove the alternative with required behavior; each intake reevaluates retained bindings and reuses valid executions. Do not revert edits to manufacture a failure.

After coherent repair and cleanup, follow the [verification step](../repo-production-workflow/SKILL.md#9-verification).
