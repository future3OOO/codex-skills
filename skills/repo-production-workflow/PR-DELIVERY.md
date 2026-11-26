# PR delivery

Every PR delivery follows these rules, from a workflow pass or from docs-only work.

Account for every review signal: threads, inline/issue comments, annotations,
CI failures, and human/automated findings. Give each an evidenced disposition in
the review loop. Fix valid findings through the applicable workflow; update the
task contract when scope changes. Resolve threads only after the fix is pushed
or the evidence posted.

After each push, wait for reviews/checks; for the PR and every open PR below it in
its stack, re-query the current head SHA, checks, merge state, and every unresolved
non-outdated thread. Older-head output is stale. For PR quiet windows, calculate the
deadline from the latest reviewer/check event and repeat the reviewer re-query before merging.
Do not declare completion or switch tasks until task acceptance is reconciled,
legitimate findings are fixed or rejected with evidence, no unresolved
non-outdated threads remain, and required checks pass. Unrelated failures are blockers.
Link related issues in the PR description; after merge, close them and verify closure.

Once the task's PR has merged, `git worktree remove` its worktree and remove its
Repo Context Forge analysis copy.
