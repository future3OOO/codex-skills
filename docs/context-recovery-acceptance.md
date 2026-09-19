# Context recovery acceptance (#59 C/D)

The issue body owns scope. This page is the reproducible acceptance procedure,
not another decision record and not proof that a native experiment ran.

## Check the actual boundary first

On the isolated candidate estate, retain the exact installed client version,
configuration and candidate/source identities. Capture a sanitized real
PostToolUse input and the corresponding final model-visible result. Run the
payload through `skills/repo-production-workflow/scripts/context.py probe` to
report which fields actually exist. The probe reports observations only: a
`tool_response` may precede later output replacement/truncation. Do not infer a
transcript format or a client's retained knowledge from field names.

Command-only payloads remain request history. For source content worth retaining,
use `context.py read --repo ... --path ... --start ... --end ...`; its JSON gives
the actual range, exact produced text, source version, and `nextStart` for a
byte-limited request. `show --id ...` recovers the text, checking current source
and fragment equality. `--historical` is explicitly stale/unbound output, never
current coverage. `list --offset ...` pages references without asserting memory.
Unsupported/binary/oversized sources use ordinary tools. No automatic rerun of a
stored shell command is performed.

## Reclassify the original capture, not the aggregate table

Retain a provenance-bearing labeled JSON document matching the schema in
`benchmarks/context_evidence_audit.py`. Each event must point to its real trace
location and distinguish requested scope, observed source version and actual
returned output. Use null when evidence is missing. The tool reports repeated
paths, repeated requests, and repeated output under the same version label separately. None is
classified as avoidable retrieval: necessary recovery, changed source, overlapping
ranges and retained-context availability require evidence and judgment.

Do not republish 47.7%, 76%, 239/239, or 24 events as measured savings. Retain the
labeling script, trace digest and reviewed labels; do not reconstruct a transcript
from those aggregate figures. Use canonical `response_item`/`o200k_base` accounting
only on an actual supported capture. If unavailable, report the missing operation;
never replace tokens with a byte ratio.

## Paired native experiment

Use the existing README's isolated estate procedure. Pin N to `33e4614` and N+1
to the exact candidate commit. Each arm gets the same repository, task, model and
budget, separate mutable workflow/session state, and verified loaded source.
Capture both before and after comparable real compaction boundaries. Record the
baseline first, then preregister a falsifiable net improvement target before N+1;
do not choose the threshold after seeing its result.

The task must require information outside an earlier fragment and information
inside a preserved fragment. Check that the resumed agent obtains the unseen
information while reusing sufficient available evidence where appropriate. Include
a changed source and lost/corrupt artifact: necessary retrieval must stay possible.
Count all recording, recovery, packet and re-arm input/output tokens and latency,
not merely repeated file accesses. Report first-read overhead as well as avoided
retrieval, and treat incomparable compaction counts as inconclusive. Answers,
coverage and verification obligations must be preserved; cheaper omitted work
fails acceptance. RCF candidates do not close coverage automatically.

The mechanical cost check is:

```bash
python3 benchmarks/context_recovery.py --baseline <checkout> --candidate <checkout> \
  --out <report.json>
```

It measures isolated real hook/CLI costs and byte counts on a synthetic fixture. It is useful regression evidence, but it is NOT this native
experiment and does not establish model behavior or token savings. Both refs are
trusted code running with the caller's permissions, not an OS sandbox. Keep output
outside the checkout. Run the focused hook/context suites and normal CI; obtain
independent review of the exact final head against the original outcome.

Do not mark #59 complete, merge on a transport-only result, or silently change a
context-preservation feature into history-only telemetry.
