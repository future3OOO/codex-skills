# Recorded source comparisons

Preflight records the initial probe list. One invocation owns source selection, isolated execution, attribution, comparison, freshness and transactional evidence publication:

```text
workflow.py tdd --behavior-id BM_X --timeout 900 -- COMMAND [ARG...]
```

Drive the retained attack probe through its real production Interface and collaborators. Repeat `--behavior-id` to share its command across behaviors. The runner binds the complete probe environment on each recorded production tree. Removed probes stay removed; production files cannot be overlaid as support. Isolate mutable resources outside the runner's private checkout and workflow state.

The compact receipt names each compared tree with its outcome; trees with identical production share one execution, and distinct ones run concurrently in isolated checkouts. Full output stays in the existing evidence ledger. Repeating an unchanged comparison reuses its evidence. After coherent edits, quality verification checks the gate first, then refreshes stale recorded comparisons automatically, so do not rerun them by hand. Missing commands and incomplete execution remain unresolved.

For changed obligations, submit the complete list through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","basis":"original request","behavior":"requested observable behavior","seam":"public operation","expected":"observable result","sourceRefs":[{"type":"finding","evidenceId":"INTAKE","id":"R-1"}]}]}
```

`sourceRefs` is optional unless the probe owns a finding. Unchanged items retain applicable proof; changed items require comparison. The runner refuses deletion of the only material finding owner. An unchanged list writes no event. Use existing review dispositions for measured rejections; changing a list is not a finding disposition.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
