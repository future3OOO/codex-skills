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
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("ISSUE96_PRODUCT_ROOT", Path(__file__).resolve().parents[2]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity
from hooks.tests.support import build_no_change_document, empty_advisor_envelope, record_context_forge

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

    def ledger_counts(self) -> dict[str, int]:
        tables = ("metadata", "workflows", "evidence", "review_manifests", "workflow_events",
                  "event_evidence", "event_manifests", "active_projection")
        if not self.database.exists(): return {table: 0 for table in tables}
        with sqlite3.connect(self.database) as connection:
            existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if table in existing else 0 for table in tables}

    def begin(self, slug: str = "ledger") -> dict[str, object]:
        result = self.cli("begin", "--repo", str(self.repo), "--slug", slug)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_events_keep_metadata_and_each_workflow_keeps_one_current_state(self) -> None:
        self.begin("one-current-state")
        with sqlite3.connect(self.database) as connection:
            events = {row[1] for row in connection.execute("PRAGMA table_info(workflow_events)")}
            workflows = {row[1] for row in connection.execute("PRAGMA table_info(workflows)")}
            self.assertNotIn("state_json", events, "PROJECTION_MIGRATION_PARTIAL")
            self.assertIn("state_json", workflows, "PROJECTION_MIGRATION_PARTIAL")
        resumed = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(resumed.returncode, 0, "PROJECTION_MIGRATION_PARTIAL " + resumed.stderr)
        self.assertNotIn("gitnexus", json.loads(resumed.stdout), "retired step leaked into status")

    def test_migration_overwrites_a_stale_partial_projection_from_event_history(self) -> None:
        self.begin("partial-migration")
        with sqlite3.connect(self.database) as connection:
            current = connection.execute("SELECT state_json FROM workflows").fetchone()[0]
            connection.execute("ALTER TABLE workflow_events ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            connection.execute("UPDATE workflow_events SET state_json = ?", (current,))
            stale = json.loads(current)
            stale["phase"] = "stale-projection"
            connection.execute("UPDATE workflows SET state_json = ?", (json.dumps(stale),))
        resumed = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(resumed.returncode, 0, "PROJECTION_MIGRATION_PARTIAL " + resumed.stderr)
        self.assertNotEqual(json.loads(resumed.stdout)["phase"], "stale-projection", "PROJECTION_MIGRATION_PARTIAL")
        with sqlite3.connect(self.database) as connection:
            events = {row[1] for row in connection.execute("PRAGMA table_info(workflow_events)")}
        self.assertNotIn("state_json", events, "PROJECTION_MIGRATION_PARTIAL")

    def test_migration_moves_a_clean_event_snapshot_into_the_current_projection(self) -> None:
        self.begin("schema-one")
        with sqlite3.connect(self.database) as connection:
            current = connection.execute("SELECT state_json FROM workflows").fetchone()[0]
            connection.execute("ALTER TABLE workflow_events ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            connection.execute("UPDATE workflow_events SET state_json = ?", (current,))
            connection.execute("ALTER TABLE workflows DROP COLUMN state_json")
        resumed = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(resumed.returncode, 0, "PROJECTION_MIGRATION_PARTIAL " + resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["slug"], "schema-one", "PROJECTION_MIGRATION_PARTIAL")

    def test_concurrent_schema_one_opens_share_one_migration(self) -> None:
        self.begin("schema-one-race")
        with sqlite3.connect(self.database) as connection:
            current = connection.execute("SELECT state_json FROM workflows").fetchone()[0]
            connection.execute("ALTER TABLE workflow_events ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            connection.execute("UPDATE workflow_events SET state_json = ?", (current,))
            connection.execute("ALTER TABLE workflows DROP COLUMN state_json")
        with sqlite3.connect(self.database) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            processes = [subprocess.Popen(
                [sys.executable, str(WORKFLOW), "status", "--repo", str(self.repo)],
                cwd=self.repo, env=self.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) for _ in range(2)]
            time.sleep(.3)
            blocker.rollback()
            results = [process.communicate(timeout=15) for process in processes]
        for process, (stdout, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, "SCHEMA1_RACE " + stderr)
            self.assertEqual(json.loads(stdout)["slug"], "schema-one-race")

    def test_killed_migration_reopens_with_every_event(self) -> None:
        self.begin("interrupted-migration")
        with sqlite3.connect(self.database) as connection:
            wid, repo_key, slug, created, state = connection.execute(
                "SELECT workflow_id, repo_key, slug, created_at, state_json FROM workflows"
            ).fetchone()
            rows = [(f"{index + 1:032x}", repo_key, slug, created, state.replace(wid, f"{index + 1:032x}"))
                    for index in range(50_000)]
            connection.executemany("INSERT INTO workflows VALUES (?,?,?,?,?)", rows)
            connection.executemany(
                "INSERT INTO workflow_events(workflow_id,kind,recorded_at,state_schema_version,policy_version,activates_workflow) "
                "VALUES (?,'begin',?,1,1,0)", ((row[0], created) for row in rows),
            )
            connection.execute("ALTER TABLE workflow_events ADD COLUMN state_json TEXT")
            connection.execute("UPDATE workflow_events SET state_json=(SELECT state_json FROM workflows "
                               "WHERE workflows.workflow_id=workflow_events.workflow_id)")
            connection.execute("UPDATE workflows SET state_json='{}'")
        process = subprocess.Popen(
            [sys.executable, str(WORKFLOW), "status", "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        journal = Path(str(self.database) + "-journal")
        killed = False
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                if journal.exists() and journal.stat().st_size > 4096:
                    process.kill()
                    killed = True
                    break
                time.sleep(.002)
            self.assertTrue(killed, "PROJECTION_INTERRUPTION_NOT_REACHED")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=30)
        resumed = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(resumed.returncode, 0, "PROJECTION_INTERRUPTION_LOST " + resumed.stderr)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0], 50_001,
                             "PROJECTION_INTERRUPTION_LOST")
            self.assertNotIn("state_json", {row[1] for row in connection.execute(
                "PRAGMA table_info(workflow_events)")}, "PROJECTION_INTERRUPTION_LOST")

    def test_readiness_has_no_shadow_production_step(self) -> None:
        self.begin("step-readiness")
        status = json.loads(self.cli("status", "--repo", str(self.repo)).stdout)
        self.assertNotIn("productionCode", status, "STEP_READINESS_DRIFT")
        self.assertEqual(status["nextAction"], "repo-context-forge", "STEP_READINESS_DRIFT")

    def test_checkpoint_materializes_ordered_channels_once(self) -> None:
        begun = self.cli("begin", "--repo", str(self.repo), "--slug", "channels", "--intent", "channel proof")
        self.assertEqual(begun.returncode, 0, begun.stderr)
        record_context_forge(self.repo, self.tmp)
        directory = self.tmp / "channels"
        response = self.cli("checkpoint", "--repo", str(self.repo), "--phase", "preflight-advice",
                            "--channel-dir", str(directory))
        self.assertEqual(response.returncode, 0, "CHANNEL_MANIFEST_MISSING " + response.stderr)
        result = json.loads(response.stdout)
        channels = result["channels"]
        self.assertEqual([item["name"] for item in channels], ["original-intent", "advisor-projection"],
                         "CHANNEL_MANIFEST_ORDER")
        for channel in channels:
            path = Path(channel["contentPath"])
            self.assertEqual(path.parent, directory)
            self.assertEqual(channel["bytes"], path.stat().st_size)
        self.assertNotIn("advisorProjection", result, "CHANNEL_MANIFEST_DUPLICATED_INLINE")

    def test_unused_preflight_prose_is_refused_without_a_write(self) -> None:
        state, path = self.prepare_preflight_ready("map-parts")
        doc = build_no_change_document("map revision")
        doc["chosenApproach"] = "unused explanation"
        path.write_text(json.dumps(doc), encoding="utf-8")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo),
                            "--slug", "map-parts", "--workflow-id", str(state["workflowId"]),
                            "--input", str(path))
        self.assertEqual(recorded.returncode, 2)
        self.assertIn("unknown fields: chosenApproach", recorded.stderr)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence WHERE kind = 'preflight'").fetchone()[0], 0)

    def test_preflight_record_accepts_the_map_without_unused_prose(self) -> None:
        state, path = self.prepare_preflight_ready("map-only-preflight")
        items = build_no_change_document("map only")["behaviorMap"]
        items[0].pop("evidence", None)
        path.write_text(json.dumps({"behaviorMap": items}),
                        encoding="utf-8")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo),
                            "--slug", "map-only-preflight", "--workflow-id", str(state["workflowId"]),
                            "--input", str(path))
        self.assertEqual(recorded.returncode, 0, "PREFLIGHT_PROSE_REQUIRED " + recorded.stderr)

    def test_record_check_explains_schema_and_writes_nothing(self) -> None:
        state, path = self.prepare_preflight_ready("check-only")
        path.write_text(json.dumps({"behaviorMap": build_no_change_document("check only")["behaviorMap"]}),
                        encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        checked = self.cli("record", "preflight", "--repo", str(self.repo), "--slug", "check-only",
                           "--workflow-id", str(state["workflowId"]), "--input", str(path), "--check")
        self.assertEqual(checked.returncode, 0, "RECORD_CHECK_MUTATED " + checked.stderr)
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before,
                         "RECORD_CHECK_MUTATED")
        help_result = self.cli("record", "preflight", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("behaviorMap", help_result.stdout, "RECORD_SCHEMA_HIDDEN")
        empty = self.cli("record", "advisor-disposition", "--repo", str(self.repo), "--check")
        self.assertEqual(empty.returncode, 2, "EMPTY_DISPOSITION_CHECK_PASSED")
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before,
                         "EMPTY_DISPOSITION_CHECK_MUTATED")

    def test_map_check_uses_the_recorder_without_a_commit(self) -> None:
        state, path = self.prepare_preflight_ready("map-check")
        path.write_text(json.dumps({"behaviorMap": build_no_change_document("map check")["behaviorMap"]}),
                        encoding="utf-8")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo), "--slug", "map-check",
                            "--workflow-id", str(state["workflowId"]), "--input", str(path))
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        path.write_text(json.dumps({"reassessment": "check", "items": [], "dispositions": []}), encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        checked = self.cli("record", "map", "--repo", str(self.repo), "--input", str(path), "--check")
        self.assertEqual(checked.returncode, 0, "MAP_CHECK_MUTATED " + checked.stderr)
        path.write_text(json.dumps({"reassessment": "invalid independent inputs", "items": [{"id": "BM_BAD"}],
                                    "dispositions": [{"id": "BM_MISSING", "status": "invalid", "evidence": "x"}]}), encoding="utf-8")
        invalid = self.cli("record", "map", "--repo", str(self.repo), "--input", str(path), "--check")
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        self.assertIn("BM_MISSING", invalid.stderr, "MAP_CHECK_STOPPED_AT_ITEM")
        self.assertIn("BM_BAD", invalid.stderr, "MAP_CHECK_STOPPED_AT_DISPOSITION")
        path.write_text('{"reassessment":"check","items":1,"dispositions":1}', encoding="utf-8")
        containers = self.cli("record", "map", "--repo", str(self.repo), "--input", str(path), "--check")
        self.assertEqual(containers.returncode, 2, containers.stdout + containers.stderr)
        self.assertIn("items must be an array", containers.stderr, "MAP_CONTAINERS_STOP_EARLY")
        self.assertIn("dispositions must be an array", containers.stderr, "MAP_CONTAINERS_STOP_EARLY")
        path.write_text('{"reassessment":"","items":1,"dispositions":1}', encoding="utf-8")
        independent = self.cli("record", "map", "--repo", str(self.repo), "--input", str(path), "--check")
        for field in ("reassessment", "items must be an array", "dispositions must be an array"):
            self.assertIn(field, independent.stderr, "MAP_FIELDS_STOP_EARLY")
        path.write_text(json.dumps({"reassessment": "check", "dispositions": [
            {"id": "BM_A", "badA": True}, {"id": "BM_B", "badB": True}]}), encoding="utf-8")
        entries = self.cli("record", "map", "--repo", str(self.repo), "--input", str(path), "--check")
        for field in ("badA", "badB"):
            self.assertIn(field, entries.stderr, "MAP_ENTRIES_STOP_EARLY")
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before,
                         "MAP_CHECK_MUTATED")

    def test_record_check_reports_independent_shape_errors_together(self) -> None:
        marker = "RECORD_CHECK_WRONG"
        state = self.begin("multi-error")
        path = self.tmp / "invalid-preflight.json"
        path.write_text(json.dumps({"unknownField": True, "behaviorMap": [{"id": "BROKEN"}]}),
                        encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        refused = self.cli("record", "preflight", "--repo", str(self.repo), "--slug", "multi-error",
                           "--workflow-id", str(state["workflowId"]), "--input", str(path), "--check")
        self.assertEqual(refused.returncode, 2, marker + " " + refused.stdout)
        self.assertIn("unknownField", refused.stderr, marker)
        self.assertIn("missing fields", refused.stderr, marker)
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before,
                         marker)

    def test_advisor_checks_report_independent_finding_errors(self) -> None:
        state = self.begin("advisor-errors")
        path = self.tmp / "invalid-advisor.json"
        path.write_text(json.dumps({"schemaVersion": 1, "verdict": "completed", "findings": [
            {"id": "A", "claim": "", "material": True, "priorFinding": []},
            {"id": "B", "claim": "valid", "material": "maybe"},
            {"id": "", "claim": "", "material": "bad"},
        ]}), encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        result = self.cli("record", "advisor-result", "--repo", str(self.repo),
                          "--slug", "advisor-errors", "--workflow-id", str(state["workflowId"]),
                          "--stage", "preflight", "--source", "codex-advisor", "--input", str(path), "--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("finding A", result.stderr, "ADVISOR_ERRORS_STOP_AT_FIRST_ITEM")
        self.assertIn("finding B", result.stderr, "ADVISOR_ERRORS_STOP_AT_FIRST_ITEM")
        self.assertIn("priorFinding", result.stderr, "ADVISOR_ERRORS_STOP_WITHIN_ITEM")
        self.assertIn("finding 3 needs a non-empty unique id", result.stderr, "ADVISOR_ERRORS_STOP_WITHIN_ITEM")
        self.assertIn("finding  requires claim", result.stderr, "ADVISOR_ERRORS_STOP_WITHIN_ITEM")
        path.write_text('{"schemaVersion":1,"findings":[],"verdict":[]}', encoding="utf-8")
        invalid_verdict = self.cli("record", "advisor-result", "--repo", str(self.repo),
                                   "--slug", "advisor-errors", "--workflow-id", str(state["workflowId"]),
                                   "--stage", "preflight", "--source", "codex-advisor", "--input", str(path), "--check")
        self.assertEqual(invalid_verdict.returncode, 2, "ADVISOR_VERDICT_TYPE_ESCAPED " + invalid_verdict.stdout + invalid_verdict.stderr)
        path.write_text('{"schemaVersion":1,"findings":[{"id":"","claim":"","material":"bad"}],"verdict":"bogus"}', encoding="utf-8")
        independent = self.cli("record", "advisor-result", "--repo", str(self.repo),
                               "--slug", "advisor-errors", "--workflow-id", str(state["workflowId"]),
                               "--stage", "preflight", "--source", "codex-advisor", "--input", str(path), "--check")
        self.assertEqual(independent.returncode, 2)
        self.assertIn("verdict", independent.stderr, "ADVISOR_VERDICT_STOPPED_FINDINGS")
        self.assertIn("finding 1", independent.stderr, "ADVISOR_VERDICT_STOPPED_FINDINGS")
        path.write_text('{"schemaVersion":1,"findings":1,"verdict":"bogus"}', encoding="utf-8")
        malformed = self.cli("record", "advisor-result", "--repo", str(self.repo),
                             "--slug", "advisor-errors", "--workflow-id", str(state["workflowId"]),
                             "--stage", "preflight", "--source", "codex-advisor", "--input", str(path), "--check")
        self.assertIn("findings array", malformed.stderr, "ADVISOR_CONTAINER_STOPPED_VERDICT")
        self.assertIn("verdict", malformed.stderr, "ADVISOR_CONTAINER_STOPPED_VERDICT")
        path.write_text('{"schemaVersion":1,"findings":1,"verdict":"bogus","unexpected":true}', encoding="utf-8")
        extra = self.cli("record", "advisor-result", "--repo", str(self.repo),
                         "--slug", "advisor-errors", "--workflow-id", str(state["workflowId"]),
                         "--stage", "preflight", "--source", "codex-advisor", "--input", str(path), "--check")
        self.assertIn("only schemaVersion", extra.stderr, "ADVISOR_SHAPE_STOPPED_FIELDS")
        self.assertIn("incompatible with stage", extra.stderr, "ADVISOR_SHAPE_STOPPED_FIELDS")
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before)

    def test_disposition_check_reports_independent_item_errors(self) -> None:
        state = self.begin("disposition-errors")
        path = self.tmp / "invalid-disposition.json"
        path.write_text(json.dumps({"intakeEvidenceId": "evidence-recorded", "dispositions": [
            {"finding_id": "A", "status": "invalid", "reason": "x", "evidenceRefs": ["proof"]},
            {"finding_id": "B", "status": "fixed", "reason": "", "evidenceRefs": ["proof"]},
            {"finding_id": "C", "status": "fixed", "kind": [], "mechanism": [], "premise": {},
             "occurrence": {}, "materialConsequence": {}, "evidence": "", "unknownField": True},
        ]}), encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        result = self.cli("record", "advisor-disposition", "--repo", str(self.repo),
                          "--slug", "disposition-errors", "--workflow-id", str(state["workflowId"]),
                          "--stage", "final", "--input", str(path), "--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("finding A", result.stderr, "DISPOSITION_ERRORS_STOP_AT_FIRST_ITEM")
        self.assertIn("finding_id, status, reason", result.stderr, "DISPOSITION_ERRORS_STOP_AT_FIRST_ITEM")
        for field in ("mechanism", "kind", "premise", "occurrence", "materialConsequence", "requires evidence", "unknown or missing fields"):
            self.assertIn(field, result.stderr, "DISPOSITION_ERRORS_STOP_WITHIN_ITEM")
        for document, fields in (({"intakeEvidenceId": "", "dispositions": [
                {"finding_id": "", "status": "fixed", "reason": "", "evidenceRefs": [], "mechanism": []}]},
                ("intakeEvidenceId", "reference a finding", "mechanism", "evidenceRefs")),
                ({"intakeEvidenceId": "evidence-recorded", "dispositions": [
                    {"finding_id": "", "status": "fixed", "reason": "", "evidenceRefs": [], "mechanism": []}]},
                 ("reference a finding", "mechanism", "evidenceRefs"))):
            path.write_text(json.dumps(document), encoding="utf-8")
            independent = self.cli("record", "advisor-disposition", "--repo", str(self.repo),
                                   "--slug", "disposition-errors", "--workflow-id", str(state["workflowId"]),
                                   "--stage", "final", "--input", str(path), "--check")
            for field in fields:
                self.assertIn(field, independent.stderr, "DISPOSITION_ID_STOPPED_FIELDS")
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before)

    def test_advisor_check_rejects_commit_invalid_flag_pairs(self) -> None:
        state = self.begin("advisor-flag-check")
        path = self.tmp / "advisor-valid.json"
        path.write_text('{"schemaVersion":1,"findings":[],"verdict":"completed"}', encoding="utf-8")
        base = ("record", "advisor-result", "--repo", str(self.repo), "--slug", "advisor-flag-check",
                "--workflow-id", str(state["workflowId"]), "--stage", "preflight", "--source", "codex-advisor")
        for flags, label in ((("--input", str(path), "--verdict", "unavailable"), "mixed"),
                             (("--verdict", "unavailable"), "reason")):
            checked = self.cli(*base, *flags, "--check")
            self.assertEqual(checked.returncode, 2, label + checked.stdout + checked.stderr)
        invalid_source = self.cli(*base[:-2], "--source", "codex-agent", "--verdict", "unavailable",
                                  "--reason", "transport", "--check")
        self.assertEqual(invalid_source.returncode, 2, "ADVISOR_SOURCE_CHECK_COMMIT_DRIFT " + invalid_source.stdout + invalid_source.stderr)
        path.write_text(json.dumps({"intakeEvidenceId": "evidence-recorded", "dispositions": [
            {"finding_id": "A", "status": "fixed", "reason": "executed", "evidenceRefs": ["evidence-run:0"]}]}), encoding="utf-8")
        checked = self.cli("record", "advisor-disposition", "--repo", str(self.repo),
                           "--slug", "advisor-flag-check", "--workflow-id", str(state["workflowId"]),
                           "--stage", "preflight", "--findings", "none", "--input", str(path), "--check")
        self.assertEqual(checked.returncode, 2, "DISPOSITION_CHECK_COMMIT_DRIFT " + checked.stdout + checked.stderr)
        for findings in ("bogus", "addressed"):
            checked = self.cli("record", "advisor-disposition", "--repo", str(self.repo),
                               "--slug", "advisor-flag-check", "--workflow-id", str(state["workflowId"]),
                               "--stage", "preflight", "--findings", findings, "--check")
            self.assertEqual(checked.returncode, 2, "DISPOSITION_CHECK_COMMIT_DRIFT " + checked.stdout + checked.stderr)
        checked = self.cli("record", "advisor-disposition", "--repo", str(self.repo),
                           "--slug", "advisor-flag-check", "--workflow-id", str(state["workflowId"]),
                           "--stage", "bogus", "--findings", "none", "--check")
        self.assertEqual(checked.returncode, 2, "DISPOSITION_CHECK_COMMIT_DRIFT " + checked.stdout + checked.stderr)
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).returncode, 0)

    def test_record_check_reports_errors_in_two_map_items(self) -> None:
        marker = "MAP_ERRORS_NOT_AGGREGATED"
        state = self.begin("two-item-errors")
        item = build_no_change_document("two errors")["behaviorMap"][0]
        first = {**item, "id": "BM_A", "kind": "invalid"}
        second = {**item, "id": "BM_B", "seam": ""}
        path = self.tmp / "two-errors.json"
        path.write_text(json.dumps({"behaviorMap": [first, second]}), encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        refused = self.cli("record", "preflight", "--repo", str(self.repo),
                           "--slug", "two-item-errors", "--workflow-id", str(state["workflowId"]),
                           "--input", str(path), "--check")
        self.assertEqual(refused.returncode, 2, marker + refused.stdout)
        self.assertIn("BM_A kind", refused.stderr, marker)
        self.assertIn("BM_B requires seam", refused.stderr, marker)
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before, marker)

    def test_record_check_reports_independent_fields_in_map_and_review(self) -> None:
        marker = "VALIDATION_ERRORS_NOT_AGGREGATED"
        state = self.begin("all-field-errors")
        item = build_no_change_document("all fields")["behaviorMap"][0]
        invalid_map = self.tmp / "invalid-map.json"
        invalid_map.write_text(json.dumps({"behaviorMap": [{**item, "id": "BM_A",
                                                             "kind": "invalid", "seam": "",
                                                             "boundaryInputs": [], "interpretations": [],
                                                             "sourceRefs": [{"type": "bogus", "id": "X"},
                                                                            {"type": "bogus", "id": "Y"}]}]}), encoding="utf-8")
        invalid_review = self.tmp / "invalid-review.json"
        invalid_review.write_text(json.dumps({"findings": [
            {"id": "A", "kind": "invalid", "material": True, "claim": "A claim"},
            {"id": "B", "kind": "behavioral", "material": True, "claim": ""},
            {"id": "C", "kind": "invalid", "material": True, "claim": ""},
        ]}), encoding="utf-8")
        before = self.cli("history", "--repo", str(self.repo)).stdout
        preflight = self.cli("record", "preflight", "--repo", str(self.repo),
                             "--slug", "all-field-errors", "--workflow-id", str(state["workflowId"]),
                             "--input", str(invalid_map), "--check")
        review = self.cli("record", "review", "--repo", str(self.repo),
                          "--slug", "all-field-errors", "--workflow-id", str(state["workflowId"]),
                          "--input", str(invalid_review), "--check")
        self.assertEqual((preflight.returncode, review.returncode), (2, 2), marker)
        missing = [text for text, output in (
            ("BM_A kind", preflight.stderr), ("BM_A requires seam", preflight.stderr),
            ("BM_A requires non-empty boundaryInputs", preflight.stderr),
            ("BM_A interpretations requires competing readings", preflight.stderr),
            ("sourceRef 1", preflight.stderr), ("sourceRef 2", preflight.stderr),
            ("finding A has an invalid kind", review.stderr), ("finding B requires claim", review.stderr),
            ("finding C has an invalid kind", review.stderr), ("finding C requires claim", review.stderr),
        ) if text not in output]
        self.assertFalse(missing, marker + ": " + ", ".join(missing))
        self.assertEqual(self.cli("history", "--repo", str(self.repo)).stdout, before, marker)

    def test_record_uses_the_single_active_workflow_identity(self) -> None:
        _, path = self.prepare_preflight_ready("active-record")
        path.write_text(json.dumps({"behaviorMap": build_no_change_document("active identity")["behaviorMap"]}),
                        encoding="utf-8")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo), "--input", str(path))
        self.assertEqual(recorded.returncode, 0, "RECORD_IDENTITY_STILL_AUTHORED " + recorded.stderr)
        state = json.loads(self.cli("status", "--repo", str(self.repo)).stdout)
        self.assertEqual(state["preflight"], "passed", "RECORD_IDENTITY_STILL_AUTHORED")

    def test_map_item_needs_no_unused_basis_prose(self) -> None:
        state, path = self.prepare_preflight_ready("no-basis")
        item = build_no_change_document("no basis")["behaviorMap"][0]
        item.pop("basis", None)
        path.write_text(json.dumps({"behaviorMap": [item]}), encoding="utf-8")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo),
                            "--slug", "no-basis", "--workflow-id", str(state["workflowId"]),
                            "--input", str(path))
        self.assertEqual(recorded.returncode, 0, "MAP_BASIS_CEREMONY " + recorded.stderr)

    def test_verification_runs_are_stored_once_across_evidence_revisions(self) -> None:
        from hooks.lib._workflow_db import _decode_json
        self.begin("run-parts")
        for _ in range(2):
            recorded = self.cli("verify", "--repo", str(self.repo), "--slug", "run-parts",
                                "--", sys.executable, "-c", "print('run')")
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
        with sqlite3.connect(self.database) as connection:
            rows = [_decode_json(row[0]) for row in connection.execute(
                "SELECT document_json FROM evidence WHERE kind = 'verification' ORDER BY rowid")]
        self.assertTrue(all("runsRevision" in row and "runs" not in row for row in rows),
                        "RUN_REFERENCE_CHANGED")
        self.assertTrue(all("updatedAt" not in row for row in rows), "REDUNDANT_DOCUMENT_TIMESTAMP")
        with sqlite3.connect(self.database) as connection:
            parts = connection.execute(
                "SELECT COUNT(*) FROM evidence_parts WHERE kind = 'run'").fetchone()[0]
        self.assertEqual(parts, 2, "RUN_REFERENCE_CHANGED")

    def test_run_receipt_keeps_digest_and_bounded_output_without_unused_gate_fields(self) -> None:
        from hooks.lib._workflow_db import read_evidence
        import hashlib

        self.begin("run-retention")
        raw = "a" * 5000
        result = self.cli("verify", "--repo", str(self.repo), "--slug", "run-retention",
                          "--", sys.executable, "-c", f"print({raw!r}, end='')")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout.splitlines()[-1])
        run = read_evidence(self.identity, receipt["evidenceId"])["document"]["runs"][0]
        self.assertEqual(run["outputSha256"], hashlib.sha256(raw.encode()).hexdigest(),
                         "RUN_OUTPUT_DIGEST_MISSING")
        self.assertLessEqual(len(run["outputTail"].encode()), 1024, "RUN_BODY_RETAINED")
        self.assertTrue({"command", "exitCode", "treeManifestId"} <= run.keys(),
                        "RUN_RECEIPT_INCOMPLETE")
        self.assertTrue({"gate", "at", "baseRef", "graphEvidenceId"}.isdisjoint(run),
                        "RUN_UNUSED_FIELDS_RETAINED")
        binary = self.cli("verify", "--repo", str(self.repo), "--slug", "run-retention",
                          "--", sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff' * 5000)")
        self.assertEqual(binary.returncode, 0, binary.stderr)
        receipt = json.loads(binary.stdout.splitlines()[-1])
        run = read_evidence(self.identity, receipt["evidenceId"])["document"]["runs"][-1]
        self.assertLessEqual(len(run["outputTail"].encode()), 1024, "NONUTF8_TAIL_OVERSIZED")

    def test_reserved_revision_root_keys_fail_before_evidence_insert(self) -> None:
        from hooks.lib._workflow_db import LedgerError, _insert_evidence, evidence_write
        wid = self.begin("reserved-root")["workflowId"]
        with sqlite3.connect(self.database) as connection:
            for field in ("runsRevision", "behaviorMapRevision"):
                with self.subTest(field=field):
                    try:
                        _insert_evidence(connection, [evidence_write(wid, "preflight", {field: "caller"})])
                    except LedgerError:
                        pass
                    else:
                        self.fail("RESERVED_ROOT_ACCEPTED " + field)

    def test_observed_runs_in_different_directories_do_not_merge(self) -> None:
        from hooks.lib._workflow_db import read_evidence

        self.begin("observed-cwd")
        (self.repo / "sub").mkdir()
        (self.repo / "fail").write_text("fail", encoding="utf-8")
        command = (sys.executable, "-c", "from pathlib import Path; raise SystemExit(Path('fail').exists())")
        receipts = []
        for cwd in (self.repo, self.repo / "sub"):
            result = self.cli("verify", "--repo", str(self.repo), "--slug", "observed-cwd",
                              "--observed", "--run-cwd", str(cwd), "--", *command)
            receipts.append(json.loads(result.stdout.splitlines()[-1]))
        self.assertEqual([item["exitCode"] for item in receipts], [1, 0])
        runs = read_evidence(self.identity, receipts[-1]["evidenceId"])["document"]["runs"]
        self.assertEqual(runs[-1].get("runCwd"), "sub", "OBSERVED_CWD_LOST")
        self.assertEqual(json.loads(self.cli("status", "--repo", str(self.repo)).stdout)["verification"],
                         "pending", "OBSERVED_CWD_COLLAPSED")

    def test_evidence_and_current_state_remain_readable_raw_json(self) -> None:
        self.begin("raw-forensics")
        self.assertEqual(self.cli("verify", "--repo", str(self.repo), "--slug", "raw-forensics",
                                  "--", sys.executable, "-c", "print('run')").returncode, 0)
        with sqlite3.connect(self.database) as connection:
            kinds = connection.execute("SELECT DISTINCT typeof(document_json) FROM evidence").fetchall()
            states = connection.execute("SELECT DISTINCT typeof(state_json) FROM workflows").fetchall()
            versions = connection.execute("SELECT DISTINCT state_schema_version FROM workflow_events").fetchall()
        self.assertEqual(kinds, [("text",)], "FORENSIC_JSON_COMPRESSED")
        self.assertEqual(states, [("text",)], "FORENSIC_JSON_COMPRESSED")
        self.assertEqual(versions, [(2,)], "FORENSIC_SCHEMA_NOT_BUMPED")

    def test_large_subtrees_are_shared_and_unlinked_refs_are_rejected(self) -> None:
        from hooks.lib._workflow_db import LedgerError, _insert_evidence, _schema, evidence_write, read_evidence
        self.begin("subtree-parts")
        shared = {"detail": "repeated evidence " * 30}
        workflow_id = self.cli("status", "--repo", str(self.repo)).stdout
        workflow_id = json.loads(workflow_id)["workflowId"]
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            _schema(connection)
            writes = [evidence_write(workflow_id, "governed-design", {"index": index, "payload": shared})
                      for index in range(3)]
            _insert_evidence(connection, writes)
            connection.commit()
            packed = [json.loads(row[0]) for row in connection.execute(
                "SELECT document_json FROM evidence WHERE kind = 'governed-design' ORDER BY rowid")]
            self.assertEqual(len({row["payload"]["detail"]["$part"] for row in packed}), 1,
                             "SUBTREE_COPIED")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM evidence_parts WHERE kind = 'subtree'").fetchone()[0], 1,
                "SUBTREE_COPIED")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM evidence_part_links").fetchone()[0], 3,
                "PART_EDGE_MISSING")
        for write in writes:
            self.assertEqual(read_evidence(self.identity, write.evidence_id)["document"]["payload"], shared)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM evidence_part_links WHERE evidence_id = ?", (writes[0].evidence_id,))
        with self.assertRaisesRegex(LedgerError, "unlinked"):
            read_evidence(self.identity, writes[0].evidence_id)
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            literal = {"payload": {"$part": packed[0]["payload"]["detail"]["$part"]}}
            write = evidence_write(workflow_id, "governed-design", literal)
            _insert_evidence(connection, [write])
            connection.commit()
        self.assertEqual(read_evidence(self.identity, write.evidence_id)["document"], literal,
                         "CALLER_MARKER_INTERPRETED_AS_PART")

    def test_map_mutations_grow_with_distinct_facts_in_a_synthetic_ledger(self) -> None:
        from hooks.lib._workflow_db import _insert_evidence, _schema, evidence_write, read_evidence

        state = self.begin("map-growth")
        workflow_id = state["workflowId"]
        repeated = "".join(__import__("hashlib").sha256(str(index).encode()).hexdigest()
                           for index in range(80))
        writes = [evidence_write(workflow_id, "preflight", {
            "workflowId": workflow_id,
            "behaviorMap": [{"id": "BM-1", "behavior": repeated, "expected": f"outcome {index}"}],
        }) for index in range(20)]
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            _schema(connection)
            _insert_evidence(connection, writes)
            connection.commit()
            bytes_used = sum(
                connection.execute(
                    f"SELECT COALESCE(SUM(length(CAST({column} AS BLOB))), 0) FROM {table}"
                ).fetchone()[0]
                for table in ("evidence", "evidence_parts", "evidence_part_links")
                for column in (row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            )
            shared_count = connection.execute(
                "SELECT COUNT(*) FROM evidence_parts WHERE kind = 'subtree'"
            ).fetchone()[0]
        self.assertEqual(shared_count, 1, "MAP_COMMON_FACT_COPIED")
        self.assertLess(bytes_used, 20000, "MAP_COMMON_FACT_COPIED")
        for index, write in enumerate(writes):
            item = read_evidence(self.identity, write.evidence_id)["document"]["behaviorMap"][0]
            self.assertEqual((item["behavior"], item["expected"]),
                             (repeated, f"outcome {index}"), "MAP_FACT_HYDRATION_MISMATCH")

    def test_pruning_one_workflow_keeps_a_shared_part_owned_by_another(self) -> None:
        from hooks.lib._workflow_db import (apply_retention, _insert_evidence, evidence_write,
                                             read_evidence, retention_inventory)

        first = self.begin("shared-first")
        second = self.begin("shared-second")
        shared = {"detail": "cross-workflow fact " * 50}
        writes = [evidence_write(str(state["workflowId"]), "governed-design", {"payload": shared})
                  for state in (first, second)]
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            _insert_evidence(connection, writes)
            connection.commit()
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM evidence_parts WHERE kind = 'subtree'").fetchone()[0], 1,
                "SHARED_PART_COPIED")
        inventory, error = retention_inventory(self.database, self.identity.key)
        self.assertIsNone(error)
        outcome = apply_retention(self.database, self.identity.key, inventory,
                                  {str(first["workflowId"])})
        self.assertEqual(outcome.status, "applied", outcome.error)
        self.assertEqual(read_evidence(self.identity, writes[1].evidence_id)["document"]["payload"],
                         shared, "SHARED_PART_PRUNED")

    def test_map_revisions_store_only_the_changed_item_ids(self) -> None:
        from hooks.lib._workflow_db import _insert_evidence, evidence_write, read_evidence

        workflow_id = self.begin("map-revisions")["workflowId"]
        base = [{"id": f"BM-{index}", "behavior": f"stable behavior {index}"}
                for index in range(30)]
        writes = []
        for revision in range(20):
            items = json.loads(json.dumps(base))
            items[0]["behavior"] = f"changed behavior {revision}"
            writes.append(evidence_write(workflow_id, "preflight", {"behaviorMap": items}))
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            _insert_evidence(connection, writes)
            connection.commit()
            revision_bytes = connection.execute(
                "SELECT SUM(length(CAST(content_json AS BLOB))) FROM evidence_parts "
                "WHERE kind = 'map-revision'").fetchone()[0]
        self.assertLess(revision_bytes, 4000, "MAP_REVISION_COPIES_UNCHANGED_IDS")
        for revision, write in enumerate(writes):
            items = read_evidence(self.identity, write.evidence_id)["document"]["behaviorMap"]
            self.assertEqual((len(items), items[0]["behavior"], items[-1]["behavior"]),
                             (30, f"changed behavior {revision}", "stable behavior 29"),
                             "MAP_REVISION_HYDRATION_MISMATCH")

    def test_map_items_keep_literal_storage_field_names(self) -> None:
        from hooks.lib._workflow_db import _insert_evidence, evidence_write, read_evidence

        workflow_id = self.begin("literal-items")["workflowId"]
        payload = {key: {"value": "x" * 250} for key in
                   ("runs", "behaviorMap", "runsRevision", "behaviorMapRevision", "$part", "$literal")}
        document = {"behaviorMap": [{"id": "BM-LITERAL", "boundaryInputs": payload}]}
        write = evidence_write(workflow_id, "preflight", document)
        try:
            with sqlite3.connect(self.database) as connection:
                _insert_evidence(connection, [write])
                connection.commit()
        except Exception as exc:
            self.fail("LITERAL_JSON_INTERPRETED_AS_LEDGER_MARKER: " + str(exc))
        self.assertEqual(read_evidence(self.identity, write.evidence_id)["document"], document,
                         "LITERAL_JSON_INTERPRETED_AS_LEDGER_MARKER")

    def test_preexisting_raw_evidence_keeps_literal_marker_keys(self) -> None:
        from hooks.lib._workflow_db import _insert_evidence, evidence_write, read_evidence

        workflow_id = self.begin("raw-marker-history")["workflowId"]
        document = {"boundaryInputs": [{"$literal": {"x": 1}}, {"$part": "user-input"}]}
        write = evidence_write(workflow_id, "preflight", document, schema_version=1)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO evidence(evidence_id,workflow_id,kind,schema_version,recorded_at,document_json) "
                "VALUES (?,?,?,?,?,?)",
                (write.evidence_id, write.workflow_id, write.kind, write.schema_version,
                 write.recorded_at, json.dumps(document)),
            )
        try:
            hydrated = read_evidence(self.identity, write.evidence_id)["document"]
        except Exception as exc:
            self.fail("LEGACY_LITERAL_KEY_LOST: " + str(exc))
        self.assertEqual(hydrated, document, "LEGACY_LITERAL_KEY_LOST")
        with sqlite3.connect(self.database) as connection:
            _insert_evidence(connection, [evidence_write(workflow_id, "preflight", document)])
        try:
            replayed = read_evidence(self.identity, write.evidence_id)["document"]
        except Exception as exc:
            self.fail("SCHEMA1_DUP_CORRUPTED: " + str(exc))
        self.assertEqual(replayed, document, "SCHEMA1_DUP_CORRUPTED")

    def test_review_manifests_share_unchanged_tree_entries(self) -> None:
        for index in range(100):
            (self.repo / f"file-{index:03}.txt").write_text(f"value {index}\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "large tree")
        self.begin("manifest-parts")
        first = self.cli("verify", "--repo", str(self.repo), "--slug", "manifest-parts",
                         "--", sys.executable, "-c", "print('run')")
        self.assertEqual(first.returncode, 0, first.stderr)
        (self.repo / "file-000.txt").write_text("changed\n", encoding="utf-8")
        second = self.cli("verify", "--repo", str(self.repo), "--slug", "manifest-parts",
                          "--", sys.executable, "-c", "print('run')")
        self.assertEqual(second.returncode, 0, second.stderr)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT manifest_id, manifest_json FROM review_manifests ORDER BY rowid").fetchall()
        self.assertEqual(len(rows), 2, "MANIFEST_DUPLICATE")
        self.assertTrue(isinstance(rows[0][1], str), "MANIFEST_BASE_NOT_RAW_JSON")
        from hooks.lib._workflow_db import read_manifest
        stored = sum(len(row[1]) for row in rows)
        manifests = [read_manifest(self.identity, row[0]) for row in rows]
        full = sum(len(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
                   for manifest in manifests)
        self.assertLess(stored, full * 0.7, "MANIFEST_DUPLICATE")
        self.assertNotEqual(manifests[0], manifests[1], "MANIFEST_LOST")

    def test_review_manifest_survives_git_filter_and_candidate_prune(self) -> None:
        from hooks.lib._workflow_db import read_manifest
        from hooks.lib.state_store import tree_manifest

        git(self.repo, "config", "filter.rewrite.clean", "sed s/RAW/NORM/g")
        git(self.repo, "config", "filter.rewrite.smudge", "cat")
        (self.repo / ".gitattributes").write_text("*.txt filter=rewrite\n", encoding="utf-8")
        (self.repo / "filtered.txt").write_text("RAW base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "filter")
        self.begin("filter-manifest")
        (self.repo / "filtered.txt").write_text("RAW dirty\n", encoding="utf-8")
        expected = tree_manifest(self.identity)
        verified = self.cli("verify", "--repo", str(self.repo), "--slug", "filter-manifest",
                            "--", sys.executable, "-c", "print('run')")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        with sqlite3.connect(self.database) as connection:
            manifest_id = connection.execute("SELECT manifest_id FROM review_manifests").fetchone()[0]
        git(self.repo, "prune", "--expire=now")
        self.assertEqual(read_manifest(self.identity, manifest_id), expected, "MANIFEST_LOST")

    def test_first_edit_needs_no_shadowed_production_code_record(self) -> None:
        from hooks.lib.workflow_state import ready_for_edit

        state, path = self.prepare_preflight_ready("first-edit")
        recorded = self.cli("record", "preflight", "--repo", str(self.repo),
                            "--slug", "first-edit", "--workflow-id", state["workflowId"],
                            "--input", str(path))
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        decided = self.cli("tdd", "--repo", str(self.repo), "--slug", "first-edit",
                           "--not-required", "no production behavior change")
        self.assertEqual(decided.returncode, 0, decided.stderr)
        self.assertEqual(ready_for_edit(self.identity, "app.py"), (True, []), "FIRST_EDIT_BLOCKED")
        self.assertNotIn("productionCode", json.loads(self.cli("status", "--repo", str(self.repo)).stdout),
                         "SHADOWED_STEP_RETAINED")
        self.assertEqual(self.cli("record-production-code", "--repo", str(self.repo)).returncode, 2,
                         "SHADOWED_RECORD_VERB_RETAINED")
        self.assertEqual(self.cli("set-phase", "--repo", str(self.repo), "--phase", "implementation",
                                  "--status", "passed").returncode, 2,
                         "SHADOWED_IMPLEMENTATION_VERB_RETAINED")

    def prepare_preflight_ready(self, slug: str = "atomic") -> tuple[dict[str, object], Path]:
        state = self.begin(slug)
        record_context_forge(self.repo, self.tmp)
        workflow_id = str(state["workflowId"])
        for command in (
            ("record", "advisor-result", "--slug", slug, "--workflow-id", workflow_id, "--stage", "preflight", "--source", "codex-advisor", "--input", empty_advisor_envelope(self.tmp, "completed")),
            ("record", "advisor-disposition", "--slug", slug, "--workflow-id", workflow_id, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.cli(*command, "--repo", str(self.repo))
            self.assertEqual(result.returncode, 0, result.stderr)
        document = self.tmp / "preflight.json"
        document.write_text(json.dumps(build_no_change_document("ledger proof")), encoding="utf-8")
        return state, document

    def test_advisor_success_requires_an_immutable_envelope(self) -> None:
        state = self.begin("advisor-envelope")
        record_context_forge(self.repo, self.tmp)
        before = self.ledger_counts()
        result = self.cli(
            "record", "advisor-result", "--repo", str(self.repo),
            "--slug", "advisor-envelope", "--workflow-id", str(state["workflowId"]),
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.ledger_counts(), before, "bare success mutated the ledger")
        accepted = self.cli(
            "record", "advisor-result", "--repo", str(self.repo),
            "--slug", "advisor-envelope", "--workflow-id", str(state["workflowId"]),
            "--stage", "preflight", "--source", "codex-advisor",
            "--input", empty_advisor_envelope(self.tmp, "completed"),
        )
        self.assertEqual(accepted.returncode, 0, "RECORD_KIND_REFUSED " + accepted.stderr)
        self.assertGreater(self.ledger_counts()["evidence"], before["evidence"], "RECORD_KIND_REFUSED")

    def test_record_derives_the_active_workflow_identity(self) -> None:
        self.begin("derived-identity")
        record_context_forge(self.repo, self.tmp)
        recorded = self.cli(
            "record", "advisor-result", "--repo", str(self.repo),
            "--stage", "preflight", "--source", "codex-advisor",
            "--input", empty_advisor_envelope(self.tmp, "completed"),
        )
        self.assertEqual(recorded.returncode, 0, "DERIVED_BINDING_WRONG " + recorded.stderr)
        state = json.loads(self.cli("status", "--repo", str(self.repo)).stdout)
        self.assertEqual(state["advisorPreflight"]["status"], "completed", "DERIVED_BINDING_WRONG")

    def test_status_without_database_is_missing_not_success(self) -> None:
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

    def test_old_json_snapshot_cannot_become_an_active_workflow(self) -> None:
        self.slot.mkdir(parents=True)
        (self.slot / "workflow.json").write_text(json.dumps({
            "schemaVersion": 1, "repo": self.identity.as_dict(),
            "slug": "old-json", "workflowId": uuid.uuid4().hex,
        }), encoding="utf-8")
        status = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 2, status.stdout)
        self.assertFalse(self.database.exists(), "old JSON must not create an authoritative ledger")

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
            "repoContextForge", "advisorPreflight", "preflight",
            "tdd", "implementation", "verification",
            "codeReview", "finalReview",
        }
        self.assertTrue(stable <= set(projection), stable - set(projection))
        rendered = json.dumps(projection, sort_keys=True)
        for private_detail in (
            "workflow.sqlite3", "workflow_events", "active_projection",
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

    def test_status_repairs_a_missing_or_stale_active_pointer(self) -> None:
        first = json.loads(self.cli("begin", "--repo", str(self.repo), "--slug", "first").stdout)
        second = json.loads(self.cli("begin", "--repo", str(self.repo), "--slug", "second").stdout)
        slot = self.state_root / resolve_repo_identity(self.repo).key
        database = slot / "workflow.sqlite3"

        import sqlite3
        connection = sqlite3.connect(database)
        try:
            first_event = connection.execute(
                "SELECT event_id FROM workflow_events WHERE workflow_id = ? ORDER BY event_id DESC LIMIT 1",
                (first["workflowId"],),
            ).fetchone()[0]
            connection.execute("DELETE FROM active_projection")
            connection.execute(
                "INSERT INTO active_projection(slot, workflow_id, event_id) VALUES (1, ?, ?)",
                (first["workflowId"], first_event),
            )
            connection.commit()
        finally:
            connection.close()

        repaired = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(json.loads(repaired.stdout)["workflowId"], second["workflowId"])
        connection = sqlite3.connect(database)
        try:
            pointer = connection.execute(
                "SELECT workflow_id, event_id FROM active_projection WHERE slot = 1"
            ).fetchone()
            latest = connection.execute(
                "SELECT event_id FROM workflow_events WHERE workflow_id = ? ORDER BY event_id DESC LIMIT 1",
                (second["workflowId"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(pointer, (second["workflowId"], latest))

        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE active_projection SET event_id = 999999 WHERE slot = 1")
            connection.commit()
        finally:
            connection.close()
        dangling = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(dangling.returncode, 0, dangling.stderr)
        connection = sqlite3.connect(database)
        try:
            repaired_pointer = connection.execute(
                "SELECT workflow_id, event_id FROM active_projection WHERE slot = 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(repaired_pointer, (second["workflowId"], latest))

    def test_invalid_transition_appends_no_event_or_evidence(self) -> None:
        self.begin("invalid")
        before = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        refused = self.cli(
            "set-phase", "--repo", str(self.repo),
            "--phase", "implementation", "--status", "passed",
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
            pointer = connection.execute(
                "SELECT workflow_id, event_id FROM active_projection WHERE slot = 1"
            ).fetchone()
            connection.execute("""
                CREATE TRIGGER abort_projection BEFORE UPDATE ON active_projection
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
            current_pointer = connection.execute(
                "SELECT workflow_id, event_id FROM active_projection WHERE slot = 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(after, before)
        self.assertEqual(current_pointer, pointer)

    def test_history_refuses_an_invalid_older_event_instead_of_publishing_it(self) -> None:
        # History validates immutable event metadata even though state now
        # lives once per workflow, not on each event.
        self.begin("first")
        self.begin("second")
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE workflow_events SET state_schema_version = 999 "
                "WHERE event_id = (SELECT MIN(event_id) FROM workflow_events)"
            )
            connection.commit()
        finally:
            connection.close()

        status = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 0, status.stderr)
        history = self.cli("history", "--repo", str(self.repo))
        self.assertEqual(history.returncode, 2, history.stdout)
        self.assertIn("unsupported", history.stderr)

    def test_future_event_schema_or_policy_fails_closed(self) -> None:
        for column in ("state_schema_version", "policy_version"):
            with self.subTest(column=column):
                shutil.rmtree(self.state_root, ignore_errors=True)
                self.begin(f"future-{column}")
                connection = sqlite3.connect(self.database)
                try:
                    connection.execute(
                        f"UPDATE workflow_events SET {column} = 999 "
                        "WHERE event_id = (SELECT MAX(event_id) FROM workflow_events)"
                    )
                    connection.commit()
                finally:
                    connection.close()
                result = self.cli("status", "--repo", str(self.repo))
                self.assertEqual(result.returncode, 2)
                self.assertIn("event schema or policy", result.stderr)

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
            "--stage", "preflight", "--source", "codex-advisor", "--input", empty_advisor_envelope(self.tmp, "completed"),
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("workflow instance", refused.stderr)
        after = json.loads(self.cli("history", "--repo", str(self.repo)).stdout)["events"]
        self.assertEqual(after, history)

    def test_corrupt_authoritative_database_never_falls_back_to_stale_json(self) -> None:
        self.begin("corrupt-store")
        first = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.database.write_bytes(b"not a sqlite database")
        refused = self.cli("status", "--repo", str(self.repo))
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn("Traceback", refused.stderr)
        self.assertIn("workflow database", refused.stderr)



if __name__ == "__main__":
    unittest.main(verbosity=2)
