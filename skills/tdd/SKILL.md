---
name: tdd
description: TDD for production behavior changes through real Seams. Use when changing production behavior test-first or when another workflow requires TDD proof.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one **behavior-specific RED** before production code changes.

A RED is valid only when the failure is the mapped product failure - the declared `redFailure`, which may be an assertion marker or the product's own exception or diagnostic; failing earlier is evidence for no item. When the public entrypoint does not exist yet, its verified absence is RED for exactly one atomic initial behavior that requires it (`assert hasattr(db, "x"), MARKER`, or the absent CLI route); every independent guarantee - one that could fail while that behavior passes - stays pending until the entrypoint exists and is then driven through it, honestly late. Matching markers never establish reach: the recorder refuses a RED whose observed failure another item's RED already recorded, and `summary` names items whose REDs rendered the same failure at different sites. Contract before preservation: the requested behavior's RED comes first.

An **attack vector test (ACT)** is the production test: drive the real production Interface, state the expected result, and compare it with the observed one; for a bug, reproduce and trace it before editing. Reuse an existing check when it reaches the behavior; justify additional checks under [tests.md](tests.md). A runner-backed ACT (directly invoked pytest or unittest) lets the recorder establish reach from the runner's own report of the executed test's failure. A non-runner ACT - the product's CLI, a script, an end-to-end operation - opens its item's RED when it fails carrying the declared failure, and the recorder records its reach as unresolved: review establishes that the observed failure is the mapped promise. Matching output alone never establishes behavior. Either verdict is a bounded reading of the output - evidence the lead verifies, not an attestation, because the ledger is continuity. Do not manufacture a second test path or rewrite a real production failure into a marker assertion.

Use the real N/N+1 operations required by [Production Code](../production-code/SKILL.md#minimum-implementation-decision) as the TDD proof itself. Choose execution by the claimed production behavior, not recorder support; do not create a second test path to satisfy recording.

The canonical mock ban in `~/.codex/AGENTS.md` applies without exception. This skill never creates a test-only proof path.

Before selecting the first slice, read [tests.md](tests.md). Before naming a RED whose correctness depends on transaction, filesystem, process, protocol, concurrency, timing, or serialization semantics, read [mocking.md](mocking.md).

## Task Boundary and Seams

Tests serve the task's behavior surface. Do not test unrelated unchanged behavior. When the change wraps, replaces, intercepts, or reroutes an existing production Seam, preserving every material success, failure, input-form, state, and atomicity guarantee the new path can alter is task behavior.

A **Seam** is the public Interface or externally observable product boundary where behavior is driven and observed without substituting an interior path. Name it before writing the test. When the contract is inferred from repository convention or an analogue, the RED must exercise an input that distinguishes the plausible interpretations.

Make the real Seam drivable: establish its required runtime and collaborators; use `/codebase-design` to expose the production Interface when needed. If the change creates the Seam, prove its absence as narrowly allowed by the Core Rule, create it, then return and drive every required behavior through it. Setup or entrypoint absence never substitutes for executed behavior proof.

## 1. Record the Behavior Map in Preflight

The recorded production preflight owns the initial Behavior Map. A plan may reference it but is not authoritative.

A behavior slice is the smallest independently-failable observable outcome under one relevant precondition. Split outcomes when different defects could break them independently. “And” joining independent outcomes is a smell, not a mechanical rule.

Map:

- every contract-declared success, error, refusal, exception, and non-success outcome;
- every meaningful state transition and rejected transition, including permitted nesting or re-entry;
- at every wrapped or rerouted Seam, each material success, failure, input-form, state, and atomicity guarantee the new path can alter;
- interactions where one behavior can mutate state or invalidate a guarantee owned by another;
- every value one evaluation system produces and another decides under its own semantics; the item names which system's rules decide, and its attack is **differential** (tests.md);
- known load-bearing assumptions that need semantic falsification.

Each item has a stable ID and a `kind`: `contract` for the requested behavior, `preservation` for everything the change must keep true. A behavior-changing map has at least one contract item. Every applicable category above must be accounted for before the first RED. Use one item per independently failing outcome, not per input spelling or finding. Parameterized cases may share an operation; independently missing guarantees remain visible. Finding closure may claim only the domain its owning attacks executed.

**Statuses.** Items start `pending`; the producer records `red`, then `green` through that RED. A passing pytest/unittest RED instead records a baseline, `already-satisfied`, without a cycle. For a contract item that route closes once any production path changed in the pass: a candidate-only pass after the behavior landed describes the candidate, not the baseline, so the run is retained as a refused attempt and the item stays pending - prove it through a real failure at its Seam, observe it on the pass-start tree, or narrow the obligation with governing evidence; do not manufacture RED or hide the sensitivity gap. A preservation item's candidate observation remains its evidence: its late baseline is admitted and labelled late. A baseline is executed preservation evidence, not proof of a repaired defect: baseline alone never owns `fixed`. A pending non-runner exit-zero baselines a pending item under the same conditions - bounded observation and site recorded, reach unresolved for review to establish, one observed outcome settles one item - while a silent exit 0 is refused like an empty selector, and a contract item after production changed stays refused as above. `already-satisfied` is producer-recorded: a preservation item authored or dispositioned `already-satisfied` by prose carries no observation and stays unresolved until an executed passing run records its baseline (revalidate it, then run RED); governing `omitted` remains the settlement route; contract items are never omitted. A never-attacked contract with no open/fixed ownership may be `withdrawn`. A GREEN or reopened attacked item may be `superseded`, but its terminal replacement needs currently proved GREEN, not a baseline. Retired `post-edit-passed` map state is refused; historical documents are not rewritten.

A retained real-Seam probe that passes can disprove a suspected defect or support a measured rejection; it never waives RED/GREEN for a claimed repair. Reuse its actual command and assertions, not an invented failure.

Affected preservation uses producer-owned `revalidationRequired: true`, never authored initial/additional items. Reopening settled preservation to `pending` removes present settlement authority; historical GREEN stays GREEN but flagged proof is unresolved. Only accepted passing execution clears the flag. Governing `omitted` can suspend applicability, including flagged GREEN, subject to finding ownership; it retains the flag and is not proof. Prose cannot restore flagged `already-satisfied`. [recorder.md](recorder.md) owns the execution/binding details.

## 2. Drive One Mapped Vertical Slice

Select one pending contract ID and write its RED before the production edit that satisfies it. Settle each preservation item by baselining it through `tdd --phase red` or dispositioning it through `tdd-map`, early enough that a later RED on it means a regression.

Use [preflight's operational definitions](../production-preflight/SKILL.md#authoritativecontract)
and governing authority to set the expected result, then drive its discriminating
input through the real Seam. Inputs on which competing readings agree cannot
prove the chosen distinction. If the expected result merely repeats an undefined
predicate, return to that investigation and reassess the same map item before
dependent code; do not derive the oracle from the planned implementation.

**RED**

- Write one test for that atomic behavior through its recorded Seam.
- Fail with the item's declared `redFailure` only where the product outcome is absent: the assertion's behavior-specific marker, or the product's own exception or diagnostic.
- Run `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase red --behavior-id <ID> -- <targeted-command>`.
- A passing runner run baselines the item; do not manufacture a RED or edit production code for it.
- A preservation RED records like any other RED. After implementation a preservation item goes RED only when the real Seam shows the change regressed it.

**GREEN**

- Write the smallest production change that passes the same test surface.
- Run `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase green --behavior-id <ID> -- <same-test-surface>`.
- Do not implement unrelated future features; affected guarantees and known defects belong to this repair, and one coherent edit may satisfy several recorded REDs.

**ORDER OF PROOF**

Each contract item's RED belongs on the tree before the production edit that satisfies it; the recorder admits a second RED beside an open one, and the edit hook names any contract item still without its RED instead of refusing. A RED taken after production changed is **late**: recorded as such, labelled in `summary`, shown to the final review, never refused for being late; a RED observing another item's recorded failure is still refused as inherited. A passing RED after production changed baselines no contract item (see Statuses). One edit may satisfy several red items; each reaches GREEN through its own RED. A GREEN for an item with no RED is refused. Map updates are admitted while cycles are open.

Several assertions may jointly prove one behavior; every assertion participating in that joint proof carries the same behavior-specific `redFailure` marker, so whichever guarantee breaks first still names the mapped failure. State after success or failure must match the complete observable contract.

## 3. Update the Map When a Proof Changes It

For a material interpretation choice, use preflight's optional `boundaryInputs`,
`interpretations`, `interpretation` and `authority` on the same item through
`tdd-map` dispositions. Unsettled readings keep applicable items unresolved even
when their inputs are represented. Removing inputs requires governing
`evidence`; supersession must retain their obligations. Sufficient current proof
is reused without another execution. Missing or stale input proof revalidates only
the affected item; an applicable RED remains usable. A changed obligation or
surface returns to pending and ordinary RED, never a manufactured failure.

Normal GREEN/baseline admission, including receipt reuse, screens declared inputs
against the selected execution-bound surface. `inputEvidence` distinguishes
represented values, confirmed missing values (refused immediately), and unresolved
extraction with its limits. Source inspection is bounded to supported selected
operations and literal data; comments, expected-only values and unselected tests
are not input evidence. Representation proves neither execution of each branch nor
assertion sensitivity. Resolve real omissions and assess indirect inputs from
available bound evidence before review. Unsupported extraction alone does not
justify renaming tests, splitting batches, instrumentation or repeating sufficient
proof. Unambiguous items do no extraction. Existing review challenges semantic
adequacy and the mandatory final advisor still applies.

GREEN exposes implementation consequences. Inspect what the implementation actually chose - value conversions, callees, shared writers, hooks and mutation paths, and every operation whose effects could erase a rule before it is judged - and classify each material risk against the contract as needing a real probe, having reusable proof, or being unreachable. When one reveals a new load-bearing mechanism, a touched-Seam preservation or interaction behavior, or a defect, add the item before the next production edit; when it reveals nothing, record the classification and nothing else. The map advisory raises impacted-test candidates; review challenges the decisions and the omissions. Pass the document on stdin instead of a scratch file:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" \
  tdd-map --repo "$PWD" --slug <task> --workflow-id <active-workflowId> --input - <<'JSON'
{"sourceBehaviorId": "BM_...", "reassessment": "...", "items": [...]}
JSON
```

The document accepts `sourceBehaviorId`, `reassessment`, `items`, and `dispositions`. Use Production Code's **Minimum Implementation Decision** to identify affected guarantees before editing; batch their reassessment after the coherent change and before closure. New independently failing outcomes need items; existing non-withdrawn attacks gain finding ownership through additive `sourceRefs`, without re-executing unchanged evidence. Source references union by full `(type,evidenceId,id)` identity; duplicate unions write nothing.

A disposition may carry `revalidate:true` plus evidence, or `status` plus evidence, never both; additive references can accompany either or stand alone. Supersession names `supersededBy` and preserves finding ownership. Reference-only updates preserve active cycles and downstream readiness, execute nothing, and add no acknowledgement. Requesting or finishing reassessment does not replay downstream checks solely for metadata; source edits still invalidate current-tree checks.

For execution and the necessary call forms, use [recorder.md](recorder.md). Add unrelated future features neither to this repair nor its map. Cycle count is not a quality target.

## 4. Refactor and Complete

The refactor window opens only after every contract item is resolved and at least one reached GREEN through RED; a baseline alone never opens it. Refactor only inside that window and rerun relevant tests after each step. If GREEN reveals a structural refactor candidate, use `/codebase-design` to evaluate it.

TDD is complete only when every contract item is GREEN, baseline, or `withdrawn`, every preservation item is GREEN, producer-baselined `already-satisfied`, or `omitted` with evidence — a superseded item of either kind instead needs a currently proved GREEN terminal replacement — no applicable revalidation or proof gap remains, the affected retained checks pass, and no behavior-changing edit occurred after the last applicable GREEN.

When governed workflow continuity is active, follow [recorder.md](recorder.md). It records bounded map/RED/GREEN evidence; it is not authorization.
