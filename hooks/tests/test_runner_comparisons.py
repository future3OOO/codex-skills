"""Real public proof-runner comparisons, with independent Git and process state."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

from hooks.tests import test_tdd_repairs as harness
from hooks.tests.support import pending_behavior


class RunnerComparisonTests(unittest.TestCase):
    def setUp(self):
        self.case = harness.MappedTddRepairTests()
        self.case.setUp()
        self.case.workflow = Path(os.environ.get("ISSUE125_COMPARISON_ROOT", str(harness.ROOT))) / "skills/repo-production-workflow/scripts/workflow.py"
        self.addCleanup(self.case.tearDown)

    def item(self, value=1):
        return pending_behavior("BM_VALUE", behavior=f"value is {value}", expected=f"value is {value}")

    def operation(self, value=1, body=None, extra=()):
        case = self.case
        slug, _ = case.begin_with_map([self.item(value)])
        (case.repo / "test_value.py").write_text(body or (
            "import pathlib, unittest, app\n"
            "class Value(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(pathlib.Path(app.__file__).parent, pathlib.Path.cwd())\n"
            f"        self.assertEqual(app.value, {value}, 'VALUE_NOT_EXPECTED')\n"
        ))
        if value != 1:
            (case.repo / "app.py").write_text(f"value = {value}\n")
        return case.cli("tdd", "--repo", str(case.repo), "--slug", slug,
                        "--behavior-id", "BM_VALUE", *extra, "--", sys.executable,
                        "-m", "unittest", "test_value")

    def test_phase_free_operation(self):
        result = self.operation()
        self.assertEqual(result.returncode, 0,
                         "RUNNER_COMPARISON_ENTRYPOINT_MISSING: " + result.stdout + result.stderr)

    def test_empty_approved_list_needs_no_proof_bookkeeping(self):
        self.case.begin_with_map([])
        state = json.loads(self.case.cli("status", "--repo", str(self.case.repo)).stdout)
        self.assertEqual(state["nextAction"], "verification", "EMPTY_PROBE_LIST_STUCK")

    def test_inline_operation_executes_against_each_source(self):
        self.operation()
        (self.case.repo / "app.py").write_text("value = 2\n")
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", "import app; assert app.value == 2, app.value; print(app.value)")
        self.assertEqual(result.returncode, 0, "INLINE_OPERATION_REFUSED: " + repr(result.stdout + result.stderr))
        self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["failed", "passed"])

    def test_original_and_candidate_are_executed(self):
        result = self.operation(2)
        marker = "RECORDED_SOURCE_COMPARISON_MISSING: " + result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, marker)
        receipt = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(receipt.get("comparison"), "changed", marker)
        self.assertEqual([arm["outcome"] for arm in receipt["arms"]], ["failed", "passed"], marker)
        self.assertNotEqual(receipt["arms"][0]["sourceTree"], receipt["arms"][1]["sourceTree"], marker)

    def test_incomplete_execution_is_not_preservation(self):
        prefix = "import unittest, app\nclass Value(unittest.TestCase):\n"
        cases = {
            "equal failures": (prefix + "    def test_value(self): self.assertEqual(app.value, 9)\n", ()),
            "skipped": (prefix + "    @unittest.skip('unavailable')\n    def test_value(self): self.assertEqual(app.value, 1)\n", ()),
            "mixed skip": (prefix + "    def test_value(self): self.assertEqual(app.value, 1)\n    @unittest.skip('unavailable')\n    def test_second(self): self.assertEqual(app.value, 2)\n", ()),
            "setup": (prefix + "    def setUp(self): raise RuntimeError('setup error')\n    def test_value(self): self.assertEqual(app.value, 1)\n", ()),
            "import": ("import missing_production_module\n" + prefix, ()),
            "empty": ("import unittest\n", ()),
            "timeout": (prefix + "    def test_value(self):\n        import time\n        time.sleep(10)\n", ("--timeout", "0.1")),
        }
        for name, (body, extra) in cases.items():
            with self.subTest(name=name):
                result = self.operation(body=body, extra=extra)
                marker = "INCOMPLETE_COMPARISON_ACCEPTED: " + name + result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, marker)
                receipt = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(receipt["comparison"], "incomplete", marker)
                self.assertFalse(receipt["valid"], marker)

    def test_identical_comparison_reuses_execution(self):
        first = self.operation()
        second = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_value")
        marker = "COMPARISON_REUSE_MISSING: " + first.stdout + second.stdout + second.stderr
        self.assertEqual(second.returncode, 0, repr(marker))
        before, after = (json.loads(result.stdout.splitlines()[-1]) for result in (first, second))
        self.assertEqual(before["summaryId"], after["summaryId"], marker)
        self.assertTrue(after.get("reused"), marker)

    def test_shell_bookkeeping_does_not_invalidate_comparison(self):
        first = self.operation()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.case.env.update(SHLVL="9", PWD="/irrelevant-parent", OLDPWD="/previous", _="/usr/bin/env")
        second = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_value")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(first.stdout)["summaryId"], json.loads(second.stdout)["summaryId"],
                         "SHELL_BOOKKEEPING_INVALIDATED_PROOF")

    def ledger_counts(self):
        from hooks.lib._workflow_db import database_path
        from hooks.lib.repo_identity import resolve_repo_identity
        with sqlite3.connect(f"file:{database_path(resolve_repo_identity(self.case.repo))}?mode=ro", uri=True) as db:
            return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                         for table in ("workflow_events", "evidence"))

    def during_comparison(self, action):
        self.operation()
        started, release = self.case.tmp / "started", self.case.tmp / "release"
        (self.case.repo / "test_value.py").write_text(
            "import pathlib, time, unittest, app\nclass Value(unittest.TestCase):\n"
            "    def test_value(self):\n"
            f"        pathlib.Path({str(started)!r}).touch()\n"
            f"        while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
            "        self.assertEqual(app.value, 1)\n")
        command = [sys.executable, str(self.case.workflow), "tdd", "--repo", str(self.case.repo),
                   "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "test_value"]
        process = subprocess.Popen(command, cwd=self.case.repo, env=self.case.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(started.exists(), "probe never reached the production operation")
            action()
            release.touch()
            output, error = process.communicate(timeout=10)
            return process.returncode, output, error, command
        finally:
            release.touch()
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_concurrent_edit_cannot_publish_success(self):
        code, output, error, _ = self.during_comparison(
            lambda: (self.case.repo / "app.py").write_text("value = 2\n"))
        self.assertEqual(code, 2, "DRIFTED_COMPARISON_ACCEPTED: " + repr(output + error))
        self.assertFalse(json.loads(output)["valid"], "DRIFTED_COMPARISON_ACCEPTED")

    def test_probe_list_race_refuses_stale_publication_and_retry_is_atomic(self):
        counts = []
        def update():
            item = {key: value for key, value in self.item().items()
                    if key in {"id", "basis", "behavior", "seam", "expected", "sourceRefs"}}
            item["expected"] = "read the original value after the competing list update"
            path = self.case.tmp / "map.json"
            path.write_text(json.dumps({"items": [item]}))
            changed = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
            self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
            counts.append(self.ledger_counts())
        code, output, error, command = self.during_comparison(update)
        self.assertEqual(code, 2, "STALE_OWNER_PROOF_PUBLISHED: " + repr(output + error))
        self.assertEqual(self.ledger_counts(), counts[0], "COMPARISON_PUBLICATION_NOT_ATOMIC")
        retry = subprocess.run(command, cwd=self.case.repo, env=self.case.env, capture_output=True, text=True)
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        after = self.ledger_counts()
        repeat = subprocess.run(command, cwd=self.case.repo, env=self.case.env, capture_output=True, text=True)
        self.assertEqual(repeat.returncode, 0, repeat.stdout + repeat.stderr)
        self.assertEqual(self.ledger_counts(), after, "COMPARISON_PUBLICATION_NOT_ATOMIC")

    def test_each_source_arm_has_private_durable_state(self):
        body = ("import os, pathlib, sqlite3, unittest, app\nclass Value(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        root = pathlib.Path(os.environ['CODEX_WORKFLOW_STATE_ROOT'])\n"
                "        root.mkdir(exist_ok=True)\n"
                "        with sqlite3.connect(root / 'probe.db') as db:\n"
                "            db.execute('create table if not exists rows (value integer)')\n"
                "            self.assertEqual(db.execute('select count(*) from rows').fetchone()[0], 0)\n"
                "            db.execute('insert into rows values (?)', (app.value,))\n"
                "        with sqlite3.connect(root / 'probe.db') as reader:\n"
                "            self.assertEqual(reader.execute('select value from rows').fetchall(), [(1,)])\n")
        result = self.operation(body=body)
        self.assertEqual(result.returncode, 0, "CROSS_ARM_STATE_CONTAMINATION: " + repr(result.stdout + result.stderr))
        self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["passed", "passed"])
        self.assertFalse((self.case.tmp / "state" / "probe.db").exists())
        self.assertEqual((self.case.repo / "app.py").read_text(), "value = 1\n")

    def test_unchanged_probe_list_does_not_write_an_event(self):
        result = self.operation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = {key: value for key, value in self.item().items()
                if key in {"id", "basis", "behavior", "seam", "expected", "sourceRefs"}}
        path = self.case.tmp / "same-map.json"
        path.write_text(json.dumps({"items": [item]}))
        before = self.ledger_counts()
        result = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ledger_counts(), before, "LEDGER_STATE_REGRESSION_HIDDEN")

    def test_probe_text_keeps_supported_whitespace_normalization(self):
        initial = self.operation()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        item = self.item()
        item = {key: f" {value} " if isinstance(value, str) else value for key, value in item.items()}
        path = self.case.tmp / "spaced-map.json"
        path.write_text(json.dumps({"items": [item]}))
        changed = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(changed.returncode, 0, "PROBE_TEXT_NORMALIZATION_LOST: " + repr(changed.stdout + changed.stderr))
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                                "--", sys.executable, "-m", "unittest", "test_value")
        self.assertEqual(current.returncode, 0, "PROBE_TEXT_NORMALIZATION_LOST: " + repr(current.stdout + current.stderr))
        self.assertEqual(json.loads(initial.stdout)["summaryId"], json.loads(current.stdout)["summaryId"])

    def test_source_edit_invalidates_comparison(self):
        result = self.operation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        code = ("import json; from hooks.lib.repo_identity import resolve_repo_identity; "
                "from hooks.lib.tdd_workflow import completion_blockers; "
                "from hooks.lib.workflow_state import read_workflow; "
                "i=resolve_repo_identity('.'); print(json.dumps(completion_blockers(i,read_workflow(i))))")
        env = {**self.case.env, "PYTHONPATH": str(harness.ROOT)}
        self.case.env.update(PYTHONPATH=str(harness.ROOT))
        # Rebind to the same real environment used by the independent reader.
        self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                      "--", sys.executable, "-m", "unittest", "test_value")
        def blockers():
            result = subprocess.run([sys.executable, "-c", code], cwd=self.case.repo, env=env,
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        self.assertEqual(blockers(), [])
        (self.case.repo / "app.py").write_text("value = 2\n")
        self.assertTrue(blockers(), "STALE_COMPARISON_ACCEPTED")

    def review(self):
        path = self.case.tmp / "review.json"
        path.write_text(json.dumps({"findings": [{"id": "R1", "claim": "value must remain one",
                                                 "material": True, "kind": "behavioral"}]}))
        result = self.case.cli("record", "review", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["summaryId"]

    def own(self, evidence):
        item = self.item()
        item = {key: value for key, value in item.items() if key in {"id", "basis", "behavior", "seam", "expected"}}
        item["sourceRefs"] = [{"type": "finding", "evidenceId": evidence, "id": "R1"}]
        path = self.case.tmp / "map.json"
        path.write_text(json.dumps({"items": [item]}))
        result = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_probe_compares_reviewed_tree_without_reverting_edits(self):
        _, receipt = self.repair()
        marker = "REVIEWED_TREE_REPAIR_MISSING: " + str(receipt)
        self.assertEqual([arm["outcome"] for arm in receipt["arms"]], ["passed", "failed", "passed"], marker)
        self.assertEqual(receipt["comparison"], "changed", marker)
        self.assertEqual((self.case.repo / "app.py").read_text(), "value = 1\n# repaired current source\n", marker)

    def repair(self):
        self.operation()
        (self.case.repo / "app.py").write_text("value = 2\n")
        evidence = self.review()
        self.own(evidence)
        (self.case.repo / "test_value.py").rename(self.case.repo / "test_review.py")
        (self.case.repo / "app.py").write_text("value = 1\n# repaired current source\n")
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_review")
        marker = "REVIEWED_TREE_REPAIR_MISSING: " + result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, marker)
        receipt = json.loads(result.stdout)
        return evidence, receipt

    def fix(self, evidence, receipt):
        path = self.case.tmp / "fixed.json"
        path.write_text(json.dumps({"intakeEvidenceId": evidence, "dispositions": [{
            "finding_id": "R1", "status": "fixed", "evidenceRefs": [f"{receipt['summaryId']}:{receipt['runIndex']}"],
            "reason": "The owning comparison executes the real value read on the defective reviewed tree and repaired candidate."}]}))
        return self.case.cli("record", "review", "--repo", str(self.case.repo), "--input", str(path))

    def test_old_repair_cannot_close_a_later_occurrence(self):
        evidence, receipt = self.repair()
        first = self.fix(evidence, receipt)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        repaired = (self.case.repo / "app.py").read_text()
        (self.case.repo / "app.py").write_text("value = 3\n")
        latest = self.review()
        (self.case.repo / "app.py").write_text(repaired)
        result = self.fix(latest, receipt)
        self.assertEqual(result.returncode, 2, "EARLIER_REPAIR_CLOSED_RECURRENCE: " + result.stdout + result.stderr)
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_review")
        marker = "EARLIER_REPAIR_CLOSED_RECURRENCE: " + current.stdout + current.stderr
        self.assertEqual(current.returncode, 0, marker)
        fresh = json.loads(current.stdout)
        self.assertIn("3 != 1", str(fresh["arms"]), marker)
        closed = self.fix(latest, fresh)
        self.assertEqual(closed.returncode, 0, "EARLIER_REPAIR_CLOSED_RECURRENCE: " + closed.stdout + closed.stderr)

    def test_comparison_receipt_closes_finding(self):
        evidence, receipt = self.repair()
        result = self.fix(evidence, receipt)
        self.assertEqual(result.returncode, 0, "COMPARISON_RECEIPT_UNUSABLE: " + result.stdout + result.stderr)

    def test_deleting_only_owner_cannot_discharge_finding(self):
        self.operation()
        self.own(self.review())
        path = self.case.tmp / "map.json"
        path.write_text(json.dumps({"items": []}))
        result = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(result.returncode, 2, "FINDING_OWNER_LOST: " + result.stdout + result.stderr)

    def test_cancellation_reaps_the_executing_probe(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                self.cancel_probe(signum)

    def cancel_probe(self, signum):
        self.operation()
        ready = self.case.tmp / "probe.pid"
        ready.unlink(missing_ok=True)
        (self.case.repo / "test_value.py").write_text(
            "import os, pathlib, time, unittest\nclass Value(unittest.TestCase):\n"
            f"    def test_value(self):\n        pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "        time.sleep(30)\n"
        )
        process = subprocess.Popen([sys.executable, str(harness.WORKFLOW), "tdd", "--repo", str(self.case.repo),
                                    "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "test_value"],
                                   env=self.case.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = None
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), "probe did not start")
            child = int(ready.read_text())
            process.send_signal(signum)
            process.communicate(timeout=5)
            with self.assertRaises(ProcessLookupError, msg="INTERRUPTED_PROOF_PUBLISHED: executing child survived cancellation"):
                os.kill(child, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    child = None
