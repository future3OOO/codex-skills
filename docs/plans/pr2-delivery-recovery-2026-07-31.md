# PR #2 minimal workflow recovery

## Objective and authority

Replace PR #2 from `origin/main` with one deletion-heavy PR that preserves the
useful production-workflow and skill improvements while removing Git command
enforcement, shell-command policing, and commit/evidence authorization.

This plan records the governing scope, including the operator's later
completion-state correction. It supersedes the prior nine-slice delivery plan
and the Git-gate remediation documents.

Trusted base: `origin/main` at `89ea1dcb806bc42b0b15bb544828515159421c46`.
Implementation branch: `fix/pr2-minimal-workflow-recovery`.

## Contract

The maintained boundary is the production workflow, not Git:

1. Repo Context Forge;
2. diagnosis when applicable;
3. packet-scoped GitNexus;
4. advisor preflight;
5. production preflight;
6. TDD when required and implementation;
7. verification and quality feedback;
8. fresh code review for non-trivial changes;
9. final Codex Agent or Codex Advisor review;
10. workflow completion, delivery, and reviewer completion.

Workflow state is agent-writable continuity state, not an attestation,
permission object, or security boundary. `complete` refuses while the final
review is not `commit-ready` or material findings remain pending. A later
production edit resets code-review and final-review readiness. No workflow
state operation intercepts or authorizes Git.

## Module and delivery shape

One `workflow_state` module owns the repository-scoped state file, lock,
transitions, edit invalidation, completion rule, flush, and bounded summary.
Its CLI exposes `begin`, `set-phase`, `advisor-result`, `complete`, and
`summary`. Repo Context Forge, TDD, review, advisor, and hook adapters consume
that owner instead of writing parallel evidence records.

The replacement is one PR with coherent commits:

1. remove Git/Bash enforcement and obsolete authorization language;
2. replace the evidence lifecycle with minimal workflow state and real-seam
   hook/CLI tests;
3. restore and simplify the useful workflow, review, TDD, advisor, compaction,
   and operator guidance.

Target: approximately 500 net human-authored source lines. Stop and shrink if
the replacement reaches 1,000 net lines; there is no blanket exception.

## Proof and no-change surfaces

Required proof crosses real production Interfaces:

- a real repository commit between `begin` and later phases does not stale the
  pass;
- `complete` blocks missing/non-ready final review and pending findings, and
  accepts `commit-ready` from either supported reviewer source;
- the real Edit/PostToolUse hooks enforce preflight sequencing and reset review
  readiness before quality feedback, including when the real quality gate
  fails;
- TDD RED/GREEN remains same-command/same-seam and no longer depends on a Git
  fingerprint;
- compact/resume restores the full chain and current next action;
- settings contain no Bash `PreToolUse` command matcher and ordinary Git
  operations remain untouched;
- the full workflow, advisor-wrapper, TDD, quality, and CI contract suites pass.

No-change surfaces: the production quality engine, Repo Context Forge packet
generation, packet-scoped GitNexus, docs/scratch exemptions, read-only advisor
transport, structured Stop feedback, and the PR reviewer completion loop.

Mocks, stubs, fake collaborators, fixture-substituted transports, and static
source assertions do not prove these runtime contracts. The live advisor
transport must be exercised in a dedicated pane.

## Execution checklist

- [x] Create a clean replacement worktree and branch from current
  `origin/main`.
- [x] Run Repo Context Forge, packet-scoped GitNexus caller/callee checks, a
  fresh live advisor scope consult, and production preflight before code edits.
- [x] Write the real-CLI and real-hook RED tests.
- [x] Implement minimal workflow state and remove Git/Bash enforcement.
- [x] Restore only the governed workflow and skill improvements; remove
  superseded remediation debris.
- [x] Run focused and integrated verification, cleanup, net-line measurement,
  GitNexus reanalysis, code review, and a fresh final advisor challenge.
- [x] Commit and push coherent changes and open replacement PR #11.
- [ ] Finish the current-head reviewer loop for PR #11.
- [x] Close PR #2 only after the replacement PR is pushed and reviewable, with
  a pointer to the replacement and no claim that PR #2 was merged.
