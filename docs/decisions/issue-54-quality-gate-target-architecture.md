# Decision: Issue #54 quality-gate target architecture

Date: 2026-08-05. Status: proposed; not binding until PR #79 is reviewed and
merged.

This is the normative completed-state architecture for issues #75, #76, and #77.
The
[`issue-54-quality-gate-target-architecture.html`](issue-54-quality-gate-target-architecture.html)
companion is a non-normative visual index; this Markdown controls whenever the
two differ.

This decision supplements, and does not replace or re-sequence, the
[`issue-54-quality-gate-delivery-2026-08-05.md`](../plans/issue-54-quality-gate-delivery-2026-08-05.md)
plan. The agent already executing #75 continues against that approved slice.
Parent #54 remains the human calibration and promotion gate. Issue #49 remains
the sole owner of review freshness, final-tree binding, workflow state, and
persistence; [PR #74](https://github.com/future3OOO/claude-skills/pull/74) is
its active implementation and is a no-change surface for #54.

## Binding versus illustrative content

Binding in this decision:

- Module responsibility and forbidden-dependency boundaries;
- caller-visible Interfaces and versioned data contracts;
- evaluation ordering and ownership;
- rule-family independence, identity, state, severity, completeness, and
  resolution invariants;
- required deletion and old-to-survivor ownership;
- no-change surfaces and externally observable proof.

Illustrative and private until an owning slice proves the real seam:

- Python helper/function names other than `runner.check`;
- private dataclass names and field layout;
- the filename and helper shape of shared test support;
- private capture, parsing, indexing, and normalization phases;
- the slice-convergence sketch in this document.

Private shapes may change without revising this decision when the binding
owner, Interface, behavior, and deletion contract remain intact.

## Final ownership

| Module | Binding responsibility | Must not own |
|---|---|---|
| `runner.py` | The stable call Interface, fixed evaluation order, rule-policy projection, and result serialization | Git/filesystem reads, classification, diff parsing, detector algorithms, workflow state |
| `context.py` | One immutable evaluation containing captured base/candidate evidence, stored classification, hunks, growth, capture gaps, and external evidence | Rule severity, display strings, persistence |
| `git_scope.py` | Resolve the base it is given and capture one coherent base-to-candidate comparison | Choosing workflow/PR policy, path roles, findings, ownership inference |
| `path_policy.py` | The quality gate's sole path/role/language classifier and the standalone test-like compatibility predicate consumed by workflow state | Snapshot, findings, or workflow imports |
| `inputs.py` | Decode and structurally validate captured evidence and the versioned disposition data Interface selected by #77 | Trust, semantic authority, finding state, severity |
| `checks.py` | Existing immediate safety blockers and cumulative production/test/test-support growth | Duplicate or responsibility analysis, independent role/diff walks |
| `redundancy.py` | One deep Module containing independently driveable exact-duplication and owner-competition paths plus disposition/resolution; neither rule family gates the other | Classification, Git/filesystem reads, live advisor/GitNexus calls, persistence |
| `symbols.py` | Language-aware extraction and canonicalization of exact syntax/code regions | Name/token similarity, semantic responsibility, severity |
| `findings.py` | Stable rule policy, structured finding/result contracts, completeness projection, and canonical serialization | Repository reads or detection |
| `cli.py` | Arguments, optional-input transport, rendering, and process exit | Evaluation or rule policy |

`hooks/code-quality-gate.py` is the PostToolUse Adapter. The standalone
`code_quality_gate.py` remains a thin entrypoint. They are outside the internal
detector Module boundary.

There is no detector protocol, runtime registry, topological scheduler,
plugin loader, filesystem port, live advisor client, live GitNexus client, or
quality-gate persistence Adapter. The fixed roster is ordinary, literal
control flow.

## Caller-visible Interfaces

### 1. Stable call Interface

The positional order, defaults, and six parameters stay unchanged through #77:

```python
def check(
    repo: Path,
    base_ref: str | None,
    fail_on_warnings: bool,
    repo_context_packet: str = "",
    gitnexus_context_json: str = "",
    staged_only: bool = False,
) -> dict[str, object]:
    ...
```

This is signature compatibility, not byte-for-byte or semantic compatibility.
No seventh parameter is added, and neither existing evidence parameter is
overloaded with an unrelated document.

### 2. Versioned result Interface

Issue #75 advances `schemaVersion` from 1 to 2. #76 and #77 may add optional v2
fields but may not remove or retype a v2 field. Removal/retyping requires a
later schema version and parent decision. `gateVersion` identifies the build;
it does not reinterpret an existing rule ID.

Schema v2 adds the binding collections `evaluation`, `findings`, and
`resolvedFindings`. It removes the unconsumed `bloat` and `reuseFindings`
projections. It retains `checks`, `hardRules`, `errors`, `warnings`, and
`gitnexusQueries` as projections from the one typed report.

### 3. Versioned disposition-evidence Interface

Responsibility dispositions are a second external data Interface, even though
they do not change the Python signature. #77 owns the exact carrier/path and
must version it; this decision does not mandate a candidate-tree filename.

> Parent amendment (#54 issuecomment-5259793024, 2026-08-12): v1 is exactly
> the field set the four parent-pinned record digests were computed over —
> ruleId, responsibilityKey, disposition, repair where applicable, base,
> candidate, owner anchors, survivor where applicable, parentRecord, and
> schemaVersion, with duplicate-reference rejection validation-side. The
> wider list below is v2 territory requiring a parent re-pin of the digest
> table; anything v1 cannot express leaves the finding active.

The full data contract must carry:

- its schema version and the evaluated base/candidate identities;
- the target finding ID and content anchors, never wildcard/path-only claims;
- the responsibility key, role, existing and competing owner anchors;
- the semantic disposition: `same-responsibility`, `distinct-authority`, or
  `temporary-coexistence`;
- for same-responsibility or coexistence, the intended repair: `deepen`,
  `replace`, or `consolidate`;
- survivor, superseded surfaces, affected reference anchors, and evidence
  references;
- for coexistence, the tracked follow-up and expiry slice;
- an immutable validation-record identifier and digest when a semantic claim
  is expected to affect state.

Unknown versions, missing/stale/duplicate references, wildcards, and unresolved
anchors leave the finding active and emit rule-specific incompleteness. Trust
must come from an immutable validation root outside the candidate tree whose
identifier and digest are explicitly bound by parent #54: the parent-pinned
owner manifest or another parent-approved external advisor/human review record.
A provenance string inside the candidate tree does not establish independence.

A validated `same-responsibility` disposition moves a candidate to
`confirmed-unresolved`. A `distinct-authority` disposition moves it to
`resolved` telemetry only when every referenced structural anchor is
snapshot-valid and resolves, every owner-discovery evidence class and required
graph/test scope is complete, and the decision matches that external validation
root. Otherwise it remains an active `candidate`. Candidate-authored,
candidate-modified, stale, or merely previously committed assertions cannot
resolve themselves. `temporary-coexistence` always moves to and remains
`confirmed-unresolved`.

`deepen`, `replace`, and `consolidate` are repair declarations, not finding-state
transitions. A candidate reaches `resolved` only through validated
`distinct-authority`; a `confirmed-unresolved` finding reaches `resolved` only
through the structural predicate in the one-owner reduction contract. A
disposition never clears a `QG54-DUPLICATE-*` finding while its duplicate
occurrences remain and can never assert that gate-computed scope is complete.

## Base and candidate contract

Base selection belongs to the caller Adapter. `git_scope.py` resolves and
captures the base it receives; it never invents workflow/PR policy.

| Mode | Base contract | Candidate contract |
|---|---|---|
| `staged_only=True` | explicit `base_ref` is required and resolves to one commit | the captured Git index tree OID |
| worktree with explicit base | caller-supplied `base_ref` resolves to one commit | one coherent captured worktree/index/untracked view |
| worktree without explicit base | `HEAD`; reported as `baseSource=HEAD` | one coherent captured working view |

The `HEAD~1` clean-tree fallback is deleted. An unbound `HEAD` result describes
only the working delta and cannot claim branch-cumulative growth. The
PostToolUse Adapter supplies its trusted branch/PR base when it has one; when it
does not, growth is visibly incomplete for the cumulative claim rather than
silently clean. Corpus and PR/completion evaluation always supply an exact
base.

Snapshot construction produces one internally consistent comparison from
captured bytes. It does not concatenate commit, index, dirty, and untracked
diffs, and detectors never reread live files. Separate hunks stay separate.
The private capture mechanism must either capture a coherent tree or detect
pre/post drift and record a capture gap; the architecture does not mandate a
particular temporary-index or manifest implementation.

Candidate identity is discriminated:

```text
git-tree          tree OID for staged evaluation
worktree-snapshot gate-owned digest for edit-time provenance only
```

Neither identity authorizes review. #49 owns its different review manifest and
must never accept or recreate the gate's worktree digest. A later-promoted
completion rule is applied only through #49's own bound review/final-tree
contract.

## Canonical classification and snapshot

Every entry stores one `path_policy` result containing at least:

```text
role: production | test | test-support | docs | generated | vendored | unknown
language: python | javascript | go | rust | shell | php | ruby | other
humanAuthored: bool
source: bool
testLikeCompat: bool
exclusionReason: string | null
```

The role and language are stored once. Checks, redundancy analysis, symbol
extraction, runner counts, and result projection never call `path_policy`
again.

`is_test_like_path(path)` remains standalone-loadable at
`skills/production-code/scripts/_quality_gate/path_policy.py` and returns the
result of `classify_path(path).testLikeCompat`; it is not inferred from role
alone and does not require an evaluation snapshot. The
pre-#75 truth must remain identical, including generated paths and
`*.schema.json` being test-like. A full-history characterization over every
repository path proves zero differences between the old and new predicates.

The real compatibility dependency is:

```text
workflow_state → state_store → path_policy.is_test_like_path
```

`state_store` retains its own reviewable, governance, code, and documentation
decisions. It delegates only test-like classification. Workflow code never
imports the snapshot, findings, checks, or redundancy Modules.

The immutable evaluation owns captured base/candidate text or deletion state,
old/new hunk ranges, contiguous regions, stored classification, production and
test/test-support growth, repository-index coverage, captured external
evidence, and capture gaps. These are responsibilities, not mandated private
Python field names.

## Completeness

Capture completeness and rule completeness are different contracts:

- **Capture gaps** record unreadable, unsupported, ambiguous, skipped,
  truncated, cap-reached, or drifted files/hunks/anchors/references.
- **Rule completeness** selects only gaps relevant to the rule's required file,
  hunk, anchor, owner-discovery, caller, callee, test, and test-support scopes.

Owner-rule completeness also covers every mechanical evidence class:
state/external-boundary writers, invariant validators, public/test surfaces,
workflow/lifecycle coordinators, caller/callee parallel entry points,
fixture/builder/harness lifecycle, forwarding shape, and exact implementation.
Each class is evaluated against the eligible repository index even when it
produces zero candidates. An omitted, unimplemented, skipped, capped, or
truncated class makes the affected owner rule incomplete; evaluating exact and
forwarding evidence alone can never report owner discovery complete.
Missing required external graph evidence makes its caller/callee evidence class
incomplete; an empty optional input cannot be interpreted as a completed scan
with zero candidates.

`findings.py` owns the literal mapping from stable rule ID to required scopes.
The gate emits one `QG54-ANALYSIS-INCOMPLETE` finding per affected rule ID and
scope kind. Unrelated unknown files do not dirty an unrelated rule. A cap may
protect execution, but reaching it makes the affected scope incomplete; no
configuration, allowlist, exclusion, or cap can shrink a denominator into a
clean or resolved result.

Rule status is `passed`, `finding`, `incomplete`, or `not-evaluated`. At edit
time an incomplete QG54 rule remains non-blocking but visibly non-clean. If a
named rule later becomes blocker-eligible, its required incomplete scope blocks
that rule's completion projection automatically.

## Fixed evaluation flow

```text
caller selects base
→ capture one base and candidate
→ classify each path once, including language and compatibility truth
→ decode and structurally validate captured evidence
→ freeze the immutable evaluation
→ immediate safety checks
→ cumulative growth
→ exact added/added and added/retained-baseline findings from the snapshot
→ independently generate responsibility candidates from every owner evidence
  class using the snapshot; exact evidence is optional input, never an entry
  requirement
→ validate snapshot-bound dispositions, confirm conflicts or legitimate
  distinctions, and evaluate structural resolution
→ validate finding identities, references, and rule completeness
→ project active warnings, resolved telemetry, compatibility keys, and exit
```

Detectors consume only the frozen evaluation. They cannot read Git, disk,
`path_policy`, workflow state, live GitNexus, or a live advisor.

## Stable rules and severity

Rule IDs match independent calibration/promotion units:

| Rule ID | Evidence unit | Initial edit-time severity |
|---|---|---|
| `QG54-ANALYSIS-INCOMPLETE` | one affected rule ID and scope kind | warning / visibly incomplete |
| `QG54-GROWTH-CUMULATIVE` | production plus test/test-support cumulative human-authored growth | warning |
| `QG54-DUPLICATE-ADDED-SYMBOL` | exact complete added symbol/helper body | warning |
| `QG54-DUPLICATE-ADDED-BLOCK` | calibrated exact contiguous added block | warning |
| `QG54-DUPLICATE-BASELINE` | exact added implementation whose baseline anchor remains in the candidate | warning |
| `QG54-OWNER-COMPETITION-PRODUCTION` | mechanically evidenced competing production owners | warning |
| `QG54-OWNER-COMPETITION-TEST` | mechanically evidenced competing test/test-support owners | warning |

Changing a rule's meaning requires a new ID. Promotion is by one exact rule ID,
never by prefix, family, role field, or score. A promotion record names the
rule ID, effective decision, #49 binding requirement, and completion-verdict
projection. The calibration slices make no promotion decision.

Immediate merge-marker, temporary-artifact, and quality-escape blockers remain
separate existing rule policies with `severity=error`. All structured findings
share one model whose severity permits `error` or `warning`; only QG54 rules
are constrained to warning in these slices.

`QG54-DUPLICATE-*` and `QG54-OWNER-COMPETITION-*` are separate rule families.
The former asks whether implementation was copied; the latter asks whether
multiple surfaces try to decide, mutate, validate, orchestrate, or maintain one
responsibility. One evaluation may trigger either family, both, or neither.

### Approved schema-v2 `fail_on_warnings` policy

In schema v2, `fail_on_warnings` evaluates typed active findings through each
exact rule ID's explicit warning-promotion eligibility. Each QG54 rule ID
enumerated for #75–#77 initially has promotion eligibility disabled until
parent #54 explicitly approves that exact ID. Surviving non-QG54 warning rules
retain their individually defined existing eligibility. Promotion never uses
rendered strings, prefixes, families, roles, scores, or free-text explanations.
CLI input-transport failures remain governed separately by the CLI transport
contract.

Schema v2 therefore assigns an exact rule ID and explicit eligibility to every
surviving warning rule. Eligibility is internal immutable rule-policy metadata
owned by `findings.py`; it is not serialized per finding and is never supplied
by a caller. An untyped warning string is not a promotion input.

The schema-v2 transition has exactly two non-QG54 warning IDs:

| Exact rule ID | Lifetime | `fail_on_warnings` eligibility |
|---|---|---|
| `QG-LEGACY-REUSE-ADVISORY` | #75 and #76; deleted with lexical reuse scoring in #77 | enabled, preserving schema-v1 behavior |
| `QG-LEGACY-GITNEXUS-CONTEXT` | #75 and #76; replaced in #77 by per-affected-rule `QG54-ANALYSIS-INCOMPLETE` evidence | enabled, preserving schema-v1 behavior |

No other non-QG54 warning rule survives #75, and none survives the completed
#77 architecture. Per-file bloat warnings are replaced by
`QG54-GROWTH-CUMULATIVE`; malformed or missing graph evidence relevant to #77
becomes disabled `QG54-ANALYSIS-INCOMPLETE` evidence for each affected rule.
The CLI transport contract is the `cli.py` responsibility in the ownership
table above; optional-input read failures remain outside `runner.check`
promotion.

Promotion does not retype the source finding or change its intrinsic check
result. When `fail_on_warnings=true` selects an eligible exact ID, the finding
remains in `findings`, `warnings`, and `checks[].warnings` with
`severity=warning` and `passed=true`; the projection adds an `errors` entry
bound to that exact rule ID and sets top-level `ok=false`.

## PostToolUse visibility

Warning-only must mean non-blocking feedback, not discarded output.
`hooks/code-quality-gate.py` surfaces every active QG54 warning to the hook's
supported feedback channel while returning zero. Existing blockers still print
failure output and return nonzero. `resolvedFindings` never appears in edit-time
feedback.

The Adapter may parse schema-v2 JSON to select active warnings; the gate does
not gain a warnings-fail mode, suppression file, rate limiter, or verbosity
configuration. A real hook test executes the production hook, observes active
warning text, and proves exit zero.

## Exact evidence

Exact comparison preserves identifiers, literals, operators, control flow,
symbol boundaries, relative indentation, roles, languages, and hunk
boundaries. Canonicalization may remove tokenizer-proven comments, blank lines,
trailing whitespace, common outer indentation, and insignificant intra-line
whitespace outside literals. Tabs and spaces remain distinct where the
language treats them as distinct. Python uses its real tokenizer; regex comment
stripping is forbidden. If safe language-aware normalization is unavailable,
the affected exact rule is `not-evaluated` or `incomplete`, never guessed.

Complete symbol/helper bodies are considered before arbitrary blocks. A block
is contiguous, stays inside one hunk and symbol boundary, and uses a
versioned/calibrated threshold owned by #76 rather than this architecture.
Separate hunks never combine. Human-authored fixture/test-support code always
enters warning candidate analysis; calibration controls later blocker
eligibility, not whether it is inspected.

Added-to-baseline evidence requires a base anchor whose content still exists in
the captured candidate. Copying then deleting the baseline implementation
clears the retained-baseline rule. Calling/reusing the baseline owner does not
create a duplicate. When one occurrence qualifies as both added/added and
retained-baseline, the baseline rule owns the finding to avoid double-reporting
one defect.

## Finding identity and output

Every finding serializes:

```text
ruleId, findingId, severity, state
base/candidate evaluation identities
ordered regions with path, role, language, display lines, content anchor, evidence role
typed evidence and rule completeness
action and a discriminated rerunnable pass condition
```

`state` is null for growth, exact, safety, and incomplete findings. A null-state
finding is active when emitted. Responsibility state is `candidate`,
`confirmed-unresolved`, or `resolved`; only the first two are active.

Content anchors hash anchor kind, language, and canonical implementation bytes.
Finding identity is rule-family specific:

- duplicate: rule ID plus normalized implementation fingerprint and role/
  language;
- responsibility: rule ID plus responsibility key and canonically sorted owner
  content anchors;
- growth: rule ID plus the evaluated base/candidate identities;
- incompleteness: rule ID plus affected rule ID, scope kind, and relevant
  content anchor when present.

Paths, lines, and commits are provenance/display regions, not duplicate or
responsibility identity. Rename/move-only therefore preserves the debt's ID.
Regions are canonically sorted by content anchor, role, path, and display line.

Pass-condition kinds include at least `duplicate-absent`, `one-owner`,
`analysis-complete`, and `growth-below`; each names the anchors/scopes required
to rerun it on a later evaluation.

### Compatibility projection

| Typed result | Schema-v2 projection |
|---|---|
| immediate safety error | `errors`; `checks[].status=finding`; `passed=false`; `ok=false` |
| active QG54 finding | `findings`, `warnings`, and `checks[].warnings`; while its exact ID is ineligible, `status=finding`, `passed=true`, and `ok` unchanged; if parent #54 later enables that exact ID, `fail_on_warnings=true` keeps `severity=warning` and `passed=true`, adds an exact-ID `errors` projection, and sets `ok=false` |
| incomplete QG54 rule | `findings` and `warnings`; `status=incomplete`; `passed=null`; edit-time `ok` unchanged |
| QG54 rule not evaluated | `findings` and `warnings`; `status=not-evaluated`; `passed=null`; edit-time `ok` unchanged |
| active transitional non-QG54 warning | `findings`, `warnings`, and `checks[].warnings`; normally `passed=true` and `ok` unchanged; with `fail_on_warnings=true`, an eligible exact ID keeps `severity=warning` and `passed=true`, adds an exact-ID `errors` projection, and sets `ok=false` |
| resolved owner evidence | `resolvedFindings` only; never an active warning |
| cumulative metrics | `evaluation.growth`; no legacy `bloat` object |
| owner/reuse evidence | structured findings; no legacy `reuseFindings` object |
| graph follow-up query | retained `gitnexusQueries` projection until its documented consumer migrates |

`hardRules` is computed from blocker policy only. A QG54 warning cannot flip a
hard rule to failed through a compatibility key.

## One-owner reduction contract

**Responsibility-candidate generation MUST run independently of duplicate
detection. The absence of an exact-duplicate finding MUST NOT suppress,
downgrade, or prevent an owner-competition candidate. Exact duplication is one
evidence type, not an entry requirement for ownership analysis.**

Responsibility candidate generation is broad and mechanical. Its separately
recorded evidence types include:

- multiple writers of the same state or external boundary;
- multiple validators deciding the same invariant;
- overlapping public or test Interfaces for the same domain operation;
- competing workflow or lifecycle coordinators;
- shared caller/callee structures suggesting parallel entry points;
- multiple fixtures, builders, or harnesses owning the same test lifecycle;
- pure or near-pure forwarding surfaces; and
- exact retained or repeated implementation.

Names, suffixes, vocabulary, shared data, dependencies, and graph proximity may
help generate a `candidate`; none independently proves semantic authority. A
candidate records the mechanical reason it requires disposition, not a claim
that the gate knows which Module has legitimate authority.

Two implementations may share no text and still compete to own the same state
transition, validation rule, workflow phase, fixture lifecycle, or testing
responsibility. #77 is therefore wrong if it generates owner candidates only
from #76 exact matches, even if exact detection itself is perfect. #76 evidence
may strengthen #77 evidence; it does not bound #77 discovery and cannot by
itself prove the same responsibility. Exact duplicate code in legitimately
different roles may produce only a duplicate-family finding.

Disposition and independently validated evidence determine whether a broad
candidate represents distinct authority, temporary coexistence, or a genuine
same-responsibility conflict. A candidate becomes `confirmed-unresolved` only
when the snapshot-bound external validation root establishes the responsibility
key and competing anchors. Exact implementation or forwarding evidence can make
the retained conflict mechanically deterministic after that binding; neither
establishes semantic authority on its own. Broad structural signals alone stay
candidates requiring disposition.

The binding lifecycle is:

```text
mechanical signals
→ competing-owner candidate
→ externally validated, snapshot-bound disposition
→ confirmed conflict or legitimate distinction
→ deepen, replace, or consolidate
→ rewire consumers
→ delete superseded owner
→ mechanically resolved
```

`redundancy.py` remains one deep Module; this contract does not create another
public Interface, detector Module, registry, or scheduler. It requires two
independently driveable behavior paths through `runner.check`: exact-duplicate
evaluation and owner-competition evaluation. Illustrative private phases may
look like `find_exact_duplicates(snapshot)`,
`generate_responsibility_candidates(snapshot, graph_evidence)`,
`evaluate_dispositions(candidates, evidence)`, and
`evaluate_owner_resolution(confirmed_findings, candidate_snapshot)`. Their
names and signatures are not binding, and public tests do not bypass
`runner.check` to call them.

The counted owner set is bounded: contract-backed owner anchors plus every
mechanically generated structural, exact, or forwarding candidate in the
completely scanned eligible repository index. It is proof of the declared
responsibility contract, not a universal semantic ownership detector.

A confirmed finding resolves only when:

```text
the bounded owner discovery is complete and exactly one owner remains
and every declared superseded surface is absent
and no affected caller, test, or test-support reference reaches an old anchor
and every affected caller, test, and test-support path reaches the survivor
and caller, callee, test, and test-support rule scope is complete
```

Valid repairs deepen and absorb, replace and delete, or consolidate and delete.
Partial deepening, rename/move-only changes, a facade over retained owners,
prose, suppressions, self-authored semantic claims, wildcard allowances, or a
disposition while deterministic conflict remains do not resolve the finding.

## Test and calibration ownership

There is one shared test-support owner with a small scenario-evaluation
Interface over real temporary Git repositories and `runner.check`. Its exact
filename and private helpers are illustrative. Test Modules and corpus replay
must reuse it; none reimplements Git setup, gate invocation, normalization, or
result parsing.

Required public proof includes:

- all base/candidate modes and schema-v2 serialization;
- full-history `is_test_like_path` equivalence, including generated paths and
  `*.schema.json`;
- real PostToolUse warning visibility with exit zero;
- separate hunks, Python indentation, strings containing comment markers,
  one-token differences, rename/move identity, and retained-baseline deletion;
- real capture truncation where the second owner is missed, driven through
  `runner.check` without a hand-built snapshot or stubbed discovery;
- candidate self-authorization, stale/wildcard disposition evidence, and
  retained deterministic conflict;
- textually unrelated Modules writing the same state or external boundary
  produce an owner candidate with no `QG54-DUPLICATE-*` prerequisite;
- different validators deciding the same invariant produce an owner candidate;
- separate fixture, builder, or harness implementations owning the same test
  lifecycle produce an owner candidate;
- shared data with genuinely different authority or failure policy remains a
  negative owner-competition case;
- exact duplicate code in genuinely different roles does not automatically
  confirm same-responsibility ownership;
- one scenario exercises duplicate-only, owner-only, both-family, and
  neither-family results independently;
- a confirmed conflict remains active after rename, facade creation, or partial
  deepening and resolves only after rewiring and deletion leave one owner;
- one-owner deletion/rewiring and resolved-versus-active projection;
- mixed blocker/QG54 warning compatibility output.

Corpus replay requires the source checkout's full local history. It verifies
both commits locally, fails rather than fetches/skips if either is missing,
hashes the exact output of:

```bash
git \
  -c core.autocrlf=false \
  -c core.safecrlf=false \
  -c core.quotePath=true \
  -c diff.indentHeuristic=true \
  -c diff.suppressBlankEmpty=false \
  diff \
  --no-ext-diff \
  --no-textconv \
  --no-color \
  --diff-algorithm=myers \
  --no-renames \
  --unified=3 \
  --inter-hunk-context=0 \
  --abbrev=7 \
  --src-prefix=a/ \
  --dst-prefix=b/ \
  --line-prefix= \
  --submodule=short \
  --ignore-submodules=none \
  -O/dev/null \
  <base> <candidate>
```

These options are part of the fixture identity; changing one requires a parent
re-pin. Replay drives imported `runner.check` through the shared real-repository
harness.
Replay publishes every expected/unexpected finding and requires
`unexaminedCount=0`.

The captured PR #68 round-six corpus is fixed to:

- base `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa`;
- candidate `28cf04e63fa6eb598b938d3a78d782969538d9a9`;
- diff SHA-256
  `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a`;
- human-authored code `+1129/-8`, net `1121`.

This is the captured round-six corpus, not the complete PR. #77 additionally
requires the parent-pinned owner manifest; the agent cannot select or broaden
it, and every referenced commit must exist in this repository's local history.

## Mandatory old-to-survivor consolidation

| Superseded surface | Surviving owner | Delivery convergence |
|---|---|---|
| `GateContext`, raw scope/diff reads, `diff_utils.py`, `collect_added_lines`, `added_lines_with_untracked` | canonical evaluation in `context.py` | #75 |
| detector-local role/language predicates and `production_only` | stored `path_policy` classification in the evaluation | #75 |
| per-file limits, same-directory shrink credit, additive ratios | cumulative growth in `checks.py` | #75 |
| three-line windows, cross-hunk assembly, collapse heuristics, duplicate blocker path | exact private phases in `redundancy.py` | #76 |
| `reuse.py`, `ReuseFinding`, lexical name/token scores, `RISKY_BLOCK_RULE`, `REUSE_ACTION_TOKENS`, silent index caps | responsibility private phases in `redundancy.py` and structured data in `findings.py` | #77 |
| `models.py` and runner's parallel errors/warnings/check truth | owning capture/finding types and one runner serializer | across #75–#77 |
| duplicated test Git/run/assertion helpers | the one shared scenario-test owner | owning slice |

The replacement is judged by responsibility, not filename. Equivalent old
logic recreated in another helper does not satisfy deletion. Genuinely distinct
test layers and scenarios are retained.

`gate-policy.md` and production-code guidance must stop describing duplicate
windows, lexical scores, or per-file bloat proxies as blockers and must name the
structured rule contracts that replace them.

The existing 1,800 total, 1,200 per-Module, and 180 per-function limits (with
1,200/700/90 review triggers) are repo-local implementation safeguards for
this gate package. They are not #54 runtime code-budget rules and must not
encourage artificial Module splitting. The target remains roughly 1,500–1,600
production lines versus today's 1,304, after the mandatory deletions and
without a pre-authorized new justification.

## Non-authoritative convergence sketch

The delivery plan controls sequence and scope. This sketch only shows how the
binding owners converge:

| Slice | Convergence target |
|---|---|
| #75 | canonical evaluation, stored role/language/compatibility truth, rule/result foundation, cumulative growth, authoritative-base provenance, and visible non-blocking hook output; delete superseded context/diff/bloat paths |
| #76 | deepen the one redundancy owner with calibrated exact symbol/block/baseline evidence; delete old duplicate paths |
| #77 | deepen the same owner with duplicate-independent responsibility discovery, trusted disposition evidence, lifecycle and owner-corpus replay; delete lexical reuse paths |
| Parent #54 | review named calibration results and decide any later rule-specific promotion |

## Forbidden dependencies and completion audit

The following edges must remain absent:

- workflow code → evaluation, findings, checks, or redundancy;
- quality-gate code → #49 ledger, review manifest, or persistence;
- detectors → Git, filesystem, `path_policy`, workflow state, live GitNexus, or
  live advisor;
- #49 → gate worktree-snapshot digest;
- runner → detector registry/plugin scheduler;
- candidate-authored disposition → trusted semantic resolution.

The architecture is complete only when the stable call and versioned data
Interfaces are proven; active warnings are visible without blocking; every
detector consumes one classified/language-aware immutable evaluation; rule
completeness covers every required owner evidence class and prevents false
clean/resolved output; exact and owner families are independently driveable;
exact/owner findings have stable content identity and both source regions;
one-owner resolution proves deletion and rewiring; corpus replay is exact and
fully examined; all mandatory superseded responsibilities are absent; and
issue #49 and workflow ownership remain independent.
