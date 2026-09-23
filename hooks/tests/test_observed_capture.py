#!/usr/bin/env python3
"""Real hook and workflow CLI contracts for observed test commands."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("ISSUE96_PRODUCT_ROOT", Path(__file__).resolve().parents[2]))
HOOK = ROOT / "hooks/rcf-intake-gate.py"
WORKFLOW = ROOT / "skills/repo-production-workflow/scripts/workflow.py"


class ObservedCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="observed-capture-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "CODEX_WORKFLOW_STATE_ROOT": str(Path(self.temp.name) / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        for command in (["git", "init", "-q"], ["git", "config", "user.email", "test@example.invalid"],
                        ["git", "config", "user.name", "Test"]):
            subprocess.run(command, cwd=self.repo, check=True, capture_output=True)
        (self.repo / "test_ok.py").write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            " def test_ok(self): self.assertEqual(1, 1)\n"
        )
        (self.repo / "test_bad.py").write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            " def test_bad(self): self.fail('expected native failure')\n"
        )
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / "test_sub.py").write_text(
            "import unittest\nclass Check(unittest.TestCase):\n"
            " def test_sub(self): self.assertTrue(True)\n"
        )
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True, capture_output=True)
        started = self.cli("begin", "--slug", "observed", "--intent", "capture")
        self.assertEqual(started.returncode, 0, started.stderr)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True, capture_output=True,
        )

    def hook(self, command: str, *, cwd: Path | None = None) -> dict[str, object] | None:
        payload = {
            "tool_name": "exec_command", "cwd": str(self.repo),
            "tool_input": {"command": command, **({"workdir": str(cwd)} if cwd else {})},
        }
        run = subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(payload), cwd=self.repo,
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)["hookSpecificOutput"]["updatedInput"] if run.stdout else None

    def test_failure_preserves_exit_and_records_its_real_result(self) -> None:
        original = "python3 -m unittest test_bad"
        update = self.hook(original)
        self.assertIsNotNone(update, "OBSERVED_RESULT_MISMATCH")
        self.assertEqual(shlex.split(update["command"])[-3:], ["bash", "-lc", original],
                         "OBSERVED_SHELL_COMMAND_REPARSED")
        run = subprocess.run(["bash", "-lc", update["command"]], cwd=self.repo,
                             env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 1, "OBSERVED_RESULT_MISMATCH " + run.stderr)
        self.assertIn("expected native failure", run.stdout, "OBSERVED_RESULT_MISMATCH")
        history = self.cli("history")
        self.assertEqual(history.returncode, 0, history.stderr)
        kinds = [event["kind"] for event in json.loads(history.stdout)["events"]]
        self.assertEqual(kinds, ["begin", "record-verification"], "OBSERVED_RESULT_MISMATCH")

    def test_workdir_runs_in_the_command_checkout(self) -> None:
        update = self.hook("python3 -m unittest test_sub", cwd=self.repo / "sub")
        self.assertIsNotNone(update, "OBSERVED_RESULT_MISMATCH")
        run = subprocess.run(["bash", "-lc", update["command"]], cwd=self.repo / "sub",
                             env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, "OBSERVED_RESULT_MISMATCH " + run.stderr)
        self.assertIn("Ran 1 test", run.stdout, "OBSERVED_RESULT_MISMATCH")

    def test_relative_workdir_runs_the_selected_subdirectory_test(self) -> None:
        update = self.hook("python3 -m unittest test_sub", cwd=Path("sub"))
        self.assertIsNotNone(update, "RELATIVE_CWD_BROKEN")
        run = subprocess.run(["bash", "-lc", update["command"]], cwd=self.repo / "sub",
                             env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, "RELATIVE_CWD_BROKEN " + run.stderr)
        self.assertIn("Ran 1 test", run.stdout, "RELATIVE_CWD_BROKEN")

    def test_passing_command_keeps_exit_zero_when_its_tree_binding_changes(self) -> None:
        (self.repo / "test_mutate.py").write_text(
            "import pathlib, unittest\nclass Check(unittest.TestCase):\n"
            " def test_edit(self): pathlib.Path('test_ok.py').write_text('changed\\n')\n"
        )
        subprocess.run(["git", "add", "test_mutate.py"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "add mutating test"], cwd=self.repo, check=True, capture_output=True)
        update = self.hook("python3 -m unittest test_mutate")
        self.assertIsNotNone(update, "OBSERVED_ZERO_EXIT_CHANGED")
        run = subprocess.run(["bash", "-lc", update["command"]], cwd=self.repo,
                             env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, "OBSERVED_ZERO_EXIT_CHANGED " + run.stderr)
        self.assertIn('"valid": false', run.stdout, "OBSERVED_INVALID_BINDING_CLAIMED_VALID")

    def test_unsupported_shell_forms_pass_through(self) -> None:
        for command in (
            "python3 -m unittest test_ok && echo done",
            "python3 -m unittest test_ok &",
            "python3 -m unittest test_ok; echo done",
            "python3 -m unittest $TARGET",
            "python3 -m unittest test_ok\npython3 -m unittest test_bad",
            "python3 workflow.py verify --observed -- python3 -m unittest",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.hook(command), "CAPTURE_PASSTHROUGH_CHANGED")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        self.assertIsNone(self.hook("python3 -m unittest test_ok", cwd=outside),
                          "CAPTURE_PASSTHROUGH_CHANGED")
        history = self.cli("history")
        self.assertEqual(len(json.loads(history.stdout)["events"]), 1,
                         "CAPTURE_PASSTHROUGH_CHANGED")

    def test_nonexecuting_modes_are_not_recorded_as_tests(self) -> None:
        for command in ("pytest --help", "pytest --version", "python3 -m unittest --help",
                        "pytest --collect-only -q", "pytest --co -q", "pytest --fixtures",
                        "pytest --fixtures-per-test", "pytest --markers", "python3 -m pytest --setup-plan", "pytest --setup-only -q"):
            with self.subTest(command=command):
                self.assertIsNone(self.hook(command), "HELP_CAPTURE_FALSE_RUN")
        self.assertEqual(len(json.loads(self.cli("history").stdout)["events"]), 1, "HELP_CAPTURE_FALSE_RUN")

    def test_unreadable_ledger_does_not_block_the_shell_command(self) -> None:
        database, = Path(self.env["CODEX_WORKFLOW_STATE_ROOT"]).rglob("workflow.sqlite3")
        database.write_bytes(b"corrupt ledger")
        payload = {"tool_name": "exec_command", "cwd": str(self.repo),
                   "tool_input": {"command": "python3 -m unittest test_ok"}}
        run = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                             cwd=self.repo, env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, "CORRUPT_HOOK_BLOCKED")
        self.assertEqual(run.stdout, "", "CORRUPT_HOOK_BLOCKED")

    def test_shell_glob_keeps_the_original_test_arguments(self) -> None:
        command = "python3 -m unittest test_o?.py"
        original = subprocess.run(["bash", "-lc", command], cwd=self.repo,
                                  env=self.env, text=True, capture_output=True)
        self.assertEqual(original.returncode, 0, "CAPTURE_GLOB_CHANGED " + original.stderr)
        self.assertIn("Ran 1 test", original.stderr, "CAPTURE_GLOB_CHANGED")
        self.assertIsNone(self.hook(command), "CAPTURE_GLOB_CHANGED")
        history = self.cli("history")
        self.assertEqual(len(json.loads(history.stdout)["events"]), 1,
                         "CAPTURE_GLOB_CHANGED")

    def test_shell_hash_cannot_turn_zero_tests_into_success(self) -> None:
        command = "python3 -m unittest test_ok -k test_ok#no_match"
        original = subprocess.run(["bash", "-lc", command], cwd=self.repo,
                                  env=self.env, text=True, capture_output=True)
        self.assertEqual(original.returncode, 5, "CAPTURE_HASH_CHANGED " + original.stderr)
        self.assertIn("NO TESTS RAN", original.stderr, "CAPTURE_HASH_CHANGED")
        self.assertIsNone(self.hook(command), "CAPTURE_HASH_CHANGED")
        history = self.cli("history")
        self.assertEqual(len(json.loads(history.stdout)["events"]), 1,
                         "CAPTURE_HASH_CHANGED")

    def test_shell_carriage_return_keeps_zero_test_failure(self) -> None:
        command = "python3 -m unittest test_ok -k test_ok\r"
        original = subprocess.run(["bash", "-lc", command], cwd=self.repo,
                                  env=self.env, text=True, capture_output=True)
        self.assertEqual(original.returncode, 5, "CAPTURE_CR_CHANGED " + original.stderr)
        self.assertIn("NO TESTS RAN", original.stderr, "CAPTURE_CR_CHANGED")
        self.assertIsNone(self.hook(command), "CAPTURE_CR_CHANGED")
        history = self.cli("history")
        self.assertEqual(len(json.loads(history.stdout)["events"]), 1,
                         "CAPTURE_CR_CHANGED")
