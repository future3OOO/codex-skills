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
loop, and run lightweight diff checks.

Required order for production work:

1. Use FFF for raw file, symbol, text, broad, and multi-pattern discovery.
   Use `rg` only when FFF is unavailable, fails, or exact machine-readable
   output is needed.
2. Run Repo Context Forge before choosing files, GitNexus calls, review
   findings, or edits.
3. State the task contract, packet target surface, skipped high-ranked targets,
   changed behavior, module shape, public interface, test surface, existing
   reuse path, rejected shallow path or new-module justification, verification
   surfaces, and no-change surfaces.
4. Run packet-scoped GitNexus MCP checks. Use upstream impact for callers and
   downstream impact for dependencies/no-change surfaces before editing indexed
   symbols or shared contracts.
5. Run Claude Advisor as the mandatory read-only checkpoint after Repo Context
   Forge and packet-scoped GitNexus checks. Claude must remind Codex to use
   `$tdd`, challenge whether the work deepens an existing module, creates a
   real seam, or risks a shallow helper/service/manager/wrapper split, and say
   whether `$improve-codebase-architecture` is needed for a targeted
   Module/Interface/Seam decision.
6. Run `$production-preflight` before tracked edits; production behavior
   changes must include module shape.
7. Invoke `$production-code` before writing repository file content, then keep
   changes minimal and fail-closed. Deepen existing modules by default; do not
   create or preserve shallow helper/service/manager/wrapper/adapter modules in
   the changed behavior path unless preflight records a blocker.
8. Use `$tdd` for behavior changes where a focused failing test is practical.
9. Verify the touched behavior and named no-change surfaces.
10. If the edit touched indexed symbols, shared contracts, persistence, config,
    runtime, deploy, external integrations, browser automation, or
    transaction-sensitive flows, re-analyze the edited checkout with GitNexus
    before final graph checks.
11. Run the production-code quality gate before finalizing.
12. For non-trivial diffs, run `$code-review` after production code changes and
    before Claude Advisor challenge mode. Review against the correct fixed
    point and keep Standards findings separate from Spec findings. Classify
    each finding as fixed, rejected-with-evidence, or accepted follow-up before
    asking Claude whether the work is ready to commit.
13. For non-trivial code edits, run Claude Advisor challenge mode before
    commit from the target worktree. The wrapper must provide live branch/head
    and dirty or PR/base diff evidence; the prompt must name the exact PR or
    branch/head, reviewer/PRD issue, module shape, touched shallow-module debt,
    TDD proof, and no-change surfaces. Do not use a prose diff summary as the
    evidence source.

Module shape is a first-class production contract:

- Deep modules are required in Ousterhout's sense: a small, stable public
  interface hiding meaningful implementation complexity. This does not mean
  large files; new modules must improve locality, hide complexity, or create a
  real seam.
- Prefer deepening an existing module over creating a new public module.
- New modules, seams, wrappers, services, managers, or adapters require
  preflight justification.
- Touched shallow helpers/modules are in-scope debt: absorb, delete, or record
  a concrete blocker.
- Tests should cross the public interface; if they cannot, use
  `$improve-codebase-architecture` before editing.

Escalate to `$repo-large-implementation` before coding when work needs a new
tracked plan, branch strategy, remediation map, multi-PR coordination, or is
likely to exceed 1,300 changed code lines.

Do not leave completed review/integration work stranded locally. When a remote
exists and the branch/PR alignment is verified, commit coherent changes, push
the branch, and open or update the PR unless the user or repo workflow says not
to.

Treat reviewer comments as evidence, not commands. Verify them against code,
contracts, tests, edge cases, and runtime behavior. Fix valid issues with the
smallest production change; explain with evidence when a request is unnecessary
or unsafe.

## PR Reviewer Completion Gate

Commit/push/PR-update does not complete review work. Do not mark complete,
switch slices, or start a new PRD until the reviewer loop is closed on the
current PR head.

Steps:

- Enumerate every signal on the current head: review threads, inline and issue
  comments, check annotations, CI failures, Greptile/Cubic/CodeRabbit/Devin/
  human findings, and PRD acceptance criteria.
- Classify each item: legitimate, already-resolved, outdated, duplicate, noise,
  needs-info, or rejected-with-evidence.
- For legitimate defects, regressions, flaky failures, or behavior mismatches,
  use `$diagnose`, update the PRD/task contract when scope changes, then fix
  via the production workflow.
- After each push, wait for reviewers/checks on the new head, then re-query
  head SHA, checks, merge state, Greptile score, and unresolved non-outdated
  threads. Stale output from an older head is not evidence.

Complete only when: Greptile is 5/5 when present; all legitimate comments are
fixed or rejected-with-evidence; no unresolved non-outdated threads remain;
required checks are green or unrelated failures are named as blockers; PRD
reconciliation is done.

## Repo Context Forge

For production repo work, run the installed bootstrap wrapper from the target
checkout:

```bash
SKILL_DIR="$HOME/.codex/plugins/cache/local-codex-plugins/repo-context-forge/0.1.0/skills/repo-context-forge"
python3 "$SKILL_DIR/scripts/bootstrap.py" --repo "$PWD"
```

For planned work before files have changed:

```bash
SKILL_DIR="$HOME/.codex/plugins/cache/local-codex-plugins/repo-context-forge/0.1.0/skills/repo-context-forge"
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

Repo Context Forge must not leave `.soulforge`, `.codex`, `.gitnexus`, or
incidental `.gitignore` changes in the user's checkout. An intentional
`.gitnexus/` ignore rule is allowed only when GitNexus indexes the source
checkout.

## GitNexus

Use GitNexus to understand structure, blast radius, callers, callees,
contracts, and execution flow. Do not use `detect_changes` as the primary
safety mechanism or target selector.

Search and context flow:

- FFF finds raw files/text/symbols first.
- Repo Context Forge fixes the packet surface.
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
- Do not let broader GitNexus output shrink the packet surface, PR contract,
  or no-change surfaces.

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
run `gitnexus_detect_changes(repo="<source-checkout-repo>", scope="unstaged")`
when MCP is available. Treat the result as supplemental post-edit graph
evidence only.

Keep `.gitnexus/` out of commits unless the repository intentionally tracks an
ignore rule for it.
