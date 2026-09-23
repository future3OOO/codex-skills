#!/usr/bin/env python3
"""Public contract for retiring workflow state: the real prune CLI over a synthetic root.

Every test builds its own state root under a temporary directory and points
CODEX_WORKFLOW_STATE_ROOT at it. Pruning is destructive, so no test may ever
reach the estate's live root.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity

WORKFLOW_CLI = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.rstrip("\n")


def digest_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class SQLiteStatePruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-prune-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        git(self.repo, "add", "app.py")
        git(self.repo, "commit", "-q", "-m", "base")
        self.state_root = self.tmp / "state"
        self.env = os.environ.copy()
        self.env["CODEX_WORKFLOW_STATE_ROOT"] = str(self.state_root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW_CLI), *args], cwd=self.repo, env=self.env,
            text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )

    def begin(self, slug: str) -> dict[str, object]:
        result = self.cli("begin", "--repo", str(self.repo), "--slug", slug)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_report_only_does_not_create_or_mutate_state(self) -> None:
        missing = self.cli("prune")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertFalse(self.state_root.exists())

        self.begin("active")
        before = digest_tree(self.state_root)
        reported = self.cli("prune")
        self.assertEqual(reported.returncode, 0, reported.stderr)
        self.assertFalse(json.loads(reported.stdout)["applied"])
        self.assertEqual(digest_tree(self.state_root), before)

    def test_unknown_slot_is_never_treated_as_an_old_workflow(self) -> None:
        slot = self.state_root / "unknown"
        slot.mkdir(parents=True)
        snapshot = slot / "workflow.json"
        snapshot.write_text('{"schemaVersion":1,"workflowId":"old"}\n', encoding="utf-8")
        result = self.cli("prune", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)["slots"][0]
        self.assertEqual((report["store"], report["status"]), ("unknown", "skipped"))
        self.assertTrue(snapshot.is_file(), "unknown state was classified or deleted")

    def test_sqlite_apply_retains_active_and_four_recent_histories(self) -> None:
        passes = [self.begin(f"pass-{index}") for index in range(6)]
        slot = self.state_root / resolve_repo_identity(self.repo).key
        oldest = str(passes[0]["workflowId"])
        pointer_dir = self.state_root / "_advisor-sessions"
        pointer_dir.mkdir(mode=0o700)
        pointer = pointer_dir / f"{slot.name}-pass-0-{oldest}.sid"
        pointer.write_text("session\n", encoding="utf-8")

        report = json.loads(self.cli("prune").stdout)
        decisions = {
            item["workflowId"]: item["decision"]
            for item in report["slots"][0]["workflows"]
        }
        self.assertEqual(decisions[oldest], "removable")
        self.assertEqual(sum(value == "retained" for value in decisions.values()), 5)
        self.assertTrue(pointer.exists(), "report-only prune must not remove advisor pointers")

        applied = self.cli("prune", "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        result = json.loads(applied.stdout)
        self.assertEqual(result["advisorSessions"][0]["decision"], "removed")
        self.assertFalse(pointer.exists())
        history = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertNotIn(oldest, {event["workflowId"] for event in history})
        self.assertEqual(len({event["workflowId"] for event in history}), 5)
        self.assertEqual(json.loads(self.cli("status", "--repo", str(self.repo)).stdout)["workflowId"], passes[-1]["workflowId"])

    def test_schema_one_inventory_is_read_only_and_apply_retires_inactive_history(self) -> None:
        marker = "SCHEMA1_PRUNE_SKIPPED"
        passes = [self.begin(f"old-pass-{index}") for index in range(6)]
        database = self.state_root / resolve_repo_identity(self.repo).key / "workflow.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("ALTER TABLE workflow_events ADD COLUMN state_json TEXT")
            connection.execute("UPDATE workflow_events SET state_json = (SELECT state_json FROM workflows "
                               "WHERE workflows.workflow_id = workflow_events.workflow_id)")
            connection.execute("UPDATE workflow_events SET state_schema_version = 1")
            connection.execute("ALTER TABLE workflows DROP COLUMN state_json")
            connection.execute("DROP TABLE evidence_part_links")
            connection.execute("DROP TABLE evidence_parts")
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        reported = self.cli("prune")
        self.assertEqual(reported.returncode, 0, marker + reported.stderr)
        inventory = json.loads(reported.stdout)["slots"][0]
        self.assertEqual(len(inventory["workflows"]), 6, marker)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before, marker)
        applied = self.cli("prune", "--apply")
        self.assertEqual(applied.returncode, 0, marker + applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["slots"][0]["status"], "applied", marker)
        history = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        remaining = {event["workflowId"] for event in history}
        self.assertEqual(len(remaining), 5, marker)
        self.assertNotIn(passes[0]["workflowId"], remaining, marker)
        self.assertIn(passes[-1]["workflowId"], remaining, marker)

    def test_sqlite_busy_apply_refuses_without_deleting(self) -> None:
        passes = [self.begin(f"pass-{index}") for index in range(6)]
        slot = self.state_root / resolve_repo_identity(self.repo).key
        database = slot / "workflow.sqlite3"
        connection = sqlite3.connect(database, timeout=0, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = self.cli("prune", "--apply")
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)["slots"][0]
        self.assertEqual(report["status"], "skipped")
        self.assertTrue(any(item["reason"] == "busy-database" for item in report["workflows"]))
        history = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertEqual(len({event["workflowId"] for event in history}), len(passes))

    def test_copied_authoritative_database_is_not_pruned_under_the_wrong_slot(self) -> None:
        self.begin("active")
        source = self.state_root / resolve_repo_identity(self.repo).key / "workflow.sqlite3"
        copied = self.state_root / "copied-slot"
        copied.mkdir(mode=0o700)
        shutil.copy2(source, copied / "workflow.sqlite3")

        result = self.cli("prune", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = next(
            slot for slot in json.loads(result.stdout)["slots"]
            if slot["slot"] == "copied-slot"
        )
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["store"], "unknown")
        self.assertIn("repository identity", report["reason"])
        self.assertTrue((copied / "workflow.sqlite3").exists())

    def test_unsupported_event_schema_is_not_pruned(self) -> None:
        """An authoritative but unreadable ledger is preserved whole."""
        for index in range(6):
            self.begin(f"pass-{index}")
        slot = self.state_root / resolve_repo_identity(self.repo).key
        database = slot / "workflow.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE workflow_events SET state_schema_version = 999 "
                "WHERE event_id = (SELECT MAX(event_id) FROM workflow_events)"
            )
            connection.commit()
        finally:
            connection.close()
        before = hashlib.sha256(database.read_bytes()).hexdigest()

        result = self.cli("prune", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = next(
            item for item in json.loads(result.stdout)["slots"]
            if item["slot"] == slot.name
        )
        self.assertEqual(report["store"], "unknown")
        self.assertEqual(report["status"], "skipped")
        self.assertIn("event schema or policy", report["reason"])
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_configured_symlinked_state_root_is_traversed(self) -> None:
        self.begin("active")
        linked = self.tmp / "linked-state"
        linked.symlink_to(self.state_root, target_is_directory=True)
        previous = self.env["CODEX_WORKFLOW_STATE_ROOT"]
        self.env["CODEX_WORKFLOW_STATE_ROOT"] = str(linked)
        try:
            result = self.cli("prune")
        finally:
            self.env["CODEX_WORKFLOW_STATE_ROOT"] = previous
        self.assertEqual(result.returncode, 0, result.stderr)
        slots = json.loads(result.stdout)["slots"]
        self.assertTrue(slots, "a trusted configured state-root symlink was treated as empty")
        self.assertEqual(slots[0]["store"], "sqlite")

    def test_question_mark_state_root_keeps_authoritative_sqlite_classification(self) -> None:
        """A URI-reserved state-root path still opens its authoritative database."""
        self.state_root = self.tmp / "state?reserved"
        self.env["CODEX_WORKFLOW_STATE_ROOT"] = str(self.state_root)
        self.begin("active")
        slot_key = resolve_repo_identity(self.repo).key
        database = self.state_root / slot_key / "workflow.sqlite3"
        self.assertTrue(database.is_file())

        result = self.cli("prune")
        self.assertEqual(result.returncode, 0, result.stderr)
        slot = next(
            item for item in json.loads(result.stdout)["slots"]
            if item["slot"] == slot_key
        )
        self.assertEqual(slot["store"], "sqlite")

    def test_database_apply_rolls_back_if_delete_is_aborted(self) -> None:
        for index in range(6):
            self.begin(f"pass-{index}")
        slot = self.state_root / resolve_repo_identity(self.repo).key
        database = slot / "workflow.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("""
            CREATE TRIGGER refuse_workflow_delete BEFORE DELETE ON workflows
            BEGIN SELECT RAISE(ABORT, 'forced prune abort'); END
        """)
        connection.commit()
        connection.close()

        before = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)
        result = self.cli("prune", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)
        self.assertEqual(after, before)
        report = json.loads(result.stdout)["slots"][0]
        self.assertEqual(report["status"], "skipped")
        self.assertTrue(any("forced prune abort" in item["reason"] for item in report["workflows"] if item["decision"] == "skipped"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
