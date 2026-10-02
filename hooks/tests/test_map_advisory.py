"""The PostToolUse edit hook on a production edit in a governed pass.

The pass is real: begin, a governed intake that builds the pass-start GitNexus
index, an approved preflight and a recorded comparison. The hook is the real
code-quality-gate.py; detect-changes launches are counted at the real gitnexus
CLI boundary. Nothing substitutes the producer, the hook, the index or the map.
"""
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

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    advisor_disposition,
    instance_id,
    read_workflow,
    record_advisor_result,
)
from hooks.tests.support import (  # noqa: E402
    approve_preflight,
    POST_EDIT,
    WORKFLOW,
    build_document,
    fixture_env,
    pending_behavior,
    run_git,
    run_intake,
    run_post_edit,
    run_workflow,
)

CANONICAL_BOOTSTRAP = Path.home() / ".local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py"
GITNEXUS = shutil.which("gitnexus")
UNITTEST = (sys.executable, "-m", "unittest")
COMPUTE = "tests.test_app.AppTests.test_compute"
ITEM = "BM_FIXTURE"
SESSION = "edit-hook-session"

APP = "def compute(value):\n    return value + {}\n"
TESTS = """import unittest

from app import compute


class AppTests(unittest.TestCase):
    def test_compute(self):
        self.assertEqual(compute(1), 3, "FIXTURE_VALUE_NOT_THREE")

    def test_second(self):
        self.assertEqual(compute(1), 3, "FIXTURE_VALUE_NOT_THREE")

    def test_other(self):
        self.assertGreaterEqual(compute(2), 3)
"""


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
@unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
class MapAdvisoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-edit-hook-"))
        self.repo = self.tmp / "repo"
        (self.repo / "tests").mkdir(parents=True)
        self.slug = "edit-hook"
        self.intent = "compute adds two"
        previous = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")

        def restore_state_root() -> None:
            if previous is None:
                os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
            else:
                os.environ["CODEX_WORKFLOW_STATE_ROOT"] = previous

        self.addCleanup(restore_state_root)
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = fixture_env(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("remote", "add", "origin", "https://example.invalid/workflow-fixture.git")
        (self.repo / "app.py").write_text(APP.format(1), encoding="utf-8")
        (self.repo / "caller.py").write_text("from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8")
        (self.repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.repo / "tests" / "test_app.py").write_text(TESTS, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = run_git(self.repo, self.env, *args)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_workflow(self.repo, self.env, *args)

    def begin_pass(self) -> None:
        """One governed pass with its pass-start index and one recorded comparison."""
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        intake = run_intake(self.repo, self.env, self.slug, self.intent)
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        identity = resolve_repo_identity(self.repo)
        workflow_id = instance_id(read_workflow(identity))
        record_advisor_result(identity, self.slug, workflow_id, "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, self.slug, workflow_id, "preflight", "none")
        document = build_document("edit hook fixture", behavior_map=[pending_behavior(
            ITEM, behavior="compute adds two", seam="tests/test_app.py through unittest", expected="compute(1) is 3")])
        approve_preflight(self.repo, document)
        path = self.tmp / "preflight.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        recorded = self.workflow("record", "preflight", "--slug", self.slug, "--workflow-id", workflow_id, "--input", str(path))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        compared = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", self.slug,
                                   "--behavior-id", ITEM, "--", *UNITTEST, COMPUTE],
                                  cwd=self.repo, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(compared.returncode, 2, compared.stdout + compared.stderr)

    def hook(self, relative: str = "app.py") -> tuple[str, int]:
        """The real edit hook on one file: its additionalContext and detect-changes launches."""
        scans = self.tmp / "scans"
        shim_dir = self.tmp / "shim"
        shim_dir.mkdir(exist_ok=True)
        (shim_dir / "gitnexus").write_text(
            f'#!/bin/sh\ncase "$1" in detect-changes) echo x >> "{scans}";; esac\nexec "{GITNEXUS}" "$@"\n')
        (shim_dir / "gitnexus").chmod(0o755)
        result = run_post_edit(self.repo, self.env, relative, session=SESSION,
                               env_extra={"PATH": f"{shim_dir}{os.pathsep}{self.env['PATH']}"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        context = "".join(json.loads(line)["hookSpecificOutput"]["additionalContext"]
                          for line in result.stdout.splitlines() if line.startswith("{"))
        return context, scans.read_text().count("x") if scans.exists() else 0

    def test_a_production_edit_runs_no_impacted_test_scan(self) -> None:
        # The scan named a whole test file; agents then ran that whole module.
        self.begin_pass()
        (self.repo / "app.py").write_text(APP.format(2) + "VALUE = undefined_name\n", encoding="utf-8")
        context, scans = self.hook()
        self.assertEqual(("map advisory" in context, scans), (False, 0),
                         "EDIT_SCAN_RUNS: a production edit scanned or advised")
        self.assertIn("undefined_name", context, "lint advice lost")

    def test_the_edit_survives_a_closed_stdout_reader(self) -> None:
        # Lint feedback rides the hook's stdout; a broken reader loses it, never the edit.
        (self.repo / "app.py").write_text(APP.format(2) + "VALUE = undefined_name\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "app.py")}, "session_id": SESSION})
        proc = subprocess.Popen([str(POST_EDIT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=self.repo, env=self.env)
        proc.stdout.close()
        _, err = proc.communicate(input=payload, timeout=120)
        self.assertEqual(proc.returncode, 0, f"EDIT_FAILS_ON_CLOSED_STDOUT: exit={proc.returncode} {err[-200:]}")
        self.assertNotIn("BrokenPipeError", err, "EDIT_FAILS_ON_CLOSED_STDOUT")


if __name__ == "__main__":
    unittest.main()
