#!/usr/bin/env python3
"""Recorder validation tests; these inputs do not prove that a review ran."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.tests.support import build_no_change_document, empty_advisor_envelope, empty_advisor_intake, record_context_forge  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.lib.workflow_state import advisor_disposition, commit_evidence_phase, read_workflow, record_advisor_result, set_phase  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"


class ReviewSummaryHarness(unittest.TestCase):
    """Fixture repository and review-stage drivers shared by the suites below;
    carries no tests of its own so subclasses never duplicate them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="review-summary-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        self.env.update({
            "CODEX_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        subprocess.run(["git", "init", "-q"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Harness"], cwd=self.repo, env=self.env, check=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.repo, env=self.env, check=True)
        begun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        identity = record_context_forge(self.repo, self.tmp)
        self.wid = read_workflow(identity)["workflowId"]
        record_advisor_result(identity, "review-summary", self.wid, "preflight", "codex-advisor", "completed",
                              intake=empty_advisor_intake(self.tmp, "review-summary", self.wid))
        advisor_disposition(identity, "review-summary", read_workflow(identity)["workflowId"], "preflight", "none")
        setup_items = build_no_change_document("suite setup")["behaviorMap"]
        setup_items[0]["basis"] = "review fixture"
        commit_evidence_phase(identity, "review-summary", self.wid, "preflight", {
            "schemaVersion": 1, "slug": "review-summary", "workflowId": self.wid,
            "document": {"behaviorMap": setup_items},
        })
        set_phase(identity, "tdd", "not-required")
        verified = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", "review-summary",
             "--", sys.executable, "-c", "pass"],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert verified.returncode == 0, verified.stdout + verified.stderr
        quality = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", "review-summary",
             "--kind", "quality-gate", "--base-ref", "HEAD"],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert quality.returncode == 0, quality.stdout + quality.stderr

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CODEX_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def evidence(self, evidence_id: str) -> dict[str, object]:
        result = self.run_script(WORKFLOW, "evidence", "--full", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["document"]

    def event_count(self) -> int:
        result = self.run_script(WORKFLOW, "history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return len(json.loads(result.stdout)["events"])

    def disposition_context(self) -> dict[str, str]:
        candidate = _active_candidate_tree(resolve_repo_identity(self.repo))
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo,
                                       env=self.env, text=True).strip()
        return {"workflowId": self.wid, "candidateTree": candidate, "prHead": head}

    def review_finding(self) -> dict[str, object]:
        return {"id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "wrong value",
            "evidence": "app.value is 1", "consequence": "the result remains wrong",
            "smallest_action": "correct the value"}

    def disposition_document(self, intake: str, identifier: str, status: str, *,
            kind: str = "nonbehavioral", count: int = 0, complete: bool = True,
            **extra: object) -> dict[str, object]:
        field = "reference" if status == "accepted-follow-up" else "evidence"
        return {"context": self.disposition_context(), "intakeEvidenceId": intake, "dispositions": [{
            "finding_id": identifier, "status": status, "kind": kind,
            "premise": {"claim": "the finding premise holds", "command": "inspect app.py", "result": "value = 1"},
            "occurrence": {"domain": "the complete fixture repository", "count": count, "complete": complete,
                           "command": "inspect app.py", "result": f"count={count}"},
            "materialConsequence": {"claim": "the result is affected", "command": "inspect app.py",
                                    "result": "the fixture remains incorrect"},
            field: "issue-1" if field == "reference" else "verified current-tree evidence", **extra}]}

    def record_review(self, path: Path, context: str = "review") -> subprocess.CompletedProcess[str]:
        return self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--review-context-id", context, "--input", str(path),
        )

class ReviewSummaryTests(ReviewSummaryHarness):
    def test_review_intake_keeps_only_consumed_finding_fields(self) -> None:
        path = self.tmp / "minimal-review.json"
        finding = {"id": "SPEC-1", "claim": "wrong value", "material": True,
                   "kind": "nonbehavioral"}
        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        recorded = self.run_script(WORKFLOW, "record", "review", "--input", str(path))
        self.assertEqual(recorded.returncode, 0, "REVIEW_PROSE_STILL_REQUIRED " + recorded.stderr)
        document = self.evidence(json.loads(recorded.stdout)["summaryId"])
        self.assertEqual(document["findings"], [finding])

    def test_ordinary_review_can_omit_repair_context_identity(self) -> None:
        marker = "REVIEW_OPTIONAL_REFUSED"
        path = self.tmp / "ordinary-review.json"
        path.write_text(json.dumps({"findings": []}), encoding="utf-8")
        recorded = self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, marker + " " + recorded.stderr)
        document = self.evidence(json.loads(recorded.stdout)["summaryId"])
        self.assertNotIn("reviewContextId", document)

    def test_pending_findings_allow_fresh_final_assessment_without_completion(self) -> None:
        marker = "PENDING_FINDINGS_PREVENT_FINAL_ASSESSMENT"
        design = self.tmp / "design.json"
        design.write_text(json.dumps({"schemaVersion": 1, "status": "absent",
                                     "reason": "Existing CLI assessment admission probe"}))
        declared = self.run_script(WORKFLOW, "record", "advisor-result", "--slug", "review-summary",
            "--workflow-id", self.wid, "--stage", "preflight", "--source", "codex-advisor",
            "--input", empty_advisor_envelope(self.tmp, "completed"), "--design-declaration", str(design))
        self.assertEqual(declared.returncode, 0, declared.stderr)
        self.assertFalse(json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)["ready"], marker)
        path = self.tmp / "assessment.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}))
        intake = self.record_review(path, "independent-assessment")
        self.assertEqual(intake.returncode, 0, intake.stderr)
        checkpoint = self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review")
        self.assertTrue(json.loads(checkpoint.stdout)["ready"], marker + checkpoint.stdout)
        self.assertEqual(self.run_script(WORKFLOW, "complete").returncode, 2, marker)
        path.write_text(json.dumps({"schemaVersion": 1, "verdict": "fix-before-commit", "findings": [
            {"id": "SPEC-2", "claim": "Independent finding remains unresolved",
             "kind": "nonbehavioral", "material": True}]}))
        final_args = ("record", "advisor-result", "--slug", "review-summary", "--workflow-id", self.wid,
                      "--stage", "final", "--source", "codex-advisor", "--input", str(path),
                      "--design-declaration", str(design))
        update = self.tmp / "reassessment.json"
        update.write_text(json.dumps({"reassessment": "Check the newly affected read before final assessment",
            "items": [{"id": "BM_CURRENT", "kind": "contract", "basis": "newly requested read",
                       "behavior": "Current application value remains readable", "seam": "Python import",
                       "expected": "value is 1", "redFailure": "CURRENT_READ_CHANGED"}]}))
        mapped = self.run_script(WORKFLOW, "record", "map", "--slug", "review-summary",
                                 "--workflow-id", self.wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, mapped.stderr)
        self.assertEqual(self.run_script(WORKFLOW, *final_args).returncode, 2, "REASSESSED_MAP_ADMITTED_FINAL_RESULT")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
            "--slug", "review-summary", "--phase", "red", "--behavior-id", "BM_CURRENT", "--",
            sys.executable, "-c", "import app; assert app.value == 1; print('current application value is 1')"],
            cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        (self.repo / "app.py").write_text("value = 2\n")
        events = self.event_count()
        self.assertFalse(json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)["ready"], marker)
        self.assertEqual(self.run_script(WORKFLOW, *final_args).returncode, 2, marker)
        self.assertEqual(self.event_count(), events, marker)
        (self.repo / "app.py").write_text("value = 1\n")
        accepted = self.run_script(WORKFLOW, *final_args)
        self.assertEqual(accepted.returncode, 0, marker + accepted.stderr)
        self.assertTrue(all(f["status"] == "pending" for f in json.loads(self.run_script(WORKFLOW, "status").stdout)["findingStates"]), marker)
        self.assertEqual(self.run_script(WORKFLOW, "complete").returncode, 2, marker)
        events = self.event_count()
        self.assertEqual(self.run_script(WORKFLOW, *final_args).returncode, 2, marker)
        self.assertEqual(self.event_count(), events, marker)

    def test_behavioral_promotion_cannot_close_through_old_intake(self) -> None:
        marker = "BEHAVIORAL_PROMOTION_CLOSED_WITH_OLD_NONBEHAVIORAL_PROOF"
        path = self.tmp / "promotion.json"
        references = []
        for kind in ("nonbehavioral", "behavioral"):
            path.write_text(json.dumps({"findings": [{**self.review_finding(), "kind": kind}]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            references.append(json.loads(result.stdout)["summaryId"])
        before = self.event_count()
        path.write_text(json.dumps(self.disposition_document(references[0], "SPEC-1", "fixed")))
        result = self.record_review(path)
        self.assertEqual(result.returncode, 2, marker)
        self.assertEqual(self.event_count(), before, marker)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual((state["findingStates"][0]["kind"], state["codeReview"]["findings"]),
                         ("behavioral", "pending"), marker)

    def test_pending_retry_preserves_material_escalation(self) -> None:
        path = self.tmp / "review.json"
        for material in (False, True):
            path.write_text(json.dumps({"findings": [{**self.review_finding(), "material": material}]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual((len(state["findingStates"]), state["findingStates"][0]["material"],
                          state["codeReview"]["findings"]), (1, True, "pending"),
                         "MATERIAL_ESCALATION_LOST")

    def test_review_retries_reconcile_identity_and_disposition_the_observation(self) -> None:
        finding = self.review_finding()
        path = self.tmp / "review.json"
        references = []
        for claim in ("wrong value", "same defect with a new counterexample", "same defect with a new counterexample"):
            path.write_text(json.dumps({"findings": [{**finding, "claim": claim}]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            references.append(json.loads(result.stdout)["summaryId"])
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(len(state["findingStates"]), 1, "FINDING_IDENTITY_SPLIT")
        path.write_text(json.dumps(self.disposition_document(references[-1], "SPEC-1", "fixed")))
        result = self.record_review(path)
        self.assertEqual(result.returncode, 0, "OBSERVATION_REFERENCE_UNUSABLE: " + result.stderr)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(state["findingStates"][0]["status"], "fixed")
        self.assertNotEqual(references[0], references[1])
        for _ in range(2):
            path.write_text(json.dumps({"findings": [finding]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, "NONFIX_SEMANTICS_CHANGED" + result.stderr)
            reference = json.loads(result.stdout)["summaryId"]
            path.write_text(json.dumps(self.disposition_document(reference, "SPEC-1", "fixed")))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, "NONFIX_SEMANTICS_CHANGED" + result.stderr)

    def test_material_findings_require_intake_then_appended_disposition(self) -> None:
        finding = {
            "id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "wrong value",
            "evidence": "real review evidence", "consequence": "the result remains incorrect",
            "smallest_action": "correct the value",
        }
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": []}), encoding="utf-8")
        missing_identity = self.record_review(path, "")
        self.assertEqual(missing_identity.returncode, 2, missing_identity.stdout + missing_identity.stderr)
        self.assertIn("review context id", missing_identity.stderr)

        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "fresh-review-1")
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]

        before_events = self.event_count()
        for invalid in (
            {"findings": [finding], "intakeEvidenceId": intake_id, "dispositions": []},
            {"intakeEvidenceId": intake_id, "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "evidence": "verified correction",
                "claim": "restated claim",
            }]},
        ):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            refused = self.record_review(path, "fresh-review-1")
            self.assertEqual(refused.returncode, 2, "a disposition restated immutable intake")
            self.assertEqual(self.event_count(), before_events, "a refused disposition appended an event")

        def disposition(intake: str, identifier: str) -> subprocess.CompletedProcess[str]:
            path.write_text(json.dumps(self.disposition_document(intake, identifier, "fixed")), encoding="utf-8")
            return self.record_review(path, "disposition")

        recorded = disposition(intake_id, "SPEC-1")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertNotIn("findings", self.evidence(json.loads(recorded.stdout)["summaryId"]),
                         "a disposition rewrote immutable finding intake")
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed")
        second = {**finding, "id": "SPEC-2", "claim": "second wrong value"}
        path.write_text(json.dumps({"findings": [second]}), encoding="utf-8")
        second_id = json.loads(self.record_review(path, "fresh-review-2").stdout)["summaryId"]
        final = disposition(second_id, "SPEC-2")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        self.assertEqual(json.loads(final.stdout)["status"], "passed")

    def test_later_disposition_closes_only_changed_findings_and_links_history(self) -> None:
        marker = "FINDING_CLOSURE_OVERWROTE_HISTORY"
        path = self.tmp / "partial-finding-closure.json"
        first = self.review_finding()
        second = {**first, "id": "SPEC-2", "material": False, "claim": "minor follow-up"}
        path.write_text(json.dumps({"findings": [first, second]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "partial-intake").stdout)["summaryId"]

        initial = self.disposition_document(intake_id, "SPEC-1", "accepted-follow-up", count=1)
        follow_up = self.disposition_document(intake_id, "SPEC-2", "report-only", count=1)["dispositions"][0]
        follow_up["materialConsequence"]["result"] = "false"
        initial["dispositions"].append(follow_up)
        path.write_text(json.dumps(initial), encoding="utf-8")
        classified = self.record_review(path, "partial-initial")
        self.assertEqual(classified.returncode, 0, marker + classified.stdout + classified.stderr)
        first_disposition_id = json.loads(classified.stdout)["summaryId"]
        self.assertEqual(json.loads(classified.stdout)["status"], "pending", marker)

        update = self.tmp / "reopened-map.json"
        update.write_text(json.dumps({"reassessment": "A separate application guarantee needs proof", "items": [{
            "id": "BM_VALUE", "kind": "contract", "basis": "application contract",
            "behavior": "app.value is two", "seam": "import app", "expected": "value equals two",
            "redFailure": "VALUE_NOT_TWO", "status": "pending",
        }]}), encoding="utf-8")
        mapped = self.run_script(WORKFLOW, "record", "map", "--slug", "review-summary", "--workflow-id", self.wid,
                                 "--input", str(update))
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo))["tdd"], "in-progress")

        closure = self.disposition_document(intake_id, "SPEC-1", "report-only")
        closure["dispositions"][0]["materialConsequence"]["result"] = "false"
        path.write_text(json.dumps(closure), encoding="utf-8")
        closed = self.record_review(path, "partial-closure")
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        closed_payload = json.loads(closed.stdout)
        self.assertEqual(closed_payload["status"], "pending", marker)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo))["nextAction"], "tdd", marker)
        second_disposition_id = closed_payload["summaryId"]

        states = {
            entry["findingId"]: entry
            for entry in read_workflow(resolve_repo_identity(self.repo))["findingStates"]
        }
        self.assertEqual(states["SPEC-2"]["status"], "report-only", marker)
        self.assertEqual(states["SPEC-1"]["status"], "report-only", marker)
        self.assertEqual(states["SPEC-1"]["dispositionEvidenceId"], second_disposition_id, marker)
        self.assertEqual(states["SPEC-1"]["dispositionHistory"], [{
            "evidenceId": first_disposition_id,
            "status": "accepted-follow-up",
            "supersededBy": second_disposition_id,
        }], marker)
        self.assertEqual(
            self.evidence(second_disposition_id)["supersedesEvidenceIds"],
            [first_disposition_id],
            marker,
        )
        proof = self.repo / "test_value.py"
        proof.write_text("import unittest\nimport app\nclass Value(unittest.TestCase):\n"
                         "    def test_value(self):\n        self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n",
                         encoding="utf-8")
        for phase in ("red", "green"):
            if phase == "green":
                (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", "review-summary",
                 "--phase", phase, "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "-v", "test_value"],
                cwd=self.repo, env=self.env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, marker + result.stdout + result.stderr)
        verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(verified.returncode, 0, marker + verified.stdout + verified.stderr)
        path.write_text(json.dumps({"findings": []}), encoding="utf-8")
        ready = self.record_review(path, "ready-return")
        self.assertEqual(ready.returncode, 0, marker + ready.stdout + ready.stderr)
        self.assertEqual(json.loads(ready.stdout)["status"], "passed", marker)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo))["findingStates"], list(states.values()), marker)

    def test_pending_review_refreshes_binding_without_closing_findings(self) -> None:
        path = self.tmp / "pending-review.json"
        previous = None
        for findings in ([self.review_finding()], []):
            if previous is not None:
                (self.repo / "app.py").write_text("value = 2\n")
                verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
                self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            path.write_text(json.dumps({"findings": findings}))
            recorded = self.record_review(path, "current-pending-review")
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            state = json.loads(self.run_script(WORKFLOW, "status").stdout)
            self.assertEqual(state["codeReview"]["status"], "pending")
            self.assertEqual(state["findingStates"][0]["status"], "pending")
            self.assertIsNotNone(state.get("reviewManifestId"), "PENDING_REVIEW_BINDING_STALE")
            self.assertNotEqual(state["reviewManifestId"], previous, "PENDING_REVIEW_BINDING_STALE")
            checkpoint = json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)
            self.assertFalse(any("review-manifest" in reason for reason in checkpoint["missing"]),
                             "PENDING_REVIEW_BINDING_STALE")
            previous = state["reviewManifestId"]

    def test_empty_review_document_is_a_no_finding_intake(self) -> None:
        path = self.tmp / "empty-review.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded = self.record_review(path, "empty-review")
        self.assertEqual(recorded.returncode, 0, "EMPTY_REVIEW_REJECTED" + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", "EMPTY_REVIEW_REJECTED")

    def test_disposition_requires_current_measurements(self) -> None:
        marker = "UNMEASURED_REVIEW_FINDING_DISPOSITION_ACCEPTED"
        path = self.tmp / "measured-disposition.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = self.record_review(path, "measurement-intake")
        self.assertEqual(intake.returncode, 0, marker + intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        before_events = self.event_count()
        path.write_text(json.dumps({
            "context": self.disposition_context(), "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "nonbehavioral",
                "evidence": "claimed correction",
            }],
        }), encoding="utf-8")
        refused = self.record_review(path, "measurement-disposition")
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertIn("premise", refused.stderr, marker)
        self.assertEqual(self.event_count(), before_events, marker)

    def test_false_premise_can_be_rejected_without_zero_occurrence(self) -> None:
        marker = "PREMISE_FALSE_REJECTION_REFUSED"
        path = self.tmp / "false-premise-rejection.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "false-premise-intake").stdout)["summaryId"]
        document = self.disposition_document(intake_id, "SPEC-1", "rejected-with-evidence",
                                             count=1, complete=False)
        document["dispositions"][0]["premise"]["result"] = "false"
        path.write_text(json.dumps(document), encoding="utf-8")
        recorded = self.record_review(path, "false-premise-rejection")
        self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", marker)

    def test_fixed_requires_false_premise_or_complete_zero_occurrence(self) -> None:
        marker = "POSITIVE_CURRENT_OCCURRENCE_FIXED"
        path = self.tmp / "fixed-occurrence.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "fixed-occurrence-intake").stdout)["summaryId"]
        positive = self.disposition_document(intake_id, "SPEC-1", "fixed", count=1)
        path.write_text(json.dumps(positive), encoding="utf-8")
        refused = self.record_review(path, "positive-occurrence-fixed")
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertIn("false premise or zero occurrence", refused.stderr, marker)
        positive["dispositions"][0]["premise"]["result"] = "false"
        path.write_text(json.dumps(positive), encoding="utf-8")
        accepted = self.record_review(path, "false-premise-fixed")
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["status"], "passed", marker)

    def test_shape_table_is_generated_and_referenced_by_author_skills(self) -> None:
        marker = "DOCUMENT_SHAPE_TABLE_DRIFTED"
        from hooks.lib import workflow_documents
        shapes = workflow_documents.DOCUMENT_SHAPES
        table = workflow_documents.DOCUMENT_SHAPE_TABLE
        self.assertEqual(list(shapes), ["preflight", "review", "advisor-result", "advisor-disposition", "map",
                                        "fixed", "rejected-with-evidence", "report-only", "accepted-follow-up", "governed-design"], marker)
        for name, shape in shapes.items():
            self.assertIn(f"| `{name}` | {shape} |", table, marker)
            self.assertEqual(f"| `{name}` | {shape} |".count("|"), 3, "DOCUMENT_SHAPE_TABLE_HAS_EXTRA_COLUMN")
        rendered = self.run_script(WORKFLOW, "record", "--help")
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertIn(table, rendered.stdout, "AUTHOR_TABLE_COMMAND_USED_CALLER_PATH")

    def test_record_review_refusal_names_shape_and_preserves_state(self) -> None:
        marker = "REVIEW_SHAPE_GUIDANCE_MISSING"
        finding = {**self.review_finding(), "kind": "behavioral"}
        path = self.tmp / "review-shape.json"
        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "review-shape-intake")
        intake_id = json.loads(intake.stdout)["summaryId"]
        corrected = self.disposition_document(
            intake_id, "SPEC-1", "rejected-with-evidence", kind="behavioral",
        )
        wrong = json.loads(json.dumps(corrected))
        wrong["dispositions"][0]["occurrence"] = {}
        path.write_text(json.dumps(wrong), encoding="utf-8")
        before = self.run_script(WORKFLOW, "status").stdout, self.event_count()
        refused = self.record_review(path, "review-shape-refusal")
        self.assertEqual((refused.returncode, (self.run_script(WORKFLOW, "status").stdout, self.event_count())),
                         (2, before), marker + refused.stdout + refused.stderr)
        self.assertIn("rejected-with-evidence expected shape", refused.stderr, marker)
        self.assertIn('"finding_id"', refused.stderr, marker)
        self.assertIn('"kind"', refused.stderr, marker)
        path.write_text(json.dumps(corrected), encoding="utf-8")
        accepted = self.record_review(path, "review-shape-corrected")
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)

    def test_reviewer_dispositions_bind_context_and_make_report_only_terminal(self) -> None:
        path = self.tmp / "reviewer-disposition-gates.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "gate-intake").stdout)["summaryId"]
        incomplete = self.disposition_document(
            intake_id, "SPEC-1", "rejected-with-evidence", count=0, complete=False,
        )
        path.write_text(json.dumps(incomplete), encoding="utf-8")
        refused = self.record_review(path, "incomplete-domain")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("complete domain", refused.stderr)
        stale = self.disposition_document(intake_id, "SPEC-1", "report-only")
        stale["dispositions"][0]["materialConsequence"]["result"] = "false"
        stale["context"]["candidateTree"] = "0" * 40
        path.write_text(json.dumps(stale), encoding="utf-8")
        refused = self.record_review(path, "stale-candidate")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("candidateTree", refused.stderr)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "same tree"],
                       cwd=self.repo, env=self.env, check=True)
        retired = self.disposition_document(intake_id, "SPEC-1", "accepted-for-proof")
        path.write_text(json.dumps(retired), encoding="utf-8")
        refused = self.record_review(path, "retired-reservation")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("invalid or duplicate disposition", refused.stderr)
        report_only = self.disposition_document(intake_id, "SPEC-1", "report-only")
        path.write_text(json.dumps(report_only), encoding="utf-8")
        material = self.record_review(path, "material-report-only")
        self.assertEqual(material.returncode, 2, "MATERIAL_FINDING_REPORTED_ONLY" + material.stdout + material.stderr)
        report_only["dispositions"][0]["materialConsequence"]["result"] = "false"
        path.write_text(json.dumps(report_only), encoding="utf-8")
        resolved = self.record_review(path, "report-only")
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(json.loads(resolved.stdout)["status"], "passed")
        path.write_text(json.dumps(self.disposition_document(intake_id, "SPEC-1", "fixed")), encoding="utf-8")
        relabel = self.record_review(path, "relabel-fixed")
        self.assertEqual(relabel.returncode, 2, relabel.stdout + relabel.stderr)
        self.assertIn("terminal disposition report-only", relabel.stderr)

    def test_disposition_binds_the_exact_validated_manifest_snapshot(self) -> None:
        bulk = self.repo / "bulk"; bulk.mkdir()
        for index in range(2500): (bulk / f"f{index:04d}.py").write_text("x" * 4096, encoding="utf-8")
        path = self.tmp / "race.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = json.loads(self.record_review(path, "race-intake").stdout)["summaryId"]
        document = self.disposition_document(intake, "SPEC-1", "fixed")
        path.write_text(json.dumps(document), encoding="utf-8")
        before_events = self.event_count()
        process = subprocess.Popen([sys.executable, str(WORKFLOW), "record", "review", "--slug", "review-summary",
            "--workflow-id", self.wid, "--review-context-id", "race",
            "--input", str(path), "--repo", str(self.repo)], cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        saw_hash = mutated = False
        deadline = time.monotonic() + 30
        while process.poll() is None and time.monotonic() < deadline:
            try:
                children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                commands = [Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ") for pid in children]
            except OSError:
                continue
            if any(b"hash-object --no-filters" in command for command in commands):
                saw_hash = True
            elif saw_hash and not mutated:
                (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
                mutated = True
        stdout, stderr = process.communicate(timeout=30)
        self.assertTrue(saw_hash and mutated, "did not mutate after raw-manifest validation")
        self.assertEqual(process.returncode, 2, stdout + stderr)
        self.assertIn("candidateTree", stderr)
        self.assertEqual(self.event_count(), before_events, "a stale candidate appended review evidence")

    def test_unhashable_membership_fields_are_refused_not_crashed(self) -> None:
        finding = {
            "id": "F1", "axis": "Standards", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "c", "evidence": "e",
            "consequence": "k", "smallest_action": "s",
        }
        path = self.tmp / "unhashable.json"
        before_events = self.event_count()
        path.write_text(json.dumps({"findings": [{**finding, "kind": []}]}), encoding="utf-8")
        refused = self.record_review(path, "unhashable-kind")
        self.assertEqual(refused.returncode, 2, "an unhashable kind crashed instead of refusing")
        self.assertIn("has an invalid kind", refused.stderr)
        self.assertEqual(self.event_count(), before_events, "a refused intake appended an event")
        self.assertNotIn("codeReviewEvidence", json.loads(self.run_script(WORKFLOW, "status").stdout))

        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "valid-intake")
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        before_events = self.event_count()

        for field, value, diagnostic in (
            ("finding_id", [], "must reference a finding"),
            ("status", {}, "invalid or duplicate disposition"),
        ):
            document = self.disposition_document(intake_id, "F1", "fixed")
            document["dispositions"][0][field] = value
            path.write_text(json.dumps(document), encoding="utf-8")
            refused = self.record_review(path, "invalid-disposition")
            self.assertEqual(refused.returncode, 2, "an unhashable disposition member crashed")
            self.assertIn(diagnostic, refused.stderr)
            self.assertEqual(self.event_count(), before_events, "a refused disposition appended an event")
            state = json.loads(self.run_script(WORKFLOW, "status").stdout)
            self.assertEqual(state["codeReviewEvidence"], intake_id)
            self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

    def test_rejected_recorder_call_appends_no_event(self) -> None:
        rebegun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        new_wid = read_workflow(resolve_repo_identity(self.repo))["workflowId"]
        before_events = self.event_count()

        payload = self.tmp / "premature.json"
        payload.write_text(json.dumps({"findings": []}), encoding="utf-8")
        premature = self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", new_wid,
            "--review-context-id", "fresh-review-2",
            "--input", str(payload),
        )
        self.assertEqual(premature.returncode, 2, "a premature recorder call was accepted before verification")
        self.assertEqual(self.event_count(), before_events, "a rejected recorder call appended an event")


class ReportOnlyOwnershipReviewTests(ReviewSummaryHarness):
    """Issue #191: the review caller applies the same report-only ownership rule."""

    def test_report_only_refuses_a_behavioral_finding_without_an_owner_on_the_review_caller(self) -> None:
        marker = "REVIEW_REPORT_ONLY_UNOWNED_ACCEPTED"
        path = self.tmp / "unowned-report-only.json"
        finding = {**self.review_finding(), "kind": "behavioral"}
        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "unowned-intake").stdout)["summaryId"]
        closure = self.disposition_document(intake_id, "SPEC-1", "report-only", kind="behavioral")
        closure["dispositions"][0]["materialConsequence"]["result"] = "false"
        path.write_text(json.dumps(closure), encoding="utf-8")
        before = read_workflow(resolve_repo_identity(self.repo))
        refused = self.record_review(path, "unowned-closure")
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("owning", refused.stderr, marker)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo)), before, marker)


class BulkRejectionReviewTests(ReviewSummaryHarness):
    """Issue #186 part 3: bulk material rejections through the review caller."""

    def rejection_review(self, count: int, *, valid: bool = True, material: int | None = None,
                         rejected: int | None = None) -> subprocess.CompletedProcess[str]:
        material = count if material is None else material
        rejected = count if rejected is None else rejected
        findings = [dict(self.review_finding(), id=f"SPEC-{i}", material=(i <= material))
                    for i in range(1, count + 1)]
        intake_path = self.tmp / "bulk-intake.json"
        intake_path.write_text(json.dumps({"findings": findings}), encoding="utf-8")
        recorded = self.record_review(intake_path)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        summary_id = json.loads(recorded.stdout)["summaryId"]
        premise_result = "false" if valid else "the premise held on inspection"
        document = {"context": self.disposition_context(), "intakeEvidenceId": summary_id,
                    "dispositions": [{
                        "finding_id": f"SPEC-{i}",
                        "status": "rejected-with-evidence" if i <= rejected else "report-only",
                        "kind": "nonbehavioral",
                        "premise": {"claim": f"claimed defect {i}", "command": "inspect app.py",
                                    "result": premise_result},
                        "occurrence": {"domain": "the complete fixture repository",
                                       "count": 0 if valid else 2, "complete": valid,
                                       "command": "inspect app.py", "result": "measured"},
                        "materialConsequence": {"claim": "the fixture is affected",
                                                "command": "inspect app.py",
                                                "result": "measured" if i <= rejected else "false"},
                        "evidence": "measured rejection evidence",
                    } for i in range(1, count + 1)]}
        doc_path = self.tmp / "bulk-dispositions.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        return self.record_review(doc_path)

    def review_states(self) -> list[str]:
        result = self.run_script(WORKFLOW, "status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [entry["status"] for entry in json.loads(result.stdout).get("findingStates", [])
                if entry.get("stage") == "code-review"]

    def test_three_material_rejections_warn_on_the_review_caller(self) -> None:
        marker = "BULK_REJECTION_UNFLAGGED_REVIEW"
        result = self.rejection_review(3)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)
        self.assertIn("3", result.stderr, marker)
        self.assertEqual(self.review_states(), ["rejected-with-evidence"] * 3,
                         marker + ": statuses did not persist")

    def test_two_rejections_stay_silent_on_the_review_caller(self) -> None:
        marker = "SMALL_DOC_FALSELY_FLAGGED"
        result = self.rejection_review(2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_rejections_with_two_material_stay_silent_on_the_review_caller(self) -> None:
        # The warning counts MATERIAL rejections, not total rejections.
        marker = "IMMATERIAL_REJECTIONS_MISCOUNTED"
        result = self.rejection_review(3, material=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_material_with_two_rejected_stay_silent_on_the_review_caller(self) -> None:
        # The warning counts REJECTIONS, not every material disposition.
        marker = "NONREJECTION_DISPOSITIONS_MISCOUNTED"
        result = self.rejection_review(3, rejected=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_an_unmeasured_rejection_still_refuses_on_the_review_caller(self) -> None:
        marker = "REJECTION_SHAPE_ENFORCEMENT_LOST"
        result = self.rejection_review(1, valid=False)
        self.assertNotEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("false premise or zero occurrence", result.stdout + result.stderr, marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
