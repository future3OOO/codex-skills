# Agent Brief Contract

Every brief opens with its core **Objective**, before execution/workflow pointers.
Authors, including callers reusing this contract, first complete
[Author a brief](SKILL.md#author-a-brief); do not duplicate or bypass its pause.

## Objective / Evidence / Change / Acceptance

- **Objective:** State the observable result at its trigger, not an artifact or
  test run. Target performance optimization in every change; invent no targets.
- **Evidence:** Demonstrate the root cause or existing limitation, not just a
  symptom. Inspect behavior/callers, reproduce bugs, and verify prerequisites;
  methodology references are not runnable checkpoints or seeds.
  Include the authoring step's probe evidence. Unknown cause is not ready:
  disclose it instead of inventing certainty.
- **Change:** Name the existing owning Module, Interface, and Seam in the
  codebase's vocabulary and the minimal causal correction there. Search by domain
  concept and reuse the owner; paths guide discovery, not a fixed edit script.
  Follow [codebase-design](../codebase-design/SKILL.md); link applicable
  `AGENTS.md` for ownership/execution instead of copying doctrine.
  Favor deep Modules and efficient Interfaces.
  Small functions are not automatically shallow public Modules; require no new
  wrapper or extraction. For a PR, preserve working contributions and scope the
  remaining gaps.
- **Acceptance:** State independently falsifiable before/after outcomes and
  affected preservation. Always specify the relevant performance baseline,
  workload, metric, and no-regression comparison at equivalent correct outcomes,
  including necessary repairs/retries. Faster component checks do not establish
  workflow efficiency. For non-behavioral work compare actual artifacts, not an
  invented failing product baseline. Resolve missing proof before readiness;
  never demand evidence while forbidding its acquisition.

Require real observations at the claimed surface: agent/workflow outcomes need
real agents, CLI outcomes need the CLI. Reuse retained evidence; do not
prescribe extra harnesses, metadata, or a test per acceptance bullet.
Use historical examples as regression cases, not implementation templates.
Include other tasks only for concrete dependencies, shared Interfaces, or evidence.
Resolve conflicting instructions and material unknowns; ask only what cannot be
discovered. Apply user restrictions only to their stated scope.

## Compact form

```markdown
**Objective:** [Trigger, core result, performance objective.]
**Evidence:** [Cause/limitation, measured probes and output references.]
**Change:** [Existing Module/Interface/Seam, minimal correction.]
Follow [applicable AGENTS.md] for execution and verification.
**Acceptance:**
- [ ] [Before/after outcome and preservation.]
- [ ] [Baseline, workload, metric, equivalent-outcome no-regression check.]
- [ ] No shallow helpers, fake unit tests, or performance regressions. On violation,
      stop the task, retain/report evidence, reject delivery; the supervisor
      cancels the run when it controls it.
```

Include these bans and stop consequence in every brief. Remove repetition,
superseded instructions, and unrelated history.
