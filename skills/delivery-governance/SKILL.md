---
name: delivery-governance
description: Govern large-implementation delivery — commit structure, PR structure, the review budget, stack depth, and consolidation to avoid rebase debt. Use when scoping a major feature or refactor, splitting work into commits/branches/PRs, or recovering drifted stacked branches.
---

# Delivery Governance

## Core Rule

Design delivery before coding. Group work by integration boundary and mergeability, not by abstract thought units, tiny fixes, or reviewer-comment wording.

## Planning Default

If the user asks for a non-trivial plan, branch strategy, commit strategy, PR split, or implementation roadmap, use this skill before proposing the plan.
Do not output a large implementation plan that lacks commit and PR structure.

## Workflow

1. Build a delivery map before the first tracked edit.
   - List the subsystems or behaviors that can merge independently.
   - Mark each as one of: foundation, runtime, operator UX, proof/tests/docs, cleanup.
   - Choose the smallest set of PRs that can land coherently.
   - Decide the commit strategy and the PR ownership map before coding starts.
2. Choose PR shape by coupling.
   - Keep code that must change together in one PR.
   - Split only when a branch can be reviewed, tested, and merged independently.
   - Prefer subsystem and behavior boundaries over micro-fixes.
   - For stateful or contract-sensitive work, split by contract ownership and integration boundary, not reviewer comment wording.
3. Keep stacks short.
   - Default to 3 to 5 dependent PRs maximum.
   - If a plan needs more than 5 dependent PRs, regroup into broader mergeable slices before coding.
   - Do not create a PR per review comment or per tiny fix in the same subsystem.
4. Merge runtime and verification where appropriate.
   - Keep tests, docs, runbooks, and proof tools with the runtime change unless they are genuinely independent.
   - Do not split proof or docs into separate branches just to make the stack look neat.
5. Apply production-quality workflow on every active branch.
   - Use the production-preflight and production-code skills when the work warrants them.
   - Otherwise produce an explicit preflight before tracked edits and run the repo's lint, typecheck, unit, integration, build, and quality gates before calling work green.

## Default Branch Plan For New Projects

Use this unless there is a strong reason not to:

1. Foundations: config, types, schemas, state transitions, shared clients.
2. Core runtime: control loops, persistence, service logic.
3. User-facing behavior: actions, UI, APIs, external contracts.
4. Proof/tests/docs: integration coverage, CLIs, runbooks, release evidence.
5. Release hardening or consolidation: only if needed.

## Hard Limits

- Max 5 active dependent PRs.
- Max 2 branch classes mixed in one PR.
- The **review budget**: target ~500 net lines of code per PR, measured as additions minus deletions in human-authored source files and excluding generated files, lockfiles, vendored code, and pure docs. This is the canonical definition other skills reference.
- Treat a planned slice tracking materially above the target as a preflight warning that requires shrinking or a written justification in the governing artifact.
- The **split threshold** is 1,000 net lines: a planned PR likely to exceed it must be split by integration boundary or descoped before coding, absent explicit user approval for a concrete exception. Do not rely on review to catch oversize after the branch is built.
- If the same file appears in 4 or more open branches, stop and regroup.
- If a lower branch needs a second significant rewrite after reviews start, stop stacking upward.

## Consolidation Triggers

Stop opening more stacked PRs and switch to a consolidation PR when any of these happen:

- the deploy or donor branch gets ahead of the reviewed stack
- lower-branch fixes repeatedly force upstream rebases
- inherited CI failures appear across 3 or more PRs
- the same behavior is being "fixed" in multiple branches
- reviewers can no longer tell where ownership lives
- rebase time starts exceeding implementation time

## Rebase Policy

- Rebase the next active branch after a lower-branch fix.
- Do not eagerly rebase the entire remaining stack for trivial cleanups.
- Keep the remote stack honest for branches you are actively editing.
- Use `git push --force-with-lease` only for rebased branches.

## Recovery Workflow For A Messy Stack

1. Freeze deploys from donor or partially restacked branches.
2. Identify the lowest owner branch for each still-real defect.
3. Finish only the active owner branch.
4. Rebase only the next active branch.
5. When the active queue is stable, consolidate remaining donor-only or cross-cutting work into 1 or 2 PRs.
6. Supersede stale stacked PRs instead of extending the chain forever.

## Anti-Patterns

- one PR per tiny conceptual change in tightly coupled code
- using stacked PRs as the long-term delivery vehicle for dozens of branches
- pushing donor or production fixes outside the reviewed stack without reconciliation
- splitting runtime and its required tests or docs too early
- treating reviewer comments as branch boundaries
- creating a separate proof or acceptance-fixture branch when the owning workflow surface can carry the proof directly

## Required Output

When using this skill, produce:

- a delivery map with branch or PR ownership
- a commit structure for each branch or PR slice
- an estimated net-line budget for each PR slice, with any slice above the review-budget target justified and any slice above the split threshold rejected or split
- the target stack depth and consolidation rule
- the active-branch order
- verification gates per branch
- a deploy freeze rule if a stack already exists
- an explicit stop condition for when to consolidate instead of stacking further
