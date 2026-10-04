"""Probe obligations, one readiness judgement and one compact comparison view shared by workflow consumers."""
from __future__ import annotations

import copy
import difflib
import re
import shlex

from . import tdd_surface

JsonObject = dict[str, object]
REQUIRED_FIELDS = frozenset({"id", "basis", "behavior", "seam", "expected"})
KINDS = frozenset({"contract", "preservation"})
READING_FIELDS = frozenset({"interpretations", "interpretation", "authority"})
# Judgement fields: a map update may change them without invalidating the executed
# comparison, which is re-judged; a changed obligation text requires a new comparison.
JUDGEMENT_FIELDS = READING_FIELDS | {"kind", "basis", "boundaryInputs", "released", "sourceRefs"}
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
UNITTEST_CASE = re.compile(r"^(\S+ \([^()\n]+\))((?: \[.*?\])?(?: \(.*\))?)$")


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
    readings = raw.get("interpretations")
    if "interpretations" in raw and (_strings(readings) is None or len(readings) < 2):
        errors.append(f"probe {identifier} interpretations requires at least two competing readings")
    settled = {key for key in ("interpretation", "authority") if key in raw}
    if settled and ("interpretations" not in raw or settled != {"interpretation", "authority"}
                    or not all(_text(raw[key]) for key in settled)):
        errors.append(f"probe {identifier} settles its readings with both interpretation and authority")
    if "released" in raw:
        released = raw["released"]
        if not isinstance(released, dict) or set(released) != {"reason", "case"} or not all(map(_text, released.values())):
            errors.append(f"probe {identifier} released requires a non-empty reason and the executed case it is bound to")
        elif raw.get("kind") != "preservation":
            errors.append(f"probe {identifier} only a preservation item can be released")
        elif settled != {"interpretation", "authority"} and "interpretations" in raw:
            errors.append(f"probe {identifier} cannot be released while its readings are unsettled")
        elif any(ref.get("type") == "finding" for ref in raw.get("sourceRefs") or [] if isinstance(ref, dict)):
            errors.append(f"probe {identifier} owns a finding and cannot be released")
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
    released = raw.get("released")
    if isinstance(released, dict) and set(released) == {"reason", "case"} and all(map(_text, released.values())):
        fields["released"] = {key: released[key].strip() for key in ("reason", "case")}
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


MARKDOWN_MARKER = re.compile(r"^\s*(?:#+|[-*+]|\d+[.)]|\|)\s*")


def _segments(intent: str) -> str:
    """The request as sentence segments: wrapped prose joins into one line, while a heading,
    list item, table row or blank line starts a new segment, so a bullet is quotable as written."""
    segments: list[str] = []
    for block in re.split(r"\n\s*\n", intent):
        for line in block.splitlines():
            stripped = MARKDOWN_MARKER.sub("", line)
            if segments and stripped == line.strip() and block.splitlines()[0] != line:
                segments[-1] += " " + stripped
            else:
                segments.append(stripped)
    return "\n".join(" ".join(segment.split()) for segment in segments if segment.strip())


def quoted(basis: str, intent: str) -> bool:
    """A contract basis recorded after preflight is one or more complete, consecutive sentences
    of the request, verbatim (whitespace-normalized): it starts where a sentence starts."""
    sentence = " ".join(basis.split())
    return bool(sentence and sentence[-1] in ".!?"
                and re.search(r"(?:^|[.!?] |\n)" + re.escape(sentence) + r"(?=[ \n]|$)", _segments(intent)))


def unverified(entry: JsonObject | None) -> bool:
    """The lead's own printed note that a case's measurement is unavailable; it never proves or releases."""
    return entry is not None and entry.get("outcome") == "printed" and "unverified" in str(entry.get("result", "")).lower()


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

def _judge(entry: JsonObject, owners: list[JsonObject]) -> list[str]:
    """Why the item's current comparison does not yet prove it; empty when proved. `owners` are
    the other items sharing the batch; a differing case none of them names stays this
    preservation item's to answer."""
    run = entry["comparison"]
    cases, _ = folded_cases(run)
    original_outcome, current_outcome = run["arms"][0]["outcome"], run["arms"][-1]["outcome"]
    reasons: list[str] = []
    attributable: dict[str, str] = {}
    contract = entry.get("kind") == "contract"
    if inputs := entry.get("boundaryInputs"):
        for name in inputs:
            matched = [case for case in cases if name in short_ids(case)]
            if not matched:
                reasons.append(f"{name} missing on original, current")
            attributable.update(dict.fromkeys(matched, name))
        claimed = {name for owner in [entry, *owners] for name in owner.get("boundaryInputs") or []}
        unclaimed = {case: case_label(case) for case, by_tree in cases.items()
                     if case not in attributable and not short_ids(case) & claimed
                     and _differs(by_tree.get("original"), by_tree.get("current"))}
        if contract:
            reasons.extend(f"{label} differs and no item names it: name it in a contract item quoting the request, or repair"
                           for label in unclaimed.values())
        else:
            attributable.update(unclaimed)
    elif owners:
        return [f"shares its batch with {', '.join(owner['id'] for owner in owners)}: name its cases in boundaryInputs"]
    elif contract and len(cases) > 1:
        return ["several cases executed: name the requested ones in boundaryInputs"]
    else:
        attributable = {name: case_label(name) for name in cases}
        if not contract and _runner(run) not in {"pytest", "unittest"} and _changed_lines(run["arms"][0], run["arms"][-1]):
            reasons.append("unnamed output differs between original and current: name the cases (`name: result` lines)")
    if not attributable and not reasons:
        return ["no attributable case executed: name the cases (test ids or printed `name: result` lines)"]
    differing = []
    for case, label in attributable.items():
        original, current = cases[case].get("original"), cases[case].get("current")
        if unverified(original) or unverified(current):
            reasons.append(f"{label} unverified: measurement unavailable is neither proof nor a release")
        elif contract:
            if not _passed(current, current_outcome):
                reasons.append(f"{label} {_state(current)} on current")
            if original is None or original.get("outcome") == "skipped":
                reasons.append(f"{label} {_state(original)} on original")
            elif _passed(current, current_outcome) and _differs(original, current):
                differing.append(label)
        elif not (_passed(original, original_outcome) and _passed(current, current_outcome)) or _differs(original, current):
            reasons.append(f"{label} differs (original={_state(original)}, current={_state(current)}): a preservation case "
                           "must pass on both trees - repair, or record an authorized contract change "
                           "(tdd-map kind contract quoting the request)")
    if contract and not differing and not reasons:
        reasons.append("no attributable case differs between original and current")
    return reasons


def release_binding(entry: JsonObject) -> str | None:
    """Why the item's release is not bound to its current comparison; None when it is. The bound
    case is the probe's printed note, present unchanged on both trees and not marked unverified."""
    released = entry.get("released")
    run = entry.get("comparison") or {}
    if not released or not run.get("arms"):
        return "no current comparison"
    cases, _ = folded_cases(run)
    bound = [name for name in cases if released["case"] in short_ids(name)]
    if not bound:
        return f"{released['case']} was not executed by the current comparison"
    original_outcome, current_outcome = run["arms"][0]["outcome"], run["arms"][-1]["outcome"]
    for name in bound:
        original, current = cases[name].get("original"), cases[name].get("current")
        if original is None or current is None:
            return f"{released['case']} was not printed on both trees"
        if original.get("outcome") != "printed" or current.get("outcome") != "printed":
            return f"{released['case']} is a test result, not the probe's printed note stating why the context is unreachable"
        if unverified(original) or unverified(current):
            return f"{released['case']} is unverified: unavailable measurement is not evidence of unreachability"
        if not (_passed(original, original_outcome) and _passed(current, current_outcome)) or _differs(original, current):
            return f"{released['case']} differs between original and current: a measured change is not unreachable"
    return None


def open_obligations(items: list[JsonObject]) -> list[str]:
    """The single readiness result: one question per item whose required proof is missing."""
    lines = []
    for entry in items:
        head = f"{entry['id']} ({entry.get('kind') or 'kind?'}): {entry['behavior']} => {entry['expected']}; "
        if entry.get("kind") not in KINDS:
            lines.append(head + "declare kind (contract or preservation) through record tdd-map")
        elif entry.get("boundaryInputs") == []:
            lines.append(head + "recorded boundary inputs are not executed case names: map them through record tdd-map")
        elif readings_unsettled(entry):
            lines.append(head + "readings unsettled: " + " | ".join(entry["interpretations"]) + " - record interpretation and authority")
        elif not producer_proved(entry):
            comparison = entry.get("comparison") or {}
            reason = ("comparison stale (production or probe changed): rerun it" if comparison.get("valid")
                      else "no current valid comparison")
            if comparison.get("arms") and entry.get("boundaryInputs"):
                cases, _ = folded_cases(comparison)
                reason += "; " + "; ".join(
                    f"{name} " + ", ".join(f"{_state(cases.get(case, {}).get(label))} on {label}" for label in ("original", "current"))
                    for name in entry["boundaryInputs"]
                    for case in [next((case for case in cases if name in short_ids(case)), None)])
            lines.append(head + reason)
        else:
            owners = [other for other in items if other is not entry
                      and (other.get("comparison") or {}).get("runIndex") == entry["comparison"].get("runIndex")]
            reasons = _judge(entry, owners)
            if entry.get("released"):
                # A bound release answers only its own context's absent inputs (`<context> | a`);
                # other contexts' inputs, and a differing or unverified case, stay its question.
                context, suffix = entry["released"]["case"], " missing on original, current"
                reasons = [reason for reason in reasons if not (reason.endswith(suffix)
                           and reason[:-len(suffix)].split(" | ")[:-1] == context.split(" | "))]
                if problem := release_binding(entry):
                    reasons.append(f"release unbound: {problem} - rerun, repair, or withdraw the release")
            if reasons:
                lines.append(head + "; ".join(reasons))
    return lines


def producer_proved(entry: JsonObject) -> bool:
    comparison = entry.get("comparison")
    return isinstance(comparison, dict) and comparison.get("valid") is True and comparison.get("fresh", True) is True


def unresolved(items: list[JsonObject]) -> list[str]:
    return [line.split(" ", 1)[0] for line in open_obligations(items)]


def never_compared(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if not (entry.get("comparison") or {}).get("arms")]


def released_lines(items: list[JsonObject]) -> list[str]:
    return [f"released: {entry['id']}: {entry['released']['reason']} ({entry['released']['case']})"
            for entry in items if entry.get("released")]


def contract_changes(items: list[JsonObject], recorded: list[JsonObject] | None) -> list[str]:
    """What moved since the recorded preflight that only the request, or a reviewer's finding, can
    authorize, each with the basis it carries: a preservation item turned contract, a dropped
    preservation case, a contract item or case added or re-worded after preflight."""
    before = {entry["id"]: entry for entry in recorded or []}
    lines = []
    for entry in items:
        prior = before.get(entry["id"])
        for change in authorization_needed(entry, prior):
            lines.append(f"{entry['id']}: {change}: {entry['basis']}")
    return lines


def authorization_needed(entry: JsonObject, prior: JsonObject | None, owned: set[tuple[str, str]] = frozenset()) -> list[str]:
    """The changes to `entry` since `prior` that the request must authorize. An attack added for a
    finding in `owned` (the pass's material behavioral findings) carries that finding instead, and
    is still listed with it."""
    findings = [str(ref["id"]) for ref in entry.get("sourceRefs", []) if ref.get("type") == "finding"]
    changes = []
    if entry.get("kind") == "contract":
        if prior is None:
            changes.append("contract item added after preflight" + (f" (finding {', '.join(findings)})" if findings else ""))
        else:
            if prior.get("kind") != "contract":
                changes.append(f"{prior.get('kind')} -> contract")
            elif any(prior.get(key) != entry.get(key) for key in ("behavior", "expected")):
                changes.append("contract expectation re-worded")
            if added := set(entry.get("boundaryInputs") or []) - set(prior.get("boundaryInputs") or []):
                changes.append("contract cases added: " + ", ".join(sorted(added)))
    elif prior is not None and (dropped := set(prior.get("boundaryInputs") or []) - set(entry.get("boundaryInputs") or [])):
        changes.append("preservation cases dropped: " + ", ".join(sorted(dropped)))
    if prior is None and any((str(ref["evidenceId"]), str(ref["id"])) in owned for ref in entry.get("sourceRefs", [])
                             if ref.get("type") == "finding"):
        return []  # the finding authorizes the attack; contract_changes still lists it
    return changes


# --- the compact comparison view -------------------------------------------------

def _runner(run: JsonObject) -> str | None:
    return tdd_surface.identify(shlex.split(run["command"])).get("runner") if run.get("command") else None


def comparison_view(run: JsonObject) -> JsonObject:
    """One rendering for the lead receipt and the review packet; everything else stays in evidence."""
    labels = _arm_labels(run)
    cases, unnamed = folded_cases(run)
    surface = tdd_surface.identify(shlex.split(run["command"])) if run.get("command") else {}
    runner = surface.get("runner")
    outcomes = dict(zip(labels, (arm["outcome"] for arm in run["arms"])))
    groups: dict[tuple[str, str], list[str]] = {}
    for name, by_tree in sorted(cases.items()):
        original, current = by_tree.get("original"), by_tree.get("current")
        if _passed(current, outcomes["current"]) and all(not _differs(original, by_tree.get(label)) for label in labels):
            continue
        pattern = ", ".join(f"{label}={_state(by_tree.get(label))}"
                            + (f" ({by_tree[label]['subtests']} subtests)" if by_tree.get(label, {}).get("subtests") else "")
                            for label in labels)
        assertions = tuple(dict.fromkeys(" ".join(part.split()) for label in labels
                                         for part in str(by_tree.get(label, {}).get("assertion") or "").split("\n") if part.strip()))
        groups.setdefault((pattern, assertions), []).append(name)
    lines = []
    for (pattern, assertions), names in groups.items():
        shown = ", ".join(names[:8]) + (f" +{len(names) - 8} more in evidence" if len(names) > 8 else "")
        line = (f"{len(names)} cases: " if len(names) > 1 else "") + f"{shown}: {pattern}"
        for assertion in assertions:
            line += "; " + assertion[:240] + ("... [full assertion in evidence]" if len(assertion) > 240 else "")
        lines.append(line)
    if runner not in {"pytest", "unittest"}:
        lines.extend(_changed_lines(run["arms"][0], run["arms"][-1]))
    limitations = [f"{label}: {count} cases unnamed" + (" (passing subtests print no name)" if runner == "unittest" else "")
                   for label, count in unnamed.items() if count]
    if narrowed := tdd_surface.narrowing(surface):
        if len(narrowed) > 200:
            narrowed = f"{len(narrowed.split(', '))} selectors"
        limitations.append(f"narrowed selection: {narrowed}; tests outside it are not compared")
    limitations.extend(f"sensitivity lost on {arm['requestedTree'][:12]}: a tree that failed before passes the revised probe"
                       for label, arm in zip(labels, run["arms"]) if label.startswith("earlier") and arm["outcome"] == "passed")
    return {**{key: run[key] for key in ("comparison", "valid", "fresh", "runIndex", "command") if key in run},
            "arms": [{"source": label, "tree": arm["requestedTree"], "outcome": arm["outcome"],
                      "testsExecuted": (arm.get("proof") or {}).get("testsExecuted"), "unnamed": unnamed[label],
                      **({"error": arm["error"][:500]} if arm.get("error") else {})}
                     for label, arm in zip(labels, run["arms"])],
            "cases": lines, "limitations": limitations}


def _changed_lines(original: JsonObject, current: JsonObject) -> list[str]:
    """An operation probe's unnamed printed lines that differ between the original and current arms."""
    def lines(arm: JsonObject) -> list[str] | None:
        if arm.get("outcome") != "passed":
            return None
        return [line for line in str(arm.get("output", "")).replace(str(arm.get("loadedRoot")), "<source>").splitlines()
                if not tdd_surface.PRINTED_CASE.match(line)]
    before, after = lines(original), lines(current)
    if before is None or after is None:
        return []
    hunks = list(difflib.unified_diff(before, after, lineterm="", n=0))[2:]  # the two file headers
    return [f"{line[0]} {line[1:]}" for line in hunks if line[:1] in "+-"][:40]


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
