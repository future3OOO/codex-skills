---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
---

# Code review

Start in a fresh native context. Work in the lead's checkout; keep scratch outside it, isolated and cleaned up. Only the workflow records evidence and verdict. Never edit candidate, contract, Git or ledger, merge, install, begin a pass or bootstrap.

For governed work load the full review package:

~~~bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" checkpoint --repo "$PWD" --phase code-review --channel-dir "$(mktemp -d)"
~~~

Read every channel, diff and current file; use the evidence command only for missing details. Reuse graph evidence to trace callers, callees, state writers and unchanged surfaces; reinspect changed targets. Outside a pass review the Git target without creating state.

## Independent judgment

Apply code-quality, codebase-design and AGENTS.md's real-proof and diagnosis rules. Judge Standards (demonstrated quality, invariant and bloat defects) separately from Spec (missing requirements, unauthorized effects, unsupported claims). Verify original failure, current result and preservation separately. Check expectations against the request and original operations; green cases cannot authorize an omitted responsibility. Equal failures or unexecuted operations prove nothing. Do not invent distinguishing inputs for equivalent changes. A change list does not authorize changes or prove coverage. Before accepting a test's result, check what drives it: it must enter through the same entry point the real system uses, with inputs the real system meets or the request specifies, as the project's own instructions define them. A test that bypasses that entry point or runs on inputs it made up proves nothing, and is a blocking finding.

Find the design that does the least work at fixed required behavior: the checkpoint's `operation` holds the operation the design touches counted in calls on the pass-start tree and the candidate, the whole and the edited code. Work the result does not need, in the edited code or in what feeds it, blocks approval however far another cost improved; judge by calls removed against the original, never by share of a larger run. Before proposing code, establish which failures share a cause, where it starts and what else it breaks. Code you supply repairs a cause in the design that produces it: deepen the owner, never rebuild beside it what it already decides, never add code per finding; growth needs a cause test it makes pass or measured less work.

## Record

One call records it all: the verdict command plus `--input VERDICT.json`, shaped as `responseFormat`, under `caseRule`. Each finding names what decides its input; one nothing independent decides is a report line, unless it needs the user's decision: give it with the evidence. A cause names its findings and one test reporting each by id on its own input and one more; with a `repair` (one diff outside the checkout) it fails on the reviewed tree and passes with it; without one it binds if it passes on the original and fails on the candidate. Retire a frozen case a cause disproves under `retires`. All tests run within a minute; say what a cut bound leaves out. The workflow runs every test and recorded case and records what fails a check as a report line, never returned to you. Scratch calls only investigate.

Return the Standards/Spec verdict, receipts and report lines; on correction keep finding identities. Withdraw your own invalid observation with `tdd --as review --reject EVIDENCE:ID=REASON`, with its measured cause. Passing tests or a rejection command alone never justify dropping an optimization.
