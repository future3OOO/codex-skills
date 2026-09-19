# Global Codex Rules

These rules apply unless a repository `AGENTS.md` adds stricter project-specific
instructions.

## Hard Production Invariants

- **Real-Seam proof.** Prove behavior through the real production Interface
  with real collaborators. A mock, stub, fake, fixture-substituted collaborator,
  invented gateway, or test-only adapter is never proof. A capture at a Module's
  own outgoing process boundary is the real Seam for assertions about what that
  Module emits; the ban targets substituted collaborators inside the asserted
  contract. `$production-code` owns N/N+1 comparison and outcome verification.
- **Targeted verification.** Prefer a retained attack probe at the real Seam;
  a suite result cannot replace proof of the changed behavior. Run the targeted
  operations covering the affected behavior and preservation. New tests earn
  their lines like production code. `$repo-production-workflow` owns full-suite
  placement: CI where it supplies coverage, locally otherwise.
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

- Write the minimum code that solves the request. No speculative features,
  abstractions, configurability, or impossible-scenario handling.
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
- Use `$tdd` for behavior changes where a failing attack at the real Seam is
  practical. If that Seam cannot be driven, report the proof gap as a finding.
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

Invoke `$repo-production-workflow` first for production code, config, runtime,
deploy, generated-source, or behavior-changing repository work. It owns phase
order, evidence, independent review, advisor checks, and delivery. Do not jump
from Repo Context Forge straight to edits.

Docs-only changes use the lightweight path: verify checkout/branch, inspect
files directly, edit minimally, clean up, and run diff checks. Skip Repo Context
Forge and GitNexus. Governance docs that change agent behavior (`AGENTS.md`,
`CLAUDE.md`, `docs/agents/`) also require independent `$code-review` before
handoff.

- Start a new workflow pass for each new PR slice, bug fix, or review-fix round.
  Resume the same pass after compaction; load only missing or changed context.
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
  failures, automated and human findings, and PRD acceptance criteria. Use
  `docs/agents/reviewers.md` when present for the reviewer roster.
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

For coding, debugging, review, refactor, explanation, planning, or repository
exploration, invoke `$repo-context-forge` before choosing files, editing, or
GitNexus analysis (docs-only exception in §7). A delegated reviewer consumes
the lead's packet and evidence under `$code-review`; it does not bootstrap.

Use only the installed governed wrapper
`~/.codex/skills/repo-context-forge/scripts/bootstrap.py`, never the producer
snapshot under `~/.local/share/repo-context-forge/current`. Follow the workflow
for governed startup/resume; `$repo-context-forge` owns standalone modes. Supply
the active pass's slug and intent for governed intake; omit `--workflow-slug`
only when no pass is active.

Require `REPO_CONTEXT_FORGE_REQUIRED_INTAKE`; stop and surface packet blockers.
The packet fixes the first-pass surface: `<targets>`, `<soulforge_impact>`,
`<coverage_plan>`, and `<gitnexus_status><repo>`. Inspect changed files and top
targets and satisfy coverage before narrowing to a symbol or review thread;
explain skipped changed/high-ranked targets. GitHub comments supplement that
intake and the task contract.

Do not leave `.soulforge`, `.codex`, `.claude`, `.gitnexus`, or incidental
`.gitignore` changes in the checkout. An intentional `.gitnexus/` ignore is
allowed only when indexing the source checkout; keep the index out of commits.

## 9. GitNexus

Repo Context Forge fixes the surface; FFF raw discovery stays within it and is
the first discovery layer outside the gate. GitNexus validates graph impact;
`detect_changes` never selects initial targets or serves as primary safety proof.
Use MCP for `query`, `context`, `impact`, and `detect_changes`; CLI for
indexing/admin only (`analyze`, `status`, `clean`).

Before edits, use the packet's repo and read its executed graph checks. Reuse
applicable results and run missing checks; packet context covers callers, not
callees. Cover each changed target:

- `impact(direction="upstream", includeTests=true)` for indexed symbols or
  shared contracts; downstream too when moving, deepening, consolidating, or
  hiding behavior behind an Interface.
- `context` for callers AND callees. For shared state (table, row, file, lease,
  claim token, transition helper), inspect every writer and compare risk ratings.
- Before a NEW file consumes an internal Seam, run `context` on that Seam;
  import its existing tested owner rather than a second parsing/lifecycle client.

Graph output never shrinks the packet surface, PR contract, or no-change surfaces.

Re-analyze after indexed-symbol, shared-contract, persistence, config/runtime/
deploy, external-integration, browser-automation, or transaction-sensitive edits,
or when the index is stale. Skip docs-only and tiny leaf edits that do not affect
shared graph surfaces. Run `gitnexus analyze --skip-agents-md .`, then
`gitnexus status` and MCP `detect_changes(scope="unstaged")` on that source
checkout's repo. This is supplemental evidence; unavailable MCP for a required
check is a blocker or a narrow, explicitly reported exception.

## Codex-Skills Decision Record

For codex-skills work only, read the checkout's `decisions.md` at start/resume.
Before handoff, record consequential decisions, reasons, and delivery status;
mark superseded decisions and link the owning issue or PR. Keep observations
separate from decisions and completed work; no per-edit log.
