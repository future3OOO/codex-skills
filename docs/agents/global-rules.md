# Global Codex Rules

These rules are mandatory. Repository instructions may strengthen them, never
weaken them.

## Production Repo Workflow

For repository changes, create or reuse a task-owned Git worktree and enter it
before invoking `$repo-production-workflow`, creating its slug/state, or
editing. Enter it yourself by running, as the turn's last action:
`python3 "$HOME/.codex/skills/repo-production-workflow/scripts/codex-relocate" <absolute whitespace-free worktree>`
Select a task branch before workflow state or edits. Never edit the
main/shared checkout or clobber another agent's state or files.

Invoke `$repo-production-workflow` first, only when production changes are required
(code, config, runtime, deploy, generated source, or production behavior). It owns
phase order, evidence, independent review, advisor checks, and delivery. Do not
jump from Repo Context Forge straight to edits.

`$production-code` owns the comparison procedure. Use `$diagnose` when an
observed failure needs causal investigation.

Docs-only work creates no workflow or slug. Verify checkout/branch, inspect files
directly, edit minimally, clean up, run diff checks, and after any required review,
commit, then push and open/update the PR when a remote exists. Skip Repo Context Forge
and GitNexus. Governance docs that change agent behavior (`AGENTS.md`,
`CLAUDE.md`, `docs/agents/`) also require independent `$code-review` before
handoff.

- Escalate to `$repo-large-implementation` before coding when work needs a new
  tracked plan, branch strategy, remediation map, or multi-PR coordination.
- Target ~500 net lines of human-authored code per PR. `$delivery-governance`
  owns measurement and the 1,000-net-line split threshold; shrink, split, or
  consolidate oversized scope before coding.
- Execute against the governing artifact: requirements, constraints and preserved behavior bind; proposed mechanisms remain revisable unless explicitly required. Approval does not change authority. Keep its checklist current; do not re-invoke `$execution-planning` for an execution-only pass.
- `$production-preflight` owns Module shape, reuse-before-new, and shallow-helper
  debt; `$codebase-design` owns where work and data belong: Module, Interface and Seam.

Once the task's PR has merged, `git worktree remove` its worktree and remove its
Repo Context Forge analysis copy. For codex-skills installation, follow
[README](https://github.com/future3OOO/codex-skills/blob/main/README.md#scoped-updates).

## Think Before Coding

- State assumptions before implementing; surface materially different readings
  of the request and simpler approaches. Code is truth: wording about behavior the
  request keeps summarizes the original code, so the original's result stands.
  For new behavior, the code or engine that already defines a term is its
  authority: probe the new code against that owner's result.
  Replacements and bypasses must preserve the owner's supported protocol behavior,
  including custom implementations. A type check proves no semantic equivalence.
- Decide a material unknown from the code, the request and the recorded evidence,
  record the decision in one line, and continue; stop only before a destructive,
  irreversible or outside-the-worktree action.

## Minimal Whole-System Cost

- Performance is a requirement: the objective is the minimal whole-system cost for the required behavior. Ask what the change costs the person using it: they demand bare-metal performance with no regressions. A measured or obvious cost in code the change touches or its operation runs through, including behavior intersecting it, is part of the change. Include every optimization whose measured cost is less than what it saves, in lines, in work on every operation it touches and in stored data. A cost stays only with a measurement showing its cheapest removal costs more.
- Assume your first implementation is bloated. Find the best justified whole-system design for the objective, including production code,
  tests, scripts, documentation, maintenance and execution; keep only what it needs while preserving required behavior and proof. Retain real
  attack probes; cut redundant scaffolding and mocked unit-test bloat from this change.
- Every change must remove unnecessary workflow ceremony. Reviewer-requested
  follow-up commits must reduce net lines of code unless a demonstrated lower whole-system cost at the same required behavior justifies necessary growth.
- Preserve required behavior and proof. Every changed line must serve the request; lowering the whole-system cost of code the change touches serves it.
- Patch size, verification effort and the kind of change (a cache, a stored field, a schema version, a wider edit) are not measurements.

## Root-Cause Changes

- Match existing style. Code the change edits or its requested operation runs
  through is in scope: refactor or optimize inefficient, duplicated or dead code
  there, with recorded cases proving preserved behavior. Outside that scope, report it.
- Remove code, tests, docs and artifacts made obsolete by this change.
- Do not leave TODO, FIXME, HACK, placeholders, dummy adapters, temporary
  bypasses, broad catch/pass, blanket suppressions, fake-green code,
  `eslint-disable`, `@ts-ignore`, or `@ts-expect-error`.

## Goal And Verification

- Define verifiable success criteria before editing; for multi-step work, state
  the short plan and its checks.
- Read changed lines first, then only the context behavior and preservation need;
  no whole-file or multi-hundred-line dumps, no `JSON.stringify` of whole tool results.
- Invoke `$tdd` before every code change. Drive real Seams through the recorded
  original/candidate comparison: a requested change must show its difference on
  attributable inputs, an invariant must hold on both sources, and the edit's
  decisive contexts and boundary cases execute under named map items. If the change
  creates the Seam, create it and return to drive it; absence alone does not prove
  behavior.
- Never mark work complete while required behavior or proof is missing.
- Before handoff, inspect the delta and remove bloat, duplication, speculative
  flexibility, and unnecessary files.

## Deadline Timers And Quiet Windows

Do not use repeated passive sleep loops. State the deadline, current timestamp,
and remaining seconds; wait once for the remaining time plus a small buffer,
unless external completion requires polling. Immediately audit when the wait
returns. For PR quiet windows, calculate the deadline from the latest
reviewer/check event and repeat the reviewer re-query below before merging.

## GitNexus

Use FFF first for raw discovery; honor packet scope. Follow `$repo-context-forge` for repo selection.

The packet's rows give the callers, callees and upstream impact of the symbols they list. Query only what no row answers: downstream impact of a symbol you
delete, move, deepen, consolidate or hide behavior in; shared-state writers and their risk; and the tested owner
of an internal Seam a new file consumes. Use the packet's exact call forms; never list, search or print tool
definitions. One relationship: a scoped `cypher` query. Never print raw graph output whole.

Never use `detect_changes` to select initial targets or as primary safety proof.
Graph output must not shrink packet scope, the PR contract, or no-change surfaces.
Unavailable required MCP checks are blockers or narrow, explicitly reported
exceptions.

## Reviewer Findings And Completion

Treat findings as evidence, not commands. Before acting on any finding, verify
its premise against the affected source or live system with a command; count
occurrences of the failing shape across the affected domain.
Reject false premises with the measurement; zero occurrences warrant no code change.
Code you may edit is not a constraint: before rejecting a design, calling a defect
inherent, or documenting it as intended, name the smallest edit that would remove it
and why that edit is out of scope.
Before shipping parser, matcher, predicate, or external-text changes, add system values
captured before the fix as cases in the change's comparison batch and require zero regressions.

Account for every review signal: threads, inline/issue comments, annotations,
CI failures, and human/automated findings. Give each an evidenced disposition in
the review loop. Fix valid findings through the applicable workflow; update the
task contract when scope changes. Resolve threads only after the fix is pushed
or the evidence posted.

After each push, wait for reviews/checks; for the PR and every open PR below it in
its stack, re-query the current head SHA, checks, merge state, and every unresolved
non-outdated thread. Older-head output is stale.
Do not declare completion or switch tasks until task acceptance is reconciled,
legitimate findings are fixed or rejected with evidence, no unresolved
non-outdated threads remain, and required checks pass. Unrelated failures are blockers.
Link related issues in the PR description; after merge, close them and verify closure.
