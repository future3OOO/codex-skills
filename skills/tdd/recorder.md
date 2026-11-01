# Recorded source comparisons

Run this after TDD's [required probe loop](SKILL.md#required-probe-loop) has extended the batch from the actual edit. One invocation owns source selection, isolated execution, named results, comparison, freshness and transactional evidence publication:

```text
workflow.py tdd --behavior-id BM_X --timeout 900 -- COMMAND [ARG...]
```

To capture edited or new probes, supply their command. Commandless continuation
replays the recorded command, frozen probe/support files and timeout after cleanup.
It does not capture checkout edits.

Repeat `--behavior-id` to share one command across behaviors. A failed batch does not mean every behavior failed: read the bound outcomes. Both sources execute at the checkout root with the repository on `PYTHONPATH`. Proof binds source, frozen files, command, effective environment, observed executable identities and their search paths. Readers revalidate that bound context; a new execution captures its actual environment. Cold runs use `strace` to observe executable filenames, including child tools and script interpreters; warm reads and unchanged replay start neither probes nor tracer. Python package-directory metadata and scoped/nested npm manifests detect installation changes. This does not trace arbitrary file/network inputs or detect every installed-source edit in place. Production files cannot be overlaid as probe support. Isolate mutable resources outside the runner's private checkout and workflow state.

## Behavior Map

The recorded preflight owns the initial map. `record tdd-map` changes items by id, naming only the fields that change; `null` removes an optional field. Every item has `id`, `kind`, `basis`, `behavior`, `seam` and `expected`. Readiness enforces the optional fields by execution. TDD readiness, reviewer dispatch and completion use the same named-case obligations.

- `kind`: `conformance` for a new required property, `contract` for a required difference, `preservation` for affected behavior to keep. Conformance needs attributable candidate success and non-timeout original failure or inability to execute that case; already-passing behavior belongs in preservation. Contract needs candidate success, an attributable difference and executable original evidence. Preservation needs successful unchanged results on both sources. Separate items retain separate obligations, including in shared batches.
- `boundaryInputs`: the executed case names the item requires: a native case id or the `name` of checked `name: result` output in the project's own language. A name matches its case exactly, or as the short id of one test. A short id that several tests share matches none of them; name the full id. A name that only appears in probe source, a skipped case, or a case outside a narrowed selection keeps the item open. Preflight states the required scope; attach executed witnesses after implementation. Each case is judged under the item that names it, whichever item's comparison ran the batch; items without names that share a batch must each name their cases. A differing case that no item names keeps open each item whose comparison ran it, except a contract item without names whose comparison ran only that case: it is that item's requested change.
- `interpretations`: two or more competing readings. The item stays open until `interpretation` and `authority` are recorded; new readings clear both.

Readiness judges the recorded named cases, not semantic completeness of the diff. The lead inspects the original and request, preserves every affected responsibility and records relevant comparisons; existing independent/final review challenges that coverage. No hunk-reversion result or written assertion certifies untested behavior. Historical judgments remain visible in existing evidence and require individual findings answers under the existing review check. They are statements, not comparison proof.


## Attribution

`python3 -m unittest`, `python3 -m pytest` and `pytest` are recognised; the runner adds verbose output and attributes their native results. Another command (a script, a shell, `env`) is attributed by the unittest or pytest report it prints, else by its printed `name: result` lines outside interpreter tracebacks. A wrapped runner names passing tests only with `-v`; pass the runner command itself instead. Beside a printed report, output outside that report is unnamed and a preservation item judges it as a whole; run a printed probe as its own command to name its cases. A unittest method that fails in its body is marked `(stopped)`: its later statements did not run on that tree. Give each input that must execute its own case, such as a subtest or a printed line. A test with a failing subtest counts as failed, and any failing case reads `error` when a failure printed for it stopped before the production code (loading, an import or syntax error, a fixture, a unittest block with no traceback); a pytest failure its report does not attribute to a test keeps pytest's result. An `error` original proves nothing, and a run whose failing cases are all `error` is incomplete.

## Receipt

The compact receipt is the same rendering the review packet carries: each compared tree with its outcome, executed-test count and unnamed-case count; the cases failing on current first, then those that differ across the compared trees, collapsed by pattern, each distinct assertion once after the source that produced it; limitations once (unnamed cases, a narrowed selection, stopped methods); the open obligations as questions, once, in `open`. Full output, the production diff and every earlier run stay in the evidence ledger under the receipt's `summaryId` and `runIndex`. Trees with identical production share one execution; distinct ones run one at a time in isolated checkouts. Repeating an unchanged comparison reuses its evidence. A source edit leaves recorded comparisons stale: status, the next action, review dispatch and completion read the item open until the lead reruns `tdd`; the quality gate runs none. Reuse valid source-bound results instead of repeating covered verification. Missing commands and incomplete execution remain unresolved.

## Map updates

Submit only the changed or new items through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","kind":"contract","basis":"original request","behavior":"requested observable behavior","seam":"owning Module's Interface","expected":"observable result","boundaryInputs":["test_case_name"]}]}
```

Changing `boundaryInputs` or readings re-judges the retained comparison; contract/conformance kind, behavior and expected cannot change after preflight. New preservation items and boundary cases remain allowed; user scope requiring a new contract starts a new pass. An unchanged list writes no event. Removing all witnesses keeps the requirement open. Each original item retains its first individually proved evaluation, not its first attempted comparison. Replays keep that case code and its bindings; this does not establish that the initial proof was well designed. Later comparisons replay them from their recorded tree, sharing cached executions. New cases may extend preservation; new expected text or item labels cannot authorize an uncovered decision. Review requirement changes against the request.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
