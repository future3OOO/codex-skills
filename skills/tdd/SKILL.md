---
name: tdd
description: TDD for production behavior changes through real Seams. Use when changing production behavior test-first or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

Drive one independently falsifiable behavior through its real production Interface. Retain the probe, use real collaborators, and assert meaningful results and state effects. Mocks, stubs, fakes and test-only adapters cannot prove production behavior. See [test quality](tests.md) and [execution](recorder.md).

Before implementation, identify the requested behavior and affected preservation in the approved preflight. Write the smallest decisive probe first. If its Interface does not exist yet, implement that Interface and return to exercise the independent guarantees; an import failure proves no later behavior.

The existing proof runner executes the same current probe and test environment on recorded original, reviewed and candidate source trees. Run it after the smallest implementation change:

```bash
python3 "$HOME/.codex/skills/repo-production-workflow/scripts/workflow.py" tdd --behavior-id BM_X -- python3 -m unittest tests.test_feature
```

Inspect the bound outcomes. Historical assertion failure followed by candidate success demonstrates a change on exercised inputs. Successful checks on both versions establish preservation. Equal failures, skips, setup errors and timeouts leave proof incomplete. The assertions must observe the public results and relevant state effects; equal exit codes alone do not establish either.

A passing assertion can encode the wrong requirement. Compare every observed difference to the original request. Deleting implementation code does not authorize unrelated behavior changes: preserve original behavior unless the request authorizes the difference. Challenge the assertion itself when it expects an unrequested regression. Changed predicates guide decisive inputs, including inputs that make a removed term determine the result. Bound coverage to those exercised inputs.

When review adds a probe, attach its existing finding source reference and run the same command against the recorded reviewed tree and current repair. Do not revert edits to manufacture a failure. Replace the probe list only when scope changes; deleting an owner cannot close a material finding. Use the existing finding disposition after the runner supplies current proof. Repeated occurrences need proof against the latest reviewed source.

The runner reuses applicable executions and invalidates proof when production source, test content or execution configuration changes. Continue incremental probes while implementing. After coherent repair and cleanup, run quality verification; it refreshes stale recorded comparisons after the gate passes. No manual full-map replay, authored proof statuses or recovery forms are required. Missing probes still require real execution.
