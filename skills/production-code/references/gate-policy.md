# Bundled Quality Gate Policy

The bundled gate is generic, non-mutating, and risk-calibrated across
JavaScript, TypeScript, Python, shell, and common source files. It is
changed-scope evidence, not a substitute for repository lint, typecheck,
tests, build, or domain-specific gates.

- The gate evaluates one immutable base-to-candidate snapshot (schema v3: v2
  minus the never-evaluated `noDuplication`/`consequenceCoverage` hard-rule
  stubs, `gitnexusQueries` and the always-empty `warnings`, which no caller read).
  Every detector reads captured tree objects; the result carries `evaluation`
  (base/candidate identity plus growth) and structured `findings` with exact
  rule IDs.
- Hard failures include merge-conflict markers, temporary artifacts,
  fake-green suppressions, empty or broad catch/pass patterns, unsafe type
  shortcuts, and TODO/FIXME/HACK in changed source.
- Bloat and duplication are judged, not pattern-matched: `--bloat-review`
  (on by default in `workflow.py verify --kind quality-gate`) asks TypeSafe
  Jev about each changed unit (the smallest enclosing function or test, else
  the changed region, in any language; the enclosing symbol of a pure
  deletion). `--bloat-touched` adds every function of each changed file and
  `--bloat-paths` every function under named files or folders: bloat already
  in the code counts whoever wrote it. Code picks every pair and decides from
  the answer; Jev judges one pair per request, and is never sent a catalogue.
  - A changed function (production code or a test helper) is judged against
    its shortlist: the three same-role functions sharing the most
    five-character runs and the one with the most similar normalised body
    (strings, numbers and definition names blanked), at overlap 0.25 or more,
    never itself or code it uses. It prints `duplicate` at the three-level
    `same` Score 1.0 (production; 1.5 for test helpers). Character runs find
    a bound-method copy of a free function (`self.repo` for `repo`), which
    line-by-line similarity scored 0.0.
  - A new committed test is judged against its folder's shortlist, same
    rule: `duplicate` when a `host` could carry it as one more case or line
    (0.5). An edited existing test is not compared with other tests; the
    probe rule keeps new committed tests rare (0-1 per PR since #106).
  - An added assertion using a name or literal that the change's removed
    production lines use more often than its added ones is asked whether it
    only checks that their output is gone (`deleted-guard`, 0.4), given those
    removed and added lines. A changed production function writing a key a
    caller (from the recorded code graph or call names) or at most three
    other lines write is asked whether two places own it (`two-owners`, 0.7).
  - `--tdd-evidence-json` (from the pass ledger in typed verify) flags each
    new test no recorded failing run names as `test-unproven`.
  Nothing else is judged: format-only, oversized-setup and mocked tests,
  repeated assertions, unneeded or special-case production lines, and
  functions left serving only removed code have no question. `QG-BLOAT` is
  warning-only; each finding records its `question`, `score` and, for a
  Score, Jev's `confidence`. The evaluation record carries the units, the
  requests sent, the answers reused, the input tokens and the estimate.
  Without a key, when a request is refused, or over budget it reports
  incomplete and never changes the verdict.
- Every request not already answered is sized before any is sent (1.6
  characters per token, the least measured, plus 64 tokens a request; real
  requests run 1.9-2.9 characters per token). Over
  `--bloat-budget` (default 400,000 input tokens) nothing is sent and the
  rule names the estimate. Each answer is kept in the repository's Git
  directory (`codex-quality-gate/jev-answers.jsonl`) by its exact request, so
  a later run sends only the pairs whose code changed; identical code cannot
  flap between runs. The store is the only file the gate writes; deleting it
  only makes the next run send everything again.
- Measured in `~/jev-evaluation/codex-skills-labels` (labels 6a4b05e; step
  results in `rebuild-gates.md`). Bars were chosen on the dev split.
  - Shortlist, code only (`shortlist_score.py`): a labelled production
    duplicate's counterpart is shortlisted for 35 of 43 held-out (24 of 29
    dev); the Jev catalogue search this replaced reached 35 of 47 (14 of 29),
    at about 400k tokens a PR #111 run.
  - End to end on functions (`same_score.py`, the gate's own `shortlist` and
    `same`): held-out 22 of 47 duplicates print against their counterpart and
    0 of 22 non-duplicates print (dev 12 of 29, 0 of 7).
  - `host` (0.5) is fitted on PR #106's eight folds; `deleted-guard` (3 of 3
    on PR #111; 2 labels) and `two-owners` (no labels) are PR #111 tuning.
- Known blind spot: a new test that belongs as one more case of an existing
  table-driven test it does not resemble (PR #106: 8 new tests, 6 later
  folded into one fault table). Text similarity ranks that host 20th-376th of
  642 tests. Running the new tests' files to see which tests execute the
  same changed lines took 475 s and ranked it in the top three for 1 of 7
  (about 60 tests run the same lines), so the gate does not trace.
- Cost of a first run, measured: PR #106's merge 11 requests, 7.0k input
  tokens, 2.4 s; PR #109 1 request, 0.5k, 1.5 s; PR #111 28 requests, 19.1k,
  3.3 s (about 370 requests, 720k tokens and 22 s with the catalogue
  search). A rerun of unchanged code sends nothing.
- Growth is warning-only: `QG54-GROWTH-CUMULATIVE` reports cumulative
  production, test, test-support, generated, and human-authored added/deleted/
  net, warning when human-authored net exceeds the 500-line review budget.
  There are no per-file size blockers, same-directory shrink credits, or
  additive-ratio failures.
- Incomplete required scope never reads clean: missing base refs, capture
  failures, binary (unmeasured) counts and unattributed hunks propagate `incomplete` to affected checks
  and hard rules. Typed incomplete findings are additionally surfaced as
  `QG54-ANALYSIS-INCOMPLETE`.
- `--fail-on-warnings` promotes only typed active findings whose exact rule ID
  carries promotion eligibility in immutable rule-policy metadata. Every rule
  ID currently starts ineligible; promotion of an exact ID is a separate
  human decision. Promotion keeps the finding
  `severity=warning` with its intrinsic check passed, adds an exact-ID error,
  and sets top-level `ok=false`.
- Each warning-rule result is reported once, in `findings`; `checks` carries
  only each rule's pass/status/gaps.
- Checks are path-aware through one stored classification per entry (role,
  parser language, human-authored/source status, test-like compatibility,
  exclusion reason). Production source remains strict; tests still fail
  suppression, broad catch/pass, TODO/FIXME/HACK, and `|| true` patterns.
- `--gitnexus-context-json` is checked against the evaluated snapshot: only a
  document declaring the evaluated base and candidate is accepted; an
  unreadable or stale one is a named `QG-BLOAT` gap. The gate creates no
  reports or repository artifacts; its one file is the answer store above,
  inside the Git directory. `workflow.py verify --kind
  quality-gate` hands over the context the Repo Context Forge bootstrap
  recorded for the active pass.
- The gate's evaluated hard-rule results are `cleanup` and
  `noMergeConflictMarkers`.
- Legacy debt outside the changed surface does not block. Touched debt is fixed
  or recorded as a concrete blocker.
