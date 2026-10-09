"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from .behavior_map import initial_items, map_errors
from .state_store import utc_timestamp

JsonObject = dict[str, object]
REVIEWER_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
REVIEWER_DISPOSITIONS = REVIEWER_RESOLVED | {"accepted-follow-up"}
ADVISOR_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
ADVISOR_DISPOSITIONS = ADVISOR_RESOLVED | {"accepted-follow-up"}


def load_json(path: str, *, label: str) -> JsonObject:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object")
    return value


def _unique(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"document repeats field: {key}")
        result[key] = value
    return result


def preflight_document(path: str) -> JsonObject:
    """The contract and its Behavior Map; every violation is named in one refusal."""
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot read preflight JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("preflight document must be a JSON object")
    errors = [message for message, bad in (
        (f"preflight document has unknown sections: {', '.join(sorted(set(value) - PREFLIGHT_SECTIONS))}",
         set(value) - PREFLIGHT_SECTIONS),
        ("preflight authoritativeContract must be non-empty text", not _text(value.get("authoritativeContract"))),
    ) if bad] + map_errors(value.get("behaviorMap"), allow_runtime=False)
    if errors:
        raise ValueError("; ".join(errors))
    return {"authoritativeContract": str(value["authoritativeContract"]).strip(),
            "behaviorMap": initial_items(value["behaviorMap"])}


PREFLIGHT_SECTIONS = frozenset({"authoritativeContract", "behaviorMap"})


def advisor_envelope(
    path: str, *, slug: str, workflow_id: str, stage: str, producer: str,
) -> tuple[JsonObject, str]:
    """Retain typed findings once; the response digest preserves byte identity."""
    try:
        raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read advisor envelope JSON: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "findings", "verdict"}:
        raise ValueError("advisor envelope requires only schemaVersion, findings, and verdict")
    findings, verdict = value.get("findings"), value.get("verdict")
    if type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != 1 or not isinstance(findings, list):
        raise ValueError("advisor envelope requires schemaVersion 1 and a findings array")
    allowed = {"completed", "approved", "changes-required"} if stage == "preflight" else FINAL_ENVELOPE_VERDICTS if stage == "final" else set()
    if verdict not in allowed:
        raise ValueError(f"advisor envelope verdict {verdict!r} is incompatible with stage {stage}")
    typed: list[JsonObject] = []
    identifiers: set[str] = set()
    for position, item in enumerate(findings, 1):
        if not isinstance(item, dict) or not {"id", "claim", "material"} <= set(item):
            raise ValueError(f"advisor finding {position} requires id, claim, and material")
        identifier, kind = item.get("id"), item.get("kind")
        if not _text(identifier) or identifier in identifiers:
            raise ValueError("advisor finding ids must be non-empty and unique")
        if not _text(item.get("claim")) or not isinstance(item.get("material"), bool):
            raise ValueError(f"advisor finding {identifier} requires claim and material boolean")
        if not isinstance(kind, str) or kind not in {"behavioral", "nonbehavioral"}:
            kind = "behavioral"
        identifiers.add(str(identifier))
        prior = item.get("priorFinding")
        if prior is not None and (not isinstance(prior, dict) or set(prior) != {"evidenceId", "id"}
                                  or not all(_text(v) for v in prior.values())):
            raise ValueError("priorFinding requires evidenceId and id")
        finding = {"id": identifier, "claim": item["claim"], "material": item["material"], "kind": kind,
                   **({"priorFinding": prior} if prior is not None else {})}
        if stage == "final" and item["material"]:
            sketch = item.get("fixSketch")
            if (isinstance(sketch, dict) and set(sketch) == {"change", "probe"}
                    and all(_text(v) and not re.search(r"[\ud800-\udfff]", v) for v in sketch.values())
                    and sum(len(v.encode("utf-8")) for v in sketch.values()) <= 8192):
                finding["fixSketch"] = sketch
            else:
                finding["fixSketchIssue"] = "missing, malformed, or exceeds 8192 UTF-8 bytes"
        typed.append(finding)
    if verdict in {"approved", "changes-required", "commit-ready", "fix-before-commit"} and ((verdict in {"approved", "commit-ready"}) == any(item["material"] for item in typed)):
        raise ValueError("advisor envelope verdict is incompatible with finding materiality")
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "producer": producer,
        "stage": stage,
        "verdict": verdict,
        "findings": typed,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "recordedAt": utc_timestamp(),
        "observationId": uuid.uuid4().hex,
    }, str(verdict)


FINAL_ENVELOPE_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}


DESIGN_FILE_SHAPE = (
    'a readable non-empty UTF-8 design narrative; the declaration is '
    '{"schemaVersion":1,"status":"present","sha256":"<64 hex>"} or '
    '{"schemaVersion":1,"status":"absent","reason":"..."}'
)
ITEM_SHAPE = ('{"id":"BM_X","kind":"contract|preservation","basis":"original request or preservation",'
              '"behavior":"...","seam":"...","expected":"...","boundaryInputs":["executed case name"],'
              '"interpretations":["reading a","reading b"],"interpretation":"...","authority":"...",'
              '"sourceRefs":[{"type":"finding","evidenceId":"<intake>","id":"SPEC-1"}]}')
RECORD_SHAPES = {
    "preflight": f'{{"authoritativeContract":"text","behaviorMap":[{ITEM_SHAPE}]}}',
    "review": '{"findings":[{"id":"R-1","claim":"...","material":true}]}',
    "advisor-result": ('the advisor envelope {"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"...",'
                       '"material":true}],"verdict":"approved|changes-required|completed|commit-ready|'
                       'fix-before-commit|context-mismatch"}, or --verdict unavailable --reason TEXT'),
    "advisor-disposition": ("--finding F --fixed [--behavior-id BM]; or --finding F "
                            "--rejected|--report-only|--follow-up REF --reason TEXT; or --findings none"),
    "tdd-map": f'{{"items":[{ITEM_SHAPE}]}}; a recorded item needs only id and the changed fields, null removes an optional one',
}


def validate_design_declaration(value: object) -> JsonObject:
    """The design is a falsifiable hypothesis under attack, not a label registry.

    A declaration recorded before the catalogue was retired still loads: its
    extra ``catalogue`` field is dropped rather than refused, because prior
    evidence is append-only history, never a schema hostage.
    """
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("governed design declaration requires schemaVersion 1")
    status = value.get("status")
    if status == "present":
        if not set(value) <= {"schemaVersion", "status", "sha256", "catalogue"}:
            raise ValueError("present governed design declaration has unknown or missing fields")
        digest = value.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("present governed design declaration requires a SHA-256 digest")
        return {"schemaVersion": 1, "status": "present", "sha256": digest}
    if status == "absent":
        if set(value) != {"schemaVersion", "status", "reason"} or not _text(value.get("reason")):
            raise ValueError("absent governed design declaration requires only a non-empty reason")
        return {"schemaVersion": 1, "status": "absent", "reason": str(value["reason"])}
    raise ValueError("governed design declaration status must be present or absent")


def design_declaration(path: str) -> JsonObject:
    return validate_design_declaration(load_json(path, label="governed design declaration"))


def design_file_declaration(path: str) -> JsonObject:
    try:
        raw = Path(path).read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read governed design: {exc}") from exc
    if not raw.strip():
        raise ValueError(f"governed design is empty; expected shape: {DESIGN_FILE_SHAPE}")
    return {
        "schemaVersion": 1,
        "status": "present",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def design_absence(reason: str) -> JsonObject:
    return validate_design_declaration({"schemaVersion": 1, "status": "absent", "reason": reason})


def validate_gate_result(value: object) -> JsonObject:
    """The gate verdict as recorded: its outcome and errors, the only parts any
    reader consumes; the full findings stay in the gate's own output."""
    if not isinstance(value, dict) or not all((
        isinstance(value.get("checks"), list),
        isinstance(value.get("gateVersion"), str),
        isinstance(value.get("ok"), bool),
    )):
        raise ValueError("quality-gate output is not the bundled gate's JSON verdict")
    return {"ok": value["ok"], "errors": value.get("errors") or []}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _git_oid(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None


def _resolved_graph(value: object) -> JsonObject:
    """The producer's graph result, accepted only when it resolved every check.

    Contract validation, not re-analysis: Repo Context Forge already owns checkout
    identity, index freshness, plan selection, GitNexus invocation and normalisation,
    and blocks rather than returning an unresolved answer. What is checked here is
    only that the answer this consumer is about to persist really is that resolved
    answer, so a blocked, partial or placeholder result can never become evidence.
    """
    if not isinstance(value, dict) or value.get("status") != "resolved":
        raise ValueError("the producer returned no resolved graph result; rerun Repo Context Forge")
    if value.get("unresolved_checks") != []:
        raise ValueError("the producer left graph checks unresolved; rerun Repo Context Forge")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ValueError("the resolved graph result has no entries list")
    # How many checks a packet plans is the producer's decision, and a packet that
    # planned none still resolved. Demanding facts here would invent a refusal for
    # every surface Repo Context Forge legitimately had nothing to ask about.
    # A present file GitNexus does not index (a lockfile) resolves as "unindexed" with
    # no identity; that is the producer's resolved answer for a file_context check.
    for entry in entries:
        if not isinstance(entry, dict) or not all(_text(entry.get(field)) for field in ("kind", "file", "target")):
            raise ValueError("a graph entry is unresolved or missing its identity")
        if entry.get("status") == "unindexed" and entry.get("kind") == "file_context":
            continue
        if entry.get("status") != "resolved" or not _text(entry.get("resolved_identity")):
            raise ValueError("a graph entry is unresolved or missing its identity")
    if any(type(value.get(metric)) is not int for metric in
           ("elapsed_ms", "process_count", "graph_call_count", "output_bytes")):
        raise ValueError("the resolved graph result is missing its execution metrics")
    revision = value.get("producer_revision")
    if not isinstance(revision, dict) or not _text(revision.get("commit")):
        raise ValueError("the resolved graph result names no producer revision")
    return value


def validate_advisor_projection(
    value: object, *, candidate_tree: str | None = None,
) -> JsonObject:
    fields = {
        "schemaVersion", "producerRevision", "sourceRepo", "sourceBaseOid",
        "committedHeadOid", "expectedCandidateTree", "indexedCandidateTree",
        "targets", "graph", "coverageGaps",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
    ):
        raise ValueError("advisor projection requires the installed schemaVersion 1 shape")
    revision = value.get("producerRevision")
    if (
        not isinstance(revision, dict)
        or set(revision) != {"commit", "dirty"}
        or not isinstance(revision.get("dirty"), bool)
        or not _git_oid(revision.get("commit"))
    ):
        raise ValueError("advisor projection requires canonical producer provenance")
    source_repo = value.get("sourceRepo")
    if not (_text(source_repo) or source_repo == {"gap": "source_repo_unavailable"}):
        raise ValueError("advisor projection requires canonical source repository provenance")
    for field in (
        "sourceBaseOid", "committedHeadOid", "expectedCandidateTree",
        "indexedCandidateTree",
    ):
        if not _git_oid(value.get(field)):
            raise ValueError(f"advisor projection requires a canonical Git OID for {field}")
    if value["expectedCandidateTree"] != value["indexedCandidateTree"]:
        raise ValueError("advisor projection candidate trees do not match")
    if candidate_tree is not None and value["expectedCandidateTree"] != candidate_tree:
        raise ValueError("advisor projection does not describe the active candidate tree")
    if not isinstance(value.get("targets"), list) or not all(
        isinstance(target, dict) for target in value["targets"]
    ):
        raise ValueError("advisor projection targets must be an array of objects")
    graph = value.get("graph")
    if not isinstance(graph, dict) or set(graph) != {
        "status", "references", "requiredOmissions", "optionalOmissionCount",
    }:
        raise ValueError("advisor projection graph has an invalid shape")
    if graph.get("status") != "resolved":
        raise ValueError("advisor projection graph is not resolved")
    references = graph.get("references")
    if not isinstance(references, list) or not all(_text(item) for item in references):
        raise ValueError("advisor projection graph references must be text")
    if graph.get("requiredOmissions") != []:
        raise ValueError("advisor projection has required graph omissions")
    omissions = graph.get("optionalOmissionCount")
    if type(omissions) is not int or omissions < 0:
        raise ValueError("advisor projection optional omission count is invalid")
    gaps = value.get("coverageGaps")
    if not isinstance(gaps, list) or not all(isinstance(gap, dict) for gap in gaps):
        raise ValueError("advisor projection coverage gaps must be an array of objects")
    return dict(value)


def _gate_symbols(graph: JsonObject) -> list[JsonObject]:
    """Gate-shaped symbol results carrying only genuine incoming-relationship
    data: context-check callers and file-context references, merged per
    (file, target). Impact entries hold path-only impacted files; relabeling
    those as relationship evidence would overstate the caller/callee coverage
    the owner rules establish scope from."""
    symbols: dict[tuple[str, str], dict[str, object]] = {}
    for entry in graph["entries"]:  # entries already validated by _resolved_graph
        for key in ("callers", "references"):
            found = entry.get(key)
            if not isinstance(found, list):
                continue
            symbol = symbols.setdefault(
                (str(entry["file"]), str(entry["target"])),
                {"name": str(entry["target"]), "file": str(entry["file"])},
            )
            symbol.setdefault(key, []).extend(
                str(item["identity"]) for item in found
                if isinstance(item, dict) and _text(item.get("identity"))
            )
    return [symbols[key] for key in sorted(symbols)]


def graph_evidence_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    source_root: str,
    canonical_source_repo: str | None,
    snapshot: JsonObject | None = None,
    snapshot_gap: str | None = None,
) -> JsonObject:
    """The repo-context-forge evidence document built from the producer's machine packet.

    `snapshot` is the adapter's measured claim that the analysis covered exactly
    one base commit and candidate tree; when it holds, the document additionally
    carries the gate-shaped context the typed quality-gate run hands to
    `--gitnexus-context-json`. `snapshot_gap` records the measured reason no such
    claim could be made, so an unbound pass names its gap instead of implying one
    was never measured. The gate alone adjudicates match, stale, or absent.
    """
    if snapshot is not None and snapshot_gap is not None:
        raise ValueError("a snapshot binding and a snapshot gap are mutually exclusive")
    packet = load_json(path, label="packet")
    target = packet.get("target_state")
    reported = target.get("source_repo") if isinstance(target, dict) else None
    if not _text(reported) or os.path.realpath(str(reported)) != os.path.realpath(source_root):
        raise ValueError(f"the packet was produced for {reported!r}, not {source_root}")
    committed_head = target.get("head_sha") if isinstance(target, dict) else None
    if not _git_oid(committed_head):
        raise ValueError("the packet names no canonical Git committed head")
    git_facts = packet.get("git")
    source_base = git_facts.get("merge_base") if isinstance(git_facts, dict) else None
    if not _git_oid(source_base):
        raise ValueError("the packet names no canonical Git merge base")
    gitnexus = packet.get("gitnexus")
    graph = _resolved_graph(gitnexus.get("analysis") if isinstance(gitnexus, dict) else None)
    authority = graph.get("authority")
    graph_source = authority.get("source_repository") if isinstance(authority, dict) else None
    if not _text(graph_source) or os.path.realpath(str(graph_source)) != os.path.realpath(source_root):
        raise ValueError(f"the graph was produced for {graph_source!r}, not {source_root}")
    snapshot_candidate = None
    if snapshot is not None:
        if not (_text(snapshot.get("base")) and _text(snapshot.get("candidate"))):
            raise ValueError("a snapshot binding requires its base commit and candidate tree")
        snapshot_candidate = str(snapshot["candidate"]).strip()
    projection = validate_advisor_projection(
        packet.get("advisorProjection"), candidate_tree=snapshot_candidate,
    )
    # The advisor reads which files changed, which symbols, and why each file
    # was selected; ranking signals, symbol ranges, and analysis-worktree paths
    # cost 154KB on one pass and never reached an envelope. The wrapper forwards
    # this projection whole.
    projection["targets"] = [
        {
            "path": target.get("path"), "surface_role": target.get("surface_role"),
            "rank": target.get("rank"),
            "changed_symbols": [
                symbol.get("name") for symbol in target.get("changed_symbols") or []
                if isinstance(symbol, dict)
            ],
            "why_selected": target.get("why_selected"),
        }
        for target in projection["targets"]
    ]
    expected_source_repo: object = (
        canonical_source_repo
        if canonical_source_repo is not None
        else {"gap": "source_repo_unavailable"}
    )
    if projection["sourceRepo"] != expected_source_repo:
        raise ValueError(
            f"the advisor projection was produced for {projection['sourceRepo']!r}, "
            f"not {expected_source_repo!r}"
        )
    if projection["sourceBaseOid"] != source_base:
        raise ValueError(
            f"the advisor projection was produced for source base "
            f"{projection['sourceBaseOid']!r}, not {source_base!r}"
        )
    if projection["committedHeadOid"] != committed_head:
        raise ValueError(
            f"the advisor projection was produced for committed head "
            f"{projection['committedHeadOid']!r}, not {committed_head!r}"
        )
    document: JsonObject = {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "graph": graph,
        "advisorProjection": projection,
        "recordedAt": utc_timestamp(),
    }
    if snapshot is not None:
        document["gateContext"] = {
            "base": str(snapshot["base"]).strip(),
            "candidate": str(snapshot["candidate"]).strip(),
            "symbols": _gate_symbols(graph),
        }
    elif snapshot_gap is not None:
        if not _text(snapshot_gap):
            raise ValueError("a snapshot gap requires its measured reason")
        document["gateContextGap"] = snapshot_gap.strip()
    return document


def _disposition_context(value: object) -> list[str]:
    fields = value if isinstance(value, dict) else {}
    return [message for message, bad in (
        ("disposition context requires workflowId, candidateTree, and optional prHead",
         set(fields) not in ({"workflowId", "candidateTree"}, {"workflowId", "candidateTree", "prHead"})),
        ("disposition context requires workflowId and a canonical candidateTree Git OID", "workflowId" in fields
         and not _text(fields["workflowId"]) or "candidateTree" in fields and not _git_oid(fields["candidateTree"])),
        ("disposition context prHead must be a canonical Git OID", "prHead" in fields and not _git_oid(fields["prHead"])),
    ) if bad]


def _repair_succession(succession: object) -> list[str]:
    if not isinstance(succession, dict):
        return ["repairSuccession requires context, findings, previousOwner and evidence"]
    owner, refs = succession.get("previousOwner"), succession.get("findings")
    return [problem for problem, bad in (
        ("repairSuccession requires context, findings, previousOwner and evidence",
         set(succession) != {"context", "findings", "previousOwner", "evidence"} or not _text(succession.get("evidence"))),
        ("repairSuccession requires the previous implementer and reviewer", "previousOwner" in succession and (
            not isinstance(owner, dict) or set(owner) != {"implementerContextId", "reviewerContextId"}
            or not all(_text(v) for v in owner.values()))),
        ("repairSuccession findings require evidenceId/id references", "findings" in succession and (
            not isinstance(refs, list) or not refs or any(not isinstance(ref, dict) or set(ref) != {"evidenceId", "id"}
                                                          or not all(_text(v) for v in ref.values()) for ref in refs))),
    ) if bad] + (_disposition_context(succession["context"]) if "context" in succession else [])


def review_summary(
    path: str, *, slug: str, workflow_id: str, review_context_id: str | None,
) -> tuple[JsonObject, str, str]:
    value = load_json(path, label="review")
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "producer": "code-review", "stage": "code-review", "recordedAt": utc_timestamp(),
        "observationId": uuid.uuid4().hex,
    }
    if review_context_id is not None:
        if not review_context_id.strip():
            raise ValueError("--review-context-id must be non-empty")
        common["reviewContextId"] = review_context_id.strip()
    if value == {"findings": [], "dispositions": []}:
        value = {"findings": []}
    if "findings" not in value:
        raise ValueError("review intake requires the reviewer's findings")
    unknown = sorted(set(value) - {"findings", "implementationContextId", "repairSuccession"})
    findings = value["findings"]
    errors = [message for message, bad in (
        (f"review intake has unknown fields: {', '.join(unknown)}", unknown),
        ("implementationContextId must identify the actual repair author",
         "implementationContextId" in value and not _text(value["implementationContextId"])),
    ) if bad]
    if "repairSuccession" in value:
        errors += _repair_succession(value["repairSuccession"])
        errors += [] if "implementationContextId" in value else ["repairSuccession requires implementationContextId"]
    errors += [] if isinstance(findings, list) else ["review intake findings must be an array"]
    seen: set[str] = set()
    for position, item in enumerate(findings if isinstance(findings, list) else [], 1):
        if not isinstance(item, dict):
            errors.append(f"review finding {position} must be an object")
            continue
        identifier = item.get("id")
        label = f"finding {identifier}" if _text(identifier) else f"review finding {position}"
        problems = [problem for problem, bad in (
            ("id must be non-empty and unique", not _text(identifier) or identifier in seen),
            ("requires a non-empty claim", not _text(item.get("claim"))),
            ("requires a material boolean", not isinstance(item.get("material"), bool)),
        ) if bad]
        if _text(identifier):
            seen.add(str(identifier))
        prior = item.get("priorFinding")
        if prior is not None and (not isinstance(prior, dict) or set(prior) != {"evidenceId", "id"}
                                  or not all(_text(v) for v in prior.values())):
            problems.append("priorFinding requires evidenceId and id")
        errors += [f"{label}: " + ", ".join(problems)] if problems else []
    if errors:
        raise ValueError("; ".join(errors))
    common.update({key: value[key] for key in ("implementationContextId", "repairSuccession") if key in value})
    status = "pending" if findings else "passed"
    return {**common, "kind": "intake", "status": status, "findings": findings}, status, "pending" if findings else "none"
