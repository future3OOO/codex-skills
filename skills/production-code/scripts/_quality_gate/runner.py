from __future__ import annotations

from pathlib import Path

from .checks import changed_file_failures, evaluate_growth, scan_quality_escapes
from .git_scope import collect_scope
from .findings import RULE_GROWTH, Finding, incompleteness_findings, promoted_errors
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

    # One walk builds checks and errors from the typed outcomes; the hard rules
    # derive from the same outcome column. A rule that could not see its whole
    # scope is unknown, never a pass; a violation it did see stays a violation;
    # an active warning-only rule keeps its intrinsic pass visible. Warning
    # rules report once, as `findings`, never re-rendered as strings.
    checks: list[dict[str, object]] = []

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

    # One projection per exact rule ID, named by that ID: promotion, calibration,
    # and consumers all address these rules exactly, never by family or prefix.
    checks.extend({"name": rule.rule_id, **projected(rule)} for rule in (*duplicate_rules, *owner_rules))
    checks.append({"name": "cumulative-growth", **projected(growth_rule)})
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
        "warnings": [],
        # Retained until its documented consumer migrates; its scorer is gone.
        "gitnexusQueries": [],
    }


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
    for check in result["checks"]:
        outcome = "incomplete" if check["passed"] is None else "pass" if check["passed"] else "fail"
        lines.append(f"- {check['name']}: {outcome}" + (f" ({', '.join(check['sample'])})" if check.get("sample") else ""))
    lines += ["", "Errors:", *([f"- {error}" for error in result["errors"]] or ["- none"]), "", "Warnings:"]
    # Measured growth stays visible even when an unbased run leaves the claim incomplete; then each
    # concrete finding, located (rule-level records are the `Checks` lines).
    net = result["evaluation"]["growth"]["humanAuthored"]["net"]
    active = [f"{RULE_GROWTH}: human-authored net growth {net} exceeds the 500-line review budget"] if net > 500 else []
    active += [" ".join(filter(None, (f"{item['ruleId']} [{item['findingId']}]", item["state"], item["region"].get("evidenceClass"),
               item["evidence"].get("responsibilityKey"), "for " + item["evidence"]["affectedRuleId"] if "affectedRuleId" in item["evidence"] else None))) + ": "
               + ", ".join(item["evidence"].get("gaps") or item["evidence"].get("owners") or [f"{r['path']}:{r['displayLine']}" for g in item["evidence"].get("duplicates", ()) for r in g["regions"]])
               for item in result["findings"] if item["status"] == "finding" and item["region"]["scope"] != "evaluation"]
    lines.extend([f"- {warning}" for warning in active + result["warnings"]] or ["- none"])
    return "\n".join(lines)
