---
name: tdd
description: Test-driven development with red-green-refactor loop. Use when user wants to build features or fix bugs using TDD, mentions "red-green-refactor", wants integration tests, or asks for test-first development.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one failing behavior test before production code changes.

The test must fail for the expected product/code reason. If it passes immediately, errors because of invalid setup, or only proves implementation shape, it is not a valid RED gate. A tautological test — one that asserts a mock you configured or restates the implementation — can never go RED for a product reason; rewrite it at a real Seam.

## Philosophy

**Core principle**: Tests should verify behavior through the public Interface,
not Implementation details. Code can change entirely; tests shouldn't. Use the
Module / Interface / Seam vocabulary from `$codebase-design`.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to Implementation. They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the Interface). The warning sign: your test breaks when you refactor, but behavior hasn't changed. If you rename an internal function and tests fail, those tests were testing Implementation, not behavior.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking
guidelines.

## Seams — where tests go

A **Seam** is the public boundary you test at: the Interface where behavior is
observed without reaching inside. Tests live at Seams, not internals.

Test only at agreed Seams. Before writing a test, name the Seam under test and
confirm it when the request leaves room for interpretation. Ask: "What is the
public Interface, and which Seam should this test cross?"

## Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" - treating RED as "write all tests" and GREEN as "write all code."

This produces **crap tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes - they pass when behavior breaks, fail when behavior is fine
- You outrun your headlights, committing to test structure before understanding the implementation

**Correct approach**: Vertical slices via tracer bullets. One test → one implementation → repeat. Each test responds to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matters and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Workflow

### 1. Planning

When exploring the codebase, use the project's domain glossary so that test names and Interface vocabulary match the project's language, and respect ADRs in the area you're touching.

Before production edits:

- [ ] Inspect existing test style and test commands
- [ ] Confirm with user what Interface changes are needed
- [ ] Confirm with user which behaviors to test (prioritize)
- [ ] Identify the public Interface or observable workflow being changed
- [ ] Identify whether the current Module/Interface is testable
- [ ] Identify opportunities for deep Modules using `$codebase-design`
- [ ] Design Interfaces for testability using `$codebase-design`
- [ ] List the behaviors to test (not implementation steps)
- [ ] Name the first behavior slice to prove
- [ ] For non-trivial work, list remaining behavior slices
- [ ] Get user approval on the plan

Ask: "What should the public Interface look like? Which behaviors are most important to test?"

**You can't test everything.** Confirm with the user exactly which behaviors matter most. Focus testing effort on critical paths and complex logic, not every possible edge case.

### Architecture/Testability Gate

If a behavior cannot be tested cleanly through a public Interface, requires mocking internal collaborators, or coordinates several shallow Modules, do not force a bad test.

Stop and use `$codebase-design` to inspect the Module, Interface, Seam, and
deepening opportunity. Use `$improve-codebase-architecture` when the decision
requires a repo scan or multiple candidate refactors. TDD should prove behavior
through a good Interface; it should not normalize shallow Modules.

When `$diagnose` ran first, consume its surface map. The failing test should cross the mapped Interface at a real Seam; do not regenerate the diagnose map here. If no real Seam exists, escalate to `$improve-codebase-architecture` instead of mocking the Module under test.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior -> test fails for expected reason
GREEN: Write minimal code to pass -> test passes
```

This is your tracer bullet - proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test -> fails for expected reason
GREEN: Minimal code to pass -> passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior

### 4. Refactor

After all tests pass, look for refactor candidates through `$codebase-design`:

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply the deletion test to shallow pass-through Modules
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public Interface only
[ ] Test would survive internal refactor
[ ] Code is minimal for this test
[ ] No speculative features added
```

## Proof Gates

Before completion, report:

- RED: targeted command and expected failure observed
- GREEN: targeted command passed after the smallest production change
- REGRESSION: broader relevant suite passed, or strongest practical substitute with reason
- REFACTOR: only performed while tests were green
