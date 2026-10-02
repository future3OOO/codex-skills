# Behavior Test Reference

A strong test is a real probe of one **independently-failable observable outcome** through the production Interface or externally observable state governed by that Interface. Use the CLI, application operation or public call that exercises the claimed behavior; a test runner or CI result alone establishes no coverage.

A behavior test survives internal refactoring: if observable behavior is unchanged but the test breaks, the test is coupled to implementation. Several assertions are valid when they jointly prove one behavior; one assertion can still hide an over-broad behavior.

Comparison probes need not be committed. Keep uncommitted probes runnable
in a Git-ignored path in the task worktree until pass completion.
A direct-operation probe must print or assert its outcome; exit 0 alone
proves nothing.

Commit a probe only for regression coverage no existing check has, as the
smallest case in an existing harness. Additional checks
require distinct coverage, with defect sensitivity
and no narrowing of the contract; they do not replace production acceptance. Apply
[Production Code's comparison rules](../production-code/SKILL.md#minimum-implementation-decision)
for N/N+1 proof and conditional A/B measurements.

## What a slice must prove

| Slice | Proof shape |
|---|---|
| Atomic behavior | One outcome under one relevant precondition; split outcomes that different defects could break independently. |
| Complete failure contract | Expected error or refusal, the observable state required by the contract, and the correct outward result, exit status, or propagated exception. |
| Touched-Seam preservation | A rerouted public operation retains each material success, failure, input-form, state, and atomicity guarantee the new path can alter. |
| Architecture falsifier | A reachable semantic bypass challenges a load-bearing mechanism or state boundary, not merely its obvious spelling. A passing probe is regression evidence, not a manufactured failure. |
| Interaction slice | One behavior cannot mutate state or invalidate a guarantee owned by another through shared state, lifecycle, ordering, or a touched Seam. |
| Differential | The same inputs decided in both evaluation systems agree, or the divergence is recorded with the system whose rules decide. One input per type class the Seam admits, never only the task's examples. |

## Attribution

A historical failure must reach the claimed behavior. Collection, imports, setup errors and zero tests cannot demonstrate a product regression. A rollback probe stopping at a missing API proves no rollback behavior; create the Interface and exercise its guarantees independently.

Assert meaningful public outputs and state effects with real collaborators. For a rejected transfer, assert the error, unchanged independently read balances and uncommitted result. Keep every independently falsifiable obligation covered. The runner executes the same assertions on each source version; existing review checks whether those assertions express the original objective.

## Observable state

Verify through the public Interface or the externally observable state governed by that Interface. Do not inspect an internal store merely because it is convenient. Direct state inspection is valid when the state itself is a public product artifact, or when the Interface explicitly promises its exact persisted state.

Avoid tests that:

- replace production collaborators with programmed answers;
- assert private methods, call counts, or interior sequencing instead of behavior;
- fail because setup, syntax, collection, fixture shape, or a missing API prevents the mapped Seam from being reached;
- combine independently-failable outcomes under one broad name;
- prove only the happy path while omitting declared failure or preservation behavior;
- inspect private persistence when the public contract does not expose or govern it.
