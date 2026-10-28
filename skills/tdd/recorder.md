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

Repeat `--behavior-id` to share one command across behaviors. A failed batch does not mean every behavior failed: read the bound outcomes. The runner runs the command from the checkout root with the repository on `PYTHONPATH` and executes the original and the current source. It binds each recorded production tree's probe environment: its files, command and variables, and the installed packages of every runtime the command line names by path or as a Python interpreter on a PATH. Installing, upgrading or removing a package there invalidates the comparison. Editing an installed file in place is not detected, nor reliably is a change to the packages a shell string, a program or a tool such as `uv run` selects; an undetected change reuses the comparison. Removed probes stay removed; production files cannot be overlaid as support. Isolate mutable resources outside the runner's private checkout and workflow state.

## Behavior Map

The recorded preflight owns the initial map. `record tdd-map` changes items by id, naming only the fields that change; `null` removes an optional field. Every item has `id`, `kind`, `basis`, `behavior`, `seam` and `expected`. Readiness enforces the optional fields by execution. Readiness is one result: `summary`, the `tdd` and `verify` receipts, reviewer dispatch and `complete` all read it.

- `kind`: `conformance` for a new required property, `contract` for a required difference, `preservation` for affected behavior to keep. Conformance needs attributable candidate success and an original that failed or could not execute that case; already-passing behavior belongs in preservation. Contract needs an attributable difference and executable original evidence. Preservation needs successful unchanged results on both sources. Separate items retain separate obligations, including in shared batches.
- `boundaryInputs`: the executed case names the item requires: a unittest method, a pytest id with its parameter, or the `name` of a printed `name: result` line. A name matches its case exactly, or as the short id of one test. A short id that several tests share matches none of them; name the full id. A name that only appears in probe source, a skipped case, or a case outside a narrowed selection keeps the item open. Preflight states the required scope; attach executed witnesses after implementation. Replacing or removing required names needs the designer-approved replacement below. Each case is judged under the item that names it, whichever item's comparison ran the batch; items without names that share a batch must each name their cases. A differing case that no item names keeps open each item whose comparison ran it, except a contract item without names whose comparison ran only that case: it is that item's requested change.
- `interpretations`: two or more competing readings. The item stays open until `interpretation` and `authority` are recorded; new readings clear both.

Readiness enforces declared cases and their outcomes; finding every context, unreachable ones included, is the lead's work under [context completeness](tests.md#context-completeness).

Stable item IDs retain requirements across witness edits, for native runners and general commands alike. Dropping a failing witness cannot erase its required scope.

## Attribution

`python3 -m unittest`, `python3 -m pytest` and `pytest` are recognised; the runner adds verbose output and attributes their native results. Another command (a script, a shell, `env`) is attributed by the unittest or pytest report it prints, else by its printed `name: result` lines outside interpreter tracebacks. A wrapped runner names passing tests only with `-v`; pass the runner command itself instead. Beside a printed report, output outside that report is unnamed and a preservation item judges it as a whole; run a printed probe as its own command to name its cases. A unittest method that fails in its body is marked `(stopped)`: its later statements did not run on that tree. Give each input that must execute its own case, such as a subtest or a printed line. A test with a failing subtest counts as failed, and any failing case reads `error` when a failure printed for it stopped before the production code (loading, an import or syntax error, a fixture, a unittest block with no traceback); a pytest failure its report does not attribute to a test keeps pytest's result. An `error` original proves nothing, and a run whose failing cases are all `error` is incomplete.

## Receipt

The compact receipt is the same rendering the review packet carries: each compared tree with its outcome, executed-test count and unnamed-case count; the cases failing on current first, then those that differ across the compared trees, collapsed by pattern, each distinct assertion once after the source that produced it; limitations once (unnamed cases, a narrowed selection, stopped methods, removed conditions not measured); the open obligations as questions, once, in `open`. Full output, the production diff and every earlier run stay in the evidence ledger under the receipt's `summaryId` and `runIndex`. Trees with identical production share one execution; distinct ones run one at a time in isolated checkouts. Repeating an unchanged comparison reuses its evidence. A source edit leaves recorded comparisons stale: status, the next action, review dispatch and completion read the item open until the lead reruns `tdd`; the quality gate runs none. Reuse valid source-bound results instead of repeating covered verification. Missing commands and incomplete execution remain unresolved.

## Map updates

Submit only the changed or new items through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","kind":"contract","basis":"original request","behavior":"requested observable behavior","seam":"owning Module's Interface","expected":"observable result","boundaryInputs":["test_case_name"]}]}
```

Semantic or witness-set changes require fresh proof. Additive and unchanged witness updates need no consult; no-op updates reuse evidence. New requirements, semantic amendments, and removal/replacement of required witnesses must match the existing advisor-approved draft (same ID, semantics and replacement set). Reuse `preflight-advice --reconsult`, then `record tdd-map`; keep the initial preflight. Approval never proves execution. Removing all witnesses keeps the requirement open.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
