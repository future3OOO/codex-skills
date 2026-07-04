# Design It Twice

When the user wants to explore alternative interfaces for a chosen deepening candidate, use this pattern. It is based on "Design It Twice": the first idea is unlikely to be the best.

Use the vocabulary in [SKILL.md](SKILL.md): **module**, **interface**, **seam**, **adapter**, and **leverage**.

## Process

### 1. Frame the Problem Space

Before delegating design alternatives, write a user-facing explanation of the problem space for the chosen candidate:

- The constraints any new interface would need to satisfy.
- The dependencies it would rely on, and which category they fall into. See [DEEPENING.md](DEEPENING.md).
- A rough illustrative code sketch to ground the constraints. This is not a proposal; it only makes the constraints concrete.

Show this to the user, then proceed to the design alternatives. The user can read and think while those alternatives are developed.

### 2. Produce Several Different Designs

Produce at least three radically different interfaces for the deepened module. Use delegated agents or sub-agents where the current runtime and local workflow allow it; otherwise, do the alternatives serially yourself and keep the same design constraints.

Each design should use a different pressure:

- Design 1: minimize the interface. Aim for one to three entry points and maximize leverage per entry point.
- Design 2: maximize flexibility. Support many use cases and extension points.
- Design 3: optimize for the most common caller. Make the default case trivial.
- Design 4, when applicable: design around ports and adapters for cross-seam dependencies.

Include both [SKILL.md](SKILL.md) vocabulary and project domain vocabulary in each design so the architecture language and domain language stay consistent.

Each design outputs:

1. Interface: types, methods, params, invariants, ordering, and error modes.
2. Usage example showing how callers use it.
3. What the implementation hides behind the seam.
4. Dependency strategy and adapters. See [DEEPENING.md](DEEPENING.md).
5. Trade-offs: where leverage is high and where it is thin.

### 3. Present and Compare

Present designs sequentially so the user can absorb each one, then compare them in prose. Contrast by **depth**, **locality**, and **seam placement**.

After comparing, give your recommendation: which design is strongest and why. If elements from different designs would combine well, propose a hybrid. Be opinionated; the user wants a strong read, not a menu.
