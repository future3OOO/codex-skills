"""Probe obligations, one readiness judgement and one compact comparison view shared by workflow consumers."""
from __future__ import annotations

import copy
import os
import re
import shlex


JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
KINDS = frozenset({"conformance", "contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Optional map fields; witness and reading updates re-judge retained proof. `cases` holds declared commands not yet captured.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs", "cases"}
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _strings(value: object) -> list[str] | None:
    """A non-empty list of non-empty strings, else None."""
    if isinstance(value, list) and value and all(_text(item) for item in value):
        return [str(item).strip() for item in value]
    return None


def _recorded_fields(raw: JsonObject) -> JsonObject:
    """Preserve input context; normalize the optional reading choices."""
    fields: JsonObject = {}
    if raw.get("kind") in KINDS:
        fields["kind"] = raw["kind"]
    if "boundaryInputs" in raw:
        fields["boundaryInputs"] = copy.deepcopy(raw["boundaryInputs"])
    if values := _strings(raw.get("cases")):
        fields["cases"] = values
    if (values := _strings(raw.get("interpretations"))) and len(values) >= 2:
        fields["interpretations"] = values
    if "interpretations" in fields and _text(raw.get("interpretation")) and _text(raw.get("authority")):
        fields.update(interpretation=raw["interpretation"].strip(), authority=raw["authority"].strip())
    return fields


def script_operand(command: str, *, shells: bool = False) -> str | None:
    """The file a command asks Python to run, by the argument rules of Python and of the wrappers leads put before it:
    `[NAME=VALUE | env | timeout [OPTION]... DURATION]... pythonX [OPTION]... FILE`. None for any other
    command, for `-c` and `-m`, and for a program read from standard input: their other arguments are data. With
    `shells`, also the file `sh` or `bash` runs."""
    try:
        words = shlex.split(command)
    except ValueError:  # an unclosed quote is refused when the case is captured
        return None
    at = 0
    while at < len(words) and (re.match(r"[A-Za-z_][A-Za-z_0-9]*=", words[at]) or os.path.basename(words[at]) in ("env", "timeout")):
        at += 1
        if os.path.basename(words[at - 1]) == "timeout":
            while at < len(words) and words[at].startswith("-"):
                at += 2 if words[at] in ("-k", "-s", "--kill-after", "--signal") else 1
            at += 1  # its duration
    if shells and at + 1 < len(words) and os.path.basename(words[at]) in {"sh", "bash"} and not words[at + 1].startswith("-"):
        return words[at + 1]
    if at >= len(words) or not re.fullmatch(r"python[0-9.]*", os.path.basename(words[at])):
        return None
    at += 1
    while at < len(words) and words[at].startswith("-") and words[at] != "-":
        option = words[at]
        for index, letter in enumerate(option[1:] if not option.startswith("--") else "", 1):
            if letter in "cm":
                return None
            if letter in "WX":  # its value is the rest of the word, or the next word
                at += index == len(option) - 1
                break
        at += 2 if option == "--check-hash-based-pycs" else 1
    return words[at] if at < len(words) and words[at] != "-" else None


def map_errors(value: object, *, allow_runtime: bool, scripts: bool = True) -> list[str]:
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
        if raw.get("cases") not in (None, []) and _strings(raw["cases"]) is None:
            errors.append(f"probe {identifier} cases must be a list of commands")
        if not allow_runtime:
            if not (isinstance(raw.get("kind"), str) and raw["kind"] in KINDS):
                errors.append(f"probe {identifier} requires kind: conformance, contract or preservation")
            if extra := set(raw) - REQUIRED_FIELDS - JUDGEMENT_FIELDS:
                errors.append(f"probe {identifier} has unknown fields: " + ", ".join(sorted(extra)))
            errors.extend(f"probe {identifier} case runs {script}, which does not exist: write it before declaring its command"
                          for script in map(script_operand, (scripts and _strings(raw.get("cases"))) or ())
                          if script and os.path.isabs(script) and not os.path.lexists(script))
    return errors


def validate_items(value: object, *, allow_runtime: bool, scripts: bool = True) -> list[JsonObject]:
    if errors := map_errors(value, allow_runtime=allow_runtime, scripts=scripts):
        raise ValueError("; ".join(errors))
    items = []
    for raw in value:
        item = {**{key: _text(raw[key]) for key in REQUIRED_FIELDS}, **_recorded_fields(raw)}
        if allow_runtime and "comparison" in raw:
            item["comparison"] = copy.deepcopy(raw["comparison"])
        items.append(item)
    return items


def initial_items(value: object, scripts: bool = True) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False, scripts=scripts)


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
        return [run.get("freshnessReason", "comparison inputs changed") if run.get("fresh") is False
                else "no current valid command comparison"]
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
    candidates = [proof for proof in proofs if proof["id"] == entry["id"] and proof.get("comparison")
                  and obligation(proof)[1:] == obligation(entry)[1:]]
    if not candidates:
        return ["bind the case command: workflow.py tdd --case " + entry["id"] + " 'COMMAND'"]
    reasons, behavior = [], False
    for proof in candidates:
        reasons.extend(_case_question({**proof, "kind": entry.get("kind")}))
        behavior = behavior or entry.get("kind") != "preservation" or "WORKFLOW_GROWTH" not in (proof.get("comparison", {}).get("execution") or {}).get("assignments", {})
    return list(dict.fromkeys(reasons)) or ([] if behavior else ["growth cases check scaling only; bind a behavior case: workflow.py tdd --case " + entry["id"] + " 'COMMAND'"])


def current_cases(items: list[JsonObject], runs: list[JsonObject], retired: set[str] = frozenset()) -> list[JsonObject]:
    """Project current command bindings from the existing run history."""
    cases = []
    for entry in items:
        proof = entry.get("comparison") or {}
        references = proof.get("caseRuns")
        if not references:
            references = {}
            if proof.get("command") and "runIndex" in proof:
                references = {run["command"]: index for index, run in enumerate(runs)
                              if run.get("behaviorId") == entry["id"] and run.get("arms") and not run.get("brokenSource")}
                references[proof["command"]] = proof["runIndex"]
        latest = {}
        for index in sorted(set(references.values())):
            run = runs[index]
            latest[run.get("sourceCommand", run["command"])] = run
        for run in latest.values():
            cases.append({**entry, "comparison": {**run, "obligation": proof.get("obligation", obligation(entry))}})
    return [case for case in cases if not case.get("comparison") or case_key(case["id"], case["comparison"]) not in retired] if retired else cases


def open_obligations(items: list[JsonObject], evaluation: list[JsonObject] | None = None, current: list[JsonObject] | None = None) -> list[str]:
    """One command is one case. Output and framework names never determine readiness."""
    retained = evaluation or []
    lines = [("retained " if retained_item else "") + f"{entry.get('caseKey', entry['id'])} ({entry.get('kind') or 'kind?'})"
             + (f" `{entry['comparison']['command']}`" if retained_item else "") + ": " + "; ".join(reasons)
             for group, retained_item in ((items, False), (retained, True)) for entry in group
             if (reasons := _case_question(entry) if retained_item else _question(entry, [*(current or items), *retained]))]
    return lines


def release(items: list[JsonObject], owner: JsonObject, run: JsonObject, runs: list[JsonObject]) -> None:
    """A command just proved under `owner` leaves every other item it does not satisfy; an item it proves keeps it."""
    if _case_question(owner):  # a recording that has not proved its own item takes nothing
        return
    source = run.get("sourceCommand", run["command"])
    for entry in items:
        cases = current_cases([entry], runs) if entry is not owner and (entry.get("comparison") or {}).get("caseRuns") else []
        kept = {case["comparison"]["command"]: case["comparison"]["runIndex"] for case in cases
                if case["comparison"].get("sourceCommand", case["comparison"]["command"]) != source or not _case_question(case)}
        if len(kept) < len(cases):
            recorded = entry.pop("comparison")["obligation"]  # left empty, current_cases would rebuild the binding from run history
            if kept:
                entry["comparison"] = {**runs[max(kept.values())], "obligation": recorded, "caseRuns": kept}


def retain_evaluation(items: list[JsonObject], required: set[str], retained: JsonObject,
                      runs: list[JsonObject] | None = None, retired: set[str] = frozenset()) -> None:
    """Retain each first proved command and every later distinct one. A proved recapture of a command replaces its earlier captures
    under its item and under map items no longer bound to the command, except a retired one and except when it names support:
    candidate bytes of a pass-start test file never displace a frozen capture."""
    binders: dict[str, set[str]] = {}  # per command, the map items bound to it
    for case in current_cases(items, runs) if runs is not None else ():
        binders.setdefault(case["comparison"].get("sourceCommand", case["comparison"]["command"]), set()).add(case["id"])
    present = {entry["id"] for entry in items} if runs is not None else set()  # a removed item keeps its captures
    for entry in items:
        if entry["id"] not in required or _case_question(entry):
            continue
        run = entry["comparison"]
        command = run.get("sourceCommand", run["command"])
        key = entry["id"] if runs is None else case_key(entry["id"], runs[run["runIndex"]])
        earlier = {name: key if runs is None else case_key(old["id"], runs[old["runIndex"]]) for name, old in retained.items()
                   if (old["id"] == entry["id"] or old["id"] in present and old["id"] not in binders.get(command, ())) and (runs is None or
                   runs[old["runIndex"]].get("sourceCommand", runs[old["runIndex"]]["command"]) == command
                   and (not retired or case_key(old["id"], runs[old["runIndex"]]) not in retired))}
        if key in retired or key in earlier.values() or earlier and run.get("support"):
            continue
        for name in earlier:
            del retained[name]
        key = entry["id"] if entry["id"] not in retained else key
        retained[key] = {**copy.deepcopy({key: value for key, value in entry.items() if key != "comparison"}),
                         "runIndex": run["runIndex"]}


def case_key(identifier: str, run: JsonObject) -> str:
    """Identify the recorded case, independent of later replay inputs; old ledgers carry its initial execution key."""
    return identifier + ":" + (run.get("captureKey") or run["candidateKey"])


def obligation(entry: JsonObject) -> list[object]:
    return [entry.get(key) for key in ("kind", "behavior", "seam", "expected")]


def producer_proved(entry: JsonObject) -> bool:
    """Bind proof to behavior, Seam and expected result; kind is revisable judgment."""
    comparison = entry.get("comparison")
    return (isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True
            and bool(comparison.get("command")) and bool(comparison.get("arms"))
            and isinstance(recorded := comparison.get("obligation", obligation(entry)), list)
            and recorded[1:4] == obligation(entry)[1:4])


def unresolved(items: list[JsonObject], opened: list[str] | None = None) -> list[str]:
    ids = {str(entry["id"]) for entry in items}
    return [head if (head := line.split(" ", 1)[0]) in ids else line for line in (open_obligations(items) if opened is None else opened)]


def never_compared(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not (entry.get("comparison") or {}).get("arms")]


def comparison_view(run: JsonObject, *, detail: bool = False) -> JsonObject:
    """One compact exit-code comparison, shared by receipts, evidence reads and review input; detail adds its item and passing output."""
    arms = run.get("arms") or []
    return {"comparison": run.get("comparison", "incomplete"), "valid": run.get("valid", False),
            "runIndex": run.get("runIndex"), "command": run.get("command"),
            **({"behaviorId": run["behaviorId"]} if detail and run.get("behaviorId") else {}),
            "probeTree": run.get("probeTree"),
            "sensitive": run.get("sensitive", False), "breakExit": run.get("breakExit"),
            **({"breakCommand": shlex.join(["git", "cat-file", "blob", run["breakOid"]])} if run.get("breakOid") else {}),
            "arms": [{"source": "original" if index < (len(arms) + 1) // 2 else "current", "tree": arm.get("requestedTree", arm.get("sourceTree")),
                      "outcome": arm.get("outcome"), "exitCode": arm.get("exitCode"), "timedOut": arm.get("timedOut", False),
                      **({"outputTail": str(arm.get("output") or arm.get("error") or arm.get("bindingError") or "")[-1200:]}
                         if arm.get("outcome") != "passed" or detail and arm.get("output") else {})}
                     for index, arm in enumerate(arms)],
            **({"limitations": [run["bindingError"]]} if run.get("bindingError") else {})}


def compact(value: object) -> object:
    """An evidence value with every comparison inside it replaced by its detailed view."""
    if isinstance(value, dict):
        return comparison_view(value, detail=True) if isinstance(value.get("arms"), list) else {key: compact(item) for key, item in value.items()}
    return [compact(item) for item in value] if isinstance(value, list) else value


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
