#!/usr/bin/env python3
"""Recorder validation tests; these inputs do not prove that a review ran."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
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

    def review_finding(self, expected: int = 3) -> dict[str, object]:
        """An executable finding: a probe that fails on the candidate binds."""
        return {"id": "SPEC-1", "kind": "nonbehavioral", "claim": "wrong value", "probe": {
            "path": "test_spec1.py", "command": f"{sys.executable} -m unittest test_spec1",
            "content": f"import app, unittest\nclass S(unittest.TestCase):\n    def test_value(self): self.assertEqual(app.value, {expected})\n"}}

    def fixed(self, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.run_script(WORKFLOW, "record", "advisor-disposition", "--finding", identifier, "--fixed",
                               "--reason", "The current fixture was inspected.")

    def record_review(self, path: Path, context: str = "review") -> subprocess.CompletedProcess[str]:
        return self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", self.wid, "--input", str(path),
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
        result = self.fixed("SPEC-1")
        self.assertEqual(result.returncode, 2, marker)
        self.assertEqual(self.event_count(), before, marker)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual((state["findingStates"][0]["kind"], state["codeReview"]["findings"]),
                         ("behavioral", "pending"), marker)

    def test_a_review_finding_requires_a_claim_and_keeps_its_fields(self) -> None:
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

    def test_a_clean_rereview_settles_its_findings_and_refreshes_the_binding(self) -> None:
        marker, path = "PENDING_REVIEW_BINDING_STALE", self.tmp / "pending-review.json"
        status = lambda: json.loads(self.run_script(WORKFLOW, "status").stdout)
        path.write_text(json.dumps({"findings": [self.review_finding(2)]}))
        self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        first = status()
        self.assertEqual((first["findingStates"][0]["status"], first["nextAction"]), ("pending", "code-review"), marker)
        (self.repo / "app.py").write_text("value = 2\n")
        verified = self.run_script(WORKFLOW, "verify", "--slug", "review-summary", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        path.write_text(json.dumps({"findings": [{"id": "R-2", "kind": "nonbehavioral", "claim": "a note"}]}))
        self.assertEqual(self.record_review(path, "current-pending-review").returncode, 0, marker)
        state = status()
        self.assertEqual(({e["findingId"]: e["status"] for e in state["findingStates"]}["SPEC-1"], state["codeReview"]["status"]),
                         ("resolved", "passed"), marker + ": the clean re-review left its finding open")
        self.assertNotIn(state.get("reviewManifestId"), (None, first["reviewManifestId"]), marker)

    def test_legacy_empty_document_is_a_no_finding_intake(self) -> None:
        path = self.tmp / "legacy-empty.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded = self.record_review(path, "legacy-empty")
        self.assertEqual(recorded.returncode, 0, "LEGACY_EMPTY_REVIEW_REJECTED" + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", "LEGACY_EMPTY_REVIEW_REJECTED")


    def test_rejected_recorder_call_appends_no_event(self) -> None:
        rebegun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        new_wid = read_workflow(resolve_repo_identity(self.repo))["workflowId"]
        before_events = self.event_count()

        payload = self.tmp / "premature.json"
        payload.write_text(json.dumps({"findings": []}), encoding="utf-8")
        premature = self.run_script(
            WORKFLOW, "record", "review", "--slug", "review-summary", "--workflow-id", new_wid,
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
                             r"|\breleased\b|request sentence|quoting, verbatim|what-a-slice-must-prove|mcdc-decisive-contexts"
                             r"|appeal|recurring-behavioral-repairs|--review-context-id|repair owner|fixSketch|rejected-with-evidence")
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
