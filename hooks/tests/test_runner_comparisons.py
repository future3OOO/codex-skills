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

    def test_quality_gate_leaves_stale_proof_to_tdd(self):
        self.assertEqual(self.operation().returncode, 0)
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
        (case.repo / "app.py").write_text("value: int = 1\n")
        passed = case.cli("verify", "--repo", str(case.repo), "--kind", "quality-gate")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertEqual(len(runs()), initial, "QUALITY_GATE_RAN_COMPARISONS")
        receipt = json.loads(passed.stdout.strip().splitlines()[-1])
        self.assertEqual((receipt["kind"], receipt["nextAction"]), ("quality-gate", "tdd"), "STALE_PROOF_NOT_ROUTED_TO_TDD")
        rerun = case.cli("tdd", "--repo", str(case.repo), "--behavior-id", "BM_VALUE")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual((len(runs()), json.loads(case.cli("status", "--repo", str(case.repo)).stdout)["tdd"]),
                         (initial + 1, "passed"), "RERUN_DID_NOT_RESTORE_PROOF")

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
            "            with self.subTest(name=name): self.assertEqual(actual, expected, name)\n"
            "    def test_shared(self):\n"
            "        for index, message in enumerate(('BOTH', 'ORIGINAL' if app.value == 1 else 'CURRENT')):\n"
            "            with self.subTest(index=index): self.fail(message)\n"
            "    def test_wide(self):\n"
            "        for expected in (10, 20, 30, 40):\n"
            "            with self.subTest(expected=expected): self.assertEqual(app.value, expected)\n")
        result = case.cli("tdd", "--repo", str(case.repo), "--behavior-id", "BM_VALUE", "--",
                          sys.executable, "-m", "unittest", "test_value")
        compact = json.loads(result.stdout)
        self.assertEqual(compact["cases"], [
            "test_loop (test_value.Value.test_loop): original=failed (stopped): 1 != 2; current=failed (stopped): 2 != 1",
            "test_partitions (test_value.Value.test_partitions): original=failed (1 subtests): 1 != 2 : requested; "
            "current=failed (1 subtests): False != True : preserved",
            "test_preserved (test_value.Value.test_preserved): original=passed; current=failed (stopped): False is not true : PRESERVATION_CHANGE",
            "test_shared (test_value.Value.test_shared): original=failed (2 subtests): BOTH; ORIGINAL; current=failed (2 subtests): CURRENT; as above",
            "test_wide (test_value.Value.test_wide): original=failed (4 subtests): 1 != 10; 1 != 20; "
            "1 != 30; 1 != 40; current=failed (4 subtests): 2 != 10; "
            "2 != 20; 2 != 30; 2 != 40",
            "test_requested (test_value.Value.test_requested): original=failed (stopped): 1 != 2 : REQUESTED_CHANGE; current=passed",
        ], "STOPPED_EXECUTION_HIDDEN: " + result.stdout)
        self.assertEqual(compact["limitations"], ["original: 1 cases unnamed (passing subtests print no name)",
                                                  "current: 1 cases unnamed (passing subtests print no name)", STOPPED_NOTE],
                         "STOPPED_EXECUTION_HIDDEN: " + result.stdout)
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

    def test_cancellation_during_arm_setup_removes_the_snapshot(self):
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
        process = subprocess.Popen([sys.executable, str(case.workflow), "tdd", "--repo", str(case.repo), "--behavior-id",
                                    "BM_VALUE"], cwd=case.repo, env={**case.env, "TMPDIR": str(arms)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 60
        while not any(arms.glob("workflow-proof-*/source")) and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertTrue(any(arms.glob("workflow-proof-*/source")), "arm setup did not start")
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
        self.assertEqual(list(arms.glob("workflow-proof-*")), [], "SETUP_CANCEL_LEAKED: interrupted arm checkout remains")

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
STOPPED_NOTE = ("stopped: a test method that failed in its body skipped its remaining statements on that tree, "
                "so inputs after the failure did not execute there")
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

    def relaxed(self, changed, original, marker):
        """An item's kind and obligation may change; the changed item reads stale until a rerun proves it."""
        result = self.update(changed)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": a changed item read proved")
        self.assertEqual(self.update(original).returncode, 0, marker)

    def update(self, *items):
        path = self.case.tmp / "map.json"
        path.write_text(json.dumps({"items": list(items)}))
        return self.case.cli("record", "tdd-map", "--repo", str(self.repo), "--input", str(path))

    def open_lines(self, receipt):
        return list(receipt["open"])

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
        self.assertEqual(receipt["cases"], ["test_value (test_value.Value.test_value): original=failed (stopped): "
                                            "1 != 2 : VALUE_NOT_TWO; current=passed"], marker)
        # a shared batch needs each owner to name its cases
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two"),
                    pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertEqual(sorted(line.split(" ")[0] for line in self.open_lines(receipt)), ["BM_CHANGE", "BM_KEEP"], marker + ": " + json.dumps(receipt))
        self.assertTrue(all("name its cases in boundaryInputs" in line for line in self.open_lines(receipt)), marker)
        # a contract pair: one input shows the change, the other keeps its result; both execute on both trees
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", basis="Make the value two.",
                                     boundaryInputs=["test_value", "test_kept"]),
                    pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                     boundaryInputs=["test_kept"]))
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertIn("BM_CHANGE: contract cases added: test_kept, test_value: Make the value two.", self.summary(), marker)
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')",
                   "@unittest.skipIf(app.value == 1, 'original only')\n    def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        lines = self.open_lines(receipt)
        self.assertTrue(any(line.startswith("BM_CHANGE") and "test_kept skipped on original" in line for line in lines),
                        marker + ": " + json.dumps(receipt))

    def test_kind_change_requires_its_own_comparison(self):
        marker = "KIND_CHANGE_REUSED_OLD_PROOF"
        keep = pending_behavior("BM_KEEP", kind="preservation", behavior="kept is measured", expected="kept stays unchanged",
                                boundaryInputs=["kept"])
        self.begin(keep, intent="Keep kept unchanged.")
        (self.repo / "probe.test.py").write_text("import app\nassert isinstance(app.kept, int)\nprint('kept:', app.kept)\n")
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        command = (sys.executable, "probe.test.py")
        self.tdd("BM_KEEP", command=command)
        self.assertEqual(self.update(keep | {"kind": "contract"}).returncode, 0, marker)
        self.assertEqual(self.status()["tdd"], "in-progress", marker)
        self.assertFalse(self.tdd("BM_KEEP", command=command).get("reused", False), marker)

    def test_changed_item_stays_open_until_rerun(self):
        marker = "APPROVED_OBLIGATION_REWRITTEN"
        keep = pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                boundaryInputs=["test_kept"])
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"])
        spare = pending_behavior("BM_SPARE", kind="preservation", behavior="value stays an int", expected="value is an int",
                                 boundaryInputs=["test_int"])
        later = pending_behavior("BM_LATER", kind="preservation", behavior="kept stays below two", expected="kept < 2",
                                 boundaryInputs=["test_late"])
        self.begin(keep, change, spare, intent="Make the value two. Keep kept unchanged.")
        probes = lambda kept: ("def test_value(self): self.assertEqual(app.value, 2)",
                               f"def test_kept(self): self.assertEqual(app.kept, {kept})",
                               "def test_late(self): self.assertLess(app.kept, 2)",
                               "def test_int(self): self.assertIsInstance(app.value, int)")
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe(*probes(2))
        self.tdd("BM_KEEP", "BM_CHANGE")
        snapshot = lambda: (self.status()["tdd"], self.summary().split("Open map:", 1)[-1])
        before = snapshot()
        self.assertEqual(before[0], "in-progress", marker)
        for name, items in (("a preservation sentence offered as authority", [keep | {"kind": "contract", "basis": "Keep kept unchanged."}]),
                            ("an unmet contract downgraded", [change | {"kind": "preservation"}]),
                            ("a mixed update", [later, keep | {"kind": "contract"}])):
            changed = self.update(*items)
            self.assertEqual(changed.returncode, 0, f"{marker}: {name}: {changed.stdout}{changed.stderr}")
            self.assertEqual(snapshot()[0], "in-progress", f"{marker}: {name} read proved")
            self.assertEqual(self.update(keep, change).returncode, 0, marker)
        # case names attach freely: replacing an open item's cases keeps it open, and its invariant stays
        replaced = self.update(keep | {"boundaryInputs": ["test_value"]})
        self.assertEqual(replaced.returncode, 0, "CASE_NAMES_FROZEN: " + replaced.stdout + replaced.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", "CASE_NAMES_FROZEN")
        self.assertEqual(self.update(keep).returncode, 0, "CASE_NAMES_FROZEN")
        hook = subprocess.run([sys.executable, str(harness.ROOT / "hooks" / "rcf-intake-gate.py")], cwd=self.repo, env=self.case.env,
                              text=True, capture_output=True, input=json.dumps({"cwd": str(self.repo), "tool_name": "spawn_agent",
                                                                               "tool_input": {"agent_type": "default"}}))
        self.assertIn('"deny"', hook.stdout, marker + ": " + hook.stdout + hook.stderr)
        # a stale comparison keeps the changed item open, and a finding reference does not relax it
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n# touched\n")
        self.relaxed(keep | {"kind": "contract"}, keep, marker + ": stale comparison")
        findings = self.case.tmp / "findings.json"
        findings.write_text(json.dumps({"findings": [{"id": "R-1", "claim": "kept must become two", "material": True, "kind": "behavioral"}]}))
        self.assertEqual(self.case.cli("record", "review", "--repo", str(self.repo), "--input", str(findings)).returncode, 0, marker)
        intake = self.status()["findingStates"][-1]["intakeEvidenceId"]
        ref = {"sourceRefs": [{"type": "finding", "evidenceId": intake, "id": "R-1"}]}
        self.relaxed(keep | {"kind": "contract"} | ref, keep | ref, marker + ": finding-owned")
        # a new attack for the finding is evidence, listed with its finding for review
        fix = pending_behavior("BM_FIX", behavior="value becomes two for R-1", expected="value is two", boundaryInputs=["test_value"],
                               basis="finding R-1 attack", sourceRefs=[{"type": "finding", "evidenceId": intake, "id": "R-1"}])
        self.assertEqual(self.update(fix).returncode, 0, marker)
        self.assertIn("BM_FIX: contract item added after preflight (finding R-1): finding R-1 attack", self.summary(), marker)
        # an item added after preflight stays open the same way once its comparison fails
        self.assertEqual(self.update(later).returncode, 0, marker)
        self.tdd("BM_LATER")
        self.relaxed(later | {"kind": "contract"}, later, marker + ": failing later item relabeled")
        # before any comparison the lead may revise an item; the revision is listed against the preflight
        revised = self.update(spare | {"kind": "contract", "basis": "the value type may change"})
        self.assertEqual(revised.returncode, 0, marker + ": " + revised.stdout + revised.stderr)
        self.assertIn("BM_SPARE: preservation -> contract: the value type may change", self.summary(), marker)
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        self.assertIn("BM_SPARE: preservation -> contract: the value type may change", packet["behavior-map"]["contractChanges"], marker)
        self.assertEqual(self.update(spare).returncode, 0, marker)
        # repaired code closes every open item
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe(*probes(1))
        receipt = self.tdd("BM_KEEP", "BM_CHANGE", "BM_LATER", "BM_SPARE", "BM_FIX")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_contract_cases_extend_without_quote(self):
        marker = "EVIDENCE_EXTENSION_REFUSED"
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"])
        self.begin(change)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_more(self): self.assertEqual(app.value * 3, 6)")
        self.tdd("BM_CHANGE")
        extended = self.update(change | {"basis": "extends the batch", "boundaryInputs": ["test_value", "test_more"]})
        self.assertEqual(extended.returncode, 0, marker + ": " + extended.stdout + extended.stderr)
        receipt = self.tdd("BM_CHANGE")
        self.assertIn("BM_CHANGE: contract cases added: test_more: extends the batch", self.summary(), marker)
        self.assertEqual(self.status()["tdd"], "passed", marker + ": " + json.dumps(receipt))
        # an added case is judged like every other
        self.update(change | {"basis": "extends the batch", "boundaryInputs": ["test_value", "test_more", "test_broken"]})
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_more(self): self.assertEqual(app.value * 3, 6)",
                   "def test_broken(self): self.assertEqual(app.value, 3)")
        [line] = self.open_lines(self.tdd("BM_CHANGE"))
        self.assertIn("test_broken failed on original, failed on current", line, marker + ": " + line)

    def test_open_item_waits_for_a_comparison_that_proves_it(self):
        marker = "OPEN_ITEM_RELEASED"
        keep = pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                boundaryInputs=["test_kept"])
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"])
        self.begin(keep, change, intent="Make the value two. Keep kept unchanged.")
        probes = lambda kept: ("def test_value(self): self.assertEqual(app.value, 2)",
                               f"def test_kept(self): self.assertEqual(app.kept, {kept})",
                               "def test_int(self): self.assertIsInstance(app.kept, int)")
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe(*probes(1))
        self.tdd("BM_KEEP", "BM_CHANGE")
        for field in ("behavior", "seam", "expected"):  # rewording drops the comparison, so it is refused too
            self.relaxed(keep | {field: "reworded"}, keep, f"{marker}: {field} reworded")
            self.relaxed(change | {field: "reworded"}, change, f"{marker}: {field} reworded")
        # a later comparison that leaves the cases missing or unverified does not prove the item
        for command in ((sys.executable, "-m", "unittest", "test_value.Value.test_int"),
                        (sys.executable, "-c", "print('test_kept: unverified - fixture lost'); print('test_value: unverified - fixture lost')")):
            self.tdd("BM_KEEP", "BM_CHANGE", command=command)
            for relaxed, original in ((keep | {"kind": "contract", "basis": "Keep kept unchanged."}, keep),
                                      (change | {"kind": "preservation", "boundaryInputs": ["test_int"]}, change)):
                self.relaxed(relaxed, original, marker + ": " + " ".join(command))
            self.assertEqual(self.status()["tdd"], "in-progress", marker)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        receipt = self.tdd("BM_KEEP", "BM_CHANGE")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        self.assertEqual(self.update(keep | {"expected": "kept is exactly one"}).returncode, 0, marker + ": proved item stays revisable")

    def test_wrapped_report_beside_printed_output_is_judged(self):
        marker = "MIXED_BATCH_HIDDEN"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        self.probe("def test_int(self): self.assertIsInstance(app.value, int)")
        (self.repo / "probe.test.py").write_text("import app\nprint(f'kept is {app.kept}')\n")
        batch = ("bash", "-c", f"{sys.executable} -m unittest -v test_value 2>&1; {sys.executable} probe.test.py")
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        receipt = self.tdd("BM_KEEP", command=batch)
        self.assertIn("unnamed output differs", " ".join(self.open_lines(receipt)), marker + ": " + json.dumps(receipt))
        (self.repo / "app.py").write_text("value = 1\nkept = 1\n# touched\n")
        receipt = self.tdd("BM_KEEP", command=batch)
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_every_subtest_label_form_fails_its_test(self):
        marker = "LABELLED_SUBTEST_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        for subtest in ("'X | K=T | a', n=n", "'a] b - c'"):
            self.probe(f"def test_kept(self):\n        for n in (1, 2):\n            with self.subTest({subtest}): self.assertEqual(app.kept, 1)")
            receipt = self.tdd("BM_KEEP", command=(sys.executable, "-m", "pytest", "-q", "test_value.py"))
            [line] = self.open_lines(receipt)
            self.assertIn("test_kept passed on original, failed on current", line, marker + ": " + subtest + line)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]))
        self.probe("def test_value(self):\n        for n in (1, 2):\n            with self.subTest('X | P=T | a', n=n): self.assertEqual(app.value, 2)")
        receipt = self.tdd("BM_CHANGE", command=(sys.executable, "-m", "pytest", "-q", "test_value.py"))
        self.assertFalse(any(line.startswith("BM_CHANGE") for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))

    def test_printed_output_beside_a_changed_report_is_judged(self):
        marker = "MIXED_BATCH_HIDDEN"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_int"]))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_int(self): self.assertIsInstance(app.value, int)")
        (self.repo / "probe.test.py").write_text("import app\nprint(f'kept is {app.kept}')\n")
        batch = ("bash", "-c", f"{sys.executable} -m unittest -v test_value 2>&1; {sys.executable} probe.test.py")
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=batch)
        lines = [line for line in self.open_lines(receipt) if line.startswith("BM_KEEP")]
        self.assertTrue(lines and "unnamed output differs" in lines[0], marker + ": " + json.dumps(receipt))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=batch)
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_repaired_code_proves_the_item(self):
        marker = "REPAIR_UNPROVED"
        operation = "value = {}\nkept = 1\ndef run():\n    print('kept:', kept)\n"
        (self.repo / "app.py").write_text(operation.format(1))
        self.case.git("add", "app.py")
        self.case.git("commit", "-qm", "an operation")
        keep = pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept: 1", boundaryInputs=["kept"])
        test = pending_behavior("BM_TEST", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["test_kept"])
        loose = pending_behavior("BM_LOOSE", kind="preservation", behavior="the module keeps its results", expected="unchanged")
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"])
        self.begin(keep, test, loose, change, intent="Make the value two. Keep everything else.")
        pytest = (sys.executable, "-m", "pytest", "-q", "test_value.py")
        proved = lambda receipt, *ids: not [line for line in self.open_lines(receipt) if line.split(" ", 1)[0] in ids]
        # an import broken mid-edit, and a switch of runner while it is broken
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)")
        (self.repo / "app.py").write_text(operation.format(1) + "import no_such_module\n")
        for item, command in (("BM_TEST", None), ("BM_LOOSE", None), ("BM_LOOSE", pytest)):
            self.tdd(item, **({"command": command} if command else {}))
        (self.repo / "app.py").write_text(operation.format(1))
        for item, command in (("BM_TEST", None), ("BM_LOOSE", None), ("BM_LOOSE", pytest)):
            receipt = self.tdd(item, **({"command": command} if command else {}))
        self.assertTrue(proved(receipt, "BM_TEST", "BM_LOOSE"), marker + ": repaired import: " + json.dumps(receipt))
        # a mixed batch on a correct edit, then the split commands its receipt advises
        (self.repo / "app.py").write_text(operation.format(2))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_kept(self): self.assertEqual(app.kept, 1)")
        (self.repo / "probe.test.py").write_text("import app\napp.run()\n")
        self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "-c", f"{sys.executable} -m unittest test_value 2>&1; {sys.executable} probe.test.py"))
        self.tdd("BM_CHANGE")
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "probe.test.py"))
        self.assertTrue(proved(receipt, "BM_CHANGE", "BM_KEEP"), marker + ": split batch: " + json.dumps(receipt))

    def test_report_span_starts_at_the_runner_first_line(self):
        marker = "REPORT_SPAN_WRONG"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_x_int"]))
        self.probe('def test_value(self):\n        """The value."""\n        self.assertEqual(app.value, 2)',
                   "def test_x_int(self): self.assertIsInstance(app.value, int)")
        (self.repo / "probe.test.py").write_text("import app\nprint('kept is', app.kept)\n")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        for flags in ("", "-v "):  # the progress line; the bare id line of a test with a docstring
            receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=(
                "bash", "-c", f"{sys.executable} -m unittest {flags}test_value 2>&1; {sys.executable} probe.test.py"))
            self.assertNotIn("unnamed output differs", " ".join(self.open_lines(receipt)), marker + ": " + flags + json.dumps(receipt))
        # a printed record before the report is the command's own output, also when its name holds `::`
        (self.repo / "probe.test.py").write_text("import app\nprint('app::kept:', app.kept)\n")
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        receipt = self.tdd("BM_KEEP", command=(
            "bash", "-c", f"{sys.executable} probe.test.py; {sys.executable} -m unittest -v test_value.Value.test_x_int 2>&1"))
        self.assertTrue(any(line.startswith("BM_KEEP") and "unnamed output differs" in line for line in self.open_lines(receipt)),
                        marker + ": " + json.dumps(receipt))

    def test_output_outside_every_report_is_judged(self):
        marker = "REPORT_SEGMENT_HIDDEN"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_x_int"]))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_x_int(self): self.assertIsInstance(app.value, int)")
        (self.repo / "probe.test.py").write_text("import app\nprint('kept is', app.kept)\n")
        unittest_run = f"{sys.executable} -m unittest -v test_value 2>&1"
        batches = (f"{sys.executable} probe.test.py; {unittest_run}",  # the original arm fails on the requested change
                   f"{unittest_run}; {sys.executable} probe.test.py; {unittest_run}")  # a probe between two reports
        for batch in batches:
            (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
            receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "-c", batch))
            self.assertTrue(any(line.startswith("BM_KEEP") and "unnamed output differs" in line for line in self.open_lines(receipt)),
                            marker + ": " + batch + json.dumps(receipt))
            (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
            receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "-c", batch))
            self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + batch + json.dumps(receipt))

    def test_shared_batch_judges_each_case_under_its_owner(self):
        marker = "SIBLING_CHANGE_UNEXPLAINED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_kept(self): self.assertEqual(app.kept, 1)")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        # the shared batch run for the preservation item alone: the sibling's mapped change is the sibling's
        receipt = self.tdd("BM_KEEP")
        self.assertEqual([line.split(" ", 1)[0] for line in self.open_lines(receipt)], ["BM_CHANGE"], marker + ": " + json.dumps(receipt))
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        # a regression fails the shared batch for every item that runs it, and stays its owner's question
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual([line.split(" ", 1)[0] for line in self.open_lines(receipt)], ["BM_CHANGE", "BM_KEEP"], marker + ": " + json.dumps(receipt))
        [line] = [line for line in self.open_lines(self.tdd("BM_KEEP")) if line.startswith("BM_KEEP")]
        self.assertIn("test_kept passed on original, failed on current", line, marker + ": " + line)

    def test_predicted_case_names_give_way_to_executed_cases(self):
        marker = "CASE_NAMES_FROZEN"
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value_planned"])
        self.begin(change, intent="Make the value two.")
        paused = json.loads(self.case.cli("pause", "--repo", str(self.repo), "--reason", "inspect").stdout)
        self.assertIn("its cases: test_value_planned", paused["next"]["input"], "MAPPED_CASE_UNSHOWN")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        [line] = self.open_lines(self.tdd("BM_CHANGE"))
        self.assertIn("test_value_planned missing on original, current", line, marker + ": " + line)
        renamed = self.update(change | {"boundaryInputs": ["test_value"]})
        self.assertEqual(renamed.returncode, 0, marker + ": " + renamed.stdout + renamed.stderr)
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_readiness_reads_only_the_latest_run(self):
        marker = "READINESS_IGNORED_LATEST_RUN"
        kept, remainder = "def test_kept(self): self.assertEqual(app.kept, 1)", "def test_int(self): self.assertIsInstance(app.value, int)"
        unit = lambda *methods: UNITTEST_PROBE + "".join(f"    {method}\n" for method in methods)
        run = lambda *arguments: (sys.executable, *arguments)
        both = ["test_kept", "test_int"]
        # a current failure blocks; after a deletion, rewrite or narrower run the next passing run decides
        for name, names, edit, first, files, update, then in (
                ("accepted change, test deleted", both, "kept = 2\n", run("-m", "unittest", "test_value"),
                 {"test_value.py": unit(remainder)}, {"boundaryInputs": ["test_int"]}, run("-m", "unittest", "test_value")),
                ("accepted change, test rewritten", both, "kept = 2\n", run("-m", "unittest", "test_value"),
                 {"test_value.py": unit(kept.replace("1)", "2)"), remainder)}, {"kind": "contract"}, run("-m", "unittest", "test_value")),
                ("whole file deleted", both, "kept = 2\n", run("-m", "unittest", "test_value"),
                 {"test_value.py": None, "test_int.py": unit(remainder)}, {"boundaryInputs": ["test_int"]}, run("-m", "unittest", "test_int")),
                ("printed probe deleted", ["kept"], "kept = 1\nextra = 5\n", run("probe.test.py"),
                 {"probe.test.py": None}, {"boundaryInputs": ["test_kept"]}, run("-m", "unittest", "test_value")),
                ("narrower command", both, "kept = 2\n", run("-m", "unittest", "test_value"),
                 {}, {"boundaryInputs": ["test_int"]}, run("-m", "unittest", "test_value.Value.test_int"))):
            with self.subTest(name):
                self.setUp()
                keep = pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                        boundaryInputs=names)
                self.begin(keep, intent="Keep kept unchanged.")
                self.probe(kept, remainder)
                (self.repo / "probe.test.py").write_text("import app\nprint('kept:', app.kept)\n"
                                                         "if hasattr(app, 'extra'): print('extra:', app.extra)\n")
                (self.repo / "app.py").write_text("value = 1\n" + edit)
                receipt = self.tdd("BM_KEEP", command=first)
                self.assertEqual(self.status()["tdd"], "in-progress", f"{marker}: {name}: " + json.dumps(receipt))
                for path, text in files.items():
                    (self.repo / path).unlink() if text is None else (self.repo / path).write_text(text)
                self.assertEqual(self.update(keep | update).returncode, 0, marker)
                receipt = self.tdd("BM_KEEP", command=then)
                self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), f"{marker}: {name}: " + json.dumps(receipt))

    def test_a_later_contract_proves_its_change_after_a_crash(self):
        marker = "REQUESTED_CHANGE_UNCLEARABLE"
        (self.repo / "app.py").write_text("value = 1\nkept = 1\ndef scaled():\n    return 10\n")
        self.case.git("add", "app.py")
        self.case.git("commit", "-qm", "scaled")
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays", expected="kept 1", boundaryInputs=["kept"]),
                   intent="Make scaled twenty. Keep kept.")
        late = pending_behavior("BM_LATE", behavior="scaled becomes twenty", expected="scaled 20", boundaryInputs=["scaled"])
        self.assertEqual(self.update(late).returncode, 0, marker)
        (self.repo / "late.test.py").write_text("import app\nprint('scaled:', app.scaled())\n")
        (self.repo / "keep.test.py").write_text("import app\nprint('kept:', app.kept)\n")
        (self.repo / "app.py").write_text("value = 1\nkept = 1\ndef scaled():\n    raise RuntimeError('half done')\n")
        self.tdd("BM_LATE", command=(sys.executable, "late.test.py"))
        (self.repo / "app.py").write_text("value = 1\nkept = 1\ndef scaled():\n    return 20\n")
        self.tdd("BM_LATE", command=(sys.executable, "late.test.py"))
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "keep.test.py"))
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_governance_revalidation_reruns_stale_comparisons(self):
        marker = "REVALIDATION_STUCK_ON_STALE_PROOF"
        from hooks.lib.repo_identity import resolve_repo_identity
        from hooks.lib.workflow_state import invalidate_after_edit, set_phase
        from hooks.tests.support import commit_ready_envelope, record_context_forge
        _, wid = self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                             boundaryInputs=["test_value"]), intent="Make the value two.")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.tdd("BM_CHANGE")

        def close_out():
            for command in (("verify", "--", sys.executable, "-c", "pass"), ("verify", "--kind", "quality-gate", "--base-ref", "HEAD")):
                self.case.cli(*command, "--repo", str(self.repo))
            record_context_forge(self.repo, self.case.tmp)
            set_phase(resolve_repo_identity(self.repo), "code-review", "passed", findings="none")
            for command in (("record", "advisor-result", "--slug", "mapped-repair", "--workflow-id", wid, "--stage", "final",
                             "--source", "codex-advisor", "--input", commit_ready_envelope(self.case.tmp)),
                            ("record", "advisor-disposition", "--slug", "mapped-repair", "--workflow-id", wid, "--stage", "final",
                             "--findings", "none")):
                self.case.cli(*command, "--repo", str(self.repo))
            return self.case.cli("complete", "--repo", str(self.repo))

        self.assertEqual(close_out().returncode, 0, marker)
        for path in ("skills/x/SKILL.md", "AGENTS.md"):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(f"# governance edit {path}\n")
            invalidate_after_edit(resolve_repo_identity(self.repo), path)
            self.case.cli("verify", "--repo", str(self.repo), "--", sys.executable, "-c", "pass")
            self.assertEqual(self.status()["nextAction"], "tdd", f"{marker}: {path}")
            rerun = self.case.cli("tdd", "--repo", str(self.repo), "--behavior-id", "BM_CHANGE")
            self.assertEqual(rerun.returncode, 0, f"{marker}: {path}: " + rerun.stdout + rerun.stderr)
            completed = close_out()
            self.assertEqual(completed.returncode, 0, f"{marker}: {path}: " + completed.stdout + completed.stderr)

    def test_the_final_recheck_closes_its_own_findings(self):
        marker = "RECHECK_NEEDS_DISPOSITIONS"
        from hooks.lib.repo_identity import resolve_repo_identity
        from hooks.lib.workflow_state import invalidate_after_edit, set_phase
        from hooks.tests.support import record_context_forge
        _, wid = self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                             boundaryInputs=["test_value"]), intent="Make the value two.")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        identity = resolve_repo_identity(self.repo)

        def final(verdict, *ids):
            envelope = self.case.tmp / "final.json"
            envelope.write_text(json.dumps({"schemaVersion": 1, "verdict": verdict, "findings": [
                {"id": i, "claim": f"{i}: the value must stay two", "material": True, "kind": "behavioral"} for i in ids]}))
            return self.case.cli("record", "advisor-result", "--slug", "mapped-repair", "--workflow-id", wid, "--stage", "final",
                                 "--source", "codex-advisor", "--input", str(envelope), "--repo", str(self.repo))

        def correct(value):
            (self.repo / "app.py").write_text(f"value = 2\nkept = {value}\n")
            invalidate_after_edit(identity, "app.py")
            self.tdd("BM_CHANGE")
            record_context_forge(self.repo, self.case.tmp)

        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.tdd("BM_CHANGE")
        for command in (("verify", "--", sys.executable, "-c", "pass"), ("verify", "--kind", "quality-gate", "--base-ref", "HEAD")):
            self.case.cli(*command, "--repo", str(self.repo))
        record_context_forge(self.repo, self.case.tmp)
        set_phase(identity, "code-review", "passed", findings="none")
        self.assertEqual(final("fix-before-commit", "SPEC-1", "SPEC-2").returncode, 0, marker)
        self.assertEqual(self.status()["nextAction"], "address-review-findings", marker + ": the fix waits on dispositions")
        correct(3)
        self.assertEqual(self.status()["nextAction"], "final-review", marker)
        self.assertEqual(final("fix-before-commit", "SPEC-2").returncode, 0, marker)
        statuses = lambda: {e["findingId"]: e["status"] for e in self.status()["findingStates"]}
        self.assertEqual(statuses(), {"SPEC-1": "pending", "SPEC-2": "pending"}, marker + ": a fix-before-commit settled a finding")
        correct(4)
        gate = lambda: {k: self.status().get(k) for k in ("verification", "qualityGateManifestId", "nextAction")}
        bound = gate()
        for exit_code in (0, 1):  # an observed run is a receipt only, passing or failing
            self.case.cli("verify", "--observed", "--", sys.executable, "-c", f"raise SystemExit({exit_code})")
            self.assertEqual(gate(), bound, f"OBSERVED_RUN_DROPPED_GATE: exit {exit_code}")
        self.assertEqual(final("commit-ready").returncode, 0, marker)
        self.assertEqual((statuses(), self.status()["finalReview"]["findings"]),
                         ({"SPEC-1": "resolved", "SPEC-2": "resolved"}, "none"), marker + ": the commit-ready re-check left findings open")
        self.assertEqual(final("fix-before-commit", "SPEC-1").returncode, 0, marker)
        self.assertEqual(self.status()["findingStates"][-1].get("recurrence"), 1, "SETTLED_FINDING_RECURRENCE_LOST")
        correct(5)
        self.assertEqual(final("commit-ready").returncode, 0, marker)
        completed = self.case.cli("complete", "--repo", str(self.repo))
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stdout + completed.stderr)

    def test_renaming_a_requested_case_owes_nothing(self):
        marker = "RENAMED_REQUESTED_CHANGE_OWED"
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"])
        self.begin(change, pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                            boundaryInputs=["test_kept"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        kept = "def test_kept(self): self.assertEqual(app.kept, 1)"
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", kept)
        self.tdd("BM_CHANGE", "BM_KEEP")
        self.probe("def test_value_is_two(self): self.assertEqual(app.value, 2)", kept)
        self.assertEqual(self.update(change | {"boundaryInputs": ["test_value_is_two"]}).returncode, 0, marker)
        receipt = self.tdd("BM_CHANGE", "BM_KEEP")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_receipt_omits_contract_history_review_keeps_it(self):
        marker = "CONTRACT_NOISE_IN_RECEIPT"
        change = pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                  boundaryInputs=["test_value", "test_value_planned"])
        self.begin(change, intent="Make the value two.")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.assertEqual(self.update(change | {"boundaryInputs": ["test_value", "test_more"]}).returncode, 0, marker)
        receipt = self.tdd("BM_CHANGE")
        self.assertNotIn("contractChanges", receipt, marker + ": " + json.dumps(receipt))
        self.assertTrue(any("test_more missing on original, current" in line for line in self.open_lines(receipt)),
                        marker + ": " + json.dumps(receipt))
        dropped = "BM_CHANGE: contract cases dropped: test_value_planned"
        self.assertIn(dropped, self.summary(), marker)
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        self.assertTrue(any(line.startswith(dropped) for line in packet["behavior-map"]["contractChanges"]), marker + ": " + json.dumps(packet))

    def test_printed_lines_after_the_last_footer_are_judged(self):
        marker = "REPORT_TAIL_HIDDEN"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_x_int"]))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_x_int(self): self.assertIsInstance(app.value, int)")
        probe = f"{sys.executable} probe.test.py"
        batch = ("bash", "-c", f"{probe} before; {sys.executable} -m unittest -v test_value 2>&1; {probe} between; "
                               f"{sys.executable} -m unittest test_value 2>&1; {probe} after")
        for line, place in ((line, place) for line in ("...", "E", "-" * 70, "=" * 70, "checked (2 cases)", "loaded (cfg.main)")
                            for place in ("before", "between", "after")):
            (self.repo / "probe.test.py").write_text(f"import app, sys\nif sys.argv[1] == {place!r}:\n"
                                                     f"    print({line!r})\n    print('kept is', app.kept)\n")
            (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
            receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=batch)
            self.assertTrue(any(line.startswith("BM_KEEP") and "unnamed output differs" in line for line in self.open_lines(receipt)),
                            f"{marker if place == 'after' else 'REPORT_HEAD_HIDDEN'}: {line!r} {place}: " + json.dumps(receipt))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=batch)
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_diagnostics_never_become_printed_cases(self):
        marker = "DIAGNOSTIC_PSEUDO_CASE"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        (self.repo / "test_p.py").write_text("import app\ndef test_a(): assert app.value == 1\n"
                                             "def test_b(): assert {'k': app.kept} == {'k': 1}\n")
        (self.repo / "probe.test.py").write_text("import app, unittest\nprint('value:', app.value)\n"
                                                 "unittest.TestCase().assertEqual({'kept': app.kept, 'v': 1}, {'kept': 1, 'v': 1})\n")
        for command, allowed in ((("bash", "-c", f"{sys.executable} -m pytest -qq test_p.py 2>&1"), lambda name: "::" in name),
                                 ((sys.executable, "probe.test.py"), lambda name: name == "value")):
            receipt = self.tdd("BM_KEEP", command=command)
            evidence = json.loads(self.case.cli("evidence", "--repo", str(self.repo), "--evidence-id", receipt["summaryId"], "--full").stdout)
            names = {name for arm in evidence["document"]["runs"][receipt["runIndex"]]["arms"] for name in arm["cases"]}
            self.assertTrue(names and all(map(allowed, names)), marker + ": " + " ".join(command) + json.dumps(sorted(names)))

    def test_wrapped_runner_output_is_attributed_natively(self):
        marker = "WRAPPED_RUNNER_MISATTRIBUTED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual({'candidates': [app.value]}, {'candidates': [2]})",
                   "def test_kept(self): self.assertEqual(app.kept, 1)")
        for runner in (f"{sys.executable} -m unittest -v test_value 2>&1", f"{sys.executable} -m pytest -v test_value.py 2>&1",
                       f"{sys.executable} -m unittest -v test_value 2>&1 | sed '/^Ran /,$d'"):
            receipt = self.tdd("BM_CHANGE", "BM_KEEP", command=("bash", "-c", runner))
            self.assertEqual(self.status()["tdd"], "passed", marker + ": " + runner + json.dumps(receipt))
            [case] = receipt["cases"]
            self.assertTrue(case.startswith(("test_value (test_value.Value.test_value): ", "test_value.py::Value::test_value: ")),
                            marker + ": " + runner + json.dumps(receipt["cases"]))
        # a wrapped report naming no case gets the supported invocation once
        receipt = self.tdd("BM_KEEP", command=("bash", "-c", f"{sys.executable} -m unittest test_value.Value.test_kept 2>&1"))
        [note] = [line for line in receipt["limitations"] if "names no case" in line]
        self.assertIn("`python3 -m unittest ...` itself as the command", note, marker + ": " + note)
        self.assertIn("test_kept missing on original, current", " ".join(self.open_lines(receipt)), marker)
        # a printed probe that raises keeps its printed case and gains no traceback case; a rule-less FAIL: x is a record
        receipt = self.tdd("BM_CHANGE", command=(sys.executable, "-c", "import app\nprint('test_value:', app.value)\n"
                                                 "print('FAIL: x')\nassert app.value == 2, 'not two'"))
        self.assertFalse(any(line.startswith("BM_CHANGE") for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))
        self.assertNotIn("AssertionError", json.dumps(receipt["cases"]), marker)
        evidence = json.loads(self.case.cli("evidence", "--repo", str(self.repo), "--evidence-id", receipt["summaryId"], "--full").stdout)
        arms = evidence["document"]["runs"][receipt["runIndex"]]["arms"]
        self.assertEqual(sorted(arms[0]["cases"]), ["FAIL", "test_value"], marker + ": " + json.dumps(arms[0]["cases"]))

    def test_pytest_subtest_failure_fails_its_test(self):
        marker = "SUBTEST_FAILURE_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        for subtest in ("n=n", "'X | K=T | a (n)'", "label='a b) c'"):
            if subtest != "n=n":
                marker = "LABELLED_SUBTEST_PASSED"
            self.probe(f"def test_kept(self):\n        for n in (1, 2):\n            with self.subTest({subtest}): self.assertEqual(app.kept, 1)")
            receipt = self.tdd("BM_KEEP", command=(sys.executable, "-m", "pytest", "-q", "test_value.py"))
            self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + subtest + json.dumps(receipt))
            [line] = self.open_lines(receipt)
            self.assertIn("test_kept passed on original, failed on current", line, marker + ": " + subtest + line)

    def test_stopped_method_is_reported(self):
        marker = "STOPPED_EXECUTION_HIDDEN"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two across boundary cases", expected="value is two",
                                    boundaryInputs=["test_loop"]),
                   pending_behavior("BM_LOOP", behavior="each input reports two", expected="two", boundaryInputs=["empty", "one", "several"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_loop(self):\n        for name in ['empty', 'one', 'several']:\n            self.assertEqual(app.value, 2, name)")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(receipt["cases"], ["test_loop (test_value.Value.test_loop): original=failed (stopped): "
                                            "1 != 2 : empty; current=passed"], marker + ": " + json.dumps(receipt))
        [note] = [line for line in receipt["limitations"] if "stopped" in line]
        self.assertFalse({"one", "several"} & set(note.replace(",", " ").split()), marker + ": " + note)
        self.assertFalse(any(line.startswith("BM_CHANGE") for line in self.open_lines(receipt)), marker)
        # inputs the map declares as their own cases stay open while the original stops before them
        script = "import app\nfor name in ['empty', 'one', 'several']:\n    print(name + ':', app.value)\n    assert app.value == 2, name"
        receipt = self.tdd("BM_LOOP", command=(sys.executable, "-c", script))
        [line] = [line for line in self.open_lines(receipt) if line.startswith("BM_LOOP")]
        self.assertIn("one unnamed on original", line, marker + ": " + line)
        self.assertIn("several unnamed on original", line, marker + ": " + line)

    def test_domain_unverified_value_is_not_a_note(self):
        marker = "DOMAIN_VALUE_HELD"
        (self.repo / "app.py").write_text("value = 1\nkept = 1\nstatus = 'unverified'\n")
        self.case.git("add", "app.py")
        self.case.git("commit", "-qm", "a real account status")
        self.begin(pending_behavior("BM_STATUS", kind="preservation", behavior="new accounts remain unverified", expected="unverified",
                                    boundaryInputs=["account"]))
        receipt = self.tdd("BM_STATUS", command=(sys.executable, "-c", "import app\nassert app.status == 'unverified'\n"
                                                 "print('account: ' + app.status)"))
        self.assertEqual(self.status()["tdd"], "passed", marker + ": " + json.dumps(receipt))
        # the documented note still holds its item, and an assertion naming the word is an ordinary result
        receipt = self.tdd("BM_STATUS", command=(sys.executable, "-c", "print('account: unverified - the fixture cannot open one')"))
        [line] = self.open_lines(receipt)
        self.assertIn("account unverified", line, marker + ": " + line)
        self.update(pending_behavior("BM_WORD", behavior="status becomes verified", expected="verified", boundaryInputs=["test_word"]))
        self.probe("def test_word(self): self.assertEqual('verified' if app.value == 2 else 'unverified', 'verified')")
        (self.repo / "app.py").write_text("value = 2\nkept = 1\nstatus = 'unverified'\n")
        receipt = self.tdd("BM_WORD")
        self.assertFalse(any(line.startswith("BM_WORD") for line in self.open_lines(receipt)), marker + ": " + json.dumps(receipt))

    def test_receipt_renders_each_question_once(self):
        marker = "QUESTION_REPEATED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept", "test_other"]))
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        self.probe("def test_kept(self): self.assertEqual(app.kept, 2)", "def test_other(self): self.assertEqual(app.kept * 2, 4)")
        receipt = self.tdd("BM_KEEP")
        [line] = self.open_lines(receipt)
        self.assertNotIn(line, receipt["next"].get("input", ""), marker + ": " + json.dumps(receipt["next"]))
        self.assertEqual(line.count("must pass unchanged on both trees"), 1, marker + ": " + line)

    def test_shared_assertion_rendered_once(self):
        marker = "ASSERTION_REPEATED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_a", "test_b"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_a(self): self.fail('SHARED_DIAGNOSTIC')",
                   "def test_b(self):\n        if app.value == 2:\n            with self.subTest(i=1): self.fail('SHARED_DIAGNOSTIC')\n            self.fail('B_ONLY')")
        receipt = self.tdd("BM_KEEP")
        self.assertEqual(len(receipt["cases"]), 2, marker + ": " + json.dumps(receipt["cases"]))
        self.assertTrue(receipt["cases"][1].endswith("as above"), marker + ": a repeated assertion vanished unmarked")
        self.assertEqual(sum(line.count("SHARED_DIAGNOSTIC") for line in receipt["cases"]), 1, marker + ": " + json.dumps(receipt["cases"]))

    def test_short_name_matching_several_tests_stays_open(self):
        marker = "AMBIGUOUS_ALIAS_DISCHARGED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        (self.repo / "test_value.py").write_text(UNITTEST_PROBE + "    def test_value(self): self.assertEqual(app.value, 2)\n"
                                                 "class Other(unittest.TestCase):\n    def test_value(self): self.assertEqual(app.kept, 1)\n")
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("test_value matches several tests", line, marker + ": " + line)
        self.update(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                     boundaryInputs=["test_value (test_value.Value.test_value)"]))
        receipt = self.tdd("BM_CHANGE")
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))

    def test_named_preservation_still_judges_unnamed_output(self):
        marker = "UNNAMED_OUTPUT_IGNORED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one", boundaryInputs=["kept"]))
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        receipt = self.tdd("BM_KEEP", command=(sys.executable, "-c", "import app\nprint('kept:', app.kept)\nprint('total is', app.value + app.kept)"))
        self.assertEqual(self.status()["tdd"], "in-progress", marker + ": " + json.dumps(receipt))
        [line] = self.open_lines(receipt)
        self.assertIn("unnamed output differs", line, marker + ": " + line)

    def test_unrelated_failed_run_is_not_replayed(self):
        marker = "UNRELATED_TREE_REPLAYED"
        self.begin(pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two", boundaryInputs=["test_value"]),
                   pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        value, kept = ((sys.executable, "-m", "unittest", f"test_value.Value.{name}") for name in ("test_value", "test_kept"))
        self.probe("def test_value(self): self.assertEqual(app.value, 2)", "def test_kept(self): self.assertEqual(app.kept, 1)")
        (self.repo / "app.py").write_text("value = 2\nkept = 2\n")
        self.tdd("BM_KEEP", command=kept)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.tdd("BM_CHANGE", command=value)
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'REVISED')", "def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", command=value)
        self.assertEqual([arm["source"] for arm in receipt["arms"]], ["original", "current"], marker + ": " + json.dumps(receipt["arms"]))
        # this batch's own failed run on a broken edit returns when its probe is revised again
        (self.repo / "app.py").write_text("value = 3\nkept = 1\n")
        self.tdd("BM_CHANGE", command=value)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.tdd("BM_CHANGE", command=value)
        self.probe("def test_value(self): self.assertEqual(app.value, 2, 'REVISED AGAIN')", "def test_kept(self): self.assertEqual(app.kept, 1)")
        receipt = self.tdd("BM_CHANGE", command=value)
        self.assertEqual([arm["source"] for arm in receipt["arms"]], ["original", "earlier", "current"], marker + ": " + json.dumps(receipt["arms"]))
        self.assertEqual(receipt["cases"], ["test_value (test_value.Value.test_value): original=failed (stopped): 1 != 2 : "
                                            "REVISED AGAIN; earlier=failed (stopped): 3 != 2 : REVISED AGAIN; current=passed"], marker)

    def test_narrowing_reports_every_selection_form(self):
        marker = "NARROWING_MISREPORTED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one",
                                    boundaryInputs=["test_kept"]))
        self.probe("def test_kept(self): self.assertEqual(app.kept, 1)", "def test_other(self): self.assertEqual(app.value, 1)")
        for command, form in (((sys.executable, "-m", "pytest", "-qk", "kept", "test_value.py"), "-k kept"),
                              ((sys.executable, "-m", "pytest", "--deselect", "test_value.py::Value::test_other", "test_value.py"),
                               "--deselect test_value.py::Value::test_other"),
                              ((sys.executable, "-m", "unittest", "test_value.Value"), "test_value.Value")):
            receipt = self.tdd("BM_KEEP", command=command)
            self.assertEqual(receipt.get("limitations"), [f"narrowed selection: {form}; tests outside it are not compared"],
                             marker + ": " + json.dumps(receipt))

    def test_infeasible_context_needs_executed_cases(self):
        marker = "SELF_RELEASE_ACCEPTED"
        (self.repo / "app.py").write_text(
            "value = 1\nkept = 1\n"
            "def parse(k, q):\n    if k and q:\n        raise ValueError('K and Q are exclusive')\n    return k, q\n"
            "def decide(p, x, k, q):\n    k, q = parse(k, q)\n    return 'OUT' if p and x and (k or q) else None\n")
        self.case.git("add", "app.py")
        self.case.git("commit", "-qm", "a parse constraint")
        pair = ["X | P=T,K=T,Q=T | a", "X | P=T,K=T,Q=T | b"]
        context = pending_behavior("BM_CTX", kind="preservation", behavior="X decides OUT when P=T,K=T,Q=T",
                                   expected="unreachable: parse raises ValueError when K and Q are both true", boundaryInputs=pair)
        self.begin(context, pending_behavior("BM_CHANGE", behavior="value becomes two", expected="value is two",
                                             boundaryInputs=["value", *pair]))
        note = "X | P=T,K=T,Q=T: unreachable - parse rejects K and Q together"
        self.tdd("BM_CTX", command=(sys.executable, "-c", f"print({note!r})"))
        released = self.update(context | {"released": {"reason": "parse rejects K and Q", "case": "X | P=T,K=T,Q=T"}})
        self.assertEqual(released.returncode, 2, marker + ": " + released.stdout + released.stderr)
        self.assertIn("unknown fields: released", released.stderr, marker)
        self.assertEqual(self.status()["tdd"], "in-progress", marker)
        crashed = self.update(context | {"released": {"reason": "parse rejects K and Q", "case": "X | P=T,K=T,Q=T"}, "sourceRefs": 1})
        self.assertEqual(crashed.returncode, 2, marker + ": " + crashed.stdout + crashed.stderr)
        self.assertIn("sourceRefs must be an array", crashed.stderr, marker)
        self.assertNotIn("Traceback", crashed.stderr, marker)
        # the Interface itself shows the context cannot be reached, on both trees, for both item kinds
        (self.repo / "probe.test.py").write_text(
            "import app\nfor label, x in (('a', True), ('b', False)):\n    try:\n        result = app.decide(True, x, True, True)\n"
            "    except ValueError as error:\n        result = 'rejected by parse: ' + str(error)\n"
            "    print(f'X | P=T,K=T,Q=T | {label}: {result}')\nprint('value:', app.value)\n")
        (self.repo / "app.py").write_text((self.repo / "app.py").read_text().replace("value = 1", "value = 2"))
        receipt = self.tdd("BM_CTX", "BM_CHANGE", command=(sys.executable, "probe.test.py"))
        self.assertEqual((self.open_lines(receipt), self.status()["tdd"]), ([], "passed"), marker + ": " + json.dumps(receipt))
        packet = checkpoint_channels(self.repo, self.case.env, "code-review")
        [item] = [item for item in packet["behavior-map"]["items"] if item["id"] == "BM_CTX"]
        self.assertIn("parse raises ValueError", item["expected"], marker)
        # once the constraint changes, the same cases reopen the item
        (self.repo / "app.py").write_text((self.repo / "app.py").read_text().replace("if k and q:", "if False:"))
        receipt = self.tdd("BM_CTX", "BM_CHANGE", command=(sys.executable, "probe.test.py"))
        self.assertTrue(any(line.startswith("BM_CTX") and "X | P=T,K=T,Q=T | a differs" in line for line in self.open_lines(receipt)),
                        marker + ": " + json.dumps(receipt))

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

    def test_parametrized_cases_keep_their_parameter_in_the_question(self):
        marker = "UNEXECUTED_BOUNDARY_PASSED"
        self.begin(pending_behavior("BM_KEEP", kind="preservation", behavior="kept stays one", expected="kept is one"))
        (self.repo / "test_params.py").write_text("import app, pytest\n@pytest.mark.parametrize('n', [1, 2])\n"
                                                  "def test_kept(n):\n    assert app.kept * n == 2 * n\n")
        (self.repo / "app.py").write_text("value = 1\nkept = 2\n")
        for touch in range(3):  # separate processes: the label must not depend on set order
            receipt = self.tdd("BM_KEEP", command=(sys.executable, "-m", "pytest", "-q", "test_params.py"))
            self.assertEqual(receipt["comparison"], "changed", marker + ": " + json.dumps(receipt))
            [line] = self.open_lines(receipt)
            self.assertIn("test_kept[1] differs", line, marker + ": " + line)
            self.assertIn("test_kept[2] differs", line, marker + ": " + line)
            (self.repo / "app.py").write_text("value = 1\nkept = 2\n" + "# touch\n" * (touch + 1))

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
        dispatch = lambda: subprocess.run([sys.executable, str(harness.ROOT / "hooks" / "rcf-intake-gate.py")], cwd=self.repo,
                                          env=self.case.env, text=True, capture_output=True,
                                          input=json.dumps({"cwd": str(self.repo), "tool_name": "spawn_agent",
                                                            "tool_input": {"agent_type": "default"}}))
        hook = dispatch()
        self.assertIn('"deny"', hook.stdout, marker + ": " + hook.stdout + hook.stderr)
        self.assertIn("tdd", hook.stdout, marker)
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n")
        self.probe("def test_value(self): self.assertEqual(app.value, 2)")
        self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "passed", marker)
        # an edit after green leaves the proof stale: every reader routes back to tdd until it reruns
        (self.repo / "app.py").write_text("value = 2\nkept = 1\n# edited\n")
        status = self.status()
        self.assertEqual((status["tdd"], status["nextAction"]), ("in-progress", "tdd"), "STALE_PROOF_READ_AS_PASSED")
        self.assertIn("comparison stale", self.summary(), "STALE_PROOF_READ_AS_PASSED")
        checkpoint = json.loads(self.case.cli("checkpoint", "--repo", str(self.repo), "--phase", "code-review").stdout)
        self.assertIn("tdd", checkpoint["missing"], "STALE_PROOF_READ_AS_PASSED: " + json.dumps(checkpoint))
        self.assertIn("tdd", dispatch().stdout, "STALE_PROOF_READ_AS_PASSED")
        self.tdd("BM_CHANGE")
        self.assertEqual(self.status()["tdd"], "passed", "STALE_PROOF_READ_AS_PASSED")
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
        self.assertEqual(receipt["cases"], ["test_loop (test_value.Value.test_loop): original=failed (3 subtests): "
                                            "1 != 2 : LOOP_NOT_TWO; current=passed"], marker)
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
            "test_kept (test_value.Value.test_kept): original=failed (2 subtests): "
            "1 != 20; 1 != 2; current=passed",
            "test_value (test_value.Value.test_value): original=failed (stopped): 1 != 2 : VALUE_NOT_TWO; current=passed",
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
        self.assertEqual(receipt["cases"], ["test_kept (test_value.Value.test_kept): original=failed (stopped): 1 != 2 : KEPT_WRONG; "
                                            "current=failed (stopped): 3 != 2 : KEPT_WRONG"], marker + ": " + json.dumps(receipt))
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
            "test_broken (test_value.Value.test_broken): original+current=failed (stopped): 1 != 3 : ALWAYS_BROKEN",
            "2 cases: test_same_text (test_value.Value.test_same_text), test_value (test_value.Value.test_value): "
            "original=failed (stopped): 1 != 2 : VALUE_NOT_TWO; current=passed",
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
