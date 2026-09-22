"""Repository-scoped production workflow policy and transactional commands."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from typing import Callable, Sequence

from . import behavior_map, tdd_surface
from ._workflow_db import (
    EvidenceWrite,
    LedgerError,
    LedgerMutation,
    ManifestWrite,
    evidence_write,
    manifest_write,
    mutation,
    read_active,
    read_evidence,
    read_manifest,
)
from .repo_identity import RepoIdentity
from .workflow_documents import validate_advisor_projection, validate_design_declaration
from .state_store import (
    _active_candidate_tree,
    is_governance_path,
    is_reviewable_path,
    is_test_path,
    manifest_diff,
    tree_manifest,
    utc_timestamp,
)

JsonObject = dict[str, object]
STEP_FIELDS = {
    "repo-context-forge": "repoContextForge",
    "preflight": "preflight",
    "tdd": "tdd",
    "production-code": "productionCode",
    "implementation": "implementation",
    "verification": "verification",
}
WORKFLOW_SEQUENCE = (
    "repo-context-forge",
    "preflight",
    "tdd",
    "verification",
    "code-review",
    "final-review",
)
STEP_STATUSES = {"pending", "in-progress", "passed", "not-required", "unavailable"}
FINDING_STATUSES = {"pending", "none", "addressed"}
REVIEW_SOURCES = {"codex-advisor"}
FINAL_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}
NO_INSTANCE_ID = "this state predates workflow instance identity and can no longer advance; begin a new workflow"
SLUG_MISMATCH = "--slug does not match the active workflow"
INSTANCE_MISMATCH = "--workflow-id does not match the active workflow instance"
PREFLIGHT_CLOSED = "governance revalidation permits only re-verification and review; preflight consults are closed"
TDD_CLOSED = "governance revalidation permits only re-verification and review; tdd is closed"
MANIFEST_MISSING = "review-manifest-missing"
MANIFEST_STALE = "review-manifest-stale"
QUALITY_GATE_MISSING = "quality-gate-tree-missing"
QUALITY_GATE_STALE = "quality-gate-tree-stale"


class WorkflowError(LedgerError):
    """Base error for invalid workflow operations."""


class WorkflowMissing(WorkflowError):
    """No active workflow exists for this repository."""


class WorkflowIncomplete(WorkflowError):
    """The workflow cannot transition to complete."""


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-workflow"


def _normalise(state: JsonObject | None) -> JsonObject | None:
    if state is None:
        return None
    advisor = state.get("advisorPreflight")
    if isinstance(advisor, dict):
        advisor.setdefault("findings", "pending")
        advisor.setdefault("reason", None)
    return state


def read_workflow(identity: RepoIdentity) -> JsonObject | None:
    try:
        return _normalise(read_active(identity))
    except LedgerError as exc:
        raise WorkflowError(str(exc)) from exc


def _require_state(state: JsonObject | None) -> JsonObject:
    if state is None:
        raise WorkflowMissing("no active workflow")
    return _normalise(state) or state


def _require(identity: RepoIdentity) -> JsonObject:
    return _require_state(read_workflow(identity))


def _updated(state: JsonObject) -> JsonObject:
    state["updatedAt"] = utc_timestamp()
    return state


def _commit(
    transaction: LedgerMutation,
    state: JsonObject,
    kind: str,
    *, evidence: Sequence[EvidenceWrite] = (),
    manifests: Sequence[ManifestWrite] = (),
) -> JsonObject:
    return transaction.append(_updated(state), kind, evidence=evidence, manifests=manifests)


EVIDENCE_PHASES = ("repo-context-forge", "preflight", "production-code", "verification")


def _evidence_ready(state: JsonObject, phase: str) -> bool:
    """A producer-recorded passed: status alone is a bare claim for evidence phases."""
    field = STEP_FIELDS[phase]
    return state.get(field) == "passed" and (
        phase not in EVIDENCE_PHASES or bool(state.get(f"{field}Evidence"))
    )


def _allows_next(state: JsonObject, phase: str) -> bool:
    if phase in STEP_FIELDS:
        if phase == "tdd":
            return state.get("tdd") in {"passed", "not-required"}
        if phase == "verification":
            return (
                _evidence_ready(state, phase)
                and bool(state.get("qualityGateEvidence"))
                and bool(state.get("qualityGateManifestId"))
            )
        return _evidence_ready(state, phase)
    if phase == "code-review":
        review = state.get("codeReview")
        return (
            isinstance(review, dict)
            and review.get("status") in {"passed", "not-required"}
            and review.get("findings") in {"none", "addressed"}
        )
    if phase == "final-review":
        review = state.get("finalReview")
        return (
            isinstance(review, dict)
            and review.get("source") in REVIEW_SOURCES
            and review.get("status") in {"commit-ready", "fix-before-commit"}
            and (review.get("status") == "commit-ready" or bool(review.get("intakeEvidence")))
            and review.get("findings") in {"none", "addressed"}
        )
    return False


def _preflight_finding_states(state: JsonObject) -> list[JsonObject]:
    states = state.get("findingStates")
    if not isinstance(states, list):
        return []
    return [entry for entry in states if isinstance(entry, dict) and entry.get("stage") == "preflight"]


def _rides_the_map(entry: JsonObject) -> bool:
    """A pending behavioral finding is a direct attack obligation the map owns."""
    return entry.get("status") == "pending" and entry.get("kind") == "behavioral"


def _review_assessed(state: JsonObject) -> bool:
    """A recorded assessment may expose open findings to its final advisor.

    The checkpoint and result recorder separately validate both tree bindings.
    This is not completion authority and permits only the first final result.
    """
    review, final = state.get("codeReview"), state.get("finalReview")
    return bool(state.get("codeReviewEvidence") and state.get("reviewManifestId")
                and _allows_next(state, "tdd") and _allows_next(state, "verification")
                and isinstance(review, dict) and review.get("status") in {"pending", "passed"}
                and isinstance(final, dict) and final.get("source") is None)


def _require_predecessor(state: JsonObject, phase: str) -> None:
    if phase not in WORKFLOW_SEQUENCE:
        return
    if phase == "code-review" and not _allows_next(state, "tdd"):
        raise WorkflowIncomplete("code-review requires tdd")
    if phase == "final-review" and _review_assessed(state):
        return
    position = WORKFLOW_SEQUENCE.index(phase)
    if position and not _allows_next(state, WORKFLOW_SEQUENCE[position - 1]):
        raise WorkflowIncomplete(f"{phase} requires {WORKFLOW_SEQUENCE[position - 1]}")


def _next_incomplete_phase(state: JsonObject) -> str:
    return next(
        (phase for phase in WORKFLOW_SEQUENCE if not _allows_next(state, phase)),
        "complete-workflow",
    )


def _derive_next_action(state: JsonObject, tdd_document: JsonObject | None = None) -> str:
    finding_states = state.get("findingStates", [])
    correction = [
        entry for entry in finding_states
        if isinstance(entry, dict) and entry.get("stage") in {"code-review", "final"}
    ] if isinstance(finding_states, list) else []
    if state.get("finalReviewContextMismatchEvidence"):
        return "re-consult-final-review"
    if any(entry.get("status") == "pending" and _finding_unresolved(entry) for entry in correction):
        return "classify-current-findings"
    accepted = any(
        entry.get("status") == "accepted-follow-up" and entry.get("material") is True
        for entry in correction
    )
    if accepted:
        if state.get("tdd") == "in-progress":
            return "run-mapped-tdd"
        return "close-current-findings"
    if any(entry.get("appealStatus") == "pending" for entry in finding_states):
        return "appeal-final-review"
    phase = _next_incomplete_phase(state)
    if phase == "final-review":
        review = state.get("finalReview")
        if isinstance(review, dict) and review.get("status") not in {None, "pending"}:
            return "address-review-findings"
    return phase


def begin(identity: RepoIdentity, slug: str, intent: str = "") -> JsonObject:
    normalized = safe_slug(slug)
    if normalized == "unnamed-workflow":
        raise ValueError("workflow requires a non-empty slug")
    now = utc_timestamp()
    head = _head_oid(identity)
    if head is None:
        raise WorkflowError("workflow begin requires HEAD^{commit}")
    candidate = _active_candidate_tree(identity)
    state: JsonObject = {
        "schemaVersion": 1,
        "repo": identity.as_dict(),
        "slug": normalized,
        "workflowId": uuid.uuid4().hex,
        "passStartOid": head,
        "activeCandidateTree": candidate,
        "intent": intent,
        "leadContextId": os.environ.get("CODEX_THREAD_ID"),
        "phase": "intake",
        "nextAction": "repo-context-forge",
        "repoContextForge": "pending",
        "advisorPreflight": {"source": None, "status": "pending", "findings": "pending", "reason": None},
        "preflight": "pending",
        "tdd": "pending",
        "productionCode": "pending",
        "implementation": "pending",
        "verification": "pending",
        "codeReview": {"status": "pending", "findings": "pending"},
        "finalReview": {"source": None, "status": "pending", "findings": "pending"},
        "createdAt": now,
        "updatedAt": now,
    }
    with mutation(identity, expected_candidate_tree=candidate) as transaction:
        return transaction.append(state, "begin", activate=True)


def _head_oid(identity: RepoIdentity) -> str | None:
    result = subprocess.run(["git", "-C", str(identity.root), "rev-parse", "HEAD"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _is_commit_oid(identity: RepoIdentity, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    result = subprocess.run(
        ["git", "-C", str(identity.root), "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == value


def _bind_review_to_tree(
    identity: RepoIdentity, state: JsonObject, document: dict[str, str] | None = None,
    head: str | None = None,
) -> ManifestWrite | None:
    """Create the lead-review tree binding and reopen independent final review."""
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state.pop("reviewManifestId", None); state.pop("reviewHead", None)
    try:
        document = document if document is not None else tree_manifest(identity)
    except RuntimeError:
        return None
    if head := head or _head_oid(identity): state["reviewHead"] = head
    write = manifest_write(str(state["workflowId"]), "lead-review-tree", document)
    state["reviewManifestId"] = write.manifest_id
    return write


def _stored_manifest(
    identity: RepoIdentity,
    state: JsonObject,
    field: str,
    transaction: LedgerMutation | None,
) -> dict[str, str] | None:
    value = state.get(field)
    if not isinstance(value, str) or not value:
        return None
    return transaction.manifest(value) if transaction is not None else read_manifest(identity, value)


def _tree_drift(
    identity: RepoIdentity,
    state: JsonObject,
    *,
    field: str,
    missing: str,
    stale: str,
    transaction: LedgerMutation | None = None,
) -> str | None:
    recorded = _stored_manifest(identity, state, field, transaction)
    if recorded is None:
        return missing
    try:
        current = tree_manifest(identity)
    except RuntimeError as exc:
        return f"{missing} (uncomputable: {exc})"
    return _manifest_drift(recorded, current, stale=stale)


def _manifest_drift(
    recorded: dict[str, str], current: dict[str, str], *, stale: str,
) -> str | None:
    difference = manifest_diff(recorded, current)
    if not any(difference.values()):
        return None
    named = "; ".join(f"{kind}={', '.join(paths)}" for kind, paths in difference.items() if paths)
    return f"{stale}: {named}"


def _binding_drift(
    identity: RepoIdentity,
    state: JsonObject,
    binding: str,
    transaction: LedgerMutation | None = None,
) -> str | None:
    field, missing, stale = {
        "review": ("reviewManifestId", MANIFEST_MISSING, MANIFEST_STALE),
        "quality-gate": ("qualityGateManifestId", QUALITY_GATE_MISSING, QUALITY_GATE_STALE),
    }[binding]
    return _tree_drift(
        identity, state, field=field, missing=missing, stale=stale, transaction=transaction,
    )


def _clear_verification(state: JsonObject) -> None:
    """Invalidate acceptance while retaining the prior evidence for audit and drift reporting."""
    state["verification"] = "pending"
    state.pop("verificationEvidence", None)
    state.pop("verificationLatestEvidence", None)
    state.pop("qualityGateEvidence", None)
    state.pop("qualityGateManifestId", None)


def _apply_step(
    identity: RepoIdentity,
    state: JsonObject,
    phase: str,
    status: str,
    findings: str | None = None,
    review_manifest: dict[str, str] | None = None,
    review_head: str | None = None,
) -> ManifestWrite | None:
    """Validated policy mutation shared by every transactional command."""
    if status not in STEP_STATUSES:
        raise ValueError(f"unsupported workflow status: {status}")
    if phase not in STEP_FIELDS and phase != "code-review":
        raise ValueError(f"unsupported workflow phase: {phase}")
    _require_open(state)
    if state.get("revalidation") and phase not in {"repo-context-forge", "verification", "code-review"}:
        raise WorkflowError(
            f"governance revalidation permits only context refresh, re-verification, and review; {phase} is closed"
        )
    state.pop("paused", None)
    # Retain executed verification while attack obligations remain pending.
    if phase != "verification":
        _require_predecessor(state, phase)
    if phase == "implementation" and status == "passed" and state.get("tdd") not in {"passed", "not-required"}:
        raise WorkflowIncomplete("implementation passed requires tdd passed or not-required")
    manifest: ManifestWrite | None = None
    if phase == "code-review":
        if findings not in FINDING_STATUSES:
            raise ValueError("code-review requires --findings pending, none, or addressed")
        state["codeReview"] = {"status": status, "findings": findings}
        state.pop("codeReviewEvidence", None)
        manifest = _bind_review_to_tree(identity, state, review_manifest, review_head)
    else:
        if findings is not None:
            raise ValueError(f"{phase} does not accept findings")
        field = STEP_FIELDS[phase]
        if phase == "verification":
            _clear_verification(state)
        else:
            state.pop(f"{field}Evidence", None)
        state[field] = status
    state["phase"] = phase
    state["nextAction"] = _derive_next_action(state)
    return manifest


def set_phase(
    identity: RepoIdentity,
    phase: str,
    status: str,
    *,
    findings: str | None = None,
    slug: str | None = None,
    workflow_id: str | None = None,
    expected_candidate_tree: str | None = None,
) -> JsonObject:
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _require_state(transaction.state)
        _require_instance(state, slug, workflow_id)
        manifest = _apply_step(identity, state, phase, status, findings)
        return _commit(
            transaction,
            state,
            f"set-{phase}",
            manifests=[manifest] if manifest else [],
        )


TDD_ACTIONS = {"reopen", "in-progress", "passed", "not-required"}


def _map_items(
    document: JsonObject | None, *, terminals: dict[str, JsonObject] | None = None,
) -> list[JsonObject] | None:
    if not isinstance(document, dict):
        return None
    value = document.get("behaviorMap")
    if value is None:
        inner = document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return behavior_map.runtime_items(value, terminals=terminals) if value is not None else None


def _linked_finding_items(
    transaction: LedgerMutation | None, document: JsonObject | None = None,
    *, items: list[JsonObject] | None = None, state: JsonObject | None = None,
) -> dict[tuple[str, str], dict[str, JsonObject]]:
    """Map items grouped by the recorded intake finding each sourceRef names."""
    by_ref: dict[tuple[str, str], dict[str, JsonObject]] = {}
    intakes: dict[str, set[str]] = {}
    for entry in (_map_items(document) or []) if items is None else items:
        for ref in entry.get("sourceRefs", []):
            if isinstance(ref, dict) and ref.get("type") == "finding":
                key = (str(ref.get("evidenceId")), str(ref.get("id")))
                if transaction is not None and key[0] not in intakes:
                    intake = transaction.evidence(key[0])
                    findings = intake.get("findings") if isinstance(intake, dict) else None
                    intakes[key[0]] = {
                        str(finding.get("id")) for finding in findings if isinstance(finding, dict)
                    } if (isinstance(findings, list)
                          and intake.get("workflowId") == transaction.state.get("workflowId")) else set()
                if transaction is not None and key[1] not in intakes[key[0]]:
                    raise WorkflowError(f"behavior {entry['id']} finding sourceRef is unrecorded, stale, or foreign")
                by_ref.setdefault(key, {})[str(entry["id"])] = entry
    for finding in (state or (transaction.state if transaction else {}) or {}).get("findingStates", []):
        keys = [(str(finding["intakeEvidenceId"]), str(finding["findingId"])),
                *((str(ref["evidenceId"]), str(ref["id"])) for ref in finding.get("observations", []))]
        linked = {identifier: item for key in keys for identifier, item in by_ref.get(key, {}).items()}
        for key in keys:
            if linked:
                by_ref[key] = linked
    return by_ref


def _require_owned_behavioral_findings(
    state: JsonObject, owned: dict[tuple[str, str], dict[str, JsonObject]],
) -> None:
    """A pending behavioral preflight finding is admitted only as a mapped attack obligation."""
    unowned = sorted(
        str(entry.get("findingId")) for entry in _preflight_finding_states(state)
        if _rides_the_map(entry)
        and (str(entry.get("intakeEvidenceId")), str(entry.get("findingId"))) not in owned
    )
    if unowned:
        raise WorkflowError(
            "pending behavioral finding(s) need an owning Behavior Map attack item "
            "(finding sourceRef) or a terminal disposition: " + ", ".join(unowned)
        )


def _mechanism_explanation(
    reference: object, read: Callable[[str], JsonObject | None], workflow_id: object,
) -> str | None:
    """Resolve existing diagnosis or disposition references without a second store."""
    seen: set[tuple[str, str]] = set()
    while isinstance(reference, dict):
        evidence_id, finding_id = reference.get("evidenceId"), reference.get("id")
        if not isinstance(evidence_id, str) or not isinstance(finding_id, str):
            return None
        key = (evidence_id, finding_id)
        if key in seen:
            return None
        seen.add(key)
        document = read(evidence_id)
        if not document or document.get("workflowId") != workflow_id:
            return None
        diagnosis = document.get("reassessment")
        if isinstance(diagnosis, str) and diagnosis.strip():
            return diagnosis.strip()
        reference = next((item.get("mechanism") for item in document.get("dispositions", [])
                          if item.get("finding_id") == finding_id), None)
    return reference.strip() if isinstance(reference, str) and reference.strip() else None


def commit_tdd(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    summary_doc: JsonObject | None,
    action: str | None,
    *,
    expected_evidence_id: str | None = None,
    opens_cycle: bool = False,
    tree_before: dict[str, str] | None = None,
    review_changed: bool = False,
    reassessed: frozenset[str] = frozenset(),
) -> tuple[JsonObject, str | None]:
    """Commit a TDD transition and its logical evidence under one transaction.

    `opens_cycle` is the caller's answer to the one question the committed
    action cannot carry: `reopen` is recorded both for a cycle-opening RED and
    for a GREEN regression, so the count is kept forward here rather than
    reconstructed from a history that cannot tell the two apart.
    """
    if action is not None and action not in TDD_ACTIONS:
        raise ValueError(f"unsupported tdd action: {action}")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("revalidation"):
            raise WorkflowError(TDD_CLOSED)
        if action is not None:
            _require_predecessor(state, "tdd")
            if not state.get("preflightEvidence"):
                raise WorkflowError("tdd requires recorded preflight evidence")
        if state.get("tddEvidence") != expected_evidence_id:
            raise WorkflowError("TDD evidence changed during the run; re-read and re-run the candidate")
        if summary_doc is not None:
            if summary_doc.get("workflowId") != state["workflowId"]:
                raise WorkflowError("TDD document belongs to another workflow instance")
            if tree_before is not None:
                run = summary_doc["runs"][-1]
                try:
                    drift = _manifest_drift(tree_before, tree_manifest(identity),
                                            stale="candidate changed during reassessment")
                    if _active_candidate_tree(identity) != run.get("candidateTree") and drift is None:
                        drift = "candidate tree changed during reassessment"
                except (OSError, RuntimeError) as exc:
                    drift = f"candidate could not be sampled at commit: {exc}"
                if drift:
                    run.update(valid=False, bindingError=drift)
                    prior = transaction.evidence(expected_evidence_id) or {
                        "workflowId": state["workflowId"],
                        "behaviorMap": _map_items(transaction.evidence(state.get("preflightEvidence"))),
                    }
                    summary_doc = {**prior, "runs": [*prior.get("runs", []), run],
                                   "updatedAt": utc_timestamp()}
                    action, opens_cycle = None, False
            terminals: dict[str, JsonObject] = {}
            items = _map_items(summary_doc, terminals=terminals)
            if items is None:
                items = _map_items(
                    transaction.evidence(state.get("preflightEvidence")), terminals=terminals,
                ) or []
            owned = _linked_finding_items(transaction, items=items)
            pending = set(behavior_map.unresolved(items, terminals=terminals))
            # Mutation may request proof from already-settled owners; closure
            # still judges current proof. Ownership cannot be moved away.
            for entry in state.get("findingStates", []):
                if isinstance(entry, dict) and entry.get("status") in {"fixed", "report-only"} and entry.get("kind") == "behavioral":
                    _behavioral_finding_closure(
                        str(entry.get("intakeEvidenceId")),
                        str(entry.get("findingId")), admit_pending=True,
                        require_green=entry.get("status") == "fixed", owned=owned,
                        terminals=terminals, pending=pending,
                    )
        mechanism_updates: list[JsonObject] = []
        if reassessed and summary_doc is not None:
            affected = _linked_finding_items(transaction, items=[item for item in items if item["id"] in reassessed])
            mechanism_updates = [entry for entry in state.get("findingStates", [])
                if entry.get("kind") == "behavioral" and _finding_unresolved(entry)
                and (str(entry["intakeEvidenceId"]), str(entry["findingId"])) in affected
                and _mechanism_explanation(entry.get("mechanismEvidence"), transaction.evidence,
                                           state["workflowId"]) != summary_doc.get("reassessment")]
            previous = _map_items(transaction.evidence(expected_evidence_id))
            if previous is None:
                previous = _map_items(transaction.evidence(state.get("preflightEvidence")))
            if action is None and json.dumps(items, sort_keys=True) == json.dumps(previous, sort_keys=True) and not mechanism_updates:
                return state, expected_evidence_id
        writes: list[EvidenceWrite] = []
        manifests: list[ManifestWrite] = []
        evidence_id: str | None = None
        if summary_doc is not None:
            if tree_before is not None:
                measured = manifest_write(str(state["workflowId"]), "tdd-tree", tree_before)
                manifests.append(measured)
                summary_doc["runs"][-1]["treeManifestId"] = measured.manifest_id
            write = evidence_write(str(state["workflowId"]), "tdd", summary_doc)
            writes.append(write)
            evidence_id = write.evidence_id
            state["tddEvidence"] = evidence_id
            for entry in mechanism_updates:
                prior = entry.get("mechanismEvidence")
                if prior:
                    entry.setdefault("mechanismHistory", []).append(prior)
                entry["mechanismEvidence"] = {"evidenceId": evidence_id, "id": entry["findingId"]}
        if action is not None:
            state.pop("paused", None)
        if opens_cycle:
            state["tddCycleCount"] = state.get("tddCycleCount", 0) + 1
        verification = transaction.evidence(state.get("verificationLatestEvidence"))
        if isinstance(verification, dict) and verification.get("runs"):
            manifest_id = state.get("qualityGateManifestId") or verification["runs"][-1].get("treeManifestId")
            try:
                current_tree = tree_manifest(identity)
            except RuntimeError as exc:
                raise WorkflowError(f"verification binding could not be sampled: {exc}") from exc
            if not manifest_id or transaction.manifest(manifest_id) != current_tree:
                _reset_downstream(state)
        if review_changed:
            _reset_reviews(state)
        if action == "reopen":
            state["tdd"] = "in-progress"
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
            _reset_downstream(state)
        elif action is not None:
            state["tdd"] = action
            state["phase"] = "implementation" if opens_cycle else "tdd"
            if opens_cycle:
                state["implementation"] = "in-progress"
        state["nextAction"] = _derive_next_action(state, summary_doc)
        return _commit(transaction, state, f"tdd-{action or 'annotated'}", evidence=writes, manifests=manifests), evidence_id


def annotate_tdd_evidence(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    summary_doc: JsonObject,
    *,
    expected_evidence_id: str | None = None,
    reassessed: frozenset[str] = frozenset(),
) -> tuple[JsonObject, str | None]:
    """Use the same binding/ownership transaction without changing lifecycle."""
    state, evidence_id = commit_tdd(
        identity, slug, workflow_id, summary_doc, None,
        expected_evidence_id=expected_evidence_id, reassessed=reassessed,
    )
    return state, evidence_id


def _candidate_tree(identity: RepoIdentity) -> str:
    return _active_candidate_tree(identity)


def _validate_disposition_context(identity: RepoIdentity, state: JsonObject, document: JsonObject) -> tuple[dict[str, str], str | None]:
    context = document.get("context")
    if not isinstance(context, dict) or context.get("workflowId") != state.get("workflowId"):
        raise WorkflowError("disposition context does not match the active workflow instance")
    manifest = tree_manifest(identity)
    if context.get("candidateTree") != _candidate_tree(identity):
        raise WorkflowError("disposition candidateTree does not match the current reviewable tree")
    return manifest, _head_oid(identity)


def _finding_unresolved(entry: JsonObject) -> bool:
    return (
        (entry.get("status") in {"pending", "accepted-for-proof"} and entry.get("material") is not False)
        or (entry.get("status") == "accepted-follow-up" and entry.get("material") is True)
        # A pending appeal waits for the advisor's one response; a recorded
        # disagreement is history: the lead's measured rejection stands.
        or entry.get("appealStatus") == "pending"
    )


def _stage_unresolved(state: JsonObject, stage: str, source: str, excluded: Sequence[JsonObject] = ()) -> bool:
    """Whether any registered finding of this stage and producer is still open."""
    return any(
        isinstance(entry, dict) and entry not in excluded and _finding_unresolved(entry)
        and ((entry.get("stage") == stage and entry.get("producer") == source)
             or any(ref.get("stage") == stage and ref.get("producer") == source
                    for ref in entry.get("observations", [])))
        for entry in state.get("findingStates") or []
    )


def commit_review(
    identity: RepoIdentity, slug: str, workflow_id: str | None,
    summary_doc: JsonObject, status: str, findings: str,
) -> tuple[JsonObject, str]:
    """Commit immutable review intake or an appended disposition."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if summary_doc.get("kind") == "intake":
            _require_predecessor(state, "code-review")
        write = evidence_write(str(state["workflowId"]), "code-review", summary_doc)
        manifest: ManifestWrite | None = None
        if summary_doc.get("kind") == "intake":
            intake = summary_doc.get("findings", [])
            reference = _register_finding_intake(transaction, state, write.evidence_id, summary_doc, {})
            repairs = [entry for entry in state.get("findingStates", [])
                       if int(entry.get("recurrence", 0)) >= 2 and _finding_unresolved(entry)]
            selected = [entry for entry in repairs if entry.get("repairOwner", {}).get("implementerContextId")
                        == summary_doc.get("implementationContextId")
                        and entry.get("repairOwner", {}).get("reviewerContextId")
                        == summary_doc.get("reviewContextId")]
            succession = summary_doc.get("repairSuccession")
            if succession is not None:
                _validate_disposition_context(identity, state, succession)
                successor = {"implementerContextId": summary_doc["implementationContextId"],
                             "reviewerContextId": summary_doc["reviewContextId"]}
                if (successor == succession["previousOwner"]
                        or successor["implementerContextId"] == successor["reviewerContextId"]):
                    raise WorkflowError("repair succession requires changed, distinct actual roles")
                selected = []
                for ref in succession["findings"]:
                    entry = _finding_state(state, ref["evidenceId"], ref["id"])
                    if (entry is None or entry not in repairs or entry in selected
                            or entry.get("kind") != "behavioral"
                            or entry.get("repairOwner") != succession["previousOwner"]):
                        raise WorkflowError("repair succession requires unique current recurring findings and their exact previous owner")
                    selected.append(entry)
                for entry in selected:
                    entry.setdefault("repairOwnerHistory", []).append({
                        "owner": entry["repairOwner"], "reviewEvidenceId": write.evidence_id,
                    })
                    entry["repairOwner"] = dict(successor)
                    entry.pop("repairReviewEvidence", None)
                    entry.pop("repairReviewedTree", None)
            if repairs and summary_doc.get("implementationContextId") and not selected:
                raise WorkflowError("second recurrence review must name a retained repair implementer")
            for entry in selected:
                owner = entry.get("repairOwner", {})
                implementer = owner.get("implementerContextId")
                reviewer = owner.get("reviewerContextId")
                if (not implementer or not reviewer or implementer == reviewer
                        or summary_doc.get("reviewContextId") != reviewer
                        or summary_doc.get("implementationContextId") != implementer):
                    raise WorkflowError("second recurrence requires retained reviewer repair and independent lead review")
                if any(_finding_state(state, write.evidence_id, finding["id"]) is entry for finding in intake):
                    entry.pop("repairReviewEvidence", None)
                    entry.pop("repairReviewedTree", None)
                else:
                    entry["repairReviewEvidence"] = write.evidence_id
                    entry["repairReviewedTree"] = _candidate_tree(identity)
            if not any(_finding_unresolved(entry) and entry.get("repairOwner")
                       for entry in state.get("findingStates", [])):
                state["reviewerContextId"] = summary_doc["reviewContextId"]
            unresolved = _stage_unresolved(state, "code-review", "code-review")
            if unresolved:
                manifest = _apply_step(identity, state, "code-review", "pending", "pending")
                if intake:
                    state["codeReviewIntakeEvidence"] = reference
            else:
                manifest = _apply_step(identity, state, "code-review", "passed", "none")
        else:
            summary_doc = _resolve_disposition_receipts(identity, transaction, state, summary_doc)
            review_manifest, review_head = _validate_disposition_context(identity, state, summary_doc)
            summary_doc = _linked_disposition_document(state, summary_doc, "code-review", "code-review")
            write = evidence_write(str(state["workflowId"]), "code-review", summary_doc)
            intake_id = str(summary_doc["intakeEvidenceId"])
            unresolved = _apply_finding_dispositions(
                transaction, state, intake_id, summary_doc["dispositions"], "code-review", "code-review",
                write.evidence_id,
            )
            status, findings = ("pending", "pending") if unresolved else ("passed", "addressed")
            if _allows_next(state, "tdd") and _allows_next(state, "verification"):
                manifest = _apply_step(
                    identity, state, "code-review", status, findings, review_manifest, review_head,
                )
            else:
                status, findings = "pending", "pending"
                state["codeReview"] = {"status": status, "findings": findings}
                state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
        state["codeReviewEvidence"] = write.evidence_id
        state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, "record-code-review", evidence=[write],
                       manifests=[manifest] if manifest else []), write.evidence_id


_NO_CAS = object()


def commit_evidence_phase(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    phase: str,
    evidence_doc: JsonObject,
    *,
    status: str = "passed",
    expected_evidence_id: object = _NO_CAS,
) -> tuple[JsonObject, str]:
    """Commit validated producer evidence and its workflow transition."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        field = STEP_FIELDS[phase]
        latest_field = f"{field}LatestEvidence"
        if expected_evidence_id is not _NO_CAS and state.get(latest_field) != expected_evidence_id:
            raise WorkflowError(f"{phase} evidence changed during the run; re-read and re-run the command")
        if phase == "preflight":
            _require_owned_behavioral_findings(state, _linked_finding_items(transaction, evidence_doc))
        _apply_step(identity, state, phase, status)
        write = evidence_write(str(state["workflowId"]), phase, evidence_doc)
        state[latest_field] = write.evidence_id
        if status == "passed":
            state[f"{field}Evidence"] = write.evidence_id
            state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction,
            state,
            f"record-{phase}",
            evidence=[write],
        ), write.evidence_id


def record_base_oid(identity: RepoIdentity, slug: str, workflow_id: str | None, oid: str) -> JsonObject:
    """Record the pass's base commit OID, immutable for the life of the pass.

    The Repo Context Forge packet owns base resolution; this recorder only
    stores its resolved commit so every later per-edit measurement reads one
    coherent base. The first recorded OID wins: a rerun that resolves the same
    commit is idempotent, and a differing rerun keeps the original — the
    caller reports that conflict, because a moving base would make successive
    per-edit growth measurements incoherent.
    """
    if not _is_commit_oid(identity, oid):
        raise ValueError("base OID must be a canonical commit OID for this repository")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        existing = state.get("baseOid")
        if isinstance(existing, str) and existing:
            return state
        state["baseOid"] = oid
        return _commit(transaction, state, "record-base-oid")


PASS_START_SNAPSHOT_FIELDS = ("indexRepo", "indexPath", "analysisRepo", "sourceCommit", "indexedTree", "recordedAt")


def record_pass_start_snapshot(
    identity: RepoIdentity, slug: str, workflow_id: str | None,
    snapshot: JsonObject | None = None, gap: str | None = None,
) -> JsonObject:
    """Record the index this pass started against, immutable for the life of the pass.

    The advisory diffs the current candidate against the index built at intake,
    so this names that one: its GitNexus selector, index directory, analysis
    checkout, source commit, and the tree the index was built from. Revalidation
    re-indexes the dirty candidate and records its own identity in that run's
    evidence, which is a different graph and never this baseline — so the first
    recorded snapshot wins here exactly as `baseOid` does, and a differing rerun
    is reported by the caller rather than absorbed.

    A partial identity is never stored as a snapshot: a consumer cannot tell a
    missing field from an absent baseline, and inventing one would bind the
    advisory to a snapshot nothing measured. Its measured reason is recorded as
    a gap instead, and unlike the snapshot a gap is replaceable — a later intake
    that does resolve an identity is the pass's baseline, where a recorded
    snapshot is already the answer and stands.
    """
    if (snapshot is None) == (gap is None):
        raise ValueError("record either a pass-start snapshot or its measured gap")
    recorded = None
    if snapshot is not None:
        missing = [name for name in PASS_START_SNAPSHOT_FIELDS if not str(snapshot.get(name) or "").strip()]
        if missing:
            raise ValueError("pass-start snapshot is missing " + ", ".join(missing))
        recorded = {name: str(snapshot[name]).strip() for name in PASS_START_SNAPSHOT_FIELDS}
    elif not str(gap).strip():
        raise ValueError("a pass-start snapshot gap requires its measured reason")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        existing = state.get("passStartSnapshot")
        if isinstance(existing, dict) and existing:
            return state
        if recorded is None:
            state["passStartSnapshotGap"] = str(gap).strip()
        else:
            state["passStartSnapshot"] = recorded
            state.pop("passStartSnapshotGap", None)
        return _commit(transaction, state, "record-pass-start-snapshot")


def _verification_key(run: JsonObject) -> str:
    return "quality-gate" if run.get("kind") == "quality-gate" else f"generic:{run.get('command')}"


BASELINE_PROOF_QUALITIES = frozenset({"baseline-passed", "operation-succeeded"})


def run_recorded_baseline(run: object) -> bool:
    """The run recorded a baseline settlement for its item: a runner's
    ``baseline-passed`` or a non-runner ``operation-succeeded`` redProof."""
    proof = run.get("redProof") if isinstance(run, dict) else None
    return isinstance(proof, dict) and proof.get("quality") in BASELINE_PROOF_QUALITIES


def execution_digest(run: object) -> str | None:
    """Stable identity of a stored execution across reference spellings and
    cumulative evidence-document copies: the canonical run record."""
    if not isinstance(run, dict):
        return None
    return hashlib.sha256(
        json.dumps(run, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execution_receipt(identity: RepoIdentity, state: JsonObject, reference: str,
                      transaction: LedgerMutation | None = None) -> tuple[JsonObject, dict[str, str]]:
    """Resolve an actual execution at its original target; references never execute."""
    evidence_id, separator, index = reference.rpartition(":")
    document = (transaction.evidence(evidence_id) if transaction is not None
                else evidence_document(identity, evidence_id)) if separator else None
    if (not isinstance(document, dict) or document.get("workflowId") != state["workflowId"]
            or not index.isdecimal() or int(index) >= len(document.get("runs", []))):
        raise WorkflowError("execution reference requires an owned evidence-id:run-index")
    run = document["runs"][int(index)]
    manifest = _stored_manifest(identity, run, "treeManifestId", transaction)
    try:
        current_tree = tree_manifest(identity)
    except RuntimeError as exc:
        raise WorkflowError(f"execution reference could not be sampled: {exc}") from exc
    if (manifest is None or manifest != current_tree or run.get("bindingError")
            or run.get("timedOut") or ("outputTail" not in run
                and not (run.get("sourceReference") and run.get("testId")))):
        raise WorkflowError("execution reference is stale, unbound, incomplete or not an executed receipt")
    return run, manifest


def commit_verification(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    run: JsonObject,
    *,
    tree_before: dict[str, str] | None,
) -> tuple[JsonObject, str, JsonObject]:
    """Merge one completed run with the verification evidence current at commit.

    The runner hands over its finished run and the reviewable-tree manifest it
    sampled before the command started; nothing is read ahead of execution, so
    a competing completion that landed meanwhile is merged rather than refused
    and recorded order is completion order. A valid run whose reviewable tree
    differs at commit is retained invalid, naming the drift: its result
    describes another tree. `tree_before` is None only when the runner could
    not sample it, and that run already carries its reason as invalid.
    Readiness and the typed-gate binding derive from the merged runs, so a
    generic completion can neither drop a valid binding nor revive one a later
    typed run invalidated. The binding is kept only while its manifest still
    describes the tree: a valid run measures one tree and reports no drift, so
    run results alone never notice that the gate's tree has since moved on.
    """
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        prior = transaction.evidence(state.get("verificationLatestEvidence"))
        prior_runs = (
            prior["runs"]
            if isinstance(prior, dict) and prior.get("workflowId") == state["workflowId"]
            and isinstance(prior.get("runs"), list)
            else []
        )
        prior_manifest_id = state.get("qualityGateManifestId")
        run = dict(run)
        typed = run.get("kind") == "quality-gate"
        try:
            current: dict[str, str] | None = tree_manifest(identity)
        except RuntimeError as exc:
            current, sampling_error = None, f"the reviewable tree could not be sampled at commit: {exc}"
        if tree_before is not None and run.get("valid") is True:
            drift = (
                _manifest_drift(
                    tree_before, current,
                    stale=f"reviewable tree changed during the {'quality-gate' if typed else 'verification'} run",
                )
                if current is not None else sampling_error
            )
            if drift is not None:
                run["valid"] = False
                run["bindingError"] = drift
        runs = [*prior_runs, run]
        manifests: list[ManifestWrite] = []
        run["runIndex"] = len(prior_runs)
        if tree_before is not None:
            measured = manifest_write(str(state["workflowId"]), "verification-tree", tree_before)
            manifests.append(measured)
            run["treeManifestId"] = measured.manifest_id
        replacement = run.get("replaces")
        if replacement is not None:
            ref, separator, index_text = str(replacement).rpartition(":")
            referenced = transaction.evidence(ref) if separator else None
            if (not isinstance(referenced, dict) or referenced.get("workflowId") != state["workflowId"]
                    or not index_text.isdecimal() or typed):
                raise WorkflowError("replacement requires a current failed generic evidence-id:run-index")
            source_runs = referenced.get("runs", [])
            index = int(index_text)
            if index >= len(source_runs) or source_runs != prior_runs[:len(source_runs)]:
                raise WorkflowError("replacement evidence is stale or outside current verification history")
            failed = source_runs[index]
            key = _verification_key(failed)
            latest_index = max(i for i, item in enumerate(prior_runs) if _verification_key(item) == key)
            if (failed.get("kind") != "generic" or failed.get("valid") is True or index != latest_index
                    or any(item.get("replacedKey") == key and item.get("valid") is True for item in prior_runs[index + 1:])):
                raise WorkflowError("replacement no longer names its active failed generic invocation")
            if (not failed.get("treeManifestId") or tree_before is None
                    or transaction.manifest(failed["treeManifestId"]) != tree_before):
                raise WorkflowError("replacement target does not match the failed invocation")
            if run.get("valid") is True:
                run["replacedKey"] = key
        latest: dict[str, bool] = {}
        for item in runs:
            if item.get("valid") is True and isinstance(item.get("replacedKey"), str):
                latest.pop(item["replacedKey"], None)
            latest[_verification_key(item)] = item.get("valid") is True
        status = "passed" if latest and all(latest.values()) else "pending"
        _apply_step(identity, state, "verification", status)
        if typed and run["valid"] is True:
            manifest = manifest_write(str(state["workflowId"]), "quality-gate-tree", tree_before)
            manifests.append(manifest)
            quality_manifest_id: str | None = manifest.manifest_id
        elif (
            latest.get("quality-gate") is True
            and isinstance(prior_manifest_id, str)
            and current is not None
            and transaction.manifest(prior_manifest_id) == current
        ):
            quality_manifest_id = prior_manifest_id
        else:
            quality_manifest_id = None
        document: JsonObject = {
            "schemaVersion": 1,
            "slug": state["slug"],
            "workflowId": state["workflowId"],
            "status": status,
            "runs": runs,
            "updatedAt": utc_timestamp(),
        }
        if quality_manifest_id is not None:
            document["qualityGateManifestId"] = quality_manifest_id
        write = evidence_write(str(state["workflowId"]), "verification", document)
        state["verificationLatestEvidence"] = write.evidence_id
        if quality_manifest_id is not None:
            state["qualityGateEvidence"] = write.evidence_id
            state["qualityGateManifestId"] = quality_manifest_id
        else:
            state.pop("qualityGateEvidence", None)
            state.pop("qualityGateManifestId", None)
        if status == "passed":
            state["verificationEvidence"] = write.evidence_id
            state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction,
            state,
            "record-verification",
            evidence=[write],
            manifests=manifests,
        ), write.evidence_id, run


def evidence_document(identity: RepoIdentity, evidence_id: str | None) -> JsonObject | None:
    if not evidence_id:
        return None
    envelope = read_evidence(identity, evidence_id)
    document = envelope.get("document") if isinstance(envelope, dict) else None
    return document if isinstance(document, dict) else None


def evidence_record(identity: RepoIdentity, evidence_id: str) -> JsonObject | None:
    return read_evidence(identity, evidence_id)


def _graph_candidate_ready(
    document: object, candidate: str, *, slug: object, workflow_id: object,
) -> bool:
    if (
        not isinstance(document, dict)
        or type(document.get("schemaVersion")) is not int
        or document.get("schemaVersion") != 1
        or document.get("slug") != slug
        or document.get("workflowId") != workflow_id
    ):
        return False
    try:
        validate_advisor_projection(
            document.get("advisorProjection"), candidate_tree=candidate,
        )
    except ValueError:
        return False
    return True


def _register_finding_intake(
    transaction: LedgerMutation, state: JsonObject, intake_id: str, intake: JsonObject,
    intakes: dict[str, JsonObject],
) -> str:
    """Register new obligations while retaining identical pending identities."""
    finding_states = state.setdefault("findingStates", [])
    if not isinstance(finding_states, list):
        raise WorkflowError("recorded finding states are corrupt")
    # Read immutable observations once; the state carries only their identities.
    observed: list[tuple[JsonObject, JsonObject, JsonObject, JsonObject]] = []
    latest: dict[tuple[str, str], JsonObject] = {}
    for entry in finding_states:
        # Nonbehavioral settled findings cannot match a behavioral signature.
        # Skip unrelated namespaces/IDs before reading their immutable intakes.
        if (entry.get("kind") != "behavioral" and not entry.get("observations")
                and not any(item["id"] == entry["findingId"]
                            and intake["stage"] == entry["stage"] and intake["producer"] == entry["producer"]
                            or item.get("priorFinding") == {"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]}
                            for item in intake["findings"])):
            continue
        root = entry.get("canonicalFinding") or {
            "evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"],
        }
        refs = [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]},
                *entry.get("observations", [])]
        for ref in refs:
            reference = str(ref["evidenceId"])
            if reference not in intakes:
                previous = transaction.evidence(reference)
                if not isinstance(previous, dict) or previous.get("workflowId") != state["workflowId"]:
                    raise WorkflowError("recorded finding intake is corrupt or foreign")
                intakes[reference] = previous
            document = intakes[reference]
            for finding in document["findings"]:
                if finding["id"] == ref["id"]:
                    observed.append((root, ref, document, finding))
                    break
        latest[(str(root["evidenceId"]), str(root["id"]))] = entry
    references: set[str] = set()
    pending: list[tuple[JsonObject, JsonObject, bool]] = []
    for item in intake["findings"]:
        explicit = item.get("priorFinding")
        matches: set[tuple[str, str]] = set()
        for root, ref, document, finding in observed:
            same_id = (item["id"] == finding["id"]
                       and all(document.get(k) == intake.get(k) for k in ("producer", "stage")))
            same_claim = (item["kind"] == finding["kind"] == "behavioral"
                          and str(item["claim"]).strip() == str(finding["claim"]).strip())
            linked = explicit == {"evidenceId": ref["evidenceId"], "id": ref["id"]}
            if (explicit and linked) or (not explicit and (same_id or same_claim)):
                matches.add((str(root["evidenceId"]), str(root["id"])))
        if explicit and not matches:
            raise WorkflowError("priorFinding is unrecorded or belongs to another workflow")
        if len(matches) > 1:
            raise WorkflowError("ambiguous finding identity; reference an existing finding with priorFinding")
        prior = latest[next(iter(matches))] if matches else None
        if prior and prior.get("kind") == "behavioral" and prior.get("material") is True and (
            item["kind"] != "behavioral" or item["material"] is not True
        ):
            raise WorkflowError("a material behavioral finding requires a measured disposition, not demotion")
        if prior and prior.get("status") in {"pending", "accepted-for-proof", "accepted-follow-up"}:
            reference = str(prior["intakeEvidenceId"])
            if (prior["findingId"] != item["id"]
                    or any(prior.get(k) != intake.get(k) for k in ("producer", "stage"))
                    or not any(finding["id"] == item["id"] and finding["kind"] == item["kind"]
                               for finding in intakes[reference]["findings"])):
                reference = intake_id
            prior["material"] = prior["material"] or item["material"]
            if item["kind"] == "behavioral":
                prior["kind"] = "behavioral"
            pending.append((prior, item, any(all(finding.get(k) == item.get(k) for k in ("id", "claim", "kind", "material"))
                       and all(document.get(k) == intake.get(k) for k in ("producer", "stage"))
                       for root, ref, document, finding in observed
                       if (str(root["evidenceId"]), str(root["id"])) in matches)))
        else:
            reference = intake_id
            entry = {
                "producer": intake["producer"], "stage": intake["stage"],
                "intakeEvidenceId": intake_id, "findingId": item["id"],
                "material": item["material"], "kind": item["kind"], "status": "pending",
            }
            if prior:
                root = next(iter(matches))
                entry["canonicalFinding"] = {"evidenceId": root[0], "id": root[1]}
                if item["kind"] == "behavioral":
                    entry["recurrence"] = int(prior.get("recurrence", 0)) + (prior.get("kind") == "behavioral" and prior.get("status") == "fixed")
                    for field in ("mechanismEvidence", "repairOwner"):
                        if prior.get(field):
                            entry[field] = prior[field]
            finding_states.append(entry)
        current = prior if prior and prior.get("status") in {"pending", "accepted-for-proof", "accepted-follow-up"} else entry
        root = current.get("canonicalFinding") or {
            "evidenceId": current["intakeEvidenceId"], "id": current["findingId"],
        }
        latest[(str(root["evidenceId"]), str(root["id"]))] = current
        observed.append((root, {"evidenceId": intake_id, "id": item["id"]}, intake, item))
        references.add(reference)
    reference = next(iter(references)) if len(references) == 1 else intake_id
    for prior, item, unchanged in pending:
        if reference == intake_id or intake["producer"] == "code-review" or not unchanged:
            prior.setdefault("observations", []).append({"evidenceId": intake_id, "id": item["id"],
                                                        "producer": intake["producer"], "stage": intake["stage"]})
    for current in latest.values():
        owner = current.get("repairOwner", {})
        if (_finding_unresolved(current) and int(current.get("recurrence", 0)) >= 2
                and (not owner.get("implementerContextId") or not owner.get("reviewerContextId")
                     or owner["implementerContextId"] == owner["reviewerContextId"])):
            current.pop("repairOwner", None)
            lead = state.get("leadContextId") or os.environ.get("CODEX_THREAD_ID")
            reviewer = state.get("reviewerContextId")
            if not reviewer or reviewer == lead:
                review = transaction.evidence(state.get("codeReviewIntakeEvidence") or state.get("codeReviewEvidence"))
                reviewer = review.get("reviewContextId") if review else None
            if (not reviewer or reviewer == lead) and intake["producer"] == "code-review":
                reviewer = intake.get("reviewContextId")
            if reviewer and lead and reviewer != lead:
                state["reviewerContextId"] = reviewer
                current["repairOwner"] = {
                    "implementerContextId": reviewer, "reviewerContextId": lead,
                }
    return reference


def record_advisor_result(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    stage: str,
    source: str,
    verdict: str,
    *,
    findings: str | None = None,
    reason: str | None = None,
    design: JsonObject | None = None,
    intake: JsonObject | None = None,
    expected_candidate_tree: str | None = None,
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    if findings not in {None, "pending"}:
        raise ValueError("advisor-result records findings=pending; disposition findings with advisor-disposition")
    if stage == "final" and verdict == "context-mismatch" and intake is None:
        raise ValueError("final context-mismatch requires the advisor finding envelope")
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        writes: list[EvidenceWrite] = []
        intake_write: EvidenceWrite | None = None
        intake_reference: str | None = None
        intakes: dict[str, JsonObject] = {}
        if intake is not None and any(intake.get(field) != expected for field, expected in (
            ("workflowId", state["workflowId"]), ("stage", stage),
            ("producer", source), ("verdict", verdict),
        )):
            raise WorkflowError("advisor finding intake does not match this workflow result")
        replayed_design = False
        if design is not None:
            # The design is a falsifiable hypothesis: a deepened declaration is
            # recorded append-only in the same pass, never a reason to restart.
            candidate = evidence_write(str(state["workflowId"]), "governed-design", design)
            existing_id = state.get("governedDesignEvidence")
            if isinstance(existing_id, str) and transaction.evidence(existing_id) == design:
                replayed_design = True
            else:
                state["governedDesignEvidence"] = candidate.evidence_id
                writes.append(candidate)
        if stage == "preflight":
            if state.get("revalidation"):
                raise WorkflowError(PREFLIGHT_CLOSED)
            if not _allows_next(state, "repo-context-forge"):
                raise WorkflowIncomplete("advisor-preflight requires repo-context-forge")
            if source != "codex-advisor":
                raise ValueError("preflight advisor source must be codex-advisor")
            if verdict not in {"completed", "unavailable"}:
                raise ValueError("preflight verdict must be completed or unavailable")
            measured_reason = str(reason or "").strip() or None
            if verdict == "unavailable" and not measured_reason:
                raise ValueError("preflight unavailable requires --reason")
            recorded_reason = measured_reason if verdict == "unavailable" else None
            if intake is not None:
                intake_write = evidence_write(
                    str(state["workflowId"]), "finding-intake-preflight", intake,
                )
                writes.append(intake_write)
                intake_reference = _register_finding_intake(
                    transaction, state, intake_write.evidence_id, intake, intakes,
                )
            current = state.get("advisorPreflight")
            if intake is None and replayed_design and isinstance(current, dict) and all((
                current.get("findings") == ("pending" if _stage_unresolved(state, stage, source) else "none"),
                current.get("source") == source,
                current.get("status") == verdict,
                current.get("reason") == recorded_reason,
            )):
                return state
            state.pop("paused", None)
            state["advisorPreflight"] = {
                "source": source,
                "status": verdict,
                "findings": "pending" if _stage_unresolved(state, stage, source) else "none",
                "reason": recorded_reason,
                **({"intakeEvidence": intake_reference} if intake_write is not None else {}),
            }
            state["phase"] = "advisor-preflight"
        elif stage == "final":
            state.pop("paused", None)
            if state.get("nextAction") != "appeal-final-review":
                _require_predecessor(state, "final-review")
            if drift := _binding_drift(identity, state, "review", transaction):
                raise WorkflowError(f"the reviewed tree changed after the lead review: {drift}")
            if drift := _binding_drift(identity, state, "quality-gate", transaction):
                raise WorkflowError(f"the quality gate did not cover the current tree: {drift}")
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            if verdict == "context-mismatch":
                if intake is not None:
                    mismatch = evidence_write(
                        str(state["workflowId"]), "finding-context-mismatch-final", intake,
                    )
                    writes.append(mismatch)
                    state["finalReviewContextMismatchEvidence"] = mismatch.evidence_id
            else:
                record = state.get("finalReview")
                finding_states = state.setdefault("findingStates", [])
                if not isinstance(finding_states, list):
                    raise WorkflowError("recorded finding states are corrupt")
                rejected = [
                    entry for entry in finding_states
                    if isinstance(entry, dict)
                    and entry.get("status") == "rejected-with-evidence"
                    and entry.get("appealStatus") == "pending"
                ]
                if state.get("finalAppealConsumed") and (
                    rejected or isinstance(record, dict) and record.get("status") != "pending"
                ):
                    raise WorkflowError("final appeal already consumed")
                correction = _stage_unresolved(state, stage, source, rejected)
                if rejected and correction:
                    raise WorkflowError("final appeal is blocked by unresolved final-review work")
                legacy_recovery = isinstance(record, dict) and record.get("source") == source and (
                    record.get("status"), record.get("findings"), "intakeEvidence" in record
                ) == ("fix-before-commit", "addressed", False)
                if not rejected and not legacy_recovery and isinstance(record, dict) and record.get("status") != "pending" and (
                    not state.get("finalReviewContextMismatchEvidence") or correction):
                    raise WorkflowError("final review result already recorded for the current candidate")
                if rejected:
                    if intake is None:
                        raise WorkflowError("final appeal requires the advisor finding envelope")
                    appeal_write = evidence_write(str(state["workflowId"]), "finding-appeal-final", intake)
                    writes.append(appeal_write)
                    responses = {str(item["id"]): item for item in intake["findings"]}
                    rejected_ids = {str(entry["dispositionFindingId"]) for entry in rejected}
                    for entry in rejected:
                        response = responses.get(str(entry["dispositionFindingId"]))
                        if response is not None and response.get("material") is True:
                            # A material re-raise carries a new measurement: the
                            # finding reopens for one more lead disposition, and
                            # that second measured disposition stands.
                            prior = entry.get("dispositionEvidenceId")
                            if prior:
                                entry.setdefault("dispositionHistory", []).append({
                                    "evidenceId": prior,
                                    "status": entry.get("status"),
                                    "supersededBy": appeal_write.evidence_id,
                                })
                            entry["appealStatus"] = "disagreement"
                            entry["status"] = "pending"
                            entry["material"] = True
                        else:
                            entry["appealStatus"] = "conceded"
                        entry["appealEvidenceId"] = appeal_write.evidence_id
                    new_findings = [item for item in intake["findings"] if str(item["id"]) not in rejected_ids]
                    if new_findings:
                        derived_intake: JsonObject = {
                            "schemaVersion": 1, "workflowId": state["workflowId"], "stage": stage,
                            "producer": source, "verdict": verdict, "findings": new_findings,
                            "sourceAppealEvidenceId": appeal_write.evidence_id,
                        }
                        intake_write = evidence_write(str(state["workflowId"]), "finding-intake-final", derived_intake)
                        writes.append(intake_write)
                        intake_reference = _register_finding_intake(
                            transaction, state, intake_write.evidence_id, derived_intake, intakes,
                        )
                    if not isinstance(record, dict):
                        raise WorkflowError("final appeal review state is corrupt")
                    disposition = transaction.evidence(rejected[0]["dispositionEvidenceId"])
                    reference = str(disposition["intakeEvidenceId"])
                    original = intakes[reference] if reference in intakes else transaction.evidence(reference)
                    if not isinstance(original, dict): raise WorkflowError("final appeal intake is corrupt")
                    record.update({"source": source, "status": original["verdict"], "intakeEvidence": reference,
                                   "appealEvidence": appeal_write.evidence_id, "appealVerdict": verdict})
                    if intake_write is not None:
                        record["intakeEvidence"] = intake_reference
                    record["findings"] = "pending" if _stage_unresolved(state, stage, source) else "addressed"
                    state["finalAppealConsumed"] = True
                else:
                    if intake is not None:
                        intake_write = evidence_write(
                            str(state["workflowId"]), "finding-intake-final", intake,
                        )
                        writes.append(intake_write)
                        intake_reference = _register_finding_intake(
                            transaction, state, intake_write.evidence_id, intake, intakes,
                        )
                    state.pop("finalAppealConsumed", None)
                    state["finalReview"] = {
                        "source": source, "status": verdict,
                        "findings": "pending" if _stage_unresolved(state, stage, source) else "none",
                        **({"intakeEvidence": intake_reference} if intake_write is not None else {}),
                    }
                state.pop("finalReviewContextMismatchEvidence", None)
                state["phase"] = "final-review"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction, state, f"advisor-{stage}-result", evidence=writes,
        )


def _require_open(state: JsonObject) -> None:
    if state.get("phase") == "complete" and not state.get("revalidation"):
        raise WorkflowError("workflow is terminal after completion; begin a new pass")


def _require_instance(state: JsonObject, slug: str | None, workflow_id: str | None) -> None:
    if slug is not None and state.get("slug") != safe_slug(str(slug)):
        raise WorkflowError(SLUG_MISMATCH)
    if workflow_id is not None and instance_id(state) != workflow_id:
        raise WorkflowError(INSTANCE_MISMATCH)


def _active_for_slug(state: JsonObject | None, slug: str) -> JsonObject:
    value = _require_state(state)
    _require_open(value)
    _require_instance(value, slug or "", None)
    return value


def _bound_instance_state(state: JsonObject | None, slug: str, workflow_id: str | None) -> JsonObject:
    value = _active_for_slug(state, slug)
    if instance_id(value) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    _require_instance(value, None, workflow_id or "")
    return value


def bound_state(identity: RepoIdentity, slug: str) -> JsonObject:
    return _active_for_slug(read_workflow(identity), slug)


def instance_id(state: JsonObject) -> str | None:
    value = state.get("workflowId")
    return value if isinstance(value, str) and value else None


def pause(identity: RepoIdentity, slug: str, workflow_id: str | None, reason: str, *,
          expected_candidate_tree: str | None = None) -> JsonObject:
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("pause requires a non-empty --reason")
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        state["paused"] = {"reason": cleaned, "at": utc_timestamp()}
        return _commit(transaction, state, "pause")


def _behavioral_finding_closure(
    intake_id: str, finding_id: str,
    *,
    owned: dict[tuple[str, str], dict[str, JsonObject]],
    terminals: dict[str, JsonObject],
    pending: set[str],
    admit_pending: bool = False,
    require_green: bool = True,
) -> None:
    """Judge current owning proof; mutation admission keeps reassessment reachable.

    Only existing fixed/report-only owners use admit_pending. It preserves
    ownership, not a closure verdict; a new disposition always uses strict
    proof, and completion re-judges all settled findings against the current map.
    """
    linked = owned.get((intake_id, finding_id), {})
    if not linked:
        raise WorkflowError(
            f"behavioral fixed for {finding_id} requires an owning Behavior Map "
            "attack item carrying its finding sourceRef"
        )
    for identifier, entry in linked.items():
        if entry.get("status") == "superseded" and str(
            terminals[str(entry["id"])].get("id")
        ) not in linked:
            raise WorkflowError(
                f"finding {finding_id} loses its owning attack: {identifier} is "
                "superseded by an item without the finding sourceRef; keep the "
                "finding's domain owned or re-disposition it explicitly"
            )
    if admit_pending and any(
        entry.get("revalidationRequired") or (
            entry.get("status") == "already-satisfied" and behavior_map.producer_proved(entry)
        ) for entry in linked.values()
    ):
        # Reassessment may temporarily remove all current proof, or finish as
        # a baseline that cannot sustain fixed. Strict closure still blocks it;
        # measured correction must remain possible without fabricating RED.
        return
    if not require_green:
        proved = [
            identifier for identifier, entry in linked.items()
            if behavior_map.producer_proved(terminals[str(entry["id"])])
        ]
        if not proved:
            raise WorkflowError(
                f"behavioral report-only for {finding_id} requires an owning attack the tdd "
                "producer proved (GREEN, or a recorded baseline); unproved owners: "
                + ", ".join(sorted(linked))
            )
        return
    not_green = sorted(
        identifier for identifier, entry in linked.items()
        if not (admit_pending and entry.get("status") in {"pending", "red"})
        and (identifier in pending
             or entry.get("status") not in behavior_map.PROOF_STATUSES | {"superseded"}
             and not (entry.get("status") == "already-satisfied"
                      and (entry.get("kind") == "preservation" or behavior_map.producer_proved(entry))))
    )
    if not_green:
        raise WorkflowError(
            f"behavioral fixed for {finding_id} requires linked GREEN or producer-proved item(s): "
            + ", ".join(not_green)
        )
    if not any(
        behavior_map.green_through_red(entry) and identifier not in pending
        for identifier, entry in linked.items()
    ):
        raise WorkflowError(
            f"behavioral fixed for {finding_id} requires at least one owning attack proved on "
            "the current candidate (GREEN through its recorded RED); "
            "a baseline alone demonstrates no occurrence"
        )


def _finding_state_blockers(state: JsonObject) -> list[str]:
    states = state.get("findingStates", [])
    if not isinstance(states, list):
        return ["finding lifecycle evidence is corrupt"]
    unresolved = [f"{entry.get('stage')}:{entry.get('findingId')}" for entry in states
                  if isinstance(entry, dict) and _finding_unresolved(entry)]
    result: list[str] = []
    if state.get("finalReviewContextMismatchEvidence"):
        result.append("final-review context mismatch requires re-consultation")
    if unresolved:
        result.append("pending findings: " + ", ".join(unresolved))
    return result


def correction_blockers(
    identity: RepoIdentity, state: JsonObject, *, items: list[JsonObject] | None = None,
    terminals: dict[str, JsonObject] | None = None,
) -> list[str]:
    if terminals is None:
        terminals = {}
    if items is None:
        items = _recorded_items(identity, state, terminals=terminals)
    if not terminals and items:
        terminals = behavior_map.terminal_items(items)
    pending = behavior_map.unresolved(items, terminals=terminals)
    return (["unresolved Behavior Map items: " + ", ".join(pending)] if pending else []) + _finding_proof_blockers(
        None, state, items=items, terminals=terminals, pending=set(pending),
    )


def _finding_proof_blockers(
    transaction: LedgerMutation | None, state: JsonObject, *, items: list[JsonObject] | None = None,
    terminals: dict[str, JsonObject] | None = None, pending: set[str] | None = None,
) -> list[str]:
    states = state.get("findingStates", [])
    if not isinstance(states, list):
        return ["finding lifecycle evidence is corrupt"]
    if terminals is None:
        terminals = {}
    if items is None:
        items = _map_items(transaction.evidence(state.get("tddEvidence")), terminals=terminals)
        if items is None:
            items = _map_items(transaction.evidence(state.get("preflightEvidence")), terminals=terminals) or []
    if not terminals and items:
        terminals = behavior_map.terminal_items(items)
    if pending is None:
        pending = set(behavior_map.unresolved(items, terminals=terminals))
    owned = _linked_finding_items(transaction, items=items, state=state)
    blockers: list[str] = []
    for entry in states:
        if isinstance(entry, dict) and entry.get("status") in {"fixed", "report-only"} and entry.get("kind") == "behavioral":
            try:
                _behavioral_finding_closure(
                    str(entry.get("intakeEvidenceId")), str(entry.get("findingId")),
                    require_green=entry.get("status") == "fixed", owned=owned,
                    terminals=terminals, pending=pending,
                )
            except WorkflowError as exc:
                blockers.append(str(exc))
    return blockers


def _disposition_evidence(
    state: JsonObject, finding_state: JsonObject, stage: str, producer: str,
) -> str | None:
    current = finding_state.get("dispositionEvidenceId")
    if isinstance(current, str) and current:
        return current
    if producer == "code-review":
        legacy = state.get("codeReviewEvidence")
    else:
        field = "advisorPreflight" if stage == "preflight" else "finalReview"
        record = state.get(field)
        legacy = record.get("dispositionEvidence") if isinstance(record, dict) else None
    return legacy if isinstance(legacy, str) and legacy else None


def _linked_disposition_document(
    state: JsonObject, document: JsonObject, stage: str, producer: str,
) -> JsonObject:
    intake_id = str(document["intakeEvidenceId"])
    states = state.get("findingStates", [])
    if not isinstance(states, list):
        raise WorkflowError("recorded finding states are corrupt")
    prior = {
        evidence_id
        for disposition in document["dispositions"]
        if (finding_state := _finding_state(state, intake_id, disposition["finding_id"])) is not None
        and finding_state.get("status") != "pending"
        and (evidence_id := _disposition_evidence(state, finding_state, stage, producer))
    }
    linked = json.loads(json.dumps(document))
    if prior:
        linked["supersedesEvidenceIds"] = sorted(prior)
    return linked


def _finding_state(state: JsonObject, intake_id: object, finding_id: object) -> JsonObject | None:
    return next((entry for entry in state.get("findingStates", [])
                 if (entry.get("intakeEvidenceId") == intake_id and entry.get("findingId") == finding_id)
                 or any(ref.get("evidenceId") == intake_id and ref.get("id") == finding_id
                        for ref in entry.get("observations", []))), None)


def _resolve_disposition_receipts(identity: RepoIdentity, transaction: LedgerMutation,
                                  state: JsonObject, document: JsonObject) -> JsonObject:
    # Legacy inline nonbehavioral dispositions have no immutable finding intake.
    if not document.get("intakeEvidenceId"):
        return document
    document = json.loads(json.dumps(document))
    intake = transaction.evidence(document.get("intakeEvidenceId"))
    if not isinstance(intake, dict) or intake.get("workflowId") != state["workflowId"]:
        raise WorkflowError("receipt disposition requires an owned immutable intake")
    findings = {item["id"]: item for item in intake.get("findings", [])}
    receipts: dict[str, JsonObject] = {}
    for item in document["dispositions"]:
        if "evidenceRefs" not in item:
            continue
        finding = findings.get(item["finding_id"])
        if finding is None:
            raise WorkflowError("receipt disposition references a finding outside its intake")
        item["kind"] = finding["kind"]
        for reference in item["evidenceRefs"]:
            if reference not in receipts:
                receipts[reference], _ = execution_receipt(identity, state, reference, transaction)
        if item["status"] == "fixed" and not any(
            run.get("exitCode") == 0 and (run.get("valid") is True or run_recorded_baseline(run))
            for run in (receipts[ref] for ref in item["evidenceRefs"])
        ):
            raise WorkflowError("fixed requires a successful current executed receipt")
    if document.get("context") is None:
        document["context"] = {"workflowId": state["workflowId"], "candidateTree": _candidate_tree(identity)}
    for item in document["dispositions"]:
        finding = findings.get(item["finding_id"])
        if finding is None:
            continue
        entry = _finding_state(state, document["intakeEvidenceId"], item["finding_id"])
        if entry is None:
            raise WorkflowError("mechanism binding requires the current finding intake")
        if entry["kind"] != "behavioral":
            continue
        canonical = entry.get("canonicalFinding") or {
            "evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"],
        }
        mechanism = item.get("mechanism") or entry.get("mechanismEvidence")
        if mechanism is None:
            if item["status"] == "fixed":
                raise WorkflowError("behavioral fixed requires its existing mechanism explanation or reference")
            continue
        if isinstance(mechanism, dict):
            owned = any(
                mechanism in [owner.get("mechanismEvidence"), *owner.get("mechanismHistory", [])]
                and (owner.get("canonicalFinding") or {
                    "evidenceId": owner["intakeEvidenceId"], "id": owner["findingId"],
                }) == canonical for owner in state.get("findingStates", [])
            )
            source = transaction.evidence(mechanism.get("evidenceId"))
            if not owned or not source or source.get("workflowId") != state["workflowId"]:
                raise WorkflowError("mechanism reference is missing or belongs to a foreign finding/workflow")
        item["mechanism"] = mechanism
        item["mechanismBinding"] = {
            "workflowId": state["workflowId"], "canonicalFinding": canonical,
            "intakeEvidenceId": document["intakeEvidenceId"],
            "candidateTree": document["context"]["candidateTree"],
        }
    return document


def _apply_finding_dispositions(
    transaction: LedgerMutation, state: JsonObject, intake_id: str,
    dispositions: list[JsonObject], stage: str, producer: str,
    disposition_evidence_id: str,
) -> bool:
    intake = transaction.evidence(intake_id)
    if not isinstance(intake, dict) or any(intake.get(field) != expected for field, expected in (
        ("workflowId", state["workflowId"]), ("stage", stage), ("producer", producer),
    )):
        raise WorkflowError("disposition references an unrecorded, stale, or foreign finding intake")
    findings = {str(item["id"]): item for item in intake.get("findings", []) if isinstance(item, dict)}
    selected = {str(item["finding_id"]) for item in dispositions}
    if not selected or not selected <= set(findings):
        raise WorkflowError("dispositions reference a finding outside the immutable intake")
    states = state.get("findingStates", [])
    if not isinstance(states, list):
        raise WorkflowError("recorded finding lifecycle is corrupt")
    intake_states = {identifier: _finding_state(state, intake_id, identifier) for identifier in selected}
    if any(entry is None for entry in intake_states.values()):
        raise WorkflowError("recorded finding lifecycle does not match immutable intake")
    terminals: dict[str, JsonObject] = {}
    items = _map_items(transaction.evidence(state.get("tddEvidence")), terminals=terminals)
    if items is None:
        items = _map_items(transaction.evidence(state.get("preflightEvidence")), terminals=terminals) or []
    owned = _linked_finding_items(transaction, items=items, state=state)
    pending = set(behavior_map.unresolved(items, terminals=terminals))
    for disposition in dispositions:
        identifier, status = str(disposition["finding_id"]), str(disposition["status"])
        kind = str(disposition["kind"])
        finding_state = intake_states.get(identifier)
        if finding_state is None:
            raise WorkflowError(f"finding {identifier} has no immutable intake state")
        current = finding_state.get("status")
        if kind != findings[identifier].get("kind"):
            raise WorkflowError(f"finding {identifier} disposition kind differs from immutable intake")
        # Input retains its immutable observation kind; closure uses the current obligation.
        kind = str(finding_state["kind"])
        if status == current:
            raise WorkflowError(f"finding {identifier} disposition does not change effective state")
        if current in {"fixed", "rejected-with-evidence", "report-only"}:
            correcting = False
            if kind == "behavioral" and current in {"fixed", "report-only"} and status in {"report-only", "rejected-with-evidence"}:
                try:
                    _behavioral_finding_closure(
                        intake_id, identifier, require_green=current == "fixed",
                        owned=owned, terminals=terminals, pending=pending,
                    )
                except WorkflowError:
                    # Only a terminal claim its present owning proof no longer
                    # supports may be corrected. The new measured disposition
                    # is validated normally and retains the old one in history.
                    correcting = True
            if not correcting:
                raise WorkflowError(f"finding {identifier} already has terminal disposition {current}")
        if status == "fixed" and int(finding_state.get("recurrence", 0)) >= 2:
            binding = disposition.get("mechanismBinding", {})
            if (not finding_state.get("repairReviewEvidence")
                    or finding_state.get("repairReviewedTree") != binding.get("candidateTree")):
                raise WorkflowError("second recurrence fixed requires current independent lead review of the reviewer repair")
        if status in {"fixed", "report-only"} and kind == "behavioral":
            _behavioral_finding_closure(
                intake_id, identifier, require_green=status == "fixed",
                owned=owned, terminals=terminals, pending=pending,
            )
        if current != "pending":
            prior = _disposition_evidence(state, finding_state, stage, producer)
            if prior is None:
                raise WorkflowError(f"finding {identifier} has no effective disposition evidence")
            history = finding_state.setdefault("dispositionHistory", [])
            if not isinstance(history, list):
                raise WorkflowError(f"finding {identifier} disposition history is corrupt")
            history.append({
                "evidenceId": prior,
                "status": current,
                "supersededBy": disposition_evidence_id,
            })
        finding_state["status"] = status
        finding_state["dispositionEvidenceId"] = disposition_evidence_id
        finding_state["dispositionFindingId"] = identifier
        if "mechanismBinding" in disposition:
            finding_state["mechanismEvidence"] = {"evidenceId": disposition_evidence_id, "id": identifier}
        if stage == "final" and status == "rejected-with-evidence":
            finding_state["appealStatus"] = (
                "disagreement" if state.get("finalAppealConsumed") else "pending"
            )
    bulk = sum(
        1 for item in dispositions
        if str(item.get("status")) == "rejected-with-evidence"
        and findings[str(item["finding_id"])].get("material") is True
    )
    if bulk >= 3:
        # Observability, not refusal: X6R7 bulk-closed 10 material findings in
        # one document and 7 were re-raised, 2 with attacks proven fake. The
        # shape check cannot verify a measurement is real; the warning makes
        # the batch visible where the lead and reviewers read stderr.
        print(
            f"bulk-rejection warning: {bulk} material findings rejected-with-evidence "
            f"in one document (stage={stage}, intake={intake_id}); a rejection without "
            "its quoted measurement is indistinguishable from one ignored",
            file=sys.stderr,
        )
    return _stage_unresolved(state, stage, producer)


def advisor_disposition(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    stage: str,
    findings: str,
    *,
    document: JsonObject | None = None,
    expected_candidate_tree: str | None = None,
) -> JsonObject:
    if findings not in {"none", "addressed"}:
        raise ValueError("advisor disposition requires --findings none or addressed")
    if stage not in {"preflight", "final"}:
        raise ValueError(f"unsupported advisor stage: {stage}")
    if findings == "addressed" and document is None:
        raise ValueError("an addressed disposition requires the lead's disposition document")
    if findings == "none" and document is not None:
        raise ValueError("a findings-none disposition carries no document")
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if stage == "preflight" and state.get("revalidation"):
            raise WorkflowError(PREFLIGHT_CLOSED)
        state.pop("paused", None)
        field = "advisorPreflight" if stage == "preflight" else "finalReview"
        record = state.get(field)
        recorded = (
            isinstance(record, dict)
            and record.get("source") in REVIEW_SOURCES
            and (record.get("status") == "completed" if stage == "preflight" else record.get("status") in FINAL_VERDICTS)
        )
        source = record.get("source") if isinstance(record, dict) else None
        historical = False
        if not recorded and stage == "final" and isinstance(document, dict):
            intake = transaction.evidence(document.get("intakeEvidenceId"))
            historical = isinstance(intake, dict) and all((
                intake.get("workflowId") == state.get("workflowId"),
                intake.get("stage") == stage,
                intake.get("producer") in REVIEW_SOURCES,
            ))
            source = intake.get("producer") if historical else None
        if not recorded and not historical:
            raise WorkflowError("advisor disposition cannot create a result; record the consult first")
        writes: list[EvidenceWrite] = []
        if document is not None:
            document = _resolve_disposition_receipts(identity, transaction, state, document)
            _validate_disposition_context(identity, state, document)
            if "intakeEvidenceId" in document:
                document = _linked_disposition_document(state, document, stage, str(source))
            write = evidence_write(str(state["workflowId"]), f"advisor-disposition-{stage}", document)
            writes.append(write)
            if "intakeEvidenceId" in document:
                intake_id = str(document["intakeEvidenceId"])
                _apply_finding_dispositions(
                    transaction, state, intake_id, document["dispositions"], stage,
                    str(source), write.evidence_id,
                )
        states = state.get("findingStates", [])
        if not isinstance(states, list):
            raise WorkflowError("recorded finding states are corrupt")
        unresolved = _stage_unresolved(state, stage, str(source))
        if findings == "none" and unresolved:
            raise WorkflowError("findings none conflicts with an undispositioned finding intake")
        if not historical:
            record["findings"] = "pending" if unresolved else findings
            if document is not None:
                record["dispositionEvidence"] = write.evidence_id
            state["phase"] = "advisor-preflight" if stage == "preflight" else "final-review"
        state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, f"advisor-{stage}-disposition", evidence=writes)


def completion_missing(state: JsonObject) -> list[str]:
    """Canonical completion readiness shared by complete and the Stop latch."""
    missing: list[str] = [] if instance_id(state) else ["workflowId"]
    for field in ("repoContextForge", "preflight", "verification"):
        if state.get(field) != "passed":
            missing.append(field)
    for phase in EVIDENCE_PHASES:
        field = STEP_FIELDS[phase]
        if state.get(field) == "passed" and not state.get(f"{field}Evidence"):
            missing.append(f"{field}Evidence")
    if state.get("verification") == "passed":
        if not state.get("qualityGateEvidence"):
            missing.append("qualityGateEvidence")
        if not state.get("qualityGateManifestId"):
            missing.append("qualityGateManifest")
    if state.get("tdd") not in {"passed", "not-required"}:
        missing.append("tdd")
    if not _allows_next(state, "code-review"):
        missing.append("codeReview")
    if not _allows_next(state, "final-review"):
        missing.append("finalReview")
    return missing


CHECKPOINT_PHASES = {"preflight-advice", "final-review"}


def _recorded_items(
    identity: RepoIdentity, state: JsonObject, *, terminals: dict[str, JsonObject] | None = None,
) -> list[JsonObject]:
    """Read the current map, falling back only when absent, not when corrupt."""
    fields = ("preflightLatestEvidence",) if state.get("preflight") == "pending" else ()
    for field in (*fields, "tddEvidence", "preflightEvidence"):
        evidence_id = state.get(field)
        items = _map_items(evidence_document(identity, evidence_id if isinstance(evidence_id, str) else None),
                           terminals=terminals)
        if items is not None:
            return items
    return []


def _late_items(items: list[JsonObject]) -> list[JsonObject]:
    """Items whose RED or baseline ran with production already changed: the order
    of proof the recorder recorded instead of refusing (a contract RED, or a
    preservation baseline whose observation is candidate-only)."""
    late: list[JsonObject] = []
    for entry in items:
        proofs = (entry.get("redProof"), entry.get("baselineProof"))
        changed = next((proof["productionChanged"] for proof in proofs
                        if isinstance(proof, dict) and proof.get("productionChanged")), None)
        if changed:
            late.append({"id": entry.get("id"), "productionChanged": changed})
    return late


def _finding_ledger(
    identity: RepoIdentity, state: JsonObject, items: list[JsonObject],
) -> list[JsonObject]:
    """Every recorded finding's immutable claim and its owning attack items.

    This is the evidence the final consult adjudicates domain narrowing from:
    the verbatim claim beside the seams and statuses of the attacks that closed
    it, so a broad finding narrowed to one convenient attack is visible.
    """
    owned = _linked_finding_items(None, items=items, state=state)
    states = state.get("findingStates")
    intakes: dict[str, dict[str, JsonObject]] = {}
    dispositions: dict[str, dict[str, JsonObject]] = {}
    ledger: list[JsonObject] = []
    for entry in states if isinstance(states, list) else []:
        if not isinstance(entry, dict):
            continue
        intake_id = str(entry.get("intakeEvidenceId"))
        if intake_id not in intakes:
            intake = evidence_document(identity, intake_id)
            intakes[intake_id] = {str(finding.get("id")): finding for finding in (intake or {}).get("findings", [])
                                  if isinstance(finding, dict)}
        finding_id = str(entry.get("findingId"))
        claim = intakes[intake_id].get(finding_id, {}).get("claim")
        disposition_id = _disposition_evidence(state, entry, str(entry.get("stage")), str(entry.get("producer")))
        if disposition_id is not None and disposition_id not in dispositions:
            document = evidence_document(identity, disposition_id)
            dispositions[disposition_id] = {
                str(item.get("finding_id")): item for item in (document or {}).get("dispositions", [])
                if isinstance(item, dict)
            }
        measured = dispositions.get(disposition_id, {}).get(str(entry.get("dispositionFindingId", finding_id)))
        measurement = {key: measured[key] for key in ("premise", "occurrence", "materialConsequence", "evidence", "reference")
                       if measured.get(key) is not None} if measured else None
        refs = ({"evidenceId": intake_id, "id": finding_id}, entry.get("canonicalFinding", {}))
        attacks = {key: item for ref in refs
                   for key, item in owned.get((str(ref.get("evidenceId")), str(ref.get("id"))), {}).items()}
        ledger.append({
            "intakeEvidenceId": intake_id,
            "producer": entry.get("producer"), "stage": entry.get("stage"),
            "findingId": entry.get("findingId"), "kind": entry.get("kind"),
            "material": entry.get("material"), "status": entry.get("status"),
            "claim": claim,
            **{key: entry[key] for key in ("canonicalFinding", "recurrence", "observations", "mechanismEvidence", "mechanismHistory", "repairOwner", "repairOwnerHistory") if key in entry},
            "owners": [{**{key: item.get(key) for key in
                           ("id", "kind", "behavior", "expected", "seam", "status", "proofCommand")},
                        "executedCommands": behavior_map.executed_commands(item),
                        "revalidationRequired": item.get("revalidationRequired") is True}
                       for item in attacks.values()],
            "measurement": measurement,
        })
    return ledger


def _context_steps(state: JsonObject) -> tuple[tuple[str, bool], ...]:
    """The graph context this pass stands on, which is Repo Context Forge's evidence."""
    return (("repo-context-forge", _evidence_ready(state, "repo-context-forge")),)


def checkpoint(identity: RepoIdentity, phase: str, *, reconsult: bool = False) -> JsonObject:
    if phase not in CHECKPOINT_PHASES:
        raise ValueError(f"unsupported checkpoint phase: {phase}")
    if reconsult and phase != "preflight-advice":
        raise ValueError("--reconsult requires preflight-advice")
    state = _require(identity)
    workflow_id = instance_id(state)
    candidate = _active_candidate_tree(identity)
    revalidation = bool(state.get("revalidation"))
    terminal = state.get("phase") == "complete" and not revalidation
    open_for_phase = not terminal and not (phase == "preflight-advice" and revalidation)
    stage_actions = {
        "preflight-advice": {"preflight"},
        "final-review": {"final-review", "appeal-final-review", "re-consult-final-review"},
    }
    requirements = (
        ("workflowId", workflow_id is not None),
        ("open-workflow", open_for_phase),
        ("advisor-stage", reconsult or state.get("nextAction") in stage_actions[phase]
         or phase == "final-review" and _review_assessed(state)),
        ("passStartOid", _is_commit_oid(identity, state.get("passStartOid"))),
        *(
            _context_steps(state)
            if phase == "preflight-advice"
            else (
                ("verification evidence", _evidence_ready(state, "verification")),
                ("quality-gate verification", bool(state.get("qualityGateEvidence"))),
                ("code-review", _allows_next(state, "code-review") or _review_assessed(state)),
            )
        ),
    )
    missing = [name for name, ready in requirements if not ready]
    evidence_id = state.get("repoContextForgeEvidence")
    graph_document = evidence_document(
        identity, evidence_id if isinstance(evidence_id, str) else None,
    )
    projection = None
    if not isinstance(graph_document, dict):
        missing.append("advisor projection evidence")
    elif (
        type(graph_document.get("schemaVersion")) is not int
        or graph_document.get("schemaVersion") != 1
        or graph_document.get("slug") != state.get("slug")
        or graph_document.get("workflowId") != workflow_id
    ):
        missing.append("advisor projection evidence belongs to another workflow")
    else:
        try:
            projection = validate_advisor_projection(
                graph_document.get("advisorProjection"), candidate_tree=candidate,
            )
        except ValueError as exc:
            missing.append(str(exc))
    design_evidence_id = state.get("governedDesignEvidence")
    design = None
    if isinstance(design_evidence_id, str):
        try:
            design = validate_design_declaration(
                evidence_document(identity, design_evidence_id),
            )
        except ValueError as exc:
            missing.append(str(exc))
    terminals: dict[str, JsonObject] = {}
    items = _recorded_items(identity, state, terminals=terminals)
    if phase == "final-review":
        missing.extend(() if state.get("nextAction") in ("appeal-final-review", "re-consult-final-review")
                       else correction_blockers(identity, state, items=items, terminals=terminals))
        if drift := _binding_drift(identity, state, "review"):
            missing.append(drift)
        if drift := _binding_drift(identity, state, "quality-gate"):
            missing.append(drift)
    review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    return {
        "schemaVersion": 1,
        "phase": phase,
        "ready": not missing,
        "missing": missing,
        "slug": state.get("slug"),
        "workflowId": state.get("workflowId"),
        "intent": state.get("intent"),
        "nextAction": state.get("nextAction"),
        "sessionMode": "create" if phase == "preflight-advice" and not reconsult else "resume",
        "passStartOid": state.get("passStartOid"),
        "activeCandidateTree": candidate,
        "advisorProjectionEvidence": evidence_id,
        "advisorProjection": projection,
        "governedDesignEvidence": design_evidence_id,
        "governedDesign": design,
        "findingLedger": _finding_ledger(identity, state, items),
        "lateRed": _late_items(items),
        "tdd": state.get("tdd"),
        "codeReviewStatus": review.get("status"),
    }


def complete(
    identity: RepoIdentity, *, slug: str | None = None, workflow_id: str | None = None,
    expected_candidate_tree: str | None = None,
) -> JsonObject:
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _require_state(transaction.state)
        _require_instance(state, slug, workflow_id)
        state.pop("paused", None)
        state.pop("revalidation", None)
        # Behavior Map closure and design coverage are judged from the evidence
        # this transaction sees, so a concurrent map change cannot slip through.
        tdd_document = transaction.evidence(state.get("tddEvidence"))
        preflight_document = transaction.evidence(state.get("preflightEvidence"))
        missing = behavior_map.closure_blockers(
            tdd_document, preflight_document,
        ) + _finding_proof_blockers(transaction, state) + _finding_state_blockers(state) + completion_missing(state)
        graph_id = state.get("repoContextForgeEvidence")
        graph_document = transaction.evidence(graph_id) if isinstance(graph_id, str) else None
        if (
            not _graph_candidate_ready(
                graph_document, _active_candidate_tree(identity),
                slug=state.get("slug"), workflow_id=state.get("workflowId"),
            )
            and "repoContextForge" not in missing
        ):
            missing.append("repoContextForge")
        if missing:
            raise WorkflowIncomplete("workflow incomplete: " + ", ".join(missing))
        if drift := _binding_drift(identity, state, "review", transaction):
            raise WorkflowIncomplete(f"the reviewed tree changed after the final review: {drift}")
        if drift := _binding_drift(identity, state, "quality-gate", transaction):
            raise WorkflowIncomplete(f"the quality gate did not cover the current tree: {drift}")
        state["phase"] = "complete"
        state["nextAction"] = "delivery-and-reviewer-completion"
        return _commit(transaction, state, "complete")


def _reset_downstream(state: JsonObject) -> None:
    _clear_verification(state)
    _reset_reviews(state)
    state["nextAction"] = _derive_next_action(state)


def _reset_reviews(state: JsonObject) -> None:
    state["codeReview"] = {"status": "pending", "findings": "pending"}
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state.pop("finalReviewContextMismatchEvidence", None)


def invalidate_after_edit(identity: RepoIdentity, path: str) -> JsonObject | None:
    reviewable = is_reviewable_path(path)
    if not reviewable and not is_governance_path(path):
        return read_workflow(identity)
    with mutation(identity) as transaction:
        state = transaction.state
        if state is None:
            return None
        if reviewable and state.get("phase") == "complete" and not state.get("revalidation"):
            return state
        if reviewable and _binding_drift(identity, state, "quality-gate", transaction) is None:
            return state
        def material(value: JsonObject) -> str:
            return json.dumps({k: v for k, v in value.items() if k != "nextAction"},
                              sort_keys=True)

        before, before_next = material(state), state.get("nextAction")
        state.pop("paused", None)
        if reviewable:
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
            kind = "production-edit-invalidated"
        else:
            kind = "governance-edit-invalidated"
            if state.get("phase") == "complete":
                state["revalidation"] = True
        _reset_downstream(state)
        # An edit while the workflow is already dirty repeats a transition that
        # changes nothing material; committing it would append a duplicate
        # ledger event (measured: 64% of a benchmark run's events) and clobber
        # a producer-derived nextAction, such as the reassessment hint, with
        # this path's recomputation. Commit exactly when material state
        # changed; otherwise keep the committed projection intact.
        if material(state) == before:
            if before_next is not None:
                state["nextAction"] = before_next
            return state
        return _commit(transaction, state, kind)


def review_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Lead prerequisites for dispatch, without recording or rerunning proof."""
    missing = [phase for phase in WORKFLOW_SEQUENCE[:WORKFLOW_SEQUENCE.index("code-review")]
               if not _allows_next(state, phase)]
    if not missing:
        if drift := _binding_drift(identity, state, "quality-gate"):
            missing.append(drift)
    return missing


def ready_for_edit(identity: RepoIdentity, path: str) -> tuple[bool, list[str]]:
    state = read_workflow(identity)
    if state is None:
        return False, ["active workflow"]
    if state.get("revalidation"):
        return False, ["new active workflow (governance revalidation is re-verifying the completed pass; a production edit belongs to the next one)"]
    if state.get("phase") == "complete":
        return False, ["new active workflow (this one is complete; begin the next pass)"]
    missing = [
        name for name, ready in (
            *_context_steps(state),
            ("production preflight", _evidence_ready(state, "preflight")),
        ) if not ready
    ]
    if not is_test_path(path) and state.get("tdd") not in {"in-progress", "passed", "not-required"}:
        missing.append("TDD RED or a recorded not-required decision (test-like edits stay open)")
    if missing or is_test_path(path):
        return not missing, missing
    held = [str(item["id"]) for item in _recorded_items(identity, state)
            if item.get("status") not in {"superseded", "omitted", "withdrawn"} and behavior_map.interpretation_pending(item)]
    return not held, ["unsettled interpretation: " + ", ".join(held)] if held else []


def public_status(state: JsonObject, identity: RepoIdentity | None = None, *,
                  fields: set[str] | None = None, candidate_tree: str | None = None,
                  recovery: bool = False) -> JsonObject:
    """The schemaVersion 1 status projection.

    A Repo Context Forge pass is only as good as its producer evidence, so a stored
    `passed` without one is published as pending: the phase reads to every consumer the
    way it already reads to nextAction, the checkpoint, edit readiness and completion,
    and a legacy pass cannot report graph work that has no evidence behind it. Any other
    stored status is passed through untouched; only a bare claim is downgraded.

    `gitnexus` survives only as derived compatibility output for readers of the retired
    phase, reporting that same readiness. It is never stored, never writable, and never
    a second readiness source.
    """
    graph_needed = recovery or fields is None or bool(fields & {"repoContextForge", "gitnexus"})
    candidate = candidate_tree
    if identity is not None and (graph_needed or fields is None or "activeCandidateTree" in fields):
        candidate = candidate or _active_candidate_tree(identity)
    selections = (_executed_selections(identity, state)
                  if identity is not None and (fields is None or "mapSelections" in fields) else None)
    graph_id = state.get("repoContextForgeEvidence")
    graph_document = (
        evidence_document(identity, graph_id)
        if graph_needed and identity is not None and isinstance(graph_id, str)
        else None
    )
    ready = _evidence_ready(state, "repo-context-forge") and (
        candidate is None or _graph_candidate_ready(
            graph_document, candidate,
            slug=state.get("slug"), workflow_id=state.get("workflowId"),
        )
    )
    stored = state.get("repoContextForge")
    result = {
        **state,
        **({"activeCandidateTree": candidate} if candidate is not None else {}),
        # Only when the map has executed something: an empty projection would
        # change the shape every reader sees while saying nothing at all.
        **({"mapSelections": selections} if selections else {}),
        "repoContextForge": stored if ready or stored != "passed" else "pending",
        "gitnexus": "passed" if ready else "pending",
    }
    if recovery and identity is not None:
        drift = _binding_drift(identity, state, "quality-gate") if state.get("qualityGateEvidence") else None
        if drift:
            result.update(verification="pending", bindingError=drift)
        if not ready or drift:
            result["nextAction"] = _derive_next_action(result)
    if fields is None:
        # The recorded task text is multi-KB and already in the caller's context; it is
        # read back on request (--fields intent) and by the advisor checkpoint, never by default.
        result.pop("intent", None)
        return result
    return {key: value for key, value in result.items() if key in fields}


def _selection(command: str, root: object) -> JsonObject:
    """The tests one recorded command selected, or why that cannot be decided.

    Ownership that cannot be decided is reported as unknown, never as an empty
    selection: a reader comparing these against an impacted-test set would read
    an empty list as "this proof owns nothing" and an unknown as "ask someone
    else", and only one of those is safe to act on.
    """
    surface = tdd_surface.identify(shlex.split(command))
    if surface.get("runner") not in {"unittest", "pytest"}:
        return {"command": command, "targets": None, "unknown": "the runner is not a supported test surface"}
    targets, discover, ambiguous, unresolved = tdd_surface.proof_targets(surface, root)
    if ambiguous:
        return {
            "command": command, "targets": None,
            "unknown": "an unrecognized option may own these tokens: " + ", ".join(sorted(ambiguous)),
        }
    if unresolved:
        return {
            "command": command, "targets": None,
            "unknown": f"{unresolved} is not resolved by the recorded surface, so the selected tests are unknown",
        }
    return {"command": command, "targets": sorted(targets), **({"discover": True} if discover else {})}


def _executed_selections(identity: RepoIdentity, state: JsonObject) -> JsonObject | None:
    """Per current map item, the tests each recorded proof actually selected."""
    try:
        items = behavior_map.recorded_map(
            evidence_document(identity, state.get("tddEvidence")),
            evidence_document(identity, state.get("preflightEvidence")),
        )
    except (WorkflowError, LedgerError, ValueError):
        return None
    if items is None:
        return None
    selections: JsonObject = {}
    for entry in items:
        executed = {
            phase: _selection(command, identity.root)
            for phase, command in behavior_map.executed_commands(entry).items()
        }
        if executed:
            selections[str(entry["id"])] = executed
    return selections


def _earned_split(identity: RepoIdentity, state: JsonObject) -> str:
    """Contract items GREEN through RED over contract items declared: the proof
    the map has earned, not the count of items it names."""
    try:
        items = behavior_map.recorded_map(
            evidence_document(identity, state.get("tddEvidence")),
            evidence_document(identity, state.get("preflightEvidence")),
        )
    except (WorkflowError, LedgerError, ValueError):
        return " Contract green=unknown (map evidence unreadable)."
    items = items or []
    prose = ", ".join(str(entry["id"]) for entry in items
                      if entry.get("status") == "already-satisfied" and not behavior_map.producer_proved(entry))
    settled = f" Prose settlement (unresolved until an executed baseline): {prose}." if prose else ""
    contract = [entry for entry in items if entry.get("kind") == "contract"]
    if not contract:
        return settled
    earned = sum(1 for entry in contract if behavior_map.green_through_red(entry))
    late = ", ".join(str(entry["id"]) for entry in _late_items(items))
    shared = "; ".join(", ".join(group) for group in behavior_map.shared_observations(items))
    return (f" Contract green={earned}/{len(contract)}." + (f" Late RED: {late}." if late else "")
            + (f" Shared RED observation: {shared}." if shared else "") + settled)


def _map_listing(identity: RepoIdentity, state: JsonObject) -> str:
    """The map facts a resumed lead otherwise digs out of its own transcript; last in
    the line so a cap cut takes ids, never the invariant or the verification command."""
    try:
        items = behavior_map.recorded_map(
            evidence_document(identity, state.get("tddEvidence")),
            evidence_document(identity, state.get("preflightEvidence")),
        ) or []
    except (WorkflowError, LedgerError, ValueError):
        return ""
    if not items:
        return ""
    by_status: dict[str, list[str]] = {}
    for entry in items:
        by_status.setdefault(str(entry.get("status")), []).append(str(entry["id"]))
    return " Map: " + "; ".join(f"{status}: {', '.join(ids)}" for status, ids in by_status.items()) + "."


def _latest_verification_command(identity: RepoIdentity, state: JsonObject) -> str:
    """The command behind the latest verification run, so a resumed lead knows how
    verification was driven without a second evidence lookup."""
    try:
        document = evidence_document(identity, state.get("verificationLatestEvidence"))
    except (WorkflowError, LedgerError, ValueError):
        return ""
    runs = document.get("runs") if isinstance(document, dict) else []
    commands = [str(run.get("command")) for run in runs
                if isinstance(run, dict) and run.get("kind") == "generic" and run.get("valid") and run.get("command")]
    if not commands:
        return ""
    command = commands[-1]
    return f" Verified by: {command[:120] + ' […]' if len(command) > 120 else command}."


def summary(identity: RepoIdentity, limit: int = 3000) -> str:
    state = read_workflow(identity)
    if state is None:
        return "Workflow state unavailable; do not infer that any workflow step passed."
    state = public_status(state, identity, recovery=True,
                          fields=(set(state) | {"activeCandidateTree", "bindingError"}) - {"mapSelections"})
    gate_drift = state.get("bindingError")
    advisor = state.get("advisorPreflight") if isinstance(state.get("advisorPreflight"), dict) else {}
    code_review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    final_review = state.get("finalReview") if isinstance(state.get("finalReview"), dict) else {}
    mechanisms = ""
    shown: set[str] = set()
    excerpt_budget = 600
    for entry in state.get("findingStates", []):
        if entry.get("kind") != "behavioral" or not _finding_unresolved(entry):
            continue
        reference = entry.get("mechanismEvidence")
        mechanisms += (f" Mechanism {entry.get('findingId')} recurrence={entry.get('recurrence', 0)} "
                       f"repair={entry.get('repairOwner', 'lead')} evidence={reference or 'missing'}.")
        if isinstance(reference, dict) and excerpt_budget and reference["evidenceId"] not in shown:
            shown.add(reference["evidenceId"])
            diagnosis = _mechanism_explanation(reference, lambda ref: evidence_document(identity, ref), state["workflowId"])
            if isinstance(diagnosis, str) and diagnosis:
                excerpt = diagnosis[:excerpt_budget]
                mechanisms += f" Recorded diagnosis: {excerpt}" + (" …" if len(excerpt) < len(diagnosis) else "")
                excerpt_budget -= len(excerpt)
    text = (
        f"Active workflow: slug={state.get('slug')} workflowId={state.get('workflowId')} "
        f"candidate={state.get('activeCandidateTree')} phase={state.get('phase')} next={state.get('nextAction')}. "
        + (f"Binding: {gate_drift}. " if gate_drift else "")
        + " ".join(f"{field}={state[field]}" for field in (
            "preflightLatestEvidence", "tddEvidence", "verificationLatestEvidence", "qualityGateManifestId") if state.get(field)) + ". "
        # Evidence-aware, not the raw status: a compacted session reads this line, and
        # a legacy pass that claims the phase without producer evidence is pending
        # everywhere else in the workflow.
        + f"Steps: repo-context-forge={state.get('repoContextForge')}, "
        f"advisor-preflight={advisor.get('status')}/{advisor.get('findings')}, preflight={state.get('preflight')}, tdd={state.get('tdd')}, "
        f"production-code={state.get('productionCode') or 'pending'}, "
        f"implementation={state.get('implementation')}, verification={state.get('verification')}, "
        f"quality-gate={'passed' if state.get('qualityGateEvidence') and not gate_drift else 'pending'}, "
        f"code-review={code_review.get('status')}/{code_review.get('findings')}, "
        f"final-review={final_review.get('source')}/{final_review.get('status')}/{final_review.get('findings')}. "
        + _earned_split(identity, state)
        + (f" Advisor outage: {advisor.get('reason')}." if advisor.get("status") == "unavailable" else "")
        + (
            f" Paused: {str(paused.get('reason'))[:160]}."
            if isinstance(paused := state.get("paused"), dict)
            else ""
        )
        + " Missing state is pending, never success."
        + mechanisms
        + _latest_verification_command(identity, state)
        + _map_listing(identity, state)
    )
    suffix = " … Details: workflow status --repo <checkout>; workflow evidence --repo <checkout> --evidence-id <id>."
    return text if len(text) <= limit else text[:max(0, limit - len(suffix))].rsplit(" ", 1)[0] + suffix
