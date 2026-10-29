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
KINDS = frozenset({"conformance", "contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Optional map fields; witness and reading updates re-judge retained proof.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs"}
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


def _field_errors(raw: JsonObject, identifier: str) -> list[str]:
    errors = [f"probe {identifier} kind must be conformance, contract or preservation"] if "kind" in raw and not (
        isinstance(raw["kind"], str) and raw["kind"] in KINDS) else []
    if "boundaryInputs" in raw and _strings(raw["boundaryInputs"]) is None:
        errors.append(f"probe {identifier} boundaryInputs must name executed cases as non-empty strings")
    elif described := [name for name in raw.get("boundaryInputs") or [] if ": " in re.sub(r"\[.*\]", "", name)]:
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
                errors.append(f"probe {identifier} requires kind: conformance, contract or preservation")
            if extra := set(raw) - REQUIRED_FIELDS - JUDGEMENT_FIELDS:
                errors.append(f"probe {identifier} has unknown fields: " + ", ".join(sorted(extra)))
            errors.extend(_field_errors(raw, identifier))
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
    """A comparison runs the original and the current candidate; a legacy run's middle arms are numbered."""
    return ["original", *(f"earlier {index}" for index in range(1, len(run["arms"]) - 1)), "current"]


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


def _ran(case: str, test: str) -> bool:
    """Whether a runner-reported case is the traced test: pytest's node id, or the id unittest reports."""
    match = UNITTEST_CASE.match(case)
    return (match.group(1).split(" (", 1)[1][:-1] if match else case) == test


def _judge(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]]) -> list[str]:
    """Why the item's latest comparison, judged against the current map, does not prove it; none when
    it does. A case another item names is that item's to judge; a differing case no item names stays
    this preservation item's."""
    run = entry["comparison"]
    original_outcome, current_outcome = run["arms"][0]["outcome"], run["arms"][-1]["outcome"]
    reasons: list[str] = []
    found: list[str] = []
    attributable: dict[str, str] = {}
    contract = entry.get("kind") in {"conformance", "contract"}
    conformance = entry.get("kind") == "conformance"
    claimed = _claimed(entry, items, cases)
    # a sibling that has not run defers the cases its names select here: not this item's failure
    deferred = {case for other in items if other is not entry and not (other.get("comparison") or {}).get("arms")
                for name in other.get("boundaryInputs") or [] for case in _matched(name, cases)[0]} - claimed

    if inputs := entry.get("boundaryInputs"):
        for name in inputs:
            matched, ambiguity = _matched(name, cases)
            if ambiguity or not matched:
                reasons.append(ambiguity or f"{name} missing on original, current")
            attributable.update(dict.fromkeys(matched, name))
        unclaimed = {case: case_label(case) for case, by_tree in cases.items()
                     if case not in attributable and case not in claimed | deferred and not short_ids(case) & set(inputs)
                     and not (conformance and by_tree.get("current") is None
                              and (by_tree.get("original") or {}).get("execution") == "unavailable")
                     and _differs(by_tree.get("original"), by_tree.get("current"))}
        if contract:
            found.extend(f"{label} differs and no item names it" for label in unclaimed.values())
        else:
            attributable.update(unclaimed)
    elif owners := _owners(entry, items):
        return [f"shares its batch with {', '.join(owner['id'] for owner in owners)}: name its cases in boundaryInputs"]
    elif contract and len(cases) > 1:
        return ["several cases executed: name the requested ones in boundaryInputs"]
    else:
        requested = _claimed(entry, items, cases, lambda other: other.get("kind") in {"conformance", "contract"})
        attributable = {name: case_label(name) for name in cases if name not in requested | deferred}
    if not contract and _unnamed_changes(run):
        found.append("unnamed output differs between original and current: name the cases "
                     "(printed `name: result` lines, in a command apart from any test runner's report)")
    if not attributable and not reasons and not found:
        return ["no attributable case executed: name the cases (test ids or printed `name: result` lines)"]
    differing = []
    for case, label in attributable.items():
        original, current = cases[case].get("original"), cases[case].get("current")
        if unverified(current) or unverified(original):
            reasons.append(f"{label} unverified: measurement unavailable is not proof")
        elif contract:
            if not _passed(current, current_outcome):
                found.append(f"{label} {_state(current)} on current")
            if conformance and _passed(original, original_outcome):
                found.append(f"{label} already passed on original: use preservation")
            elif (original is None or original.get("outcome") in {"skipped", "error"}) and not (
                    conformance and (original is None or original.get("outcome") == "error" and original.get("execution") == "unavailable")):
                reasons.append(f"{label} {_state(original)} on original")
            elif not conformance and _passed(current, current_outcome) and _differs(original, current):
                differing.append(label)
        elif not (_passed(original, original_outcome) and _passed(current, current_outcome)) or _differs(original, current):
            found.append(f"{label} differs (original={_state(original)}, current={_state(current)})")
    if entry.get("kind") == "contract" and not differing and not reasons and not found:
        found.append("no attributable case differs between original and current")
    if found:
        found.append(("conformance needs original failure or inability and current success" if conformance
                      else "name every differing case in its owning item and show the requested change on it" if contract
                      else "preservation cases must pass unchanged on both trees")
                     + ": repair the code or the probe")
    return reasons + found


def _owners(entry: JsonObject, items: list[JsonObject]) -> list[JsonObject]:
    index = (entry.get("comparison") or {}).get("runIndex")
    return [other for other in items if other is not entry and (other.get("comparison") or {}).get("runIndex") == index]


def _claimed(entry: JsonObject, items: list[JsonObject], cases: dict[str, dict[str, JsonObject]],
             keep: Callable[[JsonObject], bool] = lambda other: True) -> set[str]:
    """The exact cases other items' names select, each in that item's own executed comparison; an item
    that has not run claims nothing, and a short id shared by another test claims nothing here."""
    return {case for other in items if other is not entry and keep(other) and (other.get("comparison") or {}).get("arms")
            for name in other.get("boundaryInputs") or [] for case in _matched(name, folded_cases(other["comparison"])[0])[0]}


def open_obligations(items: list[JsonObject]) -> list[str]:
    """The single readiness result: one question per item whose required proof is missing.
    Items sharing one comparison fold its cases once. The current comparisons then answer for the
    existing test ids the candidate renames and the conditions it removes."""
    lines: list[str] = []
    folded: dict[object, dict[str, dict[str, JsonObject]]] = {}
    for entry in items:
        head = f"{entry['id']} ({entry.get('kind') or 'kind?'}): "
        comparison = entry.get("comparison") or {}
        if comparison.get("arms") and comparison.get("runIndex") not in folded:
            folded[comparison.get("runIndex")] = folded_cases(comparison)[0]
        cases = folded.get(comparison.get("runIndex"), {}) if comparison.get("arms") else {}
        if entry.get("kind") not in KINDS:
            reason = "declare kind (conformance, contract or preservation) through record tdd-map"
        elif entry.get("boundaryInputs") == []:
            reason = "recorded boundary inputs are not executed case names: map them through record tdd-map"
        elif readings_unsettled(entry):
            reason = "readings unsettled: " + " | ".join(entry["interpretations"]) + " - record interpretation and authority"
        elif not producer_proved(entry):
            reason = ("comparison stale (production, probe or obligation changed): rerun it" if comparison.get("valid")
                      else "no current valid comparison")
            if cases and entry.get("boundaryInputs"):
                reason += "; " + "; ".join(
                    f"{name} " + ", ".join(f"{_state(cases.get(case, {}).get(label))} on {label}" for label in ("original", "current"))
                    for name in entry["boundaryInputs"]
                    for case in [next(iter(_matched(name, cases)[0]), None)])
        elif reasons := _judge(entry, items, cases):
            reason = "; ".join(reasons)
        else:
            continue
        # The item's own text stays in the map; the line keeps every case it names.
        unnamed = [str(name) for name in entry.get("boundaryInputs") or []
                   if not re.search(rf"(?:^|[\s,;(]){re.escape(str(name))}(?=$|[\s,;)])", reason)]
        lines.append(head + reason + ("; its cases: " + ", ".join(unnamed) if unnamed else ""))
    current = [entry["comparison"] for entry in items if producer_proved(entry)]
    expected = [str(entry.get("expected", "")) for entry in items if entry.get("kind") == "contract"]
    lines += [f"existing test `{case}` is missing on the candidate: restore it, or name it in the expected result "
              "of a contract item whose request removes it"
              for case in dict.fromkeys(case for run in current for case in run.get("renamed") or [])
              if not any(re.search(rf"(?<![^\s`'\"(,;]){re.escape(case)}(?![^\s`'\"),;.]|\.\S)", text) for text in expected)]
    decided: dict[str, set[bool | None]] = {}
    for run in current:
        for condition in run["arms"][0].get("conditions", []):
            decided.setdefault(condition["label"], set()).add(condition["decided"])
    # A removal a contract item names closes on one of its own cases whose result changed and whose test
    # evaluated the condition at its deciding value on the original, not shown to keep the decision.
    claimed = set()
    for entry in items:
        if entry.get("kind") == "contract" and producer_proved(entry):
            cases = folded[entry["comparison"].get("runIndex")]
            own = {case for name in entry.get("boundaryInputs") or [] for case in _matched(name, cases)[0]
                   if _state(cases[case].get("original")) != _state(cases[case].get("current"))}
            for condition in entry["comparison"]["arms"][0].get("conditions", []):
                if condition["label"] in str(entry.get("expected", "")) and any(
                        possible and _ran(case, test) for test, possible in (condition.get("reached") or {}).items() for case in own):
                    claimed.add(condition["label"])
    return lines + [f"removed condition {label} never decided a compared case"
                    for label, seen in decided.items() if False in seen and True not in seen and label not in claimed]


def obligation(entry: JsonObject) -> list[object]:
    return [entry.get(key) for key in ("kind", "behavior", "seam", "expected")]


def producer_proved(entry: JsonObject) -> bool:
    comparison = entry.get("comparison")
    return (isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True
            and isinstance(recorded := comparison.get("obligation", obligation(entry)), list)
            and len(recorded) in (4, 9) and recorded[:4] == obligation(entry))


def unresolved(items: list[JsonObject]) -> list[str]:
    ids = {str(entry["id"]) for entry in items}
    return [head if (head := line.split(" ", 1)[0]) in ids else line for line in open_obligations(items)]


def never_compared(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not (entry.get("comparison") or {}).get("arms")]


def contract_changes(items: list[JsonObject], recorded: list[JsonObject] | None) -> list[str]:
    """What moved since the recorded preflight, each with the basis it carries, for the
    reviewer and final advisor to judge against the request: a changed kind, a
    required item added or re-worded, required cases added and any case dropped. Listing authorizes
    nothing and never clears an open item."""
    before = {entry["id"]: entry for entry in recorded or []}
    lines = []
    for entry in items:
        prior, kind = before.get(entry["id"]), entry.get("kind")
        contract = kind in {"contract", "conformance"}
        changes = []
        if prior is None:
            if contract:
                changes.append(f"{kind} item added after preflight")
        else:
            inputs, earlier = set(entry.get("boundaryInputs") or []), set(prior.get("boundaryInputs") or [])
            if prior.get("kind") != kind:
                changes.append(f"{prior.get('kind')} -> {kind}")
            elif contract and any(prior.get(key) != entry.get(key) for key in ("behavior", "expected")):
                changes.append(f"{kind} expectation re-worded")
            if contract and inputs - earlier:
                changes.append(f"{kind} cases added: " + ", ".join(sorted(inputs - earlier)))
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
    groups: dict[tuple[bool, tuple], list[str]] = {}
    for name, by_tree in sorted(cases.items()):
        original, current = by_tree.get("original"), by_tree.get("current")
        if _passed(current, outcomes["current"]) and all(not _differs(original, by_tree.get(label)) for label in labels):
            continue
        slots: dict[tuple[str, ...], dict[str, list[str]]] = {}  # assertions -> state -> the sources showing them
        for label in labels:
            entry = by_tree.get(label) or {}
            state = (_state(by_tree.get(label)) + (" (stopped)" if entry.get("execution") == "stopped" else "")
                     + (f" ({entry['subtests']} subtests)" if entry.get("subtests") else ""))
            parts = tuple(dict.fromkeys(" ".join(part.split()) for part in str(entry.get("assertion") or "").split("\n") if part.strip()))
            slots.setdefault(parts, {}).setdefault(state, []).append(label)
        key = tuple((", ".join(f"{'+'.join(sources)}={state}" for state, sources in states.items()), parts) for parts, states in slots.items())
        groups.setdefault((_passed(current, outcomes["current"]), key), []).append(name)
    lines: list[str] = []
    shown: set[str] = set()
    for (_, key), names in sorted(groups.items(), key=lambda group: group[0][0]):  # current blockers first
        listed = ", ".join(names[:8]) + (f" +{len(names) - 8} more in evidence" if len(names) > 8 else "")
        rendered = []
        for sources, parts in key:
            fresh = [part for part in parts if part not in shown]
            shown.update(fresh)
            rendered.append(sources + "".join(  # a part already printed reads `as above`; the state already says it failed
                ("; " if index else ": ") + part[:240].removeprefix("AssertionError: ") + ("... [full assertion in evidence]" if len(part) > 240 else "")
                for index, part in enumerate(fresh + (["as above"] if len(fresh) < len(parts) else []))))
        lines.append((f"{len(names)} cases: " if len(names) > 1 else "") + f"{listed}: " + "; ".join(rendered))
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
    if unmeasured := [condition["label"] for condition in run["arms"][0].get("conditions", []) if condition["decided"] is None]:
        limitations.append("removed conditions not measured: " + ", ".join(unmeasured[:8])
                           + (f" +{len(unmeasured) - 8} more in evidence" if len(unmeasured) > 8 else ""))
    if run.get("renamed", []) is None:
        limitations.append("existing test ids not compared: pytest collection failed")
    return {**{key: run[key] for key in ("comparison", "valid", "fresh", "runIndex", "command") if key in run},
            "arms": [{"source": label, "tree": arm["requestedTree"], "outcome": arm["outcome"],
                      "testsExecuted": (arm.get("proof") or {}).get("testsExecuted"), "unnamed": unnamed[label],
                      **({"error": arm["error"][:500]} if arm.get("error") else {})}
                     for label, arm in zip(labels, run["arms"])],
            "cases": lines, "limitations": limitations,
            **({"added": [f"{selector}, which names changed `{function}`" for selector, function in run["added"].items()]}
               if run.get("added") else {})}


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
