"""Repository-scoped production workflow policy and transactional commands."""
from __future__ import annotations

import difflib
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
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
from .workflow_documents import ADVISOR_SHAPES, RECORD_SHAPES, validate_advisor_projection, validate_design_declaration, quality_candidates
from .state_store import (
    _EXECUTION,
    _active_candidate_tree,
    _candidate_matches,
    _comparison_scope,
    _flock,
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
FINAL_ACTIONS = {"final-review", "re-consult-final-review", "complete-workflow", "address-review-findings"}  # the final advisor may judge
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
    """Derive readiness from bound proof, including reusable evidence after a reverted edit."""
    if not state or state.get("phase") == "complete" or not (state.get("tddEvidence") or state.get("tdd") in {"passed", "not-required"}):
        return state
    documents = (evidence(state.get("tddEvidence")), evidence(state.get("preflightEvidence")))
    readiness = _readiness_lines(identity, state, documents=documents)
    status = "in-progress" if readiness["open"] else "passed" if state.get("tddEvidence") else state["tdd"]
    restore_review = status == "passed" and readiness["reviewCurrent"] and state.get("codeReview") != {"status": "passed", "findings": "none"}
    if state.get("tdd") != status or restore_review:
        state["tdd"] = status
        if restore_review:
            state["codeReview"] = {"status": "passed", "findings": "none"}
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
    return review.get("source") in REVIEW_SOURCES and review.get("status") == "commit-ready"


# The one step registry, in workflow order: step -> (state field, readiness).
# Sequence, predecessors and nextAction,
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
    if phase == "final-review" and _review_assessed(state):
        return
    position = SEQUENCE.index(phase)
    if position and not _allows_next(state, SEQUENCE[position - 1]):
        raise WorkflowIncomplete(f"{phase} requires {SEQUENCE[position - 1]}")


def _derive_next_action(state: JsonObject, tdd_document: JsonObject | None = None) -> str:
    if state.get("finalReviewContextMismatchEvidence"):
        return "re-consult-final-review"
    phase = next((phase for phase in SEQUENCE if not _allows_next(state, phase)), "complete-workflow")
    if phase == "final-review":
        review = state.get("finalReview") or {}
        if state.get("finalStarted") and (review.get("status") == "pending" or review.get("findings") == "addressed"):
            return "re-consult-final-review"
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
        previous = instance_id(transaction.state or {})
        result = transaction.append(state, "begin", activate=True)
    if previous:
        _discard_execution_tree(identity, previous)
    return result


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


def commit_tdd(identity: RepoIdentity, slug: str, workflow_id: str | None,
               summary_doc: JsonObject, *, expected_evidence_id=None, tree_before=None, selected_only=False) -> tuple[JsonObject, str, JsonObject | None]:
    """Publish comparisons; full calls also judge readiness."""
    with _ledger_mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("revalidation") and tree_before is None:
            raise WorkflowError(TDD_CLOSED)  # a rerun of the mapped items re-verifies; the map stays closed
        _require_predecessor(state, "tdd")
        if not state.get("preflightEvidence") or summary_doc.get("workflowId") != state["workflowId"]:
            raise WorkflowError("comparison requires this workflow's recorded preflight")
        if state.get("tddEvidence") != expected_evidence_id:
            raise WorkflowError("probe obligations changed during execution; retry against current evidence")
        items = behavior_map.recorded_map(summary_doc) or []
        manifests = []
        candidate = _active_candidate_tree(identity)
        if tree_before is not None and summary_doc["runs"]:
            measured_tree = summary_doc.get("candidateTree", summary_doc["runs"][-1]["candidateTree"])
            run = next(run for run in reversed(summary_doc["runs"]) if run["candidateTree"] == measured_tree)
            drift = _manifest_drift(tree_before, tree_manifest(identity), stale="candidate changed during comparison")
            if not _candidate_matches(identity, run["candidateTree"]):
                drift = drift or "candidate tree changed during comparison"
            if drift:
                run.update(valid=False, bindingError=drift)
                for entry in items:
                    if entry.get("comparison", {}).get("candidateTree") == run["candidateTree"]:
                        entry["comparison"].update(valid=False, bindingError=drift)
        from .tdd_workflow import _evaluation, _retired
        if "evaluation" not in summary_doc:
            summary_doc["evaluation"] = _evaluation(identity, state, transaction.evidence(state.get("tddEvidence")))
        readiness = None if selected_only else _readiness_lines(identity, state, items,
                    documents=(summary_doc, None), candidate=candidate)
        behavior_map.retain_evaluation(items, {item["id"] for item in items}, summary_doc["evaluation"], summary_doc["runs"], _retired(state))
        pending = readiness is None or bool(readiness["open"])
        runs = summary_doc["runs"]
        used = {case["comparison"]["runIndex"] for case in behavior_map.current_cases(items, runs) if case.get("comparison")}
        used.update(entry["comparison"]["runIndex"] for entry in items if entry.get("comparison"))
        used.update(entry["runIndex"] for entry in summary_doc["evaluation"].values())
        used.update(summary_doc.get("comparedRuns", []))
        controls = {run.get("candidateKey"): index for index, run in enumerate(runs)}
        used.update(controls[runs[index]["controlKey"]] for index in list(used) if runs[index].get("controlKey") in controls)
        positions = {old: new for new, old in enumerate(sorted(used))}
        summary_doc["runs"] = [{**runs[old], "runIndex": new} for old, new in positions.items()]
        summary_doc["behaviorMap"] = [{**entry, "comparison": {
            "runIndex": positions[proof["runIndex"]], "obligation": proof["obligation"],
            "caseRuns": {command: positions[index] for command, index in proof.get("caseRuns", {}).items() if index in positions}}}
            if (proof := entry.get("comparison")) else entry for entry in items]
        summary_doc["evaluation"] = {key: {**entry, "runIndex": positions[entry["runIndex"]]}
                                     for key, entry in summary_doc["evaluation"].items()}
        summary_doc.pop("comparedRuns", None)
        write = evidence_write(str(state["workflowId"]), "tdd", summary_doc)
        state["tddEvidence"] = write.evidence_id
        state["activeCandidateTree"] = candidate
        cost_writes = _record_costs(transaction, state, [run for run in summary_doc["runs"] if run["candidateTree"] == candidate])
        state["tdd"] = "in-progress" if pending else "passed"
        state["phase"] = "tdd"
        state.pop("paused", None)
        if pending and (not selected_only or transaction.manifest(state.get("reviewManifestId")) != tree_before):
            _reset_reviews(state)
        elif readiness and readiness["reviewCurrent"]:
            state["codeReview"] = {"status": "passed", "findings": "none"}
        state["nextAction"] = _derive_next_action(state, summary_doc)
        return _commit(transaction, state, "tdd-comparison", evidence=[write, *cost_writes], manifests=manifests), write.evidence_id, readiness


def _record_costs(transaction, state, runs):
    from .tdd_workflow import _work_errors
    findings = []
    for run in runs:
        if not (errors := _work_errors(run)):
            continue
        identifier = "COST-" + hashlib.sha256(run["command"].encode()).hexdigest()[:16]
        entry = next((entry for entry in state.get("findingStates", []) if entry["findingId"] == identifier), None)
        if entry:
            if _finding_unresolved(entry):
                continue
            disposition = transaction.evidence(entry.get("dispositionEvidenceId"), restore_runs=False) or {}
            if any(item.get("rejected") and item.get("priorFinding", {}).get("id") == identifier for item in disposition.get("findings", [])):
                intake = transaction.evidence(entry["intakeEvidenceId"], restore_runs=False) or {}
                previous = next((item for item in intake.get("findings", []) if item["id"] == identifier), {})
                if previous.get("execution", {}).get("comparison", {}).get("candidateKey") == run.get("candidateKey"):
                    continue
        comparison = {key: value for key, value in run.items() if key in {
            "command", "timeout", "support", "probeFiles", "probeTree", "originalTree", "candidateTree", "execution", "candidateKey"}}
        findings.append({"id": identifier, "claim": "\n".join(errors), "kind": "behavioral", "material": True,
                         "probe": {"command": run["command"]}, "execution": {"kind": "cost", "candidate": "failed", "comparison": comparison}})
    if not findings:
        return []
    document = {"workflowId": state["workflowId"], "slug": state["slug"], "stage": "code-review", "producer": "code-review",
                "candidateTree": state["activeCandidateTree"], "tddEvidence": state["tddEvidence"], "findings": findings, "runs": []}
    write = evidence_write(state["workflowId"], "finding-intake-code-review", document)
    _register_finding_intake(transaction, state, write.evidence_id, document, {})
    return [write]


def _finding_unresolved(entry: JsonObject) -> bool:
    return ((entry.get("status") in {"pending", "accepted-for-proof"} and entry.get("material") is not False)
            or (entry.get("status") == "accepted-follow-up" and entry.get("material") is True))


def _settle_executed(state: JsonObject, execution: JsonObject, evidence_id: str) -> None:
    """Binding predicates settle when they pass; observations with no binding case remain report lines."""
    if state.get("tddEvidence") != execution["measuredEvidence"]:
        raise WorkflowError("comparison evidence changed during finding execution; record the intake again")
    binding = [_finding_state(state, evidence_id, item["id"]) for item in execution["findings"] if item["material"]]
    outcomes = []
    for result in execution["reexecuted"]:
        run, entry = result["execution"], _finding_state(state, result["evidenceId"], result["id"])
        if any(entry is current for current in binding):
            continue
        settled = run["kind"] == "claim" or "error" not in run and run.get("candidate") != "timed-out" and (run.get("candidate") == "passed" if run["kind"] in {"probe", "cost"}
                                          else (run["candidate"] == "passed") == run["applied"])
        outcomes.append((entry, settled))
    for entry, _ in outcomes:  # a finding settles only when every binding case it holds does
        if entry is not None:
            entry.update(status="resolved" if all(done for other, done in outcomes if other is entry) else "pending",
                         dispositionEvidenceId=evidence_id)


def _stage_unresolved(state: JsonObject, stage: str, source: str) -> bool:
    """Whether any registered finding of this stage and producer is still open."""
    return any(_finding_unresolved(entry) and any(ref.get("stage") == stage and ref.get("producer") == source
                                                  for ref in (entry, *entry.get("observations", [])))
               for entry in state.get("findingStates") or [] if isinstance(entry, dict))


def _review_evidence(state: JsonObject, intake: JsonObject) -> None:
    if intake.get("tddEvidence") != state.get("tddEvidence"):
        raise WorkflowError("reviewed comparison evidence changed; inspect the current checkpoint")


def commit_findings(
    identity: RepoIdentity, slug: str, workflow_id: str | None, summary_doc: JsonObject, execution: JsonObject,
) -> tuple[JsonObject, str]:
    """Publish executed findings; only an explicit independent verdict completes review."""
    with mutation(identity, expected_candidate_tree=execution["measuredTree"]) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if summary_doc.get("candidateTree") != execution["measuredTree"]:
            raise WorkflowError("review names a different candidate tree; obtain a current review")
        if "tddEvidence" not in summary_doc:
            raise WorkflowError("review must name the comparison evidence it assessed")
        seen = transaction.evidence(summary_doc["tddEvidence"], restore_runs=False) if summary_doc["tddEvidence"] else None
        if summary_doc["tddEvidence"] is not None and (not seen or seen.get("workflowId") != state["workflowId"] or "behaviorMap" not in seen or "runs" not in seen):
            raise WorkflowError("review comparison evidence is absent or foreign")
        _review_evidence(state, summary_doc)
        summary_doc["runs"] = execution["runs"]
        write = evidence_write(str(state["workflowId"]), "finding-intake-" + str(summary_doc["stage"]), summary_doc)
        manifest: ManifestWrite | None = None
        intake = summary_doc.get("findings", [])
        reference = _register_finding_intake(transaction, state, write.evidence_id, summary_doc, {})
        _settle_executed(state, execution, write.evidence_id)
        if "verdict" not in summary_doc:
            if summary_doc["stage"] == "code-review":
                state["codeReviewIntakeEvidence"] = write.evidence_id
            else:
                state.setdefault("finalReview", {})["intakeEvidence"] = write.evidence_id
            state["nextAction"] = _derive_next_action(state)
            return _commit(transaction, state, "tdd-finding", evidence=[write]), write.evidence_id
        unresolved = _stage_unresolved(state, "code-review", "code-review")
        unresolved = unresolved or summary_doc.get("verdict") != "complete"
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



def finding_references(state: JsonObject) -> str:
    return hashlib.sha256(json.dumps([{"evidenceId": ref["evidenceId"], "id": ref["id"]}
            for entry in state.get("findingStates", []) if entry.get("material") is not False
            for ref in [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]}, *entry.get("observations", [])]], sort_keys=True).encode()).hexdigest()


def commit_retained_checks(identity: RepoIdentity, slug: str, workflow_id: str, execution: JsonObject,
                           references: str) -> JsonObject:
    with _ledger_mutation(identity, expected_candidate_tree=execution["measuredTree"]) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if finding_references(state) != references:
            raise WorkflowError("finding references changed during verification; verify current findings")
        document = {"workflowId": workflow_id, "candidateTree": execution["measuredTree"], "findingRefs": references,
                    "tddEvidence": execution["measuredEvidence"], "runnerKey": execution["runnerKey"],
                    "inputIdentities": execution["inputIdentities"], "runs": execution["runs"]}
        write = evidence_write(workflow_id, "retained-checks", document)
        _settle_executed(state, execution, write.evidence_id)
        state.setdefault("retainedChecks", {})[execution["measuredTree"]] = write.evidence_id
        return _commit(transaction, state, "retained-checks", evidence=[write])


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
        _apply_step(identity, state, phase, status)
        write = evidence_write(str(state["workflowId"]), phase, evidence_doc)
        state[latest_field] = write.evidence_id
        if status == "passed":
            state[f"{field}Evidence"] = write.evidence_id
            if phase == "preflight" and behavior_map.recorded_map(evidence_doc) == []:
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


def commit_verification(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    run: JsonObject,
    *,
    tree_before: dict[str, str] | None,
    report: JsonObject | None = None,
) -> tuple[JsonObject, str, JsonObject]:
    """Merge one completed typed-gate run with the verification evidence current at commit.

    The runner hands over its finished run and the reviewable-tree manifest it
    sampled before the command started; nothing is read ahead of execution, so
    a competing completion that landed meanwhile is merged rather than refused
    and recorded order is completion order. A valid run whose reviewable tree
    differs at commit is retained invalid, naming the drift: its result
    describes another tree. `tree_before` is None only when the runner could
    not sample it, and that run already carries its reason as invalid.
    Readiness and the gate binding follow the latest completion.
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
        run = dict(run)
        kept = [evidence_write(str(state["workflowId"]), "quality-gate-report", {"report": [report]})] if report is not None else []
        if kept:
            run["reportEvidenceId"] = kept[0].evidence_id
        try:
            current: dict[str, str] | None = tree_manifest(identity)
        except RuntimeError as exc:
            current, sampling_error = None, f"the reviewable tree could not be sampled at commit: {exc}"
        if tree_before is not None and run.get("valid") is True:
            drift = (
                _manifest_drift(tree_before, current, stale="reviewable tree changed during the quality-gate run")
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
        status = "passed" if run["valid"] is True else "pending"
        _apply_step(identity, state, "verification", status)
        quality_manifest_id: str | None = None
        if status == "passed":
            manifest = manifest_write(str(state["workflowId"]), "quality-gate-tree", tree_before)
            manifests.append(manifest)
            quality_manifest_id = manifest.manifest_id
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


def evidence_document(identity: RepoIdentity, evidence_id: str | None, *, restore_runs: bool = True) -> JsonObject | None:
    if not evidence_id:
        return None
    envelope = read_evidence(identity, evidence_id, restore_runs=restore_runs)
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
                                   "basis": "unchanged graph input bytes"}}


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
    intakes[intake_id] = intake
    for item in intake["findings"]:
        if item.get("rejected") is True:
            prior = item.get("priorFinding") or {}
            document = transaction.evidence(prior.get("evidenceId"), restore_runs=False)
            observed = next((finding for finding in (document or {}).get("findings", []) if finding["id"] == prior.get("id")), None)
            if (not document or document.get("workflowId") != state["workflowId"]
                    or any(document.get(key) != intake.get(key) for key in ("stage", "producer"))
                    or not observed or not ("probe" in observed or "change" in observed)
                    or "probe" in item or "change" in item or not str(item.get("claim") or "").strip()):
                raise WorkflowError("withdrawal must name this author's recorded executable observation and its reason")
            intakes[str(prior["evidenceId"])] = document
            for entry in finding_states:  # a withdrawn observation's retirements end with it
                if "retires" in entry:
                    entry["retires"] = [case for case in entry["retires"] if case["observation"] != prior]
    # Read immutable observations once; the state carries only their identities.
    observed: list[tuple[JsonObject, JsonObject, JsonObject, JsonObject]] = []
    latest: dict[tuple[str, str], JsonObject] = {}
    for entry in finding_states:
        refs = [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"],
                 "stage": entry["stage"], "producer": entry["producer"]}, *entry.get("observations", [])]
        if not any((item.get("priorFinding") == entry.get("canonicalFinding") or
                    any(item.get("priorFinding") == {"evidenceId": ref["evidenceId"], "id": ref["id"]} for ref in refs))
                   if item.get("priorFinding") else
                   (_finding_kind(item) == entry.get("kind") == "behavioral" or
                    any(item["id"] == ref["id"] and all(ref.get(key) in (None, intake[key]) for key in ("stage", "producer"))
                        for ref in refs)) for item in intake["findings"]):
            continue
        root = entry.get("canonicalFinding") or {
            "evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"],
        }
        refs = [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]},
                *entry.get("observations", [])]
        for ref in refs:
            reference = str(ref["evidenceId"])
            if reference not in intakes:
                previous = transaction.evidence(reference, restore_runs=False)
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
        if prior and prior.get("status") in {"pending", "accepted-for-proof", "accepted-follow-up"}:
            reference = str(prior["intakeEvidenceId"])
            if (prior["findingId"] != item["id"]
                    or any(prior.get(k) != intake.get(k) for k in ("producer", "stage"))
                    or not any(finding["id"] == item["id"] and _finding_kind(finding) == item["kind"]
                               for finding in intakes[reference]["findings"])):
                reference = intake_id
            prior["material"] = prior["material"] or item["material"]
            if item["kind"] == "behavioral":
                prior["kind"] = "behavioral"
            pending.append((prior, item, any(all(finding.get(k) == item.get(k) for k in ("id", "claim", "kind", "material", "probe", "change", "retires"))
                       and all(document.get(k) == intake.get(k) for k in ("producer", "stage"))
                       for root, ref, document, finding in observed
                       if (str(root["evidenceId"]), str(root["id"])) in matches)))
        else:
            reference = intake_id
            entry = {
                "producer": intake["producer"], "stage": intake["stage"],
                "intakeEvidenceId": intake_id, "findingId": item["id"],
                "material": item["material"], "kind": item["kind"], "status": "pending",
                **({"retires": [{**case, "observation": {"evidenceId": intake_id, "id": item["id"]}} for case in item["retires"]]}
                   if item.get("retires") else {}),
            }
            if prior:
                root = next(iter(matches))
                entry["canonicalFinding"] = {"evidenceId": root[0], "id": root[1]}
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
        live = {(case["id"], case["key"]) for case in prior.get("retires", [])}  # an unindexed repeat adds nothing it lacks
        if (reference == intake_id or intake["producer"] == "code-review" or not unchanged
                or any((case["id"], case["key"]) not in live for case in item.get("retires", []))):
            observation = {"evidenceId": intake_id, "id": item["id"]}
            prior.setdefault("observations", []).append({**observation, "producer": intake["producer"], "stage": intake["stage"]})
            if item.get("retires"):  # each retirement stands on the indexed author observation naming it; its withdrawal ends it
                prior["retires"] = [*prior.get("retires", []), *({**case, "observation": observation} for case in item["retires"])]
    return reference


def draft_diff(before: JsonObject, after: JsonObject, source: str, target: str) -> str:
    return "".join(difflib.unified_diff(json.dumps(before, sort_keys=True, indent=2).splitlines(keepends=True),
                                        json.dumps(after, sort_keys=True, indent=2).splitlines(keepends=True),
                                        fromfile=source, tofile=target))


def record_advisor_result(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    stage: str,
    source: str,
    verdict: str,
    *,
    reason: str | None = None,
    design: JsonObject | None = None,
    intake: JsonObject | None = None,
    expected_candidate_tree: str | None = None,
    preflight_draft: JsonObject | None = None,
    submitted_draft: JsonObject | None = None,
    execution: JsonObject | None = None,
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    if stage == "final" and (intake is None or execution is None):
        raise ValueError("a final advisor-result records the advisor finding envelope (--input), executed")
    with mutation(identity, expected_candidate_tree=None if stage == "preflight" else expected_candidate_tree) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        writes: list[EvidenceWrite] = []
        intake_reference: str | None = (state.get("advisorPreflight") or {}).get("intakeEvidence") if stage == "preflight" else None
        intakes: dict[str, JsonObject] = {}
        if intake is not None and any(intake.get(field) != expected for field, expected in (
            ("workflowId", state["workflowId"]), ("stage", stage),
            ("producer", source), *((("verdict", verdict),) if stage == "preflight" else ()),
        )):
            raise WorkflowError("advisor finding intake does not match this workflow result")
        replayed_design = False
        if intake is not None:
            if stage == "preflight":
                intake["candidateTree"] = expected_candidate_tree or _active_candidate_tree(identity)
            elif intake.get("candidateTree") != _active_candidate_tree(identity):
                raise WorkflowError("final review names a different candidate tree")
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
            if verdict not in {"unavailable", "approved", "changes-required"}:
                raise ValueError("preflight verdict must be approved, changes-required or unavailable")
            if verdict in {"approved", "changes-required"}:
                if intake is None or preflight_draft is None:
                    raise WorkflowError("draft advice requires its reviewed preflight artifact")
                intake["preflightDraft"] = preflight_draft
                if submitted_draft not in (None, preflight_draft):
                    intake["draftEdits"] = draft_diff(submitted_draft, preflight_draft, "submitted-draft", "approved-draft")
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
                intake_reference = intake_write.evidence_id
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
                "status": "pending" if verdict == "approved" and state.get("preflightRounds") == 1 else verdict,
                "findings": (current or {}).get("findings", "none") if verdict == "unavailable" and intake_reference else "pending" if verdict == "changes-required" else "none",
                "reason": recorded_reason,
                **({"intakeEvidence": intake_reference} if intake_reference else {}),
            }
            if not state.get("preflightEvidence"):
                state["phase"] = "advisor-preflight"
        elif stage == "final":
            if intake is not None:
                seen = transaction.evidence(intake["tddEvidence"], restore_runs=False) if intake["tddEvidence"] else None
                if intake["tddEvidence"] is not None and (not seen or seen.get("workflowId") != state["workflowId"] or "behaviorMap" not in seen or "runs" not in seen):
                    raise WorkflowError("final review comparison evidence is absent or foreign")
            _review_evidence(state, intake)
            state.pop("paused", None)
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            ready = (all(_allows_next(state, phase) for phase in SEQUENCE[:SEQUENCE.index("code-review")])
                     and _allows_next(state, "code-review")
                     and (state.get("finalStarted") or not _binding_drift(identity, state, "review", transaction))
                     and not _binding_drift(identity, state, "quality-gate", transaction))
            if verdict == "commit-ready" and not ready:
                raise WorkflowIncomplete("commit-ready requires current proof, verification and independent review")
            if verdict == "context-mismatch":
                mismatch = evidence_write(str(state["workflowId"]), "finding-context-mismatch-final", intake)
                writes.append(mismatch)
                state["finalReviewContextMismatchEvidence"] = mismatch.evidence_id
            else:
                intake["runs"] = execution["runs"]
                intake_write = evidence_write(str(state["workflowId"]), "finding-intake-final", intake)
                writes.append(intake_write)
                intake_reference = _register_finding_intake(
                    transaction, state, intake_write.evidence_id, intake, intakes,
                )
                _settle_executed(state, execution, intake_reference)
                state["finalReview"].update(status="pending", intakeEvidence=intake_write.evidence_id)
                if ready:
                    state["finalStarted"] = True
                    reviewed = manifest_write(str(state["workflowId"]), "final-review-tree", tree_manifest(identity))
                    state["reviewManifestId"] = reviewed.manifest_id
                    state["finalReview"] = {
                        "source": source, "status": verdict, "intakeEvidence": intake_write.evidence_id,
                        "findings": "pending" if _stage_unresolved(state, stage, source) else "none",
                    }
                    state["judgedTree"] = expected_candidate_tree or _active_candidate_tree(identity)
                    state.pop("finalReviewContextMismatchEvidence", None)
                    state["phase"] = "final-review"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction, state, f"advisor-{stage}-result", evidence=writes,
            manifests=[reviewed] if stage == "final" and verdict != "context-mismatch" and ready else (),
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


def _finding_state(state: JsonObject, intake_id: object, finding_id: object) -> JsonObject | None:
    return next((entry for entry in state.get("findingStates", [])
                 if (entry.get("intakeEvidenceId") == intake_id and entry.get("findingId") == finding_id)
                 or any(ref.get("evidenceId") == intake_id and ref.get("id") == finding_id
                        for ref in entry.get("observations", []))), None)


def completion_missing(state: JsonObject) -> list[str]:
    """Every registry step not ready, by its state field."""
    missing = [field for field, ready in STEPS.values() if not ready(state)]
    return missing if instance_id(state) else ["workflowId", *missing]


CHECKPOINT_PHASES = {"preflight-advice", "code-review", "final-review"}


def _finding_ledger(
    identity: RepoIdentity, state: JsonObject, items: list[JsonObject],
) -> list[JsonObject]:
    """Every recorded finding's immutable claim and status, with each binding case the runner re-executes."""
    from .tdd_workflow import _retained
    cases: dict[int, list[JsonObject]] = {}  # every binding case; the ledger is a file the advisor reads on demand
    for entry, finding in _retained(identity, state):
        cases.setdefault(id(entry), []).append({key: finding[key] for key in ("ref", "probe", "change", "execution") if key in finding})
    states = state.get("findingStates")
    intakes: dict[str, dict[str, JsonObject]] = {}
    ledger: list[JsonObject] = []
    for entry in states if isinstance(states, list) else []:
        if not isinstance(entry, dict):
            continue
        intake_id = str(entry.get("intakeEvidenceId"))
        if intake_id not in intakes:
            intake = evidence_document(identity, intake_id, restore_runs=False)
            intakes[intake_id] = {str(finding.get("id")): finding for finding in (intake or {}).get("findings", [])
                                  if isinstance(finding, dict)}
        recorded = intakes[intake_id].get(str(entry.get("findingId")), {})
        ledger.append({
            "intakeEvidenceId": intake_id,
            "producer": entry.get("producer"), "stage": entry.get("stage"),
            "findingId": entry.get("findingId"), "kind": entry.get("kind"),
            "material": entry.get("material"), "status": entry.get("status"),
            "claim": recorded.get("claim"),
            **({"cases": cases[id(entry)]} if id(entry) in cases else {}),
            **{key: entry[key] for key in ("canonicalFinding", "observations", "dispositionEvidenceId") if key in entry},
        })
    return ledger


def gate_candidates(identity: RepoIdentity, state: JsonObject) -> list[str]:
    evidence = evidence_document(identity, state.get("qualityGateEvidence")) or {}
    reference = (evidence.get("runs") or [{}])[-1].get("reportEvidenceId")
    report = (evidence_document(identity, reference) or {}).get("report") or []
    return quality_candidates(report[0]) if report else []


# Advisor evidence channels in prompt order: (name, description). The wrapper
# frames this manifest; the values below supply its current data.
CHANNELS = (
    ("intent", "original request: the completeness oracle this pass answers to"),
    ("advisor-projection", "advisor projection (schemaVersion 1)"),
    ("behavior-map", "preflight artifact / current probe list with bound comparison outcomes: challenge expectations and coverage"),
    ("finding-ledger", "finding ledger: each finding's immutable claim, status and binding cases"),
    ("quality-candidates", "non-blocking gate candidates: locations and matching rules; investigate their actual cost and necessity"),
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
        "final-review": FINAL_ACTIONS,
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
                ("preflight", _allows_next(state, "preflight")),
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
            missing.append("preflight is already recorded; draft consultations are closed")
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
    from .tdd_workflow import current_map
    items = current_map(identity, state)[0] or []
    if phase == "final-review":
        pending = [] if state.get("nextAction") == "re-consult-final-review" else _readiness_lines(identity, state, items)["open"]
        missing.extend(["unresolved named comparisons: " + ", ".join(pending)] if pending else [])
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
        "scratchPath": str(repo_state_dir(identity) / "execution-trees" / str(workflow_id) / "scratch"),  # kept with the pass's execution state
        "tddEvidence": state.get("tddEvidence"),
        "tdd": state.get("tdd"),
        "codeReviewStatus": review.get("status"),
    }
    if phase == "code-review":
        cli = Path(__file__).resolve().parents[2] / "skills/repo-production-workflow/scripts/workflow.py"
        result["verdictCommand"] = shlex.join([sys.executable, str(cli), "tdd", "--repo", identity.root,
            "--as", "review", "--workflow-id", str(workflow_id), "--expected-candidate-tree", candidate,
            "--tdd-evidence", str(state.get("tddEvidence") or "null")]) + " --verdict complete"
    else:
        shape = json.loads(ADVISOR_SHAPES["preflight" if phase == "preflight-advice" else "final"])
        if phase == "final-review":
            shape.update(workflowId=workflow_id, candidateTree=candidate, tddEvidence=state.get("tddEvidence"))
        result["responseFormat"] = json.dumps(shape)
    if state.get("tddEvidence"):
        cli = Path(__file__).resolve().parents[2] / "skills/repo-production-workflow/scripts/workflow.py"
        result["evidenceCommand"] = shlex.join([sys.executable, str(cli), "evidence", "--repo", identity.root,
            "--evidence-id", state["tddEvidence"]])
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
                           "delta": draft_diff(prior, preflight_draft, "last-recorded-draft", "current-draft")}
    since = state.get("judgedTree") if phase == "final-review" else None
    values: dict[str, tuple[object, object]] = {
        "intent": (None, state.get("intent") or None),
        "advisor-projection": (evidence_id, projection),
        "behavior-map": (None, map_content or None),
        "finding-ledger": (None, ledger or None),
        "quality-candidates": (state.get("qualityGateEvidence"), gate_candidates(identity, state) or None),
        "diff": (None, None if "passStartOid" in missing else current_pass_evidence(
            str(identity.root), f"{state['passStartOid']}^{{tree}}", candidate, since=since)),
    }
    result["channels"] = []
    Path(channel_dir).mkdir(parents=True, exist_ok=True)
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
    from .tdd_workflow import check_retained
    check_retained(identity, slug, workflow_id)
    with mutation(identity, expected_candidate_tree=expected_candidate_tree) as transaction:
        state = _require_state(transaction.state)
        _require_instance(state, slug, workflow_id)
        state.pop("paused", None)
        state.pop("revalidation", None)
        # Behavior Map closure and design coverage are judged from the evidence
        # this transaction sees, so a concurrent map change cannot slip through.
        tdd_document = transaction.evidence(state.get("tddEvidence"))
        preflight_document = transaction.evidence(state.get("preflightEvidence"))
        missing = _readiness_lines(identity, state, documents=(tdd_document, preflight_document))["open"] + _finding_state_blockers(state) + completion_missing(state)
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
        result = _commit(transaction, state, "complete")
    _discard_execution_tree(identity, state["workflowId"])
    return result


def _discard_execution_tree(identity: RepoIdentity, workflow_id: str) -> None:
    cache = repo_state_dir(identity) / "execution-trees" / workflow_id
    scope = _EXECUTION.get() or {}
    if owned := scope.get("cache_root") == cache:  # this command holds the lock; its trees are not published
        scope.pop("finish", None)
    if cache.exists():
        with contextlib.nullcontext() if owned else _flock(cache.with_suffix(".lock")):
            if cache.exists():
                shutil.rmtree(cache)


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


@_comparison_scope()
def invalidate_after_edit(identity: RepoIdentity, path: str | None) -> tuple[JsonObject | None, list[str]]:
    """Observe the candidate once; return actual paths for the hook's local feedback."""
    state = read_active(identity)
    candidate = _active_candidate_tree(identity)
    changed = [path] if path is not None else []
    if state is not None and state.get("activeCandidateTree") == candidate:
        return state, changed
    with _ledger_mutation(identity) as transaction:
        state = transaction.state
        candidate = _active_candidate_tree(identity)
        if state is not None and state.get("activeCandidateTree") == candidate:
            return state, changed
        if state is None:
            return None, sorted(set(changed)
                | set(_paths(identity, "ls-files", "--modified", "--others", "--exclude-standard", "-z"))
                | set(_paths(identity, "diff", "--cached", "--name-only", "-z")))
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


def public_status(state: JsonObject, identity: RepoIdentity | None = None, *,
                  fields: set[str] | None = None, candidate_tree: str | None = None,
                  recovery: bool = False) -> JsonObject:
    """Project evidence-bound state; unsupported graph claims stay pending."""
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


def _readiness_lines(identity: RepoIdentity, state: JsonObject, items: list[JsonObject] | None = None, *,
                     documents: tuple[JsonObject | None, JsonObject | None] | None = None,
                     review: JsonObject | None = None, candidate: str | None = None) -> JsonObject:
    """One readiness result: immutable named cases, with retained statements for review."""
    from .tdd_workflow import _evaluation, _retired, current_map, refresh_proof
    candidate = candidate or _active_candidate_tree(identity)
    if documents is None:
        items, document = current_map(identity, state, fresh=False) if items is None else (items, evidence_document(identity, state.get("tddEvidence")))
    else:
        document = documents[0]
        items = behavior_map.recorded_map(*documents) if items is None else items
    items = items or []
    evaluation = [{**entry, "caseKey": behavior_map.case_key(entry["id"], document["runs"][entry["runIndex"]]),
                   "comparison": dict(document["runs"][entry["runIndex"]])}
                  for entry in _evaluation(identity, state, document, True).values()]
    retired = _retired(state)
    observations = behavior_map.current_cases(items, (document or {}).get("runs", []), retired)
    refresh_proof(identity, [*observations, *evaluation], candidate=candidate, runs=(document or {}).get("runs", []))
    cases = behavior_map.open_obligations(items, evaluation, observations)
    review = review if review is not None else evidence_document(identity, state.get("codeReviewEvidence"))
    current = bool(review and review.get("workflowId") == state["workflowId"] and review.get("candidateTree") == candidate and review.get("verdict") == "complete" and not _stage_unresolved(state, "code-review", "code-review"))
    return {"open": cases, "reviewCurrent": current, "openCount": len(cases),
            "never": behavior_map.never_compared(items)}

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
    if obligations and ((receipt or {}).get("kind") == "tdd" or action in {"tdd", "run-mapped-tdd", "verification", "code-review"}):
        comparison = receipt or {}
        if "arms" not in comparison and "cases" not in comparison:
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
        return {"command": shlex.join(command), **({"input": questions} if questions else {})}
    elif action == "complete-workflow":
        command = [*cli, "complete", *bound]
    elif action in {"preflight", "final-review", "re-consult-final-review"}:
        preflight = action == "preflight"
        advice = state.get("advisorPreflight") or {}
        intake = (evidence_document(identity, advice.get("intakeEvidence")) or {}) if preflight else {}
        draft = intake.get("preflightDraft")
        if (preflight and advice.get("status") == "approved" and intake.get("verdict") == "approved"
                and isinstance(draft, dict) and (preflight_draft is None
                or json.dumps(draft, sort_keys=True) == json.dumps(preflight_draft, sort_keys=True))):
            return {"command": shlex.join([*cli, "record", "preflight", *bound])}
        design = repo_state_dir(identity) / "designs" / f"{state['workflowId']}.md"
        declaration = evidence_document(identity, state.get("governedDesignEvidence")) or {}
        if preflight or declaration:
            command = [str(scripts / "codex-advisor/scripts/ask-codex-advisor.sh"), "--slug", str(state["slug"]),
                       "--phase", "preflight-advice" if preflight else "final-review", "--cwd", str(identity.root)]
            needed = ["This consult takes several minutes. Wait for it in a single call of up to 300 seconds; do not set a shorter wait or yield limit anywhere, and do not poll. The call returns as soon as the consult finishes."]
            if preflight:
                if design.is_file():
                    command += ["--design-file", str(design)]
                elif declaration.get("status") == "absent":
                    command += ["--design-absent", str(declaration["reason"])]
            if preflight:
                if isinstance(draft, dict):
                    command += ["--reconsult"]
                if preflight_file:
                    command += ["--preflight-file", preflight_file]
                else:
                    needed.append("--preflight-file <current-draft.json>" + ("" if isinstance(draft, dict) else " shaped " + RECORD_SHAPES["preflight"]))
            return {"command": shlex.join(command), "input": "; ".join(needed)}
        command = [*cli, "paths", "--repo", str(identity.root), "--workflow-id", str(state["workflowId"])]
    elif action == "code-review":
        return {"command": None, "input": "Delegate code-review; its checkpoint supplies the verdict command. Reuse the reviewer for corrections; repair pending findings before return review."}
    elif action in {"tdd", "address-review-findings"}:
        command = [*cli, "tdd", *bound[:4]]
        pending = [{"findingId": f["findingId"]} for f in state.get("findingStates", []) if _finding_unresolved(f)]
        ids = [str(entry["id"]) for entry in behavior_map.recorded_map(evidence_document(
            identity, state.get("tddEvidence") or state.get("preflightEvidence"), restore_runs=False)) or []]
        operation: JsonObject = {"command": shlex.join(command),
                                "input": f"--case ID 'COMMAND' per case (recorded: {', '.join(ids) or 'none'}); use - for no item. Supply --change ID=FILE for each item’s behavior break. Omit cases to run every recorded command and frozen break."}
        if pending:
            operation["findings"] = pending
        if questions:
            operation["input"] += "\n" + questions
        return operation
    else:
        command = [*cli, "status", "--repo", str(identity.root)]
    return {"command": shlex.join(command), **({"input": questions} if questions else {})}


def operation_receipt(state: JsonObject, identity: RepoIdentity, *, readiness: JsonObject | None = None, obligations: bool = True, **details: object) -> JsonObject:
    """Return the committed operation's result and its current continuation."""
    if CHECK_ONLY.get():
        return details
    current = public_status(state, fields={"schemaVersion", "workflowId", "slug", "phase", "nextAction"})
    if obligations and details.get("kind") == "tdd":
        details.update(readiness if readiness is not None else _readiness_lines(identity, state))
    operation = next_operation(identity, {**state, **current}, details, obligations=obligations)
    return {"nextAction": operation.pop("action", current.get("nextAction")), "next": operation,
            **{key: value for key, value in current.items() if key != "nextAction"}, **details}


@_comparison_scope()
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
    operation = next_operation(identity, state, obligations=False)
    action = operation.pop("action", state.get("nextAction"))
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
        + (f" Advisor outage: {advisor.get('reason')}." if advisor.get("status") == "unavailable" else "")
        + (
            f" Paused: {str(paused.get('reason'))[:160]}."
            if isinstance(paused := state.get("paused"), dict)
            else ""
        )
        + " Missing state is pending, never success."
    )
    suffix = " … Details: workflow status --repo <checkout>; workflow evidence --repo <checkout> --evidence-id <id>."
    return text if len(text) <= limit else text[:max(0, limit - len(suffix))].rsplit(" ", 1)[0] + suffix
