---
name: repo-production-workflow
description: Orchestrate production-grade repo code changes by running Repo Context Forge first, then GitNexus packet checks, production-preflight for preflight-worthy edits, and production-code before and during repository file changes through final verification. Use for implementation, bug fixes, refactors, review-comment fixes, and repo code changes that should be minimal, verified, and fail-closed.
---

# Repo Production Workflow

Use this skill for production code changes inside a git repository.
It is an orchestration skill; it does not replace the referenced skills.

## Mandatory Order

1. Use FFF MCP tools as the first raw discovery layer for files, symbols, text, broad exploration, and multi-pattern search.
   - Use Bash `rg` only when FFF is unavailable, its transport fails, or the task requires exhaustive/machine-readable output.
   - Do not use `grep` or `find` for repository search unless both FFF and `rg` are unavailable or the task specifically requires them.
2. Run `$repo-context-forge` before choosing files, GitNexus queries, review findings, or edits.
   - If the user described planned work before files changed, pass the request as `--intent`.
   - If Repo Context Forge emits blockers, stop and surface them.
   - Use packet targets and coverage plan as the first-pass surface.
3. State the task contract from the user request and packet surface.
   - Name the behavior being changed.
   - Name skipped high-ranked targets and why they are out of scope.
   - Estimate whether the implementation can stay below 1,300 changed code lines; if not, escalate to `$repo-large-implementation` before editing.
4. Run the packet-listed GitNexus required checks.
   - Use the packet repo value and state it as the GitNexus authority for this packet.
   - Do not let broader GitNexus output shrink the packet surface.
   - Run impact analysis before editing indexed symbols.
   - If a required GitNexus MCP tool is not currently loaded, run tool discovery for that exact capability before reporting it unavailable or falling back.
5. Use `$claude-advisor` after Repo Context Forge and the packet-listed
   GitNexus required checks.
   - Claude is read-only and advisory only.
   - Ask one focused question about the packet surface, reviewer finding, slice
     ownership, no-change surfaces, ordering/idempotency risk, or minimal
     production-safe approach.
   - Claude must remind Codex to use `$tdd` before implementation and should
     say whether `$improve-codebase-architecture` is needed for a targeted
     Module/Interface/Seam decision before editing.
6. Apply `$production-preflight` before the first tracked edit when the work matches its trigger.
   - Anchor its proof to the Repo Context Forge packet plus GitNexus checks.
   - If `$diagnose` ran, carry its Surface Map B into preflight as the affected-surface input; do not replace it with a file-only scope.
   - If skipped for a trivial change, record the skip reason before editing.
   - If preflight has blocking questions, stop before editing.
7. Invoke `$production-code` before writing any repository file content, then implement under it.
   - Choose the smallest production-safe implementation path before editing.
   - This includes tracked files, untracked files, scratch implementation files, generated source, and new worktrees.
   - Make the smallest direct change.
   - Reuse existing paths before adding helpers or abstractions.
   - Keep behavior fail-closed and boundary-validated.
   - Do not clean up unrelated code.
8. Verify under `$production-code` against the affected surface.
   - Run focused tests and the repo gates relevant to touched areas.
   - Run the bundled production-code gate before finalizing the implementation.
   - Re-check no-change surfaces named in preflight and in any `$diagnose` surface map.
   - If `$diagnose` ran, reconcile the implementation against its Surface Map B before finalizing.
   - Run `gitnexus_detect_changes` after edits when GitNexus MCP is available and before committing.
9. For non-trivial diffs, run `$code-review` after the production-code gate and
   before the Claude Advisor challenge.
   - Review against the correct fixed point and the governing PRD/issue/spec.
   - Keep Standards findings separate from Spec findings.
   - Disposition every finding: fixed, rejected-with-evidence, or an accepted
     follow-up with a tracked issue.
   - After any fix, rerun the affected tests and the production-code gate.
10. Use `$claude-advisor` in challenge mode before commit for non-trivial code
   changes.
   - Use the Claude Advisor wrapper from the target worktree so Claude receives
     live branch/head/PR metadata plus the actual dirty or PR/base diff.
   - Identify the exact PR number or branch/head SHA, base ref when needed,
     reviewer/PRD issue text, TDD proof, `$code-review` findings and their
     dispositions, and no-change surfaces.
   - Ask whether the wrapper-provided live diff resolves the exact PRD or
     reviewer issue with the smallest production-safe change.
   - Do not use a prose diff summary as the evidence source.
   - Require focus on Greptile/Cubic/CodeRabbit/Devin/human finding coverage,
     bloat, duplicated logic, behavior drift, weak proof, wrong slice ownership,
     and hidden regression risk.
   - Require Claude to challenge whether the implemented test path satisfies
     `$tdd` and whether any architecture change stayed targeted rather than
     becoming a broad refactor.
   - Validate Claude's advice against code, tests, PRDs, reviewers, GitNexus,
     and production-code gates before changing or committing.
11. After commit/push/PR-update, run the **PR Reviewer Completion Gate**
    (`AGENTS.md`) before declaring complete or moving to another slice/PRD.
    The task is not complete until that gate passes for the current head.
12. Final response must include:
   - summary of the behavior changed
   - verification commands and outcomes
   - GitNexus authority repo/status used for packet-scoped checks
   - reviewer-loop status for the current PR head, when a PR/review exists
   - blockers, unverified surfaces, or follow-ups

## Scope Rules

- For review-only tasks, do not edit unless the user explicitly requested fixes.
- For large plans, branch strategy, PR structure, multi-PR work, or any likely PR over 1,300 changed code lines, use `$repo-large-implementation` first, then this skill for each execution pass.
- If not inside a git repository, state that Repo Context Forge does not apply and continue only if the task can still be safely scoped.

## Do Not

- Do not run production-preflight before Repo Context Forge on repo code changes.
- Do not edit first and write a retrospective preflight.
- Do not use GitHub review comments as a substitute for inspecting the packet target surface.
- Do not treat local branch-only tests as sufficient proof for transaction-sensitive or contract-sensitive changes.
- Do not add a wrapper, second implementation path, or broad configurability unless preflight proves it is the shortest correct path.
- Do not treat Claude Advisor output as permission to skip Repo Context Forge,
  GitNexus, production-preflight, production-code, tests, or reviewer-thread
  verification.
- Do not report PR/review work complete merely because changes were committed,
  pushed, or a PR was updated. Completion requires the reviewer-loop gate on
  the latest head.
