---
name: production-code
description: Enforce production-only implementation standards for this repo. Use when implementing, refactoring, fixing bugs, reviewing code, or tightening tests where the outcome must stay minimal, direct, duplication-averse, fail closed, strictly typed, boundary-validated, cleanup-safe, and fully verified before being called complete.
---

# Production Code

Apply this skill before writing any repository code or file content change, keep it active while implementing, and run its bundled gate before finalizing.
Use `$production-preflight` first on before-edit turns that require explicit preflight.

Before editing, use the standards below to choose the smallest production-safe implementation path. After editing, run the bundled generic gate from the target repo before finalizing:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HOME/.codex/skills/production-code/scripts/code_quality_gate.py" check --repo "$PWD"
```

Use `--base-ref <ref>` for PR or branch work when a review base is known.
When Repo Context Forge or GitNexus evidence is already available, pass it into the gate instead of forcing a new artifact:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HOME/.codex/skills/production-code/scripts/code_quality_gate.py" check --repo "$PWD" --repo-context-packet <path-or-> --gitnexus-context-json <path-or->
```

## Core Standard

- Ship production code only.
- Make the smallest correct change.
- Delete lines that do not directly serve the requirement.
- Remove dead code instead of hiding it behind flags or wrappers.
- Extend an existing correct path before adding a new branch or abstraction.
- Prefer deepening an existing Module over creating a new public Module.
- Use Ousterhout-style Depth: a small, stable public Interface hiding meaningful Implementation complexity. File size is not the measure.
- A new public Module or Seam must earn its Interface by hiding complexity, improving Locality, or supporting real variation across callers, Adapters, or test surfaces.
- Do not add orchestration layers, control-plane hops, or indirection that the requirement does not need.
- Prefer readable, direct code over verbose generated patterns.
- If the current work is governed by a tracked plan or review artifact that includes an execution checklist, follow that artifact during implementation instead of drifting to an unwritten plan.

## Non-Negotiable Rules

- Eliminate duplication.
- Reuse existing utilities when behavior is equivalent.
- Keep one implementation per behavior.
- Delete new helper functions, loops, or adapters that reimplement an existing path; call or narrowly extend the existing path instead.
- Do not add shallow helper, service, manager, wrapper, or adapter modules that only pass through, rename, split, expose many public names, or orchestrate existing behavior.
- If a shallow helper/module is inside the changed behavior path, absorb it into the deeper module or record a concrete blocker explaining why it cannot be safely changed in this PR.
- Keep private helpers behind the existing module interface unless preflight justifies a new public seam.
- Preserve direct data flow.
- Keep I/O and control flow explicit and traceable.
- Never fake green.
- Never use `|| true`, swallow-and-continue flows, blanket catch/pass, or suppression that hides a real failure.
- Never leave `TODO`, `FIXME`, `HACK`, placeholder stubs, dummy implementations, fake adapters, or temporary bypasses in shippable code.
- Treat uncertainty as a stop-and-verify condition, not a reason to guess.
- Treat review comments as evidence to verify against the code and contract, not authority to obey blindly.

## Data, Types, and Boundaries

- Validate untrusted inputs at the boundary.
- Treat network, HTTP, env, Firestore, Pub/Sub, Chat, Gmail, Cloud Tasks, and third-party payloads as `unknown` until validated.
- Validate once at the boundary with one consistent schema path, then convert to typed internal DTOs.
- Re-check invariants at state-mutation boundaries.
- Do not pass raw provider payloads deeper into the system.
- Do not silently default required fields.

## TypeScript Rules

- Keep strict TypeScript enabled.
- Require `strict: true`, `noImplicitAny: true`, `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true`, and `useUnknownInCatchVariables: true`.
- Do not use `any`.
- Do not use `// @ts-ignore`, `// @ts-expect-error`, or `eslint-disable` unless explicitly documented in `DECISIONS.md`.
- Do not use broad `unknown as X` casts as a shortcut.
- Do not use non-null assertions on external or persisted data.
- Prefer explicit narrowing and exhaustiveness over assertion chains.
- Use exhaustive `switch` statements for state machines and discriminated unions.
- Make unreachable defaults fail closed.

## State Mutation Discipline

- Keep business rules separate from persistence calls.
- Do not spray ad-hoc persistence updates through handlers.
- Route critical state changes through dedicated transition helpers or structured service methods.
- Enforce preconditions and postconditions inside each transition helper.
- Use transactions for critical multi-document state changes when needed.
- Update `updated_at` on every successful transition.
- Make failed transitions observable in logs and audit paths where appropriate.

## Affected-Surface Rewalk Rule

For every code change:

- re-walk the real affected surface before treating the fix as complete
- do not model the issue as only the edited file or the named review comment
- re-check adjacent consumers, callers, and no-change surfaces that could regress
- require proof that the surrounding surface still behaves correctly

Keep this proportional for ordinary work, but do not skip it.

## Transaction-System Rewalk Rule

When a change touches claim tokens, leases, compare-and-set/version fields, transition helpers, or replay/finalize/recovery semantics:

- re-walk the full affected transaction system before treating the fix as complete
- do not model the issue as only the local file or record named in the review comment
- re-check adjacent interleavings that can cross the mutation boundary after prepare but before finalize
- re-check projection, replay, recovery, and no-op paths that share helpers or state fields
- do not reuse one helper for real mutation and projection/recovery semantics when their invariants differ
- do not treat resolved review threads as proof

Required proof for transaction-sensitive work:

- one combined workflow proof over the affected surface
- focused invariant checks for adjacent no-change surfaces
- focused invariant checks should usually cross at least one adjacent dependency or state boundary unless the change is a genuinely pure helper
- at least one proof that stale or secondary execution cannot reach the real external mutation boundary

## Retries, Cleanup, and Dependencies

- Bound every retry by attempts or time.
- Make retries observable with logs and counters.
- Provide an explicit fail path or dead-letter path for retry loops.
- Leave no orphaned temp state, leaked leases, or silent leftovers.
- Keep startup, pre-task, and post-task cleanup deterministic.
- Do not add a new package if the standard library or an existing package already solves the problem cleanly.
- Do not introduce a second package manager or second lockfile.

## Execution Checklist

- Before writing code, including untracked files, scratch implementation files, generated source, or a new worktree, identify the existing path to extend, public Interface, test surface, minimum changed surface, and code that must be deleted or reused to avoid a second implementation.
- Inspect the delta and remove unnecessary additions.
- Scan for common quality escapes such as `TODO`, `FIXME`, `eslint-disable`, `@ts-ignore`, and broad catch/pass patterns.
- Run the bundled production code quality gate.
- If the gate reports errors or actionable warnings, go back to the code, remove the bloat or quality escape, and rerun the gate.
- If the gate reports `reuse-existing-helpers`, inspect the candidate existing path first. Delete the new duplicate helper, loop, retry, parser, normalizer, formatter, resolver, validator, mapper, or adapter unless there is a concrete behavior difference that justifies one implementation per behavior.
- For reuse warnings, use the emitted `gitnexusQueries` when GitNexus MCP is available, or inspect callers/callees with local search before deciding. The optimized gate suppresses weak speculative matches, so remaining reuse warnings should be treated as actionable until disproven.
- Treat touched shallow modules as in-scope debt: absorb, delete, or record the blocker before finalizing.
- Do not finish the turn while duplicate added code, reimplemented existing helpers, unnecessary growth, fake-green suppressions, broad catch/pass, temp artifacts, or cleanup failures remain in the changed production surface.
- Treat the gate as changed-scope evidence, not as a substitute for the repo's own lint, typecheck, tests, build, and domain-specific quality gates.
- If a tracked governing plan or review artifact exists for the current work and includes an execution checklist, update it when execution state materially changes.
- Material changes include:
  - checklist progress
  - active branch or PR state
  - superseded or dropped items
  - changed execution order
  - remaining blockers or follow-ups
- Do not create busywork edits for every tiny code change, but do not leave the governing artifact stale after a meaningful implementation pass either.
- If the current work targets an existing PR branch, do not treat local changes as complete:
  - commit the changes
  - push the branch
  - only then resolve review threads as fixed
- For every code change, compare the final code and proof against the affected-surface map before calling the work clean.
- If `$diagnose` produced Surface Map B, treat this as the final gate of record for reconciling every mapped surface: each surface is changed-with-evidence or no-change-with-reason, and residual risk is stated.
- Compare the final diff against the preflight Module shape; delete or inline shallow wrappers/helpers and verify tests cross the public Interface.
- For transaction-sensitive work, compare the final code and proof against the preflight transaction map before calling the work clean.
- Run the repo's canonical install, lint, typecheck, unit, integration, build, and quality gates for touched areas before calling work complete.
- Keep changed code paths at or above the repo coverage gate.
- Add explicit tests for critical control loops even if coverage already passes.
- For every code change, proof must cover the real affected surface rather than only the local branch or helper.
- For transaction-sensitive work, add one combined workflow proof plus sharp invariant checks; local branch-only tests are not enough on their own.
- Fix root causes, not symptoms.
- Do not mark work done while blockers, follow-ups, dead-letter gaps, retry gaps, or state-regression risks remain.
- Do not present PR remediation as complete while the fix exists only locally or while review threads were resolved ahead of the pushed fix.
- Closure notes must include: summary, commands run, key outcomes, test classes exercised, and blockers or follow-ups.

## Bundled Gate Policy

The script is generic and risk-calibrated across JavaScript, TypeScript, Python, shell, and common source files.

- Hard failures include merge conflict markers, temporary artifacts, duplicate added blocks, high-confidence reimplementation of existing helpers or loops, fake-green suppressions, empty or broad catch/pass patterns, unsafe `any`/cast shortcuts, TODO/FIXME/HACK in changed source, and high-confidence bloat.
- Quality escape checks are path-aware: production source stays strict on `Any`/`any`, casts, suppressions, broad catch/pass, TODO/FIXME/HACK, and fake-green patterns; test source may use ordinary `Any` annotations for fakes but still fails fake-green suppressions, broad catch/pass, TODO/FIXME/HACK, and `|| true`.
- Reuse detection is candidate-first and indexes only relevant tracked production source. It skips tests/fixtures/generated paths, suppresses likely moves/refactors, and treats generic names such as `handler`, `main`, and `run` as insufficient by themselves.
- Reuse warnings must be actionable. Weak single-token, cross-domain, or action-only name overlaps are suppressed rather than reported as speculative work.
- Duplicate and bloat reporting is de-noised: repeated rolling-window matches are grouped, and each bloated file reports the most specific growth error instead of overlapping generic errors.
- Optional `--repo-context-packet <path|->` and `--gitnexus-context-json <path|->` inputs can raise confidence for already-identified affected files or caller-backed symbols. The gate still remains non-mutating and writes no reports, caches, or repository artifacts.
- The gate includes benchmark-calibrated implementation budget tests. They hard-fail clear outliers and require explicit justification for future module, function, or total implementation growth above review-trigger thresholds.
- Bloat checks apply to changed production source only. They warn before hard-failing moderate growth, hard-fail very large new files, and force already-large files to avoid further growth.
- The gate emits six hard-rule results: `codeVolume`, `noDuplication`, `shortestPath`, `cleanup`, `anticipateConsequences`, and `simplicity`.
- Legacy debt outside the changed scope should not block this gate; touched debt should be fixed unless it is clearly outside the requested change and safer to report as a blocker.
