# Recorded source comparisons

Preflight records the initial probe list. One invocation owns source selection, isolated execution, attribution, comparison, freshness and transactional evidence publication:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd --behavior-id BM_X --timeout 900 -- python3 -m unittest tests.test_feature
```

Use the real pytest or unittest target. The runner retains the complete current test environment on each recorded production tree and binds all test content into its execution key. Historical tests removed from the candidate stay removed. Production files cannot be overlaid as test support. Collaborators remain real; provide isolated mutable resources where the operation reaches outside the runner's private checkout and workflow state.

The compact receipt identifies each executed source, outcome and evidence reference. Full output stays in the existing evidence ledger. Repeating an unchanged comparison reuses its evidence. After coherent edits, quality verification checks the gate first, then refreshes stale recorded comparisons automatically. Missing commands and incomplete execution remain unresolved.

For changed obligations, submit the complete list through `record tdd-map --input FILE`:

```json
{"items":[{"id":"BM_X","basis":"original request","behavior":"requested observable behavior","seam":"public operation","expected":"observable result","sourceRefs":[{"type":"finding","evidenceId":"INTAKE","id":"R-1"}]}]}
```

`sourceRefs` is optional unless the probe owns a finding. Unchanged items retain applicable proof; changed items require comparison. The runner refuses deletion of the only material finding owner. An unchanged list writes no event. Use existing review dispositions for measured rejections; changing a list is not a finding disposition.

For nonbehavioral work, an empty approved list carries no behavior claim. Preserve affected behavior with actual comparisons when production is exercised. Summary and completion derive readiness from current runner evidence, never authored statuses.
