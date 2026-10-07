# Recorded source comparisons

Run this after TDD's [required probe loop](SKILL.md#required-probe-loop) has extended the batch from the actual edit. One invocation owns source selection, isolated execution, attribution, comparison, freshness and transactional evidence publication:

```text
workflow.py tdd --behavior-id BM_X --timeout 900 -- COMMAND [ARG...]
```

To extend an existing batch, edit its retained probes and use the returned
continuation (`tdd --behavior-id BM_X`, with no command). It reuses the recorded
exact command selection, support files and timeout, comparing the current probes
on the recorded sources. Supply a replacement command when adding a new probe
entrypoint or selecting a different batch.

Repeat `--behavior-id` to share one command across behaviors. A failed batch does not mean every behavior failed: read the bound outcomes. The runner binds the complete probe environment on each recorded production tree, runs the command from the checkout root with the repository on `PYTHONPATH`, and executes the original, the current source, the recorded reviewed tree of a finding-owned item, and an earlier edited tree that failed this command once more when the probe changes. Removed probes stay removed; production files cannot be overlaid as support. Isolate mutable resources outside the runner's private checkout and workflow state.

## Behavior Map

The recorded preflight owns the initial map. `record tdd-map` changes items by id. Every item has `id`, `kind`, `basis`, `behavior`, `seam` and `expected`. Readiness enforces the optional fields by execution. Readiness is one result: `summary`, the `tdd` and `verify` receipts, reviewer dispatch and `complete` all read it.

- `kind`: `contract` for a requested change, `preservation` for an invariant the change must keep. Each contract case must pass on the current source, and at least one must give a different result on the original, so the requested difference shows. Each preservation case must pass on both trees with the same result.
- `boundaryInputs`: the executed case names the item requires: a unittest method, a pytest id with its parameter, or the `name` of a printed `name: result` line. A name matches its case exactly, or as the short id of one test. A short id that several tests share matches none of them; name the full id. A name that only appears in probe source, a skipped case, or a case outside a narrowed selection keeps the item open. Preflight states each required input and result in the item's text; attach the cases that execute them after implementation, and change the names whenever the written tests differ. Each case is judged under the item that names it, whichever item's comparison ran the batch; items without names that share a batch must each name their cases. A differing case that no item names keeps open each item whose comparison ran it, except a contract item without names whose comparison ran only that case: it is that item's requested change.
- `interpretations`: two or more competing readings. The item stays open until `interpretation` and `authority` are recorded.
- `sourceRefs`: the finding that an attack owns.

Readiness enforces declared cases and their outcomes; finding every context, unreachable ones included, is the lead's work under [context completeness](tests.md#context-completeness).

An item is exposed when a comparison leaves it open on an executed outcome. It stays exposed until a comparison proves it, also after the source changes. Its `kind` and obligation text may change; a reworded item keeps its comparison and debts, and reads stale until a rerun proves the new obligation. A regression a comparison observed (a case that no longer passes unchanged, a differing case no item names, a differing output line) stays owed in the item's comparison evidence under the command that exposed it. The runner stores a comparison's debts as judged against the map when it ran, so a difference a contract named then is that contract's change, never a debt; readiness judges the latest comparison against the current map, so a rename or dropped name reads open until that command runs again. One rule clears it: that same command runs again and restores it, the case passing unchanged on both trees, a case only the edited source produced gone from a passing run, each lost line printed as often as the original printed it, a contract item's own requested case passing verified. While the contract item recorded at preflight, its obligation unchanged, names the exact case in its own comparison, the debt is excused as the requested change, which that contract must prove; dropping the name owes it again. Deleting, skipping or renaming it, dropping its name, another command or another item clears nothing; a passing remainder is not a repair. A requirement that changes after exposure comes from the user as an amended request, which a new pass records. Each change against the preflight (a changed kind, a contract item added or re-worded, contract cases added, a case dropped) is listed for review in `summary` and the review checkpoints with its basis or finding; the `tdd` receipt carries only open obligations. The reviewer and the final advisor judge it against the request or against the finding. A listing authorizes nothing.

## Attribution

`python3 -m unittest`, `python3 -m pytest` and `pytest` are recognised; the runner adds verbose output and attributes their native results. Another command (a script, a shell, `env`) is attributed by the unittest or pytest report it prints, else by its printed `name: result` lines outside interpreter tracebacks. A wrapped runner names passing tests only with `-v`; pass the runner command itself instead. Beside a printed report, output outside that report is unnamed and a preservation item judges it as a whole; run a printed probe as its own command to name its cases. A unittest method that fails in its body is marked `(stopped)`: its later statements did not run on that tree. Give each input that must execute its own case, such as a subtest or a printed line. A pytest test with a failing subtest counts as failed.

## Receipt

The compact receipt is the same rendering the review packet carries: each compared tree with its outcome, executed-test count and unnamed-case count; only the cases that differ across the compared trees or fail on current, collapsed by pattern, each distinct assertion once; limitations once (unnamed cases, a narrowed selection, stopped methods, lost sensitivity); the open obligations as questions, once, in `open`. Full output, the production diff and every earlier run stay in the evidence ledger under the receipt's `summaryId` and `runIndex`. Trees with identical production share one execution; distinct ones run one at a time in isolated checkouts. Repeating an unchanged comparison reuses its evidence. A source edit leaves recorded comparisons stale: status, the next action, review dispatch and completion read the item open until the lead reruns `tdd`; the quality gate runs none. Reuse valid source-bound results instead of repeating covered verification. Missing commands and incomplete execution remain unresolved.

## Map updates

Submit only the changed or new items through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","kind":"contract","basis":"original request","behavior":"requested observable behavior","seam":"owning Module's Interface","expected":"observable result","boundaryInputs":["test_case_name"],"sourceRefs":[{"type":"finding","evidenceId":"INTAKE","id":"R-1"}]}]}
```

Changing `kind`, `boundaryInputs`, readings or `sourceRefs` re-judges the retained comparison; changing the obligation text requires a new comparison. The runner refuses deletion of the only material finding owner. An unchanged list writes no event. Use existing review dispositions for measured rejections; changing a list is not a finding disposition.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
