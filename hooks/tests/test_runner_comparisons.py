"""Real public proof-runner comparisons, with independent Git and process state."""
import json
import os
import signal
import shlex
import socket
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

from hooks.lib.behavior_map import comparison_view
from hooks.tests import test_tdd_repairs as harness
from hooks.tests.support import checkpoint_channels, pending_behavior


class RunnerComparisonTests(unittest.TestCase):
    def setUp(self):
        self.case = harness.MappedTddRepairTests()
        self.case.setUp()
        self.case.workflow = Path(os.environ.get("ISSUE125_COMPARISON_ROOT", str(harness.ROOT))) / "skills/repo-production-workflow/scripts/workflow.py"
        self.addCleanup(self.case.tearDown)

    def item(self, value=1):
        return pending_behavior("BM_VALUE", kind="preservation", behavior=f"value is {value}", expected=f"value is {value}")

    def details(self, result):
        receipt = json.loads(result.stdout)
        evidence = self.case.cli("evidence", "--repo", str(self.case.repo), "--evidence-id", receipt["summaryId"], "--full")
        run = json.loads(evidence.stdout)["document"]["runs"][receipt["runIndex"]]
        return {**receipt, **comparison_view(run), "sourceDelta": run.get("sourceDelta", {})}

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
        reader = subprocess.run([sys.executable, str(self.case.workflow), "summary", "--repo", str(self.case.repo)],
                                env={**self.case.env, "UNRELATED_READER_VARIABLE": "1"}, text=True, capture_output=True)
        self.assertIn("Probes compared=1/1", reader.stdout, "READER_ENVIRONMENT_STALED_PROOF: " + reader.stdout)
        self.assertIn('"comparison": "preserved"', result.stdout)
        self.assertIn("post-edit loop", json.loads(result.stdout)["next"].get("input", ""))
        self.assertIsNone(json.loads(result.stdout)["next"]["command"], "COMPARISON_SKIPS_LEAD_INVESTIGATION")
        receipt = json.loads(result.stdout)
        verified = self.case.cli("verify", "--repo", str(self.case.repo), "--from-evidence",
                                 f"{receipt['summaryId']}:{receipt['runIndex']}")
        self.assertEqual(verified.returncode, 0, "EXISTING_TEST_REFUSED: " + verified.stdout + verified.stderr)
        resume = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE")
        retained = self.case.cli(*resume)
        self.assertEqual(retained.returncode, 0, "RECORDED_BATCH_UNAVAILABLE: " + retained.stdout + retained.stderr)
        self.assertTrue(json.loads(retained.stdout).get("reused"), "UNCHANGED_BATCH_REEXECUTED")
        (self.case.repo / "app.py").write_text("value = 2\n")
        probe = self.case.repo / "test_value.py"
        probe.write_text(probe.read_text().replace("app.value, 1", "app.value, 2"))
        extended = json.loads(self.case.cli(*resume).stdout)
        self.assertEqual([arm["outcome"] for arm in extended["arms"]], ["failed", "passed"],
                         "EXTENDED_BATCH_NOT_COMPARED_ON_BOTH_SOURCES")

    def test_verify_executes_when_the_recorded_environment_changes(self):
        self.case.begin_with_map([self.item()])
        for first in ("tdd", "verify"):
            with self.subTest(receipt=first):
                command = [sys.executable, "-c", f"import os; print({first!r}, os.environ['RESULT_MODE']); assert os.environ['RESULT_MODE'] == 'old'"]
                self.case.env["RESULT_MODE"] = "old"
                initial = self.case.cli(first, "--repo", str(self.case.repo),
                                        *(["--behavior-id", "BM_VALUE"] if first == "tdd" else []), "--", *command)
                self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
                self.case.env["RESULT_MODE"] = "new"
                changed = self.case.cli("verify", "--repo", str(self.case.repo), "--", *command)
                self.assertNotEqual(changed.returncode, 0, "CHANGED_ENVIRONMENT_REUSED_A_PASS")
                self.assertIn("AssertionError", changed.stdout + changed.stderr)

    def test_current_test_environment_is_retained_and_bound(self):
        case = self.case
        (case.repo / "tests").mkdir()
        (case.repo / "tests/__init__.py").write_text("")
        helper = case.repo / "tests/support.py"
        helper.write_text("expected = 1\n")
        (case.repo / "test_removed.py").write_text("raise AssertionError('obsolete test')\n")
        case.git("add", ".")
        case.git("commit", "-qm", "historical test environment")
        slug, _ = case.begin_with_map([self.item(2)])
        (case.repo / "test_removed.py").unlink()
        helper.write_text("expected = 2\n")
        (case.repo / "app.py").write_text("value = 2\n")
        calls = case.tmp / "calls"
        test = case.repo / "test_value.py"
        test.write_text(
            "import unittest, app\nfrom pathlib import Path\nfrom tests.support import expected\n"
            "class Value(unittest.TestCase):\n"
            "    def test_value(self):\n"
            f"        with Path({str(calls)!r}).open('a') as calls: calls.write(str(app.value)+'\\n')\n"
            "        self.assertFalse(Path('test_removed.py').exists())\n"
            "        self.assertEqual(app.value, expected)\n")
        command = ("tdd", "--repo", str(case.repo), "--slug", slug, "--behavior-id", "BM_VALUE",
                   "--support", "tests/support.py", "--timeout", "7",
                   "--", sys.executable, "-m", "unittest", "test_value.Value.test_value")
        first = case.cli(*command)
        self.assertEqual(first.returncode, 0, "TEST_ENVIRONMENT_MISSING: " + first.stdout + first.stderr)
        self.assertEqual([a["outcome"] for a in json.loads(first.stdout)["arms"]], ["failed", "passed"])
        expected_calls = ["1", "2"]
        command = ("tdd", "--repo", str(case.repo), "--behavior-id", "BM_VALUE")
        for path, body in ((case.repo / "test_unrelated.py", "UNUSED = 1\n"),
                           (test, test.read_text() + "    def test_creation(self): self.assertEqual(app.value, 99)\n")):
            path.write_text(body)
            changed = case.cli(*command)
            self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
            self.assertFalse(json.loads(changed.stdout).get("reused", False))
            expected_calls.extend(["1", "2"])
            self.assertEqual(sorted(calls.read_text().splitlines()), sorted(expected_calls), "CHANGED_SUPPORT_NOT_EXECUTED")
        helper.write_text("expected = 3\n")
        changed = case.cli(*command)
        self.assertEqual(changed.returncode, 2, "HELPER_CHANGE_REUSED_SUCCESS: " + changed.stdout)
        self.assertEqual({a["outcome"] for a in json.loads(changed.stdout)["arms"]}, {"failed"})
        self.assertEqual(sorted(calls.read_text().splitlines()), sorted(expected_calls + ["1", "2"]))

    def test_quality_gate_refreshes_comparisons_only_after_success(self):
        self.assertEqual(self.operation(2).returncode, 0)
        case = self.case

        def runs():
            state = json.loads(case.cli("status", "--repo", str(case.repo)).stdout)
            return json.loads(case.cli("evidence", "--repo", str(case.repo), "--evidence-id",
                                      state["tddEvidence"], "--full").stdout)["document"]["runs"]

        from hooks.lib.repo_identity import resolve_repo_identity
        from hooks.lib.workflow_state import record_base_oid
        state = json.loads(case.cli("status", "--repo", str(case.repo)).stdout)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=case.repo, text=True).strip()
        record_base_oid(resolve_repo_identity(case.repo), state["slug"], state["workflowId"], base)
        initial = len(runs())
        (case.repo / "app.py").write_text("from typing import Any\nvalue: Any = 2\n")
        command = ("verify", "--repo", str(case.repo), "--kind", "quality-gate")
        failed = case.cli(*command)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertEqual(len(runs()), initial, "FAILED_GATE_RAN_COMPARISONS")
        (case.repo / "app.py").write_text("value: int = 2\n")
        passed = case.cli(*command)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        # CodeRabbit: the gate's own receipt is the last line, after the refresh receipts
        self.assertEqual(json.loads(passed.stdout.strip().splitlines()[-1])["kind"], "quality-gate", "GATE_RECEIPT_NOT_LAST")
        refreshed = runs()
        self.assertEqual(len(refreshed), initial + 1, "QUALITY_GATE_DID_NOT_REFRESH_PROOF")
        self.assertTrue(refreshed[-1]["valid"])
        self.assertEqual(case.cli(*command).returncode, 0)
        self.assertEqual(len(runs()), len(refreshed), "UNCHANGED_GATE_REPEATED_COMPARISON")
        (case.repo / "app.py").write_text("value: int = 3\n")
        self.assertEqual(case.cli(*command).returncode, 2, "FAILING_COMPARISON_PASSED_VERIFICATION")
        self.assertFalse(runs()[-1]["valid"])
        (case.repo / "app.py").write_text("value: int = 2\n")
        missing = pending_behavior("BM_MISSING", kind="preservation", behavior="another obligation", expected="its own proof")
        update = case.tmp / "map.json"
        update.write_text(json.dumps({"items": [missing, self.item(2)]}))
        result = case.cli("record", "tdd-map", "--repo", str(case.repo), "--input", str(update))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = case.cli(*command)
        self.assertEqual(result.returncode, 0, "UNRECORDED_PROBE_BLOCKED_REFRESH: " + result.stdout)
        self.assertTrue(runs()[-1]["valid"])
        self.assertEqual(json.loads(case.cli("status", "--repo", str(case.repo)).stdout)["tdd"], "in-progress")

    def test_snapshot_git_objects_remain_readable(self):
        self.case.begin_with_map([self.item()])
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", "import subprocess; assert subprocess.check_output(['git', 'show', 'HEAD:app.py']) == b'value = 1\\n'; print('Git source readable')")
        self.assertEqual(result.returncode, 0, "GIT_SOURCE_UNREADABLE: " + result.stdout + result.stderr)

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

    def test_operation_receipt_names_the_changed_cases(self):
        self.operation()
        (self.case.repo / "app.py").write_text("value = 2\n")
        probe = "import app\nfor case in ('a', 'b', 'c'): print(case, app.value if case == 'b' else 0)\nprint('done')"
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", probe)
        arms = self.details(result)["arms"]
        self.assertEqual(self.details(result)["comparison"], "changed", result.stdout + result.stderr)
        self.assertEqual(self.details(result)["cases"], ["- b 1", "+ b 2"], "CHANGED_CASES_HIDDEN: " + result.stdout)
        delta = self.details(result).get("sourceDelta", {})
        self.assertIn("-value = 1\n+value = 2", delta.get("patch", ""), "REMOVED_DECISION_HIDDEN")
        self.assertNotIn("test_value.py", delta["patch"], "PROBE_CHANGES_OBSCURE_PRODUCTION")
        self.assertFalse(delta["truncated"])
        reused = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", probe)
        self.assertTrue(self.details(reused)["reused"])
        self.assertEqual(self.details(reused)["sourceDelta"], delta)
        packet = checkpoint_channels(self.case.repo, self.case.env, "code-review")
        self.assertNotIn("authoritativeContract", packet["behavior-map"], "PLAN_PRESENTED_AS_REQUEST_AUTHORITY")
        self.assertIn("preflightInterpretation", packet["behavior-map"])
        comparison = packet["behavior-map"]["items"][0]["comparison"]
        self.assertNotIn("sourceDelta", comparison, "COMPARISON_REPEATS_PACKAGE_DIFF")
        self.assertEqual(comparison["arms"], arms)
        self.assertIn("-value = 1\n+value = 2", packet["diff"])
        # final SPEC-3: an extra repeated line is a changed case too
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", "import app\nfor _ in range(app.value): print('event')")
        self.assertEqual(self.details(result)["cases"], ["+ event"], "CHANGED_CASES_HIDDEN: " + result.stdout)
        # final SPEC-5: output lines that look like diff headers are cases too
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--", sys.executable, "-c",
                               "import app\nprint('-- old' if app.value == 1 else '++ new')\nprint('done')")
        self.assertEqual(self.details(result)["cases"], ["- -- old", "+ ++ new"], "CHANGED_CASES_HIDDEN: " + result.stdout)
        (self.case.repo / "app.py").write_text("value = 2\n" + "# retained context\n" * 500 + "# END_OF_CHANGE\n")
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                               sys.executable, "-c", probe)
        delta = self.details(result)["sourceDelta"]
        self.assertTrue(delta["truncated"])
        self.assertEqual(len(delta["patch"]), 8000)
        full = subprocess.run(shlex.split(delta["command"]), check=True, capture_output=True, text=True).stdout
        self.assertIn("END_OF_CHANGE", full, "TRUNCATED_DECISION_UNRECOVERABLE")
        self.assertNotIn("test_value.py", full)

    def test_batch_failures_are_attributed_to_cases_and_subtests(self):
        case = self.case
        (case.repo / "app.py").write_text("value = 1\nkept = True\n")
        case.git("add", ".")
        case.git("commit", "-qm", "two independent outcomes")
        case.begin_with_map([self.item()])
        (case.repo / "app.py").write_text("value = 2\nkept = False\n")
        (case.repo / "test_value.py").write_text(
            "import unittest, app\n"
            "class Value(unittest.TestCase):\n"
            "    def test_requested(self): self.assertEqual(app.value, 2, 'REQUESTED_CHANGE')\n"
            "    def test_preserved(self): self.assertTrue(app.kept, 'PRESERVATION_CHANGE')\n"
            "    def test_loop(self):\n"
            "        for index, expected in enumerate((2, 1)):\n"
            "            print('loop input', index, flush=True)\n"
            "            self.assertEqual(app.value, expected)\n"
            "    def test_partitions(self):\n"
            "        for name, actual, expected in [('requested',app.value,2),('preserved',app.kept,True)]:\n"
            "            with self.subTest(name=name): self.assertEqual(actual, expected, name)\n")
        result = case.cli("tdd", "--repo", str(case.repo), "--behavior-id", "BM_VALUE", "--",
                          sys.executable, "-m", "unittest", "test_value")
        compact = json.loads(result.stdout)
        self.assertEqual(compact["cases"], [
            "test_loop (test_value.Value.test_loop): original=failed, current=failed; AssertionError: 1 != 2; AssertionError: 2 != 1",
            "test_partitions (test_value.Value.test_partitions): original=failed (1 subtests), current=failed (1 subtests); "
            "AssertionError: 1 != 2 : requested; AssertionError: False != True : preserved",
            "test_preserved (test_value.Value.test_preserved): original=passed, current=failed; AssertionError: False is not true : PRESERVATION_CHANGE",
            "test_requested (test_value.Value.test_requested): original=failed, current=passed; AssertionError: 1 != 2 : REQUESTED_CHANGE",
        ], "CASE_ATTRIBUTION_MISSING: " + result.stdout)
        self.assertEqual(compact["limitations"], ["original: 1 cases unnamed (passing subtests print no name)",
                                                  "current: 1 cases unnamed (passing subtests print no name)"], result.stdout)
        evidence = case.cli("evidence", "--repo", str(case.repo), "--evidence-id", compact["summaryId"], "--full")
        arms = json.loads(evidence.stdout)["document"]["runs"][compact["runIndex"]]["arms"]
        self.assertNotIn("loop input 1", arms[0]["output"])
        self.assertIn("loop input 1", arms[1]["output"])
        self.assertEqual({name for arm in arms for name in arm["cases"] if "name=" in name},
                         {"test_partitions (test_value.Value.test_partitions) (name='requested')",
                          "test_partitions (test_value.Value.test_partitions) (name='preserved')"})

    def test_python_option_forms_preserve_inline_operations(self):
        self.operation()
        for options in (("-uc",), ("-Buc",), ("-Wignore", "-c"),
                        ("--check-hash-based-pycs", "always", "-c")):
            with self.subTest(options=options):
                result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                                       sys.executable, *options, "import app; assert app.value == 1; print(app.value)")
                self.assertEqual(result.returncode, 0, "INLINE_FLAGS_REFUSED: " + result.stdout + result.stderr)
                self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["passed", "passed"])

    def test_external_pytest_probe_cannot_reuse_unbound_success(self):
        self.operation()
        probe = self.case.tmp / "test_external.py"
        local = self.case.repo / "test_external.py"
        local.write_text("import app\ndef test_value(): assert app.value == 1\n")
        for options in (("-m",), ("-u", "-m"), ("-um",)):
            with self.subTest(options=options):
                command = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                           sys.executable, *options, "pytest", "-q")
                for assertion in ("True", "False"):
                    probe.write_text(f"def test_value(): assert {assertion}\n")
                    result = self.case.cli(*command, str(probe))
                    self.assertNotEqual(result.returncode, 0, "EXTERNAL_RUNNER_PROBE_UNBOUND: " + result.stdout)
                    self.assertIn("proof target(s) do not resolve under the repository root", result.stderr)
                allowed = self.case.cli(*command, str(local))
                self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_original_and_candidate_are_executed(self):
        result = self.operation(2, body=(
            "import subprocess, sys, unittest\n"
            "class Value(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        value = subprocess.check_output([sys.executable, '-c', 'import app; print(app.value)'], text=True)\n"
            "        self.assertEqual(value.strip(), '2', 'CHILD_RESULT_CHANGED')\n"
            "    def test_other_case(self):\n"
            "        import app\n"
            "        self.assertEqual(app.value, 2, 'SECOND_CASE_CHANGED')\n"
        ))
        marker = "RECORDED_SOURCE_COMPARISON_MISSING: " + result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, marker)
        receipt = self.details(result)
        self.assertEqual(receipt.get("comparison"), "changed", marker)
        self.assertEqual([arm["outcome"] for arm in receipt["arms"]], ["failed", "passed"], marker)
        self.assertNotEqual(receipt["arms"][0]["tree"], receipt["arms"][1]["tree"], marker)
        for failure in ("CHILD_RESULT_CHANGED", "SECOND_CASE_CHANGED"):
            self.assertIn(failure, " ".join(receipt["cases"]), "BATCH_FAILURE_HIDDEN")

    def test_a_shared_host_resource_is_preserved(self):
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        body = ("import socket, time, unittest, app\nclass Value(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        with socket.socket() as server:\n"
                f"            server.bind(('127.0.0.1', {port}))\n"
                "            time.sleep(1.5)\n"
                "        self.assertIn(app.value, (1, 2))\n")
        result = self.operation(2, body=body)
        receipt = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual((receipt["comparison"], [arm["outcome"] for arm in receipt["arms"]]), ("preserved", ["passed", "passed"]),
                         "SHARED_HOST_RESOURCE_COLLIDED: " + result.stdout)

    def test_support_spellings_cannot_overlay_production(self):
        def support(spelling, link=False):
            self.case.tearDown(); self.case.setUp()
            (self.case.repo / "tests").mkdir()
            (self.case.repo / "tests" / "test_extra.py").write_text("EXTRA = 1\n")
            if link:
                (self.case.repo / "tests" / "link.py").symlink_to("../app.py")
            result = self.operation(2, extra=("--support", spelling.replace("<root>", str(self.case.repo))))
            receipt = json.loads(result.stdout.splitlines()[-1]) if result.stdout.strip() else {}
            return result.returncode, [arm["outcome"] for arm in receipt.get("arms", [])], result.stderr
        for spelling in ("tests/../app.py", "./tests/../app.py", "tests//../app.py", "<root>/app.py", "/tmp/outside.py"):
            with self.subTest(spelling=spelling):
                code, arms, error = support(spelling)
                self.assertEqual((code, arms), (2, []), "SUPPORT_PATH_OVERLAID_PRODUCTION: " + error)
        for spelling, link in (("<root>/tests/test_extra.py", False), ("tests/./test_extra.py", False), ("tests/link.py", True)):
            with self.subTest(spelling=spelling):
                code, arms, error = support(spelling, link)
                self.assertEqual((code, arms), (0, ["failed", "passed"]), "SUPPORT_PATH_OVERLAID_PRODUCTION: " + error)

    def test_editable_package_uses_recorded_source(self):
        case = self.case
        package = case.repo / "src/auditpkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("value = 1\n")
        (case.repo / "setup.py").write_text("from setuptools import setup\nsetup(name='auditpkg',version='0.0.1',package_dir={'':'src'},packages=['auditpkg'])\n")
        case.git("add", "src", "setup.py")
        case.git("commit", "-qm", "editable source")
        venv = case.repo / ".venv"
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True, capture_output=True)
        python = str(venv / "bin/python")
        subprocess.run([python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(case.repo)], check=True, capture_output=True)
        case.begin_with_map([self.item(2)])
        (package / "__init__.py").write_text("value = 2\n")
        (case.repo / "test_value.py").write_text("import auditpkg, unittest\nclass Value(unittest.TestCase):\n def test_value(self): self.assertEqual(auditpkg.value, 2)\n")
        command = ("tdd", "--repo", str(case.repo), "--behavior-id", "BM_VALUE", "--", python, "-m", "unittest", "test_value")
        for route in (None, str(case.repo / "src")):
            if route:
                case.env["PYTHONPATH"] = route
            result = case.cli(*command)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["failed", "passed"])
        (package / "added.py").write_text("value = 2\n")
        (case.repo / "test_value.py").write_text("from auditpkg import added\nimport unittest\nclass Value(unittest.TestCase):\n def test_value(self): self.assertEqual(added.value, 2)\n")
        result = case.cli(*command)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["valid"])

    def test_external_script_is_not_unbound_proof(self):
        self.operation()
        for executable, suffix in ((sys.executable, ".py"), (shutil.which("bash"), ".sh"), (None, ".sh")):
            for folder in (self.case.tmp, self.case.repo / "tests"):
                folder.mkdir(exist_ok=True)
                path = folder / ("test_replay" + suffix)
                command = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                           *((executable, str(path)) if executable else (str(path),)))
                for value in (1, 1, 99):
                    code = f"import app; assert app.value == {value}; print(app.value)"
                    path.write_text(code + "\n" if suffix == ".py" else "#!/bin/bash\npython3 -c " + repr(code) + "\n")
                    path.chmod(0o755)
                    result = self.case.cli(*command)
                    if value == 99 or folder == self.case.tmp:
                        self.assertEqual(result.returncode, 2, "BM_SHELL_BINDING_MISSING: " + result.stdout)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unavailable_binding_is_incomplete_and_retryable(self):
        self.operation(2)
        path = self.case.tmp / "bin"
        path.mkdir()
        for tool in ("git", "realpath", "cksum"):
            (path / tool).symlink_to(shutil.which(tool))
        previous = self.case.env["PATH"]
        self.case.env["PATH"] = str(path)
        result = self.operation(2)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["valid"])
        self.case.env["PATH"] = previous
        self.assertEqual(self.operation(2).returncode, 0)

    def test_denied_binding_is_not_a_production_failure(self):
        self.operation(2)
        result = subprocess.run(["bwrap", "--unshare-user", "--disable-userns", "--dev-bind", "/", "/", "--",
            sys.executable, str(self.case.workflow), "tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
            "--", sys.executable, "-c", "import app; assert app.value == 2; print(app.value)"],
            cwd=self.case.repo, env=self.case.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["incomplete", "incomplete"])

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
        evidence = self.case.cli("evidence", "--repo", str(self.case.repo),
                                 "--evidence-id", before["summaryId"], "--full")
        run = json.loads(evidence.stdout)["document"]["runs"][0]
        arms = run["arms"]
        self.assertEqual(len({arm["requestedTree"] for arm in arms}), 2)
        self.assertEqual(len({arm["sourceTree"] for arm in arms}), 1,
                         "WITHIN_COMPARISON_REUSE_MISSING: " + first.stdout)
        self.assertEqual([arm for view in before["arms"] for arm in view if arm in {"sourceTree", "requestedTree"}], [],
                         "REUSED_ARM_NAMES_ANOTHER_TREE: " + first.stdout)
        self.assertEqual([arm.get("tree") for arm in before["arms"]], [run["originalTree"], run["candidateTree"]],
                         "REUSED_ARM_NAMES_ANOTHER_TREE: " + first.stdout)
        self.assertEqual(before["summaryId"], after["summaryId"], marker)
        self.assertTrue(after.get("reused"), marker)
        update = self.case.tmp / "map.json"
        update.write_text(json.dumps({"items": [self.item(), pending_behavior("BM_OTHER", kind="preservation")]}))
        mapped = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(update))
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        command = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--behavior-id", "BM_OTHER",
                   "--", sys.executable, "-m", "unittest", "test_value")
        shared = self.case.cli(*command)
        self.assertEqual(shared.returncode, 0, shared.stdout + shared.stderr)
        receipt = json.loads(shared.stdout)
        evidence = self.case.cli("evidence", "--repo", str(self.case.repo), "--evidence-id", receipt["summaryId"], "--full")
        owners = json.loads(evidence.stdout)["document"]["behaviorMap"]
        self.assertEqual({item["comparison"]["runIndex"] for item in owners}, {receipt["runIndex"]})
        packet = checkpoint_channels(self.case.repo, self.case.env, "code-review")["behavior-map"]["items"]
        self.assertIn("arms", packet[0]["comparison"])
        self.assertEqual(packet[1]["comparison"], {key: packet[0]["comparison"][key] for key in ("runIndex", "valid", "fresh")}, "SHARED_BATCH_REPEATED_IN_PACKET")
        repeated = self.case.cli(*command)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["summaryId"], receipt["summaryId"])
        self.assertTrue(json.loads(repeated.stdout).get("reused"))

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
                    if key in {"id", "kind", "basis", "behavior", "seam", "expected", "sourceRefs"}}
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
                "        output = pathlib.Path(os.environ['TMPDIR']) / 'probe-output'\n"
                "        self.assertEqual(app.value, int(output.read_text()) if output.exists() else 1)\n"
                "        output.write_text('99')\n"
                "        root = pathlib.Path(os.environ['CODEX_WORKFLOW_STATE_ROOT'])\n"
                "        root.mkdir(exist_ok=True)\n"
                "        with sqlite3.connect(root / 'probe.db') as db:\n"
                "            db.execute('create table if not exists rows (value integer)')\n"
                "            self.assertEqual(db.execute('select count(*) from rows').fetchone()[0], 0)\n"
                "            db.execute('insert into rows values (?)', (app.value,))\n"
                "        with sqlite3.connect(root / 'probe.db') as reader:\n"
                "            self.assertEqual(reader.execute('select value from rows').fetchall(), [(app.value,)])\n")
        result = self.operation(body=body)
        self.assertEqual(result.returncode, 0, "CROSS_ARM_STATE_CONTAMINATION: " + repr(result.stdout + result.stderr))
        self.assertEqual([a["outcome"] for a in json.loads(result.stdout)["arms"]], ["passed", "passed"])
        self.assertFalse((self.case.tmp / "state" / "probe.db").exists())
        self.assertEqual((self.case.repo / "app.py").read_text(), "value = 1\n")
        command = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE", "--",
                   sys.executable, "-m", "unittest", "test_value")
        repeated = self.case.cli(*command)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertTrue(json.loads(repeated.stdout).get("reused"))
        (self.case.repo / "app.py").write_text("value = 99\n")
        changed = self.case.cli(*command)
        self.assertEqual(changed.returncode, 2, changed.stdout + changed.stderr)
        self.assertEqual([a["outcome"] for a in json.loads(changed.stdout)["arms"]], ["passed", "failed"])

    def test_unchanged_probe_list_does_not_write_an_event(self):
        result = self.operation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        item = {key: value for key, value in self.item().items()
                if key in {"id", "kind", "basis", "behavior", "seam", "expected", "sourceRefs"}}
        from hooks.lib.repo_identity import resolve_repo_identity
        from hooks.lib.tdd_workflow import map_update
        from hooks.lib.workflow_state import read_workflow
        identity = resolve_repo_identity(self.case.repo)
        before = self.ledger_counts()
        map_update(identity, read_workflow(identity), {"items": [item]})  # the owner `record tdd-map` calls
        self.assertEqual(self.ledger_counts(), before, "LEDGER_STATE_REGRESSION_HIDDEN")

    def test_probe_text_keeps_supported_whitespace_normalization(self):
        initial = self.operation()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        item = self.item()
        item = {key: f" {value} " if isinstance(value, str) and key != "kind" else value for key, value in item.items()}
        path = self.case.tmp / "spaced-map.json"
        path.write_text(json.dumps({"items": [item]}))
        changed = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(changed.returncode, 0, "PROBE_TEXT_NORMALIZATION_LOST: " + repr(changed.stdout + changed.stderr))
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                                "--", sys.executable, "-m", "unittest", "test_value")
        self.assertEqual(current.returncode, 0, "PROBE_TEXT_NORMALIZATION_LOST: " + repr(current.stdout + current.stderr))
        self.assertEqual(json.loads(initial.stdout)["summaryId"], json.loads(current.stdout)["summaryId"])

    def test_source_edit_invalidates_comparison(self):
        self.case.env["PYTHONPATH"] = str(harness.ROOT)
        result = self.operation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        code = ("import json; from hooks.lib.repo_identity import resolve_repo_identity; "
                "from hooks.lib.tdd_workflow import completion_blockers; "
                "from hooks.lib.workflow_state import read_workflow; "
                "i=resolve_repo_identity('.'); print(json.dumps(completion_blockers(i,read_workflow(i))))")
        def blockers():
            result = subprocess.run([sys.executable, "-c", code], cwd=self.case.repo, env=self.case.env,
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
        item = {key: value for key, value in self.item().items() if key != "sourceRefs"}
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

    def fix(self):
        return self.case.cli("record", "advisor-disposition", "--repo", str(self.case.repo),
                             "--finding", "R1", "--fixed", "--reason",
                             "The owning comparison records the exercised original, reviewed and candidate results.")

    def test_old_repair_cannot_close_a_later_occurrence(self):
        self.repair()
        first = self.fix()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        repaired = (self.case.repo / "app.py").read_text()
        (self.case.repo / "app.py").write_text("value = 3\n")
        self.review()
        (self.case.repo / "app.py").write_text(repaired)
        result = self.fix()
        self.assertEqual(result.returncode, 2, "EARLIER_REPAIR_CLOSED_RECURRENCE: " + result.stdout + result.stderr)
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_review")
        marker = "EARLIER_REPAIR_CLOSED_RECURRENCE: " + current.stdout + current.stderr
        self.assertEqual(current.returncode, 0, marker)
        fresh = json.loads(current.stdout)
        self.assertIn("3 != 1", str(fresh["cases"]), marker)
        closed = self.fix()
        self.assertEqual(closed.returncode, 0, "EARLIER_REPAIR_CLOSED_RECURRENCE: " + closed.stdout + closed.stderr)

    def test_comparison_receipt_closes_finding(self):
        self.repair()
        test = self.case.repo / "test_review.py"
        test.write_text(test.read_text() + "\n# changed support before closure\n")
        stale = self.fix()
        self.assertEqual(stale.returncode, 2, "STALE_SUPPORT_CLOSED_FINDING: " + stale.stdout + stale.stderr)
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                                "--", sys.executable, "-m", "unittest", "test_review")
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertEqual([arm["outcome"] for arm in json.loads(current.stdout)["arms"]], ["passed", "failed", "passed"],
                         "STALE_SUPPORT_CLOSED_FINDING: " + current.stdout)
        result = self.fix()
        self.assertEqual(result.returncode, 0, "COMPARISON_RECEIPT_UNUSABLE: " + result.stdout + result.stderr)
        test.write_text(test.read_text() + "\n# changed support after closure\n")
        command = ("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                   "--", sys.executable, "-m", "unittest", "test_review")
        refreshed = self.case.cli(*command)
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
        receipt = json.loads(refreshed.stdout)
        self.assertEqual([arm["outcome"] for arm in receipt["arms"]], ["passed", "passed"],
                         "CLOSED_FINDING_REPLAYS_HISTORICAL_DEFECT")
        repeated = self.case.cli(*command)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertTrue(json.loads(repeated.stdout).get("reused"))
        self.assertEqual(json.loads(repeated.stdout)["runIndex"], receipt["runIndex"])

    def test_map_delta_preserves_untouched_owners(self):
        self.operation()
        self.own(self.review())
        path = self.case.tmp / "map.json"
        path.write_text(json.dumps({"items": [pending_behavior("BM_NEW", kind="preservation")]}))
        result = self.case.cli("record", "tdd-map", "--repo", str(self.case.repo), "--input", str(path))
        self.assertEqual(result.returncode, 0, "FINDING_OWNER_LOST: " + result.stdout + result.stderr)
        state = json.loads(self.case.cli("status", "--repo", str(self.case.repo)).stdout)
        document = json.loads(self.case.cli("evidence", "--repo", str(self.case.repo), "--evidence-id",
                             state["tddEvidence"], "--full").stdout)["document"]
        self.assertEqual([i["id"] for i in document["behaviorMap"]], ["BM_VALUE", "BM_NEW"])
        self.assertEqual(document["behaviorMap"][0]["sourceRefs"][0]["id"], "R1")

    def test_preservation_comparison_closes_a_finding(self):
        self.operation()
        evidence = self.review()
        self.own(evidence)
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", sys.executable, "-m", "unittest", "test_value")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(a["outcome"] == "passed" for a in json.loads(result.stdout)["arms"]))
        closed = self.fix()
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)

    def test_cancellation_reaps_the_executing_probe(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                self.cancel_probe(signum, "tdd", "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "test_value")

    def test_refresh_cancellation_reaps_the_executing_probe(self):
        self.cancel_probe(signal.SIGTERM, "verify", "--kind", "quality-gate", "--base-ref", "HEAD")

    def test_refresh_missing_executable_retains_the_committed_gate_receipt(self):
        self.assertEqual(self.operation().returncode, 0)
        executable = self.case.tmp / "python3"
        executable.symlink_to(sys.executable)
        result = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                               "--", str(executable), "-m", "unittest", "test_value")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        executable.unlink()
        result = self.case.cli("verify", "--repo", str(self.case.repo), "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(executable), result.stderr)
        receipt = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["kind"], "quality-gate", "COMMITTED_GATE_RECEIPT_LOST")
        state = json.loads(self.case.cli("status", "--repo", str(self.case.repo)).stdout)
        self.assertEqual(receipt["evidenceId"], state["verificationLatestEvidence"])

    def test_refresh_cancellation_during_arm_setup_removes_the_snapshot(self):
        case = self.case
        (case.repo / "pkg").mkdir()
        for index in range(1500):
            (case.repo / "pkg" / f"m{index}.py").write_text(f"VALUE = {index}\n")
        case.git("add", "pkg")
        case.git("commit", "-qm", "enough production files to widen arm setup")
        self.assertEqual(self.operation(2).returncode, 0)
        (case.repo / "app.py").write_text("value: int = 2\n")
        arms = case.tmp / "arms"
        arms.mkdir()
        process = subprocess.Popen([sys.executable, str(case.workflow), "verify", "--repo", str(case.repo), "--kind",
                                    "quality-gate", "--base-ref", "HEAD"], cwd=case.repo, env={**case.env, "TMPDIR": str(arms)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 60
        while not any(arms.glob("workflow-proof-*/source")) and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertTrue(any(arms.glob("workflow-proof-*/source")), "arm setup did not start")
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
        self.assertEqual(list(arms.glob("workflow-proof-*")), [], "REFRESH_SETUP_CANCEL_LEAKED: interrupted arm checkout remains")

    def cancel_probe(self, signum, *command):
        self.operation(2)
        ready = self.case.tmp / "probe.pid"
        ready.unlink(missing_ok=True)
        (self.case.repo / "test_value.py").write_text(
            "import json, os, pathlib, time, unittest\nclass Value(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        output = pathlib.Path(os.environ['TMPDIR']) / 'probe-output'\n"
            "        output.write_text('live output')\n"
            f"        pathlib.Path({str(ready)!r}).write_text(json.dumps([os.getpid(), str(output)]))\n"
            "        time.sleep(30)\n"
        )
        process = subprocess.Popen([sys.executable, str(self.case.workflow), command[0], "--repo", str(self.case.repo),
                                    *command[1:]], cwd=self.case.repo, env=self.case.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = None
        try:
            deadline = time.monotonic() + 60
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), "probe did not start")
            time.sleep(.2)
            child, output = json.loads(ready.read_text())
            self.assertEqual(Path(output).read_text(), "live output")
            process.send_signal(signum)
            stdout, stderr = process.communicate(timeout=5)
            if command[0] == "verify":
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(json.loads(stdout.decode().strip().splitlines()[-1])["kind"], "quality-gate",
                                 "COMMITTED_GATE_RECEIPT_LOST: " + stderr.decode())
            with self.assertRaises(ProcessLookupError, msg="REFRESH_CANCEL_LEAKED: executing child survived cancellation"):
                os.kill(child, 0)
            self.assertFalse(Path(output).exists(), "REFRESH_CANCEL_LEAKED: interrupted output not cleaned")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    child = None


UNITTEST_PROBE = "import unittest, app\nclass Value(unittest.TestCase):\n"
INTENT = ("Make the value two. Keep everything else the module already does, including the kept value. "
          "Report kept as two only when the request says so.")


class ReadinessTests(unittest.TestCase):
    """One readiness result: kind, boundary inputs and readings are proved by execution."""

    def setUp(self):
        self.case = harness.MappedTddRepairTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.repo = self.case.repo
        (self.repo / "app.py").write_text("value = 1\nkept = 1\n")
        self.case.git("add", "app.py")
        self.case.git("commit", "-qm", "kept value")

    def begin(self, *items, intent=INTENT):
        return self.case.begin_with_map(list(items), intent=intent)

    def probe(self, *methods):
        (self.repo / "test_value.py").write_text(UNITTEST_PROBE + "".join(f"    {m}\n" for m in methods))

    def tdd(self, *ids, command=(sys.executable, "-m", "unittest", "test_value")):
        result = self.case.cli("tdd", "--repo", str(self.repo),
                               *[value for identifier in ids for value in ("--behavior-id", identifier)], "--", *command)
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("{"), "COMPARISON_NOT_RECORDED: " + result.stdout + result.stderr)
        return json.loads(result.stdout)

    def status(self):
        return json.loads(self.case.cli("status", "--repo", str(self.repo)).stdout)

    def summary(self):
        return self.case.cli("summary", "--repo", str(self.repo)).stdout

    def update(self, *items):
        path = self.case.tmp / "map.json"
        path.write_text(json.dumps({"items": list(items)}))
        return self.case.cli("record", "tdd-map", "--repo", str(self.repo), "--input", str(path))

    def open_lines(self, receipt):
        return [line for line in receipt["next"].get("input", "").splitlines() if line.startswith("BM_")]

    def test_contract_item_needs_an_attributable_difference(self):
        marker = "CONTRACT_WITHOUT_CHANGE_PASSED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"))
        self.probe("def test_value(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(receipt["comparison"], "preserved", receipt)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("BM_CHANGE (contract): value becomes two => value is two", line, marker)
        self.assertIn("no attributable case differs between original and current", line, marker)
        self.assertIn("BM_CHANGE", self.summary(), marker)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("changed", "passed"), marker + ": " + json.dumps(receipt))
        self.assertEqual(receipt["cases"], ["test_value (test_value.Value.test_value): original=failed, current=passed; "
                                            "AssertionError: 1 != 2 : VALUE_NOT_TWO"], marker)
        # a shared batch needs each owner to name its cases
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"),
                    pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertEqual(sorted(line.split(" ")[0] for line in self.open_lines(receipt)), ["BM_CHANGE", "BM_KEEP"], marker + ": " + json.dumps(receipt))
        self.assertTrue(all("name its cases in boundaryInputs" in line for line in self.open_lines(receipt)), marker)
        # a contract pair: one input shows the change, the other keeps its result; both execute on both trees;
        # naming cases on a contract item after preflight is an authorization the request must give
        unquoted = self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                                boundaryInputs=["test_value", "test_kept"]))
        self.assertEqual(unquoted.returncode, 2, marker + ": " + unquoted.stdout + unquoted.stderr)
        self.assertIn("contract cases added", unquoted.stderr, marker)
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", basis="Make the value two.",
                                     boundaryInputs=["test_value", "test_kept"]),
                    pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                     boundaryInputs=["test_kept"]))
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertEqual(receipt["contractChanges"], ["BM_CHANGE: contract cases added: test_kept, test_value: Make the value two."], marker)
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "@unittest.skipIf(app.value == 1, 'original only')\n    def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        lines = self.open_lines(receipt)
        self.assertTrue(any(line.startswith("BM_CHANGE") and "test_kept skipped on original" in line for line in lines),
                        marker + ": " + json.dumps(receipt))

    def test_preservation_item_rejects_a_rewritten_expectation(self):
        marker = "REWRITTEN_EXPECTATION_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_KEEP")
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("preserved", "passed"), json.dumps(receipt))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_REWRITTEN')")
        receipt = self.tdd("BM_KEEP")
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("changed", "in-progress"), marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("test_kept differs (original=failed, current=passed)", line, marker)
        self.assertIn("authorized contract change", line, marker)
        (self.repo / "app.py").write_text("value = 1\nkept = 1\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)")
        self.tdd("BM_KEEP")
        self.assertEqual(self.status()["tdd"], "passed", marker)
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_REWRITTEN')")
        self.tdd("BM_KEEP")
        self.assertEqual(self.status()["tdd"], "in-progress", marker)
        converted = lambda basis: pending_behavior("BM_KEEP", behavior="kept becomes two", expected="kept is two",
                                                   basis=basis, boundaryInputs=["test_kept"])
        for basis in ("Keep everything else.", "Keep everything else the module already does.", "Keep.",
                      "else the module already does, including the kept value."):
            refused = self.update(converted(basis))
            self.assertEqual(refused.returncode, 2, marker + ": " + basis + refused.stdout + refused.stderr)
            self.assertIn("BM_KEEP", refused.stderr, marker)
        # re-pointing the preservation case away from the differing one needs the same authorization,
        # as does releasing the differing case, or attaching an unrelated finding to the conversion
        repointed = self.update(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                                 boundaryInputs=["test_value"]))
        self.assertEqual(repointed.returncode, 2, marker + ": " + repointed.stdout + repointed.stderr)
        self.assertIn("test_kept", repointed.stderr, marker)
        released = self.update(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                                boundaryInputs=["test_kept"], released={"reason": "kept=1 is unreachable now", "case": "test_kept"}))
        self.assertEqual(released.returncode, 2, marker + ": " + released.stdout + released.stderr)
        self.assertIn("printed note", released.stderr, marker)
        note = self.case.tmp / "note.json"
        note.write_text(json.dumps({"findings": [{"id": "N-1", "claim": "an unrelated note", "material": False, "kind": "nonbehavioral"}]}))
        self.assertEqual(self.case.cli("record", "review", "--repo", str(self.repo), "--input", str(note)).returncode, 0, marker)
        intake = json.loads(self.case.cli("status", "--repo", str(self.repo)).stdout)["findingStates"][0]["intakeEvidenceId"]
        owned = self.update(converted("Keep everything else the module already does.")
                            | {"sourceRefs": [{"type": "finding", "evidenceId": intake, "id": "N-1"}]})
        self.assertEqual(owned.returncode, 2, marker + ": " + owned.stdout + owned.stderr)
        accepted = self.update(converted("Report kept as two only when the request says so."))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)
        receipt = self.tdd("BM_KEEP")
        self.assertEqual(self.status()["tdd"], "passed", marker + ": " + json.dumps(receipt))
        change = "BM_KEEP: preservation -> contract: Report kept as two only when the request says so."
        self.assertEqual(receipt["contractChanges"], [change], marker)
        self.assertIn(change, self.summary(), marker)
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        self.assertEqual(packet["behavior-map"]["contractChanges"], [change], marker)
        # a contract item added or re-worded after preflight needs the request sentence too
        added = self.update(pending_behavior("BM_MORE", behavior="value becomes three", expected="value is three"))
        self.assertEqual(added.returncode, 2, marker + ": " + added.stdout + added.stderr)
        reworded = self.update(converted("Keep everything else the module already does.") | {"expected": "kept is exactly two"})
        self.assertEqual(reworded.returncode, 2, marker + ": " + reworded.stdout + reworded.stderr)
        self.assertIn("contract expectation re-worded", reworded.stderr, marker)
        reworded = self.update(converted("Report kept as two only when the request says so.") | {"expected": "kept is exactly two"})
        self.assertEqual(reworded.returncode, 0, marker + ": " + reworded.stdout + reworded.stderr)
        self.assertIn(change, self.summary(), marker)

        # a release bound to an unrelated passing case does not waive the differing one
        self.update(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["test_kept"]))
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_REWRITTEN')", "def test_other(self): self.assertEqual(app.value, 1)")
        self.tdd("BM_KEEP")
        waived = self.update(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                              boundaryInputs=["test_kept"], released={"reason": "kept=1 unreachable", "case": "test_other"}))
        self.assertEqual(waived.returncode, 2, marker + ": " + waived.stdout + waived.stderr)
        self.assertIn("printed note", waived.stderr, marker)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + self.summary())
        self.assertIn("test_kept differs", self.summary(), marker)
        # a new attack claims the case only for a material behavioral finding, and is listed with it
        fixture = lambda ref, basis: pending_behavior("BM_FIX", behavior="kept becomes two", expected="kept is two", basis=basis,
                                                      boundaryInputs=["test_kept"], sourceRefs=[{"type": "finding", "evidenceId": ref, "id": "N-1"}])
        unowned = self.update(fixture(intake, "an unrelated note"))
        self.assertEqual(unowned.returncode, 2, marker + ": " + unowned.stdout + unowned.stderr)
        note.write_text(json.dumps({"findings": [{"id": "N-1", "claim": "kept must become two", "material": True, "kind": "behavioral"}]}))
        self.assertEqual(self.case.cli("record", "review", "--repo", str(self.repo), "--input", str(note)).returncode, 0, marker)
        behavioral = json.loads(self.case.cli("status", "--repo", str(self.repo)).stdout)["findingStates"][-1]["intakeEvidenceId"]
        self.assertEqual(self.update(fixture(behavioral, "finding N-1 attack")).returncode, 0, marker)
        self.assertIn("BM_FIX: contract item added after preflight (finding N-1): finding N-1 attack", self.summary(), marker)
        receipt = self.tdd("BM_KEEP", "BM_FIX")
        self.assertTrue(all(line.startswith("BM_KEEP") for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))

    def test_unnamed_preservation_owns_its_differing_cases(self):
        marker = "REWRITTEN_EXPECTATION_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_REWRITTEN')", "def test_other(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_KEEP")
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        narrowed = self.update(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                                boundaryInputs=["test_other"]))
        self.assertEqual(narrowed.returncode, 0, marker + ": " + narrowed.stdout + narrowed.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + self.summary())
        self.assertIn("test_kept differs", self.summary(), marker)
        # naming it on a contract item claims the change, with the request sentence
        claimed = self.update(pending_behavior("BM_NEW", behavior="kept becomes two", expected="kept is two",
                                               basis="Report kept as two only when the request says so.", boundaryInputs=["test_kept"]))
        self.assertEqual(claimed.returncode, 0, marker + ": " + claimed.stdout + claimed.stderr)
        receipt = self.tdd("BM_KEEP", "BM_NEW")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_boundary_inputs_are_proved_by_execution(self):
        marker = "UNEXECUTED_BOUNDARY_PASSED"
        self.begin(pending_behavior("BM_EDGE", kind="preservation", behavior="the edge holds", expected="edge is one",
                                    boundaryInputs=["test_edge"]))
        for body in ("def test_value(self): self.assertEqual(app.value, 1)  # test_edge",
                     "@unittest.skip('later')\n    def test_edge(self): self.assertEqual(app.value, 1)",
                     "def test_edge_more(self): self.assertEqual(app.value, 1)"):
            self.probe(body)
            receipt = self.tdd("BM_EDGE")
            self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + body + json.dumps(receipt))
            self.assertEqual(len(self.open_lines(receipt)), 1, marker + ": " + body + json.dumps(receipt))
            [line] = self.open_lines(receipt)
            self.assertIn("BM_EDGE (preservation)", line, marker)
            self.assertIn("test_edge", line.split(";", 1)[1], marker + ": " + line)
        self.probe("def test_edge(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_EDGE")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        # an existing test outside a narrowed selection keeps its owner open
        self.update(pending_behavior("BM_EDGE", kind="preservation", behavior="the edge holds", expected="edge is one",
                                     boundaryInputs=["test_edge", "test_other"]))
        self.probe("def test_edge(self): self.assertEqual(app.value, 1)", "def test_other(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_EDGE", command=(sys.executable, "-m", "unittest", "test_value.Value.test_edge"))
        self.assertEqual(len(self.open_lines(receipt)), 1, marker + ": " + json.dumps(receipt))
        self.assertIn("test_other", self.open_lines(receipt)[0], marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_EDGE")
        self.assertEqual(self.open_lines(receipt), [], marker + ": " + json.dumps(receipt))
        # a pair declared on a printed-line probe needs both inputs printed
        self.update(pending_behavior("BM_PAIR", kind="preservation", behavior="X decides OUT in P=T,K=T",
                                     expected="a: OUT, b: none", boundaryInputs=["X | P=T,K=T | a", "X | P=T,K=T | b"]))
        receipt = self.tdd("BM_PAIR", command=(sys.executable, "-c", "import app\nprint('X | P=T,K=T | a:', app.value)"))
        [line] = self.open_lines(receipt)
        self.assertIn("X | P=T,K=T | b", line, marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_PAIR", command=(sys.executable, "-c",
                                               "import app\nprint('X | P=T,K=T | a:', app.value)\nprint('X | P=T,K=T | b: none')"))
        self.assertEqual(self.open_lines(receipt), [], marker + ": " + json.dumps(receipt))

    def test_narrowed_selection_is_a_limitation(self):
        marker = "NARROWING_UNREPORTED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)", "def test_other(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-m", "unittest", "test_value.Value.test_kept"))
        self.assertEqual(receipt.get("limitations"), ["narrowed selection: test_value.Value.test_kept; tests outside it are not compared"],
                         marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-m", "unittest", "-k", "kept", "test_value"))
        self.assertEqual(receipt.get("limitations"), ["narrowed selection: -k kept; tests outside it are not compared"],
                         marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_KEEP")
        self.assertEqual(receipt.get("limitations"), [], marker + ": " + json.dumps(receipt))

    def test_unsettled_reading_keeps_tdd_incomplete(self):
        marker = "UNSETTLED_READING_PASSED"
        readings = ["kept compares by value", "kept compares by identity"]
        self.begin(pending_behavior("BM_READ", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    interpretations=readings))
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_READ")
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("preserved", "in-progress"), marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("readings unsettled: kept compares by value | kept compares by identity", line, marker)
        self.assertIn("readings unsettled", self.summary(), marker)
        settled = pending_behavior("BM_READ", kind="preservation", behavior="kept stays one", expected="kept is one",
                                   interpretations=readings, interpretation=readings[0], authority="the recorded request")
        half = self.update({key: value for key, value in settled.items() if key != "authority"})
        self.assertEqual(half.returncode, 2, marker + ": " + half.stdout + half.stderr)
        full = self.update(settled)
        self.assertEqual(full.returncode, 0, marker + ": " + full.stdout + full.stderr)
        self.assertEqual(self.status()["tdd"], "passed", marker + ": " + self.summary())

    def test_release_is_bound_to_an_executed_probe_note(self):
        marker = "RELEASE_INVISIBLE"
        self.begin(pending_behavior("BM_CTX", kind="preservation", behavior="X decides OUT when P=T,K=T,Q=T",
                                    expected="a: OUT, b: none", boundaryInputs=["X | P=T,K=T,Q=T | a", "X | P=T,K=T,Q=T | b"]),
                   pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"))
        note = "X | P=T,K=T,Q=T: unreachable - Q requires K false in parse()"
        command = (sys.executable, "-c", f"import app\nprint({note!r})")
        receipt = self.tdd("BM_CTX", command=command)
        self.assertEqual(self.status()["tdd"], "in-progress", json.dumps(receipt))
        release = lambda **fields: pending_behavior("BM_CTX", kind="preservation", behavior="X decides OUT when P=T,K=T,Q=T",
                                                    expected="a: OUT, b: none",
                                                    boundaryInputs=["X | P=T,K=T,Q=T | a", "X | P=T,K=T,Q=T | b"], **fields)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        differing = self.tdd("BM_CTX", command=(sys.executable, "-c", "import app\nprint('X | P=T,K=T,Q=T:', app.value)"))
        self.assertEqual(differing["comparison"], "changed", json.dumps(differing))
        for fields in ({"released": {"reason": "cannot construct", "case": "X | nowhere"}},
                       {"released": {"reason": "value changed, so unreachable", "case": "X | P=T,K=T,Q=T"}},
                       {"released": {"reason": "", "case": "X | P=T,K=T,Q=T"}},
                       {"released": {"reason": "Q with K is rejected", "case": "X | P=T,K=T,Q=T"},
                        "interpretations": ["Q is parsed", "Q is ignored"]}):
            refused = self.update(release(**fields))
            self.assertEqual(refused.returncode, 2, marker + ": " + json.dumps(fields) + refused.stdout + refused.stderr)
        contract = self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                                released={"reason": "not needed", "case": "X | P=T,K=T,Q=T"}))
        self.assertEqual(contract.returncode, 2, marker + ": " + contract.stdout + contract.stderr)
        (self.repo / "app.py").write_text("value = 1\nkept = 1\n")
        receipt = self.tdd("BM_CTX", command=command)
        accepted = self.update(release(released={"reason": "Q requires K false in parse()", "case": "X | P=T,K=T,Q=T"}))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)
        released = "released: BM_CTX: Q requires K false in parse() (X | P=T,K=T,Q=T)"
        self.assertIn(released, self.summary(), marker)
        self.assertNotIn("BM_CTX", json.loads(self.case.cli("status", "--repo", str(self.repo)).stdout).get("open", ""), marker)
        receipt = self.tdd("BM_CTX", command=command)
        self.assertEqual(receipt["released"], [released], marker + ": " + json.dumps(receipt))
        self.assertFalse(any(line.startswith("BM_CTX") for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        self.assertEqual(packet["behavior-map"]["released"], [released], marker)
        # the lead's own unverified note neither proves the case nor releases it
        unverified = "X | P=T,K=F,Q=T | a: unverified - fixture cannot set Q"
        self.update(pending_behavior("BM_UNV", kind="preservation", behavior="X decides OUT when P=T,K=F,Q=T",
                                     expected="a: OUT, b: none", boundaryInputs=["X | P=T,K=F,Q=T | a"]))
        receipt = self.tdd("BM_UNV", command=(sys.executable, "-c", f"print({unverified!r})"))
        self.assertEqual(receipt["comparison"], "preserved", json.dumps(receipt))
        [line] = [line for line in self.open_lines(receipt) if line.startswith("BM_UNV")]
        self.assertIn("X | P=T,K=F,Q=T | a unverified", line, marker + ": " + json.dumps(receipt))
        refused = self.update(pending_behavior("BM_UNV", kind="preservation", behavior="X decides OUT when P=T,K=F,Q=T",
                                               expected="a: OUT, b: none", boundaryInputs=["X | P=T,K=F,Q=T | a"],
                                               released={"reason": "measurement unavailable", "case": "X | P=T,K=F,Q=T | a"}))
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("unverified", refused.stderr, marker)
        # a release settles only while its comparison is current and still prints the note unchanged
        (self.repo / "app.py").write_text("value = 3\nkept = 1\n")
        self.assertTrue(any(line.startswith("BM_CTX") and "stale" in line for line in self.open_lines(self.tdd("BM_UNV", command=(sys.executable, "-c", f"print({unverified!r})")))), marker)
        receipt = self.tdd("BM_CTX", command=(sys.executable, "-c", "import app\nprint('X | P=T,K=T,Q=T: unverified - lost')"))
        [line] = [line for line in self.open_lines(receipt) if line.startswith("BM_CTX")]
        self.assertIn("release unbound", line, marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_CTX", command=(sys.executable, "-c", "import app\nprint('nothing')"))
        self.assertTrue(any(line.startswith("BM_CTX") and "was not executed" in line for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_CTX", command=(sys.executable, "-c", f"import app\nprint({note!r} if app.value == 3 else 'nothing')"))
        self.assertTrue(any(line.startswith("BM_CTX") and "not printed on both trees" in line for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))

    def test_readiness_is_one_result_across_consumers(self):
        marker = "READINESS_NOT_SHARED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"))
        self.probe("def test_value(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(len(self.open_lines(receipt)), 1, marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        verify = self.case.cli("verify", "--repo", str(self.repo), "--", sys.executable, "-c", "print('checked')")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertIn(line, json.loads(verify.stdout.splitlines()[-1])["next"]["input"], marker + ": " + verify.stdout)
        self.assertIn(line, self.summary(), marker)
        complete = self.case.cli("complete", "--repo", str(self.repo))
        self.assertNotEqual(complete.returncode, 0, marker)
        self.assertIn("BM_CHANGE", complete.stdout + complete.stderr, marker)
        hook = subprocess.run([sys.executable, str(harness.ROOT / "hooks" / "rcf-intake-gate.py")], cwd=self.repo, env=self.case.env,
                              text=True, capture_output=True, input=json.dumps({"cwd": str(self.repo), "tool_name": "spawn_agent",
                                                                               "tool_input": {"agent_type": "default"}}))
        self.assertIn('"deny"', hook.stdout, marker + ": " + hook.stdout + hook.stderr)
        self.assertIn("tdd", hook.stdout, marker)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "passed", marker)
        reopened = self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                                basis="Make the value two.", boundaryInputs=["test_value", "test_missing"]))
        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + self.summary())
        self.assertIn("test_missing", self.summary(), marker)
        verify = json.loads(self.case.cli("verify", "--repo", str(self.repo), "--", sys.executable, "-c", "print('checked')").stdout.splitlines()[-1])
        self.assertIn("test_missing", verify["next"]["input"], marker + ": " + json.dumps(verify))

    def test_subtests_attribute_to_their_method(self):
        marker = "SUBTEST_PARENT_UNATTRIBUTED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_loop"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_loop(self):\n        for i in (1, 2, 3):\n            with self.subTest(i=i): self.assertEqual(app.value, 2, 'LOOP_NOT_TWO')",
                   "def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "passed", marker + ": " + json.dumps(receipt))
        self.assertEqual(receipt["cases"], ["test_loop (test_value.Value.test_loop): original=failed (3 subtests), current=passed; "
                                            "AssertionError: 1 != 2 : LOOP_NOT_TWO"], marker)
        self.assertEqual([(arm["source"], arm["unnamed"]) for arm in receipt["arms"]], [("original", 0), ("current", 3)], marker)
        self.assertIn("current: 3 cases unnamed (passing subtests print no name)", receipt["limitations"], marker)

    def test_printed_lines_are_named_cases(self):
        marker = "PRINTED_CASE_UNATTRIBUTED"
        (self.repo / "app.sh").write_text("value=1\nkept=1\n")
        self.case.git("add", "app.sh")
        self.case.git("commit", "-qm", "shell app")
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["kept"]))
        (self.repo / "probe.test.sh").write_text('. ./app.sh\necho "value: $value"\necho "kept: $kept"\n')
        (self.repo / "app.sh").write_text("value=2\nkept=1\n")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "probe.test.sh"))
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("changed", "passed"), marker + ": " + json.dumps(receipt))
        self.assertEqual(receipt["cases"], ["value: original=1, current=2"], marker)
        (self.repo / "app.sh").write_text("value=2\nkept=2\n")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "probe.test.sh"))
        self.assertEqual(len(self.open_lines(receipt)), 1, marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("BM_KEEP (preservation)", line, marker + ": " + json.dumps(receipt))
        self.assertIn("kept differs (original=1, current=2)", line, marker)

    def test_unattributed_output_is_not_proof(self):
        marker = "UNEXECUTED_BOUNDARY_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-c", "import app\nprint(app.kept)"))
        self.assertEqual((receipt["comparison"], self.status()["tdd"]), ("preserved", "in-progress"), marker + ": " + json.dumps(receipt))
        self.assertIn("no attributable case executed", self.open_lines(receipt)[0], marker)
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-c", "import app\nprint('kept: unverified - unavailable')\nprint('kept:', app.kept)"))
        [line] = self.open_lines(receipt)
        self.assertIn("kept unverified", line, marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-c", "import app\nprint('kept:', app.kept)"))
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-c", "import app\nprint('done: ok')\nprint('kept is', app.kept)"))
        [line] = self.open_lines(receipt)
        self.assertIn("unnamed output differs", line, marker + ": " + json.dumps(receipt))

    def test_nameless_contract_on_several_cases_must_name_them(self):
        marker = "CONTRACT_WITHOUT_CHANGE_PASSED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"))
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_kept(self):\n        for i in (20, 2):\n            with self.subTest(i=i): self.assertEqual(19 * app.kept - 18 if i == 20 else app.kept, i)")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        self.assertIn("several cases executed: name the requested ones", self.open_lines(receipt)[0], marker)
        self.assertEqual(receipt["cases"], [
            "test_kept (test_value.Value.test_kept): original=failed (2 subtests), current=passed; "
            "AssertionError: 1 != 20; AssertionError: 1 != 2",
            "test_value (test_value.Value.test_value): original=failed, current=passed; AssertionError: 1 != 2 : VALUE_NOT_TWO",
        ], "VIEW_NOT_COMPACT: " + json.dumps(receipt))
        # a subtest message contained in an earlier one is a distinct assertion (the runner's stored
        # shape keeps a trailing newline; the folding rule must not compare by substring)
        from hooks.lib import behavior_map
        folded, _ = behavior_map.folded_cases({"arms": [{"requestedTree": "a", "outcome": "failed", "cases": {
            "test_kept (m.T.test_kept) (i=20)": {"outcome": "failed", "assertion": "AssertionError: 1 != 20"},
            "test_kept (m.T.test_kept) (i=2)": {"outcome": "failed", "assertion": "AssertionError: 1 != 2"}}},
            {"requestedTree": "b", "outcome": "passed", "cases": {"test_kept (m.T.test_kept)": {"outcome": "passed"}}}]})
        self.assertEqual(folded["test_kept (m.T.test_kept)"]["original"]["assertion"],
                         "AssertionError: 1 != 20\nAssertionError: 1 != 2", "VIEW_NOT_COMPACT: " + json.dumps(folded))

    def test_contract_batch_cannot_ignore_an_unowned_regression(self):
        marker = "REWRITTEN_EXPECTATION_PASSED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["test_other"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_REWRITTEN')",
                   "def test_other(self): self.assertIsInstance(app.kept, int)")
        self.tdd("BM_KEEP", command=(sys.executable, "-m", "unittest", "test_value.Value.test_other"))
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("BM_CHANGE (contract)", line, marker)
        self.assertIn("test_kept differs and no item names it", line, marker)
        self.assertIn("test_kept differs and no item names it", self.summary(), "READINESS_NOT_SHARED")

    def test_view_keeps_every_distinct_assertion_and_summary_keeps_the_question(self):
        marker = "VIEW_NOT_COMPACT"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["test_kept"]))
        (self.repo / "app.py").write_text("value = 1\nkept = 3\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2, 'KEPT_WRONG')")
        receipt = self.tdd("BM_KEEP")
        self.assertEqual(receipt["cases"], ["test_kept (test_value.Value.test_kept): original=failed, current=failed; "
                                            "AssertionError: 1 != 2 : KEPT_WRONG; AssertionError: 3 != 2 : KEPT_WRONG"], marker + ": " + json.dumps(receipt))
        self.assertIn("BM_KEEP (preservation): kept stays one => kept is one; no current valid comparison; test_kept failed on original, failed on current",
                      self.summary(), "READINESS_NOT_SHARED: " + self.summary())

    def test_legacy_items_without_kind_ask_for_one(self):
        marker = "LEGACY_ITEM_AUTO_PROVED"
        from hooks.lib import behavior_map
        passed = {"test_value (test_value.Value.test_value)": {"outcome": "passed"}}
        legacy = {"id": "BM_OLD", "basis": "recorded before kind", "behavior": "value stays", "seam": "app", "expected": "1",
                  "comparison": {"valid": True, "fresh": True, "runIndex": 0, "comparison": "preserved", "command": "x",
                                 "arms": [{"requestedTree": "a", "outcome": "passed", "error": "", "cases": passed},
                                          {"requestedTree": "b", "outcome": "passed", "error": "", "cases": passed}]}}
        [item] = behavior_map.runtime_items([legacy])
        self.assertEqual(behavior_map.unresolved([item]), ["BM_OLD"], marker)
        self.assertIn("declare kind", behavior_map.open_obligations([item])[0], marker)
        self.assertEqual(comparison_view(item["comparison"])["comparison"], "preserved", marker)
        # an earlier recorder's fields and typed inputs load as absent, never refuse a recorded map
        recorded = {**legacy, "kind": "preservation", "redFailure": "X", "status": "green", "proofBinding": {},
                    "boundaryInputs": [{"now": 1}], "interpretations": ["one"], "interpretation": "one"}
        [item] = behavior_map.runtime_items([recorded])
        self.assertEqual({key for key in item if key in {"redFailure", "status", "proofBinding", "interpretations", "interpretation"}}, set(), marker)
        self.assertEqual(item["boundaryInputs"], [], marker)
        self.assertIn("recorded boundary inputs are not executed case names", behavior_map.open_obligations([item])[0], marker)
        del recorded["boundaryInputs"]
        self.assertEqual(behavior_map.unresolved(behavior_map.runtime_items([recorded])), [], marker)
        with self.assertRaises(ValueError, msg=marker):
            behavior_map.initial_items([recorded])
        # through the real CLI: a recorded map whose typed inputs loaded as the sentinel stays open until
        # executed case names replace them; an update that omits boundaryInputs cannot erase the sentinel
        from hooks.lib._workflow_db import evidence_write, mutation
        from hooks.lib.repo_identity import resolve_repo_identity
        old = lambda **fields: pending_behavior("BM_OLD", kind="preservation", behavior="value stays", expected="value is one", **fields)
        self.begin(old())
        with mutation(resolve_repo_identity(self.repo)) as transaction:
            state = dict(transaction.state)
            preflight = json.loads(json.dumps(transaction.evidence(state["preflightEvidence"])))
            preflight["document"]["behaviorMap"][0]["boundaryInputs"] = [{"now": 1}]
            write = evidence_write(str(state["workflowId"]), "preflight", preflight)
            transaction.write([write])
            transaction.append({**state, "preflightEvidence": write.evidence_id, "preflightLatestEvidence": write.evidence_id},
                               "legacy-fixture")
        self.probe("def test_value(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_OLD")
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        self.assertIn("recorded boundary inputs are not executed case names", "".join(self.open_lines(receipt)), marker)
        unmapped = self.update(old())
        self.assertEqual(unmapped.returncode, 0, marker + ": " + unmapped.stdout + unmapped.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": an update without boundaryInputs erased the unmapped inputs")
        self.assertIn("recorded boundary inputs are not executed case names", self.summary(), marker)
        mapped = self.update(old(boundaryInputs=["test_value"]))
        self.assertEqual(mapped.returncode, 0, marker + ": " + mapped.stdout + mapped.stderr)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_receipt_and_packet_share_one_compact_view(self):
        marker = "VIEW_NOT_COMPACT"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept", "test_other"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_kept(self): self.assertEqual(app.kept, 1)",
                   "def test_other(self): self.assertEqual(app.kept, 1)",
                   "def test_same_text(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_broken(self): self.assertEqual(app.kept, 3, 'ALWAYS_BROKEN')",
                   "def test_loop(self):\n        for i in (1, 2):\n            with self.subTest(i=i): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertEqual(receipt["comparison"], "incomplete", json.dumps(receipt))
        self.assertTrue({"runIndex", "comparison", "arms", "cases", "limitations"} <= set(receipt), marker + ": " + json.dumps(receipt))
        view = {key: receipt[key] for key in ("runIndex", "comparison", "arms", "cases", "limitations")}
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        [compared] = [item["comparison"] for item in packet["behavior-map"]["items"] if "cases" in item.get("comparison", {})]
        self.assertEqual({key: compared[key] for key in view}, view, marker + ": " + json.dumps(compared))
        for key in ("sourceDelta", "coverage", "output", "observation"):
            self.assertNotIn(key, json.dumps(compared), marker + ": " + key)
        self.assertEqual(receipt["cases"], [
            "test_broken (test_value.Value.test_broken): original=failed, current=failed; AssertionError: 1 != 3 : ALWAYS_BROKEN",
            "2 cases: test_same_text (test_value.Value.test_same_text), test_value (test_value.Value.test_value): "
            "original=failed, current=passed; AssertionError: 1 != 2 : VALUE_NOT_TWO",
        ], marker)
        self.assertNotIn("test_kept", json.dumps(receipt["cases"]), marker)
        self.assertEqual([(arm["source"], arm["outcome"], arm["testsExecuted"], arm["unnamed"]) for arm in receipt["arms"]],
                         [("original", "failed", 6, 0), ("current", "failed", 6, 0)], marker + ": " + json.dumps(receipt["arms"]))
        self.assertEqual(packet["behavior-map"]["open"], self.open_lines(receipt), marker)
        self.assertEqual(packet["behavior-map"]["openCount"], 2, marker)
        self.assertIsInstance(receipt["summaryId"], str, marker)

    def test_open_obligations_are_questions(self):
        marker = "OBLIGATION_QUESTION_MISSING"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        self.probe("def test_value(self): self.assertEqual(app.value, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        lines = self.open_lines(receipt)
        self.assertEqual([line.split(" ")[0] for line in lines], ["BM_CHANGE", "BM_KEEP"], marker + ": " + json.dumps(receipt))
        self.assertTrue(all(" => " in line and ";" in line for line in lines), marker + ": " + json.dumps(lines))
        self.assertIn("test_kept missing on original, current", lines[1], marker)
        self.assertNotIn("MC/DC contexts", json.dumps(receipt), marker)
        status = json.loads(self.case.cli("status", "--repo", str(self.repo), "--fields", "nextAction,tdd").stdout)
        self.assertEqual(status, {"nextAction": "tdd", "tdd": "in-progress"}, marker)
