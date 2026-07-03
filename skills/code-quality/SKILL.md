---
name: code-quality
description: Enforce production code quality before finalizing any code change. Use for implementation, refactors, bug fixes, and review tasks to prevent code bloat, duplicated logic, fake-green bypasses, and missing cleanup.
---

# Code Quality

Apply these rules before returning work as complete.

## Non-negotiable Rules

1. Keep code minimal.
- Make the smallest correct change.
- Remove dead code instead of hiding it.
- Avoid one-off wrappers and premature abstractions.

2. Eliminate duplication.
- Reuse existing utilities when behavior is equivalent.
- Consolidate repeated logic into one path only when it is truly shared.

3. Preserve direct data flow.
- Remove unnecessary hops, transforms, and retries.
- Keep I/O and control flow explicit and traceable.

4. Ban fake-green shortcuts.
- Do not suppress failures to make checks pass (`ignore`, blanket disables, swallowed exits).
- Fix root causes instead of muting tools.

5. Enforce cleanup discipline.
- Clean temporary artifacts on startup, before new work, and after completion.
- Treat orphaned files, leftover branches, and leaked state as failures.

6. Keep changes consequence-aware.
- Trace downstream consumers when changing data shapes, limits, or contracts.
- Update all affected paths in the same change.
- For stateful or contract-sensitive edits, define the contract, map the adjacent perimeter, and prove at least one full-surface behavior.

7. Keep implementation simple.
- Prefer readable, direct code over verbose generated patterns.
- Avoid redundant comments and obvious boilerplate.
- Prefer one combined workflow proof plus a few sharp invariant checks over a large pile of tiny low-signal tests.

## Language Rules

### Python
- Avoid `# type: ignore` unless unavoidable and explicitly justified.
- Avoid broad exception swallowing (`except:`, `except Exception: pass`).
- Keep type hints on public function boundaries.
- Validate external inputs at trust boundaries.

### TypeScript/JavaScript
- Avoid `any`/unsafe casts as a shortcut.
- Validate untrusted external data at boundaries before use.
- Prefer explicit types and narrowing over assertion chains.

## Execution Checklist

1. Inspect the delta and remove unnecessary additions.
- `git diff --stat`
- `git diff`

2. Scan for common quality escapes.
- `rg -n "TODO|FIXME|type: ignore|eslint-disable|@ts-ignore|\\bAny\\b|except\\s*:\\s*$|except\\s+Exception\\s*:\\s*pass" <paths>`

3. Run relevant project gates (lint/typecheck/tests/build) for touched areas.

4. For stateful or contract-sensitive changes, check that proof is not only local:
- require at least one higher-signal workflow or surface proof
- treat low-signal tests that only prove the edited branch shape as weak evidence
- use focused invariant tests only as supplements
5. Confirm cleanup and rollback are explicit for any risky operation.

6. Do not mark done until checks pass and no quality rule is violated.
