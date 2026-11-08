---
name: tdd
description: TDD for production behavior changes through recorded original/candidate comparisons at real Seams. Use when changing production behavior or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

The original source is the baseline. The request decides which differences are correct. One direct batch proves both: the workflow runner executes it on the original and the edited source. Probe quality is in [tests.md](tests.md); the runner and the Behavior Map fields are in [recorder.md](recorder.md).

## Required probe loop

Do these steps in order for each coherent production edit. Submit the whole focused batch during TDD; the existing review checks completeness against the original and request.

1. **Establish the original's responsibilities.** Start from the preflight's [Behavior Map](recorder.md#behavior-map) and the deciding original source. Trace every consumer of a producer or guard the edit will remove or rewrite. Find the affected decisions' decisive contexts with [MC/DC](tests.md#mcdc) and value boundaries (empty, one, several; below, at, above a limit). A plan's sole-use claim is not evidence. A decision the request keeps needs its own case.
2. **Record the baseline before editing production.** Reuse relevant cases and assertions through the responsible Interface with real inputs and collaborators. Submit the focused cases together under their owning map items; runtime help owns command forms. Reuse the original defect's failure as sensitivity proof; cases that pass on both sources need a behavior-breaking diff through the runtime change input. Requested failures are expected here: implement the requirement without weakening the check. Use [mocking.md](mocking.md) for runtime and failure-path selection.
3. **Implement and compare.** Implement the best justified architecture and remove what it does not need. Re-run the case commands; unchanged original executions are cached. Restore unrequested effects or repair their shared cause.
4. **Reconcile the actual diff.** Check changed conditions, branches, writes and returns against the original responsibilities and callees. Add commands for genuinely uncovered contexts. Each probe asserts its outcome and fails with a non-zero exit. Supply the command when capturing edited probes. Inspect the receipt and raw output; printed values do not establish an assertion.
5. **Judge and continue.** No surviving candidate reads does not mean an original producer had no other job. A passing case proves its observed behavior, not a whole hunk. The lead and existing reviewer judge coverage and authority. Never weaken frozen expectations; retained cases survive a narrowed current map. After final review starts, continue with that final advisor.


Bound claims to executed inputs. Equal failures establish no success. The existing reviewer must judge failure cause and coverage; exit codes do not prove that the Interface ran. Timeouts are not completed operations.

## Review findings

Follow the runner's finding receipt. Verify its premise against required behavior;
repair it or demonstrate why its proposed alternative is invalid. Reuse valid
executions. Do not revert edits to manufacture a failure.

After coherent repair and cleanup, follow the [verification step](../repo-production-workflow/SKILL.md#9-verification).
