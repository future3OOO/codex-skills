from __future__ import annotations

import re
from pathlib import Path

from .checks import duplicate_added_blocks, evaluate_bloat, scan_quality_escapes
from .context import GateContext
from .git_scope import collect_scope, read_file
from .inputs import parse_gitnexus_context_json, parse_repo_context_packet
from .path_policy import is_binary_path, is_production_source_path, is_temp_artifact
from .reuse import detect_reuse_issues


def check(
    repo: Path,
    base_ref: str | None,
    fail_on_warnings: bool,
    repo_context_packet: str = "",
    gitnexus_context_json: str = "",
) -> dict[str, object]:
    scope = collect_scope(repo, base_ref)
    errors: list[str] = list(scope["errors"])
    warnings: list[str] = []
    changed_files: set[str] = set(scope["changed_files"])
    gitnexus_boosts, gitnexus_warnings = parse_gitnexus_context_json(gitnexus_context_json)
    warnings.extend(gitnexus_warnings)
    ctx = GateContext.from_scope(repo, scope)

    conflict_files, temp_files = _changed_file_failures(repo, changed_files)
    quality_escapes = scan_quality_escapes(ctx)
    duplicates = duplicate_added_blocks(ctx)
    bloat_errors, bloat_warnings, bloat_details = evaluate_bloat(ctx)
    reuse_findings, gitnexus_queries = detect_reuse_issues(
        ctx,
        parse_repo_context_packet(repo_context_packet),
        gitnexus_boosts,
    )
    reuse_errors = [finding for finding in reuse_findings if finding.severity == "error"]
    reuse_warnings = [finding for finding in reuse_findings if finding.severity == "warning"]

    errors.extend(_error_messages(conflict_files, temp_files, quality_escapes, duplicates, reuse_errors, bloat_errors))
    warnings.extend(bloat_warnings)
    warnings.extend(_reuse_warning_messages(reuse_warnings))
    if fail_on_warnings and warnings:
        errors.extend(f"warning promoted to failure: {warning}" for warning in warnings)

    checks = _checks(conflict_files, temp_files, quality_escapes, duplicates, reuse_errors, reuse_warnings, bloat_errors, bloat_warnings)
    return {
        "ok": not errors,
        "repo": str(repo),
        "changedScope": scope["changed_scope"],
        "changedFilesCount": len(changed_files),
        "changedFilesSample": sorted(changed_files)[:30],
        "sourceFilesCount": len([path for path in changed_files if is_production_source_path(path)]),
        "checks": checks,
        "hardRules": _hard_rules(checks),
        "errors": errors,
        "warnings": warnings,
        "bloat": bloat_details,
        "reuseFindings": [finding.as_dict() for finding in reuse_findings],
        "gitnexusQueries": gitnexus_queries,
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
    for check_item in result["checks"]:
        lines.append(f"- {check_item['name']}: {'pass' if check_item['passed'] else 'fail'}")
    lines.append("")
    lines.append("Errors:")
    lines.extend([f"- {error}" for error in result["errors"]] if result["errors"] else ["- none"])
    lines.append("")
    lines.append("Warnings:")
    lines.extend([f"- {warning}" for warning in result["warnings"]] if result["warnings"] else ["- none"])
    return "\n".join(lines)


def _changed_file_failures(repo: Path, changed_files: set[str]) -> tuple[list[str], list[str]]:
    conflict_files: list[str] = []
    temp_files: list[str] = []
    for rel_path in sorted(changed_files):
        if is_temp_artifact(rel_path) and (repo / rel_path).exists():
            temp_files.append(rel_path)
        if not is_binary_path(rel_path) and (text := read_file(repo / rel_path)) and re.search(r"^<{7} |^={7}$|^>{7} ", text, re.M):
            conflict_files.append(rel_path)
    return conflict_files, temp_files


def _error_messages(
    conflict_files: list[str],
    temp_files: list[str],
    quality_escapes: list[str],
    duplicates: list[dict[str, object]],
    reuse_errors: list[object],
    bloat_errors: list[str],
) -> list[str]:
    errors = []
    if conflict_files:
        errors.append(f"merge conflict markers found in {len(conflict_files)} file(s)")
    if temp_files:
        errors.append(f"temporary artifact paths detected in {len(temp_files)} changed file(s)")
    if quality_escapes:
        errors.append(f"quality escapes detected in {len(quality_escapes)} changed location(s)")
    if duplicates:
        errors.append(f"duplicate added code blocks detected: {len(duplicates)}")
    if reuse_errors:
        errors.append(f"new code appears to reimplement existing helpers or loops: {len(reuse_errors)}")
    return errors + bloat_errors


def _reuse_warning_messages(reuse_warnings: list[object]) -> list[str]:
    return [
        f"possible reusable existing path for {finding.new_file}:{finding.new_line} -> "
        f"{finding.existing_file}:{finding.existing_line} {finding.existing_symbol} ({finding.reason})"
        for finding in reuse_warnings
    ]


def _checks(
    conflict_files: list[str],
    temp_files: list[str],
    quality_escapes: list[str],
    duplicates: list[dict[str, object]],
    reuse_errors: list[object],
    reuse_warnings: list[object],
    bloat_errors: list[str],
    bloat_warnings: list[str],
) -> list[dict[str, object]]:
    return [
        {"name": "no-merge-conflict-markers", "passed": not conflict_files, "sample": conflict_files[:10]},
        {"name": "no-temp-artifacts", "passed": not temp_files, "sample": temp_files[:10]},
        {"name": "no-quality-escapes", "passed": not quality_escapes, "sample": quality_escapes[:10]},
        {"name": "no-duplicate-added-blocks", "passed": not duplicates, "sample": duplicates[:4]},
        {
            "name": "reuse-existing-helpers",
            "passed": not reuse_errors,
            "warnings": [finding.as_dict() for finding in reuse_warnings[:10]],
            "sample": [finding.as_dict() for finding in reuse_errors[:10]],
        },
        {"name": "risk-calibrated-bloat", "passed": not bloat_errors, "warnings": bloat_warnings[:10]},
    ]


def _hard_rules(checks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    passed = {item["name"]: bool(item["passed"]) for item in checks}
    no_duplication = passed["no-duplicate-added-blocks"] and passed["reuse-existing-helpers"]
    shortest_path = passed["risk-calibrated-bloat"] and no_duplication
    return {
        "codeVolume": {"passed": passed["risk-calibrated-bloat"], "checks": ["risk-calibrated-bloat"]},
        "noDuplication": {"passed": no_duplication, "checks": ["no-duplicate-added-blocks", "reuse-existing-helpers"]},
        "shortestPath": {"passed": shortest_path, "checks": ["risk-calibrated-bloat", "no-duplicate-added-blocks", "reuse-existing-helpers"]},
        "cleanup": {"passed": passed["no-quality-escapes"] and passed["no-temp-artifacts"], "checks": ["no-quality-escapes", "no-temp-artifacts"]},
        "anticipateConsequences": {"passed": passed["no-merge-conflict-markers"], "checks": ["no-merge-conflict-markers"]},
        "simplicity": {"passed": shortest_path, "checks": ["risk-calibrated-bloat", "no-duplicate-added-blocks", "reuse-existing-helpers"]},
    }
