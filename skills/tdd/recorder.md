# Recorded source comparisons

Preflight records the initial probe list. Execute TDD's [required probe loop](SKILL.md#required-probe-loop)
to extend that batch from the actual edit before this comparison. One invocation owns source selection, isolated execution, attribution, comparison, freshness and transactional evidence publication:

```text
workflow.py tdd --behavior-id BM_X --timeout 900 -- COMMAND [ARG...]
```

To extend an existing batch, edit its retained probes and use the returned
continuation (`tdd --behavior-id BM_X`, with no command). It reuses the recorded
exact command selection, support files and timeout, comparing the current probes
on the recorded sources. Supply a replacement command when adding a new probe
entrypoint or selecting a different batch.

Drive the retained attack probe through its real production Interface and collaborators. Repeat `--behavior-id` to share its command across behaviors. The runner binds the complete probe environment on each recorded production tree. Removed probes stay removed; production files cannot be overlaid as support. Isolate mutable resources outside the runner's private checkout and workflow state.

The compact receipt is the same rendering the review packet carries: each compared tree with its outcome, executed-test count and unnamed-case count; only the cases that differ between original and current or fail on current, collapsed by pattern and assertion with counts; limitations once (unnamed cases, a narrowed selection, lost sensitivity); the open obligations as questions; released items and contract changes. Full output, the production diff and every earlier run stay in the evidence ledger under the receipt's `summaryId` and `runIndex`. Trees with identical production share one execution; distinct ones run one at a time in isolated checkouts. Repeating an unchanged comparison reuses its evidence. After coherent edits, quality verification checks the gate first, then refreshes stale recorded comparisons automatically. Reuse valid source-bound results instead of repeating covered verification. Missing commands and incomplete execution remain unresolved.

For changed obligations, submit only the changed or new items through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","kind":"contract","basis":"original request","behavior":"requested observable behavior","seam":"owning Module's Interface","expected":"observable result","boundaryInputs":["test_case_name"],"sourceRefs":[{"type":"finding","evidenceId":"INTAKE","id":"R-1"}]}]}
```

`sourceRefs` is optional unless the probe owns a finding; the other optional fields are defined in the [Behavior Map](SKILL.md#behavior-map). Changing `kind`, `boundaryInputs`, readings or `released` re-judges the retained comparison; changing the obligation text requires a new comparison. An item that becomes `contract`, drops a preservation case, or adds, re-words or names cases on a contract item after preflight must quote the request sentence in its `basis` (a new attack owning a material behavioral finding carries that finding); a release must name a case the item's current comparison executed unchanged on both trees. The runner refuses deletion of the only material finding owner. An unchanged list writes no event. Use existing review dispositions for measured rejections; changing a list is not a finding disposition.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
