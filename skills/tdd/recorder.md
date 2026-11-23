# Record a case

A map item's `cases` in the preflight draft holds its test's command: recording the approved
preflight runs nothing; a bare `tdd` captures and runs it. Submit later cases in one workflow TDD call.
The submitted command identifies the case; its in-checkout test files are frozen with it, and a test outside the checkout runs where it was written. The probe asserts its required
outcome and exits non-zero when the assertion fails. Output is diagnostic only;
printing a value or `name: result` does not assert it.

`--case` takes one command per case after its item, or `-` for no item, as two values or as `ITEM:COMMAND`. A file outside the checkout that a command names is never copied: edit it in place. Its result
stands only for the bytes of that file and of each file the case read below its folder (a run nested under a trace that records no file opens observes none of those reads), and reads pending while the file is gone. A `--case` call runs the cases it names and any recorded case still without accepted proof, captures any command an item still
declares and judges the rest of the map as recorded, without running it; a bare call captures those too, replays every recorded command and,
when all hold and nothing is open, verifies.
A declaration that cannot be prepared stays open; put the corrected command in its returned `tdd --correct` call, which also replaces a declared command that already ran (name that command before the new one when the item has several); a failing case is a row, not a failed call. `record tdd-map --input`
takes the map change itself, `items` (only the changed fields), `added` and `removed`, alone or together:
`{"items":[{"id":"BM_X","expected":"..."}],"removed":["BM_Y"]}`, and reruns the changed items' cases in the same call; a declared case may be a recorded case's `caseKey`. The receipt gives continuation, failures and evidence access.
The runner owns source preparation and reuse; do not repeat them.

A testing reply gives its reader each result once. A case's first row carries its command (`commandOf` names the row that
carries a shared one) and its whole result; a later row carries its `caseKey` and only what changed: fields, `was` for the key a
new capture replaces, and of `unexpected` the lines that differ as `{line number: text}`, numbered through its entries joined, or the
whole list when their count changed. `unchanged` counts rows that stand as given, and `open` ends with the count of lines as given.
Open lines name cases by `caseKey`; a command names the checkout as `"${PWD}"` and runs from it.
A finding given before and unchanged is its id alone, and `probeOf` names the finding that carries a shared probe.
`evidence --evidence-id` with the reply's `summaryId` gives every recorded result whole.

Only a finding's author can withdraw it, or retire a case with the cause that disproves it; the lead rejects a
final finding with `tdd --reject ID 'COMMAND'`, whose result goes to its advisor's return review. An advisor-added map item stays
open until its case shows the saving or `tdd --reject ID --change REMOVAL.diff` proves its cost rightly kept. The lead
retires cases with `--retire` and their `caseKey`s, or one with its item and command. A retired kept-behaviour case
stays the item's retained proof, holding it open while it fails on the candidate, unless another recorded case
proves the item and the case fails on the original or no longer passes after the item changed; an item left without a proving case reads open.
Reviewers receive each retired case with its command and exits.
A repair that breaks bound cases, named, is unusable: no cause under it binds, and the final advisor's recording call returns it unrecorded.
Retirement never authorizes losing required behavior. Keep the raw evidence and
other observations. The runtime rechecks retained findings at verification or
completion and preserves their source identity.

## Behavior Map

The map holds required outcomes and the bounds of their inputs. `boundaryInputs` is
data; it neither binds nor starts a command. Only an actual captured execution
creates a case binding. Replays use those captured cases and retained first proof,
not proposed commands or test names. An item without a captured case stays pending.
Kept-behavior cases use the pass-start test files on both versions; one that fails
on the original there runs, in the same call, on the current bytes of the test files
the candidate edits. Named support files use candidate bytes; contract
and unbound cases use candidate tests. Name a new in-repository kept-behavior test file with `--support`.
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

## Replay and continuation

A case with accepted proof replays its immutable recorded inputs; one without is
captured again when its probe files change. Reuse valid results instead of repeating execution for handoff. Installed
library edits without package metadata changes remain unbound.
Current and retained commands use the same readiness
check, including after map narrowing and finding resolution.

The first proved evaluation stays frozen until a proved recapture of its command, as written,
replaces it, along with captures held by map items no longer bound to the command and a retired
kept-behaviour copy of that command; a
recapture that names a pass-start test file with `--support` replaces nothing, and an attempted
comparison freezes nothing. User scope requiring a new contract starts a
new pass. Keep scratch resources outside production and workflow state.
