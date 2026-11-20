---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
---

# Code review

Start in a fresh native context; reuse it for applicable corrections. Work in the lead's checkout; keep scratch files outside it and isolate and clean up mutating scratch operations. Only the workflow records evidence and verdict. Never edit candidate, contract, Git or ledger, merge, install, begin a pass or bootstrap context.

For governed work load the full review package:

~~~bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" checkpoint --repo "$PWD" --phase code-review --channel-dir "$(mktemp -d)"
~~~

Read every channel, diff and current file. Use the evidence command for missing details only. Reuse relevant graph evidence to trace callers, callees, state writers and unchanged surfaces. Reinspect changed targets. Outside a pass review the requested Git target without creating state.

## Independent judgment

Apply code-quality, codebase-design and AGENTS.md's real-proof and diagnosis rules. Judge Standards (demonstrated quality, invariant and bloat defects) separately from Spec (missing requirements, unauthorized effects, unsupported claims). Verify original failure, current result and preservation separately. Check expectations against the request and original operations; green cases cannot authorize an omitted responsibility. Equal failures or unexecuted operations prove nothing. Do not invent distinguishing inputs for equivalent changes. A change list does not authorize changes or prove coverage.

Find the cheapest design at fixed required behavior across the entire requested path, existing machinery included. A known cheaper correct design blocks approval even if another cost improved. Prove savings and preservation with a replacement diff from actual scratch edits. Prefer net reduction; justify growth by demonstrated lower cost. Preserve frozen proof and required surviving checks when reducing tests.

## Execute and record

Submit direct operations via the finding command under the package's `caseRule`. Check source and raw output for cause and whether the operation proves the requirement; the runner sees exits only. Scratch calls only investigate.

Judge source and evidence before using the matching verdict command. Return Standards/Spec verdict, receipts and non-executable report lines. On correction retain finding identities, inspect changed evidence and answer the package's questions. Reuse valid executions: the runner rechecks even resolved findings. Name missing promises and unresolved evidence.

Withdraw your own invalid observation with `tdd --as review --reject EVIDENCE:ID=REASON`. When your finding proves a frozen case's expectation wrong, add `--retire` with the case's item and command, or its `caseKey` from the comparison receipt, to its probe or replacement command.
Give the measured failure cause; the raw observation remains recorded.

Check performance rejection evidence against minimal whole-system cost for required behavior. Passing functional tests or a rejection command alone does not justify dropping an optimization.
