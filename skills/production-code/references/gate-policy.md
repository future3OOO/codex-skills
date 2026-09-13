# Bundled Quality Gate Policy

The bundled gate is generic, non-mutating, and risk-calibrated across
JavaScript, TypeScript, Python, shell, and common source files. It is
changed-scope evidence, not a substitute for repository lint, typecheck,
tests, build, or domain-specific gates.

- The gate evaluates one immutable base-to-candidate snapshot (schema v2).
  Every detector reads captured tree objects; the result carries `evaluation`
  (base/candidate identity plus growth), structured `findings` with exact rule
  IDs, and `resolvedFindings`.
- Hard failures include merge-conflict markers, temporary artifacts,
  fake-green suppressions, empty or broad catch/pass patterns, unsafe type
  shortcuts, and TODO/FIXME/HACK in changed source.
- Exact duplication is warning-only structured evidence, never a blocker:
  `QG54-DUPLICATE-ADDED-SYMBOL` (a complete added symbol body repeated),
  `QG54-DUPLICATE-ADDED-BLOCK` (a repeated contiguous added block), and
  `QG54-DUPLICATE-BASELINE` (an added copy whose base-tree owner is still
  retained in the candidate). Each names every added region carrying the
  implementation — plus, for the baseline rule, one retained baseline owner —
  with a content anchor, over production, test, and test-support roles, and a
  decorator counts as part of the definition it decorates. Comparison is exact — identifiers, literals, operators,
  control flow, symbol boundaries, and hunk boundaries all discriminate, and
  separate hunks are never joined. Only Python is canonicalized, by its real
  tokenizer; any other language in scope is reported as incomplete rather than
  guessed. `references/duplicate-calibration.md` publishes the pinned-corpus
  replay behind the reported minimum region size.
- Growth is warning-only: `QG54-GROWTH-CUMULATIVE` reports cumulative
  production, test, test-support, generated, and human-authored added/deleted/
  net, warning when human-authored net exceeds the 500-line review budget.
  There are no per-file size blockers, same-directory shrink credits, or
  additive-ratio failures.
- Incomplete required scope never reads clean: missing base refs, capture
  failures, binary (unmeasured) counts, truncated or unreadable baseline
  discovery, and unattributed hunks propagate `incomplete` to affected checks
  and hard rules. Typed incomplete findings are additionally surfaced as
  `QG54-ANALYSIS-INCOMPLETE`.
- Responsibility-owner competition is warning-only structured evidence:
  `QG54-OWNER-COMPETITION-PRODUCTION` and `QG54-OWNER-COMPETITION-TEST`
  generate candidates independently of duplicate detection from eight
  mechanical evidence classes, each evaluated on every run with a serialized
  per-class ledger. Every finding carries exactly one state — `candidate`,
  `confirmed-unresolved`, or `resolved` — and only the first two are active
  warnings; `resolved` evidence lives in `resolvedFindings` as telemetry and
  never keeps the visible gate non-green. Semantic dispositions arrive only
  through the fixed out-of-tree carrier `$GIT_DIR/qg54-dispositions.json`,
  read during capture and frozen on the snapshot — a git-dir path is never
  part of any candidate tree, so records-shaped files inside the evaluated
  tree are never read. Each record is structurally validated against the
  exact evaluated snapshot. Every resolution requires a parent-pinned record
  and complete owner discovery; same-responsibility resolution additionally
  requires the one-owner predicate — superseded surfaces absent and
  unreferenced, every affected function rewired to the survivor, one
  surviving owner — so partial deepening, renames, facades, and
  discovery-shrinking never resolve, while a distinct-authority record
  resolves with both anchored owners present. The trust asymmetry is deliberate (parent #54 decision,
  2026-08-12): **resolution** silences a warning, so it additionally requires
  a record whose validation-root identifier and digest match the shipped
  parent-pinned table (extending that table is a parent-approved code change,
  exactly like promotion eligibility); **confirmation** only adds visible
  debt, so it accepts content-consistent records.
  `references/owner-calibration.md` publishes the parent-pinned manifest
  replay.
- `--fail-on-warnings` promotes only typed active findings whose exact rule ID
  carries promotion eligibility in immutable rule-policy metadata. Every rule
  ID currently starts ineligible; promotion of an exact ID is a separate
  human decision on parent #54. Promotion keeps the finding
  `severity=warning` with its intrinsic check passed, adds an exact-ID error,
  and sets top-level `ok=false`.
- Checks are path-aware through one stored classification per entry (role,
  parser language, human-authored/source status, test-like compatibility,
  exclusion reason). Production source remains strict; tests still fail
  suppression, broad catch/pass, TODO/FIXME/HACK, and `|| true` patterns.
- Base-tree owner capture covers production, test, and test-support source,
  kept apart per role so one rule family's discovery cannot widen or dirty
  another's.
- `--repo-context-packet` may widen owner discovery. `--gitnexus-context-json`
  is the external graph evidence the owner rules' caller/callee scope
  requires (parent #54 decision, 2026-08-12): only a document declaring the
  evaluated base and candidate establishes that scope, so an absent, unbound,
  stale, or malformed graph input leaves both owner rules incomplete as
  per-affected-rule `QG54-ANALYSIS-INCOMPLETE` evidence. The gate never
  creates reports, caches, or repository artifacts itself. In the governed
  workflow this input is supplied by `workflow.py verify --kind quality-gate`,
  which hands over the gate-shaped `{base, candidate, symbols}` context the
  Repo Context Forge bootstrap recorded snapshot-bound for the active pass
  (issue #106); the binding check here still adjudicates it, and a bare or
  per-edit-hook gate run continues to receive no evidence.
- The gate's evaluated hard-rule results are `cleanup` and
  `noMergeConflictMarkers`. `noDuplication` keeps its key and reports
  `not_evaluated`: every surviving duplication/owner rule is warning-only,
  and a warning cannot decide a hard rule.
- `consequenceCoverage` is explicitly `not_evaluated` unless caller-supplied
  evidence supports it. A syntax proxy must never be labeled consequence
  analysis.
- Legacy debt outside the changed surface does not block. Touched debt is fixed
  or recorded as a concrete blocker.
