# Plan Template

```md
# <Title>

## Status

- current state:
- governing artifact:
- last updated:

## Objective

- primary goal:
- success condition:

## Source Of Truth

- authority:
- trusted base:
- linked evidence:

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

## Scope In

- ...

## Scope Out

- ...

## Authority And Conflict Rule

- authorities:
- escalation path:

## Delivery Map

- plan type:
- PR count:
- stack depth:
- regroup rule:

## PR Plan

| PR | Branch | Base | Owner Slice | Commit Structure | Verification | Entry | Exit |
|---|---|---|---|---|---|---|---|
| A | `codex/...` | `main` | ... | ... | ... | ... | ... |

## Verification Plan

- targeted tests:
- combined workflow proof:
- focused invariant checks:
- full gate:
- post-merge checks:

## Execution Checklist

- [ ] planning artifact created
- [ ] authority verified
- [ ] PR-A
- [ ] PR-B
- [ ] PR-C
- [ ] final classification complete

## Linked Review Artifacts

- ...

## Change Log

- YYYY-MM-DD: created plan
```
