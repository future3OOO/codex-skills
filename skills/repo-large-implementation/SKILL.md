---
name: repo-large-implementation
description: Govern all non-trivial planning and large implementations in this repo by pairing $delivery-governance with $execution-planning for tracked plans and $repo-production-workflow for execution passes. Use whenever Codex is asked to create a plan, roadmap, commit structure, PR structure, remediation map, or multi-step implementation in this repo, especially when work spans multiple subsystems, is likely to require more than one PR, touches core runtime or state transitions, or when an existing PR stack is drifting and needs a controlled recovery plan before more code lands.
---

# Repo Large Implementation

Use this skill for any non-trivial plan or implementation in this repo.

This skill does not replace [AGENTS.md](../../../AGENTS.md).
It enforces the workflow order that large work must follow in this repo.

## Mandatory Skill Order

For qualifying work, use the following order:

1. `$delivery-governance` when that global skill is available in the current Codex environment
2. [$execution-planning](../execution-planning/SKILL.md) to create or refresh the tracked governing artifact under `docs/plans/` or `docs/reviews/` when the work is new and non-trivial
3. [$repo-production-workflow](../repo-production-workflow/SKILL.md) for each implementation, refactor, bug-fix, or review-comment execution pass

If `$delivery-governance` is unavailable, start at [$execution-planning](../execution-planning/SKILL.md) and keep the governing artifact self-contained inside this repo.

If a governing artifact already exists for the current work, do not rerun [$execution-planning](../execution-planning/SKILL.md) for an execution-only pass. Execute against the existing artifact through [$repo-production-workflow](../repo-production-workflow/SKILL.md) and keep it current instead.

Do not skip the planning step for new non-trivial work and jump straight into tracked edits.

## When This Skill Is Required

Use this skill when any of the following are true:

- the user asks for a plan, roadmap, branch strategy, commit strategy, or PR structure
- the work spans multiple subsystems
- the work is likely to require more than one PR
- the work touches core runtime, state transitions, auth, ingress, queues, or public contract surfaces
- the work changes claim tokens, leases, compare-and-set/version fields, transition helpers, or replay/finalize/recovery semantics on critical state
- the existing branch stack is drifting, inherited CI failures are spreading, or donor and review branches have diverged

## Affected Surface Rule

All code changes must re-walk the real affected surface before edits and again before completion.

That means:

- identify the actual boundary being changed, not just the edited file
- identify adjacent consumers, upstream triggers, and no-change surfaces that could regress
- require proof that matches that real surface instead of the local branch symptom

For ordinary local work, keep this proportional and short.
For stateful or control-loop work, use the full transaction-sensitive form below.

## Transaction-Sensitive Work

Treat the work as transaction-sensitive when it changes any mutation boundary such as:

- claim or lease fields
- compare-and-set or version preconditions
- transition helpers or transaction entrypoints
- replay, finalize, recovery, or projection behavior that reuses mutation helpers

For transaction-sensitive work, the governing artifact must define:

- authoritative records mutated together
- the real mutation boundary, not just the cited file
- adjacent interleavings that must be re-walked
- projection, replay, recovery, and no-op paths that share the same helpers or fields
- one combined workflow proof plus focused invariant checks

Do not accept a review-local remediation plan that only mirrors the named comment.

## Repo-Specific Rules

- WSL-native paths and tools are authoritative for this repo.
- For new non-trivial plans or remediation programs, create the governing artifact in the tracked format defined by [$execution-planning](../execution-planning/SKILL.md).
- The tracked format means an on-disk Markdown artifact under `docs/plans/` or `docs/reviews/` that includes explicit PR ownership, PR order, verification, and an execution checklist.
- If such a governing artifact already exists for the current work, treat it as binding for execution and keep it honest rather than creating a second competing plan in chat.
- Keep the root repo read-only when running a multi-worktree recovery or reconciliation program.
- Use one untouched donor/reference worktree when comparing against production or another reviewed tip.
- Do not use the local `codex/hetzner-deploy-baseline` branch alias as a trusted base after it has drifted. Use the remote branch as the reference source.
- For existing-PR remediation, use one explicit implementation checkout and realign that exact checkout to the live PR head before editing.
- Detached review worktrees are for inspection only unless they are deliberately realigned and used as the implementation checkout.
- Keep active dependent stack depth as low as possible. If the plan would create more than `3` active dependent PRs, stop and regroup.
- Keep each PR below `1,300` changed code lines, measured as additions plus deletions in human-authored source files and excluding generated files, lockfiles, vendored code, and pure docs.
- Treat `800` changed code lines as the normal target and `1,000` changed code lines as a warning threshold that requires shrinking or explicit written justification in the governing artifact.
- If a planned or active PR exceeds `1,300` changed code lines, stop adding scope and either split by integration boundary, remove nonessential changes, or ask the user for explicit approval to exceed the budget for a concrete reason.
- When drift appears, preserve behavior, not branch archaeology.

## Required Planning Output

Before tracked edits begin, the governing artifact must define:

- objective and scope
- trusted base branch or reference source
- target branch
- commit structure
- PR structure and ownership map
- estimated changed-code-line budget per PR, including a split plan for any slice likely to exceed `1,300` changed code lines
- verification gates per PR
- active branch order
- stack depth limit
- consolidation trigger
- deploy freeze rule if production or donor reconciliation is involved

If the governing artifact cannot say which PR owns a behavior, it is not ready.

If the work is new and non-trivial, do not stop at a chat summary. Save the artifact on disk in the tracked format first.

## Rebase And Merge Discipline

- Rebase only the next active branch after a lower-branch fix.
- Do not rebase the entire world after every cleanup.
- Use `git push --force-with-lease` only.
- Do not begin the next real owner branch until the current one is pushed and its targeted verification passes.
- If the execution model shifts from stacked recovery to consolidation, freeze the old stack and stop editing it.

## Completion Rule

A large implementation is not complete until:

- the governing artifact exists and was followed
- the owning PR is green under the repo quality gate
- the owning PR is under the `1,300` changed-code-line budget, or the user explicitly approved a justified exception before the oversize work continued
- coupled tests/docs/contracts moved with the code
- drift or supersession status is recorded honestly
