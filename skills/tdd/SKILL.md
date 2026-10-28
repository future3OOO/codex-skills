---
name: tdd
description: TDD for production behavior changes through real Seams. Use when changing production behavior test-first or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

Drive retained attack probes through real production Interfaces and collaborators; assert meaningful results and state effects. Mocks, stubs, fakes and test-only adapters cannot prove behavior. See [probe quality](tests.md) and [execution](recorder.md).

Before implementation, identify the requested behavior and affected preservation from the user's request; the approved preflight records an interpretation. Select and reuse probes under AGENTS.md's Real-Seam proof invariant. If its Interface does not exist yet, implement that Interface and return to exercise the independent guarantees; an import failure proves no later behavior.

## Required probe loop

This is the testing procedure after a coherent production edit, before verification
or review. A passing run of the pre-edit cases does not finish it.

1. **Keep one reusable direct harness.** Reuse the existing fixtures, assertions and
   real collaborators. Construct the responsible Interface with the source and state
   its production caller supplies, without invoking that caller to obtain them.
   Bootstrap and workflow orchestration are not setup inside decision probes. Share
   setup where safe and isolate mutable state.
2. **Turn the edited decisions into probe cases.** For every predicate, term, guard,
   branch, write or return the diff adds, removes, broadens or narrows, add a case
   for each outcome it decides in the original or candidate source, using an input
   that produces that outcome. Put the cases in the existing direct batch and assert
   the original result unless the request changes it; an original failure followed
   by a candidate pass establishes a change, not preservation.
   Use MC/DC (modified condition/decision coverage): an independence pair changes a
   condition and the decision's outcome, holding other conditions fixed (unique-cause)
   or varying only those that cannot affect it (masking); short-circuited operands
   are don't-care. Our extension requires a pair in every decisive context of each
   removed, weakened or rewritten condition: each combination of other conditions
   where flipping it alone flips the original decision, dropping masked conditions.
   Run the pairs on the edited code, separately from requested-change cases.
   Inferred contexts remain unverified; if no caller-supplied input reaches a
   context, explain why in a probe note for the reviewer, which the runner neither
   requires nor records. For produced values, also cover boundary and partition
   cases: empty, one and several for collections; below, at and above for limits.
   Investigate callees and decisions the runner cannot see. Where useful, seed
   combinations of fixture values with one printed result per case, and test
   metamorphic relations for requested new behavior.
3. **Run the expanded batch on both sources.** Use the existing runner below. Every
   case must execute and assert its observable result and relevant state effects on
   both sources. Use case-local assertions, such as unittest subtests, so an expected
   failure in an earlier case cannot skip the remaining cases.
4. **Investigate differences and repair.** Judge differences against the user's
   request, challenging preflight labels and advisor approval when they conflict.
   Challenge an assertion that endorses a regression, repair it
   and the implementation, and rerun the same batch. Keep the distinguishing cases.
   Complete this loop before handing the change to a reviewer or advisor.

The existing runner executes the same current probe and test environment on recorded
original, recorded edited, reviewed and current candidate source trees:

```text
workflow.py tdd --behavior-id BM_CHANGE --behavior-id BM_KEEP -- COMMAND [ARG...]
```

Share one targeted command across its owning behaviors. Inspect the bound outcomes: a failed batch does not mean every behavior failed. Historical assertion failure followed by candidate success demonstrates a change on exercised inputs. Successful checks on both versions establish preservation. Equal failures, skips, setup errors and timeouts leave proof incomplete. The assertions must observe the public results and relevant state effects; equal exit codes alone do not establish either.

Bound coverage to exercised distinguishing inputs. Deleting implementation code does
not authorize unrelated behavior changes; Production Code owns the governing
[outcome rules](../production-code/SKILL.md#minimum-implementation-decision).

When review adds a probe, attach its existing finding source reference and run the same command against the recorded reviewed tree and current repair. Do not revert edits to manufacture a failure. Update affected items by identity when scope changes; deleting an owner cannot close a material finding. Use the existing finding disposition after the runner supplies current proof. Repeated occurrences need proof against the latest reviewed source.

Continue incremental probes while implementing; follow the [verification step](../repo-production-workflow/SKILL.md#9-verification) after coherent repair and cleanup.
