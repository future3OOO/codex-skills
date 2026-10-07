"""Repository-scoped production workflow policy and transactional commands."""
from __future__ import annotations

import difflib
import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable, Sequence

from . import behavior_map
from ._workflow_db import (
    CHECK_ONLY,
    EvidenceWrite,
    LedgerError,
    LedgerMutation,
    ManifestWrite,
    evidence_write,
    manifest_write,
    mutation as _ledger_mutation,
    read_active,
    read_evidence,
    read_manifest,
)
from .advisor_diff import current_pass_evidence
from .repo_identity import RepoIdentity
from .workflow_documents import validate_advisor_projection, validate_design_declaration
from .state_store import (
    _active_candidate_tree,
    _paths,
    analysis_unchanged,
    is_governance_path,
    is_reviewable_path,
    manifest_diff,
    repo_state_dir,
    tree_manifest,
    utc_timestamp,
)

JsonObject = dict[str, object]
STEP_STATUSES = {"pending", "in-progress", "passed", "not-required", "unavailable"}
FINDING_STATUSES = {"pending", "none", "addressed"}
REVIEW_SOURCES = {"codex-advisor"}
FINAL_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}
NO_INSTANCE_ID = "this state predates workflow instance identity and can no longer advance; begin a new workflow"
SLUG_MISMATCH = "--slug does not match the active workflow"
INSTANCE_MISMATCH = "--workflow-id does not match the active workflow instance"
PREFLIGHT_CLOSED = "governance revalidation permits only re-verification and review; preflight consults are closed"
TDD_CLOSED = "governance revalidation permits only re-verification and review; the Behavior Map is closed"
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
        return _effective(identity, _normalise(read_active(identity)), lambda evidence_id: evidence_document(identity, evidence_id))
    except LedgerError as exc:
        raise WorkflowError(str(exc)) from exc


@contextlib.contextmanager
def mutation(identity: RepoIdentity, **binding: str | None):
    with _ledger_mutation(identity, **binding) as transaction:
        _effective(identity, transaction.state, transaction.evidence)
        yield transaction


def _effective(identity: RepoIdentity, state: JsonObject | None,
               evidence: Callable[[str | None], JsonObject | None]) -> JsonObject | None:
    """The one proof-readiness judgment every reader and transaction sees: a stored passed TDD
    step whose map no longer proves the current tree (a comparison stale after an edit, or an
    item open) reads in-progress, with the next action that follows."""
    if not state or state.get("tdd") != "passed" or state.get("phase") == "complete":
        return state
    items = behavior_map.recorded_map(evidence(state.get("tddEvidence")), evidence(state.get("preflightEvidence"))) or []
    if any(item.get("comparison") for item in items):
        from .tdd_workflow import refresh_proof
        refresh_proof(identity, items, state)
        if behavior_map.unresolved(items):
            state["tdd"] = "in-progress"
            state["nextAction"] = _derive_next_action(state)
    return state


def _require_state(state: JsonObject | None) -> JsonObject:
    if state is None:
        raise WorkflowMissing("no active workflow")
    return _normalise(state) or state


def _commit(
    transaction: LedgerMutation,
    state: JsonObject,
    kind: str,
    *, evidence: Sequence[EvidenceWrite] = (),
    manifests: Sequence[ManifestWrite] = (),
) -> JsonObject:
    state["updatedAt"] = utc_timestamp()
    return transaction.append(state, kind, evidence=evidence, manifests=manifests)


def _evidence_ready(state: JsonObject, field: str) -> bool:
    """A producer-recorded passed: status alone is a bare claim."""
    return state.get(field) == "passed" and bool(state.get(f"{field}Evidence"))


def _review_ready(review: object, *, final: bool = False) -> bool:
    if not isinstance(review, dict) or review.get("findings") not in {"none", "addressed"}:
        return False
    if not final:
        return review.get("status") in {"passed", "not-required"}
    return (review.get("source") in REVIEW_SOURCES
            and (review.get("status") == "commit-ready"
                 or review.get("status") == "fix-before-commit" and bool(review.get("intakeEvidence"))))


# The one step registry, in workflow order: step -> (state field, readiness).
# Sequence, predecessors, nextAction, dispatch blockers, edit readiness,
# checkpoint requirements, summary and completion all derive from it.
STEPS: dict[str, tuple[str, Callable[[JsonObject], bool]]] = {
    "repo-context-forge": ("repoContextForge", lambda state: _evidence_ready(state, "repoContextForge")),
    "preflight": ("preflight", lambda state: _evidence_ready(state, "preflight")),
    "tdd": ("tdd", lambda state: state.get("tdd") in {"passed", "not-required"}),
    "verification": ("verification", lambda state: _evidence_ready(state, "verification")
                     and bool(state.get("qualityGateEvidence")) and bool(state.get("qualityGateManifestId"))),
    "code-review": ("codeReview", lambda state: _review_ready(state.get("codeReview"))),
    "final-review": ("finalReview", lambda state: _review_ready(state.get("finalReview"), final=True)),
}
SEQUENCE = tuple(STEPS)


def _allows_next(state: JsonObject, phase: str) -> bool:
    return STEPS[phase][1](state)


def _preflight_finding_states(state: JsonObject) -> list[JsonObject]:
    states = state.get("findingStates")
    if not isinstance(states, list):
        return []
    return [entry for entry in states if isinstance(entry, dict) and entry.get("stage") == "preflight"]


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
    if phase == "code-review" and not _allows_next(state, "tdd"):
        raise WorkflowIncomplete("code-review requires tdd")
    if phase == "final-review" and _review_assessed(state):
        return
    position = SEQUENCE.index(phase)
    if position and not _allows_next(state, SEQUENCE[position - 1]):
        raise WorkflowIncomplete(f"{phase} requires {SEQUENCE[position - 1]}")


def _derive_next_action(state: JsonObject, tdd_document: JsonObject | None = None) -> str:
    finding_states = state.get("findingStates", [])
    correction = [
        entry for entry in finding_states
        if isinstance(entry, dict) and entry.get("stage") in {"code-review", "final"}
    ] if isinstance(finding_states, list) else []
    if state.get("finalReviewContextMismatchEvidence"):
        return "re-consult-final-review"
    # A final finding is settled by the advisor's own re-check: fix, rerun tdd, re-consult.
    if any(entry.get("status") == "pending" and entry.get("stage") != "final" and _finding_unresolved(entry)
           for entry in correction):
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
    phase = next((phase for phase in SEQUENCE if not _allows_next(state, phase)), "complete-workflow")
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
    if state.get("finalStarted") and binding == "quality-gate":  # after the final advisor, the gate is not rerun
        return None if _stored_manifest(identity, state, field, transaction) is not None else missing
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
    if phase not in STEPS or phase == "final-review":
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
        field = STEPS[phase][0]
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
    document: JsonObject | None,
) -> list[JsonObject] | None:
    if not isinstance(document, dict):
        return None
    value = document.get("behaviorMap")
    if value is None:
        inner = document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return behavior_map.runtime_items(value) if value is not None else None


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
                *([(str(finding["canonicalFinding"]["evidenceId"]), str(finding["canonicalFinding"]["id"]))] if finding.get("canonicalFinding") else []),
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
        if entry.get("status") == "pending" and entry.get("kind") == "behavioral"
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


def commit_tdd(identity: RepoIdentity, slug: str, workflow_id: str | None,
               summary_doc: JsonObject, *, expected_evidence_id=None,
               tree_before=None, review_changed=False) -> tuple[JsonObject, str]:
    """Publish comparison results and readiness together under existing ledger ownership."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("revalidation") and (review_changed or tree_before is None):
            raise WorkflowError(TDD_CLOSED)  # a rerun of the mapped items re-verifies; the map stays closed
        _require_predecessor(state, "tdd")
        if not state.get("preflightEvidence") or summary_doc.get("workflowId") != state["workflowId"]:
            raise WorkflowError("comparison requires this workflow's recorded preflight")
        if state.get("tddEvidence") != expected_evidence_id:
            raise WorkflowError("probe obligations changed during execution; retry against current evidence")
        items = _map_items(summary_doc) or []
        from .tdd_workflow import refresh_proof
        refresh_proof(identity, items, state)
        manifests = []
        if tree_before is not None:
            run = summary_doc["runs"][-1]
            drift = _manifest_drift(tree_before, tree_manifest(identity), stale="candidate changed during comparison")
            if _active_candidate_tree(identity) != run["candidateTree"]:
                drift = drift or "candidate tree changed during comparison"
            if drift:
                run.update(valid=False, bindingError=drift)
                for entry in items:
                    if entry.get("comparison", {}).get("candidateTree") == run["candidateTree"]:
                        entry["comparison"].update(valid=False, bindingError=drift)
            measured = manifest_write(str(state["workflowId"]), "tdd-tree", tree_before)
            manifests.append(measured)
            run["treeManifestId"] = measured.manifest_id
        owned = _linked_finding_items(transaction, items=items)
        previous = _map_items(transaction.evidence(expected_evidence_id)) or []
        previous_owned = _linked_finding_items(transaction, items=previous)
        for finding in state.get("findingStates", []):
            key = (str(finding["intakeEvidenceId"]), str(finding["findingId"]))
            if (finding.get("material") and finding.get("kind") == "behavioral"
                    and finding.get("status") != "rejected-with-evidence" and key in previous_owned and key not in owned):
                raise WorkflowError("probe edit loses the only owning attack for finding " + key[1])
        pending = set(behavior_map.unresolved(items))
        for entry in state.get("findingStates", []):
            if entry.get("kind") == "behavioral" and entry.get("status") == "fixed":
                _behavioral_finding_closure(str(entry["intakeEvidenceId"]), str(entry["findingId"]),
                    owned=owned, pending=pending,
                    admit_pending=True)
        summary_doc["behaviorMap"] = items
        write = evidence_write(str(state["workflowId"]), "tdd", summary_doc)
        state["tddEvidence"] = write.evidence_id
        state["tdd"] = "in-progress" if pending else "passed"
        state["phase"] = "tdd"
        state.pop("paused", None)
        if review_changed or pending:
            _reset_reviews(state)
        state["nextAction"] = _derive_next_action(state, summary_doc)
        return _commit(transaction, state, "tdd-comparison", evidence=[write], manifests=manifests), write.evidence_id


def _validate_disposition_context(identity: RepoIdentity, state: JsonObject, document: JsonObject) -> tuple[dict[str, str], str | None]:
    context = document.get("context")
    if not isinstance(context, dict) or context.get("workflowId") != state.get("workflowId"):
        raise WorkflowError("disposition context does not match the active workflow instance")
    manifest = tree_manifest(identity)
    if context.get("candidateTree") != _active_candidate_tree(identity):
        raise WorkflowError("disposition candidateTree does not match the current reviewable tree")
    return manifest, _head_oid(identity)


def same_agent(recorded: object, named: object) -> bool:
    """Match canonical or root-relative names, never an unrelated path suffix."""
    if not isinstance(recorded, str) or not isinstance(named, str) or not recorded.strip("/") or not named.strip("/"):
        return False
    left = recorded if recorded.startswith("/") else "/root/" + recorded
    right = named if named.startswith("/") else "/root/" + named
    return left == right


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
    """Commit immutable independent review intake."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if not summary_doc.get("findings"):
            _require_predecessor(state, "code-review")
        summary_doc["candidateTree"] = _active_candidate_tree(identity)
        write = evidence_write(str(state["workflowId"]), "code-review", summary_doc)
        manifest: ManifestWrite | None = None
        intake = summary_doc.get("findings", [])
        reference = _register_finding_intake(transaction, state, write.evidence_id, summary_doc, {})
        repairs = [entry for entry in state.get("findingStates", [])
                   if int(entry.get("recurrence", 0)) >= 2 and _finding_unresolved(entry)]
        lead = state.get("leadContextId") or os.environ.get("CODEX_THREAD_ID")
        reviewer = summary_doc.get("reviewContextId")
        # A recurrence reached before any reviewer was named takes its owner
        # from the first review that names one.
        if reviewer and lead and reviewer != lead and not summary_doc.get("implementationContextId"):
            for entry in repairs:
                if entry.get("kind") == "behavioral" and not entry.get("repairOwner"):
                    entry["repairOwner"] = {"implementerContextId": reviewer, "reviewerContextId": lead}
        selected = [entry for entry in repairs
                    if same_agent(entry.get("repairOwner", {}).get("implementerContextId"),
                                  summary_doc.get("implementationContextId"))
                    and same_agent(entry.get("repairOwner", {}).get("reviewerContextId"),
                                   summary_doc.get("reviewContextId"))]
        succession = summary_doc.get("repairSuccession")
        if succession is not None:
            _validate_disposition_context(identity, state, succession)
            if not summary_doc.get("reviewContextId"):
                raise WorkflowError("repair succession requires --review-context-id")
            successor = {"implementerContextId": summary_doc["implementationContextId"],
                         "reviewerContextId": summary_doc["reviewContextId"]}
            if (all(same_agent(value, succession["previousOwner"].get(role)) for role, value in successor.items())
                    or same_agent(successor["implementerContextId"], successor["reviewerContextId"])):
                raise WorkflowError("repair succession requires changed, distinct actual roles")
            selected = []
            for ref in succession["findings"]:
                entry = _finding_state(state, ref["evidenceId"], ref["id"])
                if (entry is None or entry not in repairs or entry in selected
                        or entry.get("kind") != "behavioral"
                        or not all(same_agent(value, entry.get("repairOwner", {}).get(role))
                                   for role, value in succession["previousOwner"].items())):
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
            raise WorkflowError("second recurrence review must name a retained repair implementer; "
                                "record the reviewer's review with --review-context-id first")
        for entry in selected:
            owner = entry.get("repairOwner", {})
            implementer = owner.get("implementerContextId")
            reviewer = owner.get("reviewerContextId")
            if (not implementer or not reviewer or same_agent(implementer, reviewer)
                    or not same_agent(reviewer, summary_doc.get("reviewContextId"))
                    or not same_agent(implementer, summary_doc.get("implementationContextId"))):
                raise WorkflowError("second recurrence requires retained reviewer repair and independent lead review")
            if any(_finding_state(state, write.evidence_id, finding["id"]) is entry for finding in intake):
                entry.pop("repairReviewEvidence", None)
                entry.pop("repairReviewedTree", None)
            else:
                entry["repairReviewEvidence"] = write.evidence_id
                entry["repairReviewedTree"] = _active_candidate_tree(identity)
        if summary_doc.get("reviewContextId") and not any(
                _finding_unresolved(entry) and entry.get("repairOwner") for entry in state.get("findingStates", [])):
            state["reviewerContextId"] = summary_doc["reviewContextId"]
        unresolved = _stage_unresolved(state, "code-review", "code-review")
        if not (_allows_next(state, "tdd") and _allows_next(state, "verification")):
            _reset_reviews(state)
        else:
            manifest = _apply_step(identity, state, "code-review",
                                   "pending" if unresolved else "passed", "pending" if unresolved else "none")
        if intake:
            state["codeReviewIntakeEvidence"] = reference
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
        field = STEPS[phase][0]
        latest_field = f"{field}LatestEvidence"
        if expected_evidence_id is not _NO_CAS and state.get(latest_field) != expected_evidence_id:
            raise WorkflowError(f"{phase} evidence changed during the run; re-read and re-run the command")
        if phase == "preflight":
            if state.get("preflightLatestEvidence"):
                raise WorkflowError("preflight is already recorded; use tdd-map for subsequent changes")
            advice = state.get("advisorPreflight") or {}
            approved = transaction.evidence(advice.get("intakeEvidence")) or {}
            if (advice.get("status") != "approved" or approved.get("verdict") != "approved"
                    or json.dumps(approved.get("preflightDraft"), sort_keys=True) != json.dumps(evidence_doc.get("document", evidence_doc), sort_keys=True)):
                raise WorkflowError("preflight requires advisor approval bound to this exact draft")
            _require_owned_behavioral_findings(state, _linked_finding_items(transaction, evidence_doc))
        _apply_step(identity, state, phase, status)
        write = evidence_write(str(state["workflowId"]), phase, evidence_doc)
        state[latest_field] = write.evidence_id
        if status == "passed":
            state[f"{field}Evidence"] = write.evidence_id
            if phase == "preflight" and _map_items(evidence_doc) == []:
                state["tdd"] = "passed"
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


def _verification_key(run: JsonObject) -> str:
    kind = run.get("kind") if run.get("kind") in {"quality-gate", "observed"} else "generic"
    return "quality-gate" if kind == "quality-gate" else f"{kind}:{run.get('command')}"


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
    compared = isinstance(run.get("arms"), list) and bool(run["arms"])
    if (manifest is None or manifest != current_tree or run.get("bindingError")
            or run.get("timedOut") or ("outputTail" not in run
                and not compared and not (isinstance(run.get("sourceReference"), str) and run["sourceReference"]))):
        raise WorkflowError("execution reference is stale, unbound, incomplete or not an executed receipt")
    if "outputTail" not in run and not run.get("testId") and not compared:
        return execution_receipt(identity, state, run["sourceReference"], transaction)
    return run, manifest


def commit_verification(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    run: JsonObject,
    *,
    tree_before: dict[str, str] | None,
    report: JsonObject | None = None,
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
        kept = [evidence_write(str(state["workflowId"]), "quality-gate-report", {"report": [report]})] if report is not None else []
        if kept:
            run["reportEvidenceId"] = kept[0].evidence_id
        typed = run.get("kind") == "quality-gate"
        observed = run.get("kind") == "observed"
        try:
            current: dict[str, str] | None = tree_manifest(identity)
        except RuntimeError as exc:
            current, sampling_error = None, f"the reviewable tree could not be sampled at commit: {exc}"
        if tree_before is not None and (run.get("valid") is True or observed):
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
        # Observed runs are receipts only; `verify --from-evidence` binds one.
        for item in (run for run in runs if run.get("kind") != "observed"):
            if item.get("valid") is True and isinstance(item.get("replacedKey"), str):
                latest.pop(item["replacedKey"], None)
            latest[_verification_key(item)] = item.get("valid") is True
        status = "passed" if latest and all(latest.values()) else "pending"
        if not observed:
            _apply_step(identity, state, "verification", status)
        if typed and run["valid"] is True:
            manifest = manifest_write(str(state["workflowId"]), "quality-gate-tree", tree_before)
            manifests.append(manifest)
            quality_manifest_id: str | None = manifest.manifest_id
        elif isinstance(prior_manifest_id, str) and (observed or (  # an observed run is a receipt only
                latest.get("quality-gate") is True and current is not None
                and transaction.manifest(prior_manifest_id) == current)):
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
            evidence=[*kept, write],
            manifests=manifests,
        ), write.evidence_id, run


def evidence_document(identity: RepoIdentity, evidence_id: str | None) -> JsonObject | None:
    if not evidence_id:
        return None
    envelope = read_evidence(identity, evidence_id)
    document = envelope.get("document") if isinstance(envelope, dict) else None
    return document if isinstance(document, dict) else None


def graph_projection(identity: RepoIdentity, value: object, candidate: str) -> JsonObject:
    projection = validate_advisor_projection(value)
    indexed = str(projection["indexedCandidateTree"])
    if indexed == candidate:
        return projection
    if not analysis_unchanged(identity, indexed, candidate):
        raise ValueError("advisor projection does not describe the active candidate tree")
    return {**projection, "reuse": {"candidateTree": candidate,
                                   "basis": "unchanged graph inputs and Python source positions"}}


def _own_graph(document: object, slug: object, workflow_id: object) -> bool:
    return (isinstance(document, dict) and type(document.get("schemaVersion")) is int and document.get("schemaVersion") == 1
            and document.get("slug") == slug and document.get("workflowId") == workflow_id)


def _graph_candidate_ready(
    document: object, candidate: str, *, identity: RepoIdentity, slug: object, workflow_id: object,
) -> bool:
    if not _own_graph(document, slug, workflow_id):
        return False
    try:
        graph_projection(identity, document.get("advisorProjection"), candidate)
    except ValueError:
        return False
    return True


def refresh_context(identity: RepoIdentity) -> None:
    """Re-record Repo Context Forge evidence for the active candidate, only when the recorded
    projection no longer describes it; the consumers that need a current snapshot call this."""
    state = _require_state(read_workflow(identity))
    document = evidence_document(identity, state.get("repoContextForgeEvidence"))
    if not _own_graph(document, state.get("slug"), state.get("workflowId")):
        return  # missing, foreign or corrupt evidence is refused by its consumer, never refreshed over
    try:
        validate_advisor_projection(document.get("advisorProjection"))
    except ValueError:
        return
    try:
        candidate = _active_candidate_tree(identity)
    except OSError:
        return  # an uncapturable tree is reported by the consumer's own capture
    if _graph_candidate_ready(document, candidate, identity=identity,
                              slug=state.get("slug"), workflow_id=state.get("workflowId")):
        return
    command = next_operation(identity, {**state, "nextAction": "repo-context-forge"})["command"]
    result = subprocess.run(shlex.split(command), cwd=identity.root, capture_output=True, text=True, check=False)
    if result.returncode:
        raise WorkflowError("Repo Context Forge refresh failed: " + (result.stderr or result.stdout).strip()[-600:])


def _finding_kind(finding: JsonObject) -> str:
    """Classify ledger obligations without rewriting the reviewer's description."""
    return "nonbehavioral" if finding.get("kind") == "nonbehavioral" else "behavioral"


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
                    observed.append((root, ref, document, {**finding, "kind": _finding_kind(finding)}))
                    break
        latest[(str(root["evidenceId"]), str(root["id"]))] = entry
    references: set[str] = set()
    pending: list[tuple[JsonObject, JsonObject, bool]] = []
    for finding in intake["findings"]:
        item = {**finding, "kind": _finding_kind(finding)}
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
        # The producer that raised a finding may re-rate it; its latest materiality stands.
        conceded = bool(prior) and item["material"] is False and prior.get("producer") == intake.get("producer")
        if prior and prior.get("status") in {"pending", "accepted-for-proof", "accepted-follow-up"}:
            reference = str(prior["intakeEvidenceId"])
            if (prior["findingId"] != item["id"]
                    or any(prior.get(k) != intake.get(k) for k in ("producer", "stage"))
                    or not any(finding["id"] == item["id"] and _finding_kind(finding) == item["kind"]
                               for finding in intakes[reference]["findings"])):
                reference = intake_id
            prior["material"] = False if conceded else prior["material"] or item["material"]
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
    preflight_draft: JsonObject | None = None,
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    if findings not in {None, "pending"}:
        raise ValueError("advisor-result records findings=pending; disposition findings with advisor-disposition")
    if stage == "final" and intake is None:
        raise ValueError("a final advisor-result records the advisor finding envelope (--input)")
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
        if intake is not None:
            intake["candidateTree"] = expected_candidate_tree or _active_candidate_tree(identity)
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
            if state.get("preflightLatestEvidence") and verdict in {"approved", "changes-required"}:
                raise WorkflowError("preflight is already recorded; draft consultations are closed")
            if verdict not in {"completed", "unavailable", "approved", "changes-required"}:
                raise ValueError("preflight verdict must be approved, changes-required, completed or unavailable")
            if verdict in {"approved", "changes-required"}:
                if intake is None or preflight_draft is None:
                    raise WorkflowError("draft advice requires its reviewed preflight artifact")
                if verdict == "changes-required" and state.get("preflightRounds"):
                    raise WorkflowError("the second preflight consult is the last: the advisor edits the draft "
                                        "in place and returns approved")
                intake["preflightDraft"] = preflight_draft
                state["preflightRounds"] = int(state.get("preflightRounds", 0)) + 1
            measured_reason = str(reason or "").strip() or None
            if verdict == "unavailable" and not measured_reason:
                raise ValueError("preflight unavailable requires --reason")
            recorded_reason = measured_reason if verdict == "unavailable" else None
            if intake is not None:
                intake_write = evidence_write(
                    str(state["workflowId"]), "finding-intake-preflight", intake,
                )
                writes.append(intake_write)
                intake_reference = (intake_write.evidence_id if verdict in {"approved", "changes-required"} else
                                    _register_finding_intake(transaction, state, intake_write.evidence_id, intake, intakes))
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
            if not state.get("finalStarted") and (drift := _binding_drift(identity, state, "review", transaction)):
                raise WorkflowError(f"the reviewed tree changed after the lead review: {drift}")
            if drift := _binding_drift(identity, state, "quality-gate", transaction):
                raise WorkflowError(f"the quality gate did not cover the current tree: {drift}")
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            if verdict == "context-mismatch":
                mismatch = evidence_write(str(state["workflowId"]), "finding-context-mismatch-final", intake)
                writes.append(mismatch)
                state["finalReviewContextMismatchEvidence"] = mismatch.evidence_id
            else:
                state["finalStarted"] = True  # the final advisor's tree is now the reviewed tree
                reviewed = manifest_write(str(state["workflowId"]), "final-review-tree", tree_manifest(identity))
                state["reviewManifestId"] = reviewed.manifest_id
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
                correction = _stage_unresolved(state, stage, source, rejected)
                # A requested reassessment of a dispositioned candidate, like the re-consult of a
                # mismatched one, is a fresh final result with its own single appeal.
                fresh = not rejected and not correction and (
                    state.get("nextAction") == "complete-workflow" or bool(state.get("finalReviewContextMismatchEvidence")))
                if state.get("finalAppealConsumed") and not fresh and (
                    rejected or isinstance(record, dict) and record.get("status") != "pending"
                ):
                    raise WorkflowError("final appeal already consumed")
                if rejected and correction:
                    raise WorkflowError("final appeal is blocked by unresolved final-review work")
                if not rejected and not fresh and isinstance(record, dict) and record.get("status") != "pending":
                    raise WorkflowError("final review result already recorded for the current candidate")
                if rejected:
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
                    intake_write = evidence_write(str(state["workflowId"]), "finding-intake-final", intake)
                    writes.append(intake_write)
                    intake_reference = _register_finding_intake(
                        transaction, state, intake_write.evidence_id, intake, intakes,
                    )
                    state.pop("finalAppealConsumed", None)
                    if verdict == "commit-ready":  # the re-check judged every pending final finding: they are settled
                        for entry in finding_states:
                            if (isinstance(entry, dict) and entry.get("stage") == "final" and entry.get("producer") == source
                                    and _finding_unresolved(entry)):
                                entry.update(status="resolved", dispositionEvidenceId=intake_reference)
                    state["finalReview"] = {
                        "source": source, "status": verdict, "intakeEvidence": intake_reference,
                        "findings": "pending" if _stage_unresolved(state, stage, source) else "none",
                    }
                    # A later final is sent only the change since the tree this verdict judged; an
                    # appeal of it re-judges from the base this verdict itself was sent.
                    state["judgedBase"] = state.get("judgedTree")
                    state["judgedTree"] = expected_candidate_tree or _active_candidate_tree(identity)
                state.pop("finalReviewContextMismatchEvidence", None)
                state["phase"] = "final-review"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction, state, f"advisor-{stage}-result", evidence=writes,
            manifests=[reviewed] if stage == "final" and verdict != "context-mismatch" else (),
        )


def _require_open(state: JsonObject) -> None:
    if state.get("phase") == "complete" and not state.get("revalidation"):
        raise WorkflowError("workflow is terminal after completion; begin a new pass")


def _require_instance(state: JsonObject, slug: str | None, workflow_id: str | None) -> None:
    if slug is not None and state.get("slug") != safe_slug(str(slug)):
        raise WorkflowError(SLUG_MISMATCH)
    if workflow_id is not None and instance_id(state) != workflow_id:
        raise WorkflowError(INSTANCE_MISMATCH)


def _active_for_slug(state: JsonObject | None, slug: str | None) -> JsonObject:
    """The active open workflow; an explicit slug must name it."""
    value = _require_state(state)
    _require_open(value)
    _require_instance(value, slug, None)
    return value


def _bound_instance_state(state: JsonObject | None, slug: str | None, workflow_id: str | None) -> JsonObject:
    value = _active_for_slug(state, slug)
    if instance_id(value) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    _require_instance(value, None, workflow_id)
    return value


def bound_state(identity: RepoIdentity, slug: str | None = None, workflow_id: str | None = None) -> JsonObject:
    value = _active_for_slug(read_workflow(identity), slug)
    _require_instance(value, None, workflow_id)
    return value


def instance_id(state: JsonObject) -> str | None:
    value = state.get("workflowId")
    return value if isinstance(value, str) and value else None


def pause(identity: RepoIdentity, slug: str, workflow_id: str | None, reason: str, *,
          expected_candidate_tree: str | None = None) -> JsonObject:
    if not (cleaned := reason.strip()):
        raise ValueError("pause requires a non-empty --reason")
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        state["paused"] = {"reason": cleaned, "at": utc_timestamp()}
        state = _commit(transaction, state, "pause")
    return public_status(state, identity, candidate_tree=expected_candidate_tree, recovery=True, fields=set(state)) if expected_candidate_tree else state


def _behavioral_finding_closure(intake_id: str, finding_id: str, *, owned,
                                pending, admit_pending=False) -> None:
    linked = owned.get((intake_id, finding_id), {})
    if not linked:
        raise WorkflowError(f"finding {finding_id} requires an owning probe with its finding sourceRef")
    if admit_pending:
        return
    unresolved = [identifier for identifier, entry in linked.items()
                  if identifier in pending or not behavior_map.producer_proved(entry)]
    if unresolved:
        raise WorkflowError(f"finding {finding_id} requires current successful owning comparisons: {', '.join(unresolved)}")


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
) -> list[str]:
    if items is None:
        items = _recorded_items(identity, state)
    pending = behavior_map.unresolved(items)
    return (["unresolved Behavior Map items: " + ", ".join(pending)] if pending else []) + _finding_proof_blockers(
        None, state, items=items, pending=set(pending),
    )


def _finding_proof_blockers(
    transaction: LedgerMutation | None, state: JsonObject, *, items: list[JsonObject] | None = None,
    pending: set[str] | None = None,
) -> list[str]:
    states = state.get("findingStates", [])
    if not isinstance(states, list):
        return ["finding lifecycle evidence is corrupt"]
    if items is None:
        items = _map_items(transaction.evidence(state.get("tddEvidence")))
        if items is None:
            items = _map_items(transaction.evidence(state.get("preflightEvidence"))) or []
    if pending is None:
        pending = set(behavior_map.unresolved(items))
    owned = _linked_finding_items(transaction, items=items, state=state)
    blockers: list[str] = []
    for entry in states:
        if isinstance(entry, dict) and entry.get("status") == "fixed" and entry.get("kind") == "behavioral":
            try:
                _behavioral_finding_closure(
                    str(entry.get("intakeEvidenceId")), str(entry.get("findingId")),
                    owned=owned,
                    pending=pending,
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
    document = json.loads(json.dumps(document))
    intake = transaction.evidence(document.get("intakeEvidenceId"))
    if not isinstance(intake, dict) or intake.get("workflowId") != state["workflowId"]:
        raise WorkflowError("receipt disposition requires an owned immutable intake")
    findings = {item["id"]: item for item in intake.get("findings", [])}
    receipts: dict[str, JsonObject] = {}
    proof_document = transaction.evidence(state.get("tddEvidence"))
    items = _map_items(proof_document) or []
    from .tdd_workflow import refresh_proof
    refresh_proof(identity, items, state)
    owned = _linked_finding_items(transaction, items=items, state=state)
    for item in document["dispositions"]:
        finding = findings.get(item["finding_id"])
        if finding is None:
            raise WorkflowError("receipt disposition references a finding outside its intake")
        item["kind"] = _finding_kind(finding)
        owners = {}
        if item["status"] == "fixed":
            owners = owned.get((document["intakeEvidenceId"], item["finding_id"]), {})
            selected = []
            for identifier, owner in owners.items():
                proof = owner.get("comparison")
                if not proof or not proof.get("fresh"):
                    raise WorkflowError(f"finding {item['finding_id']} operation {identifier} has no current comparison")
                selected.append(f"{state['tddEvidence']}:{proof['runIndex']}")
            if not selected:
                raise WorkflowError(f"finding {item['finding_id']} has no owning comparisons to resolve")
            item["evidenceRefs"] = selected
        item.setdefault("evidenceRefs", [])
        for reference in item["evidenceRefs"]:
            if reference not in receipts:
                try:
                    receipts[reference], _ = execution_receipt(identity, state, reference, transaction)
                except WorkflowError as exc:
                    raise WorkflowError(f"finding {item['finding_id']} receipt {reference}: {exc}") from exc
    if document.get("context") is None:
        document["context"] = {"workflowId": state["workflowId"], "candidateTree": _active_candidate_tree(identity)}
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
    items = _map_items(transaction.evidence(state.get("tddEvidence")))
    if items is None:
        items = _map_items(transaction.evidence(state.get("preflightEvidence"))) or []
    from .tdd_workflow import refresh_proof
    refresh_proof(transaction.identity, items, state)
    owned = _linked_finding_items(transaction, items=items, state=state)
    pending = set(behavior_map.unresolved(items))
    for disposition in dispositions:
        identifier, status = str(disposition["finding_id"]), str(disposition["status"])
        kind = str(disposition["kind"])
        finding_state = intake_states.get(identifier)
        if finding_state is None:
            raise WorkflowError(f"finding {identifier} has no immutable intake state")
        current = finding_state.get("status")
        if kind != _finding_kind(findings[identifier]):
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
                        intake_id, identifier,
                        owned=owned, pending=pending,
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
    return _stage_unresolved(state, stage, producer)


def _flag_disposition(
    transaction: LedgerMutation, state: JsonObject, flag: JsonObject, writes: list[EvidenceWrite],
) -> tuple[str, JsonObject]:
    """Resolve one flag disposition to its stage and receipt document.

    The finding's current advisor intake supplies stage and intake; `behaviorId`
    becomes the finding's owning attack on the current map, in this transaction.
    """
    finding_id = str(flag["finding"])
    matches = [entry for entry in state.get("findingStates", [])
               if (entry.get("findingId") == finding_id or any(ref.get("id") == finding_id for ref in entry.get("observations", [])))
               and entry.get("producer") in {*REVIEW_SOURCES, "code-review"}
               and entry.get("status") in {"pending", "accepted-for-proof", "accepted-follow-up"}]
    if len(matches) != 1:
        raise WorkflowError(f"finding {finding_id} is not one unresolved finding of this workflow")
    entry = matches[0]
    finding_id = str(entry["findingId"])
    behavior_id = flag.get("behaviorId")
    if behavior_id:
        field = "tddEvidence" if state.get("tddEvidence") else "preflightEvidence"
        document = json.loads(json.dumps(transaction.evidence(state.get(field)) or {}))
        items = _map_items(document)
        if items is None:
            raise WorkflowError("--behavior-id requires a recorded Behavior Map")
        try:
            behavior_map.apply_dispositions(items, [{"id": str(behavior_id), "sourceRefs": [
                {"type": "finding", "evidenceId": entry["intakeEvidenceId"], "id": finding_id}]}])
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        holder = document if "behaviorMap" in document else document["document"]
        holder["behaviorMap"] = items
        write = evidence_write(str(state["workflowId"]), "tdd" if field == "tddEvidence" else "preflight", document)
        transaction.write([write])
        writes.append(write)
        state[field] = write.evidence_id
    # The lead's judgment doubles as the repair mechanism a behavioral fixed needs.
    judgment = {"reason": flag["reason"], "mechanism": flag["reason"]} if flag.get("reason") else {}
    return str(entry["stage"]), {"intakeEvidenceId": entry["intakeEvidenceId"], "dispositions": [{
        "finding_id": finding_id, "status": flag["status"],
        **judgment,
        **({"reference": flag["reference"]} if flag.get("reference") else {})}]}


def advisor_disposition(
    identity: RepoIdentity,
    slug: str | None,
    workflow_id: str | None,
    stage: str | None,
    findings: str,
    *,
    flag: JsonObject | None = None,
    expected_candidate_tree: str | None = None,
) -> JsonObject:
    if findings not in {"none", "addressed"}:
        raise ValueError("advisor disposition requires --findings none or addressed")
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        writes: list[EvidenceWrite] = []
        document = None
        if flag is not None:
            stage, document = _flag_disposition(transaction, state, flag, writes)
        if stage is None:
            stage = "final" if state.get("finalReview", {}).get("intakeEvidence") else "preflight"
        if stage not in {"preflight", "final", "code-review"}:
            raise ValueError(f"unsupported finding stage: {stage}")
        if findings == "addressed" and document is None:
            raise ValueError("an addressed disposition requires --finding and a decision")
        if findings == "none" and document is not None:
            raise ValueError("a findings-none disposition carries no document")
        if stage == "preflight" and state.get("revalidation"):
            raise WorkflowError(PREFLIGHT_CLOSED)
        state.pop("paused", None)
        field = {"preflight": "advisorPreflight", "final": "finalReview", "code-review": "codeReview"}[stage]
        record = state.get(field)
        recorded = stage == "code-review" or (
            isinstance(record, dict)
            and record.get("source") in REVIEW_SOURCES
            and (record.get("status") in {"completed", "approved", "changes-required"} if stage == "preflight" else record.get("status") in FINAL_VERDICTS)
        )
        source = "code-review" if stage == "code-review" else record.get("source") if isinstance(record, dict) else None
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
        if document is not None:
            document = {"schemaVersion": 1, "slug": state["slug"], "workflowId": state["workflowId"],
                        "stage": stage, "recordedAt": utc_timestamp(), **document}
            document = _resolve_disposition_receipts(identity, transaction, state, document)
            _validate_disposition_context(identity, state, document)
            document = _linked_disposition_document(state, document, stage, str(source))
            write = evidence_write(str(state["workflowId"]), f"advisor-disposition-{stage}", document)
            writes.append(write)
            _apply_finding_dispositions(
                transaction, state, str(document["intakeEvidenceId"]), document["dispositions"], stage,
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
            if stage == "code-review":
                record["status"] = "pending" if unresolved or _binding_drift(identity, state, "review") else "passed"
            if document is not None:
                record["dispositionEvidence"] = write.evidence_id
            state["phase"] = {"preflight": "advisor-preflight", "final": "final-review", "code-review": "code-review"}[stage]
        state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, f"advisor-{stage}-disposition", evidence=writes)


def completion_missing(state: JsonObject) -> list[str]:
    """Every registry step not ready, by its state field."""
    missing = [field for field, ready in STEPS.values() if not ready(state)]
    return missing if instance_id(state) else ["workflowId", *missing]


CHECKPOINT_PHASES = {"preflight-advice", "code-review", "final-review"}


def _recorded_items(
    identity: RepoIdentity, state: JsonObject,
) -> list[JsonObject]:
    """Read the current map, falling back only when absent, not when corrupt."""
    fields = ("preflightLatestEvidence",) if state.get("preflight") == "pending" else ()
    for field in (*fields, "tddEvidence", "preflightEvidence"):
        evidence_id = state.get(field)
        items = _map_items(evidence_document(identity, evidence_id if isinstance(evidence_id, str) else None))
        if items is not None:
            from .tdd_workflow import refresh_proof
            refresh_proof(identity, items, state)
            return items
    return []





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
        measurement = {key: measured[key] for key in ("premise", "occurrence", "materialConsequence", "evidence",
                                                      "reference", "reason", "evidenceRefs")
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
                           ("id", "behavior", "expected", "seam")},
                        "executedCommands": behavior_map.executed_commands(item)}
                       for item in attacks.values()],
            "measurement": measurement,
        })
    return ledger


# Advisor evidence channels in prompt order: (name, description). The wrapper
# frames whatever this manifest lists, so a new channel is a change here only.
CHANNELS = (
    ("intent", "original request: the completeness oracle this pass answers to"),
    ("advisor-projection", "advisor projection (schemaVersion 1)"),
    ("behavior-map", "preflight artifact / current probe list with bound comparison outcomes: challenge expectations and coverage"),
    ("finding-ledger", "finding and attack ledger: each finding's immutable claim beside its owning attacks"),
    ("diff", "current-pass diff: passStartOid^{tree} -> activeCandidateTree; a deleted file is its header and line count"),
)


def checkpoint(identity: RepoIdentity, phase: str, *, reconsult: bool = False,
               channel_dir: str | None = None, preflight_draft: JsonObject | None = None) -> JsonObject:
    if phase not in CHECKPOINT_PHASES:
        raise ValueError(f"unsupported checkpoint phase: {phase}")
    if reconsult and phase != "preflight-advice":
        raise ValueError("--reconsult requires preflight-advice")
    state = _require_state(read_workflow(identity))
    workflow_id = instance_id(state)
    candidate = _active_candidate_tree(identity)
    revalidation = bool(state.get("revalidation"))
    terminal = state.get("phase") == "complete" and not revalidation
    open_for_phase = not terminal and not (phase == "preflight-advice" and revalidation)
    stage_actions = {
        "preflight-advice": {"preflight"},
        "final-review": {"final-review", "appeal-final-review", "re-consult-final-review", "complete-workflow"},
    }
    reviewing = phase == "code-review"
    requirements = (
        ("workflowId", workflow_id is not None),
        ("open-workflow", open_for_phase),
        ("advisor-stage", reviewing or reconsult or state.get("nextAction") in stage_actions[phase]
         or phase == "final-review" and _review_assessed(state)),
        ("passStartOid", _is_commit_oid(identity, state.get("passStartOid"))),
        *(
            (("repo-context-forge", _allows_next(state, "repo-context-forge")),)
            if phase == "preflight-advice"
            else (
                ("tdd", _allows_next(state, "tdd")),
                ("verification", _allows_next(state, "verification")),
                *(() if reviewing else (("code-review", _allows_next(state, "code-review") or _review_assessed(state)),)),
            )
        ),
    )
    missing = [name for name, ready in requirements if not ready]
    if phase == "preflight-advice":
        if preflight_draft is None:
            missing.append("preflight draft (--preflight-file)")
        if state.get("preflightLatestEvidence"):
            missing.append("preflight already recorded; draft consultations are closed")
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
            projection = graph_projection(identity, graph_document.get("advisorProjection"), candidate)
        except ValueError as exc:
            missing.append(str(exc))
    design_evidence_id = state.get("governedDesignEvidence")
    if isinstance(design_evidence_id, str):
        try:
            validate_design_declaration(evidence_document(identity, design_evidence_id))
        except ValueError as exc:
            missing.append(str(exc))
    items = _recorded_items(identity, state)
    if phase == "final-review":
        missing.extend(() if state.get("nextAction") in ("appeal-final-review", "re-consult-final-review")
                       else correction_blockers(identity, state, items=items))
        if not state.get("finalStarted") and (drift := _binding_drift(identity, state, "review")):
            missing.append(drift)
        if drift := _binding_drift(identity, state, "quality-gate"):
            missing.append(drift)
    review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    result: JsonObject = {
        "schemaVersion": 1,
        "phase": phase,
        "ready": not missing,
        "missing": missing,
        "slug": state.get("slug"),
        "workflowId": state.get("workflowId"),
        "nextAction": state.get("nextAction"),
        "sessionMode": "create" if phase == "preflight-advice" and not reconsult else "resume",
        "passStartOid": state.get("passStartOid"),
        "activeCandidateTree": candidate,
        "tdd": state.get("tdd"),
        "codeReviewStatus": review.get("status"),
    }
    if channel_dir is None:
        return result
    ledger = _finding_ledger(identity, state, items)
    contract = ((evidence_document(identity, state.get("preflightEvidence")) or {}).get("document") or {}).get("authoritativeContract")
    views: dict[object, JsonObject] = {}
    rendered = []
    for item in items:
        entry = {key: value for key, value in item.items() if key != "comparison"}
        if run := item.get("comparison"):
            # Each shared comparison renders once; later owners reference it by run index.
            entry["comparison"] = ({key: run[key] for key in ("runIndex", "valid", "fresh") if key in run}
                                   if run["runIndex"] in views else views.setdefault(run["runIndex"], behavior_map.comparison_view(run)))
        rendered.append(entry)
    map_content = ({"preflightInterpretation": contract, **_readiness_lines(identity, state, items), "items": rendered}
                   if contract or items else None)
    if phase == "preflight-advice" and preflight_draft is not None:
        previous = evidence_document(identity, (state.get("advisorPreflight") or {}).get("intakeEvidence")) or {}
        prior = previous.get("preflightDraft") if reconsult else None
        draft_id = evidence_write(str(workflow_id), "preflight-draft", preflight_draft).evidence_id
        map_content = {"digest": draft_id, "draft": preflight_draft}
        if prior is not None:
            map_content = {"digest": draft_id,
                           "baseDigest": evidence_write(str(workflow_id), "preflight-draft", prior).evidence_id,
                           "delta": "".join(difflib.unified_diff(
                               json.dumps(prior, sort_keys=True, indent=2).splitlines(keepends=True),
                               json.dumps(preflight_draft, sort_keys=True, indent=2).splitlines(keepends=True),
                               fromfile="last-recorded-draft", tofile="current-draft"))}
    since = state.get("judgedBase" if state.get("nextAction") == "appeal-final-review" else "judgedTree") \
        if phase == "final-review" else None
    values: dict[str, tuple[object, object]] = {
        "intent": (None, state.get("intent") or None),
        "advisor-projection": (evidence_id, projection),
        "behavior-map": (None, map_content or None),
        "finding-ledger": (None, ledger or None),
        "diff": (None, None if "passStartOid" in missing else current_pass_evidence(
            str(identity.root), f"{state['passStartOid']}^{{tree}}", candidate, since=since)),
    }
    result["channels"] = []
    for position, (name, description) in enumerate(CHANNELS):
        evidence, content = values[name]
        if not content:
            continue
        data = content if isinstance(content, bytes) else (
            content if isinstance(content, str) else json.dumps(content, sort_keys=True, separators=(",", ":"))).encode("utf-8")
        if since and name == "diff":  # the pass's last recorded final verdict judged everything through `since`
            description = f"the whole pass's changed files (numstat), then the diff since {since}, judged by the last final"
        path = os.path.join(channel_dir, f"{position}-{name}")
        with open(path, "wb") as handle:
            handle.write(data)
        result["channels"].append({"name": name, "evidenceId": evidence, "bytes": len(data),
                                   "contentPath": path, "description": description})
    return result


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
        from .tdd_workflow import refresh_proof
        items = behavior_map.recorded_map(tdd_document, preflight_document) or []
        refresh_proof(identity, items, state)
        missing = behavior_map.unresolved(items) + _finding_proof_blockers(transaction, state, items=items) + _finding_state_blockers(state) + completion_missing(state)
        graph_id = state.get("repoContextForgeEvidence")
        graph_document = transaction.evidence(graph_id) if isinstance(graph_id, str) else None
        if (
            not _graph_candidate_ready(
                graph_document, _active_candidate_tree(identity),
                identity=identity, slug=state.get("slug"), workflow_id=state.get("workflowId"),
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
        state.pop("finalStarted", None)
        state["phase"] = "complete"
        state["nextAction"] = "delivery-and-reviewer-completion"
        return _commit(transaction, state, "complete")


def _reset_downstream(state: JsonObject) -> None:
    if not state.get("finalStarted"):
        _clear_verification(state)
    _reset_reviews(state)
    state["nextAction"] = _derive_next_action(state)


def _reset_reviews(state: JsonObject) -> None:
    """Reopen review; once the final advisor has judged the pass, only its re-check reopens."""
    if not state.get("finalStarted"):
        state["codeReview"] = {"status": "pending", "findings": "pending"}
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state.pop("finalReviewContextMismatchEvidence", None)


def invalidate_after_edit(identity: RepoIdentity, path: str | None) -> tuple[JsonObject | None, list[str]]:
    """Observe the candidate once; return actual paths for the hook's local feedback."""
    with mutation(identity) as transaction:
        state = transaction.state
        changed = [path] if path is not None else []
        if state is None:
            return None, sorted(set(changed)
                | set(_paths(identity, "ls-files", "--modified", "--others", "--exclude-standard", "-z"))
                | set(_paths(identity, "diff", "--cached", "--name-only", "-z")))
        candidate = _active_candidate_tree(identity)
        uncomparable = False
        try:
            observed = _paths(identity, "diff", "--no-renames", "--name-only", "-z", str(state["activeCandidateTree"]), candidate)
        except RuntimeError:
            observed = _paths(identity, "ls-files", "--modified", "--others", "--exclude-standard", "-z")
            observed += _paths(identity, "diff", "--cached", "--name-only", "-z")
            uncomparable = True
        changed = sorted(set(changed) | set(observed))
        governance = uncomparable or any(is_governance_path(p) for p in changed)
        reviewable = (state.get("phase") != "complete" and not state.get("revalidation")
                      and (uncomparable or any(is_reviewable_path(p) for p in changed)))
        if not (reviewable or governance):
            return state, changed
        if not governance and not state.get("finalStarted") and _binding_drift(identity, state, "quality-gate", transaction) is None:
            if state.get("activeCandidateTree") != candidate:
                state["activeCandidateTree"] = candidate
                return _commit(transaction, state, "verified-candidate-observed"), changed
            return state, changed
        def material(value: JsonObject) -> str:
            return json.dumps({k: v for k, v in value.items() if k != "nextAction"},
                              sort_keys=True)

        before, before_next = material(state), state.get("nextAction")
        state["activeCandidateTree"] = candidate
        state.pop("paused", None)
        if reviewable:
            state["phase"] = "implementation"
            kind = "production-edit-invalidated"
        else:
            kind = "governance-edit-invalidated"
            if state.get("phase") == "complete":
                state["revalidation"] = True
        _reset_downstream(state)
        # A repeated observation must preserve the producer's nextAction and
        # avoid a duplicate ledger event when no material state changed.
        if material(state) == before:
            if before_next is not None:
                state["nextAction"] = before_next
            return state, changed
        return _commit(transaction, state, kind), changed


def review_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Lead prerequisites for dispatch, without recording or rerunning proof."""
    missing = [phase for phase in SEQUENCE[:SEQUENCE.index("code-review")] if not _allows_next(state, phase)]
    if not missing:
        if drift := _binding_drift(identity, state, "quality-gate"):
            missing.append(drift)
    return missing


def ready_for_edit(identity: RepoIdentity, path: str) -> tuple[bool, list[str]]:
    state = read_workflow(identity)
    if state is None:
        return False, ["active workflow"]
    if state.get("revalidation") or state.get("phase") == "complete":
        return False, ["new active workflow" + (" (governance revalidation permits verification/review only)"
                                               if state.get("revalidation") else "")]
    missing = [name for name, phase in (("repo-context-forge", "repo-context-forge"),
               ("production preflight", "preflight")) if not _allows_next(state, phase)]
    return not missing, missing


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
    graph_id = state.get("repoContextForgeEvidence")
    graph_document = (
        evidence_document(identity, graph_id)
        if graph_needed and identity is not None and isinstance(graph_id, str)
        else None
    )
    ready = _allows_next(state, "repo-context-forge") and (
        candidate is None or _graph_candidate_ready(
            graph_document, candidate,
            identity=identity, slug=state.get("slug"), workflow_id=state.get("workflowId"),
        )
    )
    stored = state.get("repoContextForge")
    result = {
        **state,
        **({"activeCandidateTree": candidate} if candidate is not None else {}),
        "repoContextForge": stored if ready or stored != "passed" else "pending",
        "gitnexus": "passed" if ready else "pending",
    }
    if recovery and identity is not None:
        drift = _binding_drift(identity, state, "quality-gate") if state.get("qualityGateEvidence") else None
        if drift:
            result.update(verification="pending", bindingError=drift)
        if drift:  # a stale projection is refreshed by the consumer that needs it, not routed to the lead
            result["nextAction"] = _derive_next_action({**result, "repoContextForge": stored})
    if fields is None:
        # The recorded task text is multi-KB and already in the caller's context; it is
        # read back on request (--fields intent) and by the advisor checkpoint, never by default.
        result.pop("intent", None)
        return result
    return {key: value for key, value in result.items() if key in fields}


def _earned_split(identity: RepoIdentity, state: JsonObject, labels: bool) -> str:
    items = _recorded_items(identity, state)
    return f" Probes compared={sum(behavior_map.producer_proved(entry) for entry in items)}/{len(items)}." if items else ""


def _map_listing(identity: RepoIdentity, state: JsonObject) -> str:
    """The open map a resumed lead still owes: never-compared ids grouped, then each open
    question; last in the line so a cap cut takes ids, never the invariant or the command."""
    lines = _readiness_lines(identity, state)
    never = lines.get("never", [])
    groups = [*(["pending: " + ", ".join(never)] if never else []),
              *(line for line in lines.get("open", []) if line.split(" ", 1)[0] not in never),
              *(f"contract change: {change}" for change in lines.get("contractChanges", []))]
    return " Open map: " + "; ".join(groups) + "." if groups else ""


def _readiness_lines(identity: RepoIdentity, state: JsonObject, items: list[JsonObject] | None = None) -> JsonObject:
    """The one readiness result every consumer renders: open questions and contract changes.
    A caller that already loaded the current map passes it rather than loading it again."""
    try:
        items = _recorded_items(identity, state) if items is None else items
        recorded = behavior_map.recorded_map(None, evidence_document(identity, state.get("preflightEvidence")))
    except (WorkflowError, LedgerError, ValueError):
        return {}
    opened = behavior_map.open_obligations(items)
    return {"open": opened, "openCount": len(opened), "never": behavior_map.never_compared(items),
            "contractChanges": behavior_map.contract_changes(items, recorded)}


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


def next_operation(identity: RepoIdentity, state: JsonObject, receipt: JsonObject | None = None, *,
                   preflight_draft: JsonObject | None = None, preflight_file: str | None = None,
                   obligations: bool = True) -> JsonObject:
    """Bind the selected operation once for command results and recovery."""
    scripts = Path(__file__).resolve().parents[2] / "skills"
    cli = [sys.executable, str(scripts / "repo-production-workflow/scripts/workflow.py")]
    bound = ["--repo", str(identity.root), "--slug", str(state["slug"]),
             "--workflow-id", str(state["workflowId"])]
    action = state.get("nextAction")
    questions = ""
    if (receipt is not None and receipt.get("kind") == "tdd"
            or action in {"tdd", "run-mapped-tdd", "verification", "repo-context-forge", "code-review"}):
        comparison = receipt or {}
        if "arms" not in comparison:
            runs = (evidence_document(identity, state.get("tddEvidence")) or {}).get("runs", [])
            candidate = _active_candidate_tree(identity) if runs else None
            comparison = next((run for run in reversed(runs) if run.get("candidateTree") == candidate), {})
        # A receipt carrying its own `open` list states the questions once, there.
        if obligations and "open" not in (receipt or {}):
            questions = "\n".join(_readiness_lines(identity, state).get("open", []))
        incomplete = [str(arm.get("error") or "execution incomplete") for arm in comparison.get("arms", [])
                      if arm["outcome"] == "incomplete"]
        if incomplete:
            return {"command": None, "input": "Comparison execution incomplete: " + "; ".join(dict.fromkeys(incomplete))
                    + ("\n" + questions if questions else ""), "action": "tdd"}
    if receipt is not None and receipt.get("kind") == "observed" and action not in {"tdd", "run-mapped-tdd"}:
        if receipt.get("valid") is True:
            return {"command": shlex.join([*cli, "verify", *bound, "--from-evidence",
                                          f"{receipt['evidenceId']}:{receipt['runIndex']}"])}
        return {"command": None, "input": "correct the failed operation; observation leaves verification unchanged"}
    if state.get("phase") == "complete" and not state.get("revalidation"):
        return {"command": None}
    if action == "repo-context-forge":
        command = [sys.executable, str(scripts / "repo-context-forge/scripts/bootstrap.py"),
                   "--repo", str(identity.root), "--workflow-slug", str(state["slug"]),
                   "--base", str(state.get("baseOid") or state["passStartOid"])]
        if not state.get("repoContextForgeEvidence"):
            return {"command": shlex.join([*command, "--mode", "intent"])}
        command += ["--revalidate"]
    elif action == "verification":
        command = [*cli, "verify", *bound, "--kind", "quality-gate"]
        if not state.get("baseOid"):
            command += ["--base-ref", str(state["passStartOid"])]
        if receipt and receipt.get("kind") == "tdd":
            return {"command": None, "input":
                    "Complete TDD's required post-edit loop: "
                    + str(scripts / "tdd/SKILL.md") + "#required-probe-loop. "
                    "Then continue with " + shlex.join(command) + ("\n" + questions if questions else "")}
        return {"command": shlex.join(command), **({"input": questions} if questions else {})}
    elif action == "complete-workflow":
        command = [*cli, "complete", *bound]
    elif action in {"preflight", "final-review", "re-consult-final-review", "appeal-final-review"}:
        preflight = action == "preflight"
        advice = state.get("advisorPreflight") or {}
        intake = (evidence_document(identity, advice.get("intakeEvidence")) or {}) if preflight else {}
        draft = intake.get("preflightDraft")
        if (preflight and advice.get("status") == "approved" and intake.get("verdict") == "approved"
                and isinstance(draft, dict) and (preflight_draft is None or state.get("preflightRounds", 0) >= 2
                or json.dumps(draft, sort_keys=True) == json.dumps(preflight_draft, sort_keys=True))):
            return {"command": shlex.join([*cli, "record", "preflight", *bound])}
        design = repo_state_dir(identity) / "designs" / f"{state['workflowId']}.md"
        declaration = evidence_document(identity, state.get("governedDesignEvidence")) or {}
        if preflight or declaration:
            command = [str(scripts / "codex-advisor/scripts/ask-codex-advisor.sh"), "--slug", str(state["slug"]),
                       "--phase", "preflight-advice" if preflight else "final-review", "--cwd", str(identity.root)]
            needed = ["review question on stdin"]
            if preflight:
                if design.is_file():
                    command += ["--design-file", str(design)]
                elif declaration.get("status") == "absent":
                    command += ["--design-absent", str(declaration["reason"])]
                else:
                    needed.append("--design-file <path> or --design-absent <reason>")
            if preflight:
                if isinstance(draft, dict):
                    command += ["--reconsult"]
                if preflight_file:
                    command += ["--preflight-file", preflight_file]
                else:
                    needed.append("--preflight-file <current-draft.json>")
            return {"command": shlex.join(command), "input": "; ".join(needed)}
        command = [*cli, "paths", "--repo", str(identity.root), "--workflow-id", str(state["workflowId"])]
    elif action in {"tdd", "run-mapped-tdd", "code-review", "classify-current-findings",
                    "close-current-findings", "address-review-findings"}:
        producer = {"tdd": ["tdd"], "run-mapped-tdd": ["tdd"], "address-review-findings": ["tdd"],
                    "code-review": ["record", "review"]}.get(str(action))
        if producer is None:
            producer = ["record", "advisor-disposition"]
        command = [*cli, *producer, *bound] if "record" in producer else [*cli, *producer, *bound[:4]]
        pending = [{"findingId": f["findingId"]}
                   for f in state.get("findingStates", []) if _finding_unresolved(f)]
        if producer[-1] == "advisor-disposition" and pending:
            command += ["--finding", str(pending[0]["findingId"])]
        operation: JsonObject = {"command": shlex.join(command + (["--input", "-"] if producer[-1] == "review" else [])),
                                "help": shlex.join([*cli, *producer, "--help"]),
                                "input": {
                                    "review": "independent review findings on stdin",
                                    "advisor-disposition": "--fixed, --rejected, --report-only or --follow-up REFERENCE; --reason for the judgment",
                                    "tdd": "--behavior-id and real command after --",
                                }[producer[-1]]}
        if pending:
            operation["findings"] = pending
        if questions:
            operation["input"] += "\n" + questions
        return operation
    else:
        command = [*cli, "status", "--repo", str(identity.root)]
    return {"command": shlex.join(command), **({"input": questions} if questions else {})}


def operation_receipt(state: JsonObject, identity: RepoIdentity, **details: object) -> JsonObject:
    """Return the committed operation's result and its current continuation."""
    if CHECK_ONLY.get():
        return details
    current = public_status(state, fields={"schemaVersion", "workflowId", "slug", "phase", "nextAction"})
    if details.get("kind") == "tdd":
        # The receipt carries what the lead must act on; contract-change history stays with review.
        details.update({key: value for key, value in _readiness_lines(identity, state).items() if key != "contractChanges"})
    operation = next_operation(identity, {**state, **current}, details)
    return {"nextAction": operation.pop("action", current.get("nextAction")), "next": operation,
            **{key: value for key, value in current.items() if key != "nextAction"}, **details}


def summary(identity: RepoIdentity, limit: int = 3000, *, labels: bool = True) -> str:
    """The pass for a resuming lead; the compaction re-arm drops the review labels
    which include settled review labels."""
    state = read_workflow(identity)
    if state is None:
        return "Workflow state unavailable; do not infer that any workflow step passed."
    state = public_status(state, identity, recovery=True,
                          fields=set(state) | {"activeCandidateTree", "bindingError"})
    gate_drift = state.get("bindingError")
    advisor = state.get("advisorPreflight") if isinstance(state.get("advisorPreflight"), dict) else {}
    code_review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    final_review = state.get("finalReview") if isinstance(state.get("finalReview"), dict) else {}
    records = {"advisor-preflight": f"{advisor.get('status')}/{advisor.get('findings')}",
               "code-review": f"{code_review.get('status')}/{code_review.get('findings')}",
               "final-review": f"{final_review.get('status')}/{final_review.get('findings')}"}
    operation = next_operation(identity, state, obligations=False)  # the open map below carries them once
    action = operation.pop("action", state.get("nextAction"))
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
        f"candidate={state.get('activeCandidateTree')} phase={state.get('phase')} next={action}. "
        + "\nNext invocation: " + str(operation["command"] or ("none; see input" if operation.get("input") else "none; workflow complete")) + "\n"
        + "".join(f"{key}: {value}\n" for key, value in operation.items() if key != "command")
        + (f"Binding: {gate_drift}. " if gate_drift else "")
        + " ".join(f"{field}={state[field]}" for field in (
            "preflightLatestEvidence", "tddEvidence", "verificationLatestEvidence", "qualityGateManifestId") if state.get(field)) + ". "
        # Evidence-aware, not the raw status: a compacted session reads this line, and
        # a legacy pass that claims the phase without producer evidence is pending
        # everywhere else in the workflow.
        # Open work only: a settled step or record is context the lead already holds.
        + "Open: " + (", ".join(
            f"{name}={records.get(name) or state.get(STEPS[name][0]) or 'pending'}" for name in (*STEPS, "advisor-preflight")
            if not (STEPS[name][1](state) if name in STEPS else advisor.get("findings") not in {None, "pending"})
        ) or "none") + ". "
        + _earned_split(identity, state, labels)
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
