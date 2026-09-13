"""The recorded Behavior Map contract shared by preflight and TDD."""
from __future__ import annotations

import copy
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
    "id", "kind", "basis", "behavior", "seam", "expected", "redFailure", "status",
})
OPTIONAL_FIELDS = frozenset({
    "evidence", "supersededBy", "sourceRefs", "proofCommand", "baselineProof", "supersededFrom",
    "redCommand", "redProof", "revalidationRequired",
})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
# A baselined item keeps its passing command in `evidence` behind this stamp;
# the tdd producer writes it and readers of executed selections parse it back.
BASELINE_STAMP = "baseline-passed: "


def executed_commands(entry: JsonObject) -> dict[str, str]:
    """The commands this item actually ran, by the phase that recorded each.

    An authored document may carry `proofCommand` and `evidence`, so each is read
    only beside the producer's own mark for that phase: `baselineProof` for the
    baseline command stamped into `evidence`, and a GREEN the producer recorded
    for `proofCommand`. `redCommand` the loader already refuses when authored.
    """
    evidence = entry.get("evidence")
    baseline = (
        str(evidence)[len(BASELINE_STAMP):].strip()
        if isinstance(evidence, str)
        and evidence.startswith(BASELINE_STAMP)
        and isinstance(entry.get("baselineProof"), dict)
        else ""
    )
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
    return value.strip() if isinstance(value, str) and value.strip() else None


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


def _validate_red_failure(value: object, identifier: str) -> str:
    marker = _text(value)
    if marker is None:
        raise ValueError(f"behavior {identifier} requires redFailure")
    if _names_generic_failure(marker):
        raise ValueError(
            f"behavior {identifier} redFailure must name the product behavior, "
            "not a missing API, import, fixture, syntax, collection, setup, or no-test failure"
        )
    return marker


def _source_refs(value: object, identifier: str) -> list[JsonObject] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"behavior {identifier} sourceRefs must be an array")
    result: list[JsonObject] = []
    seen: set[tuple[str, str, str]] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict) or set(raw) != {"type", "evidenceId", "id"}:
            raise ValueError(
                f"behavior {identifier} sourceRef {position} requires only type, evidenceId, and id"
            )
        reference_type, evidence_id, label = raw.get("type"), _text(raw.get("evidenceId")), _text(raw.get("id"))
        if evidence_id is None or label is None or reference_type not in {"design", "finding"}:
            raise ValueError(f"behavior {identifier} sourceRef {position} is not a valid design or finding reference")
        key = (str(reference_type), evidence_id, str(label))
        if key in seen:
            raise ValueError(f"behavior {identifier} repeats {reference_type} sourceRef {label}")
        seen.add(key)
        result.append({"type": reference_type, "evidenceId": evidence_id, "id": label})
    return result


def validate_items(
    value: object,
    *,
    allow_runtime: bool,
    existing: Iterable[JsonObject] = (),
    terminals: dict[str, JsonObject] | None = None,
) -> list[JsonObject]:
    """Validate and return one canonical Behavior Map item list.

    `existing` holds the recorded items a new batch joins, so map-level rules
    read the whole map.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("behaviorMap must be a non-empty array")
    statuses = RUNTIME_STATUSES if allow_runtime else INITIAL_STATUSES
    existing = list(existing)
    seen = {str(entry["id"]) for entry in existing}
    result: list[JsonObject] = []
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"behaviorMap item {position} must be an object")
        unknown = sorted(set(raw) - REQUIRED_FIELDS - OPTIONAL_FIELDS)
        # Maps recorded before `kind` existed still load; their items carry no
        # contract authority. New items always declare a kind.
        missing = sorted(REQUIRED_FIELDS - set(raw) - ({"kind"} if allow_runtime else set()))
        if missing:
            raise ValueError(
                f"behaviorMap item {position} is missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ValueError(
                f"behaviorMap item {position} has unknown fields: {', '.join(unknown)}"
            )
        identifier = _text(raw.get("id"))
        if identifier is None or not IDENTIFIER.fullmatch(identifier):
            raise ValueError(
                "behavior ids must be 2-64 characters: uppercase letters, digits, _ or -"
            )
        if identifier in seen:
            raise ValueError(f"behavior id is duplicated: {identifier}")
        seen.add(identifier)
        kind = _text(raw.get("kind"))
        if kind not in KINDS and not (kind is None and allow_runtime):
            raise ValueError(
                f"behavior {identifier} kind must be one of: {', '.join(sorted(KINDS))}"
            )
        status = _text(raw.get("status"))
        if status not in statuses:
            raise ValueError(
                f"behavior {identifier} status must be one of: {', '.join(sorted(statuses))}"
            )
        # Recorded evidence carries contract already-satisfied only from the
        # producer (tdd --phase red), so the loading path admits it.
        if kind == "contract" and (
            status == "omitted" or (status == "already-satisfied" and not allow_runtime)
        ):
            raise ValueError(_CONTRACT_DISPOSITION_REFUSED.format(identifier))
        if status == "withdrawn" and kind != "contract":
            raise ValueError(_PRESERVATION_WITHDRAWN_REFUSED.format(identifier))
        refs = _source_refs(raw.get("sourceRefs"), identifier)
        item: JsonObject = {
            "id": identifier,
            **({"kind": kind} if kind is not None else {}),
            "basis": _required(raw, "basis", identifier),
            "behavior": _required(raw, "behavior", identifier),
            "seam": _required(raw, "seam", identifier),
            "expected": _required(raw, "expected", identifier),
            "redFailure": _validate_red_failure(raw.get("redFailure"), identifier),
            "status": status,
            **({"sourceRefs": refs} if refs is not None else {}),
        }
        if "evidence" in raw and not isinstance(raw.get("evidence"), str):
            raise ValueError(f"behavior {identifier} evidence must be text")
        # The runner stamps the exact proving command at GREEN, so the executed
        # attack rides beside the declared one wherever the item travels.
        if "proofCommand" in raw:
            item["proofCommand"] = _required(raw, "proofCommand", identifier)
        # The RED surface and its proof stay on the item, so GREEN proves the
        # item against its own RED whichever cycle is open after a sweep.
        if "redCommand" in raw or "redProof" in raw:
            if not allow_runtime or not isinstance(raw.get("redProof"), dict):
                raise ValueError(f"behavior {identifier} redCommand and redProof are recorded only by tdd --phase red")
            item["redCommand"] = _required(raw, "redCommand", identifier)
            item["redProof"] = raw["redProof"]
        # The producer records its baseline proof here and prose never may, so
        # an already-satisfied item carrying it is producer-backed in every
        # lineage; evidence text proves nothing.
        if "revalidationRequired" in raw:
            if not allow_runtime or kind != "preservation" or raw["revalidationRequired"] is not True:
                raise ValueError(f"behavior {identifier} revalidationRequired is producer-owned preservation state")
            item["revalidationRequired"] = True
        if "baselineProof" in raw:
            if not allow_runtime or not isinstance(raw.get("baselineProof"), dict):
                raise ValueError(_BASELINE_PROOF_RESERVED.format(identifier))
            item["baselineProof"] = raw["baselineProof"]
        # Supersession keeps the proof kind it retired, so a post-edit pass
        # cannot be laundered into a GREEN through RED by being superseded.
        if "supersededFrom" in raw:
            if not allow_runtime or raw.get("supersededFrom") not in PROOF_STATUSES:
                raise ValueError(f"behavior {identifier} supersededFrom is recorded only by a tdd-map supersession")
            item["supersededFrom"] = raw["supersededFrom"]
        evidence = _text(raw.get("evidence"))
        if status in EVIDENCED_STATUSES:
            if evidence is None:
                raise ValueError(f"behavior {identifier} status {status} requires evidence")
            item["evidence"] = evidence
        elif "evidence" in raw:
            raise ValueError(
                f"behavior {identifier} status {status} cannot carry disposition evidence"
            )
        if status == "superseded":
            item["supersededBy"] = _required(raw, "supersededBy", identifier)
        elif "supersededBy" in raw:
            raise ValueError(f"behavior {identifier} status {status} cannot carry supersededBy")
        result.append(item)
    whole = [*existing, *result]
    resolved = terminal_items(whole)
    if terminals is not None:
        terminals.update(resolved)
    if not allow_runtime and any(
        entry["status"] == "pending" for entry in whole
    ) and not any(entry.get("kind") == "contract" for entry in whole):
        raise ValueError(
            "a map with a pending item must carry at least one contract item; "
            "a no-change pass maps only dispositioned preservation items"
        )
    return result


def _required(raw: dict[str, object], field: str, identifier: str) -> str:
    value = _text(raw.get(field))
    if value is None:
        raise ValueError(f"behavior {identifier} requires {field}")
    return value


def initial_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False)


def runtime_items(value: object, *, terminals: dict[str, JsonObject] | None = None) -> list[JsonObject]:
    return validate_items(value, allow_runtime=True, terminals=terminals)


def added_items(value: object, existing: list[JsonObject]) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False, existing=existing)


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
        unknown = sorted(set(raw) - {"id", "status", "evidence", "supersededBy", "sourceRefs", "revalidate"})
        if unknown:
            raise ValueError(f"TDD map disposition {position} has unknown fields: {', '.join(unknown)}")
        identifier = _text(raw.get("id"))
        if identifier is None or identifier in seen:
            raise ValueError("TDD map dispositions require unique behavior ids")
        seen.add(identifier)
        mapped = item(items, identifier)
        if "sourceRefs" in raw:
            refs = _source_refs(raw["sourceRefs"], identifier)
            if refs is None:
                raise ValueError(f"behavior {identifier} sourceRefs must be an array")
            existing = mapped.get("sourceRefs", [])
            additions = [ref for ref in refs if ref not in existing]
            if additions and mapped.get("status") == "withdrawn":
                raise ValueError(f"behavior {identifier} is withdrawn; it cannot acquire sourceRefs")
            if additions:
                mapped["sourceRefs"] = [*existing, *additions]
        revalidate = "revalidate" in raw
        status = _text(raw.get("status"))
        if revalidate and (raw["revalidate"] is not True or "status" in raw):
            raise ValueError("revalidate must be true and is mutually exclusive with status")
        if not revalidate and "status" not in raw:
            if set(raw) - {"id", "sourceRefs"} or "sourceRefs" not in raw:
                raise ValueError(f"behavior {identifier} disposition requires status, revalidate or sourceRefs")
            continue
        evidence = _text(raw.get("evidence"))
        if evidence is None:
            raise ValueError(f"behavior {identifier} disposition requires evidence")
        if "supersededBy" in raw and status != "superseded":
            raise ValueError(f"behavior {identifier} disposition {status} cannot carry supersededBy")
        previous = mapped.get("status")
        if revalidate or status == "pending":
            permitted = {"pending", "green", *DISPOSITION_STATUSES} if revalidate else DISPOSITION_STATUSES
            if revalidate and mapped.get("revalidationRequired") and previous == "red":
                permitted = permitted | {"red"}
            if mapped.get("kind") != "preservation" or previous not in permitted:
                raise ValueError(f"behavior {identifier} is a {mapped.get('kind')} item at {previous}; "
                                 "only settled preservation can be reopened or preservation revalidated")
            if revalidate and mapped.get("revalidationRequired"):
                continue
            mapped["revalidationRequired"] = True
            if previous in DISPOSITION_STATUSES:
                mapped["status"] = "pending"
                mapped.pop("evidence", None)
                mapped.pop("baselineProof", None)
            continue
        if status not in EVIDENCED_STATUSES:
            raise ValueError(f"behavior {identifier} disposition must be one of: "
                             + ", ".join(sorted(EVIDENCED_STATUSES | {"pending"})))
        if status == "superseded":
            if previous not in PROOF_STATUSES:
                raise ValueError(f"behavior {identifier} is {previous}; only a GREEN item can be superseded")
            mapped["supersededBy"] = _required(raw, "supersededBy", identifier)
            mapped["supersededFrom"] = previous
        elif status == "withdrawn":
            if mapped.get("kind") != "contract":
                raise ValueError(_PRESERVATION_WITHDRAWN_REFUSED.format(identifier))
            if previous != "pending":
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
        mapped["evidence"] = evidence


def green_through_red(entry: JsonObject) -> bool:
    """GREEN through the item's own RED: green now, or recorded green when superseded.
    A superseded item with no record is legacy in-flight state and reads as unproved."""
    return entry.get("status") == "green" or (
        entry.get("status") == "superseded" and entry.get("supersededFrom") == "green"
    )


def producer_proved(entry: JsonObject) -> bool:
    """Proof statuses come only from the producer; already-satisfied counts only with its recorded proof."""
    return not entry.get("revalidationRequired") and (
        entry.get("status") in PROOF_STATUSES or (
            entry.get("status") == "already-satisfied" and isinstance(entry.get("baselineProof"), dict)
        )
    )


def unresolved(
    items: list[JsonObject], *, terminals: dict[str, JsonObject] | None = None,
) -> list[str]:
    if terminals is None:
        terminals = terminal_items(items)
    return [
        str(entry["id"])
        for entry in items
        if entry.get("status") in {"pending", "red"}
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
        if status == "omitted" and entry.get("evidence"):
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
        for entry in items
    )


def no_change_item(evidence: str) -> JsonObject:
    """One explicit fixture/no-change disposition for non-behavioral passes."""
    return {
        "id": "BM_NO_CHANGE",
        "kind": "preservation",
        "basis": "governing evidence",
        "behavior": "No production behavior changes in this pass",
        "seam": "workflow preflight evidence",
        "expected": "TDD is not required",
        "redFailure": "unexpected production behavior change",
        "status": "omitted",
        "evidence": evidence,
        "sourceRefs": [],
    }
