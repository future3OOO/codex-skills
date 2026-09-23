"""The recorded Behavior Map contract shared by preflight and TDD."""
from __future__ import annotations

import copy
import json
import re
from typing import Iterable

JsonObject = dict[str, object]
INITIAL_STATUSES = frozenset({"pending", "already-satisfied", "omitted"})
# Map proof is GREEN through its own RED; retired statuses are not admitted.
PROOF_STATUSES = frozenset({"green"})
RUNTIME_STATUSES = INITIAL_STATUSES | PROOF_STATUSES | {"red", "superseded", "withdrawn"}
DISPOSITION_STATUSES = frozenset({"already-satisfied", "omitted"})
EVIDENCED_STATUSES = DISPOSITION_STATUSES | {"superseded", "withdrawn"}
NEVER_GREEN = DISPOSITION_STATUSES | {"withdrawn"}
KINDS = frozenset({"contract", "preservation"})
REQUIRED_FIELDS = frozenset({
    "id", "kind", "behavior", "seam", "expected", "redFailure", "status",
})
OPTIONAL_FIELDS = frozenset({
    "evidence", "supersededBy", "sourceRefs", "proofCommand", "baselineProof", "supersededFrom",
    "redCommand", "redProof", "revalidationRequired",
    "boundaryInputs", "interpretations", "interpretation", "authority",
    "proofBinding",
})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
# The producer stamps a baseline's command in `evidence`; supersession moves it
# into the existing baseline proof before replacing the authored explanation.
BASELINE_STAMP = "baseline-passed: "


def executed_commands(entry: JsonObject) -> dict[str, str]:
    """The commands this item actually ran, by the phase that recorded each.

    An authored document may carry `proofCommand` and `evidence`, so each is read
    only beside the producer's own mark for that phase: `baselineProof` for the
    baseline command (stamped evidence or retained proof), and a GREEN the producer recorded
    for `proofCommand`. `redCommand` the loader already refuses when authored.
    """
    evidence = entry.get("evidence")
    proof = entry.get("baselineProof")
    baseline = proof.get("command") if isinstance(proof, dict) else None
    if not baseline and isinstance(proof, dict) and isinstance(evidence, str) and evidence.startswith(BASELINE_STAMP):
        baseline = evidence[len(BASELINE_STAMP):].strip()
    recorded = {
        "red": entry.get("redCommand"),
        "green": entry.get("proofCommand") if green_through_red(entry) else None,
        "baseline": baseline,
    }
    return {
        phase: str(command).strip()
        for phase, command in recorded.items()
        if isinstance(command, str) and command.strip()
    }
_CONTRACT_DISPOSITION_REFUSED = (
    "behavior {} is a contract item: it is never omitted, and already-satisfied "
    "is recorded only by tdd --phase red passing its mapped surface"
)
_PRESERVATION_WITHDRAWN_REFUSED = "behavior {} is a preservation item: use omitted, not withdrawn"
_BASELINE_PROOF_RESERVED = (
    "behavior {} baselineProof is recorded only by tdd --phase red passing its mapped surface"
)
# Infra-failure phrases, matched on word boundaries: a phrase is refused when
# its words appear as an adjacent run in the marker, or its collapsed form is
# itself one of the marker's words (AttributeError). Substring matching over
# the collapsed marker was a demonstrated false-positive class - a product
# marker like USERNAME_ERROR_VISIBLE must not trip "name error".
GENERIC_RED_PHRASES = (
    "attribute error",
    "import error",
    "module not found error",
    "name error",
    "syntax error",
    "indentation error",
    "missing api",
    "api missing",
    "missing method",
    "missing function",
    "missing module",
    "no tests ran",
    "zero tests ran",
    "0 tests ran",
    "ran 0 tests",
    "no tests collected",
    "zero tests collected",
    "setup failed",
    "setup error",
    "collection failed",
    "collection error",
    "error collecting",
    "error during collection",
    "errors during collection",
    "error at setup",
    "collected 0 items",
    "fixture not found",
    "missing fixture",
)


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _names_generic_failure(marker: str) -> bool:
    words = _words(marker)
    for phrase in GENERIC_RED_PHRASES:
        parts = phrase.split()
        if "".join(parts) in words:
            return True
        if any(
            words[i : i + len(parts)] == parts
            for i in range(len(words) - len(parts) + 1)
        ):
            return True
    return False


def _validate_red_failure(value: object, identifier: str) -> list[str]:
    marker = _text(value)
    if marker is None:
        return [f"behavior {identifier} requires redFailure"]
    return [f"behavior {identifier} redFailure must name the product behavior, "
            "not a missing API, import, fixture, syntax, collection, setup, or no-test failure"
            ] if _names_generic_failure(marker) else []


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


def interpretation_errors(raw: JsonObject, identifier: str) -> list[str]:
    """Validate a material choice without interpreting its application semantics."""
    if not {"boundaryInputs", "interpretations", "interpretation", "authority"} & raw.keys():
        return []
    inputs, readings = raw.get("boundaryInputs"), raw.get("interpretations")
    errors = [] if isinstance(inputs, list) and inputs else [f"behavior {identifier} requires non-empty boundaryInputs"]
    try:
        json.dumps(inputs, allow_nan=False)
    except (TypeError, ValueError):
        errors += [f"behavior {identifier} boundaryInputs must be concrete JSON values"] if not errors else []
    if not isinstance(readings, list) or any(_text(value) is None for value in readings) or len({value.strip() for value in readings}) < 2:
        errors.append(f"behavior {identifier} interpretations requires competing readings")
    chosen = "interpretation" in raw or "authority" in raw
    return errors + [error for key in ("interpretation", "authority") if chosen for error in _required(raw, key, identifier)]


def interpretation_fields(raw: JsonObject) -> JsonObject:
    fields = {key: raw[key] for key in ("boundaryInputs", "interpretations", "interpretation", "authority") if key in raw}
    for key in {"interpretation", "authority"} & fields.keys():
        fields[key] = _text(fields[key])
    return copy.deepcopy(fields)


def interpretation_pending(entry: JsonObject) -> bool:
    return bool(entry.get("interpretations")) and not (entry.get("interpretation") and entry.get("authority"))


def map_errors(value: object, *, allow_runtime: bool, existing: Iterable[JsonObject] = ()) -> list[str]:
    """Every violation of one Behavior Map item list; the whole-map rules read it once its items are valid.

    `existing` holds the recorded items a new batch joins, so map-level rules read the whole map.
    """
    if not isinstance(value, list) or not value:
        return ["behaviorMap must be a non-empty array"]
    existing = list(existing)
    seen = {str(entry["id"]) for entry in existing}
    errors = [error for position, raw in enumerate(value, 1)
              for error in _item_errors(raw, position, seen, allow_runtime=allow_runtime)]
    if errors:
        return errors
    whole = [*existing, *(_item(raw, allow_runtime=allow_runtime) for raw in value)]
    try:
        terminal_items(whole)
    except ValueError as exc:
        errors.append(str(exc))
    uncontracted = not allow_runtime and any(entry["status"] == "pending" for entry in whole) and not any(
        entry.get("kind") == "contract" for entry in whole)
    return errors + (["a map with a pending item must carry at least one contract item; "
                      "a no-change pass maps only dispositioned preservation items"] if uncontracted else [])


def validate_items(
    value: object,
    *,
    allow_runtime: bool,
    existing: Iterable[JsonObject] = (),
    terminals: dict[str, JsonObject] | None = None,
) -> list[JsonObject]:
    """Validate and return one canonical Behavior Map item list.

    Every violation is named in one refusal. Recorded maps may still carry the
    retired `basis`; it is dropped on load.
    """
    existing = list(existing)
    errors = map_errors(value, allow_runtime=allow_runtime, existing=existing)
    if errors:
        raise ValueError("; ".join(errors))
    result = [_item(raw, allow_runtime=allow_runtime) for raw in value]
    if terminals is not None:
        terminals.update(terminal_items([*existing, *result]))
    return result


def _item_errors(raw: object, position: int, seen: set[str], *, allow_runtime: bool) -> list[str]:
    statuses = RUNTIME_STATUSES if allow_runtime else INITIAL_STATUSES
    if not isinstance(raw, dict):
        return [f"behaviorMap item {position} must be an object"]
    raw = {key: value for key, value in raw.items() if key != "basis"}
    # Maps recorded before `kind` existed still load; their items carry no
    # contract authority. New items always declare a kind.
    missing = sorted(REQUIRED_FIELDS - set(raw) - ({"kind"} if allow_runtime else {"status"}))
    unknown = sorted(set(raw) - REQUIRED_FIELDS - OPTIONAL_FIELDS)
    identifier = _text(raw.get("id"))
    kind = _text(raw.get("kind"))
    status = _text(raw.get("status", "pending" if not allow_runtime else None))
    valid_id = bool(identifier and IDENTIFIER.fullmatch(identifier))
    label = f"item {position} ({identifier})" if valid_id else f"item {position}"
    # Every structural violation of one item is named together.
    errors = [f"behaviorMap {label} {problem}" for problem, bad in (
        (f"is missing fields: {', '.join(missing)}", missing),
        (f"has unknown fields: {', '.join(unknown)}", unknown),
        ("behavior ids must be 2-64 characters: uppercase letters, digits, _ or -", not valid_id),
        ("behavior id is duplicated", identifier in seen),
        (f"kind must be one of: {', '.join(sorted(KINDS))}", kind not in KINDS and not (kind is None and allow_runtime)),
        (f"status must be one of: {', '.join(sorted(statuses))}", status not in statuses),
        # Recorded evidence carries contract already-satisfied only from the
        # producer (tdd --phase red), so the loading path admits it.
        (_CONTRACT_DISPOSITION_REFUSED.format(label), kind == "contract" and (
            status == "omitted" or (status == "already-satisfied" and not allow_runtime))),
        (_PRESERVATION_WITHDRAWN_REFUSED.format(label), status == "withdrawn" and kind != "contract"),
    ) if bad]
    if identifier is not None:
        seen.add(identifier)
    errors += [*_required(raw, "behavior", label), *_required(raw, "seam", label), *_required(raw, "expected", label),
               *_validate_red_failure(raw.get("redFailure"), label), *_source_refs(raw.get("sourceRefs"), label),
               *interpretation_errors(raw, label)]
    producer = f"behavior {label} {{}} is producer-owned state"
    red_proof = "redCommand" in raw or "redProof" in raw
    errors += [message for message, bad in (
        (f"behavior {label} evidence must be text", "evidence" in raw and not isinstance(raw["evidence"], str)),
        (f"behavior {label} redCommand and redProof are recorded only by tdd --phase red",
         red_proof and (not allow_runtime or not isinstance(raw.get("redProof"), dict))),
        (producer.format("revalidationRequired"),
         "revalidationRequired" in raw and (not allow_runtime or raw["revalidationRequired"] is not True)),
        (producer.format("proofBinding"),
         "proofBinding" in raw and (not allow_runtime or not isinstance(raw["proofBinding"], dict))),
        (_BASELINE_PROOF_RESERVED.format(label),
         "baselineProof" in raw and (not allow_runtime or not isinstance(raw["baselineProof"], dict))),
        # Supersession keeps the proof kind it retired, so a post-edit pass
        # cannot be laundered into a GREEN through RED by being superseded.
        (f"behavior {label} supersededFrom is recorded only by a tdd-map supersession", "supersededFrom" in raw and (
            not allow_runtime or raw["supersededFrom"] not in (*PROOF_STATUSES, "already-satisfied", "pending"))),
        (f"behavior {label} status {status} cannot carry disposition evidence",
         "evidence" in raw and status not in EVIDENCED_STATUSES),
        (f"behavior {label} status {status} cannot carry supersededBy", "supersededBy" in raw and status != "superseded"),
    ) if bad]
    for field, needed in (("proofCommand", "proofCommand" in raw), ("supersededBy", status == "superseded"),
                          ("redCommand", red_proof and ("redCommand" in raw or (
                              raw.get("supersededFrom", status) != "pending" and "baselineProof" not in raw)))):
        errors += _required(raw, field, label) if needed else []
    return errors


def _item(raw: JsonObject, *, allow_runtime: bool) -> JsonObject:
    """The canonical form of an item `_item_errors` accepted."""
    kind, status = _text(raw.get("kind")), _text(raw.get("status", "pending" if not allow_runtime else None))
    evidence = _text(raw.get("evidence"))
    # The runner stamps the exact proving command at GREEN and the RED surface
    # stays on the item, so GREEN proves the item against its own RED.
    return {
        "id": _text(raw["id"]),
        **({"kind": kind} if kind is not None else {}),
        **{name: _text(raw[name]) for name in ("behavior", "seam", "expected", "redFailure")},
        "status": status,
        **({"sourceRefs": _refs(raw["sourceRefs"])} if raw.get("sourceRefs") is not None else {}),
        **interpretation_fields(raw),
        **{name: _text(raw[name]) for name in ("proofCommand", "redCommand") if name in raw},
        **({"redProof": raw["redProof"]} if "redCommand" in raw or "redProof" in raw else {}),
        **({"revalidationRequired": True} if "revalidationRequired" in raw else {}),
        **{name: raw[name] for name in ("proofBinding", "baselineProof", "supersededFrom") if name in raw},
        **({"evidence": evidence} if evidence is not None and status in EVIDENCED_STATUSES else {}),
        **({"supersededBy": _text(raw["supersededBy"])} if "supersededBy" in raw else {}),
    }


def _required(raw: dict[str, object], field: str, identifier: str) -> list[str]:
    return [] if _text(raw.get(field)) else [f"behavior {identifier} requires {field}"]


def initial_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False)


def runtime_items(value: object, *, terminals: dict[str, JsonObject] | None = None) -> list[JsonObject]:
    return validate_items(value, allow_runtime=True, terminals=terminals)


def clone(items: list[JsonObject]) -> list[JsonObject]:
    return copy.deepcopy(items)


def item(items: list[JsonObject], identifier: str) -> JsonObject:
    try:
        return next(entry for entry in items if entry.get("id") == identifier)
    except StopIteration as exc:
        raise ValueError(f"behavior id is not in the recorded map: {identifier}") from exc


def terminal_items(items: list[JsonObject]) -> dict[str, JsonObject]:
    """Resolve the whole replacement graph once, including shared suffixes.

    The result belongs to this map evaluation only; no evidence survives here.
    """
    by_id = {str(entry["id"]): entry for entry in items}
    resolved: dict[str, JsonObject] = {}
    for origin in items:
        entry = origin
        path: set[str] = set()
        while str(entry["id"]) not in resolved:
            identifier = str(entry["id"])
            if identifier in path:
                raise ValueError(f"behavior {identifier} supersededBy must name another item without forming a cycle")
            path.add(identifier)
            if entry.get("status") != "superseded":
                break
            target = str(entry.get("supersededBy"))
            if target not in by_id:
                raise ValueError(f"behavior id is not in the recorded map: {target}")
            entry = by_id[target]
        terminal = resolved.get(str(entry["id"]), entry)
        if origin.get("status") == "superseded" and origin.get("boundaryInputs"):
            required = {json.dumps(value, sort_keys=True) for value in origin["boundaryInputs"]}
            carried = {json.dumps(value, sort_keys=True) for value in terminal.get("boundaryInputs", [])}
            if not required <= carried or (interpretation_pending(origin) and not
                    set(map(_text, origin["interpretations"])) <= set(map(_text, terminal.get("interpretations", [])))):
                raise ValueError(f"behavior {origin['id']} supersession must retain boundaryInputs and unsettled readings")
        if origin.get("status") == "superseded" and terminal.get("status") in NEVER_GREEN:
            raise ValueError(f"behavior {terminal['id']} is {terminal['status']} and can never be GREEN; "
                             "it cannot replace a superseded item")
        resolved.update((identifier, terminal) for identifier in path)
    return resolved


def apply_dispositions(
    items: list[JsonObject],
    value: object,
    *,
    settled_findings: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Add ownership or reassess existing obligations, never manufacture proof.

    References only grow. Reopening removes present settlement authority, while
    immutable evidence documents retain the old proof. Rejected/report-only
    findings retain the existing unowned-contract withdrawal rule.
    """
    if not isinstance(value, list):
        raise ValueError("TDD map dispositions must be an array")
    seen: set[str] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"TDD map disposition {position} must be an object")
        metadata = {"boundaryInputs", "interpretations", "interpretation", "authority"}
        unknown = sorted(set(raw) - {"id", "status", "evidence", "supersededBy", "sourceRefs", "revalidate"} - metadata)
        identifier, status, evidence = _text(raw.get("id")), _text(raw.get("status")), _text(raw.get("evidence"))
        revalidate = "revalidate" in raw
        settles = revalidate or "status" in raw
        # Every shape violation of this disposition is named together, before the map is touched.
        problems = [problem for problem, bad in (
            (f"TDD map disposition {position} has unknown fields: {', '.join(unknown)}", unknown),
            ("TDD map dispositions require unique behavior ids", identifier is None or identifier in seen),
            ("revalidate must be true and is mutually exclusive with status",
             revalidate and (raw["revalidate"] is not True or "status" in raw)),
            (f"behavior {identifier} disposition requires status, revalidate or sourceRefs", not settles and (
                set(raw) - {"id", "sourceRefs", "evidence", *unknown} - metadata
                or not ({"sourceRefs"} | metadata) & raw.keys())),
            (f"behavior {identifier} disposition requires evidence", evidence is None and (revalidate or status == "pending")),
            (f"behavior {identifier} disposition {status} cannot carry supersededBy",
             settles and "supersededBy" in raw and status != "superseded"),
            (f"behavior {identifier} disposition must be one of: " + ", ".join(sorted(EVIDENCED_STATUSES | {"pending"})),
             "status" in raw and not revalidate and status not in (*EVIDENCED_STATUSES, "pending")),
            (f"behavior {identifier} sourceRefs must be an array", "sourceRefs" in raw and raw["sourceRefs"] is None),
        ) if bad]
        mapped = next((entry for entry in items if identifier is not None and entry.get("id") == identifier), None)
        if identifier is not None:
            seen.add(identifier)
            problems += [] if mapped else [f"behavior id is not in the recorded map: {identifier}"]
        problems += _source_refs(raw.get("sourceRefs"), identifier)
        proposal = {**mapped, **{key: raw[key] for key in metadata & raw.keys()}} if mapped and metadata & raw.keys() else {}
        if proposal:
            if isinstance(raw.get("interpretations"), list) and "interpretation" not in raw and (
                    list(map(_text, raw["interpretations"])) != list(map(_text, mapped.get("interpretations", [])))):
                for key in {"interpretation", "authority"} - raw.keys():
                    proposal.pop(key, None)
            inputs = proposal.get("boundaryInputs")  # removal reads only the inputs' own shape
            removed = isinstance(inputs, list) and inputs and bool({json.dumps(value, sort_keys=True) for value in mapped.get(
                "boundaryInputs", [])} - {json.dumps(value, sort_keys=True) for value in inputs})
            problems += interpretation_errors(proposal, str(identifier)) + (
                [f"behavior {identifier} removing boundaryInputs requires governing evidence"]
                if removed and not _text(raw.get("evidence")) else [])
        if problems:
            raise ValueError("; ".join(problems))
        if proposal:
            for key in metadata:
                mapped.pop(key, None)
            mapped.update(interpretation_fields(proposal))
        if "sourceRefs" in raw:
            refs = _refs(raw["sourceRefs"] or [])
            existing = mapped.get("sourceRefs", [])
            additions = [ref for ref in refs if ref not in existing]
            if additions and mapped.get("status") == "withdrawn":
                raise ValueError(f"behavior {identifier} is withdrawn; it cannot acquire sourceRefs")
            if additions:
                mapped["sourceRefs"] = [*existing, *additions]
        if not settles:
            continue
        # Only the reopen-a-proved-item path keeps a reason; plain dispositions carry no mandated text.
        previous = mapped.get("status")
        if revalidate or status == "pending":
            if status == "pending" and mapped.get("kind") == "contract" and previous in {"red", "green"}:
                mapped["status"] = "pending"
                for field in ("redCommand", "proofCommand", "proofBinding"):
                    mapped.pop(field, None)
                continue
            permitted = {"pending", "green", *DISPOSITION_STATUSES} if revalidate else DISPOSITION_STATUSES
            if revalidate and mapped.get("revalidationRequired") and previous == "red":
                permitted = permitted | {"red"}
            if (mapped.get("kind") != "preservation" and not revalidate) or previous not in permitted or (
                revalidate and previous == "pending" and not mapped.get("revalidationRequired")
            ):
                raise ValueError(f"behavior {identifier} is a {mapped.get('kind')} item at {previous}; "
                                 "only a RED contract or settled preservation can be reopened, or preservation revalidated")
            if revalidate and mapped.get("revalidationRequired"):
                continue
            mapped["revalidationRequired"] = True
            if previous in DISPOSITION_STATUSES:
                mapped["status"] = "pending"
                mapped.pop("evidence", None)
                mapped.pop("baselineProof", None)
            continue
        if status == "superseded":
            if previous not in PROOF_STATUSES and not (
                (previous == "already-satisfied" and producer_proved(mapped))
                or (previous == "pending" and "redProof" in mapped)
            ):
                raise ValueError(f"behavior {identifier} is {previous}; only proved or reopened attacked items can be superseded")
            if previous == "already-satisfied":
                mapped["baselineProof"]["command"] = executed_commands(mapped).get("baseline")
            if missing := _required(raw, "supersededBy", str(identifier)):
                raise ValueError(missing[0])
            mapped["supersededBy"] = _text(raw["supersededBy"])
            mapped["supersededFrom"] = previous
        elif status == "withdrawn":
            if mapped.get("kind") != "contract":
                raise ValueError(_PRESERVATION_WITHDRAWN_REFUSED.format(identifier))
            if previous != "pending" or "redProof" in mapped:
                raise ValueError(f"behavior {identifier} is {previous}; only a never-attacked "
                                 "pending contract item can be withdrawn")
            if any(ref.get("type") != "finding"
                   or (str(ref.get("evidenceId")), str(ref.get("id"))) not in settled_findings
                   for ref in mapped.get("sourceRefs") or []):
                raise ValueError(f"behavior {identifier} carries sourceRefs; an owned item cannot be "
                                 "withdrawn while any owning finding is open or fixed")
        elif mapped.get("kind") == "contract":
            raise ValueError(_CONTRACT_DISPOSITION_REFUSED.format(identifier))
        elif status == "already-satisfied" and mapped.get("revalidationRequired"):
            raise ValueError(f"behavior {identifier} requires executed revalidation, not prose already-satisfied")
        elif previous != "pending" and not (
            status == "omitted" and previous == "green" and mapped.get("revalidationRequired")
        ):
            raise ValueError(f"behavior {identifier} is {previous}; only pending items can be dispositioned")
        mapped["status"] = status
        if evidence is not None:
            mapped["evidence"] = evidence


def _observation(proof: object) -> tuple[tuple[str, ...], str] | None:
    """What a recorded RED observed and where; proofs recorded before observations
    existed key nothing. The site may be empty when the runner printed none."""
    if not isinstance(proof, dict) or not isinstance(proof.get("observation"), list):
        return None
    return tuple(str(line) for line in proof["observation"]), str(proof.get("site") or "")


def _bound_observation(entry: JsonObject) -> tuple[tuple[str, ...], str] | None:
    """The observation of an item's current RED. A reopened item keeps its RED
    history as evidence, not as ownership: with no bound command it keys nothing."""
    return _observation(entry.get("redProof")) if entry.get("redCommand") else None


def _explained(observation: tuple[str, ...]) -> bool:
    """pytest `where`/`and` lines name the predicate and its values: the rendering
    itself is the observation, wherever it sits."""
    return any(line.startswith("+") for line in observation)


def same_observation(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    """One rendering extends the other without contradicting it: a pytest explanation
    that binds an intermediate to a local prints fewer `where` lines for the same
    predicate. Compatible, not equal: the label's notion, never the refusal's."""
    if not (_explained(first) and _explained(second)):
        return first == second
    shorter, longer = sorted((first, second), key=len)
    return longer[: len(shorter)] == shorter


def inherited_red(items: list[JsonObject], behavior_id: str, proof: JsonObject) -> str | None:
    """The other item whose recorded RED already observed this failure (issue #54):
    an explained rendering equal wherever it sits, or an unexplained one equal at
    the same non-empty site. A second obligation stopping there established
    nothing of its own."""
    current = _observation(proof)
    if current is None:
        return None
    marker = str(item(items, behavior_id).get("redFailure", ""))
    for entry in items:
        recorded = _bound_observation(entry) if entry.get("id") != behavior_id else None
        if recorded is None:
            continue
        # One output can carry both authored markers; neither is an observation.
        other = str(entry.get("redFailure", ""))
        observation = tuple(line.replace(other, "") for line in current[0])
        theirs = tuple(line.replace(marker, "") for line in recorded[0])
        if observation != theirs:
            continue
        if _explained(observation) or (current[1] and current[1] == recorded[1]):
            return str(entry["id"])
    return None


def _baseline_execution(proof: object) -> tuple[str, str] | None:
    """The stored execution a receipt-attributed baseline was drawn from, or None."""
    if not isinstance(proof, dict):
        return None
    source, test_id = (
        proof.get("sourceExecution") or proof.get("sourceReference")
    ), proof.get("testId")
    if not isinstance(source, str) or not source or not isinstance(test_id, str) or not test_id:
        return None
    return source, test_id


def inherited_baseline(items: list[JsonObject], behavior_id: str, proof: JsonObject) -> str | None:
    """One observed outcome settles one item: the id of another item whose recorded
    baseline carries the same observation and site, or was drawn from the same
    stored execution and test, or None."""
    current = _observation(proof)
    current_execution = _baseline_execution(proof)
    if current is None and current_execution is None:
        return None
    for entry in items:
        if entry.get("id") == behavior_id:
            continue
        recorded_proof = entry.get("baselineProof")
        if current_execution is not None and _baseline_execution(recorded_proof) == current_execution:
            return str(entry["id"])
        recorded = _observation(recorded_proof)
        if current is not None and recorded is not None and recorded == current:
            return str(entry["id"])
    return None


def shared_observations(items: list[JsonObject]) -> list[list[str]]:
    """Groups of items whose REDs rendered the same failure but were admitted: at
    different sites, or as compatible explanations. Named for review."""
    recorded = [(str(entry["id"]), observation) for entry in items
                if (observation := _bound_observation(entry)) is not None]
    groups: list[list[str]] = []
    for identifier, (observation, _) in recorded:
        for group in groups:
            anchor = next(lines for name, (lines, _) in recorded if name == group[0])
            if same_observation(anchor, observation):
                group.append(identifier)
                break
        else:
            groups.append([identifier])
    return [group for group in groups if len(group) > 1]


def green_through_red(entry: JsonObject) -> bool:
    """GREEN through the item's own RED: green now, or recorded green when superseded.
    A superseded item with no record is legacy in-flight state and reads as unproved."""
    return entry.get("status") == "green" or (
        entry.get("status") == "superseded" and entry.get("supersededFrom") == "green"
    )


def producer_proved(entry: JsonObject) -> bool:
    """Proof statuses come only from the producer; already-satisfied counts only with its recorded proof."""
    return not interpretation_pending(entry) and not entry.get("revalidationRequired") and (
        entry.get("status") in PROOF_STATUSES or (
            entry.get("status") == "already-satisfied" and isinstance(entry.get("baselineProof"), dict)
        )
    )


def unresolved(
    items: list[JsonObject], *, terminals: dict[str, JsonObject] | None = None,
) -> list[str]:
    terminals = terminal_items(items) if terminals is None else terminals
    return [
        str(entry["id"])
        for entry in items
        if (interpretation_pending(entry) and entry.get("status") not in {"superseded", "omitted", "withdrawn"})
        or entry.get("status") in {"pending", "red"}
        # Prose already-satisfied is a settlement no producer observed (issue #54).
        or (entry.get("status") == "already-satisfied" and not producer_proved(entry))
        or (entry.get("revalidationRequired") and entry.get("status") not in {"omitted", "superseded"})
        or (entry.get("status") == "superseded" and not (
            terminals[str(entry["id"])].get("status") == "green"
            and producer_proved(terminals[str(entry["id"])])
        ))
    ]


def _actionable(items: list[JsonObject]) -> set[str]:
    """Admission reads only the items a RED can act on; a superseded item's obligation moved on."""
    return {str(entry["id"]) for entry in items if entry.get("status") in {"pending", "red"}}


def may_refactor(items: list[JsonObject]) -> bool:
    """The refactor-while-GREEN window: every contract item resolved and one GREEN through RED."""
    pending = _actionable(items)
    contract = [entry for entry in items if entry.get("kind") == "contract"]
    return not any(entry["id"] in pending for entry in contract) and any(
        entry.get("status") in {"green", "superseded"} for entry in contract
    )


def edit_blocker(items: list[JsonObject]) -> str | None:
    """Missing ordering prerequisites, not the full set of edit obligations. Advice only."""
    preservation = [
        str(entry["id"]) for entry in items
        if entry.get("kind") != "contract" and entry.get("status") == "pending"
    ]
    if preservation:
        return "baseline or disposition preservation item(s) before the edit: " + ", ".join(preservation)
    unswept = [
        str(entry["id"]) for entry in items
        if entry.get("kind") == "contract" and entry.get("status") == "pending"
    ]
    if unswept:
        return "contract item(s) without a RED: " + ", ".join(unswept)
    if any(entry.get("kind") == "contract" and entry.get("status") == "red" for entry in items):
        return None
    if may_refactor(items):
        return None
    contract = [str(entry["id"]) for entry in items if entry.get("kind") == "contract"]
    return (
        "valid behavior-specific RED for a contract Behavior Map item (the refactor "
        "window needs every contract item resolved and one GREEN through RED): "
        + (", ".join(contract) or "none mapped")
    )


def obligation_digest(items: list[JsonObject], active: object = None) -> str:
    """A stateless, bounded reminder from already-loaded items; excluded rows still apply."""
    header = "Behavior obligations (top priority; consult the full map):\n"
    footer = "\n{} row(s) not displayed; budget exclusion does not remove obligations."
    remaining = 2048 - len(header.encode("utf-8")) - len(footer.format(len(items)).encode("utf-8"))
    groups: list[list[JsonObject]] = [[], [], [], []]
    for entry in items:
        status = entry.get("status")
        if status == "omitted":
            groups[3].append(entry)
        elif entry.get("kind") == "contract" and (status == "red" or entry.get("id") == active):
            groups[0].append(entry)
        elif entry.get("kind") == "preservation" and status != "superseded":
            groups[1 if status in {"pending", "red"} or entry.get("revalidationRequired") else 2].append(entry)
    rows: list[str] = []
    for group in groups:
        for entry in group:
            fields = [str(entry[key]) for key in ("id", "status", "behavior", "expected")]
            # Even ASCII cannot fit this row. Do not normalize/encode huge text
            # just to discard it, or repeatedly build/encode the whole digest.
            if sum(map(len, fields)) > remaining:
                continue
            identifier, status, behavior, expected = (
                "".join(" " if not char.isprintable() or char.isspace() else char
                        for char in field) for field in fields
            )
            label = "non-applicable" if status == "omitted" else (
                "revalidation required" if entry.get("revalidationRequired") else "applicable"
            )
            row = f"{identifier} [{status}; {label}] {behavior} => {expected}\n"
            size = len(row.encode("utf-8"))
            if size <= remaining:
                rows.append(row)
                remaining -= size
    return header + "".join(rows) + footer.format(len(items) - len(rows))


def recorded_map(
    tdd_document: JsonObject | None, preflight_document: JsonObject | None
) -> list[JsonObject] | None:
    """The current map: TDD evidence's, else the recorded preflight's, else none."""
    value = tdd_document.get("behaviorMap") if isinstance(tdd_document, dict) else None
    if value is None and isinstance(preflight_document, dict):
        inner = preflight_document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return runtime_items(value) if value is not None else None


def closure_blockers(
    tdd_document: JsonObject | None, preflight_document: JsonObject | None
) -> list[str]:
    """Why the recorded map is not closed; empty when completion may proceed."""
    items = recorded_map(tdd_document, preflight_document)
    if items is None:
        return []
    missing: list[str] = []
    pending = unresolved(items)
    if pending:
        missing.append("unresolved Behavior Map items: " + ", ".join(pending))
    return missing


def all_disposition_only(items: list[JsonObject]) -> bool:
    return bool(items) and all(
        entry.get("status") in DISPOSITION_STATUSES
        and (not entry.get("revalidationRequired") or entry.get("status") == "omitted")
        and (entry.get("status") != "already-satisfied" or producer_proved(entry))
        for entry in items
    )


def no_change_item(evidence: str) -> JsonObject:
    """One explicit fixture/no-change disposition for non-behavioral passes."""
    return {
        "id": "BM_NO_CHANGE",
        "kind": "preservation",
        "behavior": "No production behavior changes in this pass",
        "seam": "workflow preflight evidence",
        "expected": "TDD is not required",
        "redFailure": "unexpected production behavior change",
        "status": "omitted",
        "evidence": evidence,
        "sourceRefs": [],
    }
