from __future__ import annotations

import os
import re
from pathlib import Path

from .context import GateContext
from .git_scope import read_file, read_git_file
from .models import Numstat
from .path_policy import is_production_source_path, is_source_path, physical_lines


GENERAL_ESCAPE_RULES = [
    re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.I),
    re.compile(r"@ts-ignore\b"),
    re.compile(r"@ts-expect-error\b"),
    re.compile(r"eslint-disable\b"),
    re.compile(r"#\s*type:\s*ignore\b", re.I),
    re.compile(r"#\s*noqa\b", re.I),
    re.compile(r"\|\|\s*true\b"),
]

PYTHON_ESCAPE_RULES = [
    re.compile(r"\btyping\.Any\b"),
    re.compile(r":\s*Any\b"),
    re.compile(r"\bcast\s*\("),
    re.compile(r"^\s*except\s*:\s*$"),
    re.compile(r"^\s*except\s+Exception\s*:\s*pass\s*$"),
]

TS_ESCAPE_RULES = [
    re.compile(r":\s*any\b"),
    re.compile(r"<\s*any\s*>"),
    re.compile(r"\bas\s+any\b"),
    re.compile(r"\bas\s+unknown\s+as\b"),
]

EMPTY_CATCH_RULES = [
    re.compile(r"\bcatch\s*\([^)]*\)\s*\{\s*\}", re.S),
    re.compile(r"\bcatch\s*\{\s*\}", re.S),
    re.compile(r"\.catch\s*\(\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{\s*\}\s*\)", re.S),
    re.compile(r"except\s+Exception\s*:\s*\n\s*pass\b", re.S),
    re.compile(r"except\s*:\s*\n\s*pass\b", re.S),
]


def scan_quality_escapes(ctx: GateContext) -> list[str]:
    hits: list[str] = []
    added_by_file = ctx.added_lines
    for rel_path, lines in added_by_file.items():
        if is_source_path(rel_path):
            hits.extend(_line_hits(rel_path, lines, rules_for_path(rel_path)))
    for rel_path in sorted(ctx.untracked):
        if not is_source_path(rel_path) or (text := read_file(ctx.repo / rel_path)) is None:
            continue
        hits.extend(_line_hits(rel_path, list(enumerate(text.splitlines(), 1)), rules_for_path(rel_path)))
        hits.extend(_multiline_hits(rel_path, text))
    return sorted(set(hits + _multiline_added_hits(added_by_file)))


def rules_for_path(path: str) -> list[re.Pattern[str]]:
    suffix = Path(path).suffix.lower()
    rules = list(GENERAL_ESCAPE_RULES)
    if suffix == ".py" and is_production_source_path(path):
        rules.extend(PYTHON_ESCAPE_RULES)
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"} and is_production_source_path(path):
        rules.extend(TS_ESCAPE_RULES)
    return rules


def duplicate_added_blocks(ctx: GateContext) -> list[dict[str, object]]:
    windows: dict[str, dict[str, object]] = {}
    added_by_file = ctx.added_lines_with_untracked(production_only=True)
    for rel_path, lines in added_by_file.items():
        if not is_production_source_path(rel_path):
            continue
        normalized = _duplicate_candidate_lines(lines)
        for index in range(0, max(0, len(normalized) - 2)):
            key = _duplicate_window_key(normalized, index)
            if len(key) < 80:
                continue
            item = windows.setdefault(key, {"count": 0, "files": set(), "sample": key[:180]})
            item["count"] = int(item["count"]) + 1
            files = item["files"]
            assert isinstance(files, set)
            files.add(rel_path)
    return _collapse_duplicate_findings([
        {"count": item["count"], "files": sorted(item["files"]), "sample": item["sample"]}
        for item in windows.values()
        if int(item["count"]) > 1
    ])


def evaluate_bloat(ctx: GateContext) -> tuple[list[str], list[str], dict[str, object]]:
    errors: list[str] = []
    warnings: list[str] = []
    details, total_added, total_deleted, shrink_by_dir = _bloat_file_details(
        ctx.repo,
        ctx.changed_files,
        ctx.numstats,
        ctx.base_for_file,
    )
    for detail in details:
        errors.extend(_bloat_errors_for_file(detail, shrink_by_dir))
        warnings.extend(_bloat_warnings_for_file(detail))
    if total_added >= 1000 and total_added > max(1, total_deleted) * 6:
        errors.append(f"changed source diff is heavily additive: added={total_added} deleted={total_deleted}")
    elif total_added >= 500 and total_added > max(1, total_deleted) * 4:
        warnings.append(f"changed source diff is additive: added={total_added} deleted={total_deleted}")
    return errors, warnings, {"totalAdded": total_added, "totalDeleted": total_deleted, "files": details[:50]}


def _line_hits(path: str, lines: list[tuple[int, str]], rules: list[re.Pattern[str]]) -> list[str]:
    return [f"{path}:{line_no}" for line_no, text in lines if any(rule.search(text) for rule in rules)]


def _multiline_hits(path: str, text: str) -> list[str]:
    return [f"{path}:{text[: match.start()].count(chr(10)) + 1}" for rule in EMPTY_CATCH_RULES for match in rule.finditer(text)]


def _multiline_added_hits(added_by_file: dict[str, list[tuple[int, str]]]) -> list[str]:
    joined = "\n".join(f"{path}:{line_no}:{text}" for path, values in added_by_file.items() for line_no, text in values)
    hits = []
    for rule in EMPTY_CATCH_RULES:
        for match in rule.finditer(joined):
            prefix = joined[: match.start()].splitlines()[-1] if joined[: match.start()].splitlines() else ""
            bits = prefix.split(":", 2)
            if len(bits) >= 2:
                hits.append(f"{bits[0]}:{bits[1]}")
    return hits


def _duplicate_candidate_lines(lines: list[tuple[int, str]]) -> list[str]:
    normalized = [
        re.sub(r"\s+", " ", text.strip()).rstrip(";,")
        for _, text in lines
        if text.strip() and not text.strip().startswith(("//", "#", "*"))
    ]
    return [
        line
        for line in normalized
        if line not in {"{", "}"} and not re.match(r"^(?:export\s+)?(?:async\s+)?function\s+\w+\(", line)
    ]


def _duplicate_window_key(lines: list[str], index: int) -> str:
    chunk = lines[index : index + 3]
    if index and lines[index - 1] == lines[index]:
        return ""
    if index + 3 < len(lines) and lines[index + 2] == lines[index + 3]:
        return ""
    if any("wait_for_timeout" in line for line in chunk) and index >= 2:
        start = max(0, index - 2)
        chunk = lines[start : index + 3]
    return " | ".join(chunk)


def _collapse_duplicate_findings(items: list[dict[str, object]]) -> list[dict[str, object]]:
    collapsed: list[dict[str, object]] = []
    for item in sorted(items, key=lambda value: (value["files"], value["sample"])):
        duplicate = next((existing for existing in collapsed if _same_duplicate_family(existing, item)), None)
        if duplicate is None:
            collapsed.append(item)
        else:
            duplicate["count"] = int(duplicate["count"]) + int(item["count"])
    return collapsed


def _same_duplicate_family(left: dict[str, object], right: dict[str, object]) -> bool:
    if left["files"] != right["files"]:
        return False
    left_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", str(left["sample"])))
    right_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", str(right["sample"])))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.6


def _bloat_file_details(
    repo: Path,
    changed_files: set[str],
    records: list[Numstat],
    base_for_file: str,
) -> tuple[list[dict[str, object]], int, int, dict[str, int]]:
    numstat = _merge_numstats(records)
    details: list[dict[str, object]] = []
    total_added = 0
    total_deleted = 0
    shrink_by_dir: dict[str, int] = {}
    for rel_path in sorted(changed_files):
        if not is_production_source_path(rel_path) or (current_text := read_file(repo / rel_path)) is None:
            continue
        base_text = read_git_file(repo, base_for_file, rel_path)
        baseline_lines = physical_lines(base_text) if base_text is not None else None
        current_lines = physical_lines(current_text)
        record = numstat.get(rel_path)
        added = record.added if record else (current_lines if baseline_lines is None else 0)
        deleted = record.deleted if record else 0
        total_added += added
        total_deleted += deleted
        parent = str(Path(rel_path).parent).replace(os.sep, "/")
        if deleted > added:
            shrink_by_dir[parent] = shrink_by_dir.get(parent, 0) + deleted - added
        details.append({"file": rel_path, "added": added, "deleted": deleted, "currentLines": current_lines, "baselineLines": baseline_lines, "netGrowth": max(0, added - deleted)})
    return details, total_added, total_deleted, shrink_by_dir


def _merge_numstats(records: list[Numstat]) -> dict[str, Numstat]:
    merged: dict[str, Numstat] = {}
    for record in records:
        previous = merged.get(record.path)
        merged[record.path] = record if previous is None else Numstat(previous.added + record.added, previous.deleted + record.deleted, record.path)
    return merged


def _bloat_errors_for_file(detail: dict[str, object], shrink_by_dir: dict[str, int]) -> list[str]:
    rel_path = str(detail["file"])
    current_lines = int(detail["currentLines"])
    baseline_lines = detail["baselineLines"]
    net_growth = int(detail["netGrowth"])
    available_shrink = shrink_by_dir.get(str(Path(rel_path).parent).replace(os.sep, "/"), 0)
    if baseline_lines is None:
        return [f"new source file {rel_path} has {current_lines} lines (>800)"] if current_lines > 800 else []
    baseline = int(baseline_lines)
    if baseline > 1200 and current_lines >= baseline:
        return [f"large source file {rel_path} must shrink when touched ({current_lines} >= {baseline})"]
    if net_growth > 250 and available_shrink < net_growth:
        return [f"source file {rel_path} grew by {net_growth} lines without same-directory shrink"]
    if baseline >= 800 and net_growth > 80 and available_shrink < net_growth:
        return [f"large source file {rel_path} grew by {net_growth} lines without same-directory shrink"]
    return []


def _bloat_warnings_for_file(detail: dict[str, object]) -> list[str]:
    if detail["baselineLines"] is None and int(detail["currentLines"]) > 500:
        return [f"new source file {detail['file']} has {detail['currentLines']} lines (>500)"]
    return []
