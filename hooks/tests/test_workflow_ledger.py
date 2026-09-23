#!/usr/bin/env python3
"""Public workflow CLI contracts over the real on-disk SQLite ledger."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity
from hooks.tests.support import build_no_change_document, record_context_forge

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.rstrip("\n")


class WorkflowLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-ledger-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        git(self.repo, "add", "app.py")
        git(self.repo, "commit", "-q", "-m", "base")
        self.state_root = self.tmp / "state"
        self.previous_state_root = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.state_root)
        self.env = os.environ.copy()
        self.design_declaration = self.tmp / "design-absent.json"
        self.design_declaration.write_text(json.dumps({
            "schemaVersion": 1,
            "status": "absent",
            "reason": "workflow ledger test has no governing design",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CODEX_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = list(args)
        if "advisor-result" in command and "--design-declaration" not in command:
            command.extend(("--design-declaration", str(self.design_declaration)))
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *command], cwd=self.repo, env=self.env,
            text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )


    @property
    def identity(self):
        return resolve_repo_identity(self.repo)

    @property
    def slot(self) -> Path:
        return self.state_root / self.identity.key

    @property
    def database(self) -> Path:
        return self.slot / "workflow.sqlite3"

    def begin(self, slug: str = "ledger") -> dict[str, object]:
        result = self.cli("begin", "--repo", str(self.repo), "--slug", slug)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def prepare_preflight_ready(self, slug: str = "atomic") -> tuple[dict[str, object], Path]:
        state = self.begin(slug)
        record_context_forge(self.repo, self.tmp)
        workflow_id = str(state["workflowId"])
        for command in (
            ("record", "advisor-result", "--slug", slug, "--workflow-id", workflow_id, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("record", "advisor-disposition", "--slug", slug, "--workflow-id", workflow_id, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.cli(*command, "--repo", str(self.repo))
            self.assertEqual(result.returncode, 0, result.stderr)
        document = self.tmp / "preflight.json"
        document.write_text(json.dumps(build_no_change_document("ledger proof")), encoding="utf-8")
        return state, document

    def test_status_without_database_or_legacy_state_is_missing_not_success(self) -> None:
        status = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 2)
        self.assertIn("no active workflow", status.stderr)
        self.assertFalse(self.state_root.exists(),
                         "a read-only status created a permanent empty state slot")
        history = self.cli("history", "--repo", str(self.repo))
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertEqual(json.loads(history.stdout)["events"], [])
        self.assertFalse(self.state_root.exists(),
                         "read-only history created a permanent empty state slot")

    def test_begin_secures_a_new_default_state_root(self) -> None:
        codex_home = self.tmp / "claude-home"
        env = self.env.copy()
        env.pop("CODEX_WORKFLOW_STATE_ROOT", None)
        env["CODEX_HOME"] = str(codex_home)
        begun = subprocess.run(
            [sys.executable, str(WORKFLOW), "begin", "--repo", str(self.repo), "--slug", "private-root"],
            cwd=self.repo, env=env, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(begun.returncode, 0, begun.stderr)
        self.assertEqual(stat.S_IMODE((codex_home / "state").stat().st_mode), 0o700)

    def test_begin_and_status_use_private_sqlite_without_a_json_snapshot(self) -> None:
        begun = self.cli("begin", "--repo", str(self.repo), "--slug", "ledger", "--intent", "test")
        self.assertEqual(begun.returncode, 0, begun.stderr)
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "ledger")
        self.assertEqual(state["nextAction"], "repo-context-forge")

        slot = self.state_root / resolve_repo_identity(self.repo).key
        database = slot / "workflow.sqlite3"
        self.assertTrue(database.is_file())
        self.assertFalse((slot / "workflow.json").exists())
        self.assertEqual(stat.S_IMODE(slot.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

        status = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 0, status.stderr)
        projection = json.loads(status.stdout)
        self.assertEqual({key: projection[key] for key in state}, state)
        self.assertEqual(projection["schemaVersion"], 1)
        stable = {
            "schemaVersion", "repo", "slug", "workflowId", "phase", "nextAction",
            "repoContextForge", "gitnexus", "advisorPreflight", "preflight",
            "tdd", "verification",
            "codeReview", "finalReview",
        }
        self.assertTrue(stable <= set(projection), stable - set(projection))
        rendered = json.dumps(projection, sort_keys=True)
        for private_detail in (
            "workflow.sqlite3", "workflow_events", "evidence_parts",
            "event_evidence", "event_manifests", "review_manifests",
        ):
            self.assertNotIn(private_detail, rendered)
        for private_key in ("databasePath", "sqlitePath", "table", "tables", "storage"):
            self.assertNotIn(private_key, projection)

    def test_authoritative_database_refuses_a_different_repository_identity(self) -> None:
        self.begin("identity")
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q")
        git(other, "config", "user.email", "test@example.invalid")
        git(other, "config", "user.name", "Workflow Harness")
        (other / "app.py").write_text("value = 2\n", encoding="utf-8")
        git(other, "add", "app.py")
        git(other, "commit", "-q", "-m", "base")
        other_identity = resolve_repo_identity(other)
        other_slot = self.state_root / other_identity.key
        other_slot.mkdir(parents=True, mode=0o700)
        shutil.copy2(self.database, other_slot / "workflow.sqlite3")

        result = subprocess.run(
            [sys.executable, str(WORKFLOW), "status", "--repo", str(other)],
            cwd=other, env=self.env, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository identity", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_history_retains_superseded_passes_in_event_order(self) -> None:
        first = json.loads(self.cli("begin", "--repo", str(self.repo), "--slug", "first").stdout)
        second = json.loads(self.cli("begin", "--repo", str(self.repo), "--slug", "second").stdout)

        status = json.loads(self.cli("status", "--repo", str(self.repo)).stdout)
        self.assertEqual(status["workflowId"], second["workflowId"])
        history = self.cli("history", "--repo", str(self.repo))
        self.assertEqual(history.returncode, 0, history.stderr)
        events = json.loads(history.stdout)["events"]
        self.assertEqual([event["kind"] for event in events], ["begin", "begin"])
        self.assertEqual([event["workflowId"] for event in events], [first["workflowId"], second["workflowId"]])
        self.assertEqual([event["eventId"] for event in events], sorted(event["eventId"] for event in events))

    def test_invalid_transition_appends_no_event_or_evidence(self) -> None:
        self.begin("invalid")
        before = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        refused = self.cli(
            "set-phase", "--repo", str(self.repo),
            "--phase", "code-review", "--status", "not-required", "--findings", "none",
        )
        self.assertEqual(refused.returncode, 2)
        after = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertEqual(after, before)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
        finally:
            connection.close()

    def test_later_statement_abort_rolls_back_evidence_event_and_projection(self) -> None:
        state, document = self.prepare_preflight_ready()
        connection = sqlite3.connect(self.database)
        try:
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("evidence", "workflow_events", "event_evidence")
            }
            pointer = connection.execute("SELECT state_json FROM workflows").fetchall()
            connection.execute("""
                CREATE TRIGGER abort_projection BEFORE UPDATE ON workflows
                BEGIN SELECT RAISE(ABORT, 'forced later-statement abort'); END
            """)
            connection.commit()
        finally:
            connection.close()

        result = self.cli(
            "record", "preflight", "--repo", str(self.repo),
            "--slug", "atomic", "--workflow-id", str(state["workflowId"]),
            "--input", str(document),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("forced later-statement abort", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        connection = sqlite3.connect(self.database)
        try:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("evidence", "workflow_events", "event_evidence")
            }
            current_pointer = connection.execute("SELECT state_json FROM workflows").fetchall()
        finally:
            connection.close()
        self.assertEqual(after, before)
        self.assertEqual(current_pointer, pointer)

    def test_concurrent_begins_retain_both_and_stale_producer_refuses(self) -> None:
        command = [
            sys.executable, str(WORKFLOW), "begin", "--repo", str(self.repo),
            "--slug", "concurrent",
        ]
        processes = [
            subprocess.Popen(
                command, cwd=self.repo, env=self.env, text=True, encoding="utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=20) + (process.returncode,) for process in processes]
        self.assertTrue(all(code == 0 for _, _, code in results), results)
        workflows = [json.loads(stdout) for stdout, _, _ in results]
        history = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertEqual({event["workflowId"] for event in history}, {item["workflowId"] for item in workflows})
        active = json.loads(self.cli("status", "--repo", str(self.repo)).stdout)
        inactive = next(item for item in workflows if item["workflowId"] != active["workflowId"])
        refused = self.cli(
            "record", "advisor-result", "--repo", str(self.repo),
            "--slug", "concurrent", "--workflow-id", str(inactive["workflowId"]),
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("workflow instance", refused.stderr)
        after = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertEqual(after, history)

    def test_corrupt_authoritative_database_never_falls_back_to_stale_json(self) -> None:
        self.begin("corrupt")
        first = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.database.write_bytes(b"not a sqlite database")
        refused = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn("Traceback", refused.stderr)
        self.assertIn("workflow database", refused.stderr)



if __name__ == "__main__":
    unittest.main(verbosity=2)
