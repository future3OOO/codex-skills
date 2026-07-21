---
name: claude-advisor
description: Consult Claude as a one-on-one advisor or explicitly delegated write-capable worker from Codex using the local Claude CLI. Keep Claude read-only unless the user explicitly authorizes `--write` or `--full-tools`; full tool access also requires a delegated git worktree. Mandatory after Repo Context Forge and packet-scoped GitNexus checks in production repo workflow; also use when the user asks Codex to ask Claude or when architecture, migration, correctness, security, concurrency, idempotency, or non-obvious PR/worktree risk needs advisory review.
---

# Claude Advisor

Claude Advisor is a challenge Interface around Codex's work. Codex owns the
decision, implementation, tests, and final report. The advisor supplies
independent pressure against the evidence Codex provides. Claude is the default
advisor provider; Codex can be selected as an explicit fallback when Claude
tokens are unavailable.

Use the wrapper by default:

```bash
/home/prop_/.codex/skills/claude-advisor/scripts/ask-claude-advisor.sh \
  --slug "<stable-task>" \
  --cwd "$PWD" \
  -- "Question: <one focused question>"
```

The wrapper streams the composed prompt to Claude over stdin, so large diffs do
not hit shell or OS argument-length limits.

Use Codex Advisor as the fallback provider without changing the prompt contract:

```bash
/home/prop_/.codex/skills/claude-advisor/scripts/ask-claude-advisor.sh \
  --provider codex \
  --slug "<stable-task>" \
  --phase precommit-challenge \
  --cwd "$PWD" \
  --base-ref origin/main \
  -- "Question: Does the wrapper-provided live diff satisfy the production contract?"
```

`CLAUDE_ADVISOR_PROVIDER=codex` selects the same fallback through the
environment. The default provider remains `claude`.

The advisor provider does not load this skill file. Any rubric it must follow
has to be included in the wrapper phase prompt or in the question sent through
the wrapper.
Changes to checkpoint rules here are inert unless the wrapper phase prompts are
kept in sync.
The wrapper phase prompts at `scripts/ask-claude-advisor.sh` are the
operational source of truth for phase output shape and tool policy.

## Production Checkpoints

In production repo work that uses Repo Context Forge, use Claude twice:

1. **Before code**: after Repo Context Forge and packet-scoped GitNexus checks,
   before `$production-preflight` and before edits.
2. **After code**: after proof for non-trivial edits, before commit or push.

### Before Code: Scope Challenge

Ask whether the Repo Context Forge + GitNexus packet covers the PRD slice,
correct Seams, and correct surface area before production preflight.

Supply:

- task contract and PRD slice outcomes
- Repo Context Forge packet target surface, coverage plan, and skipped high-ranked targets
- packet-scoped GitNexus findings: callers, callees, blast radius, contracts
- intended Module, public Interface, hidden Implementation complexity
- existing reuse path
- new Seam justification, or why the existing Module should be deepened
- touched shallow Module debt
- `$tdd` hypothesis or first failing behavior test
- test surface and named no-change surfaces
- ordering, idempotency, data-loss, security, or regression risks
- Codex's implementation hypothesis

Command:

```bash
/home/prop_/.codex/skills/claude-advisor/scripts/ask-claude-advisor.sh \
  --slug "<stable-task>" \
  --phase preflight-advice \
  --cwd "$PWD" \
  -- "Question: Does the Repo Context Forge + GitNexus packet cover the PRD slice, correct Seams, and correct surface area before production preflight?"
```

The advisor must use `/tdd` and `/improve-codebase-architecture` as read-only
rubric references, remind Codex how `$tdd` applies, and say whether a targeted
Module/Interface/Seam decision is needed before editing. It must challenge
whether the work deepens an existing Module, creates a real Seam, or risks
shallow helper/service/manager/wrapper complexity.

The Interface is the test surface. A new Seam needs a real reason; one Adapter
is usually hypothetical, while two Adapters usually prove the Seam.

### After Code: Diff Challenge

Ask whether the live diff satisfies the PRD slice and production contract
without extra behavior or no-change surface drift.

Supply:

- exact PRD, reviewer issue, or issue tracker item
- branch, base, and head SHA
- TDD proof: RED command/failure and GREEN command/pass
- verification outcomes and any skipped or weak proof
- `$code-review` Standards/Spec findings and their dispositions, when it ran
- changed Module, public Interface, and hidden Implementation complexity
- existing reuse path and touched shallow Module debt
- named no-change surfaces
- Codex's commit-readiness hypothesis

Do not provide a prose diff summary as evidence. The wrapper attaches the live
dirty diff, staged diff, or PR/base diff from `--cwd`; the advisor must critique
that evidence directly.

If the wrapper-provided diff does not match the requested PR, PRD, reviewer
issue, branch, or head SHA, treat Claude answer as a blocker and fix the call
context before relying on it.

Command:

```bash
/home/prop_/.codex/skills/claude-advisor/scripts/ask-claude-advisor.sh \
  --slug "<stable-task>" \
  --phase precommit-challenge \
  --cwd "$PWD" \
  --base-ref origin/main \
  --budget 700 \
  -- "Question: Does the wrapper-provided live diff satisfy the PRD slice and production contract without extra behavior or no-change surface drift?"
```

Expected challenge shape:

- Verdict: commit-ready, fix-before-commit, or context-mismatch
- PRD reconciliation: implemented, missing, extra, and unproven outcomes
- Reviewer coverage: automated and human reviewer findings on the current head (roster: `docs/agents/reviewers.md` when present)
- `TDD check`: whether a vertical red-green loop was shown
- Module shape: public Interface, test surface, deep Module pressure, and any shallow unnecessary helper/service/manager/wrapper split
- `Minimality/bloat`: unnecessary code, duplication, or broad refactor
- `Regression risk`: no-change surfaces needing more proof
- `Action`: one exact next Codex step

Challenge focus:

- exact PRD/reviewer issue resolved, not just adjacent cleanup
- change belongs in the touched slice/worktree
- proof is real behavior proof, not mock-heavy or fake-green coverage
- no broad refactor, duplicate path, stale workaround, or speculative option
- `$improve-codebase-architecture` stayed targeted to Module/Interface/Seam

Use a larger budget for precommit challenges when the advisor must reconcile a real
PRD/reviewer issue against a live diff. Keep simpler advisor questions near the
default budget. The wrapper controls the default; `--budget 700` is illustrative
for real PRD/reviewer reconciliation, not a new default.

On `context-mismatch`, fix `--cwd`, `--base-ref`, branch state, or the exact
issue/PRD context and re-ask. Do not act on the prior answer.

Precommit challenge passing does not complete PR/review work. When a PR has
external or human reviewers, Codex must still pass the `AGENTS.md` PR Reviewer
Completion Gate on the current head.

## When To Ask Claude

Ask the advisor:

- after Repo Context Forge and packet-scoped GitNexus checks in production repo
  work
- before commit for a non-trivial diff that claims to resolve a PRD, reviewer,
  or issue tracker item. Exception: fix-only commits whose every change
  addresses a finding already confirmed in this pass's challenge, code-review,
  or PR reviewer loop; state the skipped round in the final report
- for architecture, migration, correctness, security, concurrency,
  idempotency, data-loss, or non-obvious PR/worktree risk
- when the user explicitly asks for Claude, Codex Advisor, advisor mode, or a
  Claude worker
- when Codex is stuck after two focused attempts

Skip Claude for mechanical edits, formatting, obvious single-file fixes, and
questions the test suite answers directly, unless the user asks for Claude.

## Prompt Contract

Ask one focused question per call. Include:

- `Role`: advisor, read-only, stdout only
- `Question`: bounded and explicit
- `Evidence`: packet, graph result, diff, error, file path, or excerpt
- `Hypothesis`: what Codex currently believes
- `Budget`: usually `<=300 words`; raise only for real review depth

Good questions:

```text
Given this PRD slice, Repo Context Forge packet, GitNexus findings, and Module-shape hypothesis, what is the highest-risk missing surface before production preflight?
Challenge the wrapper-provided live diff against PR #39 head <sha> and this PRD item. What is missing, extra, or under-proven?
Hypothesis: retries are safe because writes are idempotent. Strongest counter-argument with file:line evidence?
Options A vs B. Which fails first under the migration constraint?
```

Avoid broad prompts such as "what do you think?", whole-repo dumps, or
instructions that ask Claude to run Codex's workflow for it.

## Providers

`--provider claude` is the default and preserves the existing Claude CLI path,
including session resume state, `--write`, and `--full-tools`.

`--provider codex` starts a fresh read-only `codex exec` advisor run with the
same wrapper-built prompt, live diff evidence, and phase rubric. It is intended
as an advisory fallback only. It does not support `--write` or `--full-tools`;
those modes remain Claude-only.

Provider environment:

- `CLAUDE_ADVISOR_PROVIDER=codex` or `ADVISOR_PROVIDER=codex`: select Codex.
- `CODEX_ADVISOR_MODEL=<model>` or `--codex-model <model>`: optional Codex
  model override.
- `CLAUDE_ADVISOR_MODEL` and `CLAUDE_ADVISOR_FALLBACK_MODEL`: optional Claude
  provider overrides; both default to `claude-fable-5`.

Codex Advisor is not model-diverse from Codex implementation work, but it is a
separate session with read-only constraints and raw wrapper evidence. Treat it
as an emergency substitute for Claude pressure, not as stronger authority.

## Modes

Keep Claude read-only unless the user explicitly authorizes Claude to modify
files. Do not infer authorization from the task type, production workflow,
worktree availability, or a request merely to consult or test Claude. Use the
wrapper for every authorized write mode.

The wrapper's read-only tool policy is the source of truth:

```bash
--allowed-tools "Read Grep Glob Bash(git diff:*) Bash(git status:*) Bash(git branch:*) Bash(git rev-parse:*) Bash(gh issue view:*) Bash(gh pr view:*) Bash(gh run view:*) Bash(rg:*) Bash(ls:*) Bash(sed:*) Bash(cat:*)"
--disallowed-tools "Edit Write NotebookEdit"
```

`--phase` is only valid in read-only advisor mode. Do not combine it with
`--write` or `--full-tools`.

- `preflight-advice`: before code, after Repo Context Forge + GitNexus
- `precommit-challenge`: after proof, before commit or push

When explicitly authorized, use `--write` only for a bounded edit. The prompt
must name the task, target worktree, allowed tests, and whether commits or
pushes are allowed. Default: no commit or push. Write mode permits `Edit`,
`Write`, and `NotebookEdit`. For multiple changes, let Claude call `Edit` as
often as the task requires. Do not add `MultiEdit` to tool rules unless it
appears in the live `--tools default` inventory; an unknown name produces a
warning.

Write mode still blocks commits, pushes, destructive git operations, `rm`,
`sudo`, package-download commands, and plan artifacts. Codex must inspect
Claude's resulting diff before relying on it. Treat the wrapper as the exact
tool-policy source of truth.

The advisor may use `/tdd` and `/improve-codebase-architecture` only as read-only
rubric references. Do not ask it to invoke heavyweight repo execution
skills, bootstrap scripts, or `/production-preflight` as a substitute workflow;
the advisor should report missing preflight or Module-shape evidence instead.

When explicitly authorized, use `--full-tools` only for delegated worker tasks
in a dedicated git worktree:

```bash
/home/prop_/.codex/skills/claude-advisor/scripts/ask-claude-advisor.sh \
  --slug "<stable-task>" \
  --cwd "/path/to/delegated-worktree" \
  --full-tools \
  -- "Task: Implement <bounded task> in this worktree only. Do not commit or push unless explicitly allowed. Report changed files and verification."
```

Before using `--full-tools`, Codex must ensure:

- the target worktree was intentionally created or selected for Claude
- the base is clear: current main head, current PR head, or a named branch/SHA
- the prompt says Claude owns only that worktree
- the prompt states whether commits or pushes are allowed; default is no
- Codex will inspect Claude's diff before integrating or reporting completion

Do not use `--full-tools` for work Codex has not already scoped with the repo
workflow, or for shared contracts, persistence, deploy/runtime, or risky
external integrations unless the delegation surface is explicit and bounded.

Use `--add-dir` only when Claude needs read-only context outside the target
worktree.

The Claude CLI has no `--cwd` flag. Use the wrapper's `--cwd`; without the
wrapper, `cd` into the target worktree before running `claude -p`.

Without the wrapper, keep Claude read-only, mirror the wrapper policy, and pipe
the prompt over stdin. The tool-list flags are variadic, so a trailing
positional prompt can be consumed as another tool rule:

```bash
printf %s 'Advisor mode. Do not create files. Stdout only. <=300 words. Question: ...' |
  claude -p \
    --model claude-fable-5 \
    --fallback-model claude-fable-5 \
    --output-format text \
    --allowed-tools "Read Grep Glob Bash(git diff:*) Bash(git status:*) Bash(git branch:*) Bash(git rev-parse:*) Bash(gh issue view:*) Bash(gh pr view:*) Bash(gh run view:*) Bash(rg:*) Bash(ls:*) Bash(sed:*) Bash(cat:*)" \
    --disallowed-tools "Edit Write NotebookEdit"
```

Never use `--bare`; it bypasses local auth and reports `Not logged in`. Avoid
`--permission-mode plan`; it can create plan artifacts under `~/.claude/plans`.

## Session Discipline

Use one short stable slug per task, such as `cass` or `issue82`. Reuse it across
preflight advice, follow-up questions, and precommit challenge.

Do not put phase words in the slug: `pre-edit`, `pre-commit`, `review`,
`challenge`, `final`, or `preflight`. Phase belongs in `--phase`, not identity.

Every wrapper call emits one stderr session line with raw slug, normalized slug,
create/resume/fresh mode, session id prefix, phase, and warning state. Claude
stdout remains Claude's answer only.

For the production pair (preflight advice → precommit challenge), reusing the
same slug/session is required so the challenge retains the original scope; if
the stored session is unreachable, replay the full original scope plus the
current diff and label the round a fallback. Outside that pair, resume only
when prior advice is load-bearing; start fresh when the task, repo, branch, or
assumptions changed.

Use `--fresh` only when the current task's stored Claude session is stale or
intentionally reset.

If Claude reports that a stored resume session no longer exists, the wrapper
rotates that task's session ID and retries once as a fresh session. Do not treat
that recoverable local-state condition as a Fable outage.

Existing or previous split sessions are historical local state. Do not migrate,
merge, rename, delete, or reconcile old `.sid` files.

## Reporting

Wrapper success requires a final `exit_code=0` and non-empty advisor stdout.
Startup stderr is metadata, not completion. If a command result has a
`session_id` without `exit_code`, continue that exact command session with the
environment's `write_stdin` or polling operation. If the orchestration layer
yields a cell ID, wait on that exact cell (exposed as `functions.wait` in the
current Codex environment). Repeat until `exit_code` appears. Do not retry or
start a fallback while either handle is live.

Judge failure only from the final result: provider non-zero status or the exact
`error: <provider> advisor returned empty output` line. Warning-only stderr is
not a provider failure when final stdout contains advice.

When the advisor is unavailable or the Codex fallback provider is used, say so
in the final report; a degraded or skipped round is never silent.

Report advisor output as evidence, not authority:

- `Advisor said`: concise summary
- `Codex judgment`: accepted, rejected, or needs verification
- `Action`: exact next step or no change

Do not follow advisor output blindly. Do not let any advisor replace Repo Context Forge,
GitNexus, `$production-preflight`, `$production-code`, `$tdd`, or Codex's final
verification. The advisor should report missing preflight or Module-shape
evidence, not generate substitute preflight artifacts.

Claude write mode does not replace Repo Context Forge, GitNexus checks,
`$production-code`, or Codex's final verification.
