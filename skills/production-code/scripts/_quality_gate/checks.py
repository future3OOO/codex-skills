from __future__ import annotations

import re

from .findings import Finding, RULE_GROWTH, SnapshotEntry, pass_condition
from .path_policy import is_binary_path, is_temp_artifact
from .snapshot import EvaluationSnapshot


GENERAL_ESCAPE_RULES = [
    re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.I),
    re.compile(r"@ts-ignore\b"),
    re.compile(r"@ts-expect-error\b"),
    re.compile(r"eslint-disable\b"),
    re.compile(r"#\s*type:\s*ignore\b", re.I),
    # E402 is module-import-not-at-top, which a standalone entry point cannot
    # avoid: it must extend sys.path before importing shared code. A bare
    # suppression, or any other code, stays banned.
    re.compile(r"#\s*noqa\b(?!\s*:\s*E402\b(?!\s*,))", re.I),
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


def changed_file_failures(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str]]:
    """Merge-conflict markers and temp-artifact paths in the captured change."""
    conflict_files: list[str] = []
    temp_files: list[str] = []
    for entry in snapshot.entries:
        text = entry.current_text
        if is_temp_artifact(entry.path) and text is not None:
            temp_files.append(entry.path)
        if not is_binary_path(entry.path) and text and re.search(r"^<{7} |^>{7} ", text, re.M):
            conflict_files.append(entry.path)
    return conflict_files, temp_files


def scan_quality_escapes(snapshot: EvaluationSnapshot) -> list[str]:
    hits: list[str] = []
    for entry in snapshot.entries:
        if not entry.classification.source:
            continue
        lines = entry.added_lines()
        hits.extend(_line_hits(entry.path, lines, rules_for_entry(entry)))
        if entry.base_text is None and entry.current_text is not None:
            # A brand-new file's empty-catch shapes can span lines the unified
            # diff splits; scan its whole captured text instead.
            hits.extend(_multiline_hits(entry.path, entry.current_text))
        else:
            # An edited file's added lines are contiguous only within a hunk,
            # so an empty-catch shape split across them still hits there —
            # while lines from separate hunks are never joined.
            for hunk in entry.hunks:
                joined = "\n".join(text for _, text in hunk.added)
                for rule in EMPTY_CATCH_RULES:
                    for match in rule.finditer(joined):
                        index = joined[: match.start()].count("\n")
                        if index < len(hunk.added):
                            hits.append(f"{entry.path}:{hunk.added[index][0]}")
    return sorted(set(hits))


def rules_for_entry(entry: SnapshotEntry) -> list[re.Pattern[str]]:
    rules = list(GENERAL_ESCAPE_RULES)
    if entry.classification.role != "production":
        return rules
    if entry.classification.language == "python":
        rules.extend(PYTHON_ESCAPE_RULES)
    if entry.classification.language == "javascript":
        rules.extend(TS_ESCAPE_RULES)
    return rules


def evaluate_growth(snapshot: EvaluationSnapshot) -> Finding:
    """Cumulative growth per role, reported every run and warning-only.

    The ~500 net budget only chooses pass or warn, never suppresses evidence;
    an unbased run's cumulative claim is visibly incomplete, never silently clean.
    """
    growth = snapshot.growth()
    streams = snapshot.gap_streams()
    # Growth reads changed-entry counts, capture, and base binding — never the
    # reuse-owner baseline, so owner-discovery gaps stay with the reuse rule.
    gaps = streams["measurement_all"] + streams["capture"]
    if snapshot.base_source == "HEAD":
        gaps = gaps + ("no caller-supplied base: totals cover the working delta only, not branch-cumulative growth",)
    net = growth["humanAuthored"]["net"]
    status = "incomplete" if gaps else "finding" if net > 500 else "passed"
    return Finding(
        rule_id=RULE_GROWTH,
        severity="warning",
        status=status,
        passed=None if gaps else True,
        identity=(snapshot.base_identity, snapshot.candidate_identity),
        region={"scope": "evaluation", "changedScope": snapshot.changed_scope, "fileCount": len(snapshot.entries)},
        evidence=growth,
        action="Reduce the change, or split it, until human-authored net growth is at or under the 500-line review budget.",
        pass_condition=pass_condition(
            "growth-below",
            ("caller-supplied base", "every changed path measured"),
            "human-authored net at or under the 500-line review budget",
        ),
        gaps=gaps,
    )


def _line_hits(path: str, lines: list[tuple[int, str]], rules: list[re.Pattern[str]]) -> list[str]:
    return [f"{path}:{line_no}" for line_no, text in lines if any(rule.search(text) for rule in rules)]


def _multiline_hits(path: str, text: str) -> list[str]:
    return [f"{path}:{text[: match.start()].count(chr(10)) + 1}" for rule in EMPTY_CATCH_RULES for match in rule.finditer(text)]
