# Boundary strategies under the canonical mock ban

The canonical mock-ban statement lives in `AGENTS.md` and governs every claimed RED/GREEN or production proof. This reference does not restate or weaken it.

Use the real production Interface that owns the claimed behavior:

- **In-process behavior:** call the public Interface with real implementation code.
- **Filesystem/local runtime:** use a temporary filesystem or real local runtime that executes the production contract.
- **Owned remote service:** use the owned integration environment or a real service instance configured for tests.
- **Third-party provider:** use its sandbox/test tenant or an owned end-to-end environment. Captured fixtures may support contract analysis but do not replace the live production Seam.
- **Outgoing process boundary:** for assertions about what a Module emits to an external process, capture at that Module's own boundary; that capture is the real Seam, and the ban targets substituted collaborators inside the asserted contract. The provider's own behavior still needs the live Seam.
- **Browser/device behavior:** use the authenticated staging flow or strongest real runtime harness available.

Reuse the workflow's [baseline and candidate execution setup](../repo-production-workflow/SKILL.md#baseline-and-candidate-execution).
Claims about CLI routing, installed code or consumer reload require exercising
those paths; calling an internal function cannot establish that they work.

When proving an application failure or adversarial input, drive the real reachable precondition through the production Seam.

When proving a dependency or runtime failure, drive a real reachable condition that causes the production dependency or runtime to fail naturally. Do not replace an internal function merely to make it raise when the real Seam can produce that condition.

The dependency's relevant semantics are part of the Seam. When correctness depends on transaction, filesystem, process, protocol, concurrency, scheduling, timing, or serialization behavior, exercise those semantics in the real runtime using the strongest deterministic harness available rather than reproducing an approximation.

A programmed stand-in may isolate a diagnostic hypothesis. Label it diagnostic-only, delete it after use, and never count it as RED/GREEN, regression, or production verification.

Establish the real runtime and reachable preconditions needed to drive the Seam safely. For a Seam the change must create, follow [TDD’s creation-and-return rule](SKILL.md#task-boundary-and-seams). Completion requires actual execution; never manufacture green evidence or invent a second production path for tests.
