# Canonical Transaction-Sensitive Doctrine

This file is the single owner of transaction-sensitive workflow doctrine. Skills may add role-specific fields or consequences, but must not restate or weaken this contract.

Treat work as transaction-sensitive when it changes claim or lease fields, compare-and-set/version preconditions, mutation entrypoints or transition helpers, or replay, projection, finalize, recovery, and no-op paths that share mutation state or helpers.

Before edits and again before completion:

1. Name the authoritative records that must remain consistent together.
2. Name the real mutation boundary where authoritative state is revalidated.
3. Re-walk adjacent interleavings that can cross that boundary after prepare but before finalize.
4. Re-walk projection, replay, recovery, stale-secondary, and no-op paths sharing fields or helpers.
5. Split helpers whose mutation and projection/recovery contracts differ. Shared syntax is not shared semantics.
6. State the authoritative contract and invariants across the full surrounding surface, not only the cited file or review comment.
7. Require one combined workflow proof plus focused invariant checks that cross an adjacent dependency or state boundary unless the changed behavior is genuinely pure.
8. Prove stale or secondary execution cannot reach the real external mutation boundary.

Resolved review threads, local branch-only tests, and a green isolated helper test are not sufficient proof of this contract.
