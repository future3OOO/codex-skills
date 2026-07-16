# Mocking Is Banned

Every test crosses a real production seam. Do not mock, stub, fake, or
fixture-substitute ANY collaborator — internal or boundary. A test that cannot
drive the real seam is not written; the proof gap is surfaced as a finding
instead: name the seam, why it cannot be driven, and what real proof would
require.

What to do instead of mocking, by situation:

- **An internal collaborator "needs" a mock** — the Module is shallow or the
  Seam is wrong. Use `$codebase-design` for a targeted Interface/Seam decision
  or `$improve-codebase-architecture` for broader deepening before forcing a
  test.
- **An external system boundary** — drive the real integration through
  product-owned setup paths per the repo proof surfaces: staging session,
  captured external-system evidence, live MCP/API probes.
- **Time or randomness** — pass values through the public Interface as
  explicit parameters; do not patch internals.
- **None of these are possible in this pass** — report the untested branch
  honestly and stop. An absent test is a visible gap; a fake test is a hidden
  one.
