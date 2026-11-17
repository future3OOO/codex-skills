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

Read each listed channel and the actual diff/current files. A tool call's output is cut
at one limit however many commands it runs: read the channels in tool calls that run
nothing else, split by their listed bytes so none is cut. Where a channel lists `since` and you recorded that verdict,
read `since` in its place: what changed after the tree you judged. The package
contains the request, contract, compared outcomes, current findings and available
context. Use its evidence command only for details it does not show. Reuse applicable
graph evidence; trace affected callers, callees, state writers and no-change surfaces.
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
changes need no invented distinguishing input. Reconcile each required outcome,
preservation duty and performance criterion with what its recorded case asserts or
measures. An assertion about another result preserves nothing. A case failing on the
original and passing on the candidate asserts a difference, and its original output
shows what the original did: for every such case find the request text that asks for
exactly that difference. Wording about behavior the request keeps asks for none, even
where the new result reads like that wording: it summarizes the original code, so the
original result stands, whatever item or approval holds the case. A required
measurement not taken or missing its criterion stays unresolved unless the requirement
and behavior/cost evidence justify its disposition.

Attack whole-system cost at fixed required behavior. Treat all code on the requested
production path as variable, including existing inefficient machinery. Find the
cheapest justified design. An improved term cannot excuse remaining avoidable work;
a known cheaper correct design rejects approval. Prove saved work and preserved
outcomes with a concrete replacement. Prefer net reduction; necessary growth needs
demonstrated lower cost. Maintained-test cleanup must keep frozen proof and required
surviving checks. Generate replacement diffs from actual scratch edits.

## Execute and record

Record direct project operations together with the package's finding command, one `--case - COMMAND` per case:
a probe binds when it passes on the original and fails on the candidate. `--change REPLACEMENT_DIFF` submits a
replacement and `--change probe=BREAK_DIFF` a break for the probe, each a diff kept in scratch outside the checkout.
Expected values come from executed references or frozen requirement-grounded properties. Judge whether each operation
establishes the affected requirement. Check the failure cause and selected operation from source and raw output; the runner reads exits only. A scratch call only investigates.

Use the package's verdict command after judging its source and evidence, adding
`--report 'LINE'` for each non-executable report line. Return
the Standards/Spec verdict, receipt references, non-executable report lines and the
verdict receipt's `next` exactly as recorded: it is the lead's next call, so name no phase in its place.
Group counterexamples by owning Module and invariant. On correction retain finding
identities, inspect the changed evidence and answer specific questions supplied by
the current package. The runner rechecks retained findings, including resolved ones;
reuse valid executions. Name missing request promises and unresolved evidence.

Withdraw your own invalid observation with `tdd --as review --reject EVIDENCE:ID=REASON`. When your finding proves a frozen case's expectation wrong, add `--retire CASE_KEY` to its probe or replacement command, using `caseKey` from the comparison receipt or readiness.
Give the measured failure cause; the raw observation remains recorded.

Check performance rejection evidence against minimal whole-system cost for required behavior. Passing functional tests or a rejection command alone does not justify dropping an optimization.
