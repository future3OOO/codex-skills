# Remediation Map Template

```md
# <Title>

## Status

- current state:
- governing artifact:
- last updated:

## Source Of Truth

- authority:
- trusted base:
- linked evidence:

## Authority And Conflict Rule

- authorities:
- escalation path:

## Current Remote State

- base branch:
- active PRs:
- merge state:

## Execution Order

1. PR / branch one
2. PR / branch two

## Current PR Or Branch Scope

- branch:
- PR:
- scope in:
- scope out:

## Affected Surface

- changed boundary or behavior:
- adjacent consumers/callers:
- no-change surfaces:

## Affected Transaction System

Use only for transaction-sensitive work.

- authoritative records:
- mutation boundary:
- adjacent interleavings:
- projection/recovery/no-op paths:
- helper semantic splits:

## Contract And Proof Model

Use for all non-trivial code work.
For transaction-sensitive work, keep this section explicit.

- authoritativeContract:
- invariants:
- proofPlan:

## Must-Fix Defects

- file:
  - issue:
  - thread:
  - guidance:

## Verify-First / No-Change Threads

- file:
  - thread:
  - rationale:

## Deferred / Decision-Sensitive Items

- file:
  - reason:
  - authority link:

## Required Tests

- targeted:
- combined workflow proof:
- focused invariant checks:
- full gate:

## Next Pass Checklist

- [ ] verify trusted base and exact implementation checkout
- [ ] re-sweep live threads
- [ ] fix must-fix defects
- [ ] resolve stale threads
- [ ] rerun gates
- [ ] commit and push the branch
- [ ] resolve threads only after push

## Resolved / Stale Threads

- ...
```
