# Writing Issues and Agent Briefs

Write one compact, authoritative contract for issues and `ready-for-agent` briefs.
Reconcile the request, body and comments; replace superseded instructions.
Preserve requested outcomes and proof while removing unnecessary workflow ceremony.

Link to the applicable `AGENTS.md`; it owns execution and review corrections.
Triage starts no pass and invents no phases, approvals, reviews, budgets or stopping
rules. Keep user restrictions scoped: benchmark restrictions do not govern delivery
reviews. Distinguish repair from completed-code review under the existing workflow.
Resolve contradictory instructions instead of layering another policy over them.

## Establish the contract

1. **Objective:** state the observable result when the relevant trigger occurs.
   Completing a map, producing artifacts or running tests is not the objective.
2. **Evidence:** inspect current behavior and callers; reproduce bugs and link
   decisive evidence. Verify any prerequisite you name. A methodology reference
   is not an existing runnable checkpoint or seed.
3. **Change:** search by domain concept for the owner to reuse and describe the
   smallest sufficient correction. Paths guide discovery, not a fixed edit script.
   For a PR, scope only the remaining gaps and preserve its working contribution.
4. **Acceptance:** specify independently falsifiable outcomes and affected behavior
   to preserve. Leave implementation and proof methods to the agent under the
   repository workflow. Prescribe a method only when explicitly requested or
   necessary to establish the outcome; state why it is necessary.

Include another issue only for a concrete dependency, shared interface or needed
piece of evidence. Historical examples are regressions, not implementation templates.
Resolve material unknowns before `ready-for-agent`; ask only what cannot be discovered.

## Match proof to the claim

Require real before/after observations of the changed behavior and affected
preservation. Agent decisions need observed agent behavior; a CLI defect needs
its actual CLI exercised. Neither implies replaying an entire production workflow.
Use the least costly valid evidence, including applicable retained observations.
Component checks cannot establish an unobserved agent outcome. For non-behavioral
changes, compare the actual artifacts; do not invent a failing product baseline.

When efficiency improvement is requested, compare the stated baseline and
candidate doing equivalent work to the same correct outcome, including necessary
repair or retry work. Measure the relevant cost or time; faster component tests
cannot establish a workflow-efficiency claim. Do not invent performance targets.

Keep necessary proof explicit and reconcile it before delivery under the existing
workflow. Missing evidence remains work to complete. Check that the brief permits
obtaining its required proof; do not require a measurement while forbidding it.
Do not prescribe extra harnesses, metadata conventions, reports or a test per
acceptance bullet unless the objective requires them.

## Compact contract

```markdown
Follow the applicable `AGENTS.md` for execution.

**Objective:** When [trigger], [observable result].
**Evidence:** [Current gap and decisive evidence.]
**Change:** [Existing owner, smallest correction, necessary scope boundary.]
**Acceptance:**

- [ ] [Observable before/after outcome and affected preservation.]
- [ ] [Another independently failing outcome, only if needed.]
```

Remove repeated objectives, workflow boilerplate and unrelated history. Retain
`## Agent Brief`, category/state and the AI disclaimer for `ready-for-agent`.
Handovers need only the issue link and workflow/worktree instruction unless a
task-specific exception is necessary.
