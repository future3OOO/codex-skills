# Behavior Test Reference

A strong test is a real probe of one **independently-failable observable outcome** through the Interface of the Module that owns it, called with its production caller's setup, or the externally observable state governed by that Interface; a test runner or CI result alone establishes no coverage.

A behavior test survives internal refactoring: if observable behavior is unchanged but the test breaks, the test is coupled to implementation. Several assertions are valid when they jointly prove one behavior; one assertion can still hide an over-broad behavior.

Record direct operations through `workflow.py tdd`. Reuse the project's runner,
fixtures and supported execution matrix; use a focused script where none exists.

Recorded comparison probes need not be committed: immutable `probeTree` files and
support remain the proof when their maintained copies are removed. Removing support
also requires running the affected surviving maintained tests as named cases.

Commit a probe only for regression coverage no existing check has, as the
smallest case calling that owner. Additional checks
require distinct coverage, with defect sensitivity
and no narrowing of the contract; they do not replace production acceptance. Apply
[Production Code's comparison rules](../production-code/SKILL.md#lowest-cost-implementation-decision)
for N/N+1 proof and conditional A/B measurements.

## What the batch must prove

| Case | Proof shape |
|---|---|
| Atomic behavior | One outcome; split outcomes that different defects could break independently. |
| Complete failure contract | Expected error or refusal, the observable state required by the contract, and the correct outward result, exit status, or propagated exception. |
| Touched-Seam preservation | A rerouted public operation retains each material success, failure, input-form, state, and atomicity guarantee the new path can alter. |
| Architecture falsifier | A reachable semantic bypass challenges a load-bearing mechanism or state boundary, not merely its obvious spelling. A passing probe is regression evidence, not a manufactured failure. |
| Interaction slice | One behavior cannot mutate state or invalidate a guarantee owned by another through shared state, lifecycle, ordering, or a touched Seam. |
| Differential | The same inputs decided in both evaluation systems agree, or the divergence is recorded with the system whose rules decide. One input per type class the Seam admits, never only the task's examples. |

## MC/DC

Read the diff: these are the inputs each decision it changes puts in its item's family. One independence pair for each condition is not sufficient.

A **decision** is a Boolean expression that controls a branch, guard, ternary, filter or return. A **condition** is one atomic operand. A repeated condition, one that occurs more than once in a decision, is one condition: vary all its occurrences together.

An **independence pair** is two inputs that change one condition and change the original decision, or the edited decision for a condition the edit adds. The other conditions keep the same values (unique cause), or differ only where they cannot affect the decision (masking). A short-circuited operand is don't-care. Coupled conditions cannot vary independently: use a masking pair, or show the context cannot be reached.

A **decisive context** is a combination of the other values that permits this independent change. Assignments that differ only in masked operands are one context. Contexts that produce different outcomes are different contexts, even when the decision is true in both.

### The family

For every condition your edit adds, removes, weakens, strengthens or changes, the bounds hold both inputs of its independence pair in **EVERY feasible decisive context**. Find the contexts from the original code, and from the edited decision for an added condition. Rewording a condition without changing its decision creates no obligation. Listing every feasible combination of the decision's condition values holds them all; a sample never replaces one of them. Inputs count only when the loop runs them: a list, table or plan of pairs proves nothing, and a missing one leaves the proof incomplete.

- The preservation item's loop holds every such input the request keeps and asserts for each that the edited result **EQUALS** the original result. Do not use subset checks.
- The change item's loop holds the inputs whose result the request names and asserts that exact result. They are no evidence that another context keeps its original behavior.
- Each input starts from the same initial state on both trees and makes the target condition execute. Take its condition values from execution or independently checked fixture facts, never from labels. Keep short-circuit evaluation; do not evaluate an unsafe skipped operand.
- An infeasible context is an input too: it drives the Interface and shows the constraint on both trees, with the reason in the item's `expected`. An unknown context is not infeasible.
- A context value that only the other input of a pair supplies is unverified, and unavailable measurement is not unreachable input: the loop fails for that input and says which observation is missing. A printed warning alone does not.

An input where the original decision flips and the edited one does not is a regression unless the request names that exact change. Fix the code: rewriting the expected value, or the formula that computes it, is the same as changing it. If you believe the request needs the new result, ask the user.

### Example

Request: stop the `OUT_Q` output and keep `OUT_K`. The edit turns `if P and X and (K or Q): emit(OUT_K if K else OUT_Q)` into `if P and K: emit(OUT_K)`, which removes `X` and `Q`. A loop over the combinations of `P`, `X`, `K` and `Q`, decided by the original's result, finds the requested change at `P=T, X=T, K=F, Q=T` (`OUT_Q`, now no output) and a regression at `P=T, X=F, K=T` (no output, now `OUT_K`). That is `X`'s pair in the context `K=T`. A test of the requested change never runs it: `X`'s two contexts produce different outcomes and are not one. The correct code is `if P and X and K: emit(OUT_K)`.

### Values and effects

A changed path that produces values puts its boundaries in the family too: zero, one and several results; below, at and above a limit. Assert the actual contents and effects, and the absence of output where it applies.

The runner checks each command's exit on both sources. It does not prove Interface reach, context completeness or pair independence. The existing advisor and reviewer judge them: they match pairs by **condition AND decisive context**, including contexts for retained outcomes, and TDD does not close with a missing or unverified context or a difference the request does not permit.

## Attribution

The existing reviewer must check that a claimed regression comes from the affected behavior, using the source and raw output. The recorder compares exit codes. It does not classify collection, import, setup or missing-API failures. A failure before the asserted operation does not establish that operation's behavior.

Assert meaningful public outputs and state effects with real collaborators. For a rejected transfer, assert the error, unchanged independently read balances and uncommitted result. Keep every independently falsifiable obligation covered. The runner executes the same assertions on each source version; existing review checks whether those assertions express the original objective.

## Observable state

Verify through the owner's Interface or the externally observable state governed by that Interface. Do not inspect an internal store merely because it is convenient. Direct state inspection is valid when the state itself is a public product artifact, or when the Interface explicitly promises its exact persisted state.

Avoid tests that:

- replace production collaborators with programmed answers;
- assert private methods, call counts, or interior sequencing instead of behavior;
- claim a product regression from a setup or missing-API failure; the existing reviewer judges that premise;
- combine independently-failable outcomes under one broad name;
- prove only the happy path while omitting declared failure or preservation behavior;
- inspect private persistence when the public contract does not expose or govern it.
