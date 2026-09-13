from __future__ import annotations

from pathlib import Path

from .checks import changed_file_failures, evaluate_growth, scan_quality_escapes
from .git_scope import collect_scope
from .findings import Finding, RULE_GROWTH, RULE_INCOMPLETE, incompleteness_findings, promoted_errors
from .redundancy import find_exact_duplicates, find_owner_competition
from .snapshot import EvaluationSnapshot

GATE_VERSION = "2026-08-10.1"

# The immediate checks, each stated once: name, the error reported on a find,
# sample cap, and which gap stream makes an otherwise-clean result unknown.
_SIMPLE_CHECKS = (
    ("no-merge-conflict-markers", "merge conflict markers found in {n} file(s)", 10, "capture"),
    ("no-temp-artifacts", "temporary artifact paths detected in {n} changed file(s)", 10, "capture"),
    ("no-quality-escapes", "quality escapes detected in {n} changed location(s)", 10, "attribution"),
)


def check(
    repo: Path,
    base_ref: str | None,
    fail_on_warnings: bool,
    repo_context_packet: str = "",
    gitnexus_context_json: str = "",
    staged_only: bool = False,
) -> dict[str, object]:
    scope = collect_scope(repo, base_ref, staged_only=staged_only)
    errors: list[str] = list(scope["errors"])
    snapshot = EvaluationSnapshot.from_scope(repo, scope, repo_context_packet, gitnexus_context_json)

    conflicts, temps = changed_file_failures(snapshot)
    found = {
        "no-merge-conflict-markers": conflicts,
        "no-temp-artifacts": temps,
        "no-quality-escapes": scan_quality_escapes(snapshot),
    }
    growth_rule = evaluate_growth(snapshot)
    duplicate_rules, duplicates = find_exact_duplicates(snapshot)
    owner_rules, owner_candidates, owner_resolved = find_owner_competition(snapshot, duplicates)
    duplicate_warnings = {rule.rule_id: _duplicate_warnings(rule) for rule in duplicate_rules}
    findings: list[Finding] = [growth_rule, *duplicate_rules, *duplicates, *owner_rules, *owner_candidates]
    findings.extend(incompleteness_findings(findings))

    streams = snapshot.gap_streams()
    # The escape scan cannot claim it saw the whole change when hunks are
    # unattributed, capture failed, or a source file's counts were never
    # measured (Git supplied no hunks to inspect); the path-reading rules
    # depend on capture only. The exact-duplicate rules carry their own
    # equivalent scopes, which redundancy.py owns.
    gaps_for = {
        "capture": streams["capture"],
        "attribution": streams["attribution"] + streams["measurement"] + streams["capture"],
    }

    # One walk builds checks, warnings, and errors from the typed outcomes; the
    # hard rules derive from the same outcome column. A rule that could not see
    # its whole scope is unknown, never a pass; a violation it did see stays a
    # violation; an active warning-only rule keeps its intrinsic pass visible.
    checks: list[dict[str, object]] = []
    warnings: list[str] = []

    for name, template, cap, stream in _SIMPLE_CHECKS:
        items = found[name]
        gaps = gaps_for[stream]
        if items:
            errors.append(template.format(n=len(items)))
            checks.append({"name": name, "sample": items[:cap], "passed": False, "status": "finding", **({"gaps": list(gaps)} if gaps else {})})
        elif gaps:
            checks.append({"name": name, "sample": [], "passed": None, "status": "incomplete", "gaps": list(gaps)})
        else:
            checks.append({"name": name, "sample": [], "passed": True, "status": "passed"})

    def projected(rule: Finding) -> dict[str, object]:
        out: dict[str, object] = {"passed": rule.passed, "status": rule.status}
        if rule.status == "incomplete":
            out["gaps"] = sorted(rule.gaps)
        return out

    net = growth_rule.evidence["humanAuthored"]["net"]
    # The measured growth is reported whether or not the claim is also
    # incomplete: incompleteness qualifies the number, it does not delete it.
    growth_warning = f"{RULE_GROWTH}: human-authored net growth {net} exceeds the 500-line review budget" if net > 500 else ""
    # One projection per exact rule ID, named by that ID: promotion, calibration,
    # and consumers all address these rules exactly, never by family or prefix.
    checks.extend(
        {"name": rule.rule_id, "warnings": duplicate_warnings[rule.rule_id], **projected(rule)}
        for rule in duplicate_rules
    )
    owner_warnings = {rule.rule_id: _owner_warnings(rule.rule_id, owner_candidates) for rule in owner_rules}
    checks.extend(
        {"name": rule.rule_id, "warnings": owner_warnings[rule.rule_id], **projected(rule)}
        for rule in owner_rules
    )
    checks.append({"name": "cumulative-growth", "warnings": [growth_warning] if growth_warning else [], **projected(growth_rule)})

    for finding in findings:
        if finding.rule_id == RULE_INCOMPLETE:
            warnings.extend(f"{RULE_INCOMPLETE} for {finding.evidence['affectedRuleId']}: {gap}" for gap in finding.evidence["gaps"])
    if growth_warning:
        warnings.append(growth_warning)
    for rule in duplicate_rules:
        warnings.extend(duplicate_warnings[rule.rule_id])
    for rule in owner_rules:
        warnings.extend(owner_warnings[rule.rule_id])
    errors.extend(promoted_errors(findings, fail_on_warnings))

    outcome = {item["name"]: item["passed"] for item in checks}

    def hard_rule(*names: str) -> dict[str, object]:
        # Same lattice as a single check: a contributing failure is established
        # and an unknown sibling cannot undo it, while an unknown contributing
        # check still leaves an otherwise-passing rule unestablished.
        results = [outcome[name] for name in names]
        if any(result is False for result in results):
            return {"status": "evaluated", "passed": False, "checks": list(names)}
        if any(result is None for result in results):
            return {"status": "incomplete", "passed": None, "checks": list(names)}
        return {"status": "evaluated", "passed": True, "checks": list(names)}

    evaluation_gaps: set[str] = set().union(*streams.values())
    for finding in findings:
        evaluation_gaps.update(finding.gaps)
    return {
        "schemaVersion": 2,
        "gateVersion": GATE_VERSION,
        "ok": not errors,
        "repo": str(repo),
        "changedScope": scope["changed_scope"],
        "candidateSource": scope["candidate_source"],
        "candidateTree": scope["candidate_tree"] or None,
        "changedFilesCount": len(snapshot.entries),
        "changedFilesSample": sorted(entry.path for entry in snapshot.entries)[:30],
        "sourceFilesCount": len(snapshot.role_entries("production")),
        "evaluation": {
            "base": {"commit": snapshot.base_identity, "source": snapshot.base_source},
            "candidate": {"identity": snapshot.candidate_identity, "tree": snapshot.candidate_tree or None},
            "growth": growth_rule.evidence,
            "complete": not evaluation_gaps,
            "gaps": sorted(evaluation_gaps),
        },
        "findings": [finding.as_dict(snapshot.base_identity, snapshot.candidate_identity) for finding in findings],
        "resolvedFindings": [finding.as_dict(snapshot.base_identity, snapshot.candidate_identity) for finding in owner_resolved],
        "checks": checks,
        "hardRules": {
            # Hard rules derive from blocker policy only; every surviving
            # duplication/owner rule is warning-only.
            "noDuplication": {
                "status": "not_evaluated",
                "passed": None,
                "checks": [],
                "reason": "no blocker-eligible duplication rule remains; QG54 duplicate and owner rules are warning-only",
            },
            "cleanup": hard_rule("no-quality-escapes", "no-temp-artifacts"),
            "noMergeConflictMarkers": hard_rule("no-merge-conflict-markers"),
            "consequenceCoverage": {
                "status": "not_evaluated",
                "passed": None,
                "checks": [],
                "reason": "requires caller-supplied contract and GitNexus impact evidence",
            },
        },
        "errors": errors,
        "warnings": warnings,
        # Retained until its documented consumer migrates; its scorer is gone.
        "gitnexusQueries": [],
    }


def _duplicate_warnings(rule: Finding) -> list[str]:
    """One warning per duplicate group, naming every region that carries it."""
    return [
        f"{rule.rule_id}: identical implementation in "
        + ", ".join(f"{region['path']}:{region['displayLine']}" for region in group["regions"])
        for group in rule.evidence["duplicates"]
    ]


def _owner_warnings(rule_id: str, candidates: list[Finding]) -> list[str]:
    """One warning per active owner candidate, naming its evidence class and
    every competing owner region."""
    return [
        " ".join(filter(None, (f"{rule_id}: {candidate.state}", candidate.region["evidenceClass"],
                               candidate.evidence.get("responsibilityKey"),
                               "competing owners", ", ".join(candidate.evidence["owners"]))))
        for candidate in candidates
        if candidate.rule_id == rule_id and candidate.state in ("candidate", "confirmed-unresolved")
    ]


def format_text(result: dict[str, object]) -> str:
    lines = [
        "Production Code Quality Gate",
        f"verdict: {'pass' if result['ok'] else 'fail'}",
        f"changedScope: {result['changedScope']}",
        f"changedFilesCount: {result['changedFilesCount']}",
        f"sourceFilesCount: {result['sourceFilesCount']}",
        "",
        "Checks:",
    ]
    for check_item in result["checks"]:
        outcome = "incomplete" if check_item["passed"] is None else "pass" if check_item["passed"] else "fail"
        lines.append(f"- {check_item['name']}: {outcome}")
    lines.append("")
    lines.append("Errors:")
    lines.extend([f"- {error}" for error in result["errors"]] if result["errors"] else ["- none"])
    lines.append("")
    lines.append("Warnings:")
    lines.extend([f"- {warning}" for warning in result["warnings"]] if result["warnings"] else ["- none"])
    return "\n".join(lines)
