---
name: tdd
description: TDD for production behavior changes through real Seams. Use when changing production behavior test-first or when another workflow requires TDD proof.
---

# TDD through recorded source comparisons

Drive retained attack probes through real production Interfaces and collaborators; assert meaningful results and state effects. Mocks, stubs, fakes and test-only adapters cannot prove behavior. See [probe quality](tests.md) and [execution](recorder.md).

Before implementation, identify the requested behavior and affected preservation in the approved preflight. Write the smallest decisive probe first using [Diagnose's first construction method](../diagnose/SKILL.md). If its Interface does not exist yet, implement that Interface and return to exercise the independent guarantees; an import failure proves no later behavior.

The existing proof runner executes the same current probe and test environment on recorded original, reviewed and candidate source trees. Run it after the smallest implementation change:

```text
workflow.py tdd --behavior-id BM_CHANGE --behavior-id BM_KEEP -- COMMAND [ARG...]
```

Share one targeted command across its owning behaviors. Inspect the bound outcomes: a failed batch does not mean every behavior failed. Historical assertion failure followed by candidate success demonstrates a change on exercised inputs. Successful checks on both versions establish preservation. Equal failures, skips, setup errors and timeouts leave proof incomplete. The assertions must observe the public results and relevant state effects; equal exit codes alone do not establish either.

A passing assertion can encode the wrong requirement. Compare every observed difference to the original request. Deleting implementation code does not authorize unrelated behavior changes: preserve original behavior unless the request authorizes the difference. Challenge the assertion itself when it expects an unrequested regression. Changed predicates guide decisive inputs, including inputs that make a removed term determine the result. Bound coverage to those exercised inputs.

When review adds a probe, attach its existing finding source reference and run the same command against the recorded reviewed tree and current repair. Do not revert edits to manufacture a failure. Replace the probe list only when scope changes; deleting an owner cannot close a material finding. Use the existing finding disposition after the runner supplies current proof. Repeated occurrences need proof against the latest reviewed source.

Continue incremental probes while implementing; follow the [verification step](../repo-production-workflow/SKILL.md#9-verification) after coherent repair and cleanup.
