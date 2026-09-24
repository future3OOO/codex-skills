---
name: production-preflight
description: Produce the required before-edit proof for production code changes — the authoritative contract and the Behavior Map of attacks that falsify it. Use before writing code on implementation, refactor, bug-fix, or review-comment passes.
---

# Production Preflight

Use this skill before making tracked edits on preflight-required code turns.

## Core Doctrine

- Prefer root-cause fixes over band-aids.
- Follow the canonical review-comment doctrine in the repo's `AGENTS.md` (or `CLAUDE.md` if that is what the repo uses).
- Treat review comments as evidence to verify against current code and the repo contract, not authority to obey blindly.
- If you do not know, verify before editing.
- For behavior bugs, require the reproduced symptom, traced root cause, and testable hypothesis before editing; if any are missing, use `/diagnose`.
- No tracked edits before completed preflight.
- If the preflight finds an unresolved blocker, stop and surface it (see [Unknowns](#unknowns)).
- If a tracked governing plan or review artifact exists for the current work and includes an execution checklist, anchor the preflight to that artifact instead of freehanding a new execution path.

## Governing Artifact Alignment

When the current work is governed by a tracked plan or review artifact under `docs/plans/` or `docs/reviews/`:

- name that artifact explicitly in the preflight
- stay inside the owner slice defined by that artifact
- use the artifact's PR order, scope, and verification as the starting execution boundary
- if the requested change no longer fits the governing artifact, refresh the artifact or block before editing

Do not use preflight to silently fork away from the governing execution document.

## Existing PR Checkout Rule

When the turn edits code on an already-open PR:

- treat the live GitHub PR head as authoritative
- verify the exact checkout path that will be edited, not some other review worktree
- record the PR number, branch name, checkout path, live PR head SHA, local `HEAD` SHA, and whether the checkout is branch-attached or detached
- if the checkout is stale, detached, or on the wrong SHA, fetch and realign it before the first tracked edit
- do not treat routine realignment as a blocker; only block if the checkout cannot actually be realigned
- do not commit from a stale detached review worktree

## Affected Surface Rule

Apply [Production Code’s Minimum Implementation Decision](../production-code/SKILL.md#minimum-implementation-decision) before edits. State its affected surface, adjacent consumers and no-change surfaces in the contract and its guarantees in the Behavior Map; preflight owns that initial record.

## Behavior Bug Root-Cause Gate

For behavior bugs, preflight proof must name:

- reproduced symptom: the exact failure observed
- traced root cause: the source trigger, not only the visible error
- testable hypothesis: why the proposed edit fixes the source
- source-level fix: why the edit is not merely a symptom guard

If any item is missing, use `/diagnose` before editing. If the trace crosses scattered shallow helpers/modules or no clean test seam exists, use `/improve-codebase-architecture` before forcing a bad test or broad patch.

## Module Shape Gate

When the change proposes a new production Module, public Seam, or change to a
public Interface, invoke `codebase-design` before completing this gate.

State this gate's decision in `authoritativeContract` — the Interface the proof
crosses, whether an existing Module deepens or a new one is justified, the reuse
path, and the shallow split avoided; `production-code` consumes it.

Prefer deepening an existing module. Apply Ousterhout's deep-module test: does this hide meaningful complexity behind a small, stable public interface, or create a shallow helper/wrapper split? A new module must earn its interface by hiding complexity, improving locality, or creating a real seam used by more than one caller, adapter, or test surface.

Touched shallow helpers/modules are in-scope debt: absorb, delete, or record a concrete blocker in the preflight.

Block if the public test surface cannot be named, or if a new module is proposed without a concrete reason existing modules cannot absorb the behavior.

## Affected Transaction System Rule

For transaction-sensitive work, load and apply the mandatory [canonical
transaction doctrine](../production-code/references/transaction-doctrine.md).
Preflight owns the before-edit map; any unnamed authoritative record, mutation
boundary, interleaving, shared projection/recovery path, contract, invariant, or
proof surface blocks recording.

## What To Produce

Two fields: `authoritativeContract` (concise prose for the reviewers who read it;
the recorder checks only that it is present) and `behaviorMap`. `behaviorMap` is authoritative for the obligations TDD must reconcile, not for
choosing the architecture; a plan may reference it but owns no copy. Keep ordinary
local work short; transaction-sensitive work names the full surrounding surface.

### `authoritativeContract`

Before choosing an implementation or writing tests, investigate each behavioral
predicate that decides an outcome:

1. **Define the decision over its reachable values.** Inspect the actual producer
   Interfaces and trace the values reaching the deciding consumer, including
   relevant types, representations and conversions. State the deciding operation
   and how it treats those values. Task examples and convenient fixtures do not
   bound the input domain. Describing how a value is obtained, or repeating the
   predicate's label, does not define how it is judged.
2. **Challenge the rule, even when it seems conventional.** Compare it with the
   task contract and the rules used by other Modules on the same path. Could a
   supported value receive different decisions under plausible readings? Apply
   [TDD's differential proof](../tdd/tests.md#what-a-slice-must-prove): derive a
   concrete counterexample, evaluate the competing rules on it, and compare the
   outcomes. Test the proposed meaning, not an implementation chosen in advance.
3. **Resolve from governing authority or expose the uncertainty.** The authority
   must distinguish the readings. Their shared wording, your proposed consumer,
   and a runtime difference do not authorize a choice. If both readings remain
   compatible with the governing evidence, keep the choice unsettled before
   dependent code.

State the reachable values and decision rules concisely in this contract and the
existing `behaviorMap`; where readings diverge, use its interpretation fields and
concrete discriminating inputs below. Investigation is complete when each
decision's meaning is established over its reachable values or its uncertainty
is explicit. Unambiguous predicates need no additional fields or inventory.

### Unknowns

Decide every material unknown one of three ways: resolve it from the packet,
repository, runtime, governing artifact or verified source; interview with
`/grilling`, one question at a time, when it can change Module shape, public
Interface, Seam placement, data contract or irreversible scope; or block honestly
and do not record. A settled choice still needing falsification, or unsettled
readings of one behavior, is a pending `behaviorMap` item, not an unknown.

### `behaviorMap`

When plausible readings produce different behavior, put concrete discriminating
values in the owning item's optional, non-empty `boundaryInputs` JSON array.
`interpretations` is an array of at least two non-empty strings; `interpretation`
and `authority` are non-empty strings, supplied together once settled and both
omitted while unsettled. Preserve material types in the concrete input values
and their meaning in the existing explanation. Unambiguous items omit these fields; no case names or second
coverage inventory are required. Initial unsettled readings and inputs are retained
as pending preflight evidence, recoverable through the normal summary/evidence
commands. Resolve them through existing user communication or authorized advice
before dependent implementation; recording a choice does not prove behavior.
After preflight, use the same item's [TDD reassessment](../tdd/SKILL.md), not a new preflight.

Record a non-empty JSON array. Every item has these eight required fields:

```json
[
  {
    "id": "BM_ATOMICITY",
    "kind": "preservation",
    "basis": "existing transaction guarantee",
    "behavior": "a caught inner failure remains atomic under the new transaction path",
    "seam": "the public operation through that path",
    "expected": "no partial inner write survives",
    "redFailure": "PARTIAL_INNER_WRITE_SURVIVED",
    "status": "pending"
  }
]
```

- IDs are stable uppercase identifiers used by RED/GREEN evidence.
- `kind` is `contract` for the requested behavior and `preservation` for what the change must keep true. List contract items first. A map with any pending item carries at least one contract item.
- `basis` records where the item came from.
- `redFailure` names the product failure: a behavior-specific assertion marker or the product's own exception or diagnostic. A RED is valid only when the failure is that mapped product failure; failing earlier is evidence for no item. When the entrypoint does not exist yet, exactly one atomic initial item takes its absence as RED; the independent guarantees stay pending until it exists, so map them as separate items expecting a late RED, not as items that share the existence assertion.
- A contract item starts `pending`. A preservation item starts `pending`, `already-satisfied`, or `omitted`; optional `evidence` explains `already-satisfied` or `omitted` and is forbidden for `pending`. An authored `already-satisfied` is a claim, not proof: the item stays unresolved (named by `summary`) until `tdd --phase red` records its executed baseline after `revalidate`; prefer `pending` and run the baseline.
- Every item is a concrete falsifier: an adversarial attack on one load-bearing public promise through its real production Seam. Derive attacks from what the design promises, not from a universal checklist: rollback/atomicity implies success, ordinary failure, supported interruption/cancellation, nested ownership, and every caller-reachable transaction-ending path; cleanup/resource ownership implies interruption and repeated or finalized lifecycle operations; persistence implies close/reopen and a second connection or process; parsers and matchers imply malformed boundaries plus the captured production corpus; shared mutable state implies every writer and material interleaving; lifecycle state machines imply repeated, out-of-order, nested, superseded, and terminal operations the Interface admits. If the Interface deliberately excludes an implication, narrow the promise explicitly instead of contradicting it.
- Map every category the tdd skill's [Record the Behavior Map in Preflight](../tdd/SKILL.md) section lists; read it before writing the map.
- Only runtime behavior is mappable: delivery line accounting, budget measurement, and other non-runtime bookkeeping never become items.
- A pending behavioral finding is owned by giving an attack item a finding entry in `sourceRefs`; the recorder refuses a map that leaves one unowned. No preservation-only item is needed when existing focused pytest/unittest regression evidence already owns the obligation — record that runner execution as the item's executed baseline. Non-runner evidence cannot baseline an item and must use its RED/GREEN route; prose `already-satisfied` closes nothing.
- Use TDD's one-item-per-independently-failing-outcome rule, including finding-owned attacks. Parameterized forms can share an operation; separate missing guarantees stay visible. Prose cannot widen the domain the retained attacks actually prove.
- A proof gap blocks recording; it is not an omission.

## Execution Gate

- Preflight must happen before the first tracked edit on the governed pass.
- Do not make tracked edits, stage files, or resolve review threads before preflight is complete.
- Do not treat a retrospective preflight summary as valid compliance.
- Do not pause for approval unless the user explicitly asked for it or a real blocker prevents safe editing.
- If new facts invalidate the preflight after editing has started, stop, correct the contract or map, and continue from the corrected preflight.

## Recording

In the governed workflow record it with `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record preflight --input -`
(shape: `record preflight --help`; `--check` validates without recording). A refusal
names every violation at once and mutates nothing. Structurally valid unsettled
interpretations record pending evidence and exit 2; when authority arrives, record
the corrected document. Response prose is not evidence.
