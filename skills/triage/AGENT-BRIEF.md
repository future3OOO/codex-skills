# Agent Brief Contract

Start with **Objective**. Complete the [confirmation pause](SKILL.md#author-a-brief)
before drafting, including shared-contract callers.

## Objective / Evidence / Change / Acceptance

- **Objective:** State the confirmed behavior, trigger and governing contract.
  Optimize performance while satisfying that behavior; invent no targets.
- **Evidence:** Trace the root cause/limitation through current behavior, callers
  and real probes. Reproduce bugs; verify prerequisites, not methodology labels.
  Missing required cause blocks readiness.
- **Change:** Specify only the minimum causal change needed for the confirmed
  behavior at the existing owning Module, Interface and Seam. Reuse its capability;
  if it already meets the objective, cite the proof and request no edit.
  Follow [codebase-design](../codebase-design/SKILL.md) and applicable `AGENTS.md`.
  Small functions are not automatically shallow Modules; add no wrapper or
  extraction merely for the template. Preserve working contributions; exclude
  unrelated cleanup and speculative flexibility.
- **Acceptance:** Define expected results from the confirmed objective or applicable
  conformance contract, never the candidate output. Prove the desired behavior and
  affected preservation before comparing performance. Always name the baseline,
  workload and metric; compare equally correct work, including repairs/retries.
  Missing proof blocks readiness; required proof must be obtainable.

For code changes, require the same targeted probes or applicable conformance cases
through the production Interface on unchanged N and edited N+1, with real
collaborators. Verify loaded sources, equivalent inputs and isolated mutable state;
cover the actual changed code, affected cases and interactions. Follow
[production-code's comparison rules](../production-code/SKILL.md#minimum-implementation-decision).
Require the CLI for CLI claims and real agents for agent/workflow claims; compare
actual artifacts for non-behavioral changes. Neither artifact nor component checks prove
workflow speed.

Reuse evidence; add no harnesses, metadata or tests per bullet. Historical examples
are cases, not templates. Include only concrete dependencies or shared Interfaces;
settle conflicts and unknowns, ask only what cannot be discovered, and keep
user restrictions scoped.

## Compact form

```markdown
**Objective:** [Confirmed behavior, trigger, governing contract; performance goal.]
**Evidence:** [Cause/limitation, measured probes and output references.]
**Change:** [Existing Module/Interface/Seam; minimum required edit, or proven no edit.]
Follow [applicable AGENTS.md] for execution and verification.
**Acceptance:**
- [ ] [Objective proven by targeted N/N+1 probes or conformance cases for code
      changes; artifact proof otherwise. Affected behavior preserved separately.]
- [ ] [Baseline, workload, metric; equivalent correct work, no regression.]
- [ ] No shallow helpers, fake unit tests, or performance regressions. On violation,
      stop the task, retain/report evidence, reject delivery; the supervisor
      cancels the run when it controls it.
```

Include these bans and stop consequence in every brief. Remove repetition,
superseded instructions, and unrelated history.
