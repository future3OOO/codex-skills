---
name: production-preflight
description: Establish module shape, real proof, and a Behavior Map before production edits. Use for implementation, refactor, bug-fix, or review-comment passes.
---

# Production Preflight

Use this skill before making tracked edits on preflight-required code turns.

Follow the repo's `AGENTS.md` and the governing artifact. Use
[Production Code's Minimum Implementation Decision](../production-code/SKILL.md#minimum-implementation-decision)
to define affected behavior and a real proof before editing. Use `diagnose` for
bugs and regressions.

## Module Shape Gate

When the change proposes a new production Module, public Seam, or change to a
public Interface, invoke `codebase-design` before completing this gate.

Before production edits, name the module shape:

- `publicInterface`: the caller-facing interface, CLI, IPC, UI flow, or module seam the proof crosses
- `testSurface`: the public behavior surface the test or smoke check exercises
- `moduleShape`: deepen existing module | create new module
- `reusePath`: existing module/path being extended
- `newModuleJustification`: required only when adding a new production module, public seam, wrapper, service, manager, or adapter
- `rejectedShallowPath`: shallow helper/wrapper/module split deliberately avoided

Prefer deepening an existing module. Apply Ousterhout's deep-module test: does this hide meaningful complexity behind a small, stable public interface, or create a shallow helper/wrapper split? A new module must earn its interface by hiding complexity, improving locality, or creating a real seam used by more than one caller, adapter, or test surface.

Touched shallow helpers/modules are in-scope debt: absorb, delete, or record a concrete blocker in the preflight.

Block if the public test surface cannot be named, or if a new module is proposed without a concrete reason existing modules cannot absorb the behavior.

## Affected Transaction System Rule

For transaction-sensitive work, load and apply the mandatory [canonical
transaction doctrine](../production-code/references/transaction-doctrine.md).
Preflight owns the before-edit map and must place any unnamed authoritative
record, mutation boundary, interleaving, shared projection/recovery path,
contract, invariant, or proof surface in blocking `openQuestions`.

## Before the first production edit

Establish the affected behavior and real Seam, the existing owner to reuse, the
smallest approach, and the operations that can falsify it. Trace each deciding
predicate over values its real producers can send. Where plausible rules diverge,
try a concrete counterexample and settle it from governing authority; keep a
material unresolved choice open before dependent code. For transaction work,
map authoritative records, mutation, recovery, and interleavings using the
[transaction doctrine](../production-code/references/transaction-doctrine.md).

Name the proof, verification surface, and any measured resource bound before
editing. Preserve adjacent behavior. Put runtime obligations in the existing
Behavior Map using [TDD's map rules](../tdd/SKILL.md); record a non-empty map.
A governing design or review artifact owns execution order and scope. Unknowns
that could change the Interface or approach remain blockers until resolved.

## Recording

The workflow preflight input contains `behaviorMap`. The recorder validates its
items and stores the map; it no longer stores thirteen presence-checked prose
sections, repeats the design, or requires an `openQuestions: "none"` literal.
Keep the reasoning in the governing artifact or conversation where it can be
reviewed. Submit the map through `workflow.py record preflight --input <json>`;
`--input -` reads stdin. A pending interpretation records pending evidence and
must be resolved before dependent implementation.

Preflight precedes tracked production edits. Do not infer approval from a
successful record: unresolved material questions still block dependent work.
