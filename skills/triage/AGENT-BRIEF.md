# Issues and Agent Briefs

Before drafting, inspect the existing implementation and callers; reproduce and
trace bugs. Find the smallest change that meets the full objective without
regressions, reusing existing code. If current behavior already meets
the objective, record that no code change is needed.

## Contract

Use this format for new issues and agent briefs:

```markdown
**Target objective:** When [trigger], [observable result after implementation].

**Current behavior:** [Observed gap; link to reproduction or evidence.]

**Smallest change:** [Existing owner/capability to reuse or modify; why sufficient.]

**Verification:**
- [ ] [Public operation demonstrates the target behavior.]
- [ ] [Affected existing behavior remains unchanged.]
```

Include the checks the behavior needs; reuse existing tests and add uncovered
cases. Names and paths locate current owners, not a fixed edit script.
Add blockers or scope boundaries only when they affect an implementation decision.
Link investigation details; omit duplicate summaries and repository-wide rules.

For agent handoff, use `## Agent Brief` and retain the issue's category.
Keep unverified approaches as questions; resolve material unknowns before
`ready-for-agent`. The posting disclaimer is owned by [SKILL.md](SKILL.md).
