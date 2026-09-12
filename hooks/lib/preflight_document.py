"""The preflight document contract: text sections plus its Behavior Map."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .behavior_map import initial_items

SECTIONS = (
    "affectedSurface", "authoritativeContract", "invariants", "proofPlan",
    "reusePath", "chosenApproach", "rejectedAlternatives", "touchpoints",
    "verify", "update", "modularityPlan", "riskChecks", "openQuestions",
)
BEHAVIOR_MAP_SECTION = "behaviorMap"


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"preflight document repeats a section: {key}")
        seen[key] = value
    return seen


def validate_document(
    value: object, *, require_behavior_map: bool = False,
) -> dict[str, object]:
    """The validated preflight document, or a refusal naming what is wrong.

    The thirteen prose sections are the surface walk: each must be present and
    non-empty, and `openQuestions` exactly `none`, or the recording refuses.
    New producer recordings additionally require the structured Behavior Map;
    the optional legacy path exists only for importing evidence recorded before
    that field.
    """
    if not isinstance(value, dict):
        raise ValueError("preflight document must be a JSON object")
    required = set(SECTIONS) | ({BEHAVIOR_MAP_SECTION} if require_behavior_map else set())
    missing = [name for name in (*SECTIONS, BEHAVIOR_MAP_SECTION) if name in required and name not in value]
    if missing:
        raise ValueError(f"preflight document is missing sections: {', '.join(missing)}")
    allowed = set(SECTIONS) | {BEHAVIOR_MAP_SECTION}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"preflight document has unknown sections: {', '.join(unknown)}")
    empty = [
        name for name in SECTIONS
        if not isinstance(value.get(name), str) or not str(value[name]).strip()
    ]
    if empty:
        raise ValueError(f"preflight sections must be non-empty text: {', '.join(empty)}")
    document: dict[str, object] = {name: str(value[name]).strip() for name in SECTIONS}
    if document["openQuestions"] != "none":
        raise ValueError("openQuestions must be exactly 'none'; an unresolved question blocks the recording")
    if BEHAVIOR_MAP_SECTION in value:
        document[BEHAVIOR_MAP_SECTION] = initial_items(value[BEHAVIOR_MAP_SECTION])
    return document


def validated_document(path: str) -> dict[str, object]:
    """Read and strictly validate new preflight evidence from a file or stdin."""
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read preflight JSON: {exc}") from exc
    return validate_document(value, require_behavior_map=True)
