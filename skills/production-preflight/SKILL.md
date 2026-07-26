---
name: production-preflight
description: Produce a compact before-edit proof before risky tracked production changes. Use for review-comment fixes, open PR work, multi-file changes, behavior bugs, release/build/deploy changes, auth/data/filesystem/runtime/external boundaries, transaction-sensitive state, or governed execution plans.
---

# Production Preflight

Use this skill when tracked edits need explicit proof that the edit boundary, governing contract, implementation path, and verification are understood before code changes. It is a scoping gate, not a replacement for TDD or `production-code`.

## When To Use

Use for:

- review-comment remediation or already-open PR work
- multi-file implementation, refactor, or bug-fix work
- behavior changes where the obvious fix may be too local
- release, build, packaging, deployment, or installer changes
- auth, data, filesystem, runtime, connector, automation, or external API boundaries
- stateful or transaction-sensitive logic: leases, claim tokens, compare-and-set/version fields, transition helpers, replay, recovery, finalize, queue or worker state
- work governed by a tracked plan, review artifact, architecture map, or PR order

## When To Skip

Skip isolated typo/docs edits, formatting-only changes, simple version bumps, generated output, and single-line test expectation cleanup unless the user asked for preflight or risk is present.

If you cannot quickly name the safe edit boundary, use preflight.

## Core Rules

- Verify uncertain facts before editing.
- Treat review comments as evidence, not automatic authority.
- Prefer root-cause fixes and existing code paths.
- For behavior bugs, require the reproduced symptom, traced root cause, and testable hypothesis before editing; if any are missing, run `$diagnose`.
- Complete preflight before tracked edits, staging, commits, or resolving review threads.
- If a blocker is required to edit safely, stop and put it in `risksAndQuestions` or `openQuestions`.
- Do not write retrospective preflight.
- If new facts invalidate the preflight, refresh the affected fields before continuing.

## Existing PR Rule

When editing an already-open PR:

- treat the live PR head as authoritative
- verify the checkout path being edited
- record PR number, branch, checkout path, live PR head SHA when known, local `HEAD` SHA, and attached/detached state
- realign stale, detached, or wrong-SHA checkouts before editing
- do not commit from a stale detached review worktree

## Governing Artifacts

When a tracked plan or review artifact governs the work:

- name that artifact in the preflight
- stay inside its owner slice, PR order, and verification boundary
- refresh the artifact or block if the requested change no longer fits it

Do not use preflight to silently fork away from the governing execution document.

## Repo Intelligence

When repo-index or impact tools are available and current, use them to verify flow assumptions for named symbols or cross-module changes. If an index is stale, refresh it before relying on it or fall back to direct source inspection. For indexed/shared surfaces, record GitNexus impact target, direction, risk, d=1 items, affected processes, and graph-derived no-change surfaces.

## Surface Rule

Every preflight-worthy code change must name:

- the real behavior or boundary being changed
- adjacent consumers, callers, and no-change surfaces that could regress
- the authoritative contract that must remain true
- the invariants or observable conditions that prove the contract still holds
- proof that checks the surrounding surface, not only the edited file

Keep this proportional. Ordinary work should be short.

## Behavior Bug Root-Cause Gate

For behavior bugs, preflight proof must name:

- reproduced symptom: the exact failure observed
- traced root cause: the source trigger, not only the visible error
- testable hypothesis: why the proposed edit fixes the source
- source-level fix: why the edit is not merely a symptom guard

If any item is missing, run `$diagnose` before editing. If the trace crosses scattered shallow helpers/modules or no clean test seam exists, use `$improve-codebase-architecture` before forcing a bad test or broad patch.

## Module Shape Gate

Before production edits, name the module shape:

- `publicInterface`: the caller-facing interface, CLI, IPC, UI flow, or module seam the proof crosses
- `testSurface`: the public behavior surface the test or smoke check exercises
- `moduleShape`: deepen existing module | create new module
- `reusePath`: existing module/path being extended
- `newModuleJustification`: required only when adding a new production module, public seam, wrapper, service, manager, or adapter
- `rejectedShallowPath`: shallow helper/wrapper/module split deliberately avoided

Prefer deepening an existing module. Apply Ousterhout's deep-module test: does this hide meaningful complexity behind a small, stable public interface, or create a shallow helper/wrapper split? A new module must earn its interface by hiding complexity, improving locality, or creating a real seam used by more than one caller, adapter, or test surface.

Block if the public test surface cannot be named, or if a new module is proposed without a concrete reason existing modules cannot absorb the behavior.

## Transaction Rule

When the change touches claim tokens, leases, compare-and-set/version fields, transition helpers, replay/finalize/recovery semantics, queues, or worker state, re-walk the surrounding transaction system before edits.

At minimum, name:

- authoritative records mutated together
- the mutation boundary where state must be revalidated
- interleavings that can cross the boundary after prepare but before finalize
- projection, replay, recovery, and no-op paths that share helpers or state fields
- one combined workflow proof plus focused invariant checks

Block if these surfaces cannot be named.

## TDD And Production-Code Alignment

- For behavior changes and bug fixes, `proof` should name the first failing behavior or regression test before production edits unless the user explicitly approved a TDD exception.
- For behavior bugs, `proof` must also include the reproduced symptom, traced root cause, and testable hypothesis, or name `$diagnose` as the required next step.
- Preflight does not satisfy RED/GREEN proof.
- Use `production-code` during implementation and before finalizing to check minimal diff, reuse, boundary validation, cleanup, and verification.

## Default Output

Use this compact form for ordinary preflight-worthy work:

```md
**Preflight**
`scope`: ...
`contract`: ...
`approach`: ...
`moduleShape`: ...
`proof`: ...
`touchpoints`: ...
`risksAndQuestions`: none | ...
```

Field rules:

- `scope`: changed behavior plus adjacent no-change surfaces; do not reduce it to a file path.
- `contract`: authoritative rule that must remain true.
- `approach`: reuse path, chosen minimal implementation, and any realistic rejected alternative.
- `moduleShape`: public interface, test surface, existing reuse path, rejected shallow path, and new-module justification when applicable.
- `proof`: tests, commands, or checks for the changed behavior and adjacent no-change surfaces; for behavior bugs, include reproduced symptom, traced root cause, and testable hypothesis.
- `touchpoints`: likely edit, verify, update, and no-touch surfaces, including protected runtime/cache/generated paths.
- `risksAndQuestions`: concrete risks and blockers; write `none` only when no blocking fact is missing.

## Expanded Output

Use the expanded form only for transaction-sensitive work, release/deploy/package changes, auth/data/filesystem/runtime boundaries, governed execution plans, or multi-PR work:

```md
**Preflight**
`affectedSurface`: ...
`authoritativeContract`: ...
`invariants`: ...
`proofPlan`: ...
`reusePath`: ...
`chosenApproach`: ...
`rejectedAlternatives`: ...
`touchpoints`: ...
`verify`: ...
`update`: ...
`modularityPlan`: ...
`riskChecks`: ...
`openQuestions`: none | ...
```

Keep each field concrete. No filler. If blocked, say so explicitly inside `openQuestions`.
For `modularityPlan`, include public interface, test surface, module shape, reuse path, rejected shallow path, and new-module justification when applicable.

## Review Feedback

When the turn is driven by review feedback:

- restate the actual issue in technical terms
- verify whether the comment matches current `HEAD` and the repo contract
- run both admission checks below before classifying; severity labels are not a work queue, and automated reviewers are reliable about what code *can* do and unreliable about whether it *does*
- distinguish valid defect (mechanism verified AND occurrence demonstrated), false premise, no occurrence, wording mismatch with already-correct behavior, and genuine contract conflict
- if the comment conflicts with repo instructions, canonical spec, or verified behavior, block in `openQuestions` instead of implementing to comment wording

Admission checks, both unconditional and cheap:

- **premise** — name the finding's assumption about runtime, config, or installed state and verify it against the live system with a command, not by reading code; a false premise is rejected with the measurement quoted and no code changes
- **occurrence** — count the failing shape in captured data, logs, or reachable callers; zero occurrences means report line, not change

Validating a finding is not validating a fix. Before shipping a change to a parser, matcher, predicate, or anything consuming external text or markup, run the NEW code over values already captured in the system and require zero regressions. A test written from the same assumption that produced the fix cannot detect its error; only the corpus can. Where a real seam cannot be driven locally — in-page browser JavaScript is the known case — say so and let the authenticated staging run be the proof rather than writing a fixture that passes either way.

Give every finding a disposition with evidence: fixed, rejected-with-evidence, or reported-not-actioned, posted where the reviewer loop can see it. A rejection without a measurement is indistinguishable from one ignored.
