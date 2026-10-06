---
name: tdd
description: TDD for production behavior changes through recorded original/candidate comparisons at real Seams. Use when changing production behavior or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

The original source is the baseline. The request decides which differences are correct. One direct batch proves both: the workflow runner executes it on the original and the edited source. Probe quality is in [tests.md](tests.md); the runner and the Behavior Map fields are in [recorder.md](recorder.md).

## Required probe loop

Do these steps in order for each coherent production edit. Do not hand the change to a reviewer or advisor before step 6 is done.

1. **Select the direct batch.** Start from the preflight's [Behavior Map](recorder.md#behavior-map). Reuse existing tests, fixtures and assertions. Call the responsible Interface with the source and state its production caller supplies. Do not run bootstrap or workflow orchestration inside a probe. Use real collaborators; read [mocking.md](mocking.md) when you choose the runtime or the failure path for a remote, process, filesystem or concurrent Seam. If the Interface does not exist yet, create it, then drive each guarantee through it; an import failure proves nothing. Done when each map item names its cases in the batch.
2. **Make the edit.** Make the smallest change that meets the request.
3. **Reconcile the actual diff.** Read the diff against the original responsibilities. For each condition, guard, branch, write or return that the edit adds, removes, weakens, strengthens or rewrites, find its decisive contexts with [MC/DC](tests.md#mcdc). Find its value boundaries (empty, one, several; below, at, above a limit). Name each existing test whose asserted output the edit changes. Investigate callees and decisions the runner cannot see. Done when each context and boundary is a named case of the map item owning its Interface (`record tdd-map`), or has an executed case that shows it cannot be reached.
4. **Extend the batch.** Add those cases to the existing direct batch. Each case asserts the exact original result, unless the request names the new result. Use case-local assertions, such as unittest subtests, so that one failure cannot skip later cases. Done when every named case is in the probe.
5. **Compare.** Run the batch through the runner: `workflow.py tdd --behavior-id BM_X -- COMMAND [ARG...]`. Done when the receipt shows every named case on both trees.
6. **Judge and repair.** The receipt's `open` list states each item that is not proved. Judge each difference against the request, not against a preflight label or an advisor approval. Deleting code does not authorize unrelated behavior changes; Production Code owns the [outcome rules](../production-code/SKILL.md#minimum-implementation-decision). Repair the code or the probe, then run the same batch again. Never rewrite an expected value to make it pass. Done when `open` is empty.

Bound each claim to the inputs the batch executed. Equal failures, skips, setup errors and timeouts prove nothing.

## Review findings

When review adds a probe, attach the finding as `sourceRefs`. The runner then also executes the batch on the recorded reviewed tree. Do not revert edits to manufacture a failure. Deleting an owner cannot close a material finding. Repeated occurrences need proof against the latest reviewed source.

After coherent repair and cleanup, follow the [verification step](../repo-production-workflow/SKILL.md#9-verification).
