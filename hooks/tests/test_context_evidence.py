"""Real producer/hook/recovery checks; these are not a native agent experiment."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hooks.lib import context_evidence as evidence
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.state_store import repo_state_dir
from hooks.tests.test_workflow_hooks import HookHarness

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills/repo-production-workflow/scripts/context.py"


class ContextEvidenceTests(HookHarness):
    def setUp(self) -> None:
        super().setUp()
        begun = self.state("begin", "--slug", "context")
        self.assertEqual(begun.returncode, 0, begun.stderr)
        self.wid = json.loads(begun.stdout)["workflowId"]
        self.identity = resolve_repo_identity(self.repo)

    def hook(self, script: str, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "hooks" / script)],
                              cwd=self.repo, env=self.env, input=json.dumps(payload),
                              capture_output=True, text=True, timeout=15)

    def run_context(self, *args: str, success: bool = True, repo: Path | None = None,
                    stdin: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run([sys.executable, str(CLI), *args, "--repo", str(repo or self.repo)],
                                cwd=self.repo, env=self.env, input=stdin, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return result

    def read(self, start: int = 1, end: int = 1, path: str = "app.py") -> dict:
        return json.loads(self.run_context("read", "--path", path, "--start", str(start), "--end", str(end)).stdout)

    def show(self, row: dict, *args: str) -> dict:
        return json.loads(self.run_context("show", "--id", row["id"], *args).stdout)

    def document(self) -> dict:
        return evidence.context_document(self.identity, self.wid)

    def rearm(self) -> str:
        result = self.hook("skill-discipline-rearm.py", {"cwd": str(self.repo), "source": "compact"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_optional_observation_rejects_nul_and_uses_working_directory(self) -> None:
        for cwd, command in ((str(self.repo) + "\0", "cat app.py"),
                             (str(self.repo), "cat bad\0.py")):
            payload = {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}}
            result = self.hook("code-quality-gate.py", payload)
            self.assertEqual(result.returncode, 0, "OPTIONAL_PATH_BROKE_HOOK: " + result.stderr)
        command = "cat app.py"
        output = subprocess.check_output(["bash", "-c", command], cwd=self.repo, text=True)
        payload = {"working_directory": str(self.repo), "tool_name": "Bash",
                   "tool_input": {"command": command}, "tool_response": {"stdout": output, "exit_code": 0}}
        result = subprocess.run([sys.executable, str(ROOT / "hooks/code-quality-gate.py")],
                                cwd=self.tmp, env=self.env, input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.document()["records"]
        self.assertEqual(len(rows), 1, "WORKING_DIRECTORY_LOST_REQUEST")
        self.assertEqual(rows[0]["output"], output)
        self.assertEqual(rows[0]["sourceBinding"], "unknown")

    def test_revalidation_preserves_recovery_without_reopening_proof(self) -> None:
        row = self.read()
        self.complete_workflow("context", resume=True)
        self.assertNotIn("sourceData", self.rearm())
        self.run_context("show", "--id", row["id"], success=False)
        governance = self.repo / "skills/diagnose/SKILL.md"
        governance.parent.mkdir(parents=True)
        governance.write_text("updated governance\n")
        self.assertEqual(self.post_edit("skills/diagnose/SKILL.md").returncode, 0)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertTrue(state["revalidation"])
        self.assertIn("sourceData", self.rearm(), "REVALIDATION_LOST_CONTEXT")
        before = self.state("history").stdout
        self.assertEqual(self.show(row)["output"], row["output"])
        self.assertEqual(self.read()["output"], row["output"])
        self.assertEqual(self.state("history").stdout, before, "CONTEXT_REOPENED_PROOF")

    def test_command_only_and_count_results_are_not_content_claims(self) -> None:
        before = self.state("history").stdout
        for command in ("sed -n '1p' app.py", "wc -l app.py"):
            result = subprocess.run(["bash", "-c", command], cwd=self.repo, capture_output=True, text=True, check=True)
            self.assertTrue(result.stdout)
            hook = self.hook("code-quality-gate.py", {"cwd": str(self.repo), "session_id": "request-only",
                "tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertNotIn("Inspected this pass", self.rearm(), "REQUEST_IS_NOT_DELIVERED_CONTEXT")
        self.assertNotIn("sourceData", self.rearm())
        self.assertTrue(all(row["delivery"] == "unknown" for row in self.document()["records"]))
        self.assertEqual(self.state("history").stdout, before)

    def test_fresh_process_recovers_partial_content_and_reaches_missing_scope(self) -> None:
        source = "FIRST\nSECOND\nTHIRD\nNEEDED_AFTER_COMPACTION\n"
        (self.repo / "app.py").write_text(source)
        before = self.state("history").stdout
        first = self.read(1, 2)
        self.assertEqual(first["output"], "FIRST\nSECOND\n")
        self.assertEqual(first["range"], [1, 2])
        self.assertEqual(first["delivery"], "unknown")
        self.assertNotIn("NEEDED_AFTER_COMPACTION", self.rearm())
        # A new process supplies actual old fragment bytes, not an 'already read' marker.
        recovered = self.show(first)
        self.assertEqual(recovered["output"], first["output"])
        self.assertEqual(recovered["freshness"], "source-match")
        self.assertIn(str(CLI), self.rearm())
        needed = self.read(3, 4)
        self.assertIn("NEEDED_AFTER_COMPACTION", needed["output"])
        repeated = self.read(1, 2)
        self.assertEqual(repeated["output"], first["output"])
        self.assertEqual(repeated["id"], first["id"])
        overlap = self.read(2, 3)
        self.assertEqual(overlap["output"], "SECOND\nTHIRD\n")
        self.assertEqual(len(self.document()["records"]), 3)
        self.assertEqual(self.state("history").stdout, before, "CONTEXT_CHANGED_PROOF_STATE")

    def test_source_drift_missing_and_fifo_never_silently_reuse_current_evidence(self) -> None:
        row = self.read()
        (self.repo / "app.py").write_text("CHANGED\n")
        refused = self.run_context("show", "--id", row["id"], success=False)
        self.assertIn("changed", refused.stderr)
        self.assertNotIn("sourceData", self.rearm())
        self.assertEqual(self.show(row, "--historical")["output"], row["output"])
        (self.repo / "app.py").unlink()
        self.run_context("show", "--id", row["id"], success=False)
        os.mkfifo(self.repo / "app.py")
        self.run_context("show", "--id", row["id"], success=False)
        self.assertNotIn("sourceData", self.rearm())
        self.assertEqual(self.show(row, "--historical")["freshness"], "unavailable")

    def test_output_budget_has_lossless_explicit_continuation(self) -> None:
        source = "".join(f"{i:03d} αβγ {'x' * 100}\n" for i in range(100))
        (self.repo / "app.py").write_text(source)
        start, outputs = 1, []
        while start is not None:
            row = self.read(start, 100)
            self.assertLessEqual(len(json.dumps(row, ensure_ascii=False).encode()), 2 * evidence.OUTPUT_BYTES)
            self.assertEqual(row["range"][0], start)
            outputs.append(row["output"])
            start = row["nextStart"]
        self.assertEqual("".join(outputs), source)
        (self.repo / "app.py").write_text("x" * (evidence.OUTPUT_BYTES + 1))
        self.run_context("read", "--path", "app.py", success=False)
        self.assertEqual(self.read(2, 2)["range"], None)  # Explicit beyond-EOF empty scope, not whole-file coverage.

    def test_observed_tool_output_is_retained_but_never_source_bound_or_final_delivery(self) -> None:
        (self.repo / "app.py").write_text("")  # The failed command has a real, empty observed output.
        for command, exit_code in (("wc -l app.py", 0), ("cat missing.txt app.py", 1)):
            executed = subprocess.run(["bash", "-c", command], cwd=self.repo, capture_output=True, text=True)
            self.assertEqual(executed.returncode, exit_code)
            payload = {"cwd": str(self.repo), "tool_name": "Bash", "tool_use_id": command,
                       "tool_input": {"command": command},
                       "tool_response": {"stdout": executed.stdout, "exit_code": executed.returncode}}
            self.assertEqual(self.hook("code-quality-gate.py", payload).returncode, 0)
            row = self.document()["records"][-1]
            self.assertEqual(row["kind"], "observed-output")
            self.assertEqual(row["output"], executed.stdout)
            self.assertEqual(row["sourceBinding"], "unknown")
            self.run_context("show", "--id", row["id"], success=False)
            self.assertEqual(self.show(row, "--historical")["freshness"], "unknown")
        self.assertNotIn("sourceData", self.rearm())
        history = json.loads(self.run_context("list", "--historical").stdout)
        self.assertTrue(history["historicalListing"])
        self.assertEqual(history["records"][0]["kind"], "observed-output")
        self.assertEqual(json.loads(self.run_context("list").stdout)["records"], [])
        report = json.loads(self.run_context("probe", stdin=json.dumps(payload)).stdout)
        self.assertTrue(report["hasToolResponse"])
        self.assertFalse(report["finalDeliveryObservable"])
        self.assertNotIn("output", report)

    def test_observed_large_response_cap_does_not_assert_complete_delivery(self) -> None:
        text = "λ" * evidence.OUTPUT_BYTES
        payload = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": "cat app.py"},
                   "tool_response": text}
        self.assertEqual(self.hook("code-quality-gate.py", payload).returncode, 0)
        row = self.document()["records"][-1]
        self.assertTrue(row["captureTruncated"])
        self.assertIsNone(row["observedOutputDigest"])  # No unbounded full-response hash or completeness claim.
        self.assertLessEqual(len(row["output"].encode()), evidence.OUTPUT_BYTES)
        self.assertEqual(row["delivery"], "unknown")
        self.assertNotIn("sourceData", self.rearm())

    def test_legacy_and_corrupt_sidecars_never_upgrade_to_source_evidence(self) -> None:
        first = self.read()
        sidecar = repo_state_dir(self.identity) / "reads" / f"{self.wid}.json"
        original = json.loads(sidecar.read_text())
        for kind in ([], {}, None):
            damaged = {**original, "records": [{"kind": kind}, *original["records"]]}
            sidecar.write_text(json.dumps(damaged))
            self.assertEqual(self.show(first)["output"], first["output"], "MALFORMED_KIND_BROKE_RECOVERY")
        sidecar.write_text(json.dumps(original))
        document = json.loads(sidecar.read_text())
        document["records"][0]["output"] = "FORGED_CONTENT"
        sidecar.write_text(json.dumps(document))
        self.run_context("show", "--id", first["id"], success=False)
        # Even internally consistent but false source claims are not current evidence.
        forged = {key: value for key, value in first.items() if key not in {"id", "at"}}
        forged["output"] = "NOT_THE_SOURCE\n"
        forged["outputDigest"] = evidence._hash(forged["output"].encode())
        forged_row = evidence.remember_context(self.identity, self.wid, forged)
        refused = self.run_context("show", "--id", forged_row["id"], success=False)
        self.assertIn("corrupt-snapshot", refused.stderr)
        self.assertNotIn("NOT_THE_SOURCE", self.rearm())
        sidecar.write_text(json.dumps({"schemaVersion": 1, "reads": [["app.py", first["sourceDigest"]]]}))
        self.assertIn("history only", self.rearm())
        self.assertNotIn("sourceData", self.rearm())
        self.read()
        self.assertEqual(self.document()["legacyHistoryOnly"], 1)
        sidecar.unlink()
        self.run_context("show", "--id", first["id"], success=False)
        sidecar.symlink_to(self.repo / "app.py")
        self.assertEqual(self.document()["records"], [])

    def test_multiple_worktrees_workflows_and_outside_paths_are_not_interchangeable(self) -> None:
        row = self.read()
        other = self.tmp / "worktree"
        self.git("worktree", "add", "--detach", str(other))
        self.assertEqual(self.state("begin", "--slug", "context", repo=other).returncode, 0)
        self.run_context("show", "--id", row["id"], repo=other, success=False)
        (self.repo / "outside.py").symlink_to(other / "app.py")
        self.run_context("read", "--path", "outside.py", success=False)
        self.assertEqual(self.state("begin", "--slug", "replacement").returncode, 0)
        self.run_context("show", "--id", row["id"], success=False)
        self.assertEqual(evidence.active_context(self.identity, self.wid), "")

    def test_concurrent_writers_preserve_distinct_ranges_and_private_permissions(self) -> None:
        (self.repo / "app.py").write_text("".join(f"line {i}\n" for i in range(20)))
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda n: self.read(n, n), range(1, 9)))
        self.assertEqual(len({row["id"] for row in rows}), 8)
        self.assertEqual(len(self.document()["records"]), 8)
        sidecar = repo_state_dir(self.identity) / "reads" / f"{self.wid}.json"
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(sidecar.parent.stat().st_mode), 0o700)
        self.assertEqual(self.state("history").returncode, 0)

    def test_encoded_store_window_and_reference_pagination_are_bounded(self) -> None:
        (self.repo / "app.py").write_text("line\n" * 100)
        for number in range(1, 66):
            evidence.snapshot(self.identity, self.wid, "app.py", number, number)
        self.assertEqual(len(self.document()["records"]), evidence.RECORD_LIMIT)
        page = json.loads(self.run_context("list").stdout)
        self.assertEqual(page["nextOffset"], 4)
        page2 = json.loads(self.run_context("list", "--offset", "4").stdout)
        self.assertNotEqual(page["records"][0]["id"], page2["records"][0]["id"])
        for budget in (0, 20, 256, evidence.CONTEXT_BYTES):
            self.assertLessEqual(len(evidence.context_window(self.identity, self.wid, budget).encode()), budget)
        for number in range(65):
            evidence.remember_context(self.identity, self.wid, {
                "kind": "request", "command": str(number), "paths": ["λ" * 3000], "delivery": "unknown"})
        sidecar = repo_state_dir(self.identity) / "reads" / f"{self.wid}.json"
        self.assertLessEqual(sidecar.stat().st_size, evidence.STORE_BYTES)
        self.assertLessEqual(len(self.document()["records"]), evidence.RECORD_LIMIT)
        self.assertLessEqual(sum(row["kind"] != "snapshot" for row in self.document()["records"]), 16)

    def test_reference_only_windows_do_not_read_source_content(self) -> None:
        row = self.read()
        # Audit real opens rather than mocking the reader or accepting a cached claim.
        opened = []
        active = [True]
        source = str(self.repo / "app.py")
        sys.addaudithook(lambda event, args: opened.append(args[0])
                        if active[0] and event == "open" and str(args[0]) == source else None)
        try:
            for inline, budget in ((False, evidence.CONTEXT_BYTES), (True, 1)):
                references = evidence.context_window(self.identity, self.wid, budget, inline=inline)
                if not inline:
                    self.assertIn(row["id"], references)
                self.assertNotIn("sourceData", references)
                self.assertEqual(opened, [], "REFERENCE_LIST_REREAD_SOURCE")
            available = evidence.context_window(self.identity, self.wid, evidence.CONTEXT_BYTES)
            self.assertIn("sourceData", available)
            self.assertEqual(opened, [source], "INLINE_FRAGMENT_NOT_VERIFIED")
        finally:
            active[0] = False
        # A reference is not freshness proof: ordinary recovery must still check.
        (self.repo / "app.py").write_text("changed after reference\n")
        self.run_context("show", "--id", row["id"], success=False)

    def test_rcf_consumer_uses_same_bounded_candidates_without_coverage_mutation(self) -> None:
        row = self.read()
        path = ROOT / "skills/repo-context-forge/scripts/bootstrap.py"
        spec = importlib.util.spec_from_file_location("context_rcf_consumer", path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        before = self.state("history").stdout
        candidates = adapter.active_context(self.identity, self.wid, inline=False)
        self.assertIn(row["id"], candidates)
        self.assertNotIn("sourceData", candidates)
        self.assertNotIn("satisfied", candidates)
        self.assertEqual(self.state("history").stdout, before)
        self.assertEqual(adapter.active_context(self.identity, "foreign-workflow"), "")
        # External RCF is a separate dependency; this checks its exact imported consumer,
        # not a fabricated successful graph/producer run.

    def test_cost_benchmark_binds_clean_source_and_refuses_untracked_or_edited_code(self) -> None:
        from benchmarks.context_recovery import source_identity
        original = source_identity(self.repo)
        self.assertEqual(len(original["commit"]), 40)
        self.assertEqual(len(original["tree"]), 40)
        untracked = self.repo / "not_committed.py"
        untracked.write_text("untracked code\n")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            source_identity(self.repo)
        untracked.unlink()
        (self.repo / "app.py").write_text("changed code\n")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            source_identity(self.repo)

    def test_capture_audit_separates_repeat_paths_scopes_outputs_and_unknown_savings(self) -> None:
        script = ROOT / "benchmarks/context_evidence_audit.py"
        events = [
            {"id": "1", "path": "app.py", "operation": "sed", "requestedScope": [1, 2],
             "sourceVersion": "version-A", "resultRef": "synthetic-fixture:1", "output": "one\ntwo\n"},
            {"id": "2", "path": "app.py", "operation": "sed", "requestedScope": [3, 4],
             "sourceVersion": "version-A", "resultRef": "synthetic-fixture:2", "output": "three\nfour\n"},
            {"id": "3", "path": "app.py", "operation": "sed", "requestedScope": [1, 2],
             "sourceVersion": "version-A", "resultRef": "synthetic-fixture:3", "output": "one\ntwo\n"},
            {"id": "4", "path": "app.py", "operation": "sed", "requestedScope": [1, 2],
             "sourceVersion": None, "resultRef": "synthetic-fixture:4", "output": None},
        ]
        events.extend([
            {"id": str(n), "path": "empty.txt", "operation": "cat", "requestedScope": {},
             "sourceVersion": "empty-version", "resultRef": f"synthetic-fixture:{n}", "output": ""}
            for n in (5, 6)
        ])
        events.extend([
            {"id": str(n), "path": None, "operation": "compound-output", "requestedScope": None,
             "attribution": "unbound", "sourceVersion": None, "resultRef": f"synthetic-fixture:{n}", "output": "same"}
            for n in (7, 8)
        ])
        events.extend([
            {"id": str(n), "path": "empty.txt", "operation": "cat", "requestedScope": {},
             "attribution": "unbound", "sourceVersion": "empty-version",
             "resultRef": f"synthetic-fixture:{n}", "output": ""}
            for n in (9, 10)
        ])
        capture = self.tmp / "labels.json"
        capture.write_text(json.dumps({"provenance": {"traceSha256": evidence._hash(json.dumps(events).encode()), "labeler": "synthetic-test"},
                                       "events": events}))
        result = subprocess.run([sys.executable, str(script), str(capture)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["unboundPath"], 4)
        self.assertEqual(report["counts"]["repeatedPath"], 4)
        self.assertEqual(report["counts"]["sameRequestedScope"], 3)
        self.assertEqual(report["counts"]["sameVersionOutputCandidates"], 2)
        self.assertIsNone(report["avoidableRetrieval"])
        self.assertIsNone(report["tokenSavings"])
        capture.write_text(json.dumps({"events": events}))
        result = subprocess.run([sys.executable, str(script), str(capture)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("provenance", result.stderr)
