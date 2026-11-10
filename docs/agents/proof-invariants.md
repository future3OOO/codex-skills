## Hard Production Invariants

- **Real-Seam proof.** Require equivalent real N/N+1 operations through
  production Interfaces with real collaborators; retained attack probes are
  primary proof. Mocks, stubs, fakes, fixture-substituted collaborators, invented
  gateways, and test-only adapters never prove behavior. Capturing a Module's
  own outgoing process boundary proves only what it emits. For the code being changed, reuse the project's
  relevant cases, fixtures and assertions through the responsible production
  Module's Interface. Use an enclosing operation only when the assertion concerns
  that operation's behavior or integration. A probe asserts observable results.
- **Attack probes first.** Unit tests must themselves probe real production
  behavior. Cover each request promise and the preserved behavior the change
  touches. Prove each changed line with a direct N/N+1
  probe that calls it. Add an existing test only when it calls the changed code
  directly. Never collect a whole test file, class, directory or suite; a focused
  direct script is allowed. Keep each test command within 30 seconds; never raise
  the limit. A timeout leaves verification pending; narrow the command while
  preserving its required assertions. Reuse retained proof instead of rerunning it.
  Do not duplicate retained probes with a parallel unit-test suite.
- **Imaginary-risk ban.** A theoretical risk with no demonstrated failure is a
  report line, not a system. Build nothing for it.
- **Root-cause-first.** No fix until the failure is reproduced, traced, and
  stated as a testable hypothesis.
