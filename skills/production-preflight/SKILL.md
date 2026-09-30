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

For each removed or narrowed predicate or term, trace all branches it guards in the original source and derive a decisive input for every role outside the authorized removal, reusing existing `behaviorMap` items and probes.

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
existing `behaviorMap` and concrete discriminating probes. Investigation is complete when each
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

Record concrete falsifiers of the load-bearing public promises. Each item requires `id`, `basis`, `behavior`, `seam` and `expected`; `sourceRefs` is optional. IDs are stable uppercase identifiers. The basis ties the expectation to the original request or affected preservation; the Seam names the real public operation.

```json
[{"id":"BM_EXPIRY","basis":"the deadline is inclusive","behavior":"expires at the deadline","seam":"public expiry operation","expected":"now equal to expiresAt is expired"}]
```

Derive attacks from actual promises: atomicity needs supported failure and cancellation; persistence needs reopen and another connection; shared state needs material writer interleavings; parsers need decisive boundaries and captured production inputs. Use real collaborators and observe results and state effects. A missing entrypoint is not proof of its downstream guarantees.

Resolve materially different readings from authority and concrete discriminating inputs in the contract and probes. The existing advisor challenges the expectation itself against the request. No separate interpretation form or authored proof status is needed.

An owning probe links a finding with `sourceRefs: [{"type":"finding","evidenceId":"<intake>","id":"R-1"}]`. The runner executes it on the recorded reviewed source and current repair. Replace the complete list with `record tdd-map` when obligations change; this cannot silently discharge a finding. The runner supplies change or preservation results, freshness and completion. An empty list is appropriate only when there is no behavior claim.

## Execution Gate

- Preflight must happen before the first tracked edit on the governed pass.
- Do not make tracked edits, stage files, or resolve review threads before preflight is complete.
- Do not treat a retrospective preflight summary as valid compliance.
- Do not pause for approval unless the user explicitly asked for it or a real blocker prevents safe editing.
- If new facts invalidate the recorded preflight, update the owning items through `tdd-map` before continuing; do not reopen the initial preflight loop.

## Recording

Submit the exact artifact through [Codex Advisor's preflight loop](../codex-advisor/SKILL.md#preflight-advice).
Record it once the advisor returns `approved` for that content.

In the governed workflow record it with `python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" record preflight --input -`
(shape: `record preflight --help`; `--check` validates without recording). A refusal
names every violation at once and mutates nothing. Key order and JSON formatting
do not change content.
Resolve outstanding questions before dependent implementation; update the list through `tdd-map`, never a second preflight recording. Response prose is not evidence.
