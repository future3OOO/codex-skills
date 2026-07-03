---
name: execution-planning
description: Create and maintain tracked execution plans and remediation maps for non-trivial workspace-app work. Use when Codex needs to plan multi-step implementation, PR ordering, branch strategy, recovery or consolidation, or review remediation in this repo. Save the governing artifact on disk under docs/plans or docs/reviews, define source of truth, scope, PR ownership and order, verification gates, and a checklist that implementation agents keep updated as work progresses.
---

# Execution Planning

Use this skill for non-trivial planning in this repo.

This skill does not replace [AGENTS.md](../../../AGENTS.md).
It turns planning and remediation into tracked execution artifacts on disk.

## Mandatory Workflow Position

For new non-trivial planning work, use this order:

1. `$delivery-governance` when that global skill is available in the current Codex environment
2. `$execution-planning`

If `$delivery-governance` is unavailable, start at `$execution-planning` and keep the governing artifact self-contained inside this repo.

If the work is already governed by [$repo-large-implementation](../repo-large-implementation/SKILL.md), use this skill as the tracked-artifact step inside that workflow.

For later implementation against an existing tracked artifact, do **not** tell the execution agent to invoke `$execution-planning` again. The execution workflow should be:

1. [$repo-large-implementation](../repo-large-implementation/SKILL.md) when the execution pass is still non-trivial under that skill's scope; do not re-plan when the tracked artifact already exists
2. [$production-preflight](../production-preflight/SKILL.md)
3. [$production-code](../production-code/SKILL.md)
4. `$github:gh-address-comments` when review-thread state matters and the GitHub plugin provides it

If the execution pass is narrow enough that [$repo-large-implementation](../repo-large-implementation/SKILL.md) no longer applies, say so explicitly in the handoff instead of silently omitting it.

If that plugin skill is unavailable, inspect review-thread state with the repo's GitHub tooling or `gh api graphql` directly instead of blocking on a machine-local path.

The plan owns execution. The execution agent follows it and updates its checklist; it does not replace it.

## Core Rule

Do not leave the governing plan only in chat.

Before tracked implementation starts, save the governing Markdown artifact on disk and make it the execution source for later agents.

## Artifact Selection

Create exactly one primary artifact unless the work clearly needs both:

- `docs/plans/<slug>-YYYY-MM-DD.md`
  - use for implementation plans, recovery programs, consolidation plans, PR programs, and delivery maps
- `docs/reviews/<slug>-YYYY-MM-DD.md`
  - use for remediation maps, review triage, audit notes, thread classification, and review-driven follow-up programs

If both are needed:

- create both
- cross-link them
- make it explicit which one governs implementation order

## Required Planning Workflow

### 1. Choose the authority set

Name:

- source of truth
- trusted base branch
- branch or PR references
- conflict rule

Use repo authorities explicitly when relevant:

- [AGENTS.md](../../../AGENTS.md)
- [docs/specs/appmanagedtask-implementation-spec.md](../../../docs/specs/appmanagedtask-implementation-spec.md)
- [DECISIONS.md](../../../DECISIONS.md)
- any pinned donor, production, or review evidence document

### 2. Define scope before structure

State:

- scope in
- scope out
- known non-goals
- whether the work is implementation, recovery, consolidation, remediation, or review-only

If scope cannot say what is excluded, it is too loose.

### 3. Define the delivery map

Name:

- PR count
- PR order
- branch names
- owner slice per PR
- commit structure per PR
- estimated changed-code-line budget per PR, measured as additions plus deletions in human-authored source files and excluding generated files, lockfiles, vendored code, and pure docs
- scope breaker or regroup rule

If the plan cannot answer “which PR owns this behavior,” it is not ready.

If any PR slice is likely to exceed 1,000 changed code lines, record the reason and shrink it where practical. If any PR slice is likely to exceed 1,300 changed code lines, split it before implementation or record explicit user approval for the exception.

If the work updates an existing PR, also name:

- PR number
- branch name
- exact checkout path the execution agent must edit
- trusted base branch

And require this execution rule:

- before tracked edits, fetch the PR branch and realign that exact checkout to the live PR head SHA
- do not edit from a stale detached review worktree

### 4. Define verification

List:

- targeted tests per PR or per remediation pass
- full gate commands
- merge readiness conditions
- post-merge or follow-up classification work when required

### 4a. Map the affected surface for all code work

Every code-governing artifact must define the real affected surface, not just the local diff.

At minimum, name:

- changed boundary or behavior
- adjacent consumers or dependents that must remain correct
- upstream triggers or callers when relevant
- no-change surfaces that could regress and therefore need proof

Use practical repo evidence such as entrypoints, direct imports/callers, proving tests, and cheap co-change history when needed to map this surface. Do not rely on memory or the cited comment alone.

Keep this proportional for ordinary work.

### 4b. Map the affected transaction system when the work is transaction-sensitive

When the work changes claim tokens, leases, compare-and-set/version fields, transition helpers, or replay/finalize/recovery semantics, the artifact must define:

- authoritative records mutated together
- the real mutation boundary or transaction entrypoint
- adjacent interleavings that can cross that boundary after prepare but before finalize
- projection, replay, recovery, and no-op paths that share helpers or state fields
- helper semantic splits where one helper would otherwise serve two different contracts
- `authoritativeContract` that states the rule or rules that must remain true
- `invariants` that prove the contract across adjacent no-change paths
- `proofPlan` that names the combined workflow proof and the focused invariant checks

Do not let the artifact stop at the cited review comment, the local edited file, or a surface map without an explicit contract and proof model.

### 5. Add tracked execution state

Every plan or remediation map must include an execution checklist that later agents can update.

Use these status markers:

- `[ ]` not started
- `[~]` in progress
- `[x]` complete
- `[!]` blocked
- `[-]` intentionally dropped or superseded

Completed headings may be visually struck through if useful, but the checkbox state is the canonical status marker.

If the work targets an existing PR branch, the checklist must also treat publish state as part of completion:

- code changes are committed
- commits are pushed to the PR branch
- only then are review threads resolved as fixed

Local uncommitted or unpushed changes do not count as completed remediation.

### 6. Critique the draft before finalizing

Challenge the plan before calling it ready.

Before finalizing the artifact, run a critique pass that checks at minimum:

- authority model and conflict handling
- scope in / scope out clarity
- PR count and PR order
- owner-slice boundaries
- verification completeness
- whether the implementation prompt incorrectly tells the execution agent to re-plan

Integrate real critique findings into the artifact before finalizing it.

## Minimum Readiness Rule

Do not call the plan ready unless it names all of the following:

- objective
- source of truth
- scope in
- scope out
- trusted base
- PR ownership
- PR order
- commit structure
- changed-code-line budget per PR, with no unapproved slice above 1,300 changed code lines
- verification commands
- regroup or consolidation rule

For all code work, also require:

- affected surface
- no-change proof surfaces

For transaction-sensitive work, also require:

- authoritative records
- mutation boundary
- adjacent interleavings
- projection/recovery/no-op paths
- authoritativeContract
- invariants
- proofPlan
- combined workflow proof

## Update Discipline

When a tracked plan or remediation map governs execution, later implementation agents must keep it honest.

Update the artifact when execution state materially changes, including:

- checklist status
- current active branch or PR
- changed execution order
- superseded items
- decision links
- remaining blockers

Do not leave a stale governing document behind while implementation moves elsewhere.

## Required Handoff Prompt

After saving the governing artifact, always provide a compact copy-paste prompt for the execution agent.

Rules for that prompt:

- point to the governing artifact on disk
- say explicitly: do not create a new plan
- say explicitly: do not re-plan this pass
- when the work targets an existing PR, say explicitly: fetch the branch and realign the exact checkout to the live PR head before the first tracked edit
- when the work targets an existing PR, say explicitly: commit changes, push the branch, and do not resolve review threads as fixed until the relevant commit is pushed
- say explicitly: keep the PR below the 1,300 changed-code-line budget unless the user approved a concrete exception
- say explicitly: re-walk the real affected surface before edits and again before calling the pass complete
- when the artifact marks transaction-sensitive work, say explicitly: re-walk the full affected transaction system before edits and again before calling the pass complete
- name only the execution skills:
  - [$repo-large-implementation](../repo-large-implementation/SKILL.md) when the pass is non-trivial; use it as the governing wrapper, not as a prompt to re-plan
  - [$production-preflight](../production-preflight/SKILL.md)
  - [$production-code](../production-code/SKILL.md)
  - `$github:gh-address-comments` when review state matters and the GitHub plugin provides it
- keep the prompt compact; the governing artifact already holds the detailed plan
- instruct the execution agent to keep the artifact checklist current
- do not duplicate the entire plan into the prompt unless the artifact is unavailable

## Output Shape

Use one of the reference templates as the starting point:

- [references/plan-template.md](references/plan-template.md)
- [references/remediation-map-template.md](references/remediation-map-template.md)

Keep the final artifact lean. Do not fill sections with boilerplate.

## Repo-Specific Rules

- Use WSL-native paths and tools as authoritative for this repo.
- Prefer tracked Markdown artifacts in the repo over chat-only plans.
- Use `docs/plans/` and `docs/reviews/` consistently; do not invent new top-level planning folders.
- Keep the artifact title and filename aligned.
- If recovery or donor reconciliation is involved, record pinned SHAs and evidence paths explicitly.
- If review comments drive the work, classify them as real, stale, no-change, or deferred instead of blindly converting comments into tasks.

## Completion Rule

This skill is complete only when:

- the governing artifact exists on disk
- the artifact has the required structure
- PR ownership and order are explicit
- verification is explicit
- the execution checklist exists
- later agents could use the document without reconstructing the plan from chat
