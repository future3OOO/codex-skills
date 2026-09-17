# PR #62: inspection claims after the safety repair

Base: `e5f14abb2a3e8b77cbff42dc9e50f4ad35063cde`.
Owner: [PR #62](https://github.com/future3OOO/codex-skills/pull/62),
change C of [issue #59](https://github.com/future3OOO/codex-skills/issues/59).

## Decisions that supersede the original dispositions

The reassignment disposition saying this can only under-record is withdrawn.
`f=a.py; f=b.py; cat "$f"` prints `b.py` but the previous matcher returned
`a.py`. File existence also does not establish that an `if` branch executed.
The existing matcher now declines ambiguous execution instead of recording it.

PostToolUse runs after the whole invocation. Hashing before the hook's edit
branch is not hashing before a shell write. Mixed read/write and opaque batches
are omitted so `cat app.py; printf NEW > app.py` cannot refresh a recorded read
with the unread replacement's digest. An existing old record remains available
for the re-arm to classify as changed. Write invalidation itself is unchanged.

Initial, unique literal assignments remain supported, with quoted scalar
expansion and no single-quote expansion. Reassignment, late assignment,
conditional/background execution, command substitution, malformed shell,
stdout redirects, arbitrary Python and mutating commands decline capture.
The known `2>/dev/null` and `2>>/dev/null` forms still permit read capture.
Inline Python capture requires a literal read actually passed to `print` in
a supported straight-line quoted heredoc; merely opening a SQLite connection
or executing a script from stdin does not establish context inspection.

The unchanged/changed lists now select newest entries under both the entry
and character caps. They count entries, not commas inside filenames, and do
not cut a filename in half. Matching large-file metadata is explicitly listed
as `content identity unverified`, never as unchanged content. The existing
bounded digest implementation and read-sidecar schema are not replaced.

## Meaning and limits of the record

A supported command can inspect a range or matches rather than a whole file.
A digest match is a context-reuse hint, not proof of whole-file coverage,
retention after compaction, a satisfied coverage area, or successful verification.
The matcher remains a conservative supported subset, not a shell interpreter
or an execution trace. This patch does not claim immunity to external concurrent
writers or proof that every byte of a tool result was delivered to an agent.
No gate, receipt, workflow phase, or verification is satisfied by a read entry.

The old `239/239` extraction replay must not be carried forward as this head's
recall result. The original 350-command labelled capture is not retained here;
some previously claimed shapes are now deliberately omitted. The checked-in
shape tests are portable regressions, not a reconstructed CX2 transcript.
Rerunning the original corpus, or disclosing its absence, remains necessary.

## Validation and delivery boundary

The patch includes real-hook regressions in `hooks/tests/test_read_capture.py`
for executed branches, reassignment, read-then-write, long-name recency, and a
same-size/same-mtime large-file mutation. Run them in the complete checkout:

```bash
python3 -m unittest hooks.tests.test_read_capture -v
```

The patch-building environment executed exact-source matcher and renderer
components plus disposable Bash reproductions. It did not execute the workflow
state engine or the complete hook suite. Added integration tests, a current-head
independent review, and installed-estate verification remain required.

C-only scope is unchanged: this patch does not implement D or establish the
paired-pass behavioral acceptance for #59. That requires the real same-task,
same-model, same-budget N/N+1 run with equal real compaction counts, canonical
repeat-read token measurement, at least 50% reduction, no file read more than
twice, and preserved coverage and verification obligations. Unequal compaction
counts are inconclusive. Synthetic fixtures or smaller packets are not a pass.
