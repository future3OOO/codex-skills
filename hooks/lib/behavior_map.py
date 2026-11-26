"""Probe obligations, one readiness judgement and one compact comparison view shared by workflow consumers."""
from __future__ import annotations

import copy
import json
import re


JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
KINDS = frozenset({"conformance", "contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Optional map fields; witness and reading updates re-judge retained proof. `cases` holds declared commands not yet captured.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs", "cases", "measured", "remaining"}
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
    for field in ("measured", "remaining"):
        if field in raw:
            fields[field] = raw[field]
    if "boundaryInputs" in raw:
        fields["boundaryInputs"] = copy.deepcopy(raw["boundaryInputs"])
    if values := _strings(raw.get("cases")):
        fields["cases"] = values
    if (values := _strings(raw.get("interpretations"))) and len(values) >= 2:
        fields["interpretations"] = values
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
        if raw.get("cases") not in (None, []) and _strings(raw["cases"]) is None:
            errors.append(f"probe {identifier} cases must be a list of commands")
        if "remaining" in raw and (type(raw["remaining"]) is not int or raw["remaining"] < 0 or raw.get("measured") is not True):
            errors.append(f"probe {identifier} remaining requires measured=true and a nonnegative integer ceiling")
        if "measured" in raw and type(raw["measured"]) is not bool:
            errors.append(f"probe {identifier} measured must be a boolean")
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


def merge(previous: list[JsonObject], value: JsonObject, keep: list[str] | tuple[str, ...] = ()) -> tuple[list[JsonObject], list[JsonObject]]:
    """`value`'s `items` and `added` over `previous`, without its `removed` ids apart from those in `keep`: an item names
    only what changes (a given field replaces, null removes, an omitted field stays). Returns the updated items, then the map."""
    recorded = {entry["id"]: entry for entry in previous}
    removed = value.get("removed", [])
    if not isinstance(removed, list) or any(not isinstance(name, str) or name not in recorded for name in removed):
        raise ValueError("removed must contain existing probe ids")
    parts = [value[key] for key in ("items", "added") if key in value]
    merged = next((part for part in parts if not isinstance(part, list)), [entry for part in parts if isinstance(part, list) for entry in part])
    if isinstance(merged, list):
        fields, folded = REQUIRED_FIELDS | JUDGEMENT_FIELDS, {}
        for entry in merged:  # an update that names one item twice changes it once, its entries in order
            key = str(entry.get("id")).strip() if isinstance(entry, dict) else id(entry)
            folded[key] = {**folded[key], **entry} if key in folded else entry
        merged = list(folded.values())
        for position, entry in enumerate(merged):
            if isinstance(entry, dict):
                prior = recorded.get(str(entry.get("id")).strip(), {})
                kept = {key: prior[key] for key in fields if prior.get(key) not in (None, [])
                        and not ("interpretations" in entry and key in ("interpretation", "authority"))}
                merged[position] = {key: field for key, field in {**kept, **entry}.items() if field is not None}
    updates = initial_items(merged)
    return updates, [entry for entry in {entry["id"]: entry for entry in [*previous, *updates]}.values() if entry["id"] not in removed or entry["id"] in keep]


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
    if len(run["arms"]) != 4:
        return "unmeasured"
    small, large = run["arms"][1], run["arms"][0]
    errors = _work_errors({**run, "arms": [small, large, small, large]}) if small["outcome"] == large["outcome"] == "passed" else ["unmeasured"]
    return "unmeasured" if any(not error.startswith("growth: ") for error in errors) else "failed" if errors else "passed"


def _case_question(entry: JsonObject, *, measured: bool | None = None) -> list[str]:
    if entry.get("kind") not in KINDS:
        return ["declare kind (conformance, contract or preservation) through record tdd-map"]
    if readings_unsettled(entry):
        return ["readings unsettled: " + " | ".join(entry["interpretations"]) + " - record interpretation and authority"]
    run = entry.get("comparison") or {}
    if "remaining" in entry and run.get("valid") and run.get("fresh", True):
        own = (run.get("counts") or {}).get(entry["id"])
        if own is None or own[1] > entry["remaining"]:
            observed = "unmeasured" if own is None else str(own[1])
            return [f"{entry['id']} remaining work {observed}; approved maximum {entry['remaining']}"]
    if (saving := entry.get("saving")) and run.get("fresh", True) and original_outcome(run) == "passed":
        if not _comparable(saving, run, entry["id"]):
            return [f"the earned {entry['id']} workload on {saving['tree']} needs a comparable measurement"]
        if (best := run["counts"][entry["id"]][1]) > saving["best"]:
            return [f"the earned {entry['id']} saving on {saving['tree']} regressed: {saving['best']} -> {best}"]
    if run.get("differs"):
        return ["results differ at " + run["differs"]]
    if not producer_proved(entry):
        if run.get("fresh") is False:
            return [run.get("freshnessReason", "comparison inputs changed")]
        if run.get("valid") is False and run.get("arms") and run.get("obligation", obligation(entry))[1:4] == obligation(entry)[1:4]:
            return ["comparison not valid (" + ", ".join(arm["outcome"] for arm in run["arms"]) + "): " + run["command"]]
        return ["no current valid command comparison"]
    original, current = original_outcome(run), run["arms"][-1]["outcome"]
    if current != "passed":
        return ["candidate command " + current + ": " + run["command"]]
    expected = "passed" if entry["kind"] == "preservation" else "failed"
    # two passing runs prove a saving, or an approved allowance, by the item's own count
    if original != expected and not (measured is not False and original == "passed" and _own_count(entry["id"], run, entry.get("remaining"))):
        return [f"{entry['kind']} needs original {expected}, candidate passed" + ("" if entry["kind"] == "preservation" or measured is False else f", or both passed with the count {entry['id']} lower on the candidate") + f"; original {original}: " + run["command"]]
    return []


def _own_count(identifier: str, run: JsonObject, remaining: int | None = None) -> str | None:
    """This item's count on both trees: "saving", lower on the candidate, alone earns; "allowed" under a `remaining` allowance."""
    original, candidate = (run.get("counts") or {}).get(identifier) or (0, 0)
    return "saving" if candidate < original else "allowed" if remaining is not None and original <= remaining else None


def _question(entry: JsonObject, proofs: list[JsonObject], keys: bool = False, measured: tuple[str, ...] = ()) -> list[str]:
    if readings_unsettled(entry):
        return _case_question(entry)
    if entry.get("cases"):
        return [f"unexecuted declarations: {len(entry['cases'])}"] if keys else ["unexecuted declaration: " + command for command in entry["cases"]]
    candidates = [proof for proof in proofs if proof["id"] == entry["id"] and proof.get("comparison")
                  and obligation(proof)[1:] == obligation(entry)[1:]]
    if not candidates:
        return ["no case is bound"]
    reasons = []
    for proof in candidates:
        reasons.extend(_case_question({**proof, "kind": entry.get("kind"),
                                      **({"remaining": entry["remaining"]} if "remaining" in entry else {})}, measured=entry["id"] in measured))
        own = (proof["comparison"].get("counts") or {}).get(entry["id"])  # a measured cost's own count is its claimed saving, whatever its kind
        if (own and (entry["id"] in measured or entry.get("kind") != "preservation")
                and not _own_count(entry["id"], proof["comparison"], entry.get("remaining"))):
            reasons.append(f"its count must fall on the candidate: {entry['id']} {own[0]} -> {own[1]}")
    if entry["id"] in measured and not any(
            _own_count(entry["id"], run, entry.get("remaining"))
            or len(run["arms"]) == 4 and original_outcome(run) == "failed" and run["valid"]
            for proof in candidates if (run := proof["comparison"]).get("valid")):
        refs = {proof["comparison"]["command"] for proof in candidates}
        shared = any(other["id"] != entry["id"] and other["id"] in measured
                     and (other.get("comparison") or {}).get("command") in refs for other in proofs)
        reasons.append(("shares its case with another measured cost and prints no falling count of its own" if shared
                        else "measure its own work on both trees") + f': WORKFLOW_METRICS={{"{entry["id"]}": COUNT, ...}}')
    return list(dict.fromkeys(reasons))


def current_cases(items: list[JsonObject], runs: list[JsonObject], retired: set[str] = frozenset()) -> list[JsonObject]:
    """Project current command bindings from the existing run history."""
    cases = []
    for entry in items:
        proof = entry.get("comparison") or {}
        references = proof.get("caseRuns") or {}
        latest = {}
        for index in sorted(set(references.values())):
            run = runs[index]
            latest[run["command"]] = run
        for run in latest.values():
            cases.append({**entry, "comparison": {**run, "obligation": proof.get("obligation", obligation(entry))}})
    return [case for case in cases if not case.get("comparison") or case_key(case["id"], case["comparison"]) not in retired] if retired else cases


def open_obligations(items: list[JsonObject], evaluation: list[JsonObject] | None = None, current: list[JsonObject] | None = None,
                     keys: bool = False, measured: tuple[str, ...] = ()) -> list[str]:
    """One command is one case. Output and framework names never determine readiness. `keys`: a testing reply's lines, whose
    case rows carry the commands: a declaration is counted, not quoted."""
    retained = evaluation or []
    lines, rerun_items, rerun_cases = [], 0, 0
    proofs = [*(current or items), *retained]
    for group, retained_item in ((items, False), (retained, True)):
        for entry in group:
            reasons = _case_question(entry) if retained_item else _question(entry, proofs, keys, measured)
            if not reasons:
                continue
            if reasons == ["execution inputs changed or unrecorded"]:
                rerun_items += not retained_item
                rerun_cases += retained_item
                continue
            label = ("retained " if retained_item else "") + f"{entry.get('caseKey', entry['id'])} ({entry.get('kind') or 'kind?'})"
            rerun = any(reason.startswith(("unexecuted declaration", "no current valid", "comparison inputs", "recorded execution inputs")) for reason in reasons)
            lines.append(label + ": " + "; ".join(reasons) + ("; workflow.run args [\"tdd\"]" if rerun else "; its case is the lead's to write" if reasons == ["no case is bound"] else "")
                         + (", or " + json.dumps(["tdd", "--correct", entry["id"], *([] if keys else entry["cases"][:1])])
                            + (", the declaration" if keys and len(entry["cases"]) > 1 else "") + " and the command that replaces it"
                            if not retained_item and entry.get("cases") else ""))
    if rerun_items or rerun_cases:
        lines.append(f"{rerun_items} items and {rerun_cases} cases need a rerun; workflow.run args [\"tdd\"]")
    return lines


def release(items: list[JsonObject], owner: JsonObject, run: JsonObject, runs: list[JsonObject], approved: dict[str, JsonObject]) -> None:
    """A command proved under `owner` leaves every other item it does not satisfy; an item it proves keeps it, and a
    kept-behaviour item keeps one that passes on the original, and one its `approved` draft declares: each its own verdict."""
    source = run["command"]
    for entry in items:
        cases = current_cases([entry], runs) if entry is not owner and (entry.get("comparison") or {}).get("caseRuns") else []
        kept = {case["comparison"]["command"]: case["comparison"]["runIndex"] for case in cases
                if case["comparison"]["command"] != source or not _case_question(case)
                or source in approved.get(entry["id"], {}).get("cases", ())
                or entry.get("kind") == "preservation" and original_outcome(case["comparison"]) == "passed"}
        if len(kept) < len(cases):
            recorded = entry.pop("comparison")["obligation"]
            if kept:
                entry["comparison"] = {**runs[max(kept.values())], "obligation": recorded, "caseRuns": kept}


def retain_evaluation(items: list[JsonObject], required: list[JsonObject], retained: JsonObject, runs: list[JsonObject], retired: set[str]) -> None:
    """Retain each first proved command and every later distinct one. A proved recapture of a command replaces its earlier captures
    under its item and under map items no longer bound to the command, except a retired one and except when it names support:
    candidate bytes of a pass-start test file never displace a frozen capture."""
    binders: dict[str, set[str]] = {}  # per command, the map items bound to it
    for case in current_cases(items, runs):
        binders.setdefault(case["comparison"]["command"], set()).add(case["id"])
    present = {entry["id"] for entry in items}
    for entry in required:
        if _case_question(entry):
            continue
        run = entry["comparison"]
        command, key = run["command"], case_key(entry["id"], run)
        related = {name: case_key(old["id"], prior) for name, old in retained.items()
                   if (prior := runs[old["runIndex"]])["command"] == command and
                   (old["id"] == entry["id"] or old["id"] in present and old["id"] not in binders.get(command, ()))}
        earlier = [name for name, capture in related.items() if capture not in retired and not retained[name].get("saving")]
        if key in retired or key in related.values() or earlier and run.get("support"):
            continue
        for name in earlier:
            del retained[name]
        key = entry["id"] if entry["id"] not in retained else key
        retained[key] = {**copy.deepcopy({key: value for key, value in entry.items() if key != "comparison"}),
                         "runIndex": run["runIndex"]}


def retain_savings(retained: JsonObject, items: list[JsonObject], runs: list[JsonObject], candidate: str) -> None:
    """Each surviving archive earns its own best. The live earning also replaces earlier equal or worse meters of its workload."""
    if not any(e["id"] in (runs[e["runIndex"]].get("counts") or {}) for e in retained.values()):
        return
    cases = current_cases(items, runs)
    live = {(case["id"], case["comparison"]["command"]): case["comparison"] for case in cases if "measuring" not in case["comparison"]}
    def workload(entry):
        return entry["id"], entry.get("workload") or runs[entry["runIndex"]]["command"]
    # Missing another optimization budget cannot erase savings on a functionally correct candidate.
    if all(producer_proved(case) and (run := case["comparison"])["candidateTree"] == candidate and run["arms"][-1]["outcome"] == "passed"
           and (case.get("measured") or not _case_question(case)) for case in cases):
        for entry in retained.values():
            own = runs[entry["runIndex"]]
            for run in (own, live.get(workload(entry), own)) if entry.get("saving") else (own,):
                if run["candidateTree"] != candidate or not run["valid"] or not _own_count(entry["id"], run):
                    continue
                before, after = run["counts"][entry["id"]]
                prior = entry.get("saving")
                if not prior or (after < prior["best"] if _comparable(prior, run, entry["id"])
                                 else after <= prior["best"] and "measuring" not in run):
                    entry.update(runIndex=run["runIndex"], saving=dict(original=before, best=after, tree=candidate,
                                                                       originalRef=run["arms"][0].get("resultRef")))
    current, kept = {case["comparison"]["runIndex"] for case in cases}, {}
    for name, entry in sorted(((name, entry) for name, entry in retained.items() if entry.get("saving")),
                              key=lambda pair: (pair[1]["saving"]["best"], pair[1]["runIndex"] not in current)):
        group, best = workload(entry), entry["saving"]["best"]
        if kept.get(group, ...) in (None, best):
            del retained[name]
        else:
            kept[group] = None if entry["runIndex"] in current else best


def _comparable(saving: JsonObject, run: JsonObject, identifier: str) -> bool:
    """A measurement of the earned workload: its original count and stored original result."""
    counts = (run.get("counts") or {}).get(identifier)
    return bool(counts) and counts[0] == saving["original"] and run["arms"][0].get("resultRef") == saving.get("originalRef")


def case_key(identifier: str, run: JsonObject) -> str:
    """Identify the recorded case, independent of later replay inputs."""
    return identifier + ":" + run["captureKey"]


def obligation(entry: JsonObject) -> list[object]:
    return [entry.get(key) for key in ("kind", "behavior", "seam", "expected")]


def producer_proved(entry: JsonObject) -> bool:
    """Bind proof to behavior, Seam and expected result; kind is revisable judgment."""
    comparison = entry.get("comparison")
    return (isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True
            and bool(comparison.get("command")) and bool(comparison.get("arms"))
            and isinstance(recorded := comparison.get("obligation", obligation(entry)), list)
            and recorded[1:4] == obligation(entry)[1:4])


def failure_line(arm: JsonObject) -> str:
    """A failed arm in one line: its last file and line, the smallest failing input it named and its error, else its final line, cut; the evidence keeps it whole."""
    output = str(arm.get("output") or arm.get("bindingError") or arm.get("outcome", "")).strip()
    frame, named = re.findall(r'File "[^"]*?([^"/]+)", line (\d+)', output)[-1:], re.findall(r"(?m)^.*smallest failing input.*$", output)[-1:]
    error = re.findall(r"(?m)^[\w.]*(?:Error|Exception)\b.*$", output)[-1:] or output.splitlines()[-1:]
    summary = " ".join(dict.fromkeys([*(f"{name}:{line}" for name, line in frame), *named, *error]))
    return summary.encode("utf-8", "replace")[:300].decode("utf-8", "ignore")


def rejection_view(record: JsonObject) -> JsonObject:
    """The ledger retains the full test output and proposed patch."""
    return {**{key: value for key, value in record.items() if key not in ("removal", "output")},
            **({"output": " ".join(record["output"].split()).encode("utf-8", "replace")[:300].decode("utf-8", "ignore")} if record.get("output") else {})}


def comparison_view(run: JsonObject, *, detail: bool = False, show_output: bool = True) -> JsonObject:
    """One compact exit-code comparison, shared by receipts, evidence reads and review input; detail adds its item and its first failed arm's line."""
    arms = run.get("arms") or []
    failed = next((arm for arm in arms if arm.get("outcome") != "passed"), None) if detail and show_output else None
    return {"comparison": run.get("comparison", "incomplete"), "valid": run.get("valid", False),
            "runIndex": run.get("runIndex"), "command": run.get("command"),
            **({"behaviorId": run["behaviorId"]} if detail and run.get("behaviorId") else {}),
            **{key: run[key] for key in ("differs", "counts") if run.get(key)},
            **({"failedOnSameCode": True} if run.get("failedOnSameCode") else {}),
            "probeTree": run.get("probeTree"),
            "sensitive": run.get("sensitive", False),
            "arms": [{"source": "original" if index < (len(arms) + 1) // 2 else "current", "tree": arm.get("requestedTree", arm.get("sourceTree")),
                      "outcome": arm.get("outcome"), "exitCode": arm.get("exitCode"), "timedOut": arm.get("timedOut", False),
                      **({"resultRef": arm["resultRef"]} if arm.get("resultRef") else {}),
                      **({"outputTail": failure_line(arm)} if arm is failed else {})}
                     for index, arm in enumerate(arms)],
            **({"limitations": [run["bindingError"]]} if run.get("bindingError") else {})}


def compact(value: object) -> object:
    """An evidence value with every comparison inside it replaced by its detailed view."""
    show_output = True
    def visit(item):
        nonlocal show_output
        if isinstance(item, dict):
            if isinstance(item.get("arms"), list):
                view = comparison_view(item, detail=True, show_output=show_output)
                if show_output and any("outputTail" in arm for arm in view["arms"]):
                    show_output = False
                return view
            return {key: visit(field) for key, field in item.items()}
        return [visit(field) for field in item] if isinstance(item, list) else item
    return visit(value)


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
