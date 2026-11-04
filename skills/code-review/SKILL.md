---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
---

# Code review

Use a fresh native context for initial review; reuse it for corrections when still
applicable. Work in the lead's checkout. Keep scratch files outside it; isolate and clean up mutating scratch operations. The workflow
command records your evidence and verdict; do not edit candidate source, contract,
Git state or ledger files, merge, install, begin a pass or run context bootstrap.

## Target and evidence

For governed work load the complete review package:

~~~bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" checkpoint --repo "$PWD" --phase code-review --channel-dir "$(mktemp -d)"
~~~

Read every listed channel and the actual diff/current files. The package contains
the request, contract, compared outcomes, current findings and available context.
Use its evidence command only for details it does not show. Reuse applicable graph
evidence; trace affected callers, callees, state writers and no-change surfaces.
A changed target requires renewed inspection. Outside a governed pass, use the
requested Git target and report without creating workflow state.

## Independent judgment

Apply code-quality, codebase-design and AGENTS.md's real-proof and diagnosis rules.
Assess Standards and Spec independently: Standards covers demonstrated quality,
invariant and bloat defects; Spec covers missing requirements, unauthorized effects
and unsupported claims. Verify the original failure, current outcome and affected
preservation separately. Challenge expectations against the request and original
operations; selected green cases cannot authorize an omitted responsibility.
Equal failures or unexecuted operations do not prove preservation. Equivalent
changes need no invented distinguishing input. Judge changed requirements and
coverage against the request; listing a change authorizes nothing.

Attack whole-system cost at fixed behavior. Find the largest justified production
reduction or optimization in code the change edits or its requested operation runs
through, including existing inefficient machinery. Prove the saved work and kept
outcomes with a concrete replacement. Prefer net reduction; necessary growth needs
demonstrated lower cost. Maintained-test cleanup must keep frozen proof and required
surviving checks. Generate replacement diffs from actual scratch edits.

## Execute and record

Submit direct project operations together through workflow tdd with author review, one command per case.
Its help owns inputs and binding rules. Expected values come from executed
references or frozen requirement-grounded properties. Judge whether each operation
establishes the affected requirement. Check the failure cause and selected operation from source and raw output; the runner reads exits only. A scratch call only investigates.

Use the package's verdict command after judging its source and evidence. Return
the Standards/Spec verdict, receipt references and non-executable report lines.
Group counterexamples by owning Module and invariant. On correction retain finding
identities, inspect the changed evidence and answer specific questions supplied by
the current package. The runner rechecks retained findings, including resolved ones;
reuse valid executions. Name missing request promises and unresolved evidence.

Withdraw your own invalid observation with `tdd --as review --reject EVIDENCE:ID=REASON`.
Give the measured failure cause; the raw observation remains recorded.
