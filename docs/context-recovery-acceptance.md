# Context recovery acceptance (#59 / PR62)

The workflow skill owns `read/show/list/probe` behavior. `sourceDigest` identifies
content; `sourceBytes` is its size. No record asserts memory or satisfies proof.

## Required verification

Run `bash hooks/tests/run.sh hooks.tests.test_context_evidence`. The tests must
exercise real hooks and processes: raw observations; exact partial recovery;
missing/overlapping/repeated ranges; continuation; stale, corrupt and unavailable
sources; revalidation; isolation/concurrent writers; byte limits and retention.
References must open no source files; actual recovery must still check them.

Capture the installed client/version/configuration, sanitized PostToolUse payload
and final model-visible result. Run the payload through
`python3 skills/repo-production-workflow/scripts/context.py probe`.
A hook response may precede output replacement; absent evidence stays unknown.

## CX2 evidence and native experiment

The maintainer's private `cx2-evidence-bundle.zip` in the implementation conversation
has SHA-256 `b20a6d529f024a6b28a723f96371ec30bd655de319a62d13c88b8ac2f2d08bfe`.
Extract it outside the checkout. Label only raw `data/lead-*.json`/`sub-*.json`
output, keeping trace hash, labeler, exact spans and separate threads. DISPUTED
files audit retired errors; they are never labeling input. Audit each document
with `python3 benchmarks/context_evidence_audit.py LABELS.json`.
All 526 calls have output disposition, not complete source attribution. Unknown
scopes/versions and unbound output cannot establish repeat savings. The old
47.7%, 76%, 239/239 and 24-event claims remain withdrawn.

[Issue #59](https://github.com/future3OOO/codex-skills/issues/59) owns the native
paired experiment: verify clean N (`33e4614d77bacefdaf405f21d8d61896cacc8238`)
and exact N+1 worktrees, matched tasks/budgets/compactions, all preservation and
net-cost criteria. Fetching N alone is not checking it out. Native acceptance and
full external-producer integration remain unexecuted; CI does not prove savings.
