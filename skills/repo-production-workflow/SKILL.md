---
name: repo-production-workflow
description: Orchestrate production repo changes — Repo Context Forge, packet-scoped GitNexus, Claude Advisor scope check, production-preflight, production-code with tdd through final verification, code-review, and the pre-commit Claude Advisor challenge. Use for implementation, bug fixes, refactors, and review-comment fixes that must stay minimal, verified, and fail-closed.
---

# Repo Production Workflow

Use this skill for production code changes inside a git repository.
It is an orchestration skill and the single owner of the production sequence;
it does not replace the referenced skills.

## Mandatory Order

1. Run `$repo-context-forge` before choosing files, GitNexus queries, review findings, or edits.
   - If the user described planned work before files changed, pass the request as `--intent`.
   - If Repo Context Forge emits blockers, stop and surface them.
   - Use packet targets and coverage plan as the first-pass surface. FFF raw discovery (per the global search flow) operates within that surface, not ahead of it.
   - When the packet lists `delegation_tasks`, spawn the consolidated specialist before GitNexus calls or edits; pass the existing intake/packet summary and instruct it not to re-run Repo Context Forge or spawn further agents.
2. State the task contract from the user request and packet surface.
   - Name the changed behavior, module shape, public Interface, test surface, existing reuse path, and rejected shallow path or new-module justification.
   - Name verification surfaces, no-change surfaces, and skipped high-ranked targets with reasons.
   - Estimate whether the implementation can stay near the review-budget target (~500 net lines); if not, escalate to `$repo-large-implementation` before editing.
   - For bug, regression, or flaky-failure fixes, invoke `$diagnose` before preflight: no fix until the root cause is reproduced and stated as a testable hypothesis.
3. Run the packet-listed GitNexus required checks.
   - Use the packet repo value and state it as the GitNexus authority for this packet.
   - Do not let broader GitNexus output shrink the packet surface.
   - Before editing indexed symbols or shared contracts, run upstream impact; also run downstream impact when behavior is moved, deepened, consolidated, or hidden behind an Interface.
   - If a required GitNexus MCP tool is not currently loaded, run tool discovery for that exact capability before reporting it unavailable or falling back.
4. Run the `$claude-advisor` scope check after the GitNexus checks and before preflight.
   - Read-only and advisory only. Use the wrapper with a stable task slug; the challenge round in step 9 must resume this same slug/session.
   - Forward the task contract, packet targets, and GitNexus impact summary; ask whether the packet covers the correct Seams and surface area, whether the work deepens an existing Module or risks a shallow split, and whether `$improve-codebase-architecture` is needed before editing.
   - If the advisor is unavailable, do not block on an advisory input: proceed under the remaining gates and state the skipped consult in the final response.
   - Advisor findings are advisory: validate them against the packet and GitNexus before adopting; feed confirmed missed seams into preflight.
5. Run `$production-preflight` before the first tracked edit. This gate is unconditional for production code changes.
   - Anchor its proof to the Repo Context Forge packet, GitNexus checks, and confirmed advisor scope findings.
   - If `$diagnose` ran, carry its Surface Map B into preflight as the affected-surface input; do not replace it with a file-only scope.
   - If preflight has blocking questions, stop before editing.
6. Invoke `$production-code` before writing any repository file content, then implement under it.
   - For behavior changes, follow `$tdd` under production-code: write the failing test through the public Interface first, against the real seam. This produces the TDD proof step 9 forwards.
   - Choose the smallest production-safe implementation path before editing.
   - This includes tracked files, untracked files, scratch implementation files, generated source, and new worktrees.
   - Make the smallest direct change; reuse existing paths before adding helpers or abstractions.
   - Keep behavior fail-closed and boundary-validated. Do not clean up unrelated code.
7. Verify under `$production-code` against the affected surface.
   - Run focused tests and the repo gates relevant to touched areas.
   - Re-check no-change surfaces named in preflight and in any `$diagnose` surface map; if `$diagnose` ran, reconcile the implementation against its Surface Map B before finalizing.
   - When the edit touched indexed symbols, shared contracts, persistence, config/runtime/deploy surfaces, external integrations, browser automation, or transaction-sensitive flows, re-analyze the edited checkout (`gitnexus analyze --skip-agents-md .`, then `gitnexus status`) before final graph checks.
   - Then run `gitnexus_detect_changes` against the source-checkout repo before committing, and run the bundled production-code gate before finalizing.
8. For non-trivial diffs, run `$code-review` after the production-code gate and
   before the Claude Advisor challenge. Non-trivial means any diff beyond a
   mechanical edit with no behavior surface (formatting, renames, comments,
   docs); this definition also sets the step-9 threshold.
   - Review against the correct fixed point and the governing PRD/issue/spec.
   - Keep Standards findings separate from Spec findings.
   - Disposition every finding: fixed, rejected-with-evidence, or an accepted
     follow-up with a tracked issue.
   - After any fix, rerun the affected tests and the production-code gate.
9. For non-trivial diffs (same threshold as step 8), run the `$claude-advisor` challenge round before any commit, push, PR open, or PR update.
   - Fix-only commits whose every change addresses a finding already confirmed in this pass's challenge round, code-review, or the PR reviewer loop do not need a new round; state the skipped round in the final response.
   - Resume the SAME advisor slug/session from the step-4 scope check so the advisor retains the original scope. If the stored session is unreachable, run a fresh consult re-forwarding the step-4 payload plus the current diff and label it a fallback.
   - Use the wrapper from the target worktree so Claude receives live branch/head/PR metadata plus the actual dirty or PR/base diff; do not use a prose diff summary as the evidence source.
   - Identify the exact PR number or branch/head SHA, base ref when needed, reviewer/PRD issue text, TDD proof, and `$code-review` findings and dispositions.
   - Ask whether the wrapper-provided live diff resolves the exact PRD or reviewer issue with the smallest production-safe change, satisfies `$tdd`, stays free of fake tests and imaginary-risk engineering, and covers the reviewer signals on the current head (roster: `docs/agents/reviewers.md` when present).
   - If the advisor is unavailable, proceed as in step 4 and state the skipped round.
   - Validate Claude's advice against code, tests, PRDs, reviewers, GitNexus, and production-code gates before changing or committing.
10. After commit/push/PR-update, run the **PR Reviewer Completion Gate**
    (`~/.codex/AGENTS.md`) before declaring complete or moving to another
    slice/PRD.
    The task is not complete until that gate passes for the current head.
11. Final response must include:
    - summary of the behavior changed
    - verification commands and outcomes
    - GitNexus authority repo/status used for packet-scoped checks
    - `$code-review` findings and dispositions when it ran
    - Claude Advisor status: scope-check and challenge-round findings adopted or rejected, or the skipped round with its reason
    - reviewer-loop status for the current PR head, when a PR/review exists
    - blockers, unverified surfaces, or follow-ups

## Scope Rules

- For review-only tasks, do not edit unless the user explicitly requested fixes.
- For large plans, branch strategy, PR structure, multi-PR work, or any likely PR over the review budget, use `$repo-large-implementation` first, then this skill for each execution pass.
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
