"""Probe obligations, one readiness judgement and one compact comparison view shared by workflow consumers."""
from __future__ import annotations

import copy
import difflib
import re
import shlex
from typing import Callable

from . import tdd_surface

JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
KINDS = frozenset({"contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Judgement fields: a map update may change them without invalidating the executed
# comparison, which is re-judged; a changed obligation text requires a new comparison.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs", "sourceRefs"}
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
UNITTEST_CASE = re.compile(r"^(\S+ \([^()\n]+\))((?: \[.*?\])?(?: \(.*\))?)$")
STOPPED_NOTE = ("stopped: a test method that failed in its body skipped its remaining statements on that tree, "
                "so inputs after the failure did not execute there")


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _strings(value: object) -> list[str] | None:
    """A non-empty list of non-empty strings, else None."""
    if isinstance(value, list) and value and all(_text(item) for item in value):
        return [str(item).strip() for item in value]
    return None


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


def _field_errors(raw: JsonObject, identifier: str) -> list[str]:
    errors = [f"probe {identifier} kind must be contract or preservation"] if "kind" in raw and not (
        isinstance(raw["kind"], str) and raw["kind"] in KINDS) else []
    if "boundaryInputs" in raw and _strings(raw["boundaryInputs"]) is None:
        errors.append(f"probe {identifier} boundaryInputs must name executed cases as non-empty strings")
    elif described := [name for name in raw.get("boundaryInputs") or [] if ": " in name and "::" not in name]:
        errors.append(f"probe {identifier} boundaryInputs name executed cases; move the description {described[0]!r} into expected")
    readings = raw.get("interpretations")
    if "interpretations" in raw and (_strings(readings) is None or len(readings) < 2):
        errors.append(f"probe {identifier} interpretations requires at least two competing readings")
    settled = {key for key in ("interpretation", "authority") if key in raw}
    if settled and ("interpretations" not in raw or settled != {"interpretation", "authority"}
                    or not all(_text(raw[key]) for key in settled)):
        errors.append(f"probe {identifier} settles its readings with both interpretation and authority")
    return errors


def _recorded_fields(raw: JsonObject) -> JsonObject:
    """The optional fields in their valid shape; a recorded item's other fields (an earlier
    recorder's, or a typed input list) are absent rather than refused."""
    fields: JsonObject = {}
    if raw.get("kind") in KINDS:
        fields["kind"] = raw["kind"]
    for key in ("boundaryInputs", "interpretations"):
        if (values := _strings(raw.get(key))) and (key != "interpretations" or len(values) >= 2):
            fields[key] = values
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
            if "kind" not in raw:
                errors.append(f"probe {identifier} requires kind: contract for a requested change, preservation for an invariant")
            if extra := set(raw) - REQUIRED_FIELDS - JUDGEMENT_FIELDS:
                errors.append(f"probe {identifier} has unknown fields: " + ", ".join(sorted(extra)))
            errors.extend(_field_errors(raw, identifier))
        if "sourceRefs" in raw:
            errors.extend(_source_refs(raw["sourceRefs"], identifier))
    return errors


def validate_items(value: object, *, allow_runtime: bool) -> list[JsonObject]:
    if errors := map_errors(value, allow_runtime=allow_runtime):
        raise ValueError("; ".join(errors))
    items = []
    for raw in value:
        item = {**{key: _text(raw[key]) for key in REQUIRED_FIELDS}, **_recorded_fields(raw),
                "sourceRefs": _refs(raw.get("sourceRefs") or [])}
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


def readings_unsettled(entry: JsonObject) -> bool:
    return bool(entry.get("interpretations")) and not (entry.get("interpretation") and entry.get("authority"))


def unverified(entry: JsonObject | None) -> bool:
    """The lead's printed `<case>: unverified - <why>` note that a case's measurement is unavailable;
    it never proves. An ordinary printed value is a result, even the word `unverified`."""
    return entry is not None and entry.get("outcome") == "printed" and str(entry.get("result", "")).startswith("unverified - ")


# --- executed cases -------------------------------------------------------------

def case_label(name: str) -> str:
    """The stable short id a question names a case by: the runner's own id with its parameter."""
    return max(short_ids(name) - {name}, key=len, default=name)


def short_ids(name: str) -> set[str]:
    """The identities a boundary input may name: the full case name, the runner's short id,
    and a parametrized id without its parameters. Never a substring."""
    ids = {name}
    if match := UNITTEST_CASE.match(name):
        ids.add(match.group(1).split(" (")[0])
    elif "::" in name:
        last = name.rsplit("::", 1)[1]
        ids.update({last, last.split("[")[0]})
    return ids


def _arm_labels(run: JsonObject) -> list[str]:
    reviewed = set((run.get("reviewSources") or {}).values())
    labels, counts = [], {"reviewed": 0, "earlier": 0}
    for index, arm in enumerate(run["arms"]):
        if index == 0:
            labels.append("original")
        elif index == len(run["arms"]) - 1:
            labels.append("current")
        else:
            kind = "reviewed" if arm["requestedTree"] in reviewed else "earlier"
            counts[kind] += 1
            labels.append(kind if counts[kind] == 1 else f"{kind} {counts[kind]}")
    return labels


def folded_cases(run: JsonObject) -> tuple[dict[str, dict[str, JsonObject]], dict[str, int]]:
    """Per case name, each arm label's result; unittest subtests fold into their method, which
    is then named on every tree; unnamed counts cases named elsewhere but not on that tree."""
    labels = _arm_labels(run)
    raw: dict[str, dict[str, JsonObject]] = {}
    for label, arm in zip(labels, run["arms"]):
        for name, entry in (arm.get("cases") or {}).items():
            raw.setdefault(name, {})[label] = dict(entry)
    cases: dict[str, dict[str, JsonObject]] = {}
    unnamed = dict.fromkeys(labels, 0)
    for name, by_tree in raw.items():
        match = UNITTEST_CASE.match(name)
        parent = match.group(1) if match and match.group(2) else None
        if parent is None:
            cases.setdefault(name, {}).update(by_tree)
            continue
        owner = cases.setdefault(parent, {})
        for label, entry in by_tree.items():
            if entry.get("outcome") in {"failed", "error"}:
                if owner.get(label, {}).get("outcome") not in {"failed", "error"}:
                    owner[label] = {**entry, "subtests": 0}
                elif entry.get("assertion") and entry["assertion"] not in owner[label].get("assertion", "").split("\n"):
                    owner[label]["assertion"] = owner[label].get("assertion", "") + "\n" + entry["assertion"]
            if label in owner and "subtests" in owner[label]:
                owner[label]["subtests"] += 1
        for label in labels:
            if label not in by_tree:
                unnamed[label] += 1
    for name, by_tree in cases.items():
        for label in labels:
            if label not in by_tree and name in raw:
                unnamed[label] += 1
    return cases, unnamed


def _state(entry: JsonObject | None) -> str:
    if entry is None:
        return "unnamed"
    if entry.get("outcome") == "printed":
        return str(entry.get("result", ""))
    return str(entry["outcome"])


def _passed(entry: JsonObject | None, arm_outcome: str) -> bool:
    if entry is None:
        return False
    if entry.get("outcome") == "printed":
        return arm_outcome == "passed"
    return entry["outcome"] == "passed"


def _differs(original: JsonObject | None, current: JsonObject | None) -> bool:
    return _state(original) != _state(current)


# --- readiness ------------------------------------------------------------------

def _matched(name: str, cases: dict[str, dict[str, JsonObject]]) -> tuple[list[str], str | None]:
    """The executed cases a boundary name selects: the exact case, else one test's short id with
    its parameters; a short id that several tests share selects none of them."""
    if name in cases:
        return [name], None
    aliases = [case for case in cases if name in short_ids(case)]
    if len({case.split("[")[0] for case in aliases}) > 1:
        return [], f"{name} matches several tests ({', '.join(sorted(aliases))}): name the full case id"
    return aliases, None


OUTPUT_LINE = "unnamed output line: "


def _judge(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]]) -> JsonObject:
    """The item's verdict on one comparison: `reasons` it is not proved (none when proved), whether
    an executed outcome rather than missing evidence leaves it open (`found`), and the observations
    `exposing` it, each with the original tree's result: an attributable case that does not pass
    unchanged (for a contract item, a named case that passed on the original and fails on current), a
    differing case no item names, and each differing unnamed output line. A case another item names is
    that item's to judge; a differing case no item names stays this preservation item's."""
    run = entry["comparison"]
    original_outcome, current_outcome = run["arms"][0]["outcome"], run["arms"][-1]["outcome"]
    reasons: list[str] = []
    found: list[str] = []
    exposing: dict[str, object] = {}
    attributable: dict[str, str] = {}
    contract = entry.get("kind") == "contract"
    claimed = _claimed(entry, items, cases)
    # a sibling that has not run defers the cases its names select here: owed, not this item's failure
    deferred = {case for other in items if other is not entry and not (other.get("comparison") or {}).get("arms")
                for name in other.get("boundaryInputs") or [] for case in _matched(name, cases)[0]} - claimed

    def expose(case: str) -> None:
        exposing[case] = _result(cases[case].get("original"), original_outcome)

    if inputs := entry.get("boundaryInputs"):
        for name in inputs:
            matched, ambiguity = _matched(name, cases)
            if ambiguity or not matched:
                reasons.append(ambiguity or f"{name} missing on original, current")
            attributable.update(dict.fromkeys(matched, name))
        unclaimed = {case: case_label(case) for case, by_tree in cases.items()
                     if case not in attributable and case not in claimed | deferred and not short_ids(case) & set(inputs)
                     and _differs(by_tree.get("original"), by_tree.get("current"))}
        if contract:
            found.extend(f"{label} differs and no item names it" for label in unclaimed.values())
            for case in unclaimed:
                expose(case)
        else:
            attributable.update(unclaimed)
    elif owners := _owners(entry, items):
        return _verdict([f"shares its batch with {', '.join(owner['id'] for owner in owners)}: name its cases in boundaryInputs"])
    elif contract and len(cases) > 1:
        return _verdict(["several cases executed: name the requested ones in boundaryInputs"])
    else:
        requested = _claimed(entry, items, cases, lambda other: other.get("kind") == "contract")
        attributable = {name: case_label(name) for name in cases if name not in requested | deferred}
    for case in deferred - set(attributable):
        if _differs(cases[case].get("original"), cases[case].get("current")):
            expose(case)
    if not contract and (changed := _unnamed_changes(run)):
        found.append("unnamed output differs between original and current: name the cases "
                     "(printed `name: result` lines, in a command apart from any test runner's report)")
        before = _unnamed_lines(run["arms"][0], report=_reports(run)) or []
        exposing.update({OUTPUT_LINE + line[2:]: before.count(line[2:]) for line in changed})
    if not attributable and not reasons and not found:
        return _verdict(["no attributable case executed: name the cases (test ids or printed `name: result` lines)"])
    differing = []
    for case, label in attributable.items():
        original, current = cases[case].get("original"), cases[case].get("current")
        if unverified(original) or unverified(current):
            reasons.append(f"{label} unverified: measurement unavailable is not proof")
        elif contract:
            if not _passed(current, current_outcome):
                found.append(f"{label} {_state(current)} on current")
                if _passed(original, original_outcome):
                    expose(case)
            if original is None or original.get("outcome") == "skipped":
                reasons.append(f"{label} {_state(original)} on original")
            elif _passed(current, current_outcome) and _differs(original, current):
                differing.append(label)
        elif not (_passed(original, original_outcome) and _passed(current, current_outcome)) or _differs(original, current):
            found.append(f"{label} differs (original={_state(original)}, current={_state(current)})")
            if _differs(original, current) or _passed(original, original_outcome):
                expose(case)  # an equal failure or skip on both trees proves nothing and regresses nothing
    if contract and not differing and not reasons and not found:
        found.append("no attributable case differs between original and current")
    if found:
        found.append(("name every differing case in its owning item and show the requested change on it" if contract
                      else "preservation cases must pass unchanged on both trees")
                     + ": repair the code or the probe")
    return {**_verdict(reasons + found, bool(found), exposing), "deferred": deferred}


def _verdict(reasons: list[str], found: bool = False, exposing: dict[str, object] | None = None) -> JsonObject:
    return {"reasons": reasons, "found": found, "exposing": exposing or {}, "deferred": set()}


def _owners(entry: JsonObject, items: list[JsonObject]) -> list[JsonObject]:
    index = (entry.get("comparison") or {}).get("runIndex")
    return [other for other in items if other is not entry and (other.get("comparison") or {}).get("runIndex") == index]


def _claimed(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]],
             keep: Callable[[JsonObject], bool] = lambda other: True) -> set[str]:
    """The exact cases other items' names select, each in that item's own executed comparison; an item
    that has not run claims nothing, and a short id shared by another test claims nothing here."""
    return {case for other in items if other is not entry and keep(other) and (other.get("comparison") or {}).get("arms")
            for name in other.get("boundaryInputs") or [] for case in _matched(name, folded_cases(other["comparison"])[0])[0]}


def _result(entry: JsonObject | None, arm_outcome: str) -> str | None:
    """A case's result on one tree: None when absent, its outcome, or its printed value on a run that passed."""
    if entry is None:
        return None
    return _state(entry) if entry.get("outcome") != "printed" or arm_outcome == "passed" else "failed"


def _owed(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]],
          exposing: dict[str, object]) -> dict[str, dict[str, object]]:
    """The one rule that clears a regression. Per command, an item owes every observation that exposed
    it, kept from its first exposure with the original tree's result. The debt is paid only when that
    command, or for a case exposed by a directly invoked unittest or pytest run (whose ids are the same
    for every such run from the checkout root) any such run, executes the case again and restores it:
    it passes on the original again and unchanged on current, or it is a contract item's own requested case passing verified, the original passing again if it passed when exposed; a case
    only the edited source produced must be gone, and each lost output line printed as often as the
    original printed it, under the command that exposed it. An observation that differs in this
    comparison is owed by it. A missing, skipped, unverified or renamed case, a command that does not
    run it, another item or a passing remainder pays nothing; another contract naming the case only
    excuses it (`judgement`)."""
    run = entry["comparison"]
    command, before, after = run.get("command"), run["arms"][0]["outcome"], run["arms"][-1]["outcome"]
    own = {case for name in entry.get("boundaryInputs") or [] for case in _matched(name, cases)[0]} if entry.get("kind") == "contract" else set()
    printed = _unnamed_lines(run["arms"][-1], report=_reports(run))

    def paid(observation: str, result: object) -> bool:
        if observation.startswith(OUTPUT_LINE):
            return after == "passed" and printed is not None and printed.count(observation[len(OUTPUT_LINE):]) == result
        original, current = cases.get(observation, {}).get("original"), cases.get(observation, {}).get("current")
        if result is None:
            return current is None and after == "passed"
        return _passed(current, after) and not unverified(current) and (
            observation in own and (result != "passed" or _passed(original, before))
            or _passed(original, before) and not _differs(original, current))

    stored = run.get("regressions") or {}
    owed = {}
    for key, results in {**stored, command: {**exposing, **stored.get(command, {})}}.items():
        here = lambda observation: key == command or (_direct(key) and _direct(command)
                                                      and cases.get(observation, {}).get("current") is not None)
        if kept := {observation: result for observation, result in results.items()
                    if key == command and observation in exposing or not (here(observation) and paid(observation, result))}:
            owed[key] = kept
    return owed


def _direct(command: str | None) -> bool:
    """A unittest or pytest run invoked directly, not by discovery from another start directory."""
    surface = tdd_surface.identify(shlex.split(command or ""))
    return surface.get("runner") in tdd_surface.NATIVE_RUNNERS and (surface.get("arguments") or [""])[0] != "discover"


def judgement(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]] | None = None) -> JsonObject | None:
    """The item's verdict on its latest comparison, fresh or stale, with what its regressions still
    owe (`owed`, the runner stores it; `retained`, those open that this comparison does not already
    report), and whether the item is `exposed`: open on an executed outcome, now or since an earlier
    comparison, until one proves it; an owed regression keeps it exposed. An owed case is excused, not
    paid, while the preflight contract names it: the exact case its name selects in that contract's own
    comparison, which then must prove it. A case a sibling that has not run names is deferred, owed but
    not this item's failure; a debt deferred only by a later map edit stays open."""
    comparison = entry.get("comparison") or {}
    if not comparison.get("arms") or entry.get("kind") not in KINDS:
        return None
    cases = folded_cases(comparison)[0] if cases is None else cases
    verdict = _judge(entry, items, cases)
    owed = _owed(entry, items, cases, verdict["exposing"])
    requested = _claimed(entry, items, cases, lambda other: other.get("kind") == "contract"
                         and bool((other.get("comparison") or {}).get("approved")))
    open_ = {command: [key for key in results if key not in requested] for command, results in owed.items()}
    deferred = verdict["deferred"] - set(comparison.get("deferred", verdict["deferred"]))
    retained = {command: rest for command, results in open_.items() if (rest := [
        key for key in results if command != comparison.get("command") or key not in verdict["exposing"] or key in deferred])}
    exposed = bool(verdict["reasons"] or owed) and bool(verdict["found"] or owed or comparison.get("exposed"))
    return {**verdict, "owed": owed, "retained": retained, "exposed": exposed}


def approved_contracts(items: list[JsonObject], recorded: list[JsonObject] | None) -> frozenset[str]:
    """The contract items recorded at preflight whose obligation is unchanged: the requested changes."""
    before = {entry["id"]: entry for entry in recorded or []}
    return frozenset(entry["id"] for entry in items if entry.get("kind") == "contract" and entry["id"] in before
                     and all(before[entry["id"]].get(key) == entry.get(key) for key in ("kind", "behavior", "seam", "expected")))


def _retained_line(command: str, observations: list[str]) -> str:
    what = ", ".join(dict.fromkeys("its differing output" if key.startswith(OUTPUT_LINE) else case_label(key) for key in observations))
    return (f"{what} exposed this item under `{command}` and has not come back since: repair the code and rerun "
            "it; a passing remainder is not a repair")


def open_obligations(items: list[JsonObject]) -> list[str]:
    """The single readiness result: one question per item whose required proof is missing.
    Items sharing one comparison fold its cases once."""
    lines: list[str] = []
    folded: dict[object, dict[str, dict[str, JsonObject]]] = {}
    for entry in items:
        head = f"{entry['id']} ({entry.get('kind') or 'kind?'}): {entry['behavior']} => {entry['expected']}; "
        comparison = entry.get("comparison") or {}
        if comparison.get("arms") and comparison.get("runIndex") not in folded:
            folded[comparison.get("runIndex")] = folded_cases(comparison)[0]
        cases = folded.get(comparison.get("runIndex"), {}) if comparison.get("arms") else {}
        if entry.get("kind") not in KINDS:
            lines.append(head + "declare kind (contract or preservation) through record tdd-map")
        elif entry.get("boundaryInputs") == []:
            lines.append(head + "recorded boundary inputs are not executed case names: map them through record tdd-map")
        elif readings_unsettled(entry):
            lines.append(head + "readings unsettled: " + " | ".join(entry["interpretations"]) + " - record interpretation and authority")
        elif not producer_proved(entry):
            reason = ("comparison stale (production, probe or obligation changed): rerun it" if comparison.get("valid")
                      else "no current valid comparison")
            if cases and entry.get("boundaryInputs"):
                reason += "; " + "; ".join(
                    f"{name} " + ", ".join(f"{_state(cases.get(case, {}).get(label))} on {label}" for label in ("original", "current"))
                    for name in entry["boundaryInputs"]
                    for case in [next(iter(_matched(name, cases)[0]), None)])
            elif entry.get("boundaryInputs"):
                reason += "; its cases: " + ", ".join(map(str, entry["boundaryInputs"]))
            if judged := judgement(entry, items, cases):
                reason += "".join(f"; {_retained_line(key, seen)}" for key, seen in judged["retained"].items())
            lines.append(head + reason)
        else:
            judged = judgement(entry, items, cases)
            reasons = judged["reasons"] + [_retained_line(key, seen) for key, seen in judged["retained"].items()]
            if reasons:
                lines.append(head + "; ".join(reasons))
    return lines


def obligation(entry: JsonObject) -> list[object]:
    return [entry.get(key) for key in ("kind", "behavior", "seam", "expected")]


def producer_proved(entry: JsonObject) -> bool:
    comparison = entry.get("comparison")
    return (isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True
            and comparison.get("obligation", obligation(entry)) == obligation(entry))


def unresolved(items: list[JsonObject]) -> list[str]:
    return [line.split(" ", 1)[0] for line in open_obligations(items)]


def never_compared(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not (entry.get("comparison") or {}).get("arms")]


def contract_changes(items: list[JsonObject], recorded: list[JsonObject] | None) -> list[str]:
    """What moved since the recorded preflight, each with the basis or finding it carries, for the
    reviewer and final advisor to judge against the request or that finding: a changed kind, a
    contract item added or re-worded, contract cases added and any case dropped. Listing authorizes
    nothing and never clears an open item."""
    before = {entry["id"]: entry for entry in recorded or []}
    lines = []
    for entry in items:
        prior, kind = before.get(entry["id"]), entry.get("kind")
        findings = [str(ref["id"]) for ref in entry.get("sourceRefs", []) if ref.get("type") == "finding"]
        changes = []
        if prior is None:
            if kind == "contract":
                changes.append("contract item added after preflight" + (f" (finding {', '.join(findings)})" if findings else ""))
        else:
            inputs, earlier = set(entry.get("boundaryInputs") or []), set(prior.get("boundaryInputs") or [])
            if prior.get("kind") != kind:
                changes.append(f"{prior.get('kind')} -> {kind}")
            elif kind == "contract" and any(prior.get(key) != entry.get(key) for key in ("behavior", "expected")):
                changes.append("contract expectation re-worded")
            if kind == "contract" and inputs - earlier:
                changes.append("contract cases added: " + ", ".join(sorted(inputs - earlier)))
            if earlier - inputs:
                changes.append(f"{prior.get('kind')} cases dropped: " + ", ".join(sorted(earlier - inputs)))
        lines.extend(f"{entry['id']}: {change}: {entry['basis']}" for change in changes)
    return lines


# --- the compact comparison view -------------------------------------------------

def _runner(run: JsonObject) -> str | None:
    """The runner whose report the arms carry, including one a wrapping command printed."""
    surface = tdd_surface.identify(shlex.split(run["command"])) if run.get("command") else {}
    return next(filter(None, (tdd_surface.native_runner(surface, str(arm.get("output", ""))) for arm in run.get("arms", []))), None)


def _unnamed_changes(run: JsonObject) -> list[str]:
    """Output lines no named case accounts for that differ between original and current. A test
    runner's own command names everything; beside a printed runner report, every line outside that
    report is judged, whatever the report's cases show."""
    if tdd_surface.identify(shlex.split(run.get("command") or "")).get("runner") in tdd_surface.NATIVE_RUNNERS:
        return []
    return _changed_lines(run["arms"][0], run["arms"][-1], report=_reports(run))


def _reports(run: JsonObject) -> bool:
    """Whether the command prints a test runner's report beside its own output."""
    return _runner(run) in tdd_surface.NATIVE_RUNNERS


def comparison_view(run: JsonObject) -> JsonObject:
    """One rendering for the lead receipt and the review packet; everything else stays in evidence."""
    labels = _arm_labels(run)
    cases, unnamed = folded_cases(run)
    surface = tdd_surface.identify(shlex.split(run["command"])) if run.get("command") else {}
    runner = _runner(run)
    outcomes = dict(zip(labels, (arm["outcome"] for arm in run["arms"])))
    groups: dict[tuple[str, str], list[str]] = {}
    for name, by_tree in sorted(cases.items()):
        original, current = by_tree.get("original"), by_tree.get("current")
        if _passed(current, outcomes["current"]) and all(not _differs(original, by_tree.get(label)) for label in labels):
            continue
        pattern = ", ".join(f"{label}={_state(by_tree.get(label))}"
                            + (" (stopped)" if by_tree.get(label, {}).get("execution") == "stopped" else "")
                            + (f" ({by_tree[label]['subtests']} subtests)" if by_tree.get(label, {}).get("subtests") else "")
                            for label in labels)
        assertions = tuple(dict.fromkeys(" ".join(part.split()) for label in labels
                                         for part in str(by_tree.get(label, {}).get("assertion") or "").split("\n") if part.strip()))
        groups.setdefault((pattern, assertions), []).append(name)
    lines: list[str] = []
    shown: set[str] = set()
    for (pattern, assertions), names in groups.items():
        listed = ", ".join(names[:8]) + (f" +{len(names) - 8} more in evidence" if len(names) > 8 else "")
        line = (f"{len(names)} cases: " if len(names) > 1 else "") + f"{listed}: {pattern}"
        for assertion in (fresh := [assertion for assertion in assertions if assertion not in shown]):
            line += "; " + assertion[:240] + ("... [full assertion in evidence]" if len(assertion) > 240 else "")
        shown.update(fresh)
        lines.append(line + ("; assertion as above" if len(fresh) < len(assertions) else ""))
    lines.extend(_unnamed_changes(run)[:40])
    limitations = [f"{label}: {count} cases unnamed" + (" (passing subtests print no name)" if runner == "unittest" else "")
                   for label, count in unnamed.items() if count]
    if (surface.get("runner") not in tdd_surface.NATIVE_RUNNERS and runner in tdd_surface.NATIVE_RUNNERS
            and any(not arm.get("cases") for arm in run["arms"] if arm["outcome"] in {"passed", "failed"})):
        limitations.append(f"the wrapped {runner} report names no case: pass `python3 -m {runner} ...` itself as the command; "
                           "the runner already runs it from the checkout root with the repository on PYTHONPATH, "
                           "this environment and verbose output")
    if narrowed := tdd_surface.narrowing(surface, run["arms"][0].get("loadedRoot") or "."):
        if len(narrowed) > 200:
            narrowed = f"{len(narrowed.split(', '))} selectors"
        limitations.append(f"narrowed selection: {narrowed}; tests outside it are not compared")
    if any(entry.get("execution") == "stopped" for by_tree in cases.values() for entry in by_tree.values()):
        limitations.append(STOPPED_NOTE)
    limitations.extend(f"sensitivity lost on {arm['requestedTree'][:12]}: a tree that failed before passes the revised probe"
                       for label, arm in zip(labels, run["arms"]) if label.startswith("earlier") and arm["outcome"] == "passed")
    return {**{key: run[key] for key in ("comparison", "valid", "fresh", "runIndex", "command") if key in run},
            "arms": [{"source": label, "tree": arm["requestedTree"], "outcome": arm["outcome"],
                      "testsExecuted": (arm.get("proof") or {}).get("testsExecuted"), "unnamed": unnamed[label],
                      **({"error": arm["error"][:500]} if arm.get("error") else {})}
                     for label, arm in zip(labels, run["arms"])],
            "cases": lines, "limitations": limitations}


def _unnamed_lines(arm: JsonObject, report: bool = False) -> list[str] | None:
    """An arm's unnamed printed lines. Beside a wrapped runner's `report`, every line outside that
    report counts, also on an arm whose runner failed: the report's outcome says nothing about the
    command's own output."""
    if arm.get("outcome") not in ({"passed", "failed"} if report else {"passed"}):
        return None
    text = str(arm.get("output", "")).replace(str(arm.get("loadedRoot")), "<source>")
    if report:
        return tdd_surface.outside_report(text)
    return [line for line in text.splitlines() if not tdd_surface.PRINTED_CASE.match(line)]


def _changed_lines(original: JsonObject, current: JsonObject, report: bool = False) -> list[str]:
    """An operation probe's unnamed printed lines that differ between the original and current arms."""
    before, after = _unnamed_lines(original, report), _unnamed_lines(current, report)
    if before is None or after is None:
        return []
    hunks = list(difflib.unified_diff(before, after, lineterm="", n=0))[2:]  # the two file headers
    return [f"{line[0]} {line[1:]}" for line in hunks if line[:1] in "+-"]


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
