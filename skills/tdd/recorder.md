# Record a case

Submit the focused batch in one workflow TDD call.
The submitted command identifies the case; frozen source records its version. The probe asserts its required
outcome and exits non-zero when the assertion fails. Output is diagnostic only;
printing a value or `name: result` does not assert it.

Use runtime help for command forms and the receipt for continuation, failures and
evidence access. The runner owns source preparation and reuse; do not repeat them.

Only the observation's author can withdraw it or retire a wrong expectation.
Retirement never authorizes losing required behavior. Keep the raw evidence and
other observations. The runtime rechecks retained findings at verification or
completion and preserves their source identity.

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

Both exits zero establish preservation. Original non-zero and candidate zero
establish a requested difference. A failing candidate remains broken. The
requirement and affected coverage still come from the request and original code;
no exit code can authorize an omitted responsibility or a weaker assertion.
The existing reviewer judges whether a failure came from the claimed operation,
whether the case actually ran, and whether its assertions establish the promise.
Reuse the original defect's failure as sensitivity proof. For cases that pass on
both sources, supply a behavior-breaking production diff through the runtime
change input. Change values or operators only; identifier counts must stay fixed.
A non-failing break leaves proof pending. Review must judge that the break changes
the claimed behavior without breaking setup or removing its API; exit codes alone
cannot distinguish those causes or prove complete coverage.

## Replay and continuation

Capture edited probe source explicitly; continuation replays immutable recorded
inputs. Reuse valid results instead of repeating execution for handoff. Installed
library edits without package metadata changes remain unbound.
Current and retained commands use the same readiness
check, including after map narrowing and finding resolution.

The first proved evaluation stays frozen,
not the first attempted comparison. User scope requiring a new contract starts a
new pass. Keep scratch resources outside production and workflow state.
