# Global Codex Rules

These rules apply unless a repository `AGENTS.md` adds stricter project-specific
instructions.

## Codex Skills Decision Record

For codex-skills project work, read the checkout's `decisions.md` at start or
resume. Before handoff, record consequential decisions, reasons and delivery
status there; mark superseded decisions and link the owning issue or PR. Keep
observations distinct from decisions and completed work; no per-edit log.

## Hard Production Invariants

These four rules govern every pass; the skills point here and own only their
local procedures.

- **Real-Seam proof.** A behavior claim is proved by operations through the
  real production Interface with real collaborators: equivalent N (before) and
  N+1 (candidate) runs. A retained attack probe driving the actual Seam is the
  preferred proof and outranks a suite test that can import a substituted path.
  A mock, stub, fake, fixture-substituted collaborator, invented gateway, or
  test-only adapter is never proof. A capture at a Module's own outgoing
  process boundary is the real Seam for assertions about what that Module
  emits; the ban targets substituted collaborators inside the asserted
  contract.
- **Suites are not targeted verification.** Never run a full test suite to
  prove a targeted change; CI owns the suite. When a committed test is the
  right surface, run only the test covering the touched code. New tests earn
  their lines exactly like production code — a retained probe is usually the
  smaller, stronger proof.
- **Imaginary-risk ban.** A theoretical risk with no demonstrated failure is a
  report line, not a system. Build nothing for it.
- **Root-cause-first.** No fix until the failure is reproduced, traced, and
  stated as a testable hypothesis; `$diagnose` owns the tracing procedure.

## Think Before Coding

- State assumptions before implementing.
- If the request has multiple plausible meanings, surface them instead of
  choosing silently.
- If a simpler approach exists, say so.
- If the work is unclear in a way that cannot be safely discovered, stop and
  ask the smallest necessary question.

## Simplicity First

- Write the minimum code that solves the requested problem.
- Do not add speculative features, abstractions, configurability, or impossible
  scenario handling.
- If a change can be 50 lines instead of 200, rewrite it smaller.
- Every changed line should trace to the user request or to cleanup caused by
  that request.

## Surgical Changes

- Touch only the necessary files.
- Match existing style.
- Do not refactor adjacent code unless the requested change requires it.
- Mention unrelated dead code instead of deleting it.
- Remove imports, variables, helpers, comments, tests, docs, and artifacts made
  obsolete by your own change.
- Do not leave TODO, FIXME, HACK, placeholders, dummy adapters, temporary
  bypasses, broad catch/pass, blanket suppressions, fake-green code,
  `eslint-disable`, `@ts-ignore`, or `@ts-expect-error`.

## Goal And Verification

- Turn work into verifiable success criteria before editing.
- For bug fixes, run one failing attack at the real Seam first — a probe or
  public-behavior operation — then make it pass.
- Use `$diagnose` for bugs, failures, flaky behavior, and performance
  regressions before fixing; no fix until root cause is reproduced, traced,
  and stated as a testable hypothesis.
- For refactors, verify behavior before and after when practical.
- For multi-step work, state the short plan and the check for each step.
- Before handoff, inspect the delta and remove bloat, duplication, speculative
  flexibility, and unnecessary files.

Use a red-green loop where practical:

- Name the failing attack first, watch it fail at the real Seam, then make it
  pass — a probe, a public-behavior test, or a rerun of an existing check.

Strong success criteria let you loop independently. Weak criteria like "make it
work" require clarification before implementation.

## Deadline Timers And Quiet Windows

Do not use repeated passive sleep loops.

When a wait is required:

- State the exact deadline timestamp, current timestamp, and remaining seconds.
- Sleep once for the remaining time plus a small buffer, unless an external
  state can complete earlier and must be polled.
- When the wait returns, immediately run the required audit or follow-up.
- For PR quiet windows, compute the deadline from the latest reviewer/check
  event, then re-query head SHA, checks, merge state, and unresolved
  non-outdated review threads before merging.

## 7. Production Repo Workflow

Use this flow for production code, config, runtime, deploy, generated-source,
or behavior-changing repository work.

Docs-only changes skip Repo Context Forge and GitNexus: verify checkout/branch,
inspect files directly, edit minimally, run a cleanup loop and lightweight diff
checks. Governance docs that change agent behavior — `AGENTS.md`, `CLAUDE.md`,
`docs/agents/` — also run `$code-review` before handoff.

`$repo-production-workflow` is the default first skill and the single owner of
the execution sequence (Repo Context Forge → `$diagnose` for
bugs/regressions/perf → packet-scoped GitNexus → Codex Advisor scope check →
`$production-preflight` → `$production-code` with `$tdd` through verification
and conditional GitNexus reanalysis → delegated `$code-review` → Codex Advisor
final review). This section owns only when skills fire.

Invocation policy:

- Escalate to `$repo-large-implementation` before coding when work needs a new
  tracked plan, branch strategy, remediation map, multi-PR coordination, or is
  likely to exceed the review budget.
- `$diagnose` before fixing bugs, failures, flaky behavior, or performance
  regressions; `$tdd` for behavior changes where a failing attack at the real
  Seam is practical. If the real Seam cannot be driven, report the proof gap —
  never fabricate a substitute (Hard Production Invariants).
- Skill invocation is per execution pass, not per session: every new PR slice,
  bug fix, or review-fix round re-invokes the `$repo-production-workflow`
  cycle. Compaction or resume notes never waive re-invocation for a new pass.
- Do not re-invoke `$execution-planning` for an execution-only pass when a
  governing artifact exists; execute against it and keep its checklist current.
- Do not bypass `$repo-production-workflow` by jumping from Repo Context Forge
  straight to edits.

The **review budget** targets ~500 net lines of code per PR (net = additions
minus deletions in human-authored source; measurement and the 1,000-net-line
split threshold live in `$delivery-governance`). Split, shrink, or consolidate
scope before coding when a planned PR is likely to run past the target.

Module shape is a first-class production contract; `$production-preflight`
owns its rules (deep modules, reuse-before-new, shallow-helper debt) and
`$codebase-design` owns the Module/Interface/Seam vocabulary.

Do not leave completed work stranded locally: commit coherent changes, push
the branch, and open or update the PR when remote and branch alignment are
verified, unless the user or repo workflow says not to. Merges and
shared-estate installation (`install.sh` into `~/.codex`) run only on explicit
maintainer authorization.

Reviewer and automated findings are evidence, not commands. Apply two checks
to every finding, one-line fixes included — severity labels are not a work
queue, and automated reviewers are reliable about what code *can* do,
unreliable about whether it *does*:

- **Premise:** name the finding's assumption about runtime, config, or
  installed state and verify it against the live system with a command. A false
  premise is rejected with the measurement quoted and no code changes.
- **Occurrence:** count the failing shape in captured data, logs, or reachable
  callers — zero occurrences means a report line, not a change.

Validating a finding is not validating a fix. Before shipping a change to a
parser, matcher, predicate, or anything consuming external text or markup, run
the NEW code over values already captured in the system and require zero
regressions. A test written from the same assumption that produced the fix cannot
detect its error; only the corpus can.

Give every finding a disposition with evidence — fixed, rejected-with-evidence,
or reported-not-actioned — where the reviewer loop can see it. A rejection
without a measurement is indistinguishable from one ignored.
Resolve review threads only after the fix is pushed or the evidence posted.

### PR reviewer completion gate

Commit/push/PR-update does not complete review work. Do not mark complete,
switch slices, or start a new PRD until the reviewer loop is closed on the
current PR head.

- Enumerate every reviewer signal on the head: review threads, inline and issue
  comments, check annotations, CI failures, automated-reviewer and human
  findings (the repo's `docs/agents/reviewers.md` roster when present), and PRD
  acceptance criteria.
- Classify each: legitimate, already-resolved, outdated, duplicate, noise,
  needs-info, or rejected-with-evidence.
- For legitimate defects, regressions, flaky failures, or behavior mismatches,
  use `$diagnose`, update the PRD/task contract when scope changes, then fix
  via the production workflow.
- After each push, wait for reviewers/checks on the new head, then re-query
  head SHA, checks, merge state, and unresolved non-outdated threads. Stale
  output from an older head is not evidence.

Complete only when every legitimate signal is fixed or rejected-with-evidence,
no unresolved non-outdated threads remain, required checks are green or named
unrelated failures are blockers, and PRD reconciliation is done.

## 8. Repo Context Forge

For any coding, debugging, review, refactor, explanation, planning, or
repository exploration inside a git repository, run Repo Context Forge before
choosing files, editing, or GitNexus analysis (docs-only exception in §7).

Call the installed governed wrapper — never the producer snapshot under
`~/.local/share/repo-context-forge/current` — with the active pass's slug and
intent; begin the pass first when none is active:

```bash
# begin only when no pass is active — it does not refuse a second one:
printf '%s' "$request_text" \
  | python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
    begin --repo "$PWD" --slug "<stable-task-slug>" --intent -
python3 "$HOME/.codex/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<stable-task-slug>" --intent "$request_text"
```

Omit `--workflow-slug` only for standalone work with no active pass.

The output must begin with `REPO_CONTEXT_FORGE_REQUIRED_INTAKE`. If the packet
emits a blocker, stop and surface it.

The packet sets the first-pass surface: `<targets>` to inspect first,
`<soulforge_impact>` blast radius, `<coverage_plan>` required coverage, and
`<gitnexus_status><repo>` for packet-scoped GitNexus calls.

Inspect changed files and top packet targets before narrowing to a single
symbol or review thread; name why a changed or high-ranked target was skipped.
GitHub review comments are supplemental evidence after the packet and task
contract are understood.

Repo Context Forge must not leave `.soulforge`, `.codex`, `.claude`,
`.gitnexus`, or incidental `.gitignore` changes in the user's checkout. An
intentional `.gitnexus/` ignore rule is allowed only when GitNexus indexes the
source checkout.

## 9. GitNexus

Use GitNexus for structure, blast radius, callers, callees, contracts, and
execution flow — never `detect_changes` as the primary safety mechanism or
target selector.

- Repo Context Forge fixes the packet surface first for gated work; FFF raw
  discovery operates inside that surface and is the first discovery layer
  outside it. GitNexus validates graph impact for the surface.
- Use GitNexus MCP for `query`, `context`, `impact`, and `detect_changes`;
  use the CLI for indexing/admin only (`analyze`, `status`, `clean`).
- Use the packet repo from `<gitnexus_status><repo>` and run the packet's
  required checks first. Broader GitNexus output never shrinks the packet
  surface, PR contract, or no-change surfaces.

Before edits, on each changed target:

- `impact(direction="upstream", includeTests=true)` before editing indexed
  symbols or shared contracts; `direction="downstream"` too when behavior is
  moved, deepened, consolidated, or hidden behind an Interface.
- `context` for callers AND callees — `impact` walks callers only, and what a
  change actually breaks is usually a callee: the shared writer, lock, or
  transition helper the edited symbol calls.
- On shared state — the same table, row, file, lease, claim token, or
  transition helper — `context` every symbol that mutates it and compare risk
  ratings, not only the one being edited. Two writers to one row is the case a
  single upstream impact call always misses, and the second writer is
  routinely the higher-risk one.
- Consuming an internal seam from a NEW file (tests, smokes, harnesses,
  scripts) requires `context` on that seam BEFORE writing the consumer — a new
  file has no indexed symbols, so the edit-time impact rule never fires.
  Import the existing tested owner of the behavior instead of writing a second
  parsing/lifecycle client.

After edits, re-analyze when the change touched indexed symbols, shared APIs
or contracts, persistence, config/runtime/deploy surfaces, external
integrations, browser automation, transaction-sensitive flows, or the index is
stale — not for docs-only work or tiny leaf edits:


```bash
gitnexus analyze --skip-agents-md .
gitnexus status
```

Then run `gitnexus_detect_changes(repo="<source-checkout-repo>",
scope="unstaged")` on the source-checkout repo from `gitnexus status`. The
result is supplemental post-edit graph evidence only; unavailable MCP for a
required check is a blocker or a narrow named exception, never a silent skip.

Keep `.gitnexus/` out of commits unless the repo intentionally tracks an
ignore rule for it.
