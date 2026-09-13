# Writing Issues and Agent Briefs

Use the same compact contract for new issues and `ready-for-agent` briefs.

## Establish the smallest change before drafting

1. **Target objective.** State the observable behavior after implementation in
   one sentence: when a specific trigger occurs, what result should follow?
   Describe the outcome, not an activity such as “add validation.”
2. **Current behavior.** Inspect the existing implementation and its callers.
   For a bug, reproduce the failure and trace its cause. Record the observed gap
   from the objective, linking evidence instead of copying the investigation.
3. **Smallest change.** Identify the existing owner and capability to reuse or
   modify. Prefer the least code that fully meets the objective while preserving
   affected behavior. If existing behavior already satisfies it, say no code
   change is needed. Treat an unverified approach as a question, not a requirement.
4. **Verification.** Name the public operation that demonstrates the objective
   and the affected existing behavior that must remain unchanged. Reuse relevant
   tests; add only uncovered cases. Fewer lines never justify a regression.

Name current symbols or paths when they help locate the owner; they are evidence
for the approach, not a fixed edit script. Exclude unrelated cleanup and
mechanisms the objective does not require.

## Compact contract

```markdown
**Target objective:** When [trigger], [observable result].

**Current behavior:** [Observed gap and reproduction/evidence link.]

**Smallest change:** [Existing owner to reuse or modify, and why this suffices.]

**Verification:**
- [ ] [Operation demonstrating the target behavior.]
- [ ] [Affected existing behavior remains unchanged.]
```

Scale the checks to the actual behavior; the template is not a test-count quota.
Add a blocker or scope boundary only when it changes the implementation decision.
Do not repeat the objective as separate summary and desired-behavior sections or
paste repository-wide engineering rules into each issue.

For `ready-for-agent`, post this contract under `## Agent Brief`, retaining the
issue's category and the skill's required AI disclaimer. Resolve material
unknowns before marking it ready; otherwise ask the specific missing question.
