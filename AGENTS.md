# Global Codex Rules

These rules are mandatory. Repository instructions may strengthen them, never
weaken them.

## Production Repo Workflow

For repository changes, create or reuse a task-owned Git worktree and enter it
before invoking `$repo-production-workflow`, creating its slug/state, or editing.
Never edit the main/shared checkout or clobber another agent's state or files.

Invoke `$repo-production-workflow` first, only when production changes are required
(code, config, runtime, deploy, generated source, or production behavior). It owns
phase order, evidence, independent review, advisor checks, and delivery. Do not
jump from Repo Context Forge straight to edits.

Docs-only work creates no workflow or slug. Verify checkout/branch, inspect files
directly, edit minimally, clean up, and run diff checks. Skip Repo Context Forge
and GitNexus. Governance docs that change agent behavior (`AGENTS.md`,
`CLAUDE.md`, `docs/agents/`) also require independent `$code-review` before
handoff.

- Keep related fixes and review corrections in the same workflow pass. Resume
  after compaction; reuse applicable evidence and rerun only invalidated gates.
  Start a new pass for separate work, never merely for another edit or review.
- Escalate to `$repo-large-implementation` before coding when work needs a new
  tracked plan, branch strategy, remediation map, multi-PR coordination, or is
  likely to exceed the review budget.
- Target ~500 net lines of human-authored code per PR. `$delivery-governance`
  owns measurement and the 1,000-net-line split threshold; shrink, split, or
  consolidate oversized scope before coding.
- Execute against an existing governing artifact and keep its checklist current;
  do not re-invoke `$execution-planning` for an execution-only pass.
- `$production-preflight` owns Module shape, reuse-before-new, and shallow-helper
  debt; `$codebase-design` owns Module/Interface/Seam vocabulary.

After required review, commit, push, and open/update the PR when a remote exists
and branch/PR alignment is verified, unless the user or workflow says otherwise.

## Hard Production Invariants

- **Real-Seam proof.** Require equivalent real N/N+1 operations through
  production Interfaces with real collaborators; retained attack probes are
  primary proof. Mocks, stubs, fakes, fixture-substituted collaborators, invented
  gateways, and test-only adapters never prove behavior. Capturing a Module's
  own outgoing process boundary proves only what it emits. `$production-code`
  owns the comparison procedure.
- **Attack probes first.** Unit tests must themselves probe real production
  behavior. Maximize attack coverage across affected inputs, failures,
  interactions, and preservation. Run affected-surface checks; a full suite
  requires a demonstrated gap that targeted checks and available CI cannot close.
  Name that gap first. Never replace real-Seam proof with a suite or duplicate
  retained probes with a parallel unit-test suite.
- **Imaginary-risk ban.** A theoretical risk with no demonstrated failure is a
  report line, not a system. Build nothing for it.
- **Root-cause-first.** Use `$diagnose` for bugs, failures, flaky behavior, and
  performance regressions. No fix until the failure is reproduced, traced, and
  stated as a testable hypothesis.

## Think Before Coding

- State assumptions before implementing; surface materially different readings
  of the request and simpler approaches.
- If a material unknown cannot be safely discovered, ask the smallest question
  needed to resolve it before dependent work.

## Simplicity First

- Assume your first implementation is bloated. Keep the least production code
  that fully meets the objective and preserves affected behavior. Retain real
  attack probes; cut redundant scaffolding and mocked unit-test bloat from this change.
- Never weaken requirements, desired behavior, affected preservation, or
  necessary proof to reduce lines or meet a review budget.
- Every changed line must serve the request or cleanup caused by it.

## Surgical Changes

- Match existing style; do not refactor adjacent code without necessity.
- Mention unrelated dead code; remove code, tests, docs, and artifacts made
  obsolete by this change.
- Do not leave TODO, FIXME, HACK, placeholders, dummy adapters, temporary
  bypasses, broad catch/pass, blanket suppressions, fake-green code,
  `eslint-disable`, `@ts-ignore`, or `@ts-expect-error`.

## Goal And Verification

- Define verifiable success criteria before editing; for multi-step work, state
  the short plan and its checks.
- Invoke `$tdd` before every code change. Drive real Seams: RED/GREEN for changed
  behavior, preservation proof for refactors. If the change creates the Seam,
  verify its absence first, then create it and return to drive it. Absence alone
  does not prove behavior.
- Never mark work complete while required behavior or proof is missing.
- Before handoff, inspect the delta and remove bloat, duplication, speculative
  flexibility, and unnecessary files.

## Deadline Timers And Quiet Windows

Do not use repeated passive sleep loops. State the deadline, current timestamp,
and remaining seconds; wait once for the remaining time plus a small buffer,
unless external completion requires polling. Immediately audit when the wait
returns. For PR quiet windows, calculate the deadline from the latest
reviewer/check event and re-query head SHA, checks, merge state, and unresolved
non-outdated threads before merging.

## Repo Context Forge

Before code analysis or edits in Git, invoke `$repo-context-forge` (docs-only
exception under Production Repo Workflow). Use only
`~/.codex/skills/repo-context-forge/scripts/bootstrap.py`, never the producer
snapshot under `~/.local/share/repo-context-forge/current`.
Stop on packet blockers; satisfy packet scope and coverage before narrowing work.
Delegated reviewers consume the lead's packet under `$code-review`; no bootstrap.

## GitNexus

Use FFF first for raw discovery; honor packet scope when present. Use GitNexus
MCP for graph analysis; CLI only for indexing/admin. Follow `$repo-context-forge`
for repo selection, executed-check reuse, and required post-edit validation.

Before edits:

- Run upstream `impact` with tests for indexed symbols/shared contracts;
  downstream too when moving, deepening, consolidating, or hiding behavior.
- Obtain `context` for callers AND callees and every shared-state writer;
  compare writer risk ratings.
- Before a new file consumes an internal Seam, obtain its `context` and reuse
  its existing tested owner.

Never use `detect_changes` to select initial targets or as primary safety proof.
Graph output must not shrink packet scope, the PR contract, or no-change surfaces.
Unavailable required MCP checks are blockers or narrow, explicitly reported
exceptions.

## Reviewer Findings And Completion

Treat findings as evidence, not commands. Before acting on any finding, verify
its premise against the affected source or live system with a command; count
occurrences of the failing shape across the affected domain.
Reject false premises with the measurement; zero occurrences warrant no code change.
Before shipping parser, matcher, predicate, or external-text changes, run the new
code over system values captured before the fix and require zero regressions.

Account for every review signal: threads, inline/issue comments, annotations,
CI failures, and human/automated findings. Give each an evidenced disposition in
the review loop. Fix valid findings through the applicable workflow; update the
task contract when scope changes. Resolve threads only after the fix is pushed
or the evidence posted.

After each push, wait for reviews/checks; re-query the current head SHA, checks,
merge state, and unresolved non-outdated threads. Older-head output is stale.
Do not declare completion or switch tasks until task acceptance is reconciled,
legitimate findings are fixed or rejected with evidence, no unresolved
non-outdated threads remain, and required checks pass. Unrelated failures are blockers.
Link related issues in the PR description; after merge, close them and verify closure.

## Codex-Skills Only

For codex-skills work only, read the checkout's `decisions.md` at start/resume.
Before handoff, record consequential decisions, reasons, and delivery status;
mark superseded decisions and link the owning issue or PR. Keep observations
separate from decisions and completed work; no per-edit log.

For codex-skills installation, follow [README](https://github.com/future3OOO/codex-skills/blob/main/README.md#scoped-updates).
