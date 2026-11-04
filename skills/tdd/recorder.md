# Record a case

Submit the focused batch in one workflow TDD call. Each command asserts one
independently falsifiable operation.
The command is the case identity in every language. The probe asserts its required
outcome and exits non-zero when the assertion fails. Output is diagnostic only;
printing a value or `name: result` does not assert it.

The call captures the batch, prepares the sources once and records each command
result. Extra cases and breaks run in private copies up to the CPU count. Fresh
trees wait for the first run. That first call fixes the mode for the pass: wait
when its first run took at least twice the median extra-run time. Otherwise every
run uses its own copy. Ignored build outputs survive between calls; source,
probes and execution inputs still bind every result.
Use the runtime help for inputs. New probe
files belong in scratch outside the checkout. Existing cases need their command.

An author withdraws its invalid observation with `tdd --as review|final --reject EVIDENCE:ID=REASON`. Withdrawal and verdict records only write state. They start no cases and leave other bindings unchanged. Retained observations are checked at `verify` or `complete`, once per candidate tree; added observations receive their own check. When a replacement produces the candidate tree, current candidate proof covers maintained cases; run only the finding’s probe. Otherwise replay every maintained case on the candidate plus the replacement, once per candidate tree and resulting tree.
The referenced raw evidence remains; other observations stay binding.

An explicit case or item selection runs only those cases. With no selection, `tdd` replays every current case and retained proof. Between edits, unselected cases go stale. Verification remains pending until a full `tdd` call refreshes them.

## Behavior Map

The map binds required case commands, not names parsed from output. Its
`boundaryInputs` are command strings. Running a case binds its captured command to
the selected item; planned scratch paths become their frozen command paths.
Kept-behavior cases use the pass-start test files on both versions. Named support
and scratch files use candidate bytes; contract and unbound cases use candidate
tests. New kept-behavior cases need scratch source or explicit named support.
Every required command must establish the item's outcome. New commands extend
coverage without deleting the first proved command and its frozen source.
`record tdd-map` accepts `{"removed":["BM_X"]}` to remove a current item;
its proved comparisons remain in the retained evaluation.

Both exits zero establish preservation. Original non-zero and candidate zero
establish a requested difference. A failing candidate remains broken. The
requirement and affected coverage still come from the request and original code;
no exit code can authorize an omitted responsibility or a weaker assertion.
The existing reviewer judges whether a failure came from the claimed operation,
whether the case actually ran, and whether its assertions establish the promise.
For each item, supply a behavior-breaking production diff through the runtime
change input. Change values or operators only; identifier counts must stay fixed.
The runner stores the change with Git’s default context and applies it with one
context line required. Edits to its patched lines or immediate context need a new
break. Source edits rerun the break. Every command must fail under it. Findings challenge their passing
side the same way. The runner caches these executions with the ordinary runs.
A non-failing break leaves proof pending. Review must judge that the break changes
the claimed behavior without breaking setup or removing its API; exit codes alone
cannot distinguish those causes or prove complete coverage.

## Replay and continuation

Supply the command to capture edited probe source. Commandless continuation
replays its recorded command and immutable probe/support files. Unchanged observed inputs reuse cached results. Source and probe changes,
executable/lookup changes, and Python package-directory or npm manifest metadata
changes invalidate them. In-place installed-library edits without metadata
changes remain unbound. Current and retained commands use the same readiness
check, including after map narrowing and finding resolution.

Follow the receipt's next command. It reports each tree's exit and comparison;
use its evidence command for raw output. The first proved evaluation stays frozen,
not the first attempted comparison. User scope requiring a new contract starts a
new pass. Keep scratch resources outside production and workflow state.
