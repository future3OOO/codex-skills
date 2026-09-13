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

# The verification command. It lives outside the fixture repository so its markers
# never touch the reviewable tree, and its text stays constant per label: the run
# identity and outcome travel in the environment, which is what lets two runs of
# one command overlap with opposite outcomes.
BLOCKING = '''
import os, sys, time
from pathlib import Path
markers, label = Path(sys.argv[1]), sys.argv[2]
run = os.environ["S211_RUN"]
with (markers / f"executed-{label}").open("a", encoding="utf-8") as handle:
    handle.write(run + "\\n")
(markers / f"started-{run}").write_text("1", encoding="utf-8")
deadline = time.monotonic() + 120
while not (markers / f"release-{run}").exists():
    if time.monotonic() > deadline:
        sys.exit(99)
    time.sleep(0.01)
sys.exit(int(os.environ["S211_EXIT"]))
'''

# A git clean filter that holds the typed gate's own tree capture until released,
# so a generic completion can land while the gate child is provably in flight.
HOLD = '''
import sys, time
from pathlib import Path
markers = Path(sys.argv[1])
data = sys.stdin.buffer.read()
(markers / "gate-held").write_text("1", encoding="utf-8")
deadline = time.monotonic() + 120
while not (markers / "gate-release").exists():
    if time.monotonic() > deadline:
        sys.exit(99)
    time.sleep(0.01)
sys.stdout.buffer.write(data)
'''

GATE = ("quality-gate", "quality-gate", True)


class ConcurrentVerificationTests(HookHarness):
    def setUp(self) -> None:
        super().setUp()
        self.markers = self.tmp / "markers"
        self.markers.mkdir()
        self.blocking = self.tmp / "blocking.py"
        self.blocking.write_text(BLOCKING, encoding="utf-8")
        self.launched: list[str] = []
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        """End every process this test started, however the test ended.

        An assertion failing between start_blocking() and release() leaves the
        command waiting for a marker that now never arrives, and the runner
        waiting on it. Releasing every launched run is what actually stops the
        command: it runs in its own session, so terminating the runner would
        orphan it until its own deadline instead. Every command this suite
        starts waits on one of these markers, so the release is what ends them;
        both waits are bounded and the second follows a kill, which is the bound
        against a runner wedged on something else. The fixture is removed only
        afterwards, so nothing is still writing into the directory as it goes.
        """
        for name in (*(f"release-{run}" for run in self.launched), "gate-release"):
            (self.markers / name).write_text("1", encoding="utf-8")
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
        self.record_gate_evidence(slug, wid)
        self.owner_phase("implementation", "passed")
        return slug

    def verify(self, slug: str, *extra: str, env_extra: dict[str, str] | None = None) -> subprocess.Popen[str]:
        """The one place this suite starts a process, so tearDown sees them all."""
        process = subprocess.Popen(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug, *extra],
            cwd=ROOT, env={**self.env, **(env_extra or {})}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.processes.append(process)
        return process

    def launch(self, slug: str, label: str, exit_code: int) -> tuple[str, subprocess.Popen[str]]:
        run = f"run{len(self.launched) + 1}"
        self.launched.append(run)
        process = self.verify(
            slug, "--", sys.executable, str(self.blocking), str(self.markers), label,
            env_extra={"S211_RUN": run, "S211_EXIT": str(exit_code)},
        )
        return run, process

    def start_blocking(self, slug: str, label: str, *, exit_code: int = 0) -> tuple[str, subprocess.Popen[str]]:
        """A generic verify whose command has started and now waits to be released."""
        run, process = self.launch(slug, label, exit_code)
        self.await_marker(f"started-{run}", process)
        return run, process

    def release(self, run: str, process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
        (self.markers / f"release-{run}").write_text("1", encoding="utf-8")
        out, err = process.communicate(timeout=120)
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

    def generic(self, slug: str, label: str, *, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
        """A generic verify run to completion with no overlap."""
        run, process = self.launch(slug, label, exit_code)
        return self.release(run, process)

    def typed(self, slug: str, base: str = "HEAD") -> subprocess.CompletedProcess[str]:
        return self.state("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", base)

    def await_marker(self, name: str, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 60
        while not (self.markers / name).exists():
            if process.poll() is not None:
                out, err = process.communicate()
                self.fail(f"the runner exited before {name} appeared: {out}{err}")
            self.assertLess(time.monotonic(), deadline, f"{name} never appeared")
            time.sleep(0.01)

    def status(self) -> dict[str, object]:
        result = self.state("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def latest_runs(self) -> list[dict[str, object]]:
        evidence_id = self.status().get("verificationLatestEvidence")
        if not isinstance(evidence_id, str):
            return []
        result = self.state("evidence", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["document"]["runs"]

    def recorded(self) -> list[tuple[str, str, bool]]:
        """(kind, label, valid) per retained run, in recorded order."""
        return [
            (
                str(run["kind"]),
                "quality-gate" if run["kind"] == "quality-gate" else str(run["command"]).split()[-1],
                bool(run["valid"]),
            )
            for run in self.latest_runs()
        ]

    def executions(self, label: str) -> int:
        path = self.markers / f"executed-{label}"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def test_a_typed_completion_during_a_generic_run_records_both(self) -> None:
        marker = "CONCURRENT_GENERIC_COMPLETION_REFUSED"
        slug = self.advance_to_verification()
        run, process = self.start_blocking(slug, "a")
        typed = self.typed(slug)
        self.assertEqual(typed.returncode, 0, typed.stdout + typed.stderr)

        finished = self.release(run, process)

        self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        state = self.status()
        self.assertEqual(state["verification"], "passed", marker)
        self.assertIn("qualityGateEvidence", state, marker)
        self.assertIn("qualityGateManifestId", state, marker)
        self.assertEqual(self.recorded(), [GATE, ("generic", "a", True)], marker)
        self.assertEqual(self.executions("a"), 1, marker)

    def test_a_generic_completion_during_the_typed_gate_records_both(self) -> None:
        marker = "CONCURRENT_TYPED_COMPLETION_REFUSED"
        (self.repo / ".gitattributes").write_text("app.py filter=hold\n", encoding="utf-8")
        self.git("add", ".gitattributes")
        self.git("commit", "-q", "-m", "hold filter attribute")
        slug = self.advance_to_verification()
        hold = self.tmp / "hold.py"
        hold.write_text(HOLD, encoding="utf-8")
        self.git("config", "filter.hold.clean", f'"{sys.executable}" "{hold}" "{self.markers}"')
        typed = self.verify(slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.await_marker("gate-held", typed)
        generic = self.generic(slug, "a")
        self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)

        (self.markers / "gate-release").write_text("1", encoding="utf-8")
        out, err = typed.communicate(timeout=120)

        self.assertEqual(typed.returncode, 0, f"{marker}: {out}{err}")
        state = self.status()
        self.assertEqual(state["verification"], "passed", marker)
        self.assertIn("qualityGateManifestId", state, marker)
        self.assertEqual(self.recorded(), [("generic", "a", True), GATE], marker)
        self.assertEqual(self.executions("a"), 1, marker)

    def test_two_generic_commands_overlapping_record_both_in_either_order(self) -> None:
        marker = "CONCURRENT_GENERIC_PAIR_LOST"
        slug = self.advance_to_verification()
        first, first_process = self.start_blocking(slug, "a")
        second, second_process = self.start_blocking(slug, "b")
        later_started = self.release(second, second_process)
        earlier_started = self.release(first, first_process)
        for finished in (later_started, earlier_started):
            self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        self.assertEqual(self.recorded(), [("generic", "b", True), ("generic", "a", True)], marker)

        third, third_process = self.start_blocking(slug, "c")
        fourth, fourth_process = self.start_blocking(slug, "d")
        earlier_started = self.release(third, third_process)
        later_started = self.release(fourth, fourth_process)
        for finished in (earlier_started, later_started):
            self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")

        self.assertEqual(self.recorded(), [
            ("generic", "b", True), ("generic", "a", True), ("generic", "c", True), ("generic", "d", True),
        ], marker)
        self.assertEqual([self.executions(label) for label in "abcd"], [1, 1, 1, 1], marker)
        self.assertEqual(self.status()["verification"], "passed", marker)

    def test_a_distinct_failed_command_stays_pending_until_its_own_rerun(self) -> None:
        marker = "FAILED_COMMAND_NOT_HELD_PENDING"
        slug = self.advance_to_verification()
        failing, failing_process = self.start_blocking(slug, "fail", exit_code=1)
        passing, passing_process = self.start_blocking(slug, "pass")
        passed = self.release(passing, passing_process)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        failed = self.release(failing, failing_process)

        self.assertEqual(failed.returncode, 2, f"{marker}: {failed.stdout}")
        self.assertEqual(self.status()["verification"], "pending", marker)
        self.assertEqual(self.recorded(), [("generic", "pass", True), ("generic", "fail", False)], marker)

        rerun = self.generic(slug, "pass")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(self.status()["verification"], "pending", marker)

        rerun = self.generic(slug, "fail", exit_code=0)
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(self.status()["verification"], "passed", marker)
        self.assertEqual(self.recorded(), [
            ("generic", "pass", True), ("generic", "fail", False),
            ("generic", "pass", True), ("generic", "fail", True),
        ], marker)

    def test_same_command_overlap_follows_recorded_completion_order(self) -> None:
        marker = "SAME_COMMAND_LATEST_NOT_BY_COMPLETION"
        slug = self.advance_to_verification()
        failing, failing_process = self.start_blocking(slug, "same", exit_code=1)
        passing, passing_process = self.start_blocking(slug, "same")
        self.release(failing, failing_process)
        finished = self.release(passing, passing_process)
        self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        self.assertEqual(self.status()["verification"], "passed", marker)
        self.assertEqual(self.recorded(), [("generic", "same", False), ("generic", "same", True)], marker)

        passing, passing_process = self.start_blocking(slug, "same")
        failing, failing_process = self.start_blocking(slug, "same", exit_code=1)
        finished = self.release(passing, passing_process)
        self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        self.release(failing, failing_process)

        self.assertEqual(self.status()["verification"], "pending", marker)
        self.assertEqual(self.recorded(), [
            ("generic", "same", False), ("generic", "same", True),
            ("generic", "same", True), ("generic", "same", False),
        ], marker)
        self.assertEqual(self.executions("same"), 4, marker)

    def test_same_command_latest_follows_completion_not_launch_order(self) -> None:
        """Completion order decides where it disagrees with launch order.

        The pairs above complete in the order they were launched, so a rule that
        read launch order would satisfy them too. Here each pair completes in the
        reverse of its launch order, which is the only shape that tells the two
        rules apart: whichever run finishes last decides, and a launch-order rule
        would produce the opposite verification status in both halves.
        """
        marker = "LATEST_FOLLOWED_LAUNCH_ORDER"
        slug = self.advance_to_verification()
        passing, passing_process = self.start_blocking(slug, "same")
        failing, failing_process = self.start_blocking(slug, "same", exit_code=1)
        self.release(failing, failing_process)
        finished = self.release(passing, passing_process)

        self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        self.assertEqual(self.recorded(), [("generic", "same", False), ("generic", "same", True)], marker)
        self.assertEqual(self.status()["verification"], "passed", marker)

        failing, failing_process = self.start_blocking(slug, "same", exit_code=1)
        passing, passing_process = self.start_blocking(slug, "same")
        finished = self.release(passing, passing_process)
        self.assertEqual(finished.returncode, 0, f"{marker}: {finished.stderr}")
        self.release(failing, failing_process)

        self.assertEqual(self.recorded()[-2:], [("generic", "same", True), ("generic", "same", False)], marker)
        self.assertEqual(self.status()["verification"], "pending", marker)
        self.assertEqual(self.executions("same"), 4, marker)

    def test_an_edit_restored_before_a_fresh_typed_gate_publishes_only_the_measured_tree(self) -> None:
        """Restoring the edited content and re-gating publishes readiness for the tree at hand.

        Every recorded run measured the restored tree, so readiness describing it
        is honest. The proof that it is that tree, and not the edited one, is a
        further generic run on an unchanged tree: a binding taken against any
        other content is dropped rather than carried, so it would not survive.
        """
        marker = "READINESS_PUBLISHED_ACROSS_INVALIDATION"
        slug = self.advance_to_verification()
        run, process = self.start_blocking(slug, "a")
        original = (self.repo / "app.py").read_text(encoding="utf-8")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        (self.repo / "app.py").write_text(original, encoding="utf-8")
        typed = self.typed(slug)
        self.assertEqual(typed.returncode, 0, typed.stdout + typed.stderr)

        self.release(run, process)

        state = self.status()
        self.assertEqual(state["verification"], "passed", marker)
        self.assertIn("qualityGateManifestId", state, marker)
        self.assertEqual(self.generic(slug, "b").returncode, 0, marker)
        self.assertIn("qualityGateManifestId", self.status(), marker)
        self.assertEqual(self.status()["nextAction"], "code-review", marker)

    def test_a_run_on_a_tree_the_typed_gate_never_measured_drops_the_binding(self) -> None:
        """A preserved binding must describe the tree, not merely a past valid run.

        The typed gate measured the tree before the shell mutation; the generic
        run after it measures one tree and stays valid, so nothing in the run
        results themselves reports the drift. Carrying the earlier binding
        forward on that history alone would advertise a reviewable tree the gate
        never saw.
        """
        marker = "STALE_TYPED_BINDING_PRESERVED"
        slug = self.advance_to_verification()
        self.assertEqual(self.generic(slug, "a").returncode, 0)
        self.assertEqual(self.typed(slug).returncode, 0)
        self.assertIn("qualityGateManifestId", self.status())
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")

        rerun = self.generic(slug, "b")

        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        state = self.status()
        self.assertNotIn("qualityGateManifestId", state, marker)
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertEqual(state["nextAction"], "verification", marker)

    def test_a_relevant_edit_during_the_first_run_is_retained_invalid_not_published(self) -> None:
        marker = "STALE_RESULT_PUBLISHED_AFTER_EDIT"
        slug = self.advance_to_verification()
        run, process = self.start_blocking(slug, "a")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        self.release(run, process)

        state = self.status()
        self.assertEqual(state["verification"], "pending", marker)
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertEqual(self.recorded(), [("generic", "a", False)], marker)
        self.assertIn("app.py", str(self.latest_runs()[-1].get("bindingError")), marker)

    def test_a_shell_mutation_after_the_typed_gate_is_retained_invalid_not_published(self) -> None:
        marker = "STALE_RESULT_PUBLISHED_AFTER_DRIFT"
        slug = self.advance_to_verification()
        self.assertEqual(self.generic(slug, "a").returncode, 0)
        self.assertEqual(self.typed(slug).returncode, 0)
        self.assertEqual(self.status()["nextAction"], "code-review")
        run, process = self.start_blocking(slug, "b")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")

        self.release(run, process)

        state = self.status()
        self.assertEqual(state["verification"], "pending", marker)
        self.assertEqual(state["nextAction"], "verification", marker)
        self.assertEqual(self.recorded(), [("generic", "a", True), GATE, ("generic", "b", False)], marker)
        self.assertIn("app.py", str(self.latest_runs()[-1].get("bindingError")), marker)

    def test_a_replaced_workflow_refuses_the_completion(self) -> None:
        marker = "STALE_RESULT_RECORDED_INTO_REPLACEMENT"
        slug = self.advance_to_verification("first")
        run, process = self.start_blocking(slug, "a")
        begun = self.state("begin", "--slug", "second")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        finished = self.release(run, process)

        self.assertEqual(finished.returncode, 2, f"{marker}: {finished.stdout}")
        state = self.status()
        self.assertEqual(state["slug"], "second", marker)
        self.assertEqual(state["verification"], "pending", marker)
        self.assertNotIn("verificationLatestEvidence", state, marker)

    def test_an_invalid_typed_run_landing_mid_generic_is_not_revived(self) -> None:
        marker = "INVALID_TYPED_BINDING_REVIVED"
        slug = self.advance_to_verification()
        self.assertEqual(self.generic(slug, "a").returncode, 0)
        self.assertEqual(self.typed(slug).returncode, 0)
        self.assertIn("qualityGateManifestId", self.status())
        run, process = self.start_blocking(slug, "b")
        invalid = self.typed(slug, base="does-not-exist")
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)

        self.release(run, process)

        state = self.status()
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertNotIn("qualityGateManifestId", state, marker)
        self.assertEqual(state["verification"], "pending", marker)

    def test_an_edit_restored_before_completion_cannot_revive_readiness(self) -> None:
        marker = "READINESS_REVIVED_AFTER_EDIT_RESTORE"
        slug = self.advance_to_verification()
        self.assertEqual(self.generic(slug, "a").returncode, 0)
        self.assertEqual(self.typed(slug).returncode, 0)
        self.assertEqual(self.status()["nextAction"], "code-review")
        run, process = self.start_blocking(slug, "b")
        original = (self.repo / "app.py").read_text(encoding="utf-8")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        (self.repo / "app.py").write_text(original, encoding="utf-8")

        self.release(run, process)

        state = self.status()
        self.assertNotIn("qualityGateEvidence", state, marker)
        self.assertNotIn("qualityGateManifestId", state, marker)
        self.assertEqual(state["nextAction"], "verification", marker)

    def test_an_interrupted_runner_leaves_committed_evidence_intact(self) -> None:
        marker = "COMMITTED_EVIDENCE_LOST_ON_INTERRUPT"
        slug = self.advance_to_verification()
        self.assertEqual(self.generic(slug, "a").returncode, 0)
        run, process = self.start_blocking(slug, "b")
        process.kill()
        process.communicate(timeout=30)
        (self.markers / f"release-{run}").write_text("1", encoding="utf-8")
        typed = self.typed(slug)
        self.assertEqual(typed.returncode, 0, typed.stdout + typed.stderr)

        state = self.status()
        self.assertEqual(state["verification"], "passed", marker)
        self.assertIn("qualityGateManifestId", state, marker)
        self.assertEqual(self.recorded(), [("generic", "a", True), GATE], marker)


if __name__ == "__main__":
    unittest.main()
