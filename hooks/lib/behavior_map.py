"""Probe obligations, one readiness judgement and one compact comparison view shared by workflow consumers."""
from __future__ import annotations

import copy
import re
import shlex


JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
KINDS = frozenset({"conformance", "contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Optional map fields; witness and reading updates re-judge retained proof.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs"}
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _strings(value: object) -> list[str] | None:
    """A non-empty list of non-empty strings, else None."""
    if isinstance(value, list) and value and all(_text(item) for item in value):
        return [str(item).strip() for item in value]
    return None


def _recorded_fields(raw: JsonObject) -> JsonObject:
    """The optional fields in their valid shape; any other shape is absent rather than refused."""
    fields: JsonObject = {}
    if raw.get("kind") in KINDS:
        fields["kind"] = raw["kind"]
    for key in ("boundaryInputs", "interpretations"):
        if (values := _strings(raw.get(key))) and (key != "interpretations" or len(values) >= 2):
            try:
                fields[key] = list(dict.fromkeys(shlex.join(shlex.split(value)) for value in values)) if key == "boundaryInputs" else values
            except ValueError:
                fields[key] = []
    if "boundaryInputs" in raw and "boundaryInputs" not in fields:
        fields["boundaryInputs"] = []  # recorded in another shape: visible, unmapped, never silently dropped
    if "interpretations" in fields and _text(raw.get("interpretation")) and _text(raw.get("authority")):
        fields.update(interpretation=raw["interpretation"].strip(), authority=raw["authority"].strip())
    return fields


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
        if not allow_runtime:
            if not (isinstance(raw.get("kind"), str) and raw["kind"] in KINDS):
                errors.append(f"probe {identifier} requires kind: conformance, contract or preservation")
            if extra := set(raw) - REQUIRED_FIELDS - JUDGEMENT_FIELDS:
                errors.append(f"probe {identifier} has unknown fields: " + ", ".join(sorted(extra)))
    return errors


def validate_items(value: object, *, allow_runtime: bool) -> list[JsonObject]:
    if errors := map_errors(value, allow_runtime=allow_runtime):
        raise ValueError("; ".join(errors))
    items = []
    for raw in value:
        item = {**{key: _text(raw[key]) for key in REQUIRED_FIELDS}, **_recorded_fields(raw)}
        if allow_runtime and "comparison" in raw:
            item["comparison"] = copy.deepcopy(raw["comparison"])
        items.append(item)
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


def readings_unsettled(entry: JsonObject) -> bool:
    return bool(entry.get("interpretations")) and not (entry.get("interpretation") and entry.get("authority"))


def original_outcome(run: JsonObject) -> str:
    """A growth case's original is judged by its own declared bound when both of its arms ran and produced measurements."""
    if "WORKFLOW_GROWTH" not in (run.get("execution") or {}).get("assignments", {}):
        return run["arms"][0]["outcome"]
    from .tdd_workflow import _work_errors
    small, large = run["arms"][1], run["arms"][0]
    errors = _work_errors({**run, "arms": [small, large, small, large]}) if small["outcome"] == large["outcome"] == "passed" else ["unmeasured"]
    return "unmeasured" if any(not error.startswith("growth: ") for error in errors) else "failed" if errors else "passed"


def _case_question(entry: JsonObject) -> list[str]:
    if entry.get("kind") not in KINDS:
        return ["declare kind (conformance, contract or preservation) through record tdd-map"]
    if readings_unsettled(entry):
        return ["readings unsettled: " + " | ".join(entry["interpretations"]) + " - record interpretation and authority"]
    run = entry.get("comparison") or {}
    if not producer_proved(entry):
        return [(run.get("freshnessReason", "comparison inputs changed") if run.get("fresh") is False
                 else "no current valid command comparison") + "; replay the recorded batch: workflow.py tdd"]
    if not run.get("sensitive"):
        return [run.get("breakError") or "supply --change " + entry["id"] + "=FILE: every command must fail under this behavior break"]
    original, current = original_outcome(run), run["arms"][-1]["outcome"]
    if current != "passed":
        return ["candidate command " + current + ": " + run["command"]]
    expected = "passed" if entry["kind"] == "preservation" else "failed"
    if original != expected:
        return [f"{entry['kind']} needs original {expected}, candidate passed; original {original}: " + run["command"]]
    return []


def _question(entry: JsonObject, proofs: list[JsonObject]) -> list[str]:
    if readings_unsettled(entry):
        return _case_question(entry)
    commands = entry.get("boundaryInputs") or []
    if not commands:
        return ["bind the case command: workflow.py tdd --case " + entry["id"] + " 'COMMAND'"]
    candidates = [proof for proof in proofs if proof["id"] == entry["id"]
                  and obligation(proof) == obligation(entry)]
    reasons, behavior = [], False
    for command in commands:
        matching = [proof for proof in candidates if (proof.get("comparison") or {}).get("command") == command
                    or proof.get("comparison") == {} and proof.get("boundaryInputs") == [command]]
        if not matching or not matching[0].get("comparison"):
            reasons.append("case command not compared: " + command)
        else:
            reasons.extend(_case_question(matching[0]))
            behavior = behavior or entry.get("kind") != "preservation" or "WORKFLOW_GROWTH" not in (matching[0]["comparison"].get("execution") or {}).get("assignments", {})
    return reasons or ([] if behavior else ["growth cases check scaling only; bind a behavior case and its break: workflow.py tdd --case " + entry["id"] + " 'COMMAND'"])


def current_cases(items: list[JsonObject], runs: list[JsonObject]) -> list[JsonObject]:
    """Project current command bindings from the existing run history."""
    cases = []
    for entry in items:
        proof = entry.get("comparison") or {}
        references = proof.get("caseRuns")
        for command in entry.get("boundaryInputs") or []:
            if command == proof.get("command"):
                cases.append(entry)
                continue
            if references is not None:
                run = runs[references[command]] if command in references else None
            elif proof.get("command") and "runIndex" in proof:
                observed = [run for run in runs if run.get("command") == command and run.get("behaviorId") == entry["id"]
                            and not run.get("brokenSource") and run.get("arms")]
                inputs = {run["arms"][0].get("key") for run in observed}
                run = observed[-1] if len(inputs) == 1 and None not in inputs else None
            else:
                run = None
            cases.append({**entry, "boundaryInputs": [command],
                          "comparison": {**run, "obligation": proof.get("obligation", obligation(entry))} if run else {}})
    return cases


def open_obligations(items: list[JsonObject], evaluation: list[JsonObject] | None = None, current: list[JsonObject] | None = None) -> list[str]:
    """One command is one case. Output and framework names never determine readiness."""
    retained = evaluation or []
    lines = [("retained " if retained_item else "") + f"{entry.get('caseKey', entry['id'])} ({entry.get('kind') or 'kind?'})"
             + (f" `{entry['boundaryInputs'][0]}`" if retained_item else "") + ": " + "; ".join(reasons)
             for group, retained_item in ((items, False), (retained, True)) for entry in group
             if (reasons := _case_question(entry) if retained_item else _question(entry, [*(current or items), *retained]))]
    return lines


def retain_evaluation(items: list[JsonObject], required: set[str], retained: JsonObject,
                      runs: list[JsonObject] | None = None, retired: set[str] = frozenset()) -> None:
    """Retain each first proved command and every later distinct one; a retired expectation frees its command for a new one."""
    for entry in items:
        if entry["id"] not in required or _case_question(entry):
            continue
        run = entry["comparison"]
        if any(old["id"] == entry["id"] and (runs is None or
                runs[old["runIndex"]]["command"] == run["command"] and (not retired or case_key(old["id"], runs[old["runIndex"]]) not in retired))
                for old in retained.values()):
            continue
        key = entry["id"] if runs is None else case_key(entry["id"], runs[run["runIndex"]])
        if key in retired:
            continue
        key = entry["id"] if entry["id"] not in retained else key
        retained[key] = {**copy.deepcopy({key: value for key, value in entry.items() if key != "comparison"}),
                         "runIndex": run["runIndex"], "boundaryInputs": [run["command"]]}


def case_key(identifier: str, run: JsonObject) -> str:
    """Identify the recorded case, independent of later replay inputs; old ledgers carry its initial execution key."""
    return identifier + ":" + (run.get("captureKey") or run["candidateKey"])


def obligation(entry: JsonObject) -> list[object]:
    return [entry.get(key) for key in ("kind", "behavior", "seam", "expected")]


def producer_proved(entry: JsonObject) -> bool:
    comparison = entry.get("comparison")
    return (isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True
            and bool(comparison.get("command")) and bool(comparison.get("arms"))
            and isinstance(recorded := comparison.get("obligation", obligation(entry)), list)
            and recorded[:4] == obligation(entry))


def unresolved(items: list[JsonObject], opened: list[str] | None = None) -> list[str]:
    ids = {str(entry["id"]) for entry in items}
    return [head if (head := line.split(" ", 1)[0]) in ids else line for line in (open_obligations(items) if opened is None else opened)]


def never_compared(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not (entry.get("comparison") or {}).get("arms")]


def contract_changes(items: list[JsonObject], recorded: list[JsonObject] | None) -> list[str]:
    """Added required command cases and dropped commands; recorded contract wording and kind cannot change."""
    before = {entry["id"]: entry for entry in recorded or []}
    lines = []
    for entry in items:
        prior, kind = before.get(entry["id"]), entry.get("kind")
        if prior is None:
            continue
        inputs, earlier = set(entry.get("boundaryInputs") or []), set(prior.get("boundaryInputs") or [])
        changes = []
        if kind in {"contract", "conformance"} and inputs - earlier:
            changes.append(f"{kind} commands added: " + ", ".join(sorted(inputs - earlier)))
        if earlier - inputs:
            changes.append(f"{prior.get('kind')} commands dropped: " + ", ".join(sorted(earlier - inputs)))
        lines.extend(f"{entry['id']}: {change}: {entry['basis']}" for change in changes)
    return lines


def comparison_view(run: JsonObject) -> JsonObject:
    """One compact exit-code comparison, shared by receipts and review input."""
    arms = run.get("arms") or []
    return {"comparison": run.get("comparison", "incomplete"), "valid": run.get("valid", False),
            "runIndex": run.get("runIndex"), "command": run.get("command"),
            "sensitive": run.get("sensitive", False), "breakExit": run.get("breakExit"),
            **({"breakCommand": shlex.join(["git", "cat-file", "blob", run["breakOid"]])} if run.get("breakOid") else {}),
            "arms": [{"source": "original" if index == 0 else "current", "tree": arm.get("requestedTree", arm.get("sourceTree")),
                      "outcome": arm.get("outcome"), "exitCode": arm.get("exitCode"), "timedOut": arm.get("timedOut", False)}
                     for index, arm in enumerate(arms)],
            **({"limitations": [run["bindingError"]]} if run.get("bindingError") else {})}


def recorded_map(
    tdd_document: JsonObject | None, preflight_document: JsonObject | None = None
) -> list[JsonObject] | None:
    """The current map: TDD evidence's, else the recorded preflight's, else none."""
    value = tdd_document.get("behaviorMap") if isinstance(tdd_document, dict) else None
    fallback = preflight_document if preflight_document is not None else tdd_document
    if value is None and isinstance(fallback, dict):
        inner = fallback.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    items = runtime_items(value) if value is not None else None
    for entry in items or []:
        proof = entry.get("comparison")
        if proof and "runIndex" in proof and tdd_document and isinstance(tdd_document.get("runs"), list):
            entry["comparison"] = {**tdd_document["runs"][proof["runIndex"]], **proof}
    return items
