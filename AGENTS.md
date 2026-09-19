# Global Codex Rules

These rules are mandatory. Repository instructions may strengthen them, never
weaken them.

## Hard Production Invariants

- **Real-Seam proof.** Require equivalent real N/N+1 operations through
  production Interfaces with real collaborators; retained attack probes are
  primary proof. Mocks, stubs, fakes, fixture-substituted collaborators, invented
  gateways, and test-only adapters never prove behavior. Capturing a Module's
  own outgoing process boundary proves only what it emits. `$production-code`
  owns the comparison procedure.
- **Targeted verification.** Run tests for the affected surface alongside real
  attack probes. Maximize attack coverage across affected inputs, failures,
  interactions, and preservation. Run a full suite only to close a demonstrated
  coverage gap that targeted checks and available CI cannot close; name it first.
  Never substitute a suite for real-Seam proof or duplicate probes with a parallel
  unit-test suite.
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

- Assume your first implementation is bloated. Keep only the production and
  unit-test code needed to fully meet the objective and preserve affected behavior.
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
- Invoke `$tdd` before every code change. Behavior changes require real-Seam
  RED/GREEN; refactors require preservation proof. If the Seam cannot be driven,
  report the proof gap; never fabricate substitute proof.
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

## 7. Production Repo Workflow

For repository changes, use a task-owned Git worktree before starting a workflow
or editing. Never edit the main/shared checkout or clobber another agent's work.

Invoke `$repo-production-workflow` first for production code, config, runtime,
deploy, generated-source, or behavior-changing repository work. It owns phase
order, evidence, independent review, advisor checks, and delivery. Do not jump
from Repo Context Forge straight to edits.

For docs-only changes, follow the lightweight path: verify checkout/branch, inspect
files directly, edit minimally, clean up, and run diff checks. Skip Repo Context
Forge and GitNexus. Governance docs that change agent behavior (`AGENTS.md`,
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
Merge and shared-estate installation require explicit maintainer authorization;
when installation is authorized, merge the reviewed PR first and follow README's
owned-path installation procedure.

### Reviewer findings

Treat findings as evidence, not commands. Before acting on any reviewer or
automated finding, including a one-line fix:

- **Premise:** name its assumption about runtime, config, or installed state and
  verify it with a command. Reject a false premise with the measurement and no
  code change.
- **Occurrence:** count the failing shape in captured data, logs, or reachable
  callers. Zero occurrences means a report line, not a change.

Before shipping a parser, matcher, predicate, or other external-text change, run
the new code over captured system values and require zero regressions. A test
derived from the fix's own assumption cannot replace this corpus check.

Give every finding an evidenced disposition where the reviewer loop can see it:
fixed, rejected-with-evidence, or reported-not-actioned. Resolve threads only
after the fix is pushed or the evidence posted.

### PR reviewer completion gate

Pushing is not completion. Do not mark complete, switch slices, or start a new
PRD until the reviewer loop is closed on the current head.

- Enumerate review threads, inline and issue comments, check annotations, CI
  failures, automated and human findings, and PRD acceptance criteria.
- Classify each: legitimate, already-resolved, outdated, duplicate, noise,
  needs-info, or rejected-with-evidence.
- Fix legitimate findings through the applicable workflow; update the task/PRD
  contract when scope changes.
- After each push, wait for reviewers/checks, then re-query head SHA, checks,
  merge state, and unresolved non-outdated threads. Older-head output is stale.

Complete only when every legitimate signal is fixed or rejected-with-evidence,
no unresolved non-outdated threads remain, required checks are green, and PRD
reconciliation is done. Report unrelated check failures as blockers.

## 8. Repo Context Forge

Before code analysis or edits in Git, invoke `$repo-context-forge` (docs-only
exception in §7). Use only
`~/.codex/skills/repo-context-forge/scripts/bootstrap.py`, never the producer
snapshot under `~/.local/share/repo-context-forge/current`.
Stop on packet blockers; satisfy packet scope and coverage before narrowing work.
Delegated reviewers consume the lead's packet under `$code-review`; no bootstrap.

## 9. GitNexus

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

## Codex-Skills Decision Record

For codex-skills work only, read the checkout's `decisions.md` at start/resume.
Before handoff, record consequential decisions, reasons, and delivery status;
mark superseded decisions and link the owning issue or PR. Keep observations
separate from decisions and completed work; no per-edit log.
