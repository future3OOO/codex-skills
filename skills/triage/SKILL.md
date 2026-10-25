---
name: triage
description: Write agent briefs by default. Use for implementation briefs, AFK task contracts, or explicitly requested issue creation and triage.
---

# Triage

Default to a standalone agent brief: no GitHub issue, label changes, or comments.
Reading or linking an issue grants no write authority. Only explicitly requested
issue work loads [ISSUE-WORKFLOW.md](ISSUE-WORKFLOW.md); do not load it for a
standalone brief.

## Author a brief

1. **Restate and pause.** Before writing any brief, restate the user's goals,
   problem, and core objective in your own words. Ask for confirmation and **stop**:
   no draft, file, or partial brief. Proceed only after the user's actual reply
   confirms that restatement. If corrected, restate and pause again until confirmed.
   Reference that reply in conversation; create no confirmation file/schema.
   Wording-only revisions retain valid confirmation; material objective or
   constraint changes require reconfirmation. This gate belongs to the brief
   writer, including issue work, not the downstream AFK implementer.
2. **Investigate.** After confirmation, read [AGENT-BRIEF.md](AGENT-BRIEF.md).
   Inspect code, callers, and retained evidence. Run only real
   code/entrypoint probes, each completing in milliseconds. Record the reproducible
   command, measured scope and elapsed runtime (including startup), result, and
   output reference. Never run full unit tests or suites as brief-writer
   investigation; switching roles does not evade this restriction. Do not invent
   a numeric ceiling or substitute microprobes for real agent observations.
   If required cause, behavior, or performance proof remains unresolved, disclose
   the gap and withhold a ready brief.
3. **Write and reconcile.** Produce the single Objective/Evidence/Change/Acceptance
   contract from the reference, replacing superseded instructions. Check every
   field, bans, and proof against the confirmed objective.
   Deliver only when no material unknown remains and required proof is obtainable
   under the actual constraints. Triage starts no execution pass. Downstream
   implementation verification follows applicable `AGENTS.md` and explicit user
   constraints; the author-investigation restriction does not replace that workflow.

## Stop consequence

For a performance regression, shallow helper, fake unit test, or prohibited
brief-writer test/suite execution: stop the offending task execution, retain and
report evidence, and reject delivery. The supervising execution owner uses its
existing cancellation mechanism when it controls the run.
