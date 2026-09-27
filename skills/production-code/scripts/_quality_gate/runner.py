from __future__ import annotations

from pathlib import Path

from .bloat import find_bloat
from .checks import changed_file_failures, evaluate_growth, scan_quality_escapes
from .findings import RULE_BLOAT, RULE_GROWTH, Finding, incompleteness_findings, promoted_errors
from .git_scope import collect_scope, git_read
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
    gitnexus_context_json: str = "",
    staged_only: bool = False,
    bloat: tuple | None = None,
) -> dict[str, object]:
    scope = collect_scope(repo, base_ref, staged_only=staged_only)
    errors: list[str] = list(scope["errors"])
    snapshot = EvaluationSnapshot.from_scope(repo, scope, gitnexus_context_json, with_sources=bloat is not None)

    conflicts, temps = changed_file_failures(snapshot)
    found = {
        "no-merge-conflict-markers": conflicts,
        "no-temp-artifacts": temps,
        "no-quality-escapes": scan_quality_escapes(snapshot),
    }
    growth_rule = evaluate_growth(snapshot)
    # Jev's answers are kept in the repository's Git directory.
    common, failure = git_read(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]) if bloat is not None else ("", "off")
    bloat_rules = [] if bloat is None else find_bloat(snapshot, *bloat, store=None if failure else Path(common.strip()) / "codex-quality-gate" / "jev-answers.jsonl")
    findings: list[Finding] = [growth_rule, *bloat_rules]
    findings.extend(incompleteness_findings(findings))

    streams = snapshot.gap_streams()
    # The escape scan needs attributed, measured, captured hunks; the path rules need capture only.
    gaps_for = {"capture": streams["capture"], "attribution": streams["attribution"] + streams["measurement"] + streams["capture"]}

    # A rule that could not see its whole scope is unknown, never a pass; a violation it did see stays one.
    checks: list[dict[str, object]] = []
    for name, template, cap, stream in _SIMPLE_CHECKS:
        items, gaps = found[name], list(gaps_for[stream])
        if items:
            errors.append(template.format(n=len(items)))
        passed = False if items else None if gaps else True
        status = "finding" if items else "incomplete" if gaps else "passed"
        checks.append({"name": name, "sample": items[:cap], "passed": passed, "status": status, **({"gaps": gaps} if gaps else {})})
    for name, rule in ((RULE_BLOAT, bloat_rules[0] if bloat_rules else None), ("cumulative-growth", growth_rule)):
        if rule:
            checks.append({"name": name, "passed": rule.passed, "status": rule.status, **({"gaps": sorted(rule.gaps)} if rule.status == "incomplete" else {})})
    errors.extend(promoted_errors(findings, fail_on_warnings))

    outcome = {item["name"]: item["passed"] for item in checks}

    def hard_rule(*names: str) -> dict[str, object]:
        # A contributing failure is established; otherwise an unknown contributor leaves the rule unknown.
        results = [outcome[name] for name in names]
        passed = False if False in results else None if None in results else True
        return {"status": "incomplete" if passed is None else "evaluated", "passed": passed, "checks": list(names)}

    evaluation_gaps: set[str] = set().union(*streams.values())
    for finding in findings:
        evaluation_gaps.update(finding.gaps)
    return {
        "schemaVersion": 3,
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
        "checks": checks,
        "hardRules": {
            "cleanup": hard_rule("no-quality-escapes", "no-temp-artifacts"),
            "noMergeConflictMarkers": hard_rule("no-merge-conflict-markers"),
        },
        "errors": errors,
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
    active += [f"{item['ruleId']} [{item['findingId']}] {item['region'].get('category') or 'for ' + item['evidence'].get('affectedRuleId', '')}: "
               + ", ".join(item["evidence"].get("gaps") or item["evidence"].get("owners") or [])
               for item in result["findings"] if item["status"] == "finding" and item["region"]["scope"] != "evaluation"]
    lines.extend([f"- {warning}" for warning in active] or ["- none"])
    return "\n".join(lines)
