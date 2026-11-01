# Recorded source comparisons

Run this after TDD's [required probe loop](SKILL.md#required-probe-loop) has extended the batch from the actual edit. One invocation owns source selection, isolated execution, named results, comparison, freshness and transactional evidence publication:

```text
workflow.py tdd --behavior-id BM_X --timeout 900 -- COMMAND [ARG...]
```

To extend an existing batch, edit its retained probes and use the returned
continuation (`tdd --behavior-id BM_X`, with no command). It reuses the recorded
exact command selection, support files and timeout, comparing the current probes
on the recorded sources. Supply a replacement command when adding a new probe
entrypoint or selecting a different batch.

Repeat `--behavior-id` to share one command across behaviors. Omit the command to reuse recorded probe/support files, even after cleanup; supply a command or changed support to capture current files. A failed batch does not mean every behavior failed: read the bound outcomes. The runner runs from the checkout root with the repository on `PYTHONPATH` and executes the original and current source. It binds each recorded production tree's probe environment: files, command, variables and installed packages of every runtime named by path or as a Python interpreter on a PATH. Installing, upgrading or removing a package there invalidates the comparison. Editing an installed file in place is not detected, nor reliably is a change to the packages a shell string, program or tool such as `uv run` selects; an undetected change reuses the comparison. Removed probes stay removed; production files cannot be overlaid as support. Isolate mutable resources outside the runner's private checkout and workflow state.

## Behavior Map

The recorded preflight owns the initial map. `record tdd-map` changes items by id, naming only the fields that change; `null` removes an optional field. Every item has `id`, `kind`, `basis`, `behavior`, `seam` and `expected`. Readiness enforces the optional fields by execution. TDD readiness, reviewer dispatch and completion use the same named-case and current-diff obligations.

- `kind`: `conformance` for a new required property, `contract` for a required difference, `preservation` for affected behavior to keep. Conformance needs attributable candidate success and non-timeout original failure or inability to execute that case; already-passing behavior belongs in preservation. Contract needs candidate success, an attributable difference and executable original evidence. Preservation needs successful unchanged results on both sources. Separate items retain separate obligations, including in shared batches.
- `boundaryInputs`: the executed case names the item requires: a native case id or the `name` of checked `name: result` output in the project's own language. A name matches its case exactly, or as the short id of one test. A short id that several tests share matches none of them; name the full id. A name that only appears in probe source, a skipped case, or a case outside a narrowed selection keeps the item open. Preflight states the required scope; attach executed witnesses after implementation. Each case is judged under the item that names it, whichever item's comparison ran the batch; items without names that share a batch must each name their cases. A differing case that no item names keeps open each item whose comparison ran it, except a contract item without names whose comparison ran only that case: it is that item's requested change.
- `interpretations`: two or more competing readings. The item stays open until `interpretation` and `authority` are recorded; new readings clear both.

Readiness discovers raw zero-context hunks from both sides of renames, without language-specific semantic classification. `tdd --change DIFF_ID` uses the ordinary named batch on candidate and hunk-reverted source, retaining its selected case names and execution fingerprint. Results, frozen probe/support files and execution caching have one owner. Restoring the change retires its question; unrelated map metadata does not erase evidence. Later distinct named cases are retained alongside the first proved evaluation.

No sensitive result means the question remains open. For equivalence, redundancy or an infeasible context, add `--judgment "source and executed basis"`; this is explicit lead coverage/authority judgment, not proof from an unrelated green case. With an empty map, name all current changes and record a source-only judgment without manufacturing a test. Harmless comments/formatting use this route; the runner does not pretend to classify them. Review must answer the specific DIFF ids in its existing findings, and bind candidateTree and the immutable tddEvidence actually read. Referencing an id establishes response delivery, not semantic truth. A mixed hunk can contain uncovered behavior even when sensitivity is measured; independent review remains the backstop.


## Attribution

`python3 -m unittest`, `python3 -m pytest` and `pytest` are recognised; the runner adds verbose output and attributes their native results. Another command (a script, a shell, `env`) is attributed by the unittest or pytest report it prints, else by its printed `name: result` lines outside interpreter tracebacks. A wrapped runner names passing tests only with `-v`; pass the runner command itself instead. Beside a printed report, output outside that report is unnamed and a preservation item judges it as a whole; run a printed probe as its own command to name its cases. A unittest method that fails in its body is marked `(stopped)`: its later statements did not run on that tree. Give each input that must execute its own case, such as a subtest or a printed line. A test with a failing subtest counts as failed, and any failing case reads `error` when a failure printed for it stopped before the production code (loading, an import or syntax error, a fixture, a unittest block with no traceback); a pytest failure its report does not attribute to a test keeps pytest's result. An `error` original proves nothing, and a run whose failing cases are all `error` is incomplete.

## Receipt

The compact receipt is the same rendering the review packet carries: each compared tree with its outcome, executed-test count and unnamed-case count; the cases failing on current first, then those that differ across the compared trees, collapsed by pattern, each distinct assertion once after the source that produced it; limitations once (unnamed cases, a narrowed selection, stopped methods, unresolved current diff questions); the open obligations as questions, once, in `open`. Full output, the production diff and every earlier run stay in the evidence ledger under the receipt's `summaryId` and `runIndex`. Trees with identical production share one execution; distinct ones run one at a time in isolated checkouts. Repeating an unchanged comparison reuses its evidence. A source edit leaves recorded comparisons stale: status, the next action, review dispatch and completion read the item open until the lead reruns `tdd`; the quality gate runs none. Reuse valid source-bound results instead of repeating covered verification. Missing commands and incomplete execution remain unresolved.

## Map updates

Submit only the changed or new items through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","kind":"contract","basis":"original request","behavior":"requested observable behavior","seam":"owning Module's Interface","expected":"observable result","boundaryInputs":["test_case_name"]}]}
```

Changing `boundaryInputs` or readings re-judges the retained comparison; contract/conformance kind, behavior and expected cannot change after preflight. New preservation items and boundary cases remain allowed; user scope requiring a new contract starts a new pass. An unchanged list writes no event. Removing all witnesses keeps the requirement open. Each original item retains its first individually proved evaluation, not its first attempted comparison. Replays keep that case code and its bindings; this does not establish that the initial proof was well designed. Later comparisons replay them from their recorded tree, sharing cached executions. New cases may extend preservation; new expected text or item labels cannot authorize an uncovered decision. Review requirement changes against the request.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
