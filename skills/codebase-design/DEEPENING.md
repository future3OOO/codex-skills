# Deepening

How to deepen a cluster of shallow modules safely, given its dependencies. Assumes the vocabulary in [SKILL.md](SKILL.md): **module**, **interface**, **seam**, and **adapter**.

## Dependency Categories

When assessing a candidate for deepening, classify its dependencies. The category determines how the deepened module is tested across its seam.

### 1. In-Process

Pure computation, in-memory state, no I/O. Always deepenable: merge the modules and test through the new interface directly. No adapter needed.

### 2. Local Runtime Equivalent

Dependencies with a real local runtime that executes the same contract, such as PGLite for Postgres or a temporary filesystem. Deepen only when it exercises the production Interface. A programmed stand-in is diagnostic-only and cannot satisfy RED/GREEN or production verification.

### 3. Remote But Owned

Your own services across a network boundary. Define a **port** only when runtime variation already exists. Required behavior proof crosses the real owned-service Seam; a local protocol harness is diagnostic, not proof.

### 4. True External

Third-party services you do not control. Prefer the provider sandbox/test tenant, captured contract evidence, or an owned end-to-end environment. A programmed response stand-in is diagnostic-only.

## Seam Discipline

- **One runtime adapter means a hypothetical seam. Two genuine runtime variants can justify a real one.** A test-only substitute does not count as a second adapter.
- **Internal seams vs external seams.** A deep module can have internal seams private to its implementation, used by its own tests, as well as the external seam at its interface. Do not expose internal seams through the interface just because tests use them.

## Testing Strategy

Replace, do not layer:

- Old unit tests on shallow modules become waste once tests at the deepened module's interface exist. Delete them.
- Write new tests at the deepened module's interface. The **interface is the test surface**.
- Tests assert on observable outcomes through the interface, not internal state.
- Tests should survive internal refactors. They describe behavior, not implementation. If a test has to change when the implementation changes, it is testing past the interface.
