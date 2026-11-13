# Record a case

List each case's command under its map item's `cases` in the preflight draft: recording the approved
preflight captures and runs them. Submit later cases in one workflow TDD call.
The submitted command identifies the case; frozen source records its version. The probe asserts its required
outcome and exits non-zero when the assertion fails. Output is diagnostic only;
printing a value or `name: result` does not assert it.

```text
workflow.py tdd --repo "$PWD" --case BM_X 'COMMAND' --case BM_Y 'python3 /scratch/probe.py'
workflow.py tdd --repo "$PWD"
workflow.py tdd --repo "$PWD" --retire CASE_KEY --case BM_X 'COMMAND'
workflow.py record tdd-map --repo "$PWD" --input map.json
```

`workflow.py` is `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py"`.
A lead passes what follows `workflow.py` (`tdd --case BM_X 'COMMAND'`, without `--repo`) as the workflow tool's `args`; the call returns the finished receipt.
`--case` takes one command per case and `-` for no item. A scratch file a command names is frozen
with it, as is a temporary path the command or that file names; `--path` or `--support` freezes
another beside it. A `--case` call runs the cases it names and any recorded case still without accepted proof, captures any command an item still
declares and judges the rest of the map as recorded, without running it; a bare call captures those too and replays every recorded command.
A case that cannot be captured is refused alone; a failing case is a row, not a failed call. `map.json` holds
`items` (only the changed fields), `added` and `removed`, alone or together:
`{"items":[{"id":"BM_X","expected":"..."}],"removed":["BM_Y"]}`. The receipt gives continuation, failures and evidence access.
The runner owns source preparation and reuse; do not repeat them.

Only a finding's author can withdraw it, or retire a case beside its probe or replacement. The lead
retires a case (`--retire` with its `caseKey`) under a conformance or contract item, or whose item
has left the map; a preservation case only beside a named case of the same call that proves the
item, when the item changed and the case no longer passes or it fails on the original. Reviewers
receive each retired case with its command and exits.
Retirement never authorizes losing required behavior. Keep the raw evidence and
other observations. The runtime rechecks retained findings at verification or
completion and preserves their source identity. Once the final author's ready verdict judges a
tree, a probe of its finding that it captured again there, passing on both sources, replays with
that capture's files; the finding settles at `verify` or `complete` when all its probes then pass.

## Behavior Map

The map holds required outcomes and concrete input context. `boundaryInputs` is
data; it neither binds nor starts a command. Only an actual captured execution
creates a case binding. Replays use those captured cases and retained first proof,
not proposed commands or test names. An item without a captured case stays pending.
Kept-behavior cases use the pass-start test files on both versions. Named support
and scratch files use candidate bytes; contract and unbound cases use candidate
tests. Name a new in-repository kept-behavior test file with `--support`.
Every required command must establish the item's outcome. New commands extend
coverage without deleting the first proved command and its frozen source.
Removing a current map item leaves its proved comparisons in the retained evaluation.
The lead corrects its own item through `record tdd-map`: the item reads stale until
`tdd` reruns it. A command proved under another item leaves an item it does not
satisfy; a command that proves both stays with both.

Both exits zero establish preservation. Original non-zero and candidate zero
establish a requested difference. A failing candidate remains broken. The
requirement and affected coverage still come from the request and original code;
no exit code can authorize an omitted responsibility or a weaker assertion.
The existing reviewer judges whether a failure came from the claimed operation,
whether the case actually ran, and whether its assertions establish the promise.
Reuse the original defect's failure as sensitivity proof.

## Replay and continuation

A case with accepted proof replays its immutable recorded inputs; one without is
captured again when its probe files change. Reuse valid results instead of repeating execution for handoff. Installed
library edits without package metadata changes remain unbound.
Current and retained commands use the same readiness
check, including after map narrowing and finding resolution.

The first proved evaluation stays frozen until a proved recapture of its command
replaces it, along with captures held by map items no longer bound to the command; a
kept-behavior recapture that names a pass-start test file with `--support` replaces nothing, and an attempted
comparison freezes nothing. User scope requiring a new contract starts a
new pass. Keep scratch resources outside production and workflow state.
