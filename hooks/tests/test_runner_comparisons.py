"""Real public proof-runner comparisons, with independent Git and process state."""
import json
import os
import signal
import socket
import shutil
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
        reader = subprocess.run([sys.executable, str(self.case.workflow), "summary", "--repo", str(self.case.repo)],
                                env={**self.case.env, "UNRELATED_READER_VARIABLE": "1"}, text=True, capture_output=True)
        self.assertIn("Probes compared=1/1", reader.stdout, "READER_ENVIRONMENT_STALED_PROOF: " + reader.stdout)

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
                   "--", sys.executable, "-m", "unittest", "test_value.Value.test_value")
        first = case.cli(*command)
        self.assertEqual(first.returncode, 0, "TEST_ENVIRONMENT_MISSING: " + first.stdout + first.stderr)
        self.assertEqual([a["outcome"] for a in json.loads(first.stdout)["arms"]], ["failed", "passed"])
        expected_calls = ["1", "2"]
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
        self.assertEqual([a["outcome"] for a in json.loads(changed.stdout)["arms"]], ["failed", "failed"])
        self.assertEqual(sorted(calls.read_text().splitlines()), sorted(expected_calls + ["1", "2"]))

    def test_quality_gate_refreshes_comparisons_only_after_success(self):
        self.assertEqual(self.operation(2).returncode, 0)
        case = self.case

        def runs():
            state = json.loads(case.cli("status", "--repo", str(case.repo)).stdout)
            return json.loads(case.cli("evidence", "--repo", str(case.repo), "--evidence-id",
                                      state["tddEvidence"], "--full").stdout)["document"]["runs"]

        initial = len(runs())
        (case.repo / "app.py").write_text("from typing import Any\nvalue: Any = 2\n")
        command = ("verify", "--repo", str(case.repo), "--kind", "quality-gate", "--base-ref", "HEAD")
        failed = case.cli(*command)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertEqual(len(runs()), initial, "FAILED_GATE_RAN_COMPARISONS")
        (case.repo / "app.py").write_text("value: int = 2\n")
        passed = case.cli(*command)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        refreshed = runs()
        self.assertEqual(len(refreshed), initial + 1, "QUALITY_GATE_DID_NOT_REFRESH_PROOF")
        self.assertTrue(refreshed[-1]["valid"])
        self.assertEqual(case.cli(*command).returncode, 0)
        self.assertEqual(len(runs()), len(refreshed), "UNCHANGED_GATE_REPEATED_COMPARISON")
        (case.repo / "app.py").write_text("value: int = 3\n")
        self.assertEqual(case.cli(*command).returncode, 2, "FAILING_COMPARISON_PASSED_VERIFICATION")
        self.assertFalse(runs()[-1]["valid"])
        (case.repo / "app.py").write_text("value: int = 2\n")
        missing = pending_behavior("BM_MISSING", behavior="another obligation", expected="its own proof")
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
        arms = json.loads(result.stdout)["arms"]
        self.assertEqual(json.loads(result.stdout)["comparison"], "changed", result.stdout + result.stderr)
        self.assertEqual(arms[-1]["observation"], "- b 1\n+ b 2", "CHANGED_CASES_HIDDEN: " + repr(arms))

    def test_only_a_probe_whose_own_process_runs_the_change_is_proof(self):
        case = self.case
        (case.repo / "app.py").write_text("def decide(x):\n    return x + 1\n")
        case.git("commit", "-qam", "decision owner")
        slug, _ = case.begin_with_map([self.item(2)])
        (case.repo / "app.py").write_text("def decide(x):\n    return x + 2\n")
        (case.repo / "test_value.py").write_text(
            "import subprocess, sys, unittest, app\nclass Value(unittest.TestCase):\n"
            "    def test_outer(self):\n"
            "        child = subprocess.run([sys.executable, '-c', 'import app; print(app.decide(1))'], capture_output=True, text=True)\n"
            "        self.assertEqual(child.stdout.strip(), '3', 'DECISION')\n"
            "    def test_owner(self):\n"
            "        self.assertEqual(app.decide(1), 3, 'DECISION')\n")
        def compare(test):
            raw = case.cli("tdd", "--repo", str(case.repo), "--slug", slug, "--behavior-id", "BM_VALUE", "--",
                           sys.executable, "-m", "unittest", f"test_value.Value.{test}")
            return raw.returncode, json.loads(raw.stdout)["comparison"]
        self.assertEqual(compare("test_outer"), (2, "incomplete"), "OUTER_PROBE_ADMITTED")
        self.assertEqual(compare("test_owner"), (0, "changed"), "OWNER_PROBE_REFUSED")

    def test_verify_refuses_test_runs_the_comparisons_cover(self):
        self.operation(2)
        before = self.ledger_counts()
        for form in ((), ("--observed",)):
            refused = self.case.cli("verify", "--repo", str(self.case.repo), *form, "--",
                                    sys.executable, "-m", "unittest", "test_value")
            self.assertEqual(refused.returncode, 2, "DUPLICATE_TEST_RUN_ADMITTED: " + refused.stdout + refused.stderr)
        self.assertEqual(self.ledger_counts(), before, "DUPLICATE_TEST_RUN_ADMITTED")
        self.assertEqual(self.case.cli("verify", "--repo", str(self.case.repo), "--", "git", "diff", "--check").returncode, 0)

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
        result = self.operation(2)
        marker = "RECORDED_SOURCE_COMPARISON_MISSING: " + result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, marker)
        receipt = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(receipt.get("comparison"), "changed", marker)
        self.assertEqual([arm["outcome"] for arm in receipt["arms"]], ["failed", "passed"], marker)
        self.assertNotEqual(receipt["arms"][0]["tree"], receipt["arms"][1]["tree"], marker)

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
        update.write_text(json.dumps({"items": [self.item(), pending_behavior("BM_OTHER")]}))
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

    def fix(self, evidence, receipt=None):
        path = self.case.tmp / "fixed.json"
        path.write_text(json.dumps({"intakeEvidenceId": evidence, "dispositions": [{
            "finding_id": "R1", "status": "fixed",
            **({"evidenceRefs": [f"{receipt['summaryId']}:{receipt['runIndex']}"]} if receipt else {}),
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
        evidence, _ = self.repair()
        test = self.case.repo / "test_review.py"
        test.write_text(test.read_text() + "\n# changed support before closure\n")
        stale = self.fix(evidence)
        self.assertEqual(stale.returncode, 2, "STALE_SUPPORT_CLOSED_FINDING: " + stale.stdout + stale.stderr)
        current = self.case.cli("tdd", "--repo", str(self.case.repo), "--behavior-id", "BM_VALUE",
                                "--", sys.executable, "-m", "unittest", "test_review")
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertEqual([arm["outcome"] for arm in json.loads(current.stdout)["arms"]], ["passed", "failed", "passed"],
                         "STALE_SUPPORT_CLOSED_FINDING: " + current.stdout)
        result = self.fix(evidence)
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
                self.cancel_probe(signum, "tdd", "--behavior-id", "BM_VALUE", "--", sys.executable, "-m", "unittest", "test_value")

    def test_refresh_cancellation_reaps_the_executing_probe(self):
        self.cancel_probe(signal.SIGTERM, "verify", "--kind", "quality-gate", "--base-ref", "HEAD")

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
            process.communicate(timeout=5)
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
