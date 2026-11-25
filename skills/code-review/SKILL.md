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

On the first review, read every channel, diff and changed file. On return, check the repair since your verdict, outstanding findings and their current evidence. Follow the full diff and source when an affected behavior reaches beyond the repair. Reuse graph evidence to trace callers, callees, state writers and unchanged surfaces; reinspect changed targets. Outside a pass review the Git target without creating state.

## Independent judgment

Apply code-quality, codebase-design and AGENTS.md's real-proof and diagnosis rules. Judge Standards (demonstrated quality, invariant and bloat defects) separately from Spec (missing requirements, unauthorized effects, unsupported claims). Verify original failure, current result and preservation separately. Check expectations against the request and original operations; green cases cannot authorize an omitted responsibility. Equal failures or unexecuted operations prove nothing. A case marked `failedOnSameCode` passed only after the case changed while the code did not: settle the input it shows against the request and the original. Do not invent distinguishing inputs for equivalent changes. A change list does not authorize changes or prove coverage.

Demonstrated avoidable work in the requested production path is a release blocker: find the cheapest design at fixed required behavior across the whole requested path, existing machinery included. A known cheaper correct one blocks approval even if another cost improved; functional tests passing while expensive code stays untouched do not justify it. Before proposing code, establish which failures share a cause, where it starts and what else it breaks. Code you supply repairs a cause in the design that produces it: deepen the owner, never rebuild beside it what it already decides, never add code per finding; growth needs a cause test it makes pass or measured less work.

## Record

One call records it all: the verdict command plus `--input VERDICT.json`, shaped as `responseFormat`, under `caseRule`. Each finding names what decides its input; one nothing independent decides is a report line, unless it needs the user's decision: give it with the evidence. A cause names its findings and one test reporting each by id on its own input and one more; with a `repair` (one diff outside the checkout) it fails on the reviewed tree and passes with it; without one it binds if it passes on the original and fails on the candidate. Retire a frozen case a cause disproves under `retires`. All tests run within a minute; say what a cut bound leaves out. The workflow runs every test and recorded case and records what fails a check as a report line, never returned to you. Scratch calls only investigate.

Return the Standards/Spec verdict, receipts, report lines and the verdict receipt's `leadNext` call exactly as given. On a return review one verdict settles your open findings: restate each by id, without a decision once the current evidence settles it, with its decision or cause while it holds. Withdraw your own check a replaced test or evidence supersedes in that verdict: `"rejected": true`, its `priorFinding` and what replaces it.
