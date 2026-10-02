"""Probe obligations and runner-owned comparison results shared by workflow consumers."""
from __future__ import annotations

import copy
import re

JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _source_refs(value: object, identifier: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [f"behavior {identifier} sourceRefs must be an array"]
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict) or set(raw) != {"type", "evidenceId", "id"}:
            errors.append(f"behavior {identifier} sourceRef {position} requires only type, evidenceId, and id")
        fields = raw if isinstance(raw, dict) else {}
        reference_type, evidence_id, label = fields.get("type"), _text(fields.get("evidenceId")), _text(fields.get("id"))
        if evidence_id is None or label is None or reference_type not in ("design", "finding"):
            errors.append(f"behavior {identifier} sourceRef {position} is not a valid design or finding reference")
            continue
        key = (str(reference_type), evidence_id, str(label))
        if key in seen:
            errors.append(f"behavior {identifier} repeats {reference_type} sourceRef {label}")
        seen.add(key)
    return errors


def _refs(value: list[JsonObject]) -> list[JsonObject]:
    return [{"type": ref["type"], "evidenceId": _text(ref["evidenceId"]), "id": _text(ref["id"])} for ref in value]


def map_errors(value: object, *, allow_runtime: bool) -> list[str]:
    if not isinstance(value, list):
        return ["behaviorMap must be an array"]
    errors, seen = [], set()
    for raw in value:
        if not isinstance(raw, dict):
            errors.append("probe must be an object")
            continue
        identifier = _text(raw.get("id")) or ""
        if not IDENTIFIER.fullmatch(identifier) or identifier in seen:
            errors.append("probe id must be a unique uppercase identifier: " + identifier)
        seen.add(identifier)
        errors.extend(f"probe {identifier} requires {field}" for field in REQUIRED_FIELDS if not _text(raw.get(field)))
        if not allow_runtime and (extra := set(raw) - REQUIRED_FIELDS - {"sourceRefs"}):
            errors.append(f"probe {identifier} has unknown fields: " + ", ".join(sorted(extra)))
        if "sourceRefs" in raw:
            errors.extend(_source_refs(raw["sourceRefs"], identifier))
    return errors


def validate_items(value: object, *, allow_runtime: bool) -> list[JsonObject]:
    if errors := map_errors(value, allow_runtime=allow_runtime):
        raise ValueError("; ".join(errors))
    items = [{**{key: _text(raw[key]) for key in REQUIRED_FIELDS}, "sourceRefs": _refs(raw.get("sourceRefs") or []),
              **({"comparison": copy.deepcopy(raw["comparison"])} if allow_runtime and "comparison" in raw else {})}
             for raw in value]
    return items


def initial_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False)


def runtime_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=True)


def item(items: list[JsonObject], identifier: str) -> JsonObject:
    try:
        return next(entry for entry in items if entry["id"] == identifier)
    except StopIteration as exc:
        raise ValueError("probe id is not in the recorded map: " + identifier) from exc


def apply_dispositions(items: list[JsonObject], dispositions: list[JsonObject]) -> None:
    """Existing finding dispositions can attach an owning probe, never author proof."""
    for disposition in dispositions:
        if set(disposition) != {"id", "sourceRefs"}:
            raise ValueError("probe edits use the complete items list; only finding references may be attached")
        if errors := _source_refs(disposition["sourceRefs"], str(disposition["id"])):
            raise ValueError("; ".join(errors))
        entry = item(items, disposition["id"])
        for reference in _refs(disposition["sourceRefs"]):
            if reference not in entry["sourceRefs"]:
                entry["sourceRefs"].append(reference)


def producer_proved(entry: JsonObject) -> bool:
    comparison = entry.get("comparison")
    return isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True




def comparison_view(run: JsonObject) -> JsonObject:
    """Bound reviewer context; complete process evidence remains in the ledger."""
    return {**{key: run[key] for key in ("comparison", "valid", "fresh", "runIndex", "command") if key in run},
            "arms": [{"tree": arm["requestedTree"],
                      "outcome": arm["outcome"], "error": arm["error"][:500],
                      "observation": "\n".join((arm.get("proof") or {}).get("observation", []))[:1000],
                      "testsExecuted": (arm.get("proof") or {}).get("testsExecuted")}
                     for arm in run["arms"]]}


def unresolved(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not producer_proved(entry)]


def executed_commands(entry: JsonObject) -> dict[str, str]:
    command = (entry.get("comparison") or {}).get("command")
    return {"comparison": command} if isinstance(command, str) else {}


def obligation_digest(items: list[JsonObject]) -> str:
    return "Probe obligations: " + "; ".join(f"{entry['id']}: {entry['behavior']} => {entry['expected']}" for entry in items)[:1800]


def recorded_map(
    tdd_document: JsonObject | None, preflight_document: JsonObject | None
) -> list[JsonObject] | None:
    """The current map: TDD evidence's, else the recorded preflight's, else none."""
    value = tdd_document.get("behaviorMap") if isinstance(tdd_document, dict) else None
    if value is None and isinstance(preflight_document, dict):
        inner = preflight_document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return runtime_items(value) if value is not None else None
