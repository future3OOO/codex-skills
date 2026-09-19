# Writing Issues and Agent Briefs

Use the same compact contract for new issues and `ready-for-agent` briefs.
Reconcile the full request, body and comments; preserve existing obligations
and maintain one authoritative brief rather than competing copies.
Read the applicable `AGENTS.md` before drafting execution constraints: it owns
worktree isolation before workflow state, production/docs-only routing,
`$diagnose`, `$tdd`, targeted verification, and same-task continuation.
Carry its pointer into the brief; do not copy the workflow's steps.

## Establish the smallest change before drafting

1. **Target objective.** State the observable behavior after implementation in
   one sentence: when a specific trigger occurs, what result should follow?
   Describe the outcome, not an activity such as “add validation.”
2. **Current behavior.** Inspect the existing implementation and its callers.
   For a bug, reproduce the failure and trace its cause. Record the observed gap
   from the objective, linking evidence instead of copying the investigation.
3. **Smallest change.** Search for existing behavior by domain concept, not just
   the request's wording; name where you looked and the owner to reuse or modify.
   Prefer the least code that fully meets the objective while preserving
   affected behavior. If existing behavior already satisfies it, say no code
   change is needed. Treat an unverified approach as a question, not a requirement.
4. **Verification.** Require proof of the objective and affected preservation
   using the rule below. Fewer lines never justify weaker behavior or proof.

Name current symbols or paths when they help locate the owner; they are evidence
for the approach, not a fixed edit script. Exclude unrelated cleanup and
mechanisms the objective does not require.

For a PR, inspect the existing diff and its demonstrated behavior. The brief
describes only the remaining gaps and smallest correction, preserving working
contributions rather than instructing an agent to rebuild the feature.

## Prove the core objective before delivery

For changed or affected behavior, require the lead to execute equivalent
inputs against actual N (before) and N+1 (candidate) through the production
Interface with real collaborators. Name the operation, verify loaded targets,
and compare observed outputs/state effects against the requested outcome.
Cover the original failure, supported affected paths and previously working
behavior the change can affect. Derive expectations from the contract, not the
new implementation; a passing example cannot close an incomplete repair.

Match proof to the whole objective. For agent/workflow behavior, observe an
actual agent performing the relevant real task with the old and revised
behavior. CLI/executor/ledger tests prove their component outcomes, not the
lead's repair/continuation behavior. For efficiency claims, compare measured
work and results at unchanged correctness guarantees.

Require this reconciliation before the lead claims completion or commits/pushes
implementation for delivery, not after a reviewer or user notices the gap.
Missing targets, dependencies or actual agent execution remain explicit unmet
acceptance; green suites, CI, source-text checks and workflow/map state cannot
waive it. State this delivery condition in the brief itself.

Reuse existing drivers, captured inputs and applicable executed evidence. Tests
qualify by the real behavior they reach, not their unit/integration label;
mocks, substituted collaborators and helper-only assertions cannot replace the
comparison. Retain useful distinct coverage without a new framework, test per
bullet, duplicate handoff executions or mandatory report fields. For genuinely
non-behavioral work, state why behavioral N/N+1 is inapplicable and verify the
actual before/after artifact; do not manufacture a failing product baseline.

## Compact contract

```markdown
Follow the applicable `AGENTS.md` for execution.

**Target objective:** When [trigger], [observable result].

**Current behavior:** [Observed gap and reproduction/evidence link.]

**Smallest change:** [Existing owner to reuse or modify, and why this suffices.]

**Verification:**
- [ ] [Changed or affected behavior: actual N/N+1 targets, real operation and equivalent
      inputs; expected versus observed outputs/state effects. Non-behavioral
      change: compare the actual before/after artifact and explain applicability.]
- [ ] [Affected paths or artifact content that must remain unchanged; compare
      before/after and reject incomplete repairs or regressions.]
- [ ] [Whole-objective evidence, including actual agent execution where
      applicable, reconciled by the lead before committing/pushing for delivery;
      missing evidence remains unmet acceptance despite component tests passing.]
```

Scale the checks to the actual behavior; the template is not a test-count quota.
Add a blocker or scope boundary only when it changes the implementation decision.
Do not repeat the objective as separate summary and desired-behavior sections or
paste repository-wide engineering rules into each issue.

For `ready-for-agent`, post this contract under `## Agent Brief`, retaining the
issue's category and the skill's required AI disclaimer. Resolve material
unknowns before marking it ready; otherwise ask the specific missing question.
