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

from hooks.tests.support import approve_preflight
from hooks.tests.support import build_no_change_document, record_context_forge  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.lib.workflow_state import advisor_disposition, read_workflow, record_advisor_result, set_phase  # noqa: E402

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
        begun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary", "--intent", "Make the application value two.")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        identity = record_context_forge(self.repo, self.tmp)
        self.wid = read_workflow(identity)["workflowId"]
        record_advisor_result(identity, "review-summary", read_workflow(identity)["workflowId"], "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, "review-summary", read_workflow(identity)["workflowId"], "preflight", "none")
        doc_path = self.tmp / "setup-preflight.json"
        doc_path.write_text(json.dumps(build_no_change_document("suite setup")), encoding="utf-8")
        approve_preflight(self.repo, json.loads(doc_path.read_text()))
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record", "preflight", "--repo", str(self.repo), "--slug", "review-summary",
             "--workflow-id", read_workflow(identity)["workflowId"], "--input", str(doc_path)],
            cwd=str(Path(__file__).resolve().parents[2]), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
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
        return {"id": "SPEC-1", "material": True, "kind": "nonbehavioral", "claim": "wrong value"}

    def dispose(self, identifier: str, status: str = "report-only") -> subprocess.CompletedProcess[str]:
        option = {"report-only": "--report-only", "rejected-with-evidence": "--rejected",
                  "fixed": "--fixed", "accepted-follow-up": "--follow-up"}[status]
        return self.run_script(WORKFLOW, "record", "advisor-disposition", "--finding", identifier,
                               option, *(["issue-1"] if status == "accepted-follow-up" else []),
                               "--reason", "The current fixture was inspected; this observation has no material consequence.")

    def record_review(self, path: Path, context: str = "review") -> subprocess.CompletedProcess[str]:
        return self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--review-context-id", context, "--input", str(path),
        )

class ReviewSummaryTests(ReviewSummaryHarness):
    def test_pending_findings_allow_fresh_final_assessment_without_completion(self) -> None:
        marker = "PENDING_FINDINGS_PREVENT_FINAL_ASSESSMENT"
        design = self.tmp / "design.json"
        design.write_text(json.dumps({"schemaVersion": 1, "status": "absent",
                                     "reason": "Existing CLI assessment admission probe"}))
        declared = self.run_script(WORKFLOW, "record", "advisor-result", "--slug", "review-summary",
            "--workflow-id", self.wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--design-declaration", str(design))
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
        update.write_text(json.dumps({"items": [{"id": "BM_CURRENT", "kind": "preservation", "basis": "newly requested read",
                       "behavior": "Current application value remains readable", "seam": "Python import",
                       "expected": "value is 1"}]}))
        mapped = self.run_script(WORKFLOW, "record", "tdd-map", "--slug", "review-summary",
                                 "--workflow-id", self.wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, mapped.stderr)
        self.assertEqual(self.run_script(WORKFLOW, *final_args).returncode, 2, "REASSESSED_MAP_ADMITTED_FINAL_RESULT")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
            "--slug", "review-summary", "--behavior-id", "BM_CURRENT", "--",
            sys.executable, "-c", "import app; assert app.value == 1; print('current application value:', app.value)"],
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
        result = self.dispose("SPEC-1", "fixed")
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
        finding.pop("kind")
        path = self.tmp / "review.json"
        references = []
        for claim in ("wrong value", "same defect with a new counterexample", "same defect with a new counterexample"):
            path.write_text(json.dumps({"findings": [{**finding, "claim": claim}]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            references.append(json.loads(result.stdout)["summaryId"])
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(len(state["findingStates"]), 1, "FINDING_IDENTITY_SPLIT")
        result = self.dispose("SPEC-1")
        self.assertEqual(result.returncode, 0, "OBSERVATION_REFERENCE_UNUSABLE: " + result.stderr)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(state["findingStates"][0]["status"], "report-only")
        self.assertNotEqual(references[0], references[1])
        for _ in range(2):
            path.write_text(json.dumps({"findings": [finding]}))
            result = self.record_review(path)
            self.assertEqual(result.returncode, 0, "NONFIX_SEMANTICS_CHANGED" + result.stderr)
            result = self.dispose("SPEC-1")
            self.assertEqual(result.returncode, 0, "NONFIX_SEMANTICS_CHANGED" + result.stderr)

    def test_material_findings_require_intake_then_appended_disposition(self) -> None:
        finding = {"id": "SPEC-1", "material": True, "kind": "test-coverage", "claim": "wrong value",
                   "location": "app.py:1"}
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": [{key: value for key, value in finding.items() if key != "claim"}]}), encoding="utf-8")
        missing_claim = self.record_review(path, "fresh-review-1")
        self.assertEqual(missing_claim.returncode, 2, missing_claim.stdout + missing_claim.stderr)
        self.assertIn("claim", missing_claim.stderr)

        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "fresh-review-1")
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        self.assertEqual(self.evidence(intake_id)["findings"][0]["location"], "app.py:1")

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

        recorded = self.dispose("SPEC-1")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state["codeReview"]["status"], "passed")
        self.assertEqual(self.evidence(intake_id)["findings"], [finding])
        second = {**finding, "id": "SPEC-2", "claim": "second observation"}
        path.write_text(json.dumps({"findings": [second]}), encoding="utf-8")
        self.assertEqual(self.record_review(path, "fresh-review-2").returncode, 0)
        final = self.dispose("SPEC-2")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo))["codeReview"]["status"], "passed")

    def test_later_disposition_closes_only_changed_findings_and_links_history(self) -> None:
        marker = "FINDING_CLOSURE_OVERWROTE_HISTORY"
        path = self.tmp / "partial-finding-closure.json"
        first = self.review_finding()
        second = {**first, "id": "SPEC-2", "material": False, "claim": "minor follow-up"}
        path.write_text(json.dumps({"findings": [first, second]}), encoding="utf-8")
        intake = self.record_review(path, "partial-intake")
        self.assertEqual(intake.returncode, 0, intake.stderr)

        classified = self.dispose("SPEC-1", "accepted-follow-up")
        self.assertEqual(classified.returncode, 0, marker + classified.stderr)
        first_disposition_id = read_workflow(resolve_repo_identity(self.repo))["findingStates"][0]["dispositionEvidenceId"]
        self.assertEqual(self.dispose("SPEC-2").returncode, 0)
        update = self.tmp / "reopened-map.json"
        update.write_text(json.dumps({"items": [{
            "id": "BM_VALUE", "kind": "contract", "basis": "Make the application value two.",
            "behavior": "app.value is two", "seam": "import app", "expected": "value equals two",
        }]}), encoding="utf-8")
        mapped = self.run_script(WORKFLOW, "record", "tdd-map", "--slug", "review-summary", "--workflow-id", self.wid,
                                 "--input", str(update))
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual(read_workflow(resolve_repo_identity(self.repo))["tdd"], "in-progress")

        closed = self.dispose("SPEC-1")
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state["nextAction"], "tdd", marker)
        second_disposition_id = state["findingStates"][0]["dispositionEvidenceId"]

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
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", "review-summary",
             "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "-v", "test_value"],
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

    def test_a_clean_rereview_settles_its_findings_and_refreshes_the_binding(self) -> None:
        marker, path = "PENDING_REVIEW_BINDING_STALE", self.tmp / "pending-review.json"
        status = lambda: json.loads(self.run_script(WORKFLOW, "status").stdout)
        findings = [self.review_finding(), {**self.review_finding(), "id": "SPEC-2", "kind": "behavioral", "claim": "value must stay two"}]
        path.write_text(json.dumps({"findings": findings}))
        self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        first = status()
        self.assertEqual((first["findingStates"][0]["status"], first["nextAction"]), ("pending", "code-review"), marker)
        (self.repo / "app.py").write_text("value = 2\n")
        verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        path.write_text(json.dumps({"findings": [{"id": "R-2", "material": False, "kind": "nonbehavioral", "claim": "a note"}]}))
        self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        state = status()
        self.assertEqual(({e["findingId"]: e["status"] for e in state["findingStates"]}["SPEC-1"], state["codeReview"]["status"]),
                         ("resolved", "passed"), marker + ": the clean re-review left its finding open")
        self.assertNotIn(state.get("reviewManifestId"), (None, first["reviewManifestId"]), marker)
        path.write_text(json.dumps({"findings": findings}))
        self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        self.assertEqual({e["findingId"]: e.get("recurrence") for e in status()["findingStates"][-2:]},
                         {"SPEC-1": None, "SPEC-2": 1}, "SETTLED_FINDING_RECURRENCE_LOST")
        clean, again = json.dumps({"findings": []}), json.dumps({"findings": findings[1:]})
        for document in (clean, again, clean):  # recurrence two: an uncertified clean review settles nothing
            path.write_text(document)
            self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        self.assertEqual((status()["findingStates"][-1].get("recurrence"), status()["findingStates"][-1]["status"]),
                         (2, "pending"), "UNCERTIFIED_REPAIR_SETTLED")

    def test_legacy_empty_document_is_a_no_finding_intake(self) -> None:
        path = self.tmp / "legacy-empty.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded = self.record_review(path, "legacy-empty")
        self.assertEqual(recorded.returncode, 0, "LEGACY_EMPTY_REVIEW_REJECTED" + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", "LEGACY_EMPTY_REVIEW_REJECTED")


    def test_reviewer_disposition_derives_context_and_is_terminal(self) -> None:
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}))
        intake = json.loads(self.record_review(path).stdout)["summaryId"]
        result = self.dispose("SPEC-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        document = self.evidence(state["findingStates"][0]["dispositionEvidenceId"])
        self.assertEqual(document["intakeEvidenceId"], intake)
        self.assertEqual(document["context"]["candidateTree"], self.disposition_context()["candidateTree"])
        self.assertEqual(self.dispose("SPEC-1", "fixed").returncode, 2)

    def test_disposition_binds_the_exact_validated_manifest_snapshot(self) -> None:
        bulk = self.repo / "bulk"; bulk.mkdir()
        for index in range(2500): (bulk / f"f{index:04d}.py").write_text("x" * 4096, encoding="utf-8")
        path = self.tmp / "race.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = self.record_review(path, "race-intake")
        self.assertEqual(intake.returncode, 0, intake.stderr)
        before_events = self.event_count()
        process = subprocess.Popen([sys.executable, str(WORKFLOW), "record", "advisor-disposition", "--slug", "review-summary",
            "--workflow-id", self.wid, "--finding", "SPEC-1", "--report-only", "--reason", "Current observation is nonmaterial",
            "--repo", str(self.repo)], cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
        self.assertIn("candidate", stderr)
        self.assertEqual(self.event_count(), before_events, "a stale candidate appended review evidence")


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


class SkillTextTests(unittest.TestCase):
    """The skills describe the workflow that exists: every link resolves and no removed feature is named."""

    def test_skill_links_resolve_and_name_only_live_features(self) -> None:
        import re
        marker = "SKILL_TEXT_STALE"
        roots = [ROOT / "docs" / "agents", *(ROOT / "skills" / name for name in (
            "tdd", "production-preflight", "production-code", "code-review", "codex-advisor",
            "repo-production-workflow", "diagnose", "repo-context-forge"))]
        documents = [path for root in roots for path in root.rglob("*.md")]
        self.assertTrue(documents, marker)
        removed = re.compile(r"RED/GREEN|redFailure|retainedEffect|hooks/lib/mcdc|Behavior Map requirements|#task-boundary-and-seams"
                             r"|\breleased\b|request sentence|quoting, verbatim|what-a-slice-must-prove|mcdc-decisive-contexts")
        for path in documents:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(removed.search(text), f"{marker}: {path.relative_to(ROOT)} names a removed feature")
            for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                relative, _, anchor = target.partition("#")
                destination = (path.parent / relative).resolve() if relative else path
                self.assertTrue(destination.exists(), f"{marker}: {path.relative_to(ROOT)} -> {target}")
                if anchor and destination.suffix == ".md":
                    headings = {re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", re.sub(r"[`*_]", "", line.lstrip("#").strip().lower())).strip())
                                for line in destination.read_text(encoding="utf-8").splitlines() if line.startswith("#")}
                    self.assertIn(anchor, headings, f"{marker}: {path.relative_to(ROOT)} -> {target}")
        # one authoritative MC/DC procedure, in the test reference the loop points at
        tdd = {name: (ROOT / "skills" / "tdd" / name).read_text(encoding="utf-8") for name in ("SKILL.md", "tests.md", "recorder.md")}
        self.assertIn("\n## MC/DC\n", tdd["tests.md"], marker)
        for element in ("EQUALS", "the formula that computes it", "masked", "repeated", "infeasible",
                        "Do not use tests for a requested change as evidence"):
            self.assertIn(element, tdd["tests.md"].split("\n## MC/DC\n", 1)[-1], f"{marker}: MC/DC section lacks {element}")
        # an added condition has no original decision to flip: its pair flips the edited one
        added = "ADDED_CONDITION_UNSATISFIABLE"
        mcdc = tdd["tests.md"].split("\n## MC/DC\n", 1)[-1]
        self.assertIn("change the original decision, or the edited decision for a condition the edit adds", mcdc, added)
        self.assertIn("decision flip (the edited decision for an added condition)", mcdc, added)
        holders = [path.relative_to(ROOT) for path in documents if "independence pair" in path.read_text(encoding="utf-8")]
        self.assertEqual(holders, [Path("skills/tdd/tests.md")], marker)
        self.assertIn("tests.md#mcdc", tdd["SKILL.md"], marker)
        self.assertIn("mocking.md", tdd["SKILL.md"], marker)
        self.assertNotIn("\n## Behavior Map", tdd["SKILL.md"], marker)
        self.assertIn("\n## Behavior Map\n", tdd["recorder.md"], marker)
        shown = subprocess.run([sys.executable, str(WORKFLOW), "record", "tdd-map", "--help"], capture_output=True, text=True)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn('"kind"', shown.stdout, marker + ": tdd-map help omits kind")
        self.assertNotIn("released", shown.stdout, marker + ": tdd-map help names a removed field")


if __name__ == "__main__":
    unittest.main(verbosity=2)
