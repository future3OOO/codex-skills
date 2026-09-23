#!/usr/bin/env python3
"""Adversarial attacks on the finding/design authority surfaces (issue #179).

Every probe drives the real workflow CLI over a real SQLite ledger in a scratch
fixture repository. One TestCase class per mapped Behavior Map item so each
RED/GREEN cycle targets exactly one recorded surface.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.tests.support import build_document, checkpoint_channels, record_context_forge  # noqa: E402


class AttackHarness(unittest.TestCase):
    """One scratch repository, state root, and workflow per test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="finding-attacks-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
        self.env.update({
            "CODEX_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "-q")
        self.git("config", "user.email", "attack@example.invalid")
        self.git("config", "user.name", "Attack Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        self.design_absent = self.tmp / "design-absent.json"
        self.design_absent.write_text(json.dumps({
            "schemaVersion": 1, "status": "absent", "reason": "attack fixture",
        }), encoding="utf-8")
        self.documents = 0

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CODEX_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if "advisor-result" in values and "--design-declaration" not in values:
            values += ["--design-declaration", str(self.design_absent)]
        # --repo travels directly after the subcommand so a runner command after
        # the -- sentinel never swallows it.
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *values[:(split := 2 if values[0] == "record" else 1)],
             "--repo", str(self.repo), *values[split:]],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

    def ok(self, *args: str) -> dict[str, object]:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, " ".join(args[:2]) + ": " + result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1]) if result.stdout.strip() else {}

    def status(self) -> dict[str, object]:
        return self.ok("status")

    def json_file(self, name: str, value: object) -> Path:
        self.documents += 1
        path = self.tmp / f"{self.documents}-{name}"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def begin(self, slug: str, intent: str = "attack fixture intent") -> str:
        begun = self.cli("begin", "--slug", slug, "--intent", intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        record_context_forge(self.repo, self.tmp)
        return str(json.loads(begun.stdout)["workflowId"])

    def behavioral_intake(self, slug: str, wid: str, claim: str, identifier: str = "SPEC-1") -> str:
        envelope = self.json_file("envelope.json", {"schemaVersion": 1, "findings": [{
            "id": identifier, "claim": claim, "material": True, "kind": "behavioral",
        }], "verdict": "completed"})
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                           "--stage", "preflight", "--source", "codex-advisor",
                           "--input", str(envelope))
        return str(self.status()["advisorPreflight"]["intakeEvidence"])

    def owned_map(self, intake_id: str, *, marker: str) -> list[dict[str, object]]:
        return [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "advisor finding attack",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}],
        }]

    def record_preflight(self, slug: str, wid: str, behavior_map: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        payload = self.json_file("preflight.json", build_document("attack", behavior_map=behavior_map))
        return self.cli("record", "preflight", "--slug", slug, "--workflow-id", wid,
                        "--input", str(payload))

    def drive_attack_green(self, slug: str, marker: str, behavior_id: str = "BM_ATTACK", *, reassess: bool = True) -> None:
        probe = self.repo / "test_attack_probe.py"
        probe.write_text(
            "import app, unittest\n"
            "class AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", slug, "--phase", phase, "--behavior-id", behavior_id,
                                  "--", sys.executable, "-m", "unittest", "test_attack_probe"],
                                 cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)
        if not reassess:
            return
        update = self.json_file("reassess.json", {
            "sourceBehaviorId": behavior_id, "reassessment": "no new obligation",
            "items": [], "dispositions": [],
        })
        reassessed = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id",
                              str(self.status()["workflowId"]), "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, reassessed.stdout + reassessed.stderr)

    def fixed_disposition(
        self, wid: str, intake_id: str, occurrence: dict[str, object],
        premise_result: str = "true before the fix; corrected by the linked attack",
    ) -> Path:
        return self.json_file("fixed.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": "inspect app.py",
                            "result": premise_result},
                "occurrence": occurrence,
                "materialConsequence": {"claim": "callers observe the wrong value",
                                        "command": "import app", "result": "corrected"},
                "evidence": "owning attack GREEN through its recorded RED",
                "mechanism": "The constant initializer supplied 1 to every reader; initialize to 2 so fresh imports and existing callers observe the required value. No other writer exists.",
            }],
        })

    ZERO_DOMAIN = {"domain": "every caller-reachable read of app.value", "count": 0,
                   "complete": True, "command": "python -m unittest test_attack_probe",
                   "result": "count=0 after the fix"}
    SEAM_ONLY = {"seam": "fixture app module",
                 "reproduction": {"command": "python -m unittest test_attack_probe",
                                  "result": "expected 2, got 1"}}

    def open_pytest_pass(self, slug: str, marker: str) -> str:
        wid = self.begin(slug)
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        owned = self.record_preflight(slug, wid, [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "requested behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        return wid

    def mapped_tdd(self, slug: str, phase: str, command: list[str],
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                               "--", *command],
                              cwd=ROOT, env=env or self.env, text=True, capture_output=True,
                              check=False)

    def write_probe(self, marker: str) -> None:
        (self.repo / "test_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )

    def refused_unchanged(self, marker: str, action) -> subprocess.CompletedProcess[str]:
        before = self.status()
        events = len(json.loads(self.ok_text("history"))["events"])
        result = action()
        self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
        self.assertEqual(self.status(), before, marker + ": a refusal mutated workflow state")
        self.assertEqual(len(json.loads(self.ok_text("history"))["events"]), events,
                         marker + ": a refusal appended history")
        return result

    def ok_text(self, *args: str) -> str:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def plant_external_victim(self, marker: str) -> dict[str, str]:
        """Empty in-repo victim/ shadowing an external importable package."""
        (self.repo / "victim").mkdir()
        external = self.tmp / "outside" / "victim"
        external.mkdir(parents=True)
        (external / "__init__.py").write_text("", encoding="utf-8")
        (external / "test_external.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertTrue(False, {marker!r})\n",
            encoding="utf-8",
        )
        return dict(self.env, PYTHONPATH=str(self.tmp / "outside"))


class PendingAdvisorRetries(AttackHarness):
    # Captured issue #37 recorder inputs; no provider/model behavior is claimed.
    CAPTURED = {"id": "SPEC-P2", "claim": "Diagnostic marker file is absent",
                "material": True, "kind": "behavioral"}

    def test_pending_retry_preserves_material_escalation(self) -> None:
        wid = self.begin("pending-retry")
        first = self.accept(wid, [{**self.CAPTURED, "material": False}])
        original = first["findingStates"][0]["intakeEvidenceId"]
        state = self.accept(wid, [self.CAPTURED])
        self.assertEqual((len(state["findingStates"]), state["findingStates"][0]["material"],
                          state["advisorPreflight"]["findings"]), (1, True, "pending"),
                         "MATERIAL_ESCALATION_LOST")
        self.assertFalse(self.ok("evidence", "--full", "--evidence-id", original)["document"]["findings"][0]["material"])

    def test_behavioral_promotion_requires_current_behavioral_proof(self) -> None:
        marker = "BEHAVIORAL_PROMOTION_CLOSED_WITH_OLD_NONBEHAVIORAL_PROOF"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1", "kind": "nonbehavioral"}
        old = self.accept(wid, [finding])["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(old, marker="VALUE_NOT_TWO")).returncode, 0)
        for _ in range(2):
            state = self.accept(wid, [{**finding, "kind": "behavioral"}])
        entry = state["findingStates"][0]
        current = state["advisorPreflight"]["intakeEvidence"]
        receipt = self.ok("verify", "--slug", "pending-retry", "--", "git", "diff", "--check")
        receipt = f"{receipt['evidenceId']}:{receipt['runIndex']}"
        # The nonbehavioral measurement it was intaken with no longer closes it: the
        # promoted obligation needs its owning attack GREEN through RED.
        for reference in (old, current):
            full = json.loads(self.fixed_disposition(wid, reference, dict(self.ZERO_DOMAIN)).read_text())
            full["dispositions"][0]["kind"] = "nonbehavioral" if reference == old else "behavioral"
            concise = {"intakeEvidenceId": reference, "dispositions": [{"finding_id": "SPEC-1",
                       "status": "fixed", "reason": "all reads observe 2", "evidenceRefs": [receipt]}]}
            for document in (full, concise):
                refused = self.refused_unchanged(marker, lambda: self.cli(
                    "record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid, "--stage",
                    "preflight", "--findings", "addressed", "--input", str(self.json_file("promotion.json", document))))
                self.assertIn("GREEN", refused.stderr, marker + ": " + refused.stderr)
        self.assertEqual(entry["kind"], "behavioral", marker)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid, "--stage", "preflight",
                "--findings", "addressed", "--input", str(self.fixed_disposition(wid, current, dict(self.ZERO_DOMAIN))))
        self.assertEqual(self.status()["findingStates"][0]["status"], "fixed", marker)
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", old)["document"]["findings"][0]["kind"], "nonbehavioral", marker)

    def test_retained_identity_survives_changed_wording_and_ids(self) -> None:
        marker = "FINDING_IDENTITY_SPLIT"
        wid = self.begin("pending-retry")
        first = self.accept(wid, [self.CAPTURED])
        original = first["advisorPreflight"]["intakeEvidence"]
        changed = {**self.CAPTURED, "claim": "The same mechanism also loses '  x  '"}
        for finding in (changed, {**changed, "id": "RENAMED", "claim": "  " + changed["claim"] + "  "}):
            state = self.accept(wid, [finding])
            self.assertEqual(len(state["findingStates"]), 1, marker)
            self.assertEqual(state["findingStates"][0]["intakeEvidenceId"], original, marker)
        closed = self.close_finding(wid, state["advisorPreflight"]["intakeEvidence"], finding)
        self.assertEqual(closed.returncode, 0, "SAME_STAGE_ALIAS_RETURN_REFUSED: " + closed.stderr)
        distinct = {**changed, "id": "DISTINCT", "claim": changed["claim"].replace("'  x  '", "' x '")}
        self.assertEqual(len(self.accept(wid, [distinct])["findingStates"]), 2, marker)

    def test_conflicting_matches_need_an_owned_explicit_reference(self) -> None:
        marker = "AMBIGUOUS_FINDING_MERGED"
        wid = self.begin("pending-retry")
        a, b = self.CAPTURED, {**self.CAPTURED, "id": "B", "claim": "another mechanism"}
        first = self.accept(wid, [a, b])
        ref = first["advisorPreflight"]["intakeEvidence"]
        collision = {**a, "claim": b["claim"]}
        result = self.response(wid, [collision])
        self.assertEqual(result.returncode, 2, marker)
        self.assertIn("ambiguous", result.stderr, marker)
        for identifier in (a["id"], b["id"]):
            state = self.accept(wid, [{**collision, "priorFinding": {"evidenceId": ref, "id": identifier}}])
            self.assertEqual(len(state["findingStates"]), 2, marker)
        foreign = {**collision, "priorFinding": {"evidenceId": "evidence-foreign", "id": a["id"]}}
        self.refused_unchanged(marker, lambda: self.response(wid, [foreign]))

    def test_fixed_requires_owned_reusable_mechanism(self) -> None:
        marker = "UNSUPPORTED_MECHANISM_CLOSED"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1", "claim": "app.value is not 2"}
        ref = self.accept(wid, [finding])["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(ref, marker="VALUE_NOT_TWO")).returncode, 0)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO", reassess=False)
        self.assertNotIn("mechanismEvidence", self.status()["findingStates"][0], marker)
        full = json.loads(self.fixed_disposition(wid, ref, dict(self.ZERO_DOMAIN)).read_text())
        full["dispositions"][0].pop("mechanism")
        evidence = self.status()["tddEvidence"]
        measured = self.ok("evidence", "--full", "--evidence-id", evidence)["document"]
        green = next(i for i, run in enumerate(measured["runs"]) if run.get("phase") == "green")
        concise = {"intakeEvidenceId": ref, "dispositions": [{"finding_id": "SPEC-1", "status": "fixed",
                    "reason": "all reads now yield 2", "evidenceRefs": [f"{evidence}:{green}"]}]}
        for document in (full, concise):
            document["dispositions"][0]["mechanism"] = {"evidenceId": "evidence-foreign", "id": "SPEC-1"}
            result = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                              "--stage", "preflight", "--findings", "addressed", "--input",
                              str(self.json_file("mechanism.json", document)))
            self.assertEqual(result.returncode, 2, marker)
            self.assertIn("mechanism", result.stderr, marker)
        # A first fix is proved by its GREEN owner; only a recurrence must explain what the last repair missed.
        concise["dispositions"][0].pop("mechanism")
        self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                "--stage", "preflight", "--findings", "addressed", "--input", str(self.json_file("mechanism.json", concise)))
        self.assertEqual(self.status()["findingStates"][0]["status"], "fixed", marker)
        self.assertNotIn("mechanismEvidence", self.status()["findingStates"][0], marker)

    def test_recurrence_preserves_mechanism_and_retries_do_not_escalate(self) -> None:
        marker = "RECURRENCE_HISTORY_LOST"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1"}
        first = self.accept(wid, [finding])
        original = first["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(original, marker="VALUE_NOT_TWO")).returncode, 0)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        path = self.fixed_disposition(wid, original, dict(self.ZERO_DOMAIN))
        document = json.loads(path.read_text())
        document["dispositions"][0]["mechanism"] = "The constant initializer supplied the wrong value to all reads; change it to 2."
        path.write_text(json.dumps(document))
        self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                "--stage", "preflight", "--findings", "addressed", "--input", str(path))
        fixed = self.status()["findingStates"][0]
        state = self.accept(wid, [finding])
        current = state["findingStates"][-1]
        self.assertEqual(current.get("recurrence"), 1, marker)
        self.assertEqual(current.get("mechanismEvidence"), fixed.get("mechanismEvidence"), marker)
        self.assertEqual(state["findingStates"][0], fixed, marker)
        ledger = checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", [])
        self.assertEqual([owner["id"] for owner in ledger[-1]["owners"]], ["BM_ATTACK"],
                         "RECURRENCE_LEDGER_LOST_OWNERS")
        retried = self.accept(wid, [{**finding, "claim": "The current counterexample also affects a fresh process"}])
        self.assertEqual(len(retried["findingStates"]), 2, marker)
        self.assertEqual(retried["findingStates"][-1].get("recurrence"), 1, marker)
        refused = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                           "--stage", "preflight", "--findings", "addressed", "--input", str(path))
        self.assertEqual(refused.returncode, 2, marker)

    def test_active_reassessment_diagnosis_is_recoverable_before_closure(self) -> None:
        marker = "ACTIVE_MECHANISM_LOST"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1"}
        ref = self.accept(wid, [finding])["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(ref, marker="VALUE_NOT_TWO")).returncode, 0)
        explanation = "Fresh imports share the incorrect initializer. " * 100 + "Distinct final diagnosis."
        update = self.json_file("diagnosis.json", {"reassessment": explanation,
            "items": [{**self.owned_map(ref, marker="FRESH_READ_WRONG")[0], "id": "BM_FRESH"}]})
        recorded = self.ok("record", "tdd-map", "--slug", "pending-retry", "--workflow-id", wid, "--input", str(update))
        summary = self.cli("summary").stdout
        self.assertIn("Mechanism", summary, marker)
        self.assertIn("Fresh imports share the incorrect initializer.", summary, "RECOVERY_DIAGNOSIS_NOT_SHOWN")
        self.assertLessEqual(len(summary.strip()), 3000, marker)
        reference = self.status()["findingStates"][0].get("mechanismEvidence")
        self.assertEqual(reference, {"evidenceId": recorded["summaryId"], "id": "SPEC-1"}, marker)
        recovered = self.ok("evidence", "--full", "--evidence-id", reference["evidenceId"])["document"]
        self.assertEqual(recovered["reassessment"], explanation, marker)

    def test_second_recurrence_requires_reviewer_repair_and_lead_review(self) -> None:
        marker = "REPAIR_OWNERSHIP_BYPASSED"
        self.env["CODEX_THREAD_ID"] = "recurrence-lead-input"
        wid = self.start_final()
        finding = {**self.CAPTURED, "id": "SPEC-1"}
        state = self.recur_twice(wid, finding)
        current = state["findingStates"][-1]
        self.assertEqual(current.get("recurrence"), 2, marker)
        owner = current.get("repairOwner", {})
        self.assertEqual(owner.get("implementerContextId"), "retry-fixture", marker)
        self.assertEqual(owner.get("reviewerContextId"), self.env.get("CODEX_THREAD_ID"), marker)
        ref = state["advisorPreflight"]["intakeEvidence"]
        self.ok("record", "tdd-map", "--slug", "pending-retry", "--workflow-id", wid, "--input", str(self.json_file("owner.json", {
            "reassessment": "Second recurrence requires the reviewer to correct the complete read mechanism.",
            "dispositions": [{"id": "BM_ATTACK", "sourceRefs": [{"type": "finding", "evidenceId": ref, "id": "SPEC-1"}]}]})))
        result = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, ref, dict(self.ZERO_DOMAIN))))
        self.assertEqual(result.returncode, 2, marker)
        self.assertIn("lead review", result.stderr, marker)
        for tool, target, expected_denial in (("followup_task", "retry-fixture", False),
                                               ("followup_task", "another-reviewer", True),
                                               ("spawn_agent", "retry-fixture", True)):
            hook = subprocess.run([sys.executable, str(ROOT / "hooks/rcf-intake-gate.py")],
                input=json.dumps({"tool_name": tool, "session_id": self.env.get("CODEX_THREAD_ID"),
                                  "cwd": str(self.repo), "tool_input": {"target": target}}),
                env=self.env, text=True, capture_output=True)
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertEqual('"deny"' in hook.stdout, expected_denial, marker)
        record_context_forge(self.repo, self.tmp)
        self.ok("verify", "--slug", "pending-retry", "--", "git", "diff", "--check")
        self.ok("verify", "--slug", "pending-retry", "--kind", "quality-gate", "--base-ref", "HEAD")
        assessment = self.json_file("ordinary-review.json", {"findings": []})
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                "--review-context-id", "fresh-rooted-reviewer", "--input", str(assessment))
        assessed = self.status()["findingStates"][-1]
        self.assertEqual(assessed["repairOwner"], owner, marker)
        self.assertNotIn("repairReviewEvidence", assessed, marker)
        self.assertEqual(assessed["status"], "pending", marker)
        review = self.json_file("lead-review.json", {"findings": [], "implementationContextId": "retry-fixture"})
        for reviewer, expected in (("retry-fixture", 2), (self.env["CODEX_THREAD_ID"], 0)):
            result = self.cli("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                              "--review-context-id", reviewer,
                              "--input", str(review))
            self.assertEqual(result.returncode, expected, marker + result.stderr)
        self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                "--stage", "preflight", "--findings", "addressed", "--input",
                str(self.fixed_disposition(wid, ref, dict(self.ZERO_DOMAIN))))
        self.assertEqual(self.status()["finalReview"]["status"], "pending", marker)

    def response(self, wid: str, findings: list[dict[str, object]], *, stage: str = "preflight",
                 source: str = "codex-advisor", raw: str | None = None) -> subprocess.CompletedProcess[str]:
        envelope = self.json_file("retry.json", {
            "schemaVersion": 1, "findings": findings,
            "verdict": "completed" if stage == "preflight" else
                       "fix-before-commit" if any(f["material"] for f in findings) else "commit-ready",
        })
        if raw is not None:
            envelope.write_text(raw, encoding="utf-8")
        return self.cli("record", "advisor-result", "--slug", "pending-retry", "--workflow-id", wid,
                        "--stage", stage, "--source", source, "--input", str(envelope))

    def accept(self, wid: str, findings: list[dict[str, object]], **kwargs) -> dict[str, object]:
        result = self.response(wid, findings, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.status()

    def close_finding(self, wid: str, intake: str, finding: dict[str, object], *,
                      stage: str = "preflight", status: str = "rejected-with-evidence") -> subprocess.CompletedProcess[str]:
        measured = subprocess.run(["test", "-f", "app.py"], cwd=self.repo, env=self.env)
        self.assertEqual(measured.returncode, 0)
        document = self.json_file("close.json", {
            "context": {"workflowId": wid, "candidateTree": self.status()["activeCandidateTree"]},
            "intakeEvidenceId": intake,
            "dispositions": [{"finding_id": finding["id"], "kind": finding["kind"], "status": status,
                "premise": {"claim": "app.py is absent", "command": "test -f app.py", "result": "false"},
                "occurrence": {"domain": "the app.py file", "count": 0, "complete": True,
                               "command": "test -f app.py", "result": "exit 0; absent count 0"},
                "materialConsequence": {"claim": "app.py is unavailable", "command": "test -f app.py",
                                        "result": "false"},
                "evidence": "test -f app.py exited 0"}],
        })
        return self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                        "--stage", stage, "--findings", "addressed", "--input", str(document))

    def ready(self, wid: str, *context: str) -> None:
        record_context_forge(self.repo, self.tmp)
        self.ok("verify", "--slug", "pending-retry", "--", "git", "diff", "--check")
        self.ok("verify", "--slug", "pending-retry", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                *(context or ("--review-context-id", "retry-fixture")),
                "--input", str(self.json_file("review.json", {"findings": []})))

    def start_final(self, *context: str) -> str:
        wid = self.open_pytest_pass("pending-retry", "VALUE_NOT_TWO")
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ready(wid, *context)
        return wid

    def recur_twice(self, wid: str, finding: dict[str, object]) -> dict[str, object]:
        state = self.accept(wid, [finding])
        for recurrence in range(2):
            ref = state["advisorPreflight"]["intakeEvidence"]
            update = self.json_file("owner.json", {"reassessment": f"Repair {recurrence}: the initializer affects every read; set it to 2.",
                "dispositions": [{"id": "BM_ATTACK", "sourceRefs": [{"type": "finding", "evidenceId": ref, "id": "SPEC-1"}]}]})
            self.ok("record", "tdd-map", "--slug", "pending-retry", "--workflow-id", wid, "--input", str(update))
            document = self.fixed_disposition(wid, ref, dict(self.ZERO_DOMAIN))
            self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                    "--stage", "preflight", "--findings", "addressed", "--input", str(document))
            state = self.accept(wid, [finding])
        return state

    def test_an_unnamed_reviewer_context_is_recovered_by_name(self) -> None:
        marker = "REVIEWER_OWNER_LOST"
        self.env["CODEX_THREAD_ID"] = "recurrence-lead-input"
        wid = self.open_pytest_pass("pending-retry", "VALUE_NOT_TWO")
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        record_context_forge(self.repo, self.tmp)
        self.ok("verify", "--slug", "pending-retry", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.ok("record", "review", "--input", str(self.json_file("unnamed.json", {"findings": []})))
        state = self.recur_twice(wid, {**self.CAPTURED, "id": "SPEC-1"})
        self.assertNotIn("repairOwner", state["findingStates"][-1], marker)
        # The reviewer whose named review recovers ownership must remain dispatchable.
        spawned = subprocess.run([sys.executable, str(ROOT / "hooks/rcf-intake-gate.py")], env=self.env, text=True,
            capture_output=True, input=json.dumps({"tool_name": "spawn_agent", "session_id": self.env["CODEX_THREAD_ID"],
                                                   "cwd": str(self.repo), "tool_input": {}}))
        self.assertNotIn('"deny"', spawned.stdout, f"{marker}: {spawned.stdout}")
        review = self.json_file("lead-review.json", {"findings": [], "implementationContextId": "retry-fixture"})
        refused = self.cli("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                           "--review-context-id", self.env["CODEX_THREAD_ID"], "--input", str(review))
        self.assertIn("--review-context-id", refused.stderr, marker)
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", wid, "--review-context-id", "retry-fixture",
                "--input", str(self.json_file("named.json", {"findings": []})))
        self.assertEqual(self.status()["findingStates"][-1].get("repairOwner"),
                         {"implementerContextId": "retry-fixture", "reviewerContextId": self.env["CODEX_THREAD_ID"]}, marker)
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                "--review-context-id", self.env["CODEX_THREAD_ID"], "--input", str(review))

    def test_preflight_retries_keep_one_reference(self) -> None:
        marker = "DUPLICATE_PREFLIGHT_OBLIGATION"
        wid = self.begin("pending-retry")
        first = self.accept(wid, [self.CAPTURED])
        original = first["advisorPreflight"]["intakeEvidence"]
        owned = self.owned_map(original, marker="MARKER_ABSENT")
        owned[0]["sourceRefs"][0]["id"] = self.CAPTURED["id"]
        self.assertEqual(self.record_preflight("pending-retry", wid, owned).returncode, 0)
        for _ in range(2):
            replay = self.accept(wid, [self.CAPTURED])
            self.assertEqual(replay["findingStates"], first["findingStates"], marker)
            self.assertEqual(replay["advisorPreflight"]["intakeEvidence"], original, marker)
        self.assertEqual(self.record_preflight("pending-retry", wid, owned).returncode, 0, marker)
        closed = self.close_finding(wid, original, self.CAPTURED)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "addressed", marker)
        print("target=workflow.py advisor-result scale=3 duplicate_retries=2 limit=0 "
              "added_pending=0 added_references=0 extra_dispositions=0 extra_proof_executions=0")

    def test_retry_preserves_green_progress_through_completion(self) -> None:
        marker = "RETRY_LOST_PROGRESS"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1", "claim": "app.value is not 2"}
        first = self.accept(wid, [finding])
        original = first["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(original, marker="VALUE_NOT_TWO")).returncode, 0)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        before = self.status()
        ledger = checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", [])
        self.accept(wid, [finding])
        after = self.status()
        for key in ("findingStates", "preflightEvidence", "tddEvidence", "tddCycleCount"):
            self.assertEqual(after[key], before[key], marker)
        self.assertEqual(checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", []), ledger, marker)
        closed = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, original, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        self.ready(wid)
        self.accept(wid, [], stage="final")
        self.ok("complete")

    def test_refreshed_final_retry_closes_original_obligation(self) -> None:
        marker = "DUPLICATE_FINAL_OBLIGATION"
        wid = self.start_final()
        finding = {**self.CAPTURED, "id": "SPEC-FINAL", "kind": "nonbehavioral"}
        first = self.accept(wid, [finding], stage="final")
        original = first["finalReview"]["intakeEvidence"]
        self.refused_unchanged("REFUSAL_MUTATED_HISTORY", lambda: self.response(wid, [finding], stage="final"))
        (self.repo / "issue253_marker.txt").write_text("present after correction\n")
        self.refused_unchanged("REFUSAL_MUTATED_HISTORY", lambda: self.response(wid, [finding], stage="final"))
        self.ready(wid)
        refreshed = self.status()
        replay = self.accept(wid, [finding], stage="final")
        self.assertEqual(replay["findingStates"], first["findingStates"], marker)
        self.assertEqual(replay["finalReview"]["intakeEvidence"], original, marker)
        for key in ("activeCandidateTree", "verificationEvidence", "qualityGateEvidence", "codeReviewEvidence"):
            self.assertEqual(replay[key], refreshed[key], marker)
        self.assertNotEqual(replay["activeCandidateTree"], first["activeCandidateTree"], marker)
        closed = self.close_finding(wid, original, finding, stage="final", status="fixed")
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        self.ok("complete")

    def test_mixed_retry_preserves_identity_and_returns_usable_intake(self) -> None:
        marker = "DUPLICATE_MIXED_OBLIGATION"
        wid = self.begin("pending-retry")
        a = {**self.CAPTURED, "kind": "nonbehavioral"}
        b = {**a, "id": "NEW-B"}
        first = self.accept(wid, [a])
        mixed = self.accept(wid, [a, b])
        current = mixed["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(len(mixed["findingStates"]), 2, marker)
        self.assertEqual(mixed["findingStates"][0], {**first["findingStates"][0], "observations": [
            {"evidenceId": current, "id": a["id"], "producer": "codex-advisor", "stage": "preflight"}]}, marker)
        closed = self.close_finding(wid, current, b)
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        self.assertEqual(self.status()["findingStates"][0]["status"], "pending", marker)
        closed = self.close_finding(wid, current, a)
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "addressed", marker)

    def test_changed_omitted_settled_and_foreign_findings(self) -> None:
        marker = "DISTINCT_FINDING_SUPPRESSED"
        wid = self.begin("pending-retry")
        first = self.accept(wid, [self.CAPTURED])
        variants = [{**self.CAPTURED, "id": "NEW", "claim": "different claim"},
                    {**self.CAPTURED, "id": "NOTE", "kind": "nonbehavioral", "material": False},
                    {**self.CAPTURED, "id": "DOC", "kind": "nonbehavioral"}]
        for count, finding in enumerate(variants, 2):
            self.assertEqual(len(self.accept(wid, [finding])["findingStates"]), count, marker)
        omitted = self.accept(wid, [])
        self.assertEqual(len(omitted["findingStates"]), 4, marker)
        self.assertEqual(omitted["advisorPreflight"]["findings"], "pending", marker)
        original = first["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.close_finding(wid, original, self.CAPTURED).returncode, 0)
        settled = self.status()["findingStates"][0]
        replay = self.accept(wid, [self.CAPTURED], raw=json.dumps({"schemaVersion": 1,
            "findings": [self.CAPTURED], "verdict": "completed"}, indent=2))
        self.assertEqual(len(replay["findingStates"]), 5, marker)
        self.assertEqual(replay["findingStates"][0], settled, marker)
        self.refused_unchanged(marker, lambda: self.response("foreign-workflow", [self.CAPTURED]))
        self.refused_unchanged(marker, lambda: self.response(wid, [self.CAPTURED], source="other-producer"))
        other = self.ok("begin", "--slug", "pending-retry", "--intent", "new workflow")["workflowId"]
        record_context_forge(self.repo, self.tmp)
        independent = self.accept(other, [self.CAPTURED])
        self.assertEqual(len(independent["findingStates"]), 1, marker)
        self.assertNotEqual(independent["advisorPreflight"]["intakeEvidence"], original, marker)
        reference = independent["advisorPreflight"]["intakeEvidence"]
        owned = self.owned_map(reference, marker="VALUE_NOT_TWO")
        owned[0]["sourceRefs"][0]["id"] = self.CAPTURED["id"]
        self.assertEqual(self.record_preflight("pending-retry", other, owned).returncode, 0)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ready(other)
        across_stage = self.accept(other, [self.CAPTURED], stage="final")
        self.assertEqual(len(across_stage["findingStates"]), 1, marker)
        self.assertEqual(len(across_stage["findingStates"][0]["observations"]), 1, marker)
        review = self.json_file("cross-producer.json", {"findings": [{**self.CAPTURED,
            "id": "REVIEW-ALIAS", "axis": "Spec", "severity": "high", "location": "app.py",
            "evidence": "recorder input, not a native review", "consequence": "same unresolved obligation",
            "smallest_action": "repair the original finding"}]})
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", other,
                "--review-context-id", "retry-fixture",
                "--input", str(review))
        cross = self.status()
        self.assertEqual(len(cross["findingStates"]), 1, marker)
        self.assertEqual(cross["codeReview"]["findings"], "pending", marker)
        returned = self.accept(other, [self.CAPTURED], stage="final")["finalReview"]["intakeEvidence"]
        closed = self.close_finding(other, returned, self.CAPTURED, stage="final")
        self.assertEqual(closed.returncode, 0, "CROSS_STAGE_RETURN_CLOSURE_REFUSED: " + closed.stderr)
        final_wid = self.start_final()
        note = {**self.CAPTURED, "material": False, "kind": "nonbehavioral"}
        self.accept(final_wid, [note], stage="final")
        (self.repo / "note.txt").write_text("new candidate\n")
        self.ready(final_wid)
        changed_verdict = self.accept(final_wid, [note, {**note, "id": "NEW", "material": True}], stage="final")
        self.assertEqual(len(changed_verdict["findingStates"]), 2, marker)

    def test_observations_retain_raw_bytes(self) -> None:
        marker = "OBSERVATION_LOST"
        wid = self.begin("pending-retry")
        raws = [json.dumps({"schemaVersion": 1, "findings": [self.CAPTURED], "verdict": "completed"},
                           indent=indent) for indent in (None, 2, 4)]
        for raw in raws:
            self.accept(wid, [self.CAPTURED], raw=raw)
        history = self.ok("history")
        documents = [self.ok("evidence", "--full", "--evidence-id", evidence_id)
                     for event in history["events"] if event["kind"] == "advisor-preflight-result"
                     for evidence_id in event["evidenceIds"]]
        observations = [entry["document"] for entry in documents if entry["kind"] == "finding-intake-preflight"]
        self.assertEqual(sorted(d["raw"] for d in observations), sorted(raws), marker)

    def test_invalid_payload_refuses_atomically(self) -> None:
        wid = self.begin("pending-retry")
        self.accept(wid, [self.CAPTURED])
        for raw in ('{"schemaVersion":1,"findings":[],"verdict":"commit-ready"}',
                    '{"schemaVersion":1,"schemaVersion":1,"findings":[],"verdict":"completed"}',
                    json.dumps({"schemaVersion": 1, "findings": [{**self.CAPTURED, "material": 1}],
                                "verdict": "completed"})):
            self.refused_unchanged("REFUSAL_MUTATED_HISTORY", lambda: self.response(wid, [], raw=raw))

    def test_concurrent_retries_register_once(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        wid = self.begin("pending-retry")
        envelope = self.json_file("concurrent.json", {"schemaVersion": 1,
            "findings": [self.CAPTURED], "verdict": "completed"})
        args = ("record", "advisor-result", "--slug", "pending-retry", "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--input", str(envelope))
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.cli(*args), range(3)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.status()["findingStates"]), 1, "CONCURRENT_DUPLICATE_OBLIGATION")

    def test_lookup_reads_each_pending_intake_once(self) -> None:
        import pstats
        wid = self.begin("pending-retry")
        findings = [{**self.CAPTURED, "id": f"P-{i}"} for i in range(3)]
        settled = {**self.CAPTURED, "id": "SETTLED", "kind": "nonbehavioral"}
        recorded = self.accept(wid, [settled])
        self.assertEqual(self.close_finding(wid, recorded["advisorPreflight"]["intakeEvidence"], settled).returncode, 0)
        self.accept(wid, findings)
        for i in range(4):
            self.accept(wid, [], raw=json.dumps({"schemaVersion": 1, "findings": [],
                        "verdict": "completed"}, indent=i))
        envelope = self.json_file("profile.json", {"schemaVersion": 1, "findings": findings,
                                                   "verdict": "completed"})
        profile = self.tmp / "retry.prof"
        result = subprocess.run([sys.executable, "-m", "cProfile", "-o", str(profile), str(WORKFLOW),
            "record", "advisor-result", "--repo", str(self.repo), "--slug", "pending-retry", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--input", str(envelope),
            "--design-declaration", str(self.design_absent)], cwd=ROOT, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stats = pstats.Stats(str(profile)).stats
        reads = sum(counts[0] for key, entry in stats.items() if key[2] == "evidence"
                    for caller, counts in entry[4].items() if caller[2] == "_register_finding_intake")
        self.assertLessEqual(reads, 1, "UNBOUNDED_PENDING_LOOKUP")
        self.assertFalse(any(key[2] == "history" for key in stats), "UNBOUNDED_PENDING_LOOKUP")
        print(f"target=workflow.py advisor-result pending_findings=3 pending_intakes=1 history_retries=4 "
              f"read_limit=1 observed_registration_reads={reads}")


    def test_appeal_reads_original_intake_once(self) -> None:
        self.assert_appeal_reads_once(shared=False)

    def test_shared_appeal_reads_original_intake_once(self) -> None:
        self.assert_appeal_reads_once(shared=True)

    def assert_appeal_reads_once(self, *, shared: bool) -> None:
        wid = self.start_final()
        a = {**self.CAPTURED, "id": "A", "kind": "nonbehavioral"}
        b = {**a, "id": "B", "material": not shared}
        c = {**a, "id": "C"}
        first = self.accept(wid, [a, b] if shared else [a], stage="final")
        original = first["finalReview"]["intakeEvidence"]
        self.assertEqual(self.close_finding(wid, original, a, stage="final").returncode, 0)
        envelope = self.json_file("appeal.json", {"schemaVersion": 1, "findings": [a, b, c] if shared else [a, b],
                                                 "verdict": "fix-before-commit"})
        reads_file = self.tmp / "reads.json"
        # Observe real calls without replacing the recorder or its collaborators.
        tracer = (
            "import atexit,json,runpy,sys; from pathlib import Path; reads=[]; "
            "sys.setprofile(lambda f,e,a: reads.append(f.f_locals.get('evidence_id')) "
            "if e=='call' and f.f_code.co_name=='evidence' else None); "
            f"atexit.register(lambda: Path({str(reads_file)!r}).write_text(json.dumps(reads))); "
            "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        result = subprocess.run([sys.executable, "-c", tracer, str(WORKFLOW), "record", "advisor-result",
            "--repo", str(self.repo), "--slug", "pending-retry", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(envelope),
            "--design-declaration", str(self.design_absent)], cwd=ROOT, env=self.env,
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        count = json.loads(reads_file.read_text()).count(original)
        self.assertEqual(count, 1, "REFERENCED_INTAKE_READ_TWICE")
        states = self.status()["findingStates"]
        expected = [("A", "pending"), ("B", "pending")] + ([("C", "pending")] if shared else [])
        self.assertEqual([(f["findingId"], f["status"]) for f in states], expected)
        self.assertEqual(states[0]["intakeEvidenceId"], original)
        self.assertEqual(states[0]["appealStatus"], "disagreement")
        if shared:
            self.assertEqual(states[1], {**first["findingStates"][1], "observations": [
                {"evidenceId": self.status()["finalReview"]["intakeEvidence"],
                 "id": "B", "producer": "codex-advisor", "stage": "final"}]})
            self.assertNotEqual(states[2]["intakeEvidenceId"], original)
        print(f"target=workflow.py advisor-result final_appeal pending_findings={len(states)} original_intake_read_limit=1 observed={count}")


class CheckpointIntent(AttackHarness):
    def test_checkpoint_exposes_the_recorded_verbatim_intent(self) -> None:
        marker = "CHECKPOINT_OMITS_RECORDED_INTENT"
        intent = "  attack | intent\nline two\n\ttabbed\t\n"
        self.begin("intent-attack", intent)
        for phase in ("preflight-advice", "final-review"):
            payload = checkpoint_channels(self.repo, self.env, phase)
            self.assertEqual(payload.get("intent"), intent, f"{marker}: {phase}")


class SamePassDesign(AttackHarness):
    def test_a_changed_design_declaration_records_in_the_same_pass(self) -> None:
        marker = "SAME_PASS_DESIGN_DEEPENING_REFUSED"
        wid = self.begin("design-deepening")
        self.ok("record", "advisor-result", "--slug", "design-deepening", "--workflow-id", wid,
                        "--stage", "preflight", "--source", "codex-advisor",
                        "--verdict", "completed")
        first_evidence = self.status().get("governedDesignEvidence")
        deepened = self.json_file("design-b.json", {
            "schemaVersion": 1, "status": "present", "sha256": "b" * 64,
        })
        second = self.cli("record", "advisor-result", "--slug", "design-deepening", "--workflow-id", wid,
                          "--stage", "preflight", "--source", "codex-advisor",
                          "--verdict", "completed", "--design-declaration", str(deepened))
        self.assertEqual(second.returncode, 0, marker + ": " + second.stdout + second.stderr)
        after = self.status()
        self.assertNotEqual(after.get("governedDesignEvidence"), first_evidence, marker)
        if isinstance(first_evidence, str) and first_evidence:
            prior = self.cli("evidence", "--full", "--evidence-id", first_evidence)
            self.assertEqual(prior.returncode, 0, marker + ": prior declaration unreadable")


class UnownedFindingBlocks(AttackHarness):
    def test_a_pending_behavioral_finding_rides_only_an_owning_map(self) -> None:
        marker = "UNOWNED_BEHAVIORAL_FINDING_UNGATED"
        wid = self.begin("finding-ownership")
        intake_id = self.behavioral_intake("finding-ownership", wid, "the reviewed value is wrong")
        unowned = self.record_preflight("finding-ownership", wid, [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "unrelated behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(unowned.returncode, 2, marker + ": " + unowned.stdout + unowned.stderr)
        self.assertIn("SPEC-1", unowned.stderr, marker)
        self.assertEqual(self.status().get("preflight"), "pending", marker)
        owned = self.record_preflight("finding-ownership", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)


class FixedRequiresGreenAttack(AttackHarness):
    def test_behavioral_fixed_requires_an_owning_green_through_red(self) -> None:
        marker = "FIXED_CLOSED_WITHOUT_GREEN_ATTACK"
        wid = self.begin("fixed-green")
        intake_id = self.behavioral_intake("fixed-green", wid, "the reviewed value is wrong")
        owned = self.record_preflight("fixed-green", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        early = self.cli("record", "advisor-disposition", "--slug", "fixed-green", "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input",
                         str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(early.returncode, 2, marker + ": " + early.stdout + early.stderr)
        self.assertIn("SPEC-1", early.stderr, marker)
        self.assertIn("GREEN", early.stderr, marker)
        self.drive_attack_green("fixed-green", marker)
        closed = self.cli("record", "advisor-disposition", "--slug", "fixed-green", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        states = self.status()["findingStates"]
        self.assertEqual(states[0]["status"], "fixed", marker)


class DomainFreeFixed(AttackHarness):
    def test_behavioral_fixed_requires_a_complete_domain_zero_measurement(self) -> None:
        marker = "DOMAIN_FREE_BEHAVIORAL_FIXED_CLOSED"
        wid = self.begin("fixed-domain")
        intake_id = self.behavioral_intake("fixed-domain", wid, "the reviewed value is wrong")
        owned = self.record_preflight("fixed-domain", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("fixed-domain", marker)
        # The premise-false escape must not close a behavioral finding without a
        # measured complete-domain zero: exactly how a broad finding narrows away.
        domain_free = self.cli("record", "advisor-disposition", "--slug", "fixed-domain", "--workflow-id", wid,
                               "--stage", "preflight", "--findings", "addressed", "--input",
                               str(self.fixed_disposition(wid, intake_id, dict(self.SEAM_ONLY),
                                                          premise_result="false")))
        self.assertEqual(domain_free.returncode, 2, marker + ": " + domain_free.stdout + domain_free.stderr)
        self.assertIn("complete domain", domain_free.stderr, marker)
        self.assertEqual(self.status()["findingStates"][0]["status"], "pending", marker)
        measured = self.cli("record", "advisor-disposition", "--slug", "fixed-domain", "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input",
                            str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(measured.returncode, 0, marker + ": " + measured.stdout + measured.stderr)


class ReservationGone(AttackHarness):
    def test_the_reservation_lifecycle_is_no_longer_accepted(self) -> None:
        marker = "RESERVATION_LIFECYCLE_STILL_ACCEPTED"
        wid = self.begin("reservation-gone")
        intake_id = self.behavioral_intake("reservation-gone", wid, "proof is missing")
        reservation = self.json_file("reservation.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "accepted-for-proof", "kind": "behavioral",
                "premise": {"claim": "proof is missing", "command": "inspect proof", "result": "true"},
                "occurrence": {"seam": "fixture app module", "reproduction": {
                    "command": "run probe", "result": "failed"}},
                "materialConsequence": {"claim": "proof is blocked", "command": "run proof",
                                        "result": "material"},
                "reservedBehaviorIds": ["BM_ATTACK", "BM_KEEP"],
                "seam": "fixture app module",
                "preservationObligations": ["keep the fixture value readable"],
            }],
        })
        refused = self.cli("record", "advisor-disposition", "--slug", "reservation-gone", "--workflow-id", wid,
                           "--stage", "preflight", "--findings", "addressed", "--input", str(reservation))
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("invalid", refused.stderr, marker)
        self.assertNotIn("findingReservations", self.status(), marker)
        from hooks.lib.workflow_documents import ADVISOR_DISPOSITIONS, REVIEWER_DISPOSITIONS
        self.assertNotIn("accepted-for-proof", ADVISOR_DISPOSITIONS, marker)
        self.assertNotIn("accepted-for-proof", REVIEWER_DISPOSITIONS, marker)


class SamePassAttack(AttackHarness):
    def review(self, slug: str, wid: str, path: Path) -> subprocess.CompletedProcess[str]:
        return self.cli("record", "review", "--slug", slug, "--workflow-id", wid,
                        "--review-context-id",
                        "same-pass-attack", "--input", str(path))

    def test_a_late_attack_is_proved_and_closed_in_the_same_workflow(self) -> None:
        from hooks.tests.test_workflow_hooks import WrapperPromptTests
        marker = "SAME_PASS_CORRECTION_FORCED_RESTART"
        slug = "same-pass"
        env = WrapperPromptTests.wrapper_rig(self)
        env["ADVISOR_SHIM_REPLY"] = '{"schemaVersion":1,"findings":[],"verdict":"commit-ready"}'
        consult = ("--slug", slug, "--phase", "final-review", "--design-absent", "attack fixture")
        wid = self.begin(slug)
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        main_marker = "MAIN_VALUE_NOT_TWO"
        owned = self.record_preflight(slug, wid, [{
            "id": "BM_MAIN", "kind": "contract", "basis": "requested behavior",
            "behavior": "the value becomes two", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": main_marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green(slug, main_marker, "BM_MAIN")
        for extra in (("--", sys.executable, "-c", "pass"),
                      ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

        # The late-discovered behavioral finding arrives through the lead review.
        intake = self.review(slug, wid, self.json_file("review-intake.json", {"findings": [{
            "id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "behavioral", "location": "app.py:1", "claim": "the note is missing",
            "evidence": "app exposes no note", "consequence": "callers cannot read the note",
            "smallest_action": "expose the note",
        }]}))
        self.assertEqual(intake.returncode, 0, marker + ": " + intake.stdout + intake.stderr)
        intake_id = str(json.loads(intake.stdout)["summaryId"])

        # Metadata-only correction: owning the finding through tdd-map neither
        # restarts the workflow nor invalidates the recorded graph context.
        record_context_forge(self.repo, self.tmp)
        note_marker = "NOTE_SEAM_ABSENT"
        added = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                         str(self.json_file("late-attack.json", {
                             "reassessment": "own the late review finding with a real attack",
                             "dispositions": [],
                             "items": [{
                                 "id": "BM_NOTE", "kind": "contract", "basis": "review finding attack",
                                 "behavior": "the note is exposed", "seam": "fixture app module",
                                 "expected": "app.note is present", "redFailure": note_marker,
                                 "status": "pending",
                                 "sourceRefs": [{"type": "finding", "evidenceId": intake_id,
                                                 "id": "SPEC-1"}],
                             }],
                         })))
        self.assertEqual(added.returncode, 0, marker + ": " + added.stdout + added.stderr)
        after_metadata = self.status()
        self.assertEqual(after_metadata.get("workflowId"), wid, marker)
        self.assertEqual(after_metadata.get("repoContextForge"), "passed",
                         marker + ": metadata-only correction invalidated the graph context")
        refused = WrapperPromptTests.run_advisor(self, env, *consult, "--", "pending work")
        self.assertEqual(refused.returncode, 2, "PENDING_WORK_REACHED_PROVIDER")
        self.assertIn("BM_NOTE", refused.stderr)
        self.assertFalse((Path(env["CAPTURE_DIR"]) / "count").exists(), "PENDING_WORK_REACHED_PROVIDER")

        probe = self.repo / "test_note_probe.py"
        probe.write_text(
            "import app, unittest\n"
            "class NoteProbe(unittest.TestCase):\n"
            f"    def test_note(self): self.assertTrue(hasattr(app, 'note'), {note_marker!r})\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                   "--phase", "red", "--behavior-id", "BM_NOTE", "--",
                   sys.executable, "-m", "unittest", "test_note_probe"]
        red = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\nnote = 'late attack'\n", encoding="utf-8")
        command[command.index("red")] = "green"
        green = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        reassessed = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                              str(self.json_file("late-reassess.json", {
                                  "sourceBehaviorId": "BM_NOTE",
                                  "reassessment": "no new obligation", "items": [], "dispositions": [],
                              })))
        self.assertEqual(reassessed.returncode, 0, marker + ": " + reassessed.stdout + reassessed.stderr)

        # A fixed finding's owning attack cannot be silently un-owned afterwards.
        fixed = self.review(slug, wid, self.json_file("review-fixed.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "behavioral",
                "premise": {"claim": "the note is missing", "command": "import app",
                            "result": "true before the fix; the note now exists"},
                "occurrence": {"domain": "every caller-reachable attribute read of app.note",
                               "count": 0, "complete": True,
                               "command": "python -m unittest test_note_probe",
                               "result": "count=0 after the fix"},
                "materialConsequence": {"claim": "callers cannot read the note",
                                        "command": "import app", "result": "corrected"},
                "evidence": "BM_NOTE GREEN through its recorded RED",
            }],
        }))
        self.assertEqual(fixed.returncode, 0, marker + ": " + fixed.stdout + fixed.stderr)
        omit = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                        str(self.json_file("omit-owner.json", {
                            "reassessment": "silently drop the owner",
                            "items": [],
                            "dispositions": [{"id": "BM_NOTE", "status": "superseded",
                                              "supersededBy": "BM_MAIN",
                                              "evidence": "narrowed away"}],
                        })))
        self.assertEqual(omit.returncode, 2, marker + ": " + omit.stdout + omit.stderr)
        self.assertIn("SPEC-1", omit.stderr, marker)

        # Post-edit revalidation for the production fix itself - the ordinary
        # rule for changed trees, not a metadata-only rerun.
        record_context_forge(self.repo, self.tmp)
        for extra in (("--", sys.executable, "-c", "pass"),
                      ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, marker + ": " + verified.stdout + verified.stderr)
        cleared = self.review(slug, wid, self.json_file("review-clear.json",
                                                        {"findings": [], "dispositions": []}))
        self.assertEqual(cleared.returncode, 0, marker + ": " + cleared.stdout + cleared.stderr)
        ledger = checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", [])
        [entry] = [item for item in ledger if item["intakeEvidenceId"] == intake_id]
        [owner] = entry["owners"]
        self.assertEqual(owner["id"], "BM_NOTE")
        self.assertEqual(owner["executedCommands"],
                         dict.fromkeys(("red", "green"), shlex.join(command[command.index("--") + 1:])))
        self.assertFalse(owner["revalidationRequired"])
        question = "Selected verification receipt:\n" + verified.stdout
        emitted = WrapperPromptTests.run_advisor(self, env, *consult, "--", question)
        self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
        payload = WrapperPromptTests.payload(self, env, 1)
        framed = "".join(f"finding-ledger> {line}\n" for line in json.dumps(ledger, sort_keys=True, separators=(",", ":")).splitlines())
        self.assertIn(framed, payload, "CLOSED_PAYLOAD_LOST_EVIDENCE")
        self.assertTrue(payload.endswith(question + "\n"), "CLOSED_PAYLOAD_LOST_EVIDENCE")
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stdout + completed.stderr)
        history = self.ok("history")
        begins = [event for event in history["events"] if event.get("kind") == "begin"]
        self.assertEqual(len(begins), 1, marker)


class LedgerInterruptProbe(AttackHarness):
    def test_an_interrupted_mutation_leaves_the_prior_committed_state(self) -> None:
        marker = "INTERRUPTED_MUTATION_LEAKED_PARTIAL_STATE"
        self.begin("ledger-interrupt")
        before_status = self.status()
        before_events = self.ok("history")["events"]
        from hooks.lib._workflow_db import evidence_write, mutation
        identity = resolve_repo_identity(self.repo)
        with self.assertRaises(KeyboardInterrupt, msg=marker):
            with mutation(identity) as transaction:
                poisoned = dict(transaction.state)
                poisoned["phase"] = "interrupt-poisoned"
                transaction.append(
                    poisoned, "interrupt-probe",
                    evidence=[evidence_write(str(poisoned["workflowId"]), "tdd",
                                             {"probe": "interrupt"})],
                )
                raise KeyboardInterrupt()
        self.assertEqual(self.status(), before_status, marker)
        self.assertEqual(self.ok("history")["events"], before_events, marker)


class LedgerConcurrentProbe(AttackHarness):
    def test_a_concurrent_writer_is_refused_without_interleaving(self) -> None:
        marker = "CONCURRENT_WRITE_INTERLEAVED_LEDGER"
        wid = self.begin("ledger-concurrent")
        before_events = self.ok("history")["events"]
        from hooks.lib._workflow_db import mutation
        identity = resolve_repo_identity(self.repo)
        with mutation(identity) as transaction:
            self.assertIsNotNone(transaction.state, marker)
            competing = self.cli("pause", "--slug", "ledger-concurrent", "--workflow-id", wid,
                                 "--reason", "competing writer probe")
            self.assertEqual(competing.returncode, 2, marker + ": " + competing.stdout + competing.stderr)
            self.assertIn("busy", competing.stderr.lower(), marker)
        after = self.ok("history")["events"]
        self.assertEqual(after, before_events, marker)
        self.assertNotIn("paused", self.status(), marker)


class FindingLedgerAtFinal(AttackHarness):
    def test_the_final_checkpoint_carries_each_findings_claim_and_owning_attacks(self) -> None:
        marker = "FINAL_REVIEW_BLIND_TO_FINDING_DOMAINS"
        wid = self.begin("finding-ledger")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("finding-ledger", wid, claim)
        owned = self.record_preflight("finding-ledger", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("finding-ledger", marker)
        closed = self.cli("record", "advisor-disposition", "--slug", "finding-ledger", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        payload = checkpoint_channels(self.repo, self.env, "final-review")
        ledger = payload.get("finding-ledger")
        self.assertIsInstance(ledger, list, marker)
        [entry] = [item for item in ledger if item.get("findingId") == "SPEC-1"]
        self.assertEqual(entry.get("claim"), claim, marker)
        self.assertEqual(entry.get("status"), "fixed", marker)
        self.assertEqual(entry.get("kind"), "behavioral", marker)
        [owner] = entry.get("owners") or []
        self.assertEqual((owner.get("id"), owner.get("seam"), owner.get("status")),
                         ("BM_ATTACK", "fixture app module", "green"), marker)


    def test_ledger_carries_the_dispositions_measurements(self) -> None:
        # The appeal reads the rejection's numbers from the ledger, not from a
        # hand-written summary in the consult question.
        marker = "LEDGER_DROPS_DISPOSITION_MEASUREMENTS"
        wid = self.begin("ledger-measurement")
        intake_id = self.behavioral_intake("ledger-measurement", wid, "a caller-reachable operation invalidates the checkpoint")
        owned = self.record_preflight("ledger-measurement", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("ledger-measurement", marker)
        document = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        closed = self.cli("record", "advisor-disposition", "--slug", "ledger-measurement", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input", str(document))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        recorded = json.loads(Path(document).read_text(encoding="utf-8"))["dispositions"][0]
        [entry] = [item for item in checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", []) if item.get("findingId") == "SPEC-1"]
        measurement = entry.get("measurement")
        self.assertIsNotNone(measurement, marker)
        for key in ("premise", "occurrence", "materialConsequence", "evidence"):
            self.assertEqual(measurement.get(key), recorded.get(key), marker + f" ({key})")


class LedgerCarriesAttackSemantics(AttackHarness):
    def test_ledger_owners_carry_the_attacks_behavior_and_expected_outcome(self) -> None:
        marker = "LEDGER_OWNERS_LOSE_ATTACK_SEMANTICS"
        wid = self.begin("ledger-semantics")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("ledger-semantics", wid, claim)
        owned = self.record_preflight("ledger-semantics", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        payload = checkpoint_channels(self.repo, self.env, "final-review")
        [entry] = [item for item in payload.get("finding-ledger") or [] if item.get("findingId") == "SPEC-1"]
        [owner] = entry.get("owners") or []
        self.assertEqual(owner.get("behavior"), "the reviewed value is corrected", marker)
        self.assertEqual(owner.get("expected"), "app.value is 2", marker)


class LedgerCarriesProofCommand(AttackHarness):
    def test_a_green_owner_serves_its_recorded_proof_command(self) -> None:
        marker = "LEDGER_OWNER_HIDES_EXECUTED_PROOF"
        wid = self.begin("ledger-proof")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("ledger-proof", wid, claim)
        owned = self.record_preflight("ledger-proof", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("ledger-proof", marker)
        payload = checkpoint_channels(self.repo, self.env, "final-review")
        [entry] = [item for item in payload.get("finding-ledger") or [] if item.get("findingId") == "SPEC-1"]
        [owner] = entry.get("owners") or []
        self.assertEqual(owner.get("status"), "green", marker)
        self.assertIn("unittest test_attack_probe", str(owner.get("proofCommand")), marker)


class MappedProofStaysInRepository(AttackHarness):
    def test_an_out_of_repository_proof_target_is_refused_at_cycle_open(self) -> None:
        marker = "MAPPED_PROOF_ESCAPES_REPOSITORY"
        self.open_pytest_pass("proof-scope", marker)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "outside_repo_probe.py").write_text(
            "import sys, unittest\n"
            f"sys.path.insert(0, {str(self.repo)!r})\n"
            "import app\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        env = dict(self.env, PYTHONPATH=str(outside))
        before = self.status()
        # Diagnostics stay on tail lines: quoting the nested runner's failure
        # block would make this probe's own RED unattributable to the recorder.
        refused = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", "proof-scope", "--phase", "red", "--behavior-id", "BM_ATTACK",
                                  "--", sys.executable, "-m", "unittest", "outside_repo_probe"],
                                 cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("resolve inside the repository", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")
        (self.repo / "test_inside_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        red = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                              "--slug", "proof-scope", "--phase", "red", "--behavior-id", "BM_ATTACK",
                              "--", sys.executable, "-m", "unittest", "test_inside_probe"],
                             cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class PytestOptionValueStaysOptionValue(AttackHarness):
    def test_separate_value_pytest_options_reach_the_mapped_assertion(self) -> None:
        marker = "PYTEST_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-opts", marker)
        self.write_probe(marker)
        surface = [sys.executable, "-m", "pytest", "--maxfail", "1", "--tb", "short",
                   "--durations", "10", "--color", "no",
                   "--basetemp", str(self.tmp / "pt-basetemp"), "test_probe.py"]
        red = self.mapped_tdd("pytest-opts", "red", surface)
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.mapped_tdd("pytest-opts", "green", surface)
        self.assertEqual(green.returncode, 0,
                         marker + ": " + (green.stderr.strip().splitlines() or [""])[-1])


class PyargsImportSelectionRefused(AttackHarness):
    def test_pyargs_import_selection_is_refused_at_cycle_open(self) -> None:
        marker = "PYARGS_IMPORT_ESCAPED_REPOSITORY_BOUNDARY"
        self.open_pytest_pass("pyargs-refused", marker)
        env = self.plant_external_victim(marker)
        before = self.status()
        refused = self.mapped_tdd("pyargs-refused", "red",
                                  [sys.executable, "-m", "pytest", "--pyargs", "victim"], env=env)
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("--pyargs", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


class PytestPathBoundaryStillRefused(AttackHarness):
    def test_an_out_of_repository_pytest_path_target_stays_refused(self) -> None:
        marker = "OUT_OF_REPO_TARGET_ADMITTED_TO_MAPPED_PROOF"
        self.open_pytest_pass("pytest-boundary", marker)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "test_external.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertTrue(False, {marker!r})\n",
            encoding="utf-8",
        )
        before = self.status()
        refused = self.mapped_tdd("pytest-boundary", "red",
                                  [sys.executable, "-m", "pytest", "../outside/test_external.py"])
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("resolve inside the repository", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


class PytestDebugOptionValue(AttackHarness):
    def test_the_debug_separate_value_reaches_the_mapped_assertion(self) -> None:
        marker = "DEBUG_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-debug", marker)
        self.write_probe(marker)
        debug_dir = self.tmp / "pt-debug"
        debug_dir.mkdir()
        red = self.mapped_tdd("pytest-debug", "red",
                              [sys.executable, "-m", "pytest", "--debug",
                               str(debug_dir / "pt-debug.log"), "test_probe.py"])
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class PytestConfigFileOptionValue(AttackHarness):
    def test_the_config_file_separate_value_reaches_the_mapped_assertion(self) -> None:
        marker = "CONFIG_FILE_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-config", marker)
        self.write_probe(marker)
        alt_config = self.tmp / "alt-pytest.ini"
        alt_config.write_text("[pytest]\n", encoding="utf-8")
        red = self.mapped_tdd("pytest-config", "red",
                              [sys.executable, "-m", "pytest", "--config-file",
                               str(alt_config), "test_probe.py"])
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class AddoptsPyargsNeutralized(AttackHarness):
    def test_addopts_pyargs_cannot_route_execution_outside(self) -> None:
        marker = "ADDOPTS_PYARGS_ESCAPED_REPOSITORY_BOUNDARY"
        self.open_pytest_pass("addopts-pyargs", marker)
        env = self.plant_external_victim(marker)
        injected = self.tmp / "pytest.ini"
        injected.write_text("[pytest]\naddopts = --pyargs\n", encoding="utf-8")
        before = self.status()
        for options, inherited in (([], {"PYTEST_ADDOPTS": "--pyargs"}),
                                   (["-c", str(injected)], {}), (["-o", "addopts=--pyargs"], {})):
            with self.subTest(options=options, inherited=inherited):
                run = self.mapped_tdd("addopts-pyargs", "red",
                                      [sys.executable, "-m", "pytest", *options, "victim"], env=env | inherited)
                self.assertEqual(run.returncode, 2, marker)
                after = self.status()
                retained = {"tddEvidence", "updatedAt"}
                self.assertEqual({k: v for k, v in after.items() if k not in retained},
                                 {k: v for k, v in before.items() if k not in retained}, marker)
                document = self.ok("evidence", "--full", "--evidence-id", after["tddEvidence"])["document"]
                self.assertEqual(document["status"], "pending", marker)
                self.assertIsNone(document["activeBehaviorId"], marker)
                self.assertEqual([item["status"] for item in document["behaviorMap"]], ["pending"], marker)
                attempt = document["runs"][-1]
                self.assertFalse(attempt["valid"], marker)
                self.assertNotIn("redProof", attempt, marker)
                self.assertIn("redProofFailure", attempt, marker)


class BulkRejectionAdvisorTests(AttackHarness):
    """Issue #186 part 3: bulk material rejections through the advisor caller."""

    def material_intake(self, slug: str, wid: str, count: int, *, material: int | None = None) -> str:
        material = count if material is None else material
        envelope = self.json_file("envelope.json", {"schemaVersion": 1, "findings": [
            {"id": f"SPEC-{i}", "claim": f"claimed defect {i}", "material": i <= material,
             "kind": "nonbehavioral"}
            for i in range(1, count + 1)
        ], "verdict": "completed"})
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                           "--stage", "preflight", "--source", "codex-advisor",
                           "--input", str(envelope))
        return str(self.status()["advisorPreflight"]["intakeEvidence"])

    def rejection_doc(self, wid: str, intake_id: str, count: int, *, valid: bool = True,
                      rejected: int | None = None) -> Path:
        rejected = count if rejected is None else rejected
        premise_result = "false" if valid else "the premise held on inspection"
        return self.json_file("rejections.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": f"SPEC-{i}",
                "status": "rejected-with-evidence" if i <= rejected else "report-only",
                "kind": "nonbehavioral",
                "premise": {"claim": f"claimed defect {i}", "command": "inspect app.py",
                            "result": premise_result},
                "occurrence": {"domain": "the complete fixture repository", "count": 0 if valid else 2,
                               "complete": valid, "command": "inspect app.py", "result": "measured"},
                "materialConsequence": {"claim": "the fixture is affected", "command": "inspect app.py",
                                        "result": "measured" if i <= rejected else "false"},
                "evidence": "measured rejection evidence",
            } for i in range(1, count + 1)],
        })

    def reject(self, slug: str, wid: str, count: int, *, valid: bool = True,
               material: int | None = None, rejected: int | None = None) -> subprocess.CompletedProcess[str]:
        intake_id = self.material_intake(slug, wid, count, material=material)
        return self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                        "--stage", "preflight", "--findings", "addressed",
                        "--input", str(self.rejection_doc(wid, intake_id, count, valid=valid,
                                                          rejected=rejected)))

    def test_three_material_rejections_warn_on_the_advisor_caller(self) -> None:
        marker = "BULK_REJECTION_UNFLAGGED_ADVISOR"
        wid = self.begin("bulk-advisor")
        result = self.reject("bulk-advisor", wid, 3)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)
        self.assertIn("3", result.stderr, marker)
        states = json.loads(self.cli("status").stdout).get("findingStates", [])
        self.assertEqual([s["status"] for s in states], ["rejected-with-evidence"] * 3, marker)

    def test_two_rejections_stay_silent_on_the_advisor_caller(self) -> None:
        marker = "SMALL_DOC_FALSELY_FLAGGED"
        wid = self.begin("small-advisor")
        result = self.reject("small-advisor", wid, 2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_rejections_with_two_material_stay_silent_on_the_advisor_caller(self) -> None:
        # The warning counts MATERIAL rejections, not total rejections.
        marker = "IMMATERIAL_REJECTIONS_MISCOUNTED"
        wid = self.begin("filter-material")
        result = self.reject("filter-material", wid, 3, material=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_material_with_two_rejected_stay_silent_on_the_advisor_caller(self) -> None:
        # The warning counts REJECTIONS, not every material disposition.
        marker = "NONREJECTION_DISPOSITIONS_MISCOUNTED"
        wid = self.begin("filter-status")
        result = self.reject("filter-status", wid, 3, rejected=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_an_unmeasured_rejection_still_refuses_on_the_advisor_caller(self) -> None:
        marker = "REJECTION_SHAPE_ENFORCEMENT_LOST"
        wid = self.begin("shape-advisor")
        intake_id = self.material_intake("shape-advisor", wid, 1)
        before = self.status()
        result = self.cli("record", "advisor-disposition", "--slug", "shape-advisor", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed",
                          "--input", str(self.rejection_doc(wid, intake_id, 1, valid=False)))
        self.assertNotEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("false premise or zero occurrence", result.stdout + result.stderr, marker)
        self.assertEqual(self.status(), before, marker + ": a refused document mutated finding state")


class MapCorrectionAttacks(AttackHarness):
    """Issue #189: a lead's own mistaken map entry is correctable inside the pass.

    ARM X6R8 restarted one candidate twice because a post-preflight contract item
    it added by mistake could not be withdrawn, and a finding-owned preservation
    item it omitted by mistake could not be reopened. Every attack drives the
    real workflow CLI over a fixture ledger."""

    EXTRA: dict[str, object] = {
        "id": "BM_EXTRA", "kind": "contract", "basis": "added after preflight",
        "behavior": "an obligation the lead added in error", "seam": "fixture app module",
        "expected": "never attacked", "redFailure": "EXTRA_NEVER_ATTACKED", "status": "pending",
    }
    KEEP_OMITTED: dict[str, object] = {
        "id": "BM_KEEP", "kind": "preservation", "basis": "governing evidence",
        "behavior": "an existing guarantee", "seam": "fixture app module",
        "expected": "kept", "redFailure": "KEEP_REGRESSED", "status": "omitted",
        "evidence": "out of scope by governing evidence",
    }

    def contract(self, marker: str, refs: list[dict[str, str]] | None = None) -> dict[str, object]:
        return {
            "id": "BM_ATTACK", "kind": "contract", "basis": "requested behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": refs or [],
        }

    def open_pass(self, slug: str, behavior_map: list[dict[str, object]]) -> str:
        wid = self.begin(slug)
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        recorded = self.record_preflight(slug, wid, behavior_map)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return wid

    def map_update(self, slug: str, **document: object) -> subprocess.CompletedProcess[str]:
        wid = str(self.status()["workflowId"])
        update = self.json_file("map.json", {"reassessment": "map correction", **document})
        return self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))

    def withdraw(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "withdrawn", "evidence": "added in error"}])

    def reopen(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "pending", "evidence": "omitted in error"}])

    def map_items(self) -> dict[str, dict[str, object]]:
        state = self.status()
        evidence_id = state.get("tddEvidence") or state.get("preflightEvidence")
        document = self.ok("evidence", "--full", "--evidence-id", str(evidence_id))
        items = document.get("behaviorMap")
        if items is None:
            items = (document.get("document") or {}).get("behaviorMap")
        return {str(entry["id"]): entry for entry in items}

    def wrong_occurrence(self, marker: str, *, finding: bool = False,
                         neighbors: bool = False) -> tuple[str, str, list[str], list[str]]:
        """Replay issue 35's captured input against the real recorder."""
        slug = "red-correction"
        refs = []
        if finding:
            wid = self.begin(slug)
            intake = self.behavioral_intake(slug, wid, "stream estimator follows its HTTP call")
            refs = [{"type": "finding", "evidenceId": intake, "id": "SPEC-1"}]
        item = {**self.contract("ESTIMATE_OVERLAP", refs),
                "behavior": "stream constructs estimator before its own HTTP call",
                "seam": "order.txt streaming function", "expected": "estimator precedes streaming call"}
        items = [item, self.EXTRA] if neighbors else [item]
        if finding:
            recorded = self.record_preflight(slug, wid, items)
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        else:
            wid = self.open_pass(slug, items)
        (self.repo / "order.txt").write_text(
            "func other\nhttpClient.Do(other)\nfunc stream\n"
            "httpClient.Do(stream)\nestimator := newEstimator()\n", encoding="utf-8")
        common = "from pathlib import Path; text=Path('order.txt').read_text(); estimator=text.index('estimator :='); "
        wrong = [sys.executable, "-c", common + "call=text.index('httpClient.Do'); assert estimator < call, 'ESTIMATE_OVERLAP'"]
        correct = [sys.executable, "-c", common + "call=text.index('httpClient.Do', text.index('func stream')); assert estimator < call, 'ESTIMATE_OVERLAP'"]
        red = self.mapped_tdd(slug, "red", wrong)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        return slug, wid, wrong, correct

    def correct_red(self, slug: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[{
            "id": "BM_ATTACK", "status": "pending",
            "evidence": "Select the streaming call; the ordering contract is unchanged."}])

    def repair_order(self) -> None:
        fixture = self.repo / "order.txt"
        fixture.write_text(fixture.read_text().replace(
            "httpClient.Do(stream)\nestimator := newEstimator()",
            "estimator := newEstimator()\nhttpClient.Do(stream)"), encoding="utf-8")

    def test_red_command_correction_completes_same_workflow(self) -> None:
        marker = "RED_COMMAND_CORRECTION_FAILED"
        slug, wid, wrong, correct = self.wrong_occurrence(marker)
        correction = self.correct_red(slug)
        self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
        self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "pending", marker)
        document = self.ok("evidence", "--full", "--evidence-id", self.status()["tddEvidence"])["document"]
        self.assertIsNone(document["activeBehaviorId"], marker)
        for field in ("command", "surface", "runs"):
            self.assertNotIn(field, document, marker)
        refused = self.refused_unchanged(marker, lambda: self.cli("complete"))
        self.assertIn("BM_ATTACK", refused.stderr, marker)
        red = self.mapped_tdd(slug, "red", correct)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stderr)
        self.repair_order()
        wrong_result = subprocess.run(wrong, cwd=self.repo, text=True, capture_output=True)
        self.assertEqual(wrong_result.returncode, 1, marker)
        green = self.mapped_tdd(slug, "green", correct)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stderr)
        self.assertEqual(list(self.map_items()), ["BM_ATTACK"], marker)
        self.assertEqual(self.status()["workflowId"], wid, marker)
        history = self.ok("history")["events"]
        self.assertEqual(sum(e["kind"] == "begin" for e in history), 1, marker)
        recovery = [e["kind"] for e in history if e["kind"].startswith("tdd-")]
        self.assertEqual(recovery, ["tdd-in-progress", "tdd-annotated", "tdd-in-progress", "tdd-passed"], marker)
        self.assertEqual(self.status()["tddCycleCount"], 2, marker)
        self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "green", marker)
        # Supply normal completion inputs through the recorder, not a provider
        # substitute. This tests completion semantics, not advisor reasoning.
        record_context_forge(self.repo, self.tmp)
        self.ok("verify", "--slug", slug, "--", *correct)
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.ok("record", "review", "--slug", slug, "--workflow-id", wid,
                "--review-context-id", "correction-fixture",
                "--input", str(self.json_file("review.json", {"findings": []})))
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "final", "--source", "codex-advisor", "--input",
                str(self.json_file("final.json", {"schemaVersion": 1, "findings": [], "verdict": "commit-ready"})))
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stderr)
        print(f"recovery target={WORKFLOW} scale=1 limits=correction:1,corrected_RED:1,corrected_GREEN:1 "
              f"observed={recovery.count('tdd-annotated')},{recovery.count('tdd-in-progress') - 1},"
              f"{recovery.count('tdd-passed')} extra_workflows=0 duplicate_items=0 unrelated_proof_reruns=0")

    def test_corrected_item_requires_genuine_matching_red(self) -> None:
        marker = "CORRECTED_PROOF_RULES_BROKEN"
        slug, _, wrong, correct = self.wrong_occurrence(marker)
        self.refused_unchanged(marker, lambda: self.mapped_tdd(slug, "red", correct))
        correction = self.correct_red(slug)
        self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
        self.refused_unchanged(marker, lambda: self.mapped_tdd(slug, "green", correct))
        (self.repo / "test_setup.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def setUp(self): raise RuntimeError('ESTIMATE_OVERLAP')\n"
            "    def test_order(self): self.assertTrue(True)\n", encoding="utf-8")
        for command in ([sys.executable, "-c", "pass"],
                        [sys.executable, "missing.py"],
                        [sys.executable, "-c", "raise ImportError('ESTIMATE_OVERLAP')"],
                        [sys.executable, "-m", "unittest", "test_setup"]):
            with self.subTest(command=command):
                refused = self.mapped_tdd(slug, "red", command)
                self.assertEqual(refused.returncode, 2, marker + ": " + refused.stderr)
                self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "pending", marker)
                self.assertNotIn("redCommand", self.map_items()["BM_ATTACK"], marker)
        red = self.mapped_tdd(slug, "red", correct)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stderr)
        self.refused_unchanged(marker, lambda: self.mapped_tdd(slug, "green", wrong))
        self.repair_order()
        green = self.mapped_tdd(slug, "green", correct)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stderr)
        reopened = self.correct_red(slug)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "pending")
        self.refused_unchanged(marker, lambda: self.mapped_tdd(slug, "green", correct))

    def test_corrected_red_cannot_be_withdrawn(self) -> None:
        marker = "CORRECTED_RED_WITHDRAWN"
        slug, _, _, _ = self.wrong_occurrence(marker)
        original = self.map_items()["BM_ATTACK"]["redProof"]
        correction = self.correct_red(slug)
        self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
        self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[{
            "id": "BM_ATTACK", "status": "withdrawn", "evidence": "Skip corrected proof"}]))
        self.assertEqual(self.map_items()["BM_ATTACK"]["redProof"], original, marker)
        self.assertNotIn("redCommand", self.map_items()["BM_ATTACK"], marker)
        self.repair_order()
        (self.repo / "test_order.py").write_text(
            "import unittest\nfrom pathlib import Path\nclass T(unittest.TestCase):\n"
            "    def test_order(self):\n"
            "        text = Path('order.txt').read_text()\n"
            "        self.assertLess(text.index('estimator :='), "
            "text.index('httpClient.Do', text.index('func stream')), 'ESTIMATE_OVERLAP')\n",
            encoding="utf-8")
        # The repair landed before this run: a contract item cannot be backdated to
        # a baseline from the candidate-only pass; its corrected RED stays owed.
        late = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "test_order"])
        self.assertEqual(late.returncode, 2, marker + ": " + late.stdout + late.stderr)
        self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "pending", marker)
        self.assertEqual(self.map_items()["BM_ATTACK"]["redProof"], original, marker)

    def test_red_correction_preserves_other_open_and_proved_items(self) -> None:
        marker = "CORRECTION_CHANGED_NEIGHBOR"
        for proved in (False, True):
            with self.subTest(proved=proved):
                slug, _, _, correct = self.wrong_occurrence(marker, neighbors=True)
                other = [sys.executable, "-c", "import app; assert app.value == 2, 'EXTRA_NEVER_ATTACKED'"]
                (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
                self.ok("tdd", "--slug", slug, "--phase", "red", "--behavior-id", "BM_EXTRA", "--", *other)
                if proved:
                    (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
                    self.ok("tdd", "--slug", slug, "--phase", "green", "--behavior-id", "BM_EXTRA", "--", *other)
                before, neighbor = self.status(), self.map_items()["BM_EXTRA"]
                old = self.ok("evidence", "--full", "--evidence-id", before["tddEvidence"])["document"]
                correction = self.correct_red(slug)
                self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
                after = self.status()
                current = self.ok("evidence", "--full", "--evidence-id", after["tddEvidence"])["document"]
                for key in ("kind", "command", "surface", "behaviorId", "activeBehaviorId", "runs"):
                    self.assertEqual(current[key], old[key], marker)
                for key in before.keys() - {"tddEvidence", "updatedAt", "nextAction", "mapSelections"}:
                    self.assertEqual(after[key], before[key], marker + ": " + key)
                self.assertEqual(after["mapSelections"],
                                 {"BM_EXTRA": before["mapSelections"]["BM_EXTRA"]}, marker)
                self.assertEqual(self.map_items()["BM_EXTRA"], neighbor, marker)
                self.assertEqual(self.mapped_tdd(slug, "red", correct).returncode, 0, marker)
                self.repair_order()
                self.assertEqual(self.mapped_tdd(slug, "green", correct).returncode, 0, marker)
                if not proved:
                    (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
                    self.ok("tdd", "--slug", slug, "--phase", "green", "--behavior-id", "BM_EXTRA", "--", *other)

    def test_red_correction_keeps_history_contract_and_finding_refs(self) -> None:
        marker = "CORRECTION_LOST_HISTORY"
        slug, wid, _, correct = self.wrong_occurrence(marker, finding=True)
        before = self.status()
        old_id = before["tddEvidence"]
        historical = self.ok_text("evidence", "--full", "--evidence-id", old_id)
        contract = self.map_items()["BM_ATTACK"]
        correction = self.correct_red(slug)
        self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
        expected = {key: value for key, value in contract.items() if key != "redCommand"}
        expected["status"] = "pending"
        self.assertEqual(self.map_items()["BM_ATTACK"], expected, marker)
        current_finding = self.status()["findingStates"][0]
        self.assertEqual({k: current_finding[k] for k in before["findingStates"][0]}, before["findingStates"][0], marker)
        self.assertEqual(current_finding["mechanismEvidence"]["evidenceId"], self.status()["tddEvidence"], marker)
        self.assertEqual(self.mapped_tdd(slug, "red", correct).returncode, 0, marker)
        self.repair_order()
        self.assertEqual(self.mapped_tdd(slug, "green", correct).returncode, 0, marker)
        final = self.map_items()["BM_ATTACK"]
        # The corrected RED records its own observation; everything else is retained.
        for key in expected.keys() - {"status", "redProof"}:
            self.assertEqual(final[key], expected[key], marker)
        self.assertEqual(self.ok_text("evidence", "--full", "--evidence-id", old_id), historical, marker)
        self.assertEqual(self.status()["workflowId"], wid, marker)

    def test_red_correction_is_atomic_against_invalid_input_and_inflight_run(self) -> None:
        marker = "CORRECTION_ATOMICITY_BROKEN"
        slug, wid, _, correct = self.wrong_occurrence(marker)
        valid = {"id": "BM_ATTACK", "status": "pending", "evidence": "wrong occurrence"}
        for dispositions in ([{**valid, "evidence": ""}], [valid, valid],
                             [valid, {**valid, "id": "BM_MISSING"}],
                             [{**valid, "revalidate": True}], [{**valid, "supersededBy": "BM_ATTACK"}],
                             [{**valid, "sourceRefs": [{"type": "finding", "evidenceId": "foreign", "id": "SPEC-1"}]}]):
            self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=dispositions))
        correction = self.correct_red(slug)
        self.assertEqual(correction.returncode, 0, marker + ": " + correction.stderr)
        # During an old command's genuine RED rerun, a second CLI process
        # corrects it. The first process must not overwrite the correction.
        request = self.json_file("correction.json", {"reassessment": "correct scope", "dispositions": [valid]})
        probe = self.repo / "inflight.py"
        probe.write_text(
            "import subprocess, sys\nfrom pathlib import Path\n"
            "if Path('release').exists():\n"
            f"    result = subprocess.run({[sys.executable, str(WORKFLOW), 'record', 'tdd-map', '--repo', str(self.repo), '--slug', slug, '--workflow-id', wid, '--input', str(request)]!r})\n"
            "    if result.returncode: sys.exit(result.returncode)\n"
            "raise AssertionError('ESTIMATE_OVERLAP')\n", encoding="utf-8")
        inflight = [sys.executable, "inflight.py"]
        self.assertEqual(self.mapped_tdd(slug, "red", inflight).returncode, 0, marker)
        (self.repo / "release").touch()
        stale = self.mapped_tdd(slug, "red", inflight)
        self.assertEqual(stale.returncode, 2, marker + ": " + stale.stderr)
        self.assertIn("TDD evidence changed during the run", stale.stderr, marker)
        self.assertEqual(self.map_items()["BM_ATTACK"]["status"], "pending", marker)
        self.assertNotIn("redCommand", self.map_items()["BM_ATTACK"], marker)
        self.assertEqual(self.mapped_tdd(slug, "red", correct).returncode, 0, marker)

    def test_withdrawn_items_cannot_acquire_references_even_in_mixed_updates(self) -> None:
        from hooks.lib import behavior_map

        slug = "withdrawn-reference"
        wid = self.open_pass(slug, [self.contract("VALUE_NOT_TWO"), self.EXTRA, self.KEEP_OMITTED])
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0)
        intake = self.behavioral_intake(slug, wid, "another value guarantee")
        for kind in ("finding", "design"):
            ref = {"type": kind, "evidenceId": intake, "id": "SPEC-1"}
            update = {"id": "BM_EXTRA", "sourceRefs": [ref]}
            items = list(self.map_items().values())
            original = behavior_map.clone(items)
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "withdrawn"):
                    behavior_map.apply_dispositions(items, [update])
                self.assertEqual(items, original, "refusal must precede ownership mutation")
                self.refused_unchanged("WITHDRAWN_REFERENCE_ACCEPTED", lambda: self.map_update(
                    slug, dispositions=[{"id": "BM_KEEP", "revalidate": True,
                                         "evidence": "affected guarantee"}, update]))
        # A flag is idempotent, but legitimate new ownership must still union.
        self.assertEqual(self.map_update(slug, dispositions=[{
            "id": "BM_KEEP", "revalidate": True, "evidence": "affected guarantee",
        }]).returncode, 0)
        update = {"id": "BM_KEEP", "revalidate": True, "evidence": "same guarantee",
                  "sourceRefs": [{"type": "finding", "evidenceId": intake, "id": "SPEC-1"}]}
        self.assertEqual(self.map_update(slug, dispositions=[update]).returncode, 0)
        self.assertEqual(self.map_items()["BM_KEEP"]["sourceRefs"], update["sourceRefs"])
        before, history = self.status(), self.ok_text("history")
        self.assertEqual(self.map_update(slug, dispositions=[update]).returncode, 0)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ok_text("history"), history)

    def test_skipped_recheck_preserves_receipts_but_product_regression_invalidates(self) -> None:
        from hooks.tests.test_workflow_hooks import HookHarness

        for runner in ("unittest", "pytest"):
            slug = "recheck-" + runner
            keep = {**self.contract("VALUE_NOT_TWO"), "kind": "preservation", "id": "BM_KEEP"}
            self.open_pass(slug, [self.contract("VALUE_NOT_TWO"), keep])
            self.drive_attack_green(slug, "VALUE_NOT_TWO")
            (self.repo / "test_keep_probe.py").write_text(
                "import app, os, unittest\nclass T(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        if os.environ.get('RECHECK_MODE') == 'skip': self.skipTest('unavailable')\n"
                "        self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n"
                if runner == "unittest" else
                "import app, os, pytest, warnings\n"
                "if os.environ.get('RECHECK_MODE') == 'skip': pytest.skip('unavailable', allow_module_level=True)\n"
                "def test_value(): assert app.value == 2, 'VALUE_NOT_TWO'\n"
                "if os.environ.get('RECHECK_MODE') == 'warning':\n"
                "    test_value.__test__ = False\n"
                "    warnings.warn('no runnable test in this environment')\n",
                encoding="utf-8")
            def execute(phase):
                return self.cli("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_KEEP",
                                "--", sys.executable, "-m", runner,
                                "test_keep_probe" if runner == "unittest" else "test_keep_probe.py")
            for phase, value in (("red", 1), ("green", 2)):
                (self.repo / "app.py").write_text(
                    f"import os\nvalue = int(os.environ.get('RECHECK_VALUE', '{value}'))\n", encoding="utf-8")
                result = execute(phase)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            HookHarness.run_verification(self, slug)
            self.ok("set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none")
            before = self.status()
            identity = resolve_repo_identity(self.repo)
            candidate = _active_candidate_tree(identity)
            self.assertEqual(self.map_update(slug, dispositions=[{
                "id": "BM_KEEP", "revalidate": True, "evidence": "affected reader",
            }]).returncode, 0)
            self.assertEqual(execute("green").returncode, 0)
            rechecked = self.status()
            document = self.ok("evidence", "--full", "--evidence-id", str(rechecked["tddEvidence"]))["document"]
            self.assertEqual(document["status"], "passed", "RECHECK_LEFT_STALE_MAP_STATUS")
            for field in ("verificationEvidence", "qualityGateEvidence", "codeReview", "tddCycleCount"):
                self.assertEqual(rechecked.get(field), before.get(field))
            self.assertEqual(self.map_update(slug, dispositions=[{
                "id": "BM_KEEP", "revalidate": True, "evidence": "exercise unavailable or contrary results",
            }]).returncode, 0)
            flagged = self.status()
            historical_id = str(flagged["tddEvidence"])
            historical = self.ok("evidence", "--full", "--evidence-id", historical_id)
            self.assertEqual(historical["document"]["status"], "pending")
            for mode in (("skip", "regression") if runner == "unittest" else ("warning", "skip", "regression")):
                self.env["RECHECK_MODE"] = mode
                if mode == "regression":
                    self.env["RECHECK_VALUE"] = "3"  # Same source, actual public reader now returns the wrong value.
                result = execute("green")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                after = self.status()
                document = self.ok("evidence", "--full", "--evidence-id", str(after["tddEvidence"]))["document"]
                run = document["runs"][-1]
                self.assertFalse(run["valid"])
                self.assertEqual(run["candidateTree"], candidate)
                self.assertEqual(run["behaviorId"], "BM_KEEP")
                self.assertTrue(self.map_items()["BM_KEEP"]["revalidationRequired"])
                self.assertEqual(after["tddCycleCount"], before["tddCycleCount"])
                if mode != "regression":
                    ignored = {"tddEvidence", "updatedAt"}
                    self.assertEqual({k: v for k, v in after.items() if k not in ignored},
                                     {k: v for k, v in flagged.items() if k not in ignored})
                else:
                    self.assertEqual(after["verification"], "pending", "REGRESSION_REUSED_VERIFICATION")
                    self.assertEqual(after["tdd"], "in-progress")
                    self.assertEqual(after["codeReview"]["status"], "pending")
                    self.assertIsNone(after.get("qualityGateEvidence"))
            self.env.pop("RECHECK_MODE")
            self.env.pop("RECHECK_VALUE")
            self.assertEqual(execute("green").returncode, 0)
            self.assertEqual(self.status()["tdd"], "passed")
            self.assertEqual(self.status()["verification"], "pending")
            self.assertNotIn("revalidationRequired", self.map_items()["BM_KEEP"])
            self.assertEqual(self.ok("evidence", "--full", "--evidence-id", historical_id), historical)
            self.assertEqual(_active_candidate_tree(identity), candidate)

    def test_annotation_keeps_its_admission_without_waiving_transition_prerequisites(self) -> None:
        from hooks.lib.workflow_state import (
            WorkflowError, annotate_tdd_evidence, commit_tdd, pause,
        )

        slug = "annotation-admission"
        wid = self.begin(slug)  # No preflight: transition must refuse, annotation may record.
        identity = resolve_repo_identity(self.repo)
        pause(identity, slug, wid, "await preflight")
        document = {"schemaVersion": 1, "workflowId": wid,
                    "behaviorMap": [self.contract("VALUE_NOT_TWO")], "runs": []}
        before = self.status()
        with self.assertRaises(WorkflowError):
            commit_tdd(identity, slug, wid, document, "in-progress")
        self.assertEqual(self.status(), before)
        _, eid = annotate_tdd_evidence(identity, slug, wid, document)
        after = self.status()
        ignored = {"tddEvidence", "updatedAt", "nextAction"}
        self.assertEqual({k: v for k, v in after.items() if k not in ignored},
                         {k: v for k, v in before.items() if k not in ignored})
        self.assertEqual(after["nextAction"], "preflight")
        history = self.ok_text("history")
        for supplied_wid, expected in (("stale-instance", eid), (wid, None)):
            with self.subTest(workflow=supplied_wid, evidence=expected):
                with self.assertRaises(WorkflowError):
                    annotate_tdd_evidence(identity, slug, supplied_wid, document,
                                          expected_evidence_id=expected)
                self.assertEqual(self.status(), after)
                self.assertEqual(self.ok_text("history"), history)
        self.refused_unchanged("ANNOTATION_ADMITTED_EXECUTION", lambda: self.tdd(
            slug, "red", "BM_ATTACK", "test_nonexistent"))

    def test_retired_map_state_is_refused_without_rewriting_history(self) -> None:
        from hooks.lib import behavior_map
        from hooks.lib.workflow_state import WorkflowError, annotate_tdd_evidence

        slug = "retired-map-state"
        wid = self.open_pass(slug, [self.contract("VALUE_NOT_TWO")])
        self.drive_attack_green(slug, "VALUE_NOT_TWO")
        before, history = self.status(), self.ok_text("history")
        eid = str(before["tddEvidence"])
        original = self.ok("evidence", "--full", "--evidence-id", eid)["document"]
        document = behavior_map.clone([original])[0]
        document["behaviorMap"][0]["status"] = "post-edit-passed"
        with self.assertRaises((ValueError, WorkflowError)):
            annotate_tdd_evidence(resolve_repo_identity(self.repo), slug, wid, document,
                                  expected_evidence_id=eid)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ok_text("history"), history)
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", eid)["document"], original)

    def test_reassessment_preserves_interleaved_cycles_and_reference_identity(self) -> None:
        slug, marker = "interleaved-reassessment", "VALUE_NOT_TWO"
        foreign_wid = self.begin("foreign-owner")
        foreign = self.behavioral_intake("foreign-owner", foreign_wid, "foreign claim")
        wid = self.begin(slug)
        first = self.behavioral_intake(slug, wid, "first claim")
        second = self.behavioral_intake(slug, wid, "second independent claim", "SPEC-2")
        refs = [{"type": "finding", "evidenceId": value, "id": identifier}
                for value, identifier in ((first, "SPEC-1"), (second, "SPEC-2"))]
        keep = {**self.KEEP_OMITTED, "status": "already-satisfied", "evidence": "initial evidence"}
        pending = {key: value for key, value in self.KEEP_OMITTED.items() if key != "evidence"}
        pending.update(status="pending", expected="keep.value is 2")
        items = [self.contract(marker, refs), keep,
                 {**pending, "id": "BM_DIRECT"}, {**pending, "id": "BM_RUNNER"}]
        self.refused_unchanged("AUTHORED_REVALIDATION_ACCEPTED", lambda: self.record_preflight(
            slug, wid, [items[0], {**keep, "revalidationRequired": True}, *items[2:]]))
        recorded = self.record_preflight(slug, wid, items)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "keep.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo / "drift.py").write_text("value = 0\n", encoding="utf-8")
        (self.repo / "direct_probe.py").write_text(
            "import keep, os\nfrom pathlib import Path\n"
            "assert keep.value == 2, 'KEEP_REGRESSED'\n"
            "if os.environ.get('REASSESS_DRIFT'): Path('drift.py').write_text('value = 1\\n')\n",
            encoding="utf-8")
        (self.repo / "test_runner_probe.py").write_text(
            "import keep, unittest\nclass T(unittest.TestCase):\n"
            "    def test_keep(self): self.assertEqual(keep.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        self.keep_probe(1)
        self.write_probe(marker)
        commands = {
            "BM_DIRECT": [sys.executable, "direct_probe.py"],
            "BM_RUNNER": [sys.executable, "-m", "unittest", "test_runner_probe"],
            "BM_KEEP": [sys.executable, "-m", "unittest", "test_keep_probe"],
            "BM_ATTACK": [sys.executable, "-m", "unittest", "test_probe"],
        }

        def document():
            return self.ok("evidence", "--full", "--evidence-id", str(self.status()["tddEvidence"]))["document"]

        def run(phase, identifier, expected=0, command=None):
            result = self.cli("tdd", "--slug", slug, "--phase", phase,
                              "--behavior-id", identifier, "--", *(command or commands[identifier]))
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            return document()

        for identifier in ("BM_DIRECT", "BM_RUNNER"):
            run("red", identifier)
        (self.repo / "keep.py").write_text("value = 2\n", encoding="utf-8")
        for identifier in ("BM_DIRECT", "BM_RUNNER"):
            run("green", identifier)
        active = run("red", "BM_ATTACK")
        historical_id = str(self.status()["tddEvidence"])
        historical = document()
        cycle_count = self.status()["tddCycleCount"]
        binding = {key: active[key] for key in ("command", "surface", "activeBehaviorId", "behaviorId")}

        def still_bound():
            current = document()
            self.assertEqual({key: current[key] for key in binding}, binding)
            self.assertEqual(self.status()["tddCycleCount"], cycle_count)
            self.assertEqual(current["runs"][:len(active["runs"])], active["runs"])
            return current

        self.refused_unchanged("ADDED_REVALIDATION_ACCEPTED", lambda: self.map_update(slug, items=[
            {**pending, "id": "BM_FORGED", "revalidationRequired": True}]))
        self.refused_unchanged("REVALIDATE_AND_STATUS_ACCEPTED", lambda: self.map_update(slug, dispositions=[{
            "id": "BM_KEEP", "revalidate": True, "status": "pending", "evidence": "ambiguous request"}]))
        before = self.status()
        changed = self.map_update(slug, dispositions=[{"id": "BM_KEEP", "sourceRefs": refs}])
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        after = self.status()
        self.assertEqual({key: value for key, value in before.items() if key not in {"tddEvidence", "updatedAt", "findingStates"}},
                         {key: value for key, value in after.items() if key not in {"tddEvidence", "updatedAt", "findingStates"}})
        for prior, current in zip(before["findingStates"], after["findingStates"]):
            self.assertEqual({key: current[key] for key in prior}, prior)
        self.assertEqual(self.map_items()["BM_KEEP"]["sourceRefs"], refs)
        still_bound()
        events = self.ok_text("history")
        repeated = self.map_update(slug, dispositions=[{"id": "BM_KEEP", "sourceRefs": refs}])
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(self.status(), after)
        self.assertEqual(self.ok_text("history"), events)
        self.refused_unchanged("FOREIGN_REFERENCE_ACCEPTED", lambda: self.map_update(slug, dispositions=[
            {"id": "BM_DIRECT", "sourceRefs": refs},
            {"id": "BM_KEEP", "sourceRefs": [{"type": "finding", "evidenceId": foreign, "id": "SPEC-1"}]},
        ]))
        changed = self.map_update(slug, dispositions=[
            {"id": identifier, "revalidate": True, "evidence": "shared decision changed", "sourceRefs": refs}
            for identifier in ("BM_KEEP", "BM_DIRECT", "BM_RUNNER")])
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        still_bound()
        flagged = self.map_items()
        self.assertTrue(all(flagged[key]["revalidationRequired"] for key in commands if key != "BM_ATTACK"))
        self.assertEqual(flagged["BM_DIRECT"]["status"], "green")
        self.assertEqual(flagged["BM_DIRECT"]["redCommand"], historical["behaviorMap"][2]["redCommand"])
        self.assertNotIn("evidence", flagged["BM_KEEP"])
        self.refused_unchanged("PROSE_REVALIDATION_ACCEPTED", lambda: self.map_update(slug, dispositions=[
            {"id": "BM_KEEP", "status": "already-satisfied", "evidence": "old prose"}]))
        # A refused direct pending baseline, a passing runner baseline, and failed
        # or drifted GREEN rechecks all leave A's original RED usable.
        run("red", "BM_KEEP", 2, [sys.executable, "-c", "pass"])
        still_bound()
        run("red", "BM_KEEP")
        still_bound()
        self.assertNotIn("revalidationRequired", self.map_items()["BM_KEEP"])
        (self.repo / "keep.py").write_text("value = 1\n", encoding="utf-8")
        run("green", "BM_DIRECT", 2)
        run("green", "BM_RUNNER", 2)
        still_bound()
        (self.repo / "keep.py").write_text("value = 2\n", encoding="utf-8")
        self.env["REASSESS_DRIFT"] = "1"
        drifted = run("green", "BM_DIRECT", 2)
        self.env.pop("REASSESS_DRIFT")
        self.assertFalse(drifted["runs"][-1]["valid"])
        self.assertIn("drift.py", drifted["runs"][-1]["bindingError"])
        self.assertTrue(drifted["runs"][-1]["candidateTree"])
        self.assertTrue(self.map_items()["BM_DIRECT"]["revalidationRequired"])
        still_bound()
        for identifier in ("BM_DIRECT", "BM_RUNNER"):
            run("green", identifier)
            self.assertNotIn("revalidationRequired", self.map_items()[identifier])
        still_bound()
        (self.repo / "sentinel_probe.py").write_text(
            "from pathlib import Path\nPath('executed-sentinel').touch()\n", encoding="utf-8")
        for phase in ("red", "green"):
            self.refused_unchanged("CHANGED_BOUND_COMMAND_EXECUTED", lambda: self.cli(
                "tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                "--", sys.executable, "sentinel_probe.py"))
        self.assertFalse((self.repo / "executed-sentinel").exists())
        run("red", "BM_ATTACK")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        run("green", "BM_ATTACK")
        self.assertEqual(self.status()["tdd"], "passed")
        self.assertEqual(self.status()["tddCycleCount"], cycle_count)
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", historical_id)["document"], historical)
        self.ok("verify", "--slug", slug, "--", sys.executable, "-c", "print('verified')")
        before = self.status()
        self.assertEqual(self.map_update(slug, dispositions=[
            {"id": "BM_ATTACK", "sourceRefs": refs}]).returncode, 0)
        self.assertEqual(self.status(), before, "NO_OP_RESET_VERIFICATION")

        # Reopening a settled guarantee can expose a real defect. Its RED must
        # open a cycle and the same GREEN must close it, unlike a historical
        # GREEN recheck which only refreshes existing evidence.
        changed = self.map_update(slug, dispositions=[{
            "id": "BM_KEEP", "status": "pending", "evidence": "public reader changed"}])
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        run("red", "BM_KEEP")  # app.value is now 2; the retained operation expects 1.
        self.assertEqual(self.status()["tdd"], "in-progress")
        self.assertTrue(self.map_items()["BM_KEEP"]["revalidationRequired"])
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        run("green", "BM_KEEP")
        self.assertEqual(self.status()["tdd"], "passed")
        self.assertEqual(self.status()["tddCycleCount"], cycle_count + 1)
        self.assertNotIn("revalidationRequired", self.map_items()["BM_KEEP"])

    def test_settled_owner_reassessment_omission_and_supersession_keep_closure_honest(self) -> None:
        for disposition_status in ("fixed", "report-only"):
            with self.subTest(disposition_status=disposition_status):
                slug = "settled-" + disposition_status
                (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
                wid = self.begin(slug)
                intake = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
                owner = {**self.contract("VALUE_NOT_TWO"), "kind": "preservation",
                         "sourceRefs": [{"type": "finding", "evidenceId": intake, "id": "SPEC-1"}]}
                recorded = self.record_preflight(slug, wid, [owner, self.EXTRA])
                self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
                self.drive_attack_green(slug, "VALUE_NOT_TWO")
                self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0)
                disposition = self.fixed_disposition(wid, intake, dict(self.ZERO_DOMAIN))
                if disposition_status == "report-only":
                    value = json.loads(disposition.read_text())
                    value["dispositions"][0].update(status="report-only")
                    value["dispositions"][0]["materialConsequence"]["result"] = "false"
                    disposition.write_text(json.dumps(value), encoding="utf-8")
                closed = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                                  "--stage", "preflight", "--findings", "addressed", "--input", str(disposition))
                self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
                self.ok("verify", "--slug", slug, "--", sys.executable, "-c", "print('verified')")
                before = self.status()
                requested = [{"id": "BM_ATTACK", "revalidate": True, "evidence": "affected decision changed"}]
                flagged = self.map_update(slug, dispositions=requested)
                self.assertEqual(flagged.returncode, 0, flagged.stdout + flagged.stderr)
                after = self.status()
                self.assertEqual(after["phase"], before["phase"])
                self.assertEqual(after["verification"], before["verification"])
                self.assertEqual(after["tddCycleCount"], before["tddCycleCount"])
                history = self.ok_text("history")
                self.assertEqual(self.map_update(slug, dispositions=requested).returncode, 0)
                self.assertEqual(self.status(), after)
                self.assertEqual(self.ok_text("history"), history)
                checkpoint = checkpoint_channels(self.repo, self.env, "final-review")
                self.assertIn("BM_ATTACK", " ".join(checkpoint["missing"]))
                self.assertEqual(checkpoint["finding-ledger"][0]["intakeEvidenceId"], intake)
                self.assertTrue(checkpoint["finding-ledger"][0]["owners"][0]["revalidationRequired"])
                # Rechecking historical GREEN is not another defect or cycle.
                rechecked = self.tdd(slug, "green", "BM_ATTACK", "test_attack_probe")
                self.assertEqual(rechecked.returncode, 0, rechecked.stdout + rechecked.stderr)
                self.assertEqual(self.status()["phase"], before["phase"])
                self.assertEqual(self.status()["verification"], before["verification"])
                self.assertEqual(self.status()["tddCycleCount"], before["tddCycleCount"])
                self.assertNotIn("revalidationRequired", self.map_items()["BM_ATTACK"])
                self.assertEqual(self.map_update(slug, dispositions=requested).returncode, 0)
                omitted = self.map_update(slug, dispositions=[{
                    "id": "BM_ATTACK", "status": "omitted", "evidence": "governing scope excludes this guarantee"}])
                self.assertEqual(omitted.returncode, 0, omitted.stdout + omitted.stderr)
                self.assertTrue(self.map_items()["BM_ATTACK"]["revalidationRequired"])
                checkpoint = json.loads(self.cli("checkpoint", "--phase", "final-review").stdout)
                self.assertIn("SPEC-1", " ".join(checkpoint["missing"]), "OMISSION_CLAIMED_FINDING_PROOF")
                self.assertEqual(self.reopen(slug, "BM_ATTACK").returncode, 0)
                baselined = self.tdd(slug, "red", "BM_ATTACK", "test_attack_probe")
                self.assertEqual(baselined.returncode, 0, baselined.stdout + baselined.stderr)
                self.assertNotIn("revalidationRequired", self.map_items()["BM_ATTACK"])
                checkpoint = json.loads(self.cli("checkpoint", "--phase", "final-review").stdout)
                if disposition_status == "fixed":
                    self.assertIn("SPEC-1", " ".join(checkpoint["missing"]), "BASELINE_CLAIMED_REPAIRED_DEFECT")
                else:
                    self.assertNotIn("SPEC-1", " ".join(checkpoint["missing"]))
                if disposition_status == "report-only":
                    # Its baseline legitimately restored report-only closure;
                    # a new affected reassessment, not an unrelated relabel,
                    # makes correction reachable again.
                    reflag = self.map_update(slug, dispositions=[{
                        "id": "BM_ATTACK", "revalidate": True, "evidence": "a newly affected guarantee",
                    }])
                    self.assertEqual(reflag.returncode, 0, reflag.stdout + reflag.stderr)
                # A measured correction remains reachable even when fixed lost
                # its current GREEN. It does not invent a failure to regain it.
                rejection = json.loads(self.fixed_disposition(wid, intake, dict(self.ZERO_DOMAIN), "false").read_text())
                rejection["dispositions"][0]["status"] = "rejected-with-evidence"
                result = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                                  "--stage", "preflight", "--findings", "addressed", "--input",
                                  str(self.json_file("rejection.json", rejection)))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_supersession_requires_current_green_replacement_and_keeps_ownership(self) -> None:
        slug = "replacement-reassessment"
        wid = self.begin(slug)
        intake = self.behavioral_intake(slug, wid, "wrong value")
        refs = [{"type": "finding", "evidenceId": intake, "id": "SPEC-1"}]
        keep = {**self.contract("KEEP_NOT_TWO", refs), "id": "BM_KEEP", "kind": "preservation"}
        recorded = self.record_preflight(slug, wid, [self.contract("VALUE_NOT_TWO", refs), keep])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.drive_attack_green(slug, "VALUE_NOT_TWO")
        self.drive_attack_green(slug, "KEEP_NOT_TWO", "BM_KEEP")
        update = self.map_update(slug, dispositions=[
            {"id": "BM_ATTACK", "status": "superseded", "supersededBy": "BM_KEEP", "evidence": "replacement attack"},
            {"id": "BM_KEEP", "revalidate": True, "evidence": "replacement affected"}])
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
        checkpoint = json.loads(self.cli("checkpoint", "--phase", "final-review").stdout)
        self.assertIn("BM_ATTACK", " ".join(checkpoint["missing"]))
        rechecked = self.tdd(slug, "green", "BM_KEEP", "test_attack_probe")
        self.assertEqual(rechecked.returncode, 0, rechecked.stdout + rechecked.stderr)
        checkpoint = json.loads(self.cli("checkpoint", "--phase", "final-review").stdout)
        self.assertNotIn("BM_ATTACK", " ".join(checkpoint["missing"]))

    def test_a_post_preflight_contract_item_withdraws(self) -> None:
        marker = "WITHDRAW_ADDED_REFUSED"
        slug = "withdraw-added"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        added = self.map_update(slug, items=[self.EXTRA])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertEqual(json.loads(added.stdout)["pending"], ["BM_EXTRA"])
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        summary = json.loads(withdrawn.stdout)
        self.assertEqual(summary["pending"], [], marker)
        self.assertEqual(summary["status"], "passed", marker)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)
        self.assertNotIn("BM_EXTRA", self.cli("complete").stderr, marker)

    def test_a_preflight_declared_pending_item_withdraws(self) -> None:
        marker = "PREFLIGHT_DECLARED_WITHDRAWAL_REFUSED"
        slug = "withdraw-preflight"
        self.open_pass(slug, [self.contract(marker), self.EXTRA])
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(json.loads(withdrawn.stdout)["pending"], ["BM_ATTACK"], marker)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_the_last_green_closes_tdd_without_a_map_update(self) -> None:
        marker = "LAST_GREEN_LEAVES_TDD_IN_PROGRESS"
        slug = "last-green"
        self.open_pass(slug, [self.contract(marker)])
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\n"
            "class AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                                  "--", sys.executable, "-m", "unittest", "test_attack_probe"],
                                 cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_an_attacked_item_refuses_withdrawal(self) -> None:
        marker = "ATTACKED_ITEM_WITHDRAWN"
        slug = "withdraw-attacked"
        self.open_pass(slug, [self.contract(marker)])
        added = self.map_update(slug, items=[self.EXTRA])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.drive_attack_green(slug, "EXTRA_NEVER_ATTACKED", behavior_id="BM_EXTRA")
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertIn("never-attacked", refused.stderr, marker)

    def test_an_owned_item_refuses_withdrawal(self) -> None:
        marker = "OWNED_ITEM_WITHDRAWN"
        slug = "withdraw-owned"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        for reference in (
            {"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"},
            {"type": "design", "evidenceId": str(self.status()["governedDesignEvidence"]), "id": "PRES-1"},
        ):
            extra = {**self.EXTRA, "id": "BM_" + reference["type"].upper(), "sourceRefs": [reference]}
            added = self.map_update(slug, items=[extra])
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, str(extra["id"])))
            self.assertIn("sourceRefs", refused.stderr, marker)

    def test_a_preservation_item_refuses_withdrawal(self) -> None:
        marker = "PRESERVATION_WITHDRAWN"
        slug = "withdraw-preservation"
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED])
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_KEEP"))
        self.assertIn("preservation", refused.stderr, marker)

    def test_a_withdrawn_item_cannot_replace_a_superseded_one(self) -> None:
        marker = "WITHDRAWN_SUPERSESSION_TARGET_ACCEPTED"
        slug = "withdrawn-target"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[{
            "id": "BM_ATTACK", "status": "superseded", "supersededBy": "BM_EXTRA",
            "evidence": "a withdrawn item can never be GREEN"}]))

    def test_withdrawal_does_not_make_tdd_not_required(self) -> None:
        marker = "WITHDRAWN_ENABLED_NOT_REQUIRED"
        slug = "withdrawn-not-required"
        self.open_pass(slug, [self.KEEP_OMITTED])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        refused = self.cli("tdd", "--slug", slug, "--not-required", "cleanup only")
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("already-satisfied", refused.stderr, marker)

    def test_an_omitted_preservation_item_reopens(self) -> None:
        marker = "REOPEN_OMITTED_REFUSED"
        slug = "reopen-omitted"
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED])
        self.drive_attack_green(slug, marker)
        before = str(self.status()["tddEvidence"])
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout)["pending"], ["BM_KEEP"], marker)
        item = self.map_items()["BM_KEEP"]
        self.assertEqual(item["status"], "pending", marker)
        self.assertNotIn("evidence", item, marker)
        prior = self.ok("evidence", "--full", "--evidence-id", before)["document"]["behaviorMap"]
        self.assertEqual({e["id"]: e["status"] for e in prior}["BM_KEEP"], "omitted", marker)

    def test_a_reopened_owner_closes_its_finding(self) -> None:
        marker = "REOPENED_OWNER_CANNOT_CLOSE_FINDING"
        slug = "reopen-finding"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        reference = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        keep = {**self.KEEP_OMITTED, "status": "pending", "sourceRefs": reference}
        keep.pop("evidence")
        owned = self.record_preflight(slug, wid, [self.contract(marker, reference), keep])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        # The X6R8 mistake: the finding-owned preservation item is omitted in error.
        omitted = self.map_update(slug, dispositions=[
            {"id": "BM_KEEP", "status": "omitted", "evidence": "mistaken reclassification"}])
        self.assertEqual(omitted.returncode, 0, omitted.stdout + omitted.stderr)
        self.drive_attack_green(slug, marker)
        disposition = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        stuck = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input", str(disposition))
        self.assertEqual(stuck.returncode, 2, stuck.stdout + stuck.stderr)
        self.assertIn("BM_KEEP", stuck.stderr, stuck.stderr)
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                   "--slug", slug, "--phase", "red", "--behavior-id", "BM_KEEP",
                                   "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, marker + ": " + baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout + baseline.stderr,
                      marker + ": " + baseline.stdout + baseline.stderr)
        # The probe file changed the reviewable tree; the closing document binds the new one.
        disposition = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        closed = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input", str(disposition))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(self.status()["findingStates"][0]["status"], "fixed", marker)

    def test_pending_reopen_refuses_but_green_can_be_reassessed(self) -> None:
        marker = "REOPEN_REFUSAL_MISSING"
        slug = "reopen-refusals"
        also = {**self.KEEP_OMITTED, "id": "BM_ALSO", "status": "pending"}
        also.pop("evidence")
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED, also])
        for identifier in ("BM_ATTACK", "BM_ALSO"):
            refused = self.refused_unchanged(marker, lambda: self.reopen(slug, identifier))
            self.assertIn("reopened", refused.stderr, marker)
        self.assertEqual(self.map_update(slug, dispositions=[
            {"id": "BM_ALSO", "status": "omitted", "evidence": "settled"}]).returncode, 0)
        self.drive_attack_green(slug, marker)
        evidence_id = self.status()["tddEvidence"]
        historical = self.ok("evidence", "--full", "--evidence-id", evidence_id)
        reopened = self.map_update(slug, dispositions=[{
            "id": "BM_ATTACK", "status": "pending", "evidence": "GREEN lacks sufficient behavioral proof"}])
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        current = self.map_items()["BM_ATTACK"]
        self.assertEqual(current["status"], "pending")
        self.assertFalse(behavior_map.producer_proved(current))
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", evidence_id), historical)

    def keep_probe(self, value: int) -> None:
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, {value}, 'KEEP_REGRESSED')\n",
            encoding="utf-8")

    def tdd(self, slug: str, phase: str, behavior_id: str, module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--phase", phase, "--behavior-id", behavior_id,
                               "--", sys.executable, "-m", "unittest", module],
                              cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

    def test_an_already_satisfied_preservation_item_reopens(self) -> None:
        marker = "REOPEN_SATISFIED_REFUSED"
        slug = "reopen-satisfied"
        keep = {**self.KEEP_OMITTED, "status": "pending"}
        keep.pop("evidence")
        self.open_pass(slug, [self.contract(marker), keep])
        self.keep_probe(1)
        baseline = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        before = str(self.status()["tddEvidence"])
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        item = self.map_items()["BM_KEEP"]
        self.assertEqual(item["status"], "pending", marker)
        self.assertNotIn("evidence", item, marker)
        prior = self.ok("evidence", "--full", "--evidence-id", before)["document"]["behaviorMap"]
        self.assertEqual({e["id"]: e["status"] for e in prior}["BM_KEEP"], "already-satisfied", marker)

    def test_withdrawal_while_red_refuses(self) -> None:
        marker = "RED_ITEM_WITHDRAWN"
        slug = "withdraw-red"
        self.open_pass(slug, [self.contract(marker)])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        (self.repo / "test_extra_probe.py").write_text(
            "import app, unittest\nclass ExtraProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'EXTRA_NEVER_ATTACKED')\n",
            encoding="utf-8")
        opened = self.tdd(slug, "red", "BM_EXTRA", "test_extra_probe")
        self.assertEqual(opened.returncode, 0, opened.stdout + opened.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "red")
        self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "red", marker)

    def test_a_withdrawn_only_map_keeps_the_edit_gate_advising(self) -> None:
        marker = "WITHDRAWN_OPENED_EDITING"
        slug = "withdrawn-gate"
        self.open_pass(slug, [self.KEEP_OMITTED])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        gate = subprocess.run([sys.executable, str(ROOT / "hooks" / "rcf-intake-gate.py")],
                              cwd=self.repo, env=self.env, text=True, capture_output=True, check=False,
                              input=json.dumps({"tool_input": {"file_path": str(self.repo / "app.py")}}))
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        output = json.loads(gate.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", output, marker + ": " + gate.stdout)
        self.assertIn("RED", output["additionalContext"], marker + ": " + gate.stdout)

    def rejected_owner(self, slug: str, marker: str, status: str = "rejected-with-evidence") -> None:
        """A finding mapped on a false premise leaves an owned pending item behind;
        once the finding is closed without a fix, that item owns nothing."""
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        extra = {**self.EXTRA, "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}
        self.assertEqual(self.map_update(slug, items=[extra]).returncode, 0)
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertIn("sourceRefs", refused.stderr, marker)
        # A behavioral finding closes without a fix only through a proved owner.
        self.drive_attack_green(slug, marker)
        rejection = self.json_file("rejected.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": status, "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": "python -c 'import app; print(app.value)'",
                            "result": "false" if status == "rejected-with-evidence" else "true: the value differs"},
                "occurrence": {"domain": "every read of app.value", "count": 0, "complete": True,
                               "command": "python -m unittest test_probe", "result": "count=0"},
                "materialConsequence": {"claim": "callers observe the wrong value", "command": "import app",
                                        "result": "false"},
                "evidence": "python -c 'import app; print(app.value)' printed 1 as documented",
            }]})
        rejected = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input", str(rejection))
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)

    def test_an_item_owned_only_by_a_rejected_finding_withdraws(self) -> None:
        marker = "REJECTED_FINDING_OWNER_STUCK"
        slug = "withdraw-rejected-owner"
        self.rejected_owner(slug, marker)
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_an_item_owned_only_by_a_report_only_finding_withdraws(self) -> None:
        marker = "REPORT_ONLY_OWNER_STUCK"
        slug = "withdraw-report-only-owner"
        self.rejected_owner(slug, marker, status="report-only")
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_a_withdrawn_owner_survives_into_the_checkpoint_ledger(self) -> None:
        # The advisor wrapper forwards the checkpoint's findingLedger verbatim, so
        # this is the channel through which the final advisor sees map entries.
        marker = "WITHDRAWN_DROPPED_FROM_CHANNEL"
        slug = "withdrawn-ledger"
        self.rejected_owner(slug, marker)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        ledger = checkpoint_channels(self.repo, self.env, "preflight-advice").get("finding-ledger", [])
        entry = next(item for item in ledger if item["findingId"] == "SPEC-1")
        owners = {owner["id"]: owner["status"] for owner in entry["owners"]}
        self.assertEqual(owners.get("BM_EXTRA"), "withdrawn", marker + ": " + json.dumps(entry))

    def test_a_withdrawn_item_cannot_be_superseded(self) -> None:
        marker = "WITHDRAWN_SOURCE_SUPERSEDED"
        slug = "withdrawn-source"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        refused = self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[{
            "id": "BM_EXTRA", "status": "superseded", "supersededBy": "BM_ATTACK",
            "evidence": "a withdrawn item owns nothing to hand over"}]))
        self.assertIn("withdrawn", refused.stderr, marker)


class ReportOnlyProofAttacks(AttackHarness):
    """Issue #191: a behavioral finding closes report-only only through an owning
    attack the producer proved, and no disposition may cite a temp-directory
    script as its measurement. ARM X6R8 closed nineteen findings that way."""

    def disposition(self, wid: str, intake_id: str, status: str, *, command: str = "python -m unittest test_attack_probe",
                    premise_result: str = "true", evidence: str = "measured on the candidate") -> Path:
        consequence = "false" if status == "report-only" else "closed"
        return self.json_file("disposition.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": status, "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": command, "result": premise_result},
                "occurrence": {"domain": "every read of app.value", "count": 0, "complete": True,
                               "command": command, "result": "count=0"},
                "materialConsequence": {"claim": "callers observe the wrong value", "command": command,
                                        "result": consequence},
                "evidence": evidence,
            }]})

    def dispose(self, slug: str, wid: str, document: Path) -> subprocess.CompletedProcess[str]:
        return self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                        "--stage", "preflight", "--findings", "addressed", "--input", str(document))

    def owned_pass(self, slug: str, marker: str, extra: list[dict[str, object]] | None = None,
                   attack_owned: bool = True) -> tuple[str, str]:
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker)
        if not attack_owned:
            items[0]["sourceRefs"] = []
        recorded = self.record_preflight(slug, wid, items + (extra or []))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return wid, intake_id

    def keep(self, intake_id: str, **fields: object) -> dict[str, object]:
        return {"id": "BM_KEEP", "kind": "preservation", "basis": "existing guarantee",
                "behavior": "the value stays readable", "seam": "fixture app module",
                "expected": "app.value stays 1", "redFailure": "KEEP_REGRESSED", "status": "pending",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}], **fields}

    def test_report_only_refuses_without_an_owner(self) -> None:
        marker = "REPORT_ONLY_UNOWNED_ACCEPTED"
        slug = "report-only-unowned"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("owning", refused.stderr, marker)

    def test_report_only_refuses_a_pending_owner(self) -> None:
        marker = "UNPROVED_OWNER_REPORT_ONLY_ACCEPTED"
        slug = "report-only-pending"
        wid, intake_id = self.owned_pass(slug, marker)
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("BM_ATTACK", refused.stderr, marker)

    def test_report_only_accepts_a_producer_baselined_preservation_owner(self) -> None:
        marker = "OWNED_REPORT_ONLY_REFUSED"
        slug = "report-only-preservation"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker); items[0]["sourceRefs"] = []
        recorded = self.record_preflight(slug, wid, items + [self.keep(intake_id)])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 1, 'KEEP_REGRESSED')\n", encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                                   "--phase", "red", "--behavior-id", "BM_KEEP", "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        accepted = self.dispose(slug, wid, self.disposition(wid, intake_id, "report-only"))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)
        self.assertEqual(self.status()["findingStates"][0]["status"], "report-only", marker)

    def test_report_only_accepts_a_producer_baselined_contract_owner(self) -> None:
        marker = "CONTRACT_BASELINE_OWNER_REFUSED"
        slug = "report-only-contract-baseline"
        wid, intake_id = self.owned_pass(slug, marker)
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 1, {marker!r})\n", encoding="utf-8")
        baseline = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        accepted = self.dispose(slug, wid, self.disposition(wid, intake_id, "report-only"))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)

    def test_report_only_refuses_a_prose_baselined_owner(self) -> None:
        marker = "PROSE_BASELINE_OWNER_ACCEPTED"
        slug = "report-only-prose"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker); items[0]["sourceRefs"] = []
        # Prose evidence proves nothing, even when it repeats the producer's
        # own baseline wording (the shape a pre-#191 ledger can carry).
        prose = self.keep(intake_id, status="already-satisfied",
                          evidence="baseline-passed before any production edit: python -m unittest test_keep_probe")
        recorded = self.record_preflight(slug, wid, items + [prose])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("BM_KEEP", refused.stderr, marker)

    def test_a_producer_baseline_carries_its_proof_field(self) -> None:
        marker = "PRODUCER_BASELINE_UNRECORDED"
        slug = "baseline-proof-field"
        wid, intake_id = self.owned_pass(slug, marker)
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 1, {marker!r})\n", encoding="utf-8")
        baseline = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        recorded = self.ok("evidence", "--full", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["behaviorMap"]
        attack = next(item for item in recorded if item["id"] == "BM_ATTACK")
        self.assertIsInstance(attack.get("baselineProof"), dict, marker)
        # Joint proof: the field is the producer's alone.
        forged = self.keep(intake_id, status="already-satisfied", evidence="passes by inspection",
                           baselineProof=attack["baselineProof"])
        refused = self.record_preflight("baseline-proof-forged", self.begin("baseline-proof-forged"),
                                        self.owned_map(intake_id, marker=marker) + [forged])
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("tdd --phase red", refused.stderr, marker)

    def test_fixed_accepts_a_producer_baselined_contract_owner_beside_a_green_one(self) -> None:
        marker = "PRODUCER_CONTRACT_BASELINE_BLOCKED_FIXED"
        slug = "fixed-contract-baseline"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        recorded = self.record_preflight(
            slug, wid, self.owned_map(intake_id, marker=marker) + [self.keep(intake_id, kind="contract")])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 1, 'KEEP_REGRESSED')\n", encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                                   "--phase", "red", "--behavior-id", "BM_KEEP", "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        self.drive_attack_green(slug, marker)
        closed = self.dispose(slug, wid, self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN)))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(self.status()["findingStates"][0]["status"], "fixed", marker)

    def owned_item(self, identifier: str, intake_id: str, marker: str) -> dict[str, object]:
        return {"id": identifier, "kind": "contract", "basis": "advisor finding attack",
                "behavior": "the reviewed value is corrected", "seam": "fixture app module",
                "expected": "app.value is 2", "redFailure": marker, "status": "pending",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}

    def supersede_owned(self, slug: str, wid: str, intake_id: str, marker2: str) -> None:
        update = self.json_file("supersede.json", {
            "reassessment": "a sharper attack owns the outcome",
            "items": [self.owned_item("BM_ATTACK2", intake_id, marker2)],
            "dispositions": [{"id": "BM_ATTACK", "status": "superseded", "supersededBy": "BM_ATTACK2",
                              "evidence": "BM_ATTACK2 asserts the same outcome through its own surface"}]})
        superseded = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(superseded.returncode, 0, superseded.stdout + superseded.stderr)

    def test_a_temp_path_command_refuses(self) -> None:
        marker = "TEMP_PATH_COMMAND_ACCEPTED"
        slug = "temp-path-command"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        probe = str(Path(tempfile.gettempdir()) / "safe_import_delivery_matrix.py")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    command=f"python3 {probe}", premise_result="false")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn(probe, refused.stderr, marker)

    def test_a_temp_path_evidence_refuses(self) -> None:
        marker = "TEMP_PATH_EVIDENCE_ACCEPTED"
        slug = "temp-path-evidence"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        probe = str(Path(tempfile.gettempdir()) / "matrix.py")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    premise_result="false", evidence=f"see the output of {probe}")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn(probe, refused.stderr, marker)

    def test_an_estate_path_is_allowed(self) -> None:
        marker = "ESTATE_PATH_REFUSED"
        slug = "estate-path"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        estate = str(Path.home() / ".claude" / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    command=f"sed -n 1,5p {estate}", premise_result="false")
        accepted = self.dispose(slug, wid, document)
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)


class WorkflowRecovery(AttackHarness):
    """Old/candidate operations through the existing public runtime and ledger."""

    def settled(self) -> tuple[str, str]:
        slug = "recovery"
        wid = self.open_pytest_pass(slug, "VALUE_UNCORRECTED")
        self.drive_attack_green(slug, "VALUE_UNCORRECTED")
        return slug, wid

    def add_claim(self, slug: str, wid: str, identifier: str) -> None:
        item = self.owned_map("unused", marker="SECOND_OPERATION_WRONG")[0]
        item.update(id=identifier, sourceRefs=[])
        document = self.json_file("add.json", {
            "reassessment": "An independently uncovered operation", "items": [item],
        })
        self.ok("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(document))

    def test_new_obligation_retains_measurements_and_blocks_closure(self) -> None:
        marker = "OBLIGATION_DISCARDED_RECEIPTS"
        slug, wid = self.settled()
        self.ok("verify", "--slug", slug, "--", sys.executable, "-m", "unittest", "test_attack_probe")
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        before = self.status()
        self.add_claim(slug, wid, "BM_SECOND")
        after = self.status()
        self.assertEqual(before["activeCandidateTree"], after["activeCandidateTree"])
        for field in ("verificationEvidence", "verificationLatestEvidence", "qualityGateEvidence", "qualityGateManifestId"):
            self.assertEqual(after.get(field), before[field], marker)
        self.assertEqual(after["codeReview"]["status"], "pending")
        self.assertEqual(self.cli("complete").returncode, 2)
        self.assertIn("BM_SECOND", self.cli("complete").stderr)

    def test_settled_recheck_and_baseline_supersession_keep_lineage(self) -> None:
        marker = "RETAINED_PROOF_REFUSED"
        slug = "recovery"
        wid = self.open_pytest_pass(slug, "VALUE_UNCORRECTED")
        self.add_claim(slug, wid, "BM_BASELINE")
        (self.repo / "test_baseline_probe.py").write_text(
            "import app, unittest\nclass BaselineProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 1, 'SECOND_OPERATION_WRONG')\n",
            encoding="utf-8")
        self.ok("tdd", "--slug", slug, "--phase", "red", "--behavior-id", "BM_BASELINE",
                "--", sys.executable, "-m", "unittest", "test_baseline_probe")
        self.drive_attack_green(slug, "VALUE_UNCORRECTED")
        rerun = self.mapped_tdd(slug, "green", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(rerun.returncode, 0, marker + rerun.stderr)
        before = self.status()
        original = self.ok("evidence", "--full", "--evidence-id", before["tddEvidence"])["document"]
        for target in ("MISSING", "BM_BASELINE"):
            document = self.json_file("bad-replacement.json", {"reassessment": "Reject invalid chain", "dispositions": [
                {"id": "BM_BASELINE", "status": "superseded", "supersededBy": target, "evidence": "Obsolete baseline"},
            ]})
            self.refused_unchanged(marker, lambda: self.cli(
                "record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(document)))
        document = self.json_file("replacement.json", {"reassessment": "The original attack owns the corrected promise", "dispositions": [
            {"id": "BM_BASELINE", "status": "superseded", "supersededBy": "BM_ATTACK", "evidence": "Obsolete baseline"},
        ]})
        result = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(document))
        self.assertEqual(result.returncode, 0, marker + result.stderr)
        evidence = self.ok("evidence", "--full", "--evidence-id", self.status()["tddEvidence"])["document"]
        retired = next(item for item in evidence["behaviorMap"] if item["id"] == "BM_BASELINE")
        self.assertEqual(retired["supersededFrom"], "already-satisfied")
        self.assertIn("baselineProof", retired)
        self.assertEqual(self.status()["mapSelections"].get("BM_BASELINE"),
                         before["mapSelections"]["BM_BASELINE"], "BASELINE_SELECTION_LOST")
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", before["tddEvidence"])["document"], original)

    def test_manifest_sampling_errors_refuse_without_ledger_changes(self) -> None:
        slug = "sampling-error"
        wid = self.begin(slug)
        intake = self.behavioral_intake(slug, wid, "app.value must be two")
        self.assertEqual(self.record_preflight(slug, wid, self.owned_map(intake, marker="VALUE_WRONG")).returncode, 0)
        self.drive_attack_green(slug, "VALUE_WRONG")
        executed = self.cli("verify", "--slug", slug, "--", sys.executable, "-m", "unittest", "test_attack_probe")
        self.assertEqual(executed.returncode, 0, executed.stderr)
        run = json.loads(executed.stdout.splitlines()[-1])
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        item = self.owned_map("unused", marker="MISSING")[0]
        item.update(id="BM_ADDITIONAL", sourceRefs=[])
        addition = self.json_file("add.json", {"reassessment": "Additional obligation", "items": [item]})
        disposition = self.json_file("fixed.json", {"intakeEvidenceId": intake, "dispositions": [{
            "finding_id": "SPEC-1", "status": "fixed", "reason": "The owning assertion now passes",
            "evidenceRefs": [run["evidenceId"] + ":" + str(run["runIndex"])],
        }]})
        commands = [
            ["record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(addition)],
            ["record", "advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
             "--findings", "addressed", "--input", str(disposition)],
        ]
        history = self.ok("history")
        index = self.repo / ".git" / "index"
        saved = index.read_bytes()
        for command in commands:
            index.write_bytes(b"not a git index")
            try:
                refused = self.cli(*command)
            finally:
                index.write_bytes(saved)
            self.assertEqual(self.ok("history"), history)
            self.assertEqual(refused.returncode, 2, "SAMPLING_ERROR_ESCAPED" + refused.stderr)
            self.assertIn("index file smaller than expected", refused.stderr)
            self.assertNotIn("Traceback", refused.stderr)

    def test_typed_only_and_explicit_failed_command_replacement(self) -> None:
        marker = "VERIFICATION_CORRECTION_REFUSED"
        slug, _ = self.settled()
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(self.status()["verification"], "passed", marker)
        failed = self.cli("verify", "--slug", slug, "--", sys.executable, "-m", "unittest", "missing_test")
        self.assertEqual(failed.returncode, 2)
        receipt = json.loads(failed.stdout.splitlines()[-1])
        reference = receipt["evidenceId"] + ":1"
        fixed = self.cli("verify", "--slug", slug, "--replaces", reference,
                         "--reason", "Correct the misspelled test module",
                         "--", sys.executable, "-m", "unittest", "test_attack_probe")
        self.assertEqual(fixed.returncode, 0, marker + fixed.stderr)
        self.assertEqual(self.status()["verification"], "passed")
        self.assertIn("qualityGateManifestId", self.status())
        self.assertEqual(self.cli("verify", "--slug", slug, "--replaces", reference,
                                 "--reason", "A stale correction", "--", sys.executable,
                                 "-m", "unittest", "test_attack_probe").returncode, 2)

    def test_failed_settled_recheck_does_not_leave_green_proof(self) -> None:
        slug, _ = self.settled()
        (self.repo / "app.py").write_text("value = 1\n")
        failed = self.mapped_tdd(slug, "green", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(failed.returncode, 2)
        document = self.ok("evidence", "--full", "--evidence-id", self.status()["tddEvidence"])["document"]
        self.assertEqual(document["behaviorMap"][0]["status"], "red", "FAILED_RECHECK_STILL_GREEN")
        self.assertIn("BM_ATTACK", self.cli("complete").stderr)

    def test_recovery_identity_and_selected_status(self) -> None:
        marker = "RECOVERY_IDENTITY_MISSING"
        slug, wid = self.settled()
        state = self.status()
        summary = self.ok_text("summary")
        self.assertIn(wid, summary, marker)
        self.assertIn(state["activeCandidateTree"], summary, marker)
        self.assertIn(state["tddEvidence"], summary, marker)
        self.assertIn("Missing state is pending, never success.", summary, marker)
        selected = self.cli("status", "--fields", "workflowId,activeCandidateTree,nextAction")
        self.assertEqual(selected.returncode, 0, marker + selected.stderr)
        self.assertEqual(json.loads(selected.stdout), {k: state[k] for k in ("workflowId", "activeCandidateTree", "nextAction")})
        hook = subprocess.run([sys.executable, str(ROOT / "hooks/skill-discipline-rearm.py")],
                              input=json.dumps({"cwd": str(self.repo)}), env=self.env,
                              capture_output=True, text=True, check=False)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(wid, context, marker)
        self.assertIn(state["activeCandidateTree"], context, marker)

    def test_disposition_reuses_bound_execution_without_another_child(self) -> None:
        marker = "EXECUTED_RECEIPT_REFUSED"
        slug = "receipt"
        wid = self.begin(slug)
        intake = self.behavioral_intake(slug, wid, "app.value must be two")
        self.assertEqual(self.record_preflight(slug, wid, self.owned_map(intake, marker="VALUE_UNCORRECTED")).returncode, 0)
        self.drive_attack_green(slug, "VALUE_UNCORRECTED")
        counter = self.tmp / "executions"
        operation = ("import app,sys; from pathlib import Path; p=Path(sys.argv[1]); "
                     "p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert app.value == 2")
        result = self.cli("verify", "--slug", slug, "--", sys.executable, "-c", operation, str(counter))
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout.splitlines()[-1])
        document = self.json_file("disposition.json", {
            "intakeEvidenceId": intake, "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed",
                "reason": "The owning attack and current app read both return two; the tested read has zero incorrect results.",
                "mechanism": "The initializer supplied the wrong constant to all readers; setting it to 2 corrects the complete read surface, including the fresh import counterexample.",
                "evidenceRefs": [receipt["evidenceId"] + ":0"],
            }],
        })
        (self.repo / "app.py").write_text("value = 1\n")
        stale = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input", str(document))
        self.assertEqual(stale.returncode, 2)
        self.assertIn("stale", stale.stderr)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "pending")
        (self.repo / "app.py").write_text("value = 2\n")
        disposed = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input", str(document))
        self.assertEqual(disposed.returncode, 0, marker + disposed.stderr)
        self.assertEqual(counter.read_text(), "x", marker)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "addressed")

    def test_receipt_outcome_is_not_red_acceptance(self) -> None:
        marker = "RECEIPT_OUTCOME_WRONG"
        slug = "receipt-outcome"
        (self.repo / "app.py").write_text("value = 2\n")  # the passing case is the pass-start production
        self.git("commit", "-qam", "value two at pass start")
        wid = self.begin(slug)
        envelope = self.json_file("intake.json", {"schemaVersion": 1, "verdict": "completed", "findings": [
            {"id": "STD-1", "claim": "test convention needs correction", "material": True, "kind": "nonbehavioral"},
        ]})
        intake = self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                         "--source", "codex-advisor", "--input", str(envelope))
        intake = self.status()["advisorPreflight"]["intakeEvidence"]
        item = self.owned_map("unused", marker="VALUE_WRONG")[0]
        item["sourceRefs"] = []
        self.assertEqual(self.record_preflight(slug, wid, [item]).returncode, 0)
        self.write_probe("VALUE_WRONG")
        for value, expected in ((None, 2), (1, 2), (2, 0)):
            self.write_probe("VALUE_WRONG")
            (self.repo / "app.py").write_text(f"value = {value or 1}\n")
            if value is None:
                (self.repo / "test_probe.py").write_text(
                    "import unittest\nclass T(unittest.TestCase):\n"
                    " @unittest.skip('operation unavailable')\n def test_value(self): self.fail('VALUE_WRONG')\n")
            if value == 2:
                reset = self.json_file("reset.json", {"reassessment": "Observe actual successful baseline",
                    "dispositions": [{"id": "BM_ATTACK", "status": "pending", "evidence": "Release prior RED binding"}]})
                self.ok("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(reset))
            run = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "-v", "test_probe"])
            self.assertEqual(run.returncode, 2 if value is None else 0, run.stderr)
            receipt = json.loads(run.stdout.splitlines()[-1])
            document = self.json_file("fixed.json", {"intakeEvidenceId": intake, "dispositions": [{
                "finding_id": "STD-1", "status": "fixed", "reason": "Inspect convention using actual operation outcome",
                "evidenceRefs": [receipt["summaryId"] + ":" + str(receipt["runIndex"])],
            }]})
            result = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                              "--stage", "preflight", "--findings", "addressed", "--input", str(document))
            self.assertEqual(result.returncode, expected, marker + result.stdout + result.stderr)

    def test_summary_reports_current_binding_after_source_drift(self) -> None:
        slug, _ = self.settled()
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        (self.repo / "app.py").write_text("value = 3\n")
        summary = self.ok_text("summary")
        self.assertIn("verification=pending", summary, "STALE_RECOVERY_ADVERTISED_SUCCESS")
        self.assertIn("next=repo-context-forge", summary, "STALE_RECOVERY_ADVERTISED_SUCCESS")
        self.assertIn("quality-gate-tree-stale", summary, "STALE_RECOVERY_ADVERTISED_SUCCESS")
        receipt = self.ok("pause", "--slug", slug, "--workflow-id", self.status()["workflowId"],
                          "--reason", "Inspect current recovery")
        self.assertEqual(receipt["nextAction"], "repo-context-forge", "STALE_RECOVERY_ADVERTISED_SUCCESS")

    def test_printed_unexecuted_test_never_supplies_receipt_proof(self) -> None:
        cases = [(flags, flush, expected, "body") for flags, flush, expected in (([], False, 2), (["-q"], False, 2),
                                       (["-v", "-q"], False, 2), (["--verbose", "--quiet"], False, 2),
                                       (["-vq"], False, 2), (["-vfq"], False, 2),
                                       (["-ktest_value"], False, 2), (["-qv"], False, 2),
                                       (["-v"], False, 2), (["-v"], True, 2), (["-v"], True, 1))]
        cases += [(["-v"], True, expected, shape) for expected in (1, 2) for shape in ("result", "header")]
        cases += [(["-v"], True, 1, fixture) for fixture in
                  ("setUp", "tearDown", "asyncSetUp", "asyncTearDown", "setUpClass", "tearDownClass",
                   "setUpModule", "tearDownModule")]
        cases.append((["-v"], False, 1, "summary"))
        cases += [(["-v"], True, 1, shape) for shape in
                  ("prefix-space", "prefix-text", "prefix-bare", "prefix-interrupted")]
        for flags, flush, expected, shape in cases:
            h = WorkflowRecovery(); h.setUp()
            try:
                slug = "printed-proof"
                wid = h.open_pytest_pass(slug, "VALUE_UNCORRECTED")
                h.add_claim(slug, wid, "BM_OTHER")
                name = "test_other" if shape.startswith("prefix-") or shape in {"body", "result", "header", "summary"} else shape
                test_id = f"test_actual.T.{name}"
                printed = ("\n" if flush else "") + f"{name} ({test_id}) ... ok"
                if shape in {"header", "prefix-bare"}:
                    printed = printed.removesuffix(" ... ok")
                elif shape == "summary":
                    printed += "\nRan 2 tests in 0.000s\nOK"
                if shape.startswith("prefix-"):
                    printed += "\n " if shape == "prefix-space" else "\nprefix "
                end = "" if shape.startswith("prefix-") else "\n"
                printing = f"print({printed!r},end={end!r},flush={flush!r})\n"
                in_body = shape in {"body", "summary"}
                (h.repo / "test_actual.py").write_text(
                    "import app,unittest\n"
                    + ("" if in_body else printing)
                    + ("print('='*70,flush=True)\n" if shape == "result" else "")
                    + "class T(unittest.TestCase):\n"
                    " def test_value(self):\n"
                    + ("  " + printing if in_body else "")
                    + ("  print('note',end='',flush=True)\n" if shape == "prefix-interrupted" else "")
                    + f"  self.assertEqual(app.value,{expected},'VALUE_UNCORRECTED')\n"
                    " def test_other(self): self.fail('SECOND_OPERATION_WRONG')\n")
                run = h.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", *flags,
                                                "test_actual.T.test_value"])
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                receipt = json.loads(run.stdout.splitlines()[-1])
                reuse = h.cli("tdd", "--slug", slug, "--phase", "red", "--behavior-id", "BM_OTHER",
                              "--from-evidence", receipt["summaryId"] + ":" + str(receipt["runIndex"]),
                              "--test-id", test_id)
                self.assertEqual(reuse.returncode, 2, "PRINTED_TEST_BECAME_PROOF" + reuse.stdout)
                state = h.status()
                items = h.ok("evidence", "--full", "--evidence-id", state["tddEvidence"])["document"]["behaviorMap"]
                self.assertEqual(next(i["status"] for i in items if i["id"] == "BM_OTHER"),
                                 "pending", "PRINTED_TEST_BECAME_PROOF")
            finally:
                h.tearDown()

    def test_docstring_reports_reuse_red_and_green_without_execution(self) -> None:
        slug = "docstring-proof"
        wid = self.open_pytest_pass(slug, "SECOND_OPERATION_WRONG")
        self.add_claim(slug, wid, "BM_SECOND")
        counter = self.tmp / "executions"
        self.env["PROBE_COUNTER"] = str(counter)
        (self.repo / "test_actual.py").write_text(
            "import app,os,unittest\nfrom pathlib import Path\n"
            "p=Path(os.environ['PROBE_COUNTER']); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
            "class T(unittest.TestCase):\n def test_value(self):\n"
            "  \"\"\"ERROR: Value (after correction)\n\n  Preserve this useful description.\"\"\"\n"
            "  self.assertEqual(app.value,2,'SECOND_OPERATION_WRONG')\n"
            " def test_other(self):\n  \"\"\"FAIL: Value (2.0)\"\"\"\n"
            "  self.assertEqual(app.value,2,'SECOND_OPERATION_WRONG')\n")
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n")
            run = self.mapped_tdd(slug, phase, [sys.executable, "-m", "unittest", "-v", "test_actual"])
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            receipt = json.loads(run.stdout.splitlines()[-1])
            before = counter.read_text()
            reused = self.cli("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_SECOND",
                              "--from-evidence", receipt["summaryId"] + ":" + str(receipt["runIndex"]),
                              "--test-id", "test_actual.T.test_value")
            self.assertEqual(reused.returncode, 0, "DOCSTRING_REUSE_REFUSED" + reused.stderr)
            self.assertEqual(counter.read_text(), before)
        self.assertEqual(counter.read_text(), "xx")

    def test_selected_assertion_reuses_beside_unrelated_setup_failure(self) -> None:
        slug = "selected-proof"
        wid = self.open_pytest_pass(slug, "SECOND_OPERATION_WRONG")
        self.add_claim(slug, wid, "BM_SECOND")
        self.add_claim(slug, wid, "BM_SETUP")
        self.add_claim(slug, wid, "BM_CLASS")
        counter = self.tmp / "executions"
        self.env["PROBE_COUNTER"] = str(counter)
        (self.repo / "test_actual.py").write_text(
            "import app,os,unittest\nfrom pathlib import Path\n"
            "p=Path(os.environ['PROBE_COUNTER']); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
            "class T(unittest.TestCase):\n"
            " def test_value(self): self.assertEqual(app.value,2,'SECOND_OPERATION_WRONG')\n"
            "class Broken(unittest.TestCase):\n"
            " def setUp(self): raise RuntimeError('SECOND_OPERATION_WRONG')\n"
            " def test_other(self): self.fail('SECOND_OPERATION_WRONG')\n"
            "class BrokenClass(unittest.TestCase):\n"
            " @classmethod\n def setUpClass(cls): raise RuntimeError('SECOND_OPERATION_WRONG')\n"
            " def test_class(self): self.fail('SECOND_OPERATION_WRONG')\n")
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n")
            run = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "-v", "test_actual"])
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("before the test body", run.stderr)
            receipt = json.loads(run.stdout.splitlines()[-1])
            reference = receipt["summaryId"] + ":" + str(receipt["runIndex"])
            before = counter.read_text()
            reused = self.cli("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_SECOND",
                              "--from-evidence", reference, "--test-id", "test_actual.T.test_value")
            self.assertEqual(reused.returncode, 0, "SIBLING_SETUP_BLOCKED_PROOF" + reused.stderr)
            for item, test in (("BM_SETUP", "Broken.test_other"), ("BM_CLASS", "BrokenClass.setUpClass")):
                refused = self.cli("tdd", "--slug", slug, "--phase", "red", "--behavior-id", item,
                                   "--from-evidence", reference, "--test-id", "test_actual." + test)
                self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertEqual(counter.read_text(), before)
        self.assertEqual(counter.read_text(), "xx")
        self.assertIn("BM_SETUP", self.cli("complete").stderr)

    def test_one_execution_attributes_each_item_and_never_a_skip(self) -> None:
        marker = "ATTRIBUTED_PROOF_REFUSED"
        slug = "attribution"
        wid = self.begin(slug)
        intake = self.behavioral_intake(slug, wid, "app.label must be ready")
        source = self.owned_map("unused", marker="VALUE_UNCORRECTED")[0]
        source["sourceRefs"] = []
        owned = self.owned_map(intake, marker="SECOND_OPERATION_WRONG")[0]
        owned.update(id="BM_SECOND", behavior="the label is ready", expected="app.label is ready")
        self.assertEqual(self.record_preflight(slug, wid, [source, owned]).returncode, 0)
        self.add_claim(slug, wid, "BM_SKIPPED")
        counter = self.tmp / "executions"
        self.env["PROBE_COUNTER"] = str(counter)
        (self.repo / "app.py").write_text("value = 1\nlabel = 'waiting'\n")
        (self.repo / "test_batch.py").write_text(
            "import app,os,unittest\nfrom pathlib import Path\n"
            "p=Path(os.environ['PROBE_COUNTER']); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
            "class T(unittest.TestCase):\n"
            " def test_value(self): self.assertEqual(app.value,2,'VALUE_UNCORRECTED')\n"
            " def test_label(self): self.assertEqual(app.label,'ready','SECOND_OPERATION_WRONG')\n"
            " @unittest.skip('unavailable operation')\n"
            " def test_skip(self): self.fail('SECOND_OPERATION_WRONG')\n")
        for stage, phase in (("red", "red"), ("mixed", "green"), ("green", "green")):
            if phase == "green":
                (self.repo / "app.py").write_text(f"value = {1 if stage == 'mixed' else 2}\nlabel = 'ready'\n")
            execution = self.cli("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                                 "--", sys.executable, "-m", "unittest", "-v", "test_batch")
            self.assertEqual(execution.returncode, 2 if stage == "mixed" else 0, execution.stdout + execution.stderr)
            receipt = json.loads(execution.stdout.splitlines()[-1])
            reference = receipt["summaryId"] + ":" + str(receipt.get("runIndex", 0))
            result = self.cli("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_SECOND",
                              "--from-evidence", reference, "--test-id", "test_batch.T.test_label")
            self.assertEqual(result.returncode, 0, marker + result.stderr)
            if stage == "mixed":
                attributed = json.loads(result.stdout.splitlines()[-1])
                document = self.json_file("mixed-fixed.json", {"intakeEvidenceId": intake, "dispositions": [{
                    "finding_id": "SPEC-1", "status": "fixed", "reason": "The label attack passes with zero wrong labels while the independent value attack still fails.",
                    "mechanism": "The shared initializer supplied waiting to every label reader. Initialize label to ready; the attributed label operation now passes independently of the still-incorrect value initializer.",
                    "evidenceRefs": [attributed["summaryId"] + ":" + str(attributed["runIndex"])],
                }]})
                disposed = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                                    "--stage", "preflight", "--findings", "addressed", "--input", str(document))
                self.assertEqual(disposed.returncode, 0, marker + disposed.stderr)
                self.assertIn("BM_ATTACK", self.cli("complete").stderr)
            skipped = self.cli("tdd", "--slug", slug, "--phase", "red", "--behavior-id", "BM_SKIPPED",
                               "--from-evidence", reference, "--test-id", "test_batch.T.test_skip")
            self.assertEqual(skipped.returncode, 2, marker)
        self.assertEqual(counter.read_text(), "xxx", marker)
        self.assertIn("BM_SKIPPED", self.cli("complete").stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
