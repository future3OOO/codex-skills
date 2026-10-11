#!/usr/bin/env python3
"""Adversarial attacks on the finding/design authority surfaces (issue #179).

Every probe drives the real workflow CLI over a real SQLite ledger in a scratch
fixture repository. One TestCase class per mapped Behavior Map item so each
RED/GREEN cycle targets exactly one recorded surface.
"""
from __future__ import annotations

import json
import hashlib
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
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.tests.support import approve_preflight
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

    def items(self):
        state = self.status()
        evidence = state.get("tddEvidence") or state["preflightEvidence"]
        document = self.ok("evidence", "--full", "--evidence-id", evidence)["document"]
        return [{k: v for k, v in item.items() if k != "comparison"} for item in document["behaviorMap"]]

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
            "expected": "app.value is 2",
            "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}],
        }]

    def record_preflight(self, slug: str, wid: str, behavior_map: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        payload = self.json_file("preflight.json", build_document("attack", behavior_map=behavior_map))
        approve_preflight(self.repo, json.loads(payload.read_text()))
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
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        run = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                              "--slug", slug, "--behavior-id", behavior_id,
                              "--", sys.executable, "-m", "unittest", "test_attack_probe"],
                             cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, repr(run.stdout + run.stderr))

    def fixed_args(self) -> tuple[str, ...]:
        return ("--finding", "SPEC-1", "--fixed", "--reason",
                "The constant initializer supplied 1 to readers; setting it to 2 corrects the read surface.")

    def open_pytest_pass(self, slug: str, marker: str) -> str:
        wid = self.begin(slug)
        self.ok("record", "advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        owned = self.record_preflight(slug, wid, [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "requested behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2",
            "sourceRefs": [],
        }])
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        return wid

    def mapped_tdd(self, slug: str, phase: str, command: list[str],
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--behavior-id", "BM_ATTACK",
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
        refused = self.refused_unchanged(marker, lambda: self.cli(
            "record", "advisor-disposition", *self.fixed_args()))
        self.assertIn("comparison", refused.stderr, marker)
        self.assertEqual(entry["kind"], "behavioral", marker)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid, "--stage", "preflight",
                "--findings", "addressed", *self.fixed_args())
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


    def test_recurrence_preserves_mechanism_and_retries_do_not_escalate(self) -> None:
        marker = "RECURRENCE_HISTORY_LOST"
        wid = self.begin("pending-retry")
        finding = {**self.CAPTURED, "id": "SPEC-1"}
        first = self.accept(wid, [finding])
        original = first["advisorPreflight"]["intakeEvidence"]
        self.assertEqual(self.record_preflight("pending-retry", wid,
                         self.owned_map(original, marker="VALUE_NOT_TWO")).returncode, 0)
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ok("record", "advisor-disposition", *self.fixed_args())
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
        closed = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                           "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
        self.assertEqual(closed.returncode, 0, marker + closed.stderr)


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
        items = self.items()
        items[0]["sourceRefs"].append({"type": "finding", "evidenceId": ref, "id": "SPEC-1"})
        self.ok("record", "tdd-map", "--slug", "pending-retry", "--workflow-id", wid,
                "--input", str(self.json_file("owner.json", {"items": items})))
        (self.repo / "app.py").write_text("value = 2\n")
        self.assertEqual(self.mapped_tdd("pending-retry", "green", [sys.executable, "-m", "unittest", "test_attack_probe"]).returncode, 0)
        result = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
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
                "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
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
        option = {"rejected-with-evidence": "--rejected", "report-only": "--report-only", "fixed": "--fixed"}[status]
        return self.cli("record", "advisor-disposition", "--finding", finding["id"], option,
                        "--reason", "test -f app.py exited 0: the claimed absent file exists.")

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
        (self.repo / "app.py").write_text("value = 1\n")
        state = self.accept(wid, [finding])
        for recurrence in range(2):
            ref = state["advisorPreflight"]["intakeEvidence"]
            items = self.items()
            items[0]["sourceRefs"].append({"type": "finding", "evidenceId": ref, "id": "SPEC-1"})
            update = self.json_file("owner.json", {"items": items})
            self.ok("record", "tdd-map", "--slug", "pending-retry", "--workflow-id", wid, "--input", str(update))
            (self.repo / "app.py").write_text("value = 2\n")
            self.assertEqual(self.mapped_tdd("pending-retry", "green", [sys.executable, "-m", "unittest", "test_attack_probe"]).returncode, 0)
            self.ok("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                    "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
            (self.repo / "app.py").write_text("value = 1\n")
            state = self.accept(wid, [finding])
        return state

    def test_agent_paths_are_root_relative_not_suffix_matches(self) -> None:
        from hooks.lib.workflow_state import same_agent
        for left, right, expected in (
            ("/root/retry-fixture", "retry-fixture", True),
            ("/root/a/repair", "a/repair", True),
            ("/root/a/repair", "repair", False),
            ("/root/b/repair", "repair", False),
            ("lead-thread-id", "lead-thread-id", True),
            ("lead-thread-id", "other-thread-id", False),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(same_agent(left, right), expected)
                self.assertEqual(same_agent(right, left), expected)

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
        record_context_forge(self.repo, self.tmp)
        # The gate passes and runs no comparison; the lead's rerun reports the recurring defect's failing comparison.
        gate = self.cli("verify", "--slug", "pending-retry", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual((gate.returncode, self.status()["verification"]), (0, "passed"), marker + gate.stderr)
        probe = [sys.executable, "-m", "unittest", "test_attack_probe"]
        self.assertEqual(self.mapped_tdd("pending-retry", "green", probe).returncode, 2, marker)
        def dispatch(tool: str, target: str | None = None) -> str:
            return subprocess.run([sys.executable, str(ROOT / "hooks/rcf-intake-gate.py")], env=self.env, text=True,
                capture_output=True, input=json.dumps({"tool_name": tool, "session_id": self.env["CODEX_THREAD_ID"],
                                                       "cwd": str(self.repo), "tool_input": {"target": target}})).stdout
        # A fresh reviewer cannot be spawned over the recurring failure; the retained one stays continuable once named.
        self.assertIn('"deny"', dispatch("spawn_agent"), marker)
        # The named reviewer reports the recurring defect it observes; its review names the owner.
        self.ok("record", "review", "--slug", "pending-retry", "--workflow-id", wid, "--review-context-id", "/root/retry-fixture",
                "--input", str(self.json_file("named.json", {"findings": [{**self.CAPTURED, "id": "SPEC-1"}]})))
        self.assertEqual(self.status()["findingStates"][-1].get("repairOwner"),
                         {"implementerContextId": "/root/retry-fixture", "reviewerContextId": self.env["CODEX_THREAD_ID"]}, marker)
        # obs6: the lead continues its reviewer by the relative name spawn_agent returned beside the canonical one
        for target, denied in (("retry-fixture", False), ("/root/retry-fixture", False), ("fixture", True)):
            self.assertEqual('"deny"' in dispatch("followup_task", target), denied, f"{marker}: {target}")
        # The retained reviewer's repair turns the rerun comparison green; the lead then certifies it.
        (self.repo / "app.py").write_text("value = 2\n")
        self.assertEqual(self.mapped_tdd("pending-retry", "green", probe).returncode, 0, marker)
        self.ok("verify", "--slug", "pending-retry", "--kind", "quality-gate", "--base-ref", "HEAD")
        # R-25: the lead's review names the implementer as it continued it, relatively
        review = self.json_file("lead-review.json", {"findings": [], "implementationContextId": "retry-fixture"})
        refused = self.cli("record", "review", "--slug", "pending-retry", "--workflow-id", wid,
                           "--review-context-id", "/root/retry-fixture", "--input", str(review))
        self.assertIn("--review-context-id", refused.stderr, marker)
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
        self.assertEqual(self.record_preflight("pending-retry", wid, owned).returncode, 2, marker)
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
        for key in ("findingStates", "preflightEvidence", "tddEvidence"):
            self.assertEqual(after[key], before[key], marker)
        self.assertEqual(checkpoint_channels(self.repo, self.env, "final-review").get("finding-ledger", []), ledger, marker)
        closed = self.cli("record", "advisor-disposition", "--slug", "pending-retry", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
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
        self.assertEqual(self.mapped_tdd("pending-retry", "green", [sys.executable, "-m", "unittest", "test_attack_probe"]).returncode, 0)
        self.refused_unchanged("REFUSAL_MUTATED_HISTORY", lambda: self.response(wid, [finding], stage="final"))
        self.ready(wid)
        refreshed = self.status()
        replay = self.accept(wid, [finding], stage="final")
        self.assertEqual(replay["findingStates"], first["findingStates"], marker)
        self.assertEqual(replay["finalReview"]["intakeEvidence"], original, marker)
        for key in ("activeCandidateTree", "verificationEvidence", "qualityGateEvidence", "codeReviewEvidence"):
            self.assertEqual(replay[key], refreshed[key], marker)
        self.assertNotEqual(replay["activeCandidateTree"], first["activeCandidateTree"], marker)
        closed = self.close_finding(wid, original, finding, stage="final", status="report-only")
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
        self.drive_attack_green("pending-retry", "VALUE_NOT_TWO")
        self.ready(final_wid)
        changed_verdict = self.accept(final_wid, [note, {**note, "id": "NEW", "material": True}], stage="final")
        self.assertEqual(len(changed_verdict["findingStates"]), 2, marker)

    def test_observations_retain_response_identity(self) -> None:
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
        self.assertEqual(sorted(d["sha256"] for d in observations),
                         sorted(hashlib.sha256(raw.encode()).hexdigest() for raw in raws), marker)
        self.assertTrue(all("raw" not in d and d["findings"] == [self.CAPTURED] for d in observations), marker)

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
        self.assertEqual(self.close_finding(wid, original, a, stage="final-review").returncode, 0,
                         "EXECUTED_RECEIPT_REFUSED")
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
            "expected": "app.value is 2",
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
                         "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
        self.assertEqual(early.returncode, 2, marker + ": " + early.stdout + early.stderr)
        self.assertIn("SPEC-1", early.stderr, marker)
        self.assertIn("comparison", early.stderr, marker)
        self.drive_attack_green("fixed-green", marker)
        closed = self.cli("record", "advisor-disposition", "--slug", "fixed-green", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        states = self.status()["findingStates"]
        self.assertEqual(states[0]["status"], "fixed", marker)




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
        self.assertIn("unrecognized arguments", refused.stderr, marker)
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
        self.env.update(env)
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
            "expected": "app.value is 2", "sourceRefs": [],
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
                             "items": [*self.items(), {
                                 "id": "BM_NOTE", "kind": "contract", "basis": "review finding attack",
                                 "behavior": "the note is exposed", "seam": "fixture app module",
                                 "expected": "app.note is present",
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
                   "--behavior-id", "BM_NOTE", "--",
                   sys.executable, "-m", "unittest", "test_note_probe"]
        red = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 2, marker + ": " + red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\nnote = 'late attack'\n", encoding="utf-8")
        green = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        # A fixed finding's owning attack cannot be silently un-owned afterwards.
        fixed = self.cli("record", "advisor-disposition", "--finding", "SPEC-1", "--fixed",
                         "--reason", "The owning note-read comparison records the original and repaired results.")
        self.assertEqual(fixed.returncode, 0, marker + ": " + fixed.stdout + fixed.stderr)
        omit = self.cli("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                        str(self.json_file("omit-owner.json", {
                            "items": [item for item in self.items() if item["id"] != "BM_NOTE"],
                        })))
        self.assertEqual(omit.returncode, 0, marker + ": " + omit.stdout + omit.stderr)
        self.assertIn("BM_NOTE", {item["id"] for item in self.items()}, marker)

        self.assertEqual(self.cli("tdd", "--slug", slug, "--behavior-id", "BM_MAIN", "--", sys.executable,
                                  "-m", "unittest", "test_attack_probe").returncode, 0)

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
                         {"comparison": shlex.join(command[command.index("--") + 1:])})
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
                          "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        payload = checkpoint_channels(self.repo, self.env, "final-review")
        ledger = payload.get("finding-ledger")
        self.assertIsInstance(ledger, list, marker)
        [entry] = [item for item in ledger if item.get("findingId") == "SPEC-1"]
        self.assertEqual(entry.get("claim"), claim, marker)
        self.assertEqual(entry.get("status"), "fixed", marker)
        self.assertEqual(entry.get("kind"), "behavioral", marker)
        [owner] = entry.get("owners") or []
        self.assertEqual((owner.get("id"), owner.get("seam")),
                         ("BM_ATTACK", "fixture app module"), marker)


    def test_ledger_carries_the_dispositions_linked_results(self) -> None:
        wid = self.begin("ledger-measurement")
        intake = self.behavioral_intake("ledger-measurement", wid, "app.value must be two")
        self.assertEqual(self.record_preflight("ledger-measurement", wid, self.owned_map(intake, marker="VALUE_NOT_TWO")).returncode, 0)
        self.drive_attack_green("ledger-measurement", "VALUE_NOT_TWO")
        before = self.status()["tddEvidence"]
        self.ok("record", "advisor-disposition", *self.fixed_args())
        [entry] = checkpoint_channels(self.repo, self.env, "final-review")["finding-ledger"]
        self.assertEqual(entry["status"], "fixed")
        self.assertEqual(self.status()["tddEvidence"], before)
        self.assertTrue(entry["owners"][0]["executedCommands"]["comparison"])


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
        self.assertIn("unittest test_attack_probe", str(owner.get("executedCommands", {}).get("comparison")), marker)


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
                                  "--slug", "proof-scope", "--behavior-id", "BM_ATTACK",
                                  "--", sys.executable, "-m", "unittest", "outside_repo_probe"],
                                 cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("repository", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")
        (self.repo / "test_inside_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        red = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                              "--slug", "proof-scope", "--behavior-id", "BM_ATTACK",
                              "--", sys.executable, "-m", "unittest", "test_inside_probe"],
                             cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 2, marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class PytestOptionValueStaysOptionValue(AttackHarness):
    def test_separate_value_pytest_options_reach_the_mapped_assertion(self) -> None:
        marker = "PYTEST_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-opts", marker)
        self.write_probe(marker)
        surface = [sys.executable, "-m", "pytest", "--maxfail", "1", "--tb", "short",
                   "--durations", "10", "--color", "no",
                   "--basetemp", str(self.tmp / "pt-basetemp"), "test_probe.py"]
        red = self.mapped_tdd("pytest-opts", "red", surface)
        self.assertEqual(red.returncode, 2,
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
        self.assertIn("repository", tail, marker)
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
        self.assertEqual(red.returncode, 2,
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
        self.assertEqual(red.returncode, 2,
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
                for field in ("workflowId", "preflightEvidence", "advisorPreflight"):
                    self.assertEqual(after[field], before[field], marker)
                document = self.ok("evidence", "--full", "--evidence-id", after["tddEvidence"])["document"]
                attempt = document["runs"][-1]
                self.assertFalse(attempt["valid"], marker)
                self.assertTrue(all(arm["outcome"] == "incomplete" for arm in attempt["arms"]), marker)
                self.assertTrue(all(marker not in arm["output"] for arm in attempt["arms"]), marker)









class WorkflowRecovery(AttackHarness):
    """Old/candidate operations through the existing public runtime and ledger."""

    def settled(self) -> tuple[str, str]:
        slug = "recovery"
        wid = self.open_pytest_pass(slug, "VALUE_UNCORRECTED")
        self.drive_attack_green(slug, "VALUE_UNCORRECTED")
        return slug, wid

    def add_claim(self, slug: str, wid: str, identifier: str) -> None:
        item = self.owned_map("unused", marker="SECOND_OPERATION_WRONG")[0]
        item.update(id=identifier, kind="preservation", sourceRefs=[])
        document = self.json_file("add.json", {
            "items": [*self.items(), item],
        })
        self.ok("record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(document))

    def test_new_obligation_retains_measurements_and_blocks_closure(self) -> None:
        marker = "OBLIGATION_DISCARDED_RECEIPTS"
        slug, wid = self.settled()
        self.ok("verify", "--slug", slug, "--", "git", "diff", "--check")
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


    def test_manifest_sampling_errors_refuse_without_ledger_changes(self) -> None:
        slug = "sampling-error"
        wid = self.begin(slug)
        intake = self.behavioral_intake(slug, wid, "app.value must be two")
        self.assertEqual(self.record_preflight(slug, wid, self.owned_map(intake, marker="VALUE_WRONG")).returncode, 0)
        self.drive_attack_green(slug, "VALUE_WRONG")
        executed = self.cli("verify", "--slug", slug, "--", "git", "diff", "--check")
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        item = self.owned_map("unused", marker="MISSING")[0]
        item.update(id="BM_ADDITIONAL", kind="preservation", sourceRefs=[])
        addition = self.json_file("add.json", {"items": [*self.items(), item]})
        commands = [
            ["record", "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(addition)],
            ["record", "advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
             "--findings", "addressed", *self.fixed_args()],
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
            self.assertEqual(self.ok("history"), history, "SAMPLING_ERROR_ESCAPED")
            self.assertEqual(refused.returncode, 2, "SAMPLING_ERROR_ESCAPED" + refused.stderr)
            expected = "index file smaller than expected" if command[1] == "tdd-map" else "no current comparison"
            self.assertIn(expected, refused.stderr)
            self.assertNotIn("Traceback", refused.stderr)

    def test_typed_only_and_explicit_failed_command_replacement(self) -> None:
        marker = "VERIFICATION_CORRECTION_REFUSED"
        slug, _ = self.settled()
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(self.status()["verification"], "passed", marker)
        failed = self.cli("verify", "--slug", slug, "--", sys.executable, "-c", "import missing_module")
        self.assertEqual(failed.returncode, 2)
        receipt = json.loads(failed.stdout.splitlines()[-1])
        reference = receipt["evidenceId"] + ":1"
        fixed = self.cli("verify", "--slug", slug, "--replaces", reference,
                         "--reason", "Correct the misspelled test module",
                         "--", sys.executable, "-c", "import app")
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
        self.assertFalse(document["behaviorMap"][0]["comparison"]["valid"], "FAILED_RECHECK_STILL_GREEN")
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
        evidence = self.status()["tddEvidence"]
        (self.repo / "app.py").write_text("value = 1\n")
        stale = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", *self.fixed_args())
        self.assertEqual(stale.returncode, 2)
        self.assertIn("current comparison", stale.stderr)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "pending")
        (self.repo / "app.py").write_text("value = 2\n")
        disposed = self.cli("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
                            "--findings", "addressed", *self.fixed_args())
        self.assertEqual(disposed.returncode, 0, marker + disposed.stderr)
        self.assertEqual(self.status()["tddEvidence"], evidence, marker)
        self.assertEqual(self.status()["advisorPreflight"]["findings"], "addressed")


    def test_summary_reports_current_binding_after_source_drift(self) -> None:
        slug, _ = self.settled()
        self.ok("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        (self.repo / "app.py").write_text("value = 3\n")
        summary = self.ok_text("summary")
        self.assertIn("verification=pending", summary, "STALE_RECOVERY_ADVERTISED_SUCCESS")
        self.assertIn("next=tdd", summary, "STALE_RECOVERY_MISROUTED")  # stale comparisons route to tdd
        self.assertIn("quality-gate-tree-stale", summary, "STALE_RECOVERY_ADVERTISED_SUCCESS")
        receipt = self.ok("pause", "--slug", slug, "--workflow-id", self.status()["workflowId"],
                          "--reason", "Inspect current recovery")
        self.assertEqual(receipt["nextAction"], "tdd", "STALE_RECOVERY_MISROUTED")






if __name__ == "__main__":
    unittest.main(verbosity=2)
