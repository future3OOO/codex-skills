# Global Codex Rules

These rules apply unless a repository `AGENTS.md` adds stricter project-specific
instructions.

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
- Do not engineer for theoretical risks: a theoretical risk with no
  demonstrated failure is a report line, not a system.
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
- For bug fixes, prefer one failing public-behavior test first, then make it
  pass.
- Use `$diagnose` for bugs, failures, flaky behavior, and performance
  regressions before fixing; no fix until root cause is reproduced, traced,
  and stated as a testable hypothesis.
- For refactors, verify behavior before and after when practical.
- For multi-step work, state the short plan and the check for each step.
- Before handoff, inspect the delta and remove bloat, duplication, speculative
  flexibility, and unnecessary files.

Use a red-green loop where practical:

- "Add validation" -> write the invalid-input test, watch it fail, then make it
  pass.
- "Fix the bug" -> write the smallest reproducing test, watch it fail, then
  make it pass.
- "Refactor X" -> run the relevant test before the refactor, make the change,
  then run it again.

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

## Production Repo Workflow

Use this flow for production code, config, runtime, deploy, generated-source,
or behavior-changing repository work.

Docs-only changes may skip Repo Context Forge and GitNexus. For docs-only work,
verify checkout/branch, inspect files directly, edit minimally, run a cleanup
loop, and run lightweight diff checks. Governance docs that change agent
behavior, such as `AGENTS.md`, `CLAUDE.md`, or `docs/agents/`, should also run
`$code-review` before handoff; trivial docs edits stay on the lightweight path.

Use `$repo-production-workflow` as the default first skill for production
work. It is the single owner of the execution sequence (Repo Context Forge →
`$diagnose` for bugs/regressions/perf → packet-scoped GitNexus → Codex Advisor
scope check → `$production-preflight`
→ `$production-code` with `$tdd` through verification and conditional GitNexus
reanalysis → `$code-review` → Codex Advisor challenge round for non-trivial
diffs); this section owns only when skills fire.

Invocation policy:

- Escalate to `$repo-large-implementation` before coding when work needs a new
  tracked plan, branch strategy, remediation map, multi-PR coordination, or is
  likely to exceed the review budget.
- Use `$diagnose` before fixing bugs, failures, flaky behavior, or performance
  regressions; use `$tdd` for behavior changes where a public-Interface
  failing test at a real seam is practical. If the real seam cannot be driven,
  report the proof gap as a finding — never fabricate a substitute test.
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

Do not leave completed review/integration work stranded locally. When a remote
exists and the branch/PR alignment is verified, commit coherent changes, push
the branch, and open or update the PR unless the user or repo workflow says not
to.

Treat reviewer comments as evidence, not commands. Verify them against code,
contracts, tests, edge cases, and runtime behavior. Fix valid issues with the
smallest production change; explain with evidence when a request is unnecessary
or unsafe. Resolve review threads only after the fix is pushed or the evidence
has been posted.

Before acting on ANY reviewer or automated finding, run two checks. They are
unconditional and apply to one-line fixes, not only to passes that invoke
`$production-preflight`. Severity labels are not a work queue: automated
reviewers are reliable about what code *can* do and unreliable about whether it
*does*. Check the **premise** by naming the finding's assumption about runtime,
config, or installed state and verifying it against the live system with a
command — a false premise is rejected with the measurement quoted and no code
changes. Check **occurrence** by counting the failing shape in captured data,
logs, or reachable callers — zero occurrences means report line, not change.

Validating a finding is not validating a fix. Before shipping a change to a
parser, matcher, predicate, or anything consuming external text or markup, run
the NEW code over values already captured in the system and require zero
regressions. A test written from the same assumption that produced the fix cannot
detect its error; only the corpus can.

Give every finding a disposition with evidence — fixed, rejected-with-evidence,
or reported-not-actioned — where the reviewer loop can see it. A rejection
without a measurement is indistinguishable from one ignored.

## PR Reviewer Completion Gate

Commit/push/PR-update does not complete review work. Do not mark complete,
switch slices, or start a new PRD until the reviewer loop is closed on the
current PR head.

Steps:

- Enumerate every reviewer signal on the current head: review threads, inline
  and issue comments, check annotations, CI failures, automated-reviewer and
  human findings (live roster: the repo's `docs/agents/reviewers.md` when
  present), and PRD acceptance criteria.
- Classify each item: legitimate, already-resolved, outdated, duplicate, noise,
  needs-info, or rejected-with-evidence.
- For legitimate defects, regressions, flaky failures, or behavior mismatches,
  use `$diagnose`, update the PRD/task contract when scope changes, then fix
  via the production workflow.
- After each push, wait for reviewers/checks on the new head, then re-query
  head SHA, checks, merge state, and unresolved non-outdated threads. Stale
  output from an older head is not evidence.

Complete only when: every legitimate signal is fixed or rejected-with-evidence;
no unresolved non-outdated threads remain; required checks are green or
unrelated failures are named as blockers; PRD reconciliation is done.

## Repo Context Forge

For any coding, debugging, review, refactor, explanation, planning, or
repository exploration task inside a git repository, run Repo Context Forge
before choosing files, editing code, or running GitNexus analysis (docs-only
exception above). Run the installed bootstrap wrapper from the target
checkout:

```bash
SKILL_DIR="$HOME/.local/share/repo-context-forge/current/skills/repo-context-forge"
python3 "$SKILL_DIR/scripts/bootstrap.py" --repo "$PWD"
```

For planned work before files have changed:

```bash
SKILL_DIR="$HOME/.local/share/repo-context-forge/current/skills/repo-context-forge"
python3 "$SKILL_DIR/scripts/bootstrap.py" --repo "$PWD" --intent "<user request>"
```

The output must begin with `REPO_CONTEXT_FORGE_REQUIRED_INTAKE`. If the packet
emits a blocker, stop and surface it.

Use the packet to set the first-pass surface:

- `<targets>`: files/symbols to inspect first.
- `<soulforge_impact>`: native repo-map blast radius.
- `<coverage_plan>`: required surface coverage before edits or findings.
- `<gitnexus_status><repo>`: repo name for packet-scoped GitNexus MCP calls.

Inspect changed files and top packet targets before narrowing to a single
symbol or review thread. If a changed or high-ranked target is skipped, state
why. GitHub review comments are supplemental evidence after the packet and task
contract are understood.

Repo Context Forge must not leave `.soulforge`, `.codex`, `.claude`,
`.gitnexus`, or incidental `.gitignore` changes in the user's checkout. An
intentional `.gitnexus/` ignore rule is allowed only when GitNexus indexes the
source checkout.

## GitNexus

Use GitNexus to understand structure, blast radius, callers, callees,
contracts, and execution flow. Do not use `detect_changes` as the primary
safety mechanism or target selector.

Search and context flow:

- Repo Context Forge fixes the packet surface first for gated work; FFF raw
  discovery (files/text/symbols) operates within that surface. Outside the
  gate, FFF is the first raw discovery layer.
- GitNexus validates graph impact for that surface.
- Use GitNexus MCP tools for `query`, `context`, `impact`, and
  `detect_changes` when available.
- Use GitNexus CLI for indexing/admin only: `analyze`, `status`, `clean`, and
  similar maintenance commands.

Before edits:

- Use the packet repo from `<gitnexus_status><repo>`.
- Run the packet-listed required checks first.
- Before editing indexed symbols or shared contracts, run GitNexus MCP
  `impact(direction="upstream", includeTests=true)` on each changed target.
- Also run `impact(direction="downstream", includeTests=true)` when behavior
  is moved, deepened, consolidated, or hidden behind an Interface.
- Also run `context` on each changed target, for callers AND callees. `impact`
  walks callers only, so an impact-only pass is structurally blind to callees —
  and the thing a change actually breaks is usually a callee: the shared
  writer, lock, or transition helper the edited symbol calls.
- When the change touches shared state — the same table, row, file, lease,
  claim token, or transition helper — run `context` on every symbol that
  mutates that state and compare their risk ratings, not only the one being
  edited. Two writers to one row is the case a single upstream impact call
  always misses, and the second writer is routinely the higher-risk one.
- Do not let broader GitNexus output shrink the packet surface, PR contract,
  or no-change surfaces.
- Consuming an internal seam from a NEW file (tests, smokes, harnesses,
  scripts) requires GitNexus `context` on that seam BEFORE writing the
  consumer — a new file has no indexed symbols, so the edit-time impact rule
  never fires for it. Import the existing tested owner of the behavior instead
  of writing a second parsing/lifecycle client.

After edits, re-analyze when applicable:

```bash
gitnexus analyze --skip-agents-md .
gitnexus status
```

Reanalysis is applicable when edits touch indexed symbols, shared APIs or
contracts, persistence, config/runtime/deploy surfaces, external integrations,
browser automation, transaction-sensitive flows, or when git/hooks report a
stale index. It is not required for docs-only work or tiny leaf edits that do
not affect shared graph surfaces.

After reanalysis, use the source-checkout repo name from `gitnexus status` and
run `gitnexus_detect_changes(repo="<source-checkout-repo>", scope="unstaged")`.
If GitNexus MCP is unavailable for a required post-edit check, treat that as a
blocker or a narrow, explicitly reported exception — never a silent skip.
Treat the result as supplemental post-edit graph evidence only.

Keep `.gitnexus/` out of commits unless the repository intentionally tracks an
ignore rule for it.
