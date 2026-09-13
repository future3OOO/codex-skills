from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .path_policy import PathClass

RULE_GROWTH = "QG54-GROWTH-CUMULATIVE"
RULE_INCOMPLETE = "QG54-ANALYSIS-INCOMPLETE"
RULE_DUPLICATE_SYMBOL = "QG54-DUPLICATE-ADDED-SYMBOL"
RULE_DUPLICATE_BLOCK = "QG54-DUPLICATE-ADDED-BLOCK"
RULE_DUPLICATE_BASELINE = "QG54-DUPLICATE-BASELINE"
RULE_OWNER_PRODUCTION = "QG54-OWNER-COMPETITION-PRODUCTION"
RULE_OWNER_TEST = "QG54-OWNER-COMPETITION-TEST"

# Warning promotion is decided by this immutable per-exact-rule-ID metadata and
# nothing else: never rendered text, prefixes, families, roles, or scores.
# QG54 rules start ineligible until parent #54 approves the exact ID. CLI
# optional-input transport failures never reach this table.
_PROMOTION_ELIGIBLE = {
    RULE_GROWTH: False,
    RULE_INCOMPLETE: False,
    RULE_DUPLICATE_SYMBOL: False,
    RULE_DUPLICATE_BLOCK: False,
    RULE_DUPLICATE_BASELINE: False,
    RULE_OWNER_PRODUCTION: False,
    RULE_OWNER_TEST: False,
}

# The scope kind for each gap the gate's own producers emit. Identity uses the
# kind, never the rendered text, so renaming a gap-referenced path cannot move
# a finding's ID; the raw strings stay in evidence.
_SCOPE_KINDS = (
    ("Git reported no line counts", "measurement"),
    ("reuse baseline", "baseline-discovery"),
    ("diff hunks matched no changed file", "attribution"),
    ("no caller-supplied base", "base-binding"),
    ("gitnexus context JSON ignored", "graph-input"),
    ("graph evidence", "graph-input"),
)


def anchor(kind: str, role: str, language: str, content: str) -> str:
    """A stable anchor over anchor kind, role, language, and anchored content.

    Role and language are part of the anchor because the architecture's
    identity formula is a fingerprint plus role/language: the same symbol name
    in a production file and in a test fixture is not the same debt.
    """
    payload = "\x1f".join((kind, role, language, content))
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def finding_id(rule_id: str, identity: tuple[str, ...]) -> str:
    joined = "\x1f".join((rule_id, *identity))
    return hashlib.sha256(joined.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def pass_condition(kind: str, requires: tuple[str, ...], statement: str) -> dict[str, object]:
    """A discriminated, mechanically rerunnable pass condition: the kind a
    consumer switches on, the anchors and scopes a rerun needs, and the
    human-readable statement. Free text alone cannot be rerun."""
    return {"kind": kind, "requires": list(requires), "statement": statement}


@dataclass(frozen=True)
class Numstat:
    """Counts for one path; `None` when Git reported the file as binary."""

    added: int | None
    deleted: int | None
    path: str


@dataclass(frozen=True)
class Hunk:
    """One diff hunk, kept whole: separate hunks are never joined."""

    added: tuple[tuple[int, str], ...]
    deleted: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class BaselineFile:
    """One base-tree source file with its classification and captured text.

    `text` is `None` when the file was never read: outside owner discovery,
    skipped by a capture bound, or the read itself failed.
    """

    path: str
    role: str
    language: str
    text: str | None


@dataclass(frozen=True)
class SnapshotEntry:
    """One changed path with its stored classification, text, hunks, and growth."""

    path: str
    classification: PathClass
    base_text: str | None
    current_text: str | None
    untracked: bool
    added: int
    deleted: int
    hunks: tuple[Hunk, ...]
    gaps: tuple[str, ...]

    def added_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.added]

    def deleted_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.deleted]


@dataclass(frozen=True)
class SymbolDef:
    name: str
    path: str
    line: int
    kind: str
    language: str
    content: str = ""


@dataclass(frozen=True)
class Finding:
    """One evaluated rule: its identity, region, evidence, and completeness.

    `status` uses the binding rule vocabulary — `passed`, `finding`,
    `incomplete`, or `not-evaluated` — and is `incomplete` whenever the rule's
    required scope had gaps, so a rule that could not see everything can never
    read as a clean pass. `passed` is the intrinsic check result (null while
    unknown); a null-state finding is active when emitted. `identity` carries
    the rule-family anchors; paths and line numbers in `region` are display
    provenance, never identity.
    """

    rule_id: str
    severity: str
    status: str
    passed: bool | None
    identity: tuple[str, ...]
    region: dict[str, object]
    evidence: dict[str, object]
    action: str
    pass_condition: dict[str, object]
    gaps: tuple[str, ...]
    # candidate | confirmed-unresolved | resolved for owner-competition
    # findings; every other rule family has no state machine and stays None.
    state: str | None = None

    def finding_id(self) -> str:
        return finding_id(self.rule_id, self.identity)

    def as_dict(self, base: str, candidate: str) -> dict[str, object]:
        serialized = {
            "ruleId": self.rule_id,
            "findingId": self.finding_id(),
            "severity": self.severity,
            "status": self.status,
            "passed": self.passed,
            "state": self.state,
            "base": base,
            "candidate": candidate,
            "region": self.region,
            "evidence": self.evidence,
            "action": self.action,
            "passCondition": self.pass_condition,
            "completeness": {"complete": not self.gaps, "gaps": sorted(self.gaps)},
        }
        # A JSON round trip hands the caller its own copy, so later mutation of
        # the returned structure can never reshape the evaluated finding.
        return json.loads(json.dumps(serialized, sort_keys=True))


def _scope_kind(gap: str) -> str:
    for marker, kind in _SCOPE_KINDS:
        if marker in gap:
            return kind
    return "capture"


def incompleteness_findings(findings: list[Finding]) -> list[Finding]:
    """One QG54-ANALYSIS-INCOMPLETE finding per affected rule ID and scope
    kind, giving each missing scope a stable identity."""
    projected: list[Finding] = []
    for finding in findings:
        if finding.status != "incomplete":
            continue
        by_kind: dict[str, list[str]] = {}
        for gap in finding.gaps:
            by_kind.setdefault(_scope_kind(gap), []).append(gap)
        for kind in sorted(by_kind):
            projected.append(
                Finding(
                    rule_id=RULE_INCOMPLETE,
                    severity="warning",
                    status="finding",
                    passed=True,
                    identity=(finding.rule_id, kind),
                    region={"scope": "rule", "affectedRuleId": finding.rule_id, "scopeKind": kind},
                    evidence={"affectedRuleId": finding.rule_id, "scopeKind": kind, "gaps": sorted(by_kind[kind])},
                    action="Restore the missing scope (readable inputs, complete discovery, a resolvable base) and rerun.",
                    pass_condition=pass_condition(
                        "analysis-complete",
                        (f"scope:{kind}", f"rule:{finding.rule_id}"),
                        f"required {kind} scope for {finding.rule_id} is complete",
                    ),
                    gaps=(),
                )
            )
    return projected


def promoted_errors(findings: list[Finding], fail_on_warnings: bool) -> list[str]:
    """Errors for active warning findings whose exact rule ID is eligible.

    Promotion never retypes the source finding or its intrinsic check. Three
    conditions, each excluding a different wrong promotion: `passed is True`
    excludes a rule whose intrinsic result is unknown, so missing scope alone
    never becomes the failure; an active status excludes a clean pass, which is
    also `severity=warning` with `passed=True` and would otherwise fail every
    green run; eligibility is the exact-ID metadata.
    """
    if not fail_on_warnings:
        return []
    return [
        f"warning promoted to failure: {finding.rule_id} [{finding.finding_id()}]"
        for finding in findings
        if finding.severity == "warning"
        and finding.passed is True
        and finding.status in ("finding", "incomplete")
        and _PROMOTION_ELIGIBLE.get(finding.rule_id, False)
    ]
