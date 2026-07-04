---
name: code-review
description: Review a diff since a fixed point along Standards and Spec axes. Use when the user asks to review a branch, PR, WIP changes, or work since a ref.
---

# Code Review

Review the diff between `HEAD` and a fixed point along two separate axes:

- **Standards** — does the change follow this repo's documented standards?
- **Spec** — does the change implement the originating issue, PRD, or spec?

This skill is advisory. Codex remains responsible for validating findings
against the repo, tests, PRD, reviewers, and production gates. Do not resolve
review threads, commit, push, or mark PR work complete from this skill; its
findings are evidence for the production loop and the PR Reviewer Completion
Gate, never gate state.

## Process

### 1. Pin the fixed point

Use the fixed point the user supplied: commit SHA, branch, tag, `main`,
`origin/main`, or another ref. If none was supplied, default to the PR base
branch when a PR exists, otherwise the merge-base with the branch's upstream or
`origin/main`.

Verify it before reviewing:

```bash
git rev-parse <fixed-point>
git diff --stat <fixed-point>...HEAD
git log --oneline <fixed-point>..HEAD
```

Use three-dot diff form so the comparison is against the merge-base. If the
ref does not resolve or the diff is empty, stop and report that instead of
reviewing.

### 2. Identify the spec source

Look for the originating spec in this order:

1. Issue references in commit messages or branch names; fetch them using
   `docs/agents/issue-tracker.md` for the target checkout.
2. A path or issue the user supplied.
3. A PRD/spec file under `docs/`, `specs/`, or `.scratch/`.
4. If nothing exists, report that the Spec axis has no governing source.

### 3. Identify standards sources

Read repo standards such as `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`,
`CODING_STANDARDS.md`, `CONTEXT.md`, and relevant ADRs. Repo-local standards
override generic smells; they supplement but never weaken stricter global
`AGENTS.md` or production workflow rules.

Always carry this smell baseline as judgement calls, not hard violations:

- **Mysterious Name** — a name that hides purpose.
- **Duplicated Code** — same logic shape repeated in the change.
- **Feature Envy** — code reaches into another module's data more than its own.
- **Data Clumps** — fields or params travel together repeatedly.
- **Primitive Obsession** — strings or primitives stand in for domain concepts.
- **Repeated Switches** — repeated conditionals over the same type.
- **Shotgun Surgery** — one logical change forces scattered edits.
- **Divergent Change** — one module changes for unrelated reasons.
- **Speculative Generality** — abstractions for needs the spec does not have.
- **Message Chains** — callers depend on long navigation chains.
- **Middle Man** — a module mostly delegates onward.
- **Refused Bequest** — inheritance or implementation contract mostly ignored.

### 4. Review both axes

Run Standards and Spec as independent reviews. Keep findings separate so one
axis cannot mask the other.

For **Standards**, report:

- documented-standard violations with file/line evidence
- smell-baseline judgement calls with hunk evidence
- tooling-enforced issues only when the tool is unavailable or skipped

For **Spec**, report:

- requirements missing or partial
- behavior added that the spec did not ask for
- requirements that appear implemented incorrectly

### 5. Aggregate

Lead with findings, ordered by severity within each axis. Do not merge the axes
or pick one overall winner.

Use:

```md
## Standards
- finding...

## Spec
- finding...

## Summary
Standards: N findings. Spec: N findings. Worst Standards issue: ...
Worst Spec issue: ...
```

If there are no findings, say so plainly and name any remaining proof gap.
