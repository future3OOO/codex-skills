# Behavior Test Reference

A strong test proves one **independently-failable observable outcome** through the public Interface or externally observable state governed by that Interface.

A behavior test survives internal refactoring: if observable behavior is unchanged but the test breaks, the test is coupled to implementation. Several assertions are valid when they jointly prove one behavior; one assertion can still hide an over-broad behavior.

## What a slice must prove

| Slice | Proof shape |
|---|---|
| Atomic behavior | One outcome under one relevant precondition; split outcomes that different defects could break independently. |
| Complete failure contract | Expected error or refusal, the observable state required by the contract, and the correct outward result, exit status, or propagated exception. |
| Touched-Seam preservation | A rerouted public operation retains each material success, failure, input-form, state, and atomicity guarantee the new path can alter. |
| Architecture falsifier | A reachable semantic bypass challenges a load-bearing mechanism or state boundary, not merely its obvious spelling. A passing probe is regression evidence, not a manufactured RED. |
| Interaction slice | One behavior cannot mutate state or invalidate a guarantee owned by another through shared state, lifecycle, ordering, or a touched Seam. |
| Differential | The same inputs decided in both evaluation systems agree, or the divergence is recorded with the system whose rules decide. One input per type class the Seam admits, never only the task's examples. |

## A real RED

The RED must reach the mapped Seam and fail with the declared failure for the claimed product behavior: an assertion carrying a behavior-specific marker, or the product's own exception or diagnostic, recorded as `redFailure` in preflight. For directly invoked pytest and unittest, the recorder also requires at least one executed test and refuses collection, setup, loader, or zero-test failures. A non-runner operation opens the RED when it fails carrying the declared failure; its reach is recorded unresolved and review establishes the promise.

A test for “rollback restores exact state” is **not** a RED for rollback when it stops first at `AttributeError: enable_safe_import`; failing earlier is evidence for no item. The first RED of a new Seam asserts the Seam's existence (`assert hasattr(db, "enable_safe_import"), MARKER`); rollback semantics are a separate item driven once the Seam exists.

```python
def test_rejected_transfer_preserves_balances():
    before = balances(account_a, account_b)

    result = transfer(account_a, account_b, amount=-1)

    assert result.error == "invalid amount", "REJECTED_TRANSFER_CONTRACT_BROKEN"
    assert balances(account_a, account_b) == before, "REJECTED_TRANSFER_CONTRACT_BROKEN"
    assert result.committed is False, "REJECTED_TRANSFER_CONTRACT_BROKEN"
```

The error, contract-required balance preservation, and outward result jointly prove one failure behavior, so every assertion carries the same behavior-specific marker: whichever guarantee breaks first, the failure still names the mapped `redFailure`. A jointly-proving assertion without the marker would reach the Seam yet be refused by the recorder. When the guarantees can break independently and deserve independent proof, split them into separately mapped items instead.

## Observable state

Verify through the public Interface or the externally observable state governed by that Interface. Do not inspect an internal store merely because it is convenient. Direct state inspection is valid when the state itself is a public product artifact, or when the Interface explicitly promises its exact persisted state.

Avoid tests that:

- replace production collaborators with programmed answers;
- assert private methods, call counts, or interior sequencing instead of behavior;
- fail because setup, syntax, collection, fixture shape, or a missing API prevents the mapped Seam from being reached;
- combine independently-failable outcomes under one broad name;
- prove only the happy path while omitting declared failure or preservation behavior;
- inspect private persistence when the public contract does not expose or govern it.
