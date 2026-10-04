---
name: tdd
description: TDD for production behavior changes through recorded original/candidate comparisons at real Seams. Use when changing production behavior or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

Drive retained attack probes through real production Interfaces and collaborators; assert meaningful results and state effects. Mocks, stubs, fakes and test-only adapters cannot prove behavior. See [probe quality](tests.md) and [execution](recorder.md).

Before implementation, identify the requested behavior and affected preservation from the user's request; the approved preflight records them as the Behavior Map below. Select and reuse probes under AGENTS.md's Real-Seam proof invariant. If its Interface does not exist yet, implement that Interface and return to exercise the independent guarantees; an import failure proves no later behavior.

## Behavior Map

The recorded preflight owns the initial map; `record tdd-map` changes it by item id. Every item has `id`, `kind`, `basis`, `behavior`, `seam`, `expected`; the optional fields below are what readiness enforces by execution. Readiness is one result: an item is proved only by a current valid comparison whose named cases carry its required outcomes, and `summary`, the `tdd` and `verify` receipts, reviewer dispatch and `complete` all read that same result.

- `kind`: `contract` for a requested change, `preservation` for an invariant the change must keep. A contract item is proved when an attributable case fails on the original and passes on the current source; a preservation item when every attributable case passes on both. A preservation case that fails on the original and passes on the current source is a rewritten expectation: it keeps the item open until the code is repaired or the item becomes a contract item through `tdd-map` with a `basis` quoting, verbatim, one complete sentence of the recorded request that names the new result. The same quoted basis is required to drop a preservation item's named case, and to add, re-word or name cases on a contract item after preflight; a new attack owning a material behavioral finding carries that finding instead. Each such change is listed as a contract change, with its basis or finding, in the receipts, `summary` and review packets for the reviewer and final advisor to judge.
- `boundaryInputs`: the executed case names this item requires (unittest method, pytest id with its parameter, or the `name` of a printed `name: result` line; exact, never a substring). Each must execute with the expected outcome on both trees; a name only mentioned in probe source, a skipped case, or a case missing from a narrowed selection keeps the item open and is named in the question. Use them for required boundary values (empty, one, several; below, at, above a limit), for existing tests whose expectations the edit makes stale, and for MC/DC pairs. Items sharing one batch must each name their cases; a case in the batch that differs between the trees and that no item names stays the preservation items' question.
- `interpretations`: at least two competing readings of the behavior when the request admits them; the item stays open until `interpretation` and `authority` are both recorded.
- `released: {reason, case}`: settles a preservation context that cannot be reached, bound to the probe's printed `name: result` note stating why (a test result cannot bind a release), present unchanged on both trees. A release answers only the context's absent inputs: a named case that differs, or whose printed result or assertion says `unverified`, keeps the item open regardless; a contract item, a finding-owned item or an item with unsettled readings cannot be released; the binding is re-judged on every comparison, and the release is listed in receipts and packets for review.

### MC/DC decisive contexts

A decision is a Boolean expression controlling a branch, guard, ternary, filter or return; a condition is one atomic operand. An independence pair for condition X is two inputs differing in X that flip the original decision with the other conditions fixed (unique-cause) or differing only where they cannot affect it (masking); short-circuited operands are don't-care; coupled conditions cannot vary independently. For every condition the edit removes, weakens or rewrites, add one item per feasible decisive context of the original decision, naming both pair cases in `boundaryInputs` (`X | <context> | a`, `X | <context> | b`): `preservation` when the request keeps that outcome, `contract` with the quoted request sentence when it names the changed outcome. Do not merge contexts that produce distinct outcomes. A pair counts only when both inputs execute through the Interface on both trees; establish condition values from execution or independently checked fixture facts, never from labels. Unavailable measurement is not unreachable input: print the note as `<context>: unverified - <why>` and the item stays open and reported; release only a context the probe shows unreachable. Rewording creates no obligation.

## Required probe loop

This is the testing procedure after a coherent production edit, before verification
or review. A passing run of the pre-edit cases does not finish it.

1. **Keep one reusable direct harness.** Reuse the existing fixtures, assertions and
   real collaborators. Construct the responsible Interface with the source and state
   its production caller supplies, without invoking that caller to obtain them.
   Bootstrap and workflow orchestration are not setup inside decision probes. Share
   setup where safe and isolate mutable state.
2. **Turn the edited decisions into map items.** Read the actual diff against the
   original responsibilities. For every predicate, term, guard, branch, write or return
   it adds, removes, broadens or narrows, record the [decisive contexts](#mcdc-decisive-contexts)
   and value boundaries as items with named cases through `record tdd-map`, and put the
   cases in the existing direct batch, asserting the original result unless the request
   names the change. Name every existing test whose asserted output the edit changes so a
   narrowed batch cannot omit it. Investigate callees and decisions the runner cannot see.
3. **Run the expanded batch on both sources.** Use the existing runner below. Every
   case must execute and assert its observable result and relevant state effects on
   both sources. Use case-local assertions, such as unittest subtests, so an expected
   failure in an earlier case cannot skip the remaining cases.
4. **Answer the open questions.** The receipt lists each item the comparison does not
   yet prove: the differing or missing case, the unsettled reading, the unexecuted pair
   input. Judge differences against the user's request, challenging preflight labels and
   advisor approval when they conflict. Repair the code or the probe and rerun the same
   batch; never rewrite an expected value to pass. Complete this loop before handing the
   change to a reviewer or advisor.

The existing runner executes the same current probe and test environment on the recorded
original and current candidate source trees, the recorded reviewed tree for a finding-owned
item, and an earlier edited tree that failed this batch once more when the probe changed:

```text
workflow.py tdd --behavior-id BM_CHANGE --behavior-id BM_KEEP -- COMMAND [ARG...]
```

Share one targeted command across its owning behaviors. Inspect the bound outcomes: a failed batch does not mean every behavior failed. Equal failures, skips, setup errors and timeouts leave proof incomplete. The assertions must observe the public results and relevant state effects; equal exit codes alone do not establish either.

Bound coverage to exercised distinguishing inputs. Deleting implementation code does
not authorize unrelated behavior changes; Production Code owns the governing
[outcome rules](../production-code/SKILL.md#minimum-implementation-decision).

When review adds a probe, attach its existing finding source reference and run the same command against the recorded reviewed tree and current repair. Do not revert edits to manufacture a failure. Update affected items by identity when scope changes; deleting an owner cannot close a material finding. Use the existing finding disposition after the runner supplies current proof. Repeated occurrences need proof against the latest reviewed source.

Continue incremental probes while implementing; follow the [verification step](../repo-production-workflow/SKILL.md#9-verification) after coherent repair and cleanup.
