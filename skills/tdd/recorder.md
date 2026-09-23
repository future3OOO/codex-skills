# Governed TDD Recorder

Use this reference when governed workflow continuity is active. Preflight owns the
initial Behavior Map; the recorder binds real executions to its IDs. Recorded
RED/GREEN alone does not establish coverage, preservation or efficiency: apply
[Production Code's verification rules](../production-code/SKILL.md#minimum-implementation-decision).
State records proof, never authorizes delivery.

## RED and GREEN

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase red --behavior-id "BM_..." \
  -- <targeted-command>
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase green --behavior-id "BM_..." \
  -- <targeted-command>
```

The map supplies the behavior, Seam, expected outcome and `redFailure` marker or
product diagnostic. Direct pytest/unittest RED requires an executed test whose
own failure carries that diagnostic; printed or captured output is insufficient.
Other commands can open RED with a nonzero exit carrying the declared failure;
reach remains unresolved until review establishes the mapped promise. Identifiable
startup, collection, setup, zero-test, import and syntax failures supply no proof.

Pytest/unittest test targets must resolve inside the repository; redirecting them to external
executed source does not establish that binding. A passing pytest/unittest RED
baselines a preservation item, and a contract item only while no production path
has changed since the pass began; afterwards a contract baseline is retained as a
refused attempt naming the changed paths. A passing non-runner operation baselines a
pending item under the same admission conditions; its proof additionally carries a
bounded observation and site with reach unresolved for review to establish, a
silent exit 0 is refused like an empty selector, and one observed outcome settles
one item. It can also reach GREEN through its own RED. Every RED
records what it observed apart from the marker (`observation`, with object
addresses dropped) and where (`site`: the last test-side frame with its source
line, or the non-runner command); a RED observing the failure another item already
recorded - an explained pytest assertion whose rendering agrees wherever it sits,
or the same unexplained one at the same site - is refused as inherited. Only a currently bound RED owns its observation: a reopened item keeps its RED as history and keys nothing. Runs, including refusals, are retained. A refused RED
binds nothing; correct the command and retry.

Repeated RED and GREEN must match the item's recorded command surface. For pytest
and unittest, verbosity/fail-fast aliases may differ; selectors, configuration,
runner, behavior ID and Seam must match. Other runners remain exact-command bound.
Map updates and rechecks do not replace another item's open RED binding; another
valid RED can open a cycle. [SKILL.md](SKILL.md) owns lifecycle and completion rules.

## Reuse executed proof

Use `tdd --phase red|green --behavior-id <id>
--from-evidence <evidenceId>:<runIndex> --test-id <module.Class.test>` for an existing
execution with effective verbose unittest output. A later quiet flag overrides
verbose. The complete report must unambiguously attribute the selected test's
marker/outcome, including native docstring lines. Use the returned `runIndex`;
do not copy output back. One report can support several items through separate
references without another execution; a sibling pass alone proves no other item.

If reuse is unsupported, see [recovery](#recovery-and-reassessment).

## Map updates

Interpretation reassessment and the automatic selected-input diagnostics are
owned by [SKILL.md](SKILL.md#3-update-the-map-when-a-proof-changes-it).

Add uncovered outcomes or change obligations with `record map`; no-ops write nothing.
Pass the document on stdin; `record map --help` gives its shape:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record map \
  --repo "$PWD" --input - <<'JSON'
{"sourceBehaviorId": "BM_...", "reassessment": "what the proof exposed", "items": [...], "dispositions": [...]}
JSON
```

Optional `sourceBehaviorId` names the GREEN whose consequence is being recorded. New items
use preflight's schema, defaulting to pending; runtime proof fields and
`revalidationRequired` are producer-owned. Dispositions follow [SKILL.md](SKILL.md).
Invalid replacements, cycles or foreign finding references refuse the whole update.

Add finding ownership without execution using
`{"id":"BM_KEEP","sourceRefs":[{"type":"finding","evidenceId":"<intake>","id":"SPEC-1"}]}`.
References union by full identity, including historical intakes in this workflow;
withdrawn items cannot acquire ownership. References cannot remove or reassign it.
Reference-only updates preserve cycles and downstream receipts. Additive obligations
retain applicable proof while unmet obligations keep completion pending.

## Recovery and reassessment

For refused proof, inspect the retained failure reason before retrying. Ambiguous
runner reports cannot establish attribution. Selected setup-failed/skipped tests,
stale or foreign receipts, and truncated or interrupted reports supply no reusable
proof. An unrelated fixture failure does not invalidate a reached selected
assertion. When reuse cannot establish required attribution, use the existing
direct single-item route; other runners use that route too. Do not wrap a probe
just to change parser classification or manufacture a RED.

Reassess preservation with
`{"id":"BM_KEEP","revalidate":true,"evidence":"Affected guarantee and change"}`.
`revalidate` and `status` are mutually exclusive; `status:pending` also flags settled
preservation. Repeated flags are idempotent. Flagged pending uses ordinary RED:
a passing pytest/unittest baseline clears reassessment; a failure opens a cycle.
Settled or flagged GREEN reruns GREEN with its producer-recorded `redCommand`,
including direct operations. Success refreshes proof without another cycle or
invalidating unrelated receipts. Authored `proofCommand` or prose cannot replace
missing producer binding.

Failures, timeouts, non-executing checks and candidate drift leave reassessment
unresolved. Identified non-executing checks on unchanged code preserve unrelated
receipts; regressions and ambiguous failures invalidate downstream checks, as do
source/governance edits. Historical proof remains; omission retains the flag and
requires governing evidence. Missing binding needs valid omission or a currently
GREEN replacement, never an invented execution.

Correct a RED contract's command with
`{"id":"BM_ATTACK","status":"pending","evidence":"Wrong occurrence selected."}`.
This releases its command and active cycle while preserving the contract, finding
ownership, history and other receipts. Resume ordinary proof; the item remains
pending and its historical RED still forbids withdrawal.

Fixed/report-only findings can obtain reassessment evidence. If it invalidates a
terminal claim, record a measured rejection or report-only correction through the
existing disposition command, retaining history. Do not relabel a supported
terminal finding. Behavioral `fixed` requires current owning proof with genuine
GREEN-through-RED; a passing baseline alone never proves a repair.

## No behavior change

Use `--not-required` only when every map item is already satisfied by an executed baseline or omitted by
governing evidence:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" \
  --not-required "<specific reason no production behavior edit is required>"
```

Pending items and proof gaps forbid this path; it cannot replace valid RED/GREEN.
