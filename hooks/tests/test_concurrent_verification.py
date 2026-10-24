#!/usr/bin/env python3
"""Concurrent verification recording through the public verify CLI (#211 slice 1)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.tests.support import record_context_forge  # noqa: E402
from hooks.tests.test_workflow_hooks import WORKFLOW, HookHarness  # noqa: E402

# A git clean filter that holds a typed gate's tree capture until that run is released,
# so overlapping gates complete in an order the test chooses. Runs without HOLD_RUN pass.
HOLD = '''
import os, sys, time
from pathlib import Path
data = sys.stdin.buffer.read()
run = os.environ.get("HOLD_RUN")
if run:
    markers = Path(sys.argv[1])
    (markers / f"held-{run}").write_text("1", encoding="utf-8")
    deadline = time.monotonic() + 120
    while not (markers / f"release-{run}").exists():
        if time.monotonic() > deadline:
            sys.exit(99)
        time.sleep(0.01)
sys.stdout.buffer.write(data)
'''

GATE = ("quality-gate", True)


class ConcurrentVerificationTests(HookHarness):
    def setUp(self) -> None:
        super().setUp()
        self.markers = self.tmp / "markers"
        self.markers.mkdir()
        (self.repo / ".gitattributes").write_text("app.py filter=hold\n", encoding="utf-8")
        self.git("add", ".gitattributes")
        self.git("commit", "-q", "-m", "hold filter attribute")
        hold = self.tmp / "hold.py"
        hold.write_text(HOLD, encoding="utf-8")
        self.git("config", "filter.hold.clean", f'"{sys.executable}" "{hold}" "{self.markers}"')
        self.launched: list[str] = []
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        """Release every held run, then wait for each process this test started, however it ended."""
        for run in self.launched:
            (self.markers / f"release-{run}").write_text("1", encoding="utf-8")
        for process in self.processes:
            if process.poll() is None:
                try:
                    process.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=30)
        super().tearDown()

    def advance_to_verification(self, slug: str = "concurrent") -> str:
        begun = self.state("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        self.record_preflight_evidence(slug, wid)
        self.owner_phase("tdd", "not-required")
        return slug

    def held(self, slug: str, base: str = "HEAD") -> tuple[str, subprocess.Popen[str]]:
        """A typed gate started and held at its tree capture until released."""
        run = f"run{len(self.launched) + 1}"
        self.launched.append(run)
        process = subprocess.Popen(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug,
             "--kind", "quality-gate", "--base-ref", base],
            cwd=ROOT, env={**self.env, "HOLD_RUN": run}, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.processes.append(process)
        deadline = time.monotonic() + 60
        while not (self.markers / f"held-{run}").exists():
            if process.poll() is not None:
                self.fail(f"the gate exited before it was held: {process.communicate()}")
            self.assertLess(time.monotonic(), deadline, f"{run} was never held")
            time.sleep(0.01)
        return run, process

    def release(self, run: str, process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
        (self.markers / f"release-{run}").write_text("1", encoding="utf-8")
        out, err = process.communicate(timeout=120)
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

    def typed(self, slug: str, base: str = "HEAD") -> subprocess.CompletedProcess[str]:
        return self.state("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", base)

    def status(self) -> dict[str, object]:
        result = self.state("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def latest_runs(self) -> list[dict[str, object]]:
        evidence_id = self.status().get("verificationLatestEvidence")
        if not isinstance(evidence_id, str):
            return []
        result = self.state("evidence", "--full", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["document"]["runs"]

    def recorded(self) -> list[tuple[str, bool]]:
        return [(str(run["kind"]), bool(run["valid"])) for run in self.latest_runs()]

    def test_overlapping_typed_gates_retain_completion_order(self) -> None:
        marker = "TYPED_COMPLETION_ORDER_LOST"
        slug = self.advance_to_verification()
        passing, passing_process = self.held(slug)
        failing, failing_process = self.held(slug, "does-not-exist")
        self.assertEqual(self.release(failing, failing_process).returncode, 2, marker)
        self.assertEqual(self.release(passing, passing_process).returncode, 0, marker)
        self.assertEqual((self.recorded(), self.status()["verification"]), ([("quality-gate", False), GATE], "passed"), marker)

        (self.repo / "app.py").write_text("value = 1\nedited = 1\n", encoding="utf-8")
        passing, passing_process = self.held(slug)
        failing, failing_process = self.held(slug, "does-not-exist")
        self.assertEqual(self.release(passing, passing_process).returncode, 0, marker)
        self.assertEqual(self.release(failing, failing_process).returncode, 2, marker)
        state = self.status()
        self.assertEqual(self.recorded()[2:], [GATE, ("quality-gate", False)], marker)
        self.assertEqual((state["verification"], "qualityGateManifestId" in state), ("pending", False), marker)

    def test_an_edit_during_a_held_typed_gate_is_retained_invalid(self) -> None:
        marker = "STALE_TYPED_RESULT_PUBLISHED"
        slug = self.advance_to_verification()
        run, process = self.held(slug)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        finished = self.release(run, process)

        state = self.status()
        self.assertEqual((finished.returncode, state["verification"], self.recorded()), (2, "pending", [("quality-gate", False)]), marker)
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertIn("changed during the quality-gate run", str(self.latest_runs()[-1].get("bindingError")), marker)

    def test_a_replaced_workflow_refuses_a_held_typed_gate(self) -> None:
        marker = "STALE_RESULT_RECORDED_INTO_REPLACEMENT"
        slug = self.advance_to_verification("first")
        run, process = self.held(slug)
        begun = self.state("begin", "--slug", "second")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        finished = self.release(run, process)

        state = self.status()
        self.assertEqual((finished.returncode, state["slug"], state["verification"]), (2, "second", "pending"), marker)
        self.assertNotIn("verificationLatestEvidence", state, marker)

    def test_an_interrupted_typed_gate_leaves_committed_evidence_intact(self) -> None:
        marker = "COMMITTED_EVIDENCE_LOST_ON_INTERRUPT"
        slug = self.advance_to_verification()
        self.assertEqual(self.typed(slug).returncode, 0)
        committed = self.status()
        (self.repo / "app.py").write_text("value = 1\nedited = 1\n", encoding="utf-8")
        run, process = self.held(slug)
        process.kill()
        process.communicate(timeout=30)
        (self.markers / f"release-{run}").write_text("1", encoding="utf-8")

        state = self.status()
        self.assertEqual((state["verification"], state["verificationLatestEvidence"], self.recorded()),
                         ("passed", committed["verificationLatestEvidence"], [GATE]), marker)

    def test_an_invalid_typed_run_drops_the_binding(self) -> None:
        marker = "INVALID_TYPED_BINDING_KEPT"
        slug = self.advance_to_verification()
        self.assertEqual(self.typed(slug).returncode, 0)
        self.assertIn("qualityGateManifestId", self.status())
        invalid = self.typed(slug, base="does-not-exist")
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        state = self.status()
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertNotIn("qualityGateManifestId", state, marker)
        self.assertEqual(state["verification"], "pending", marker)

    def test_an_edit_restored_before_completion_cannot_revive_readiness(self) -> None:
        marker = "READINESS_REVIVED_AFTER_EDIT_RESTORE"
        slug = self.advance_to_verification()
        self.assertEqual(self.typed(slug).returncode, 0)
        original = (self.repo / "app.py").read_text(encoding="utf-8")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        (self.repo / "app.py").write_text(original, encoding="utf-8")

        state = self.status()
        self.assertNotIn("qualityGateManifestId", state, marker)
        self.assertEqual((state["verification"], state["nextAction"]), ("pending", "verification"), marker)
        rerun = self.typed(slug)
        self.assertEqual((rerun.returncode, "reused" in rerun.stdout), (0, False), marker)
        self.assertEqual(self.status()["nextAction"], "code-review", marker)


if __name__ == "__main__":
    unittest.main()
