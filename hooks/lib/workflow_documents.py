"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from .state_store import utc_timestamp

JsonObject = dict[str, object]
REVIEWER_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
REVIEWER_DISPOSITIONS = REVIEWER_RESOLVED | {"accepted-follow-up"}
ADVISOR_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
ADVISOR_DISPOSITIONS = ADVISOR_RESOLVED | {"accepted-follow-up"}
MEASUREMENT_SHAPE = '{"claim":non-empty text,"command":non-empty text,"result":non-empty text}'
COUNTED_OCCURRENCE_SHAPE = '{"domain":non-empty text,"count":int>=0,"complete":bool,"command":non-empty text,"result":non-empty text}'
SEAM_OCCURRENCE_SHAPE = '{"seam":non-empty text,"reproduction":{"command":non-empty text,"result":non-empty text}}'
DISPOSITION_REQUIREMENTS = {
    "fixed": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; premise.result strips and lowercases to false or counted occurrence has count=0 and complete=true; behavioral fixed always requires the counted occurrence with count=0 and complete=true over the finding's recorded domain",
    "rejected-with-evidence": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; premise.result strips and lowercases to false or counted occurrence has count=0 and complete=true",
    "report-only": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; materialConsequence.result strips and lowercases to false",
    "accepted-follow-up": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; reference=non-empty text",
}
DISPOSITION_SHAPES = {status: f'{{"finding_id":non-empty text,"status":"{status}","kind":"behavioral" or "nonbehavioral"}} plus {requirements}' for status, requirements in DISPOSITION_REQUIREMENTS.items()}


def _disposition_error(status: str, message: str) -> str:
    return f"{message}; {status} expected shape: {DISPOSITION_SHAPES[status]}"


def load_json(path: str, *, label: str) -> JsonObject:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object")
    return value


def advisor_envelope(
    path: str, *, slug: str, workflow_id: str, stage: str, producer: str,
) -> tuple[JsonObject, str]:
    """Validate one strict provider envelope while retaining its exact bytes."""
    try:
        raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        text = raw.decode("utf-8")

        def unique(pairs: list[tuple[str, object]]) -> JsonObject:
            result: JsonObject = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"advisor envelope repeats field: {key}")
                result[key] = value
            return result

        value = json.loads(text, object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read advisor envelope JSON: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "findings", "verdict"}:
        raise ValueError("advisor envelope requires only schemaVersion, findings, and verdict")
    findings, verdict = value.get("findings"), value.get("verdict")
    if type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != 1 or not isinstance(findings, list):
        raise ValueError("advisor envelope requires schemaVersion 1 and a findings array")
    allowed = {"completed"} if stage == "preflight" else FINAL_ENVELOPE_VERDICTS if stage == "final" else set()
    if verdict not in allowed:
        raise ValueError(f"advisor envelope verdict {verdict!r} is incompatible with stage {stage}")
    typed: list[JsonObject] = []
    identifiers: set[str] = set()
    for position, item in enumerate(findings, 1):
        # A completed consult is never discarded over its shape: extra fields are
        # dropped and an unrecognised kind is read as behavioral, the conservative
        # default that makes the finding ride the pass as an attack obligation.
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
        typed.append({"id": identifier, "claim": item["claim"], "material": item["material"], "kind": kind})
    if stage == "final" and verdict in {"commit-ready", "fix-before-commit"} and ((verdict == "commit-ready") == any(item["material"] for item in typed)):
        raise ValueError("advisor envelope verdict is incompatible with finding materiality")
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "producer": producer,
        "stage": stage,
        "verdict": verdict,
        "findings": typed,
        "raw": text,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "recordedAt": utc_timestamp(),
    }, str(verdict)


FINAL_ENVELOPE_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}


DESIGN_FILE_SHAPE = (
    'a readable non-empty UTF-8 design narrative; the declaration is '
    '{"schemaVersion":1,"status":"present","sha256":"<64 hex>"} or '
    '{"schemaVersion":1,"status":"absent","reason":"..."}'
)
DOCUMENT_SHAPES = {**DISPOSITION_SHAPES, "governed-design": DESIGN_FILE_SHAPE}
DOCUMENT_SHAPE_TABLE = "\n".join(["| Surface | Expected shape |", "|---|---|", *(f"| `{name}` | {shape} |" for name, shape in DOCUMENT_SHAPES.items())])


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


def _gate(value: object, message: str) -> JsonObject:
    if not isinstance(value, dict) or not all((
        isinstance(value.get("checks"), list),
        isinstance(value.get("gateVersion"), str),
        isinstance(value.get("ok"), bool),
    )):
        raise ValueError(message)
    return value


def gate_verdict(path: str) -> JsonObject:
    value = _gate(
        load_json(path, label="gate"),
        "input is not the bundled gate's JSON verdict (gateVersion, checks, ok)",
    )
    if not value["ok"]:
        raise ValueError("the gate verdict is ok=false; fix the baseline before recording production-code")
    return value


def validate_gate_result(value: object) -> JsonObject:
    return _gate(value, "quality-gate output is not the bundled gate's JSON verdict")


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


_ABSOLUTE_PATH = re.compile(r"(?<![\w./-])(/[^\s'\"`;|&<>()]+)")


def _refuse_temp_paths(text: str, label: str) -> None:
    """A measurement cited from the temp directory is a throwaway probe, not proof
    the repository keeps; the tdd recorder refuses those targets at cycle open and
    dispositions refuse them here."""
    temp = os.path.realpath(tempfile.gettempdir())
    for token in _ABSOLUTE_PATH.findall(text):
        resolved = os.path.realpath(token)
        if resolved == temp or resolved.startswith(temp + os.sep):
            raise ValueError(
                f"{label} cites {token}, a temporary-directory path; measurement scripts live in the repository"
            )


def _measurement(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or set(value) != {"claim", "command", "result"}:
        raise ValueError(f"{label} requires only claim, command, and result")
    if not all(_text(value.get(field)) for field in ("claim", "command", "result")):
        raise ValueError(f"{label} requires claim, command, and result")
    _refuse_temp_paths(str(value["command"]), f"{label} command")
    return dict(value)


def _occurrence(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("occurrence must be an object")
    if set(value) == {"domain", "count", "complete", "command", "result"}:
        if not all(_text(value.get(field)) for field in ("domain", "command", "result")):
            raise ValueError("counted occurrence requires domain, command, and result")
        if type(value.get("count")) is not int or value["count"] < 0 or not isinstance(value.get("complete"), bool):
            raise ValueError("counted occurrence requires a non-negative count and complete boolean")
        _refuse_temp_paths(str(value["command"]), "occurrence command")
        return dict(value)
    if set(value) == {"seam", "reproduction"} and _text(value.get("seam")):
        reproduction = value.get("reproduction")
        if isinstance(reproduction, dict) and set(reproduction) == {"command", "result"} and all(
            _text(reproduction.get(field)) for field in ("command", "result")
        ):
            _refuse_temp_paths(str(reproduction["command"]), "occurrence reproduction command")
            return {"seam": value["seam"], "reproduction": dict(reproduction)}
    raise ValueError("occurrence requires a counted domain or real-Seam reproduction")


def _disposition_context(value: object) -> JsonObject:
    if not isinstance(value, dict) or set(value) not in ({"workflowId", "candidateTree"}, {"workflowId", "candidateTree", "prHead"}):
        raise ValueError("disposition context requires workflowId, candidateTree, and optional prHead")
    if not _text(value.get("workflowId")) or not _git_oid(value.get("candidateTree")):
        raise ValueError("disposition context requires workflowId and a canonical candidateTree Git OID")
    if "prHead" in value and not _git_oid(value["prHead"]):
        raise ValueError("disposition context prHead must be a canonical Git OID")
    return dict(value)


def _finding_dispositions(value: object, allowed: set[str]) -> list[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError("disposition requires a non-empty dispositions array")
    typed: list[JsonObject] = []
    dispositions = value
    seen: set[str] = set()
    common = {"finding_id", "status", "kind", "premise", "occurrence", "materialConsequence"}
    for item in dispositions:
        if not isinstance(item, dict):
            raise ValueError("each disposition must be an object")
        identifier, status, kind = item.get("finding_id"), item.get("status"), item.get("kind")
        if not _text(identifier):
            raise ValueError(_disposition_error(status, "each disposition must reference a finding") if isinstance(status, str) and status in allowed else "each disposition must reference a finding")
        if not isinstance(status, str) or status not in allowed:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        if identifier in seen:
            raise ValueError(_disposition_error(status, f"finding {identifier} has a duplicate disposition"))
        if kind not in {"behavioral", "nonbehavioral"}:
            raise ValueError(_disposition_error(status, f"finding {identifier} kind must be behavioral or nonbehavioral"))
        try:
            premise = _measurement(item.get("premise"), f"finding {identifier} premise")
            occurrence = _occurrence(item.get("occurrence"))
            consequence = _measurement(item.get("materialConsequence"), f"finding {identifier} materialConsequence")
        except ValueError as exc:
            raise ValueError(_disposition_error(status, str(exc))) from exc
        field = "reference" if status == "accepted-follow-up" else "evidence"
        extra = {field}
        if not _text(item.get(field)):
            raise ValueError(_disposition_error(status, f"finding {identifier} {status} requires {field}"))
        if field == "evidence":
            try:
                _refuse_temp_paths(str(item["evidence"]), f"finding {identifier} evidence")
            except ValueError as exc:
                raise ValueError(_disposition_error(status, str(exc))) from exc
        if set(item) != common | extra:
            raise ValueError(_disposition_error(status, f"finding {identifier} {status} has unknown or missing fields"))
        if status in {"fixed", "rejected-with-evidence"} and not (
            premise["result"].strip().lower() == "false"
            or occurrence.get("count") == 0 and occurrence.get("complete") is True
        ):
            raise ValueError(_disposition_error(
                status, f"finding {identifier} {status} requires a false premise or zero occurrence on a complete domain",
            ))
        # Require the whole-domain claim's fields, not a certificate that the
        # measurements cover it. Closure checks owning proof; review reconciles
        # the immutable claim/domain with the operations actually executed.
        if status == "fixed" and kind == "behavioral" and not (
            occurrence.get("count") == 0 and occurrence.get("complete") is True
        ):
            raise ValueError(_disposition_error(
                status,
                f"finding {identifier} behavioral fixed requires zero occurrence on a complete domain "
                "covering the finding's recorded caller-reachable surface",
            ))
        if status == "report-only" and consequence["result"].strip().lower() != "false":
            raise ValueError(_disposition_error(status, f"finding {identifier} report-only requires no material consequence"))
        seen.add(str(identifier))
        typed.append({**dict(item), "premise": premise, "occurrence": occurrence, "materialConsequence": consequence})
    return typed


def _reviewer_finding_disposition(value: JsonObject) -> tuple[str, JsonObject, list[JsonObject]]:
    if set(value) != {"context", "intakeEvidenceId", "dispositions"}:
        raise ValueError("disposition requires only context, intakeEvidenceId, and dispositions")
    intake_id = value.get("intakeEvidenceId")
    if not _text(intake_id):
        raise ValueError("disposition requires an intakeEvidenceId")
    return str(intake_id), _disposition_context(value.get("context")), _finding_dispositions(
        value.get("dispositions"), REVIEWER_DISPOSITIONS,
    )


def review_summary(
    path: str, *, slug: str, workflow_id: str, resolved_model: str, review_context_id: str,
) -> tuple[JsonObject, str, str]:
    model, context = resolved_model.strip(), review_context_id.strip()
    if not model or not context:
        raise ValueError("resolved model and review context id must be non-empty")
    value = load_json(path, label="review")
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "producer": "code-review", "stage": "code-review",
        "resolvedModel": model, "reviewContextId": context, "recordedAt": utc_timestamp(),
    }
    if value == {"findings": [], "dispositions": []}:
        value = {"findings": []}
    if set(value) == {"findings"}:
        findings = value["findings"]
        if not isinstance(findings, list):
            raise ValueError("review intake findings must be an array")
        seen: set[str] = set()
        required = {"id", "axis", "severity", "material", "kind", "location", "claim", "evidence", "consequence", "smallest_action"}
        for item in findings:
            if not isinstance(item, dict):
                raise ValueError("each review finding must be an object")
            identifier = item.get("id")
            missing, extra = required - set(item), set(item) - required
            if len(missing) == 1 and not extra:
                raise ValueError(f"finding {identifier} requires {next(iter(missing))}")
            if missing or extra:
                raise ValueError("each review finding requires only the intake fields")
            if not _text(identifier) or identifier in seen:
                raise ValueError("review finding ids must be non-empty and unique")
            axis, kind = item.get("axis"), item.get("kind")
            if not isinstance(axis, str) or axis not in {"Standards", "Spec"}:
                raise ValueError(f"finding {identifier} has an invalid axis")
            if not isinstance(kind, str) or kind not in {"behavioral", "nonbehavioral"}:
                raise ValueError(f"finding {identifier} has an invalid kind")
            for field in required - {"id", "axis", "material", "kind"}:
                if not _text(item.get(field)):
                    raise ValueError(f"finding {identifier} requires {field}")
            if not isinstance(item.get("material"), bool):
                raise ValueError(f"finding {identifier} requires a material boolean")
            seen.add(str(identifier))
        status = "pending" if findings else "passed"
        return {**common, "kind": "intake", "status": status, "findings": findings}, status, "pending" if findings else "none"
    intake_id, context, dispositions = _reviewer_finding_disposition(value)
    return {**common, "kind": "disposition", "status": "pending", "context": context, "intakeEvidenceId": intake_id, "dispositions": dispositions}, "pending", "pending"


def advisor_disposition_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    stage: str,
) -> JsonObject:
    value = load_json(path, label="disposition")
    context = _disposition_context(value.get("context"))
    allowed = ADVISOR_DISPOSITIONS
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "stage": stage, "recordedAt": utc_timestamp(), "context": context,
    }
    if set(value) == {"context", "intakeEvidenceId", "dispositions"}:
        intake_id = value.get("intakeEvidenceId")
        if not _text(intake_id):
            raise ValueError("disposition requires an intakeEvidenceId")
        return {**common, "intakeEvidenceId": str(intake_id), "dispositions": _finding_dispositions(
            value.get("dispositions"), allowed,
        )}
    if set(value) != {"context", "findings", "dispositions"}:
        raise ValueError("disposition document requires context, findings, and dispositions")
    findings, dispositions = value.get("findings"), value.get("dispositions")
    if not isinstance(findings, list) or not isinstance(dispositions, list):
        raise ValueError("disposition document requires findings and dispositions arrays")
    if not findings:
        raise ValueError("a document with no findings is --findings none, not addressed")
    claims: set[str] = set()
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("each finding must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in claims:
            raise ValueError("finding ids must be non-empty and unique")
        if set(item) != {"id", "claim"} or not _text(item.get("claim")):
            raise ValueError(f"finding {identifier} requires a claim")
        claims.add(identifier)
    typed = _finding_dispositions(dispositions, allowed)
    if stage == "preflight" and any(item["status"] == "fixed" for item in typed):
        raise ValueError("legacy preflight fixed requires immutable finding intake")
    if any(str(item["finding_id"]) not in claims for item in typed):
        raise ValueError("each disposition must reference a finding")
    if {str(item["finding_id"]) for item in typed} != claims:
        raise ValueError("every finding requires one lead disposition")
    if any(item["kind"] == "behavioral" for item in typed):
        raise ValueError("behavioral advisor findings require immutable finding intake and a map-owned attack")
    return {**common, "findings": findings, "dispositions": typed}
