#!/usr/bin/env python3
"""Issue #96 attacks: the ledger interface costs only what its checks need.

Every case drives the real workflow CLI, hooks, and advisor wrapper against an
isolated state root; the provider behind the wrapper is the only stand-in, and
those cases assert only what the wrapper itself emits and records.
"""
from __future__ import annotations

import copy
from contextlib import closing
import io
import itertools
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import (  # noqa: E402
    FORMAT_REFUSAL, LEDGER_FORMAT, _ensure_authority, _open_connection, _schema, database_path,
)
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_documents import advisor_envelope  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError, advisor_disposition, commit_evidence_phase, complete, evidence_document, invalidate_after_edit,
    next_operation, read_workflow, ready_for_edit, record_advisor_result, review_blockers, set_phase,
)
from hooks.tests.support import approve_preflight, checkpoint_channels, record_context_forge  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
WRAPPER = ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh"
PRE_TOOL = ROOT / "hooks" / "rcf-intake-gate.py"
POST_TOOL = ROOT / "hooks" / "code-quality-gate.py"
REARM = ROOT / "hooks" / "skill-discipline-rearm.py"
# The pass base: its hooks/lib wrote every snapshot-era ledger this change migrates.
SNAPSHOT_ERA = "ddd86cbf72a38f2840f6208aca71887171e12590"
# The last commit writing format-2 ledgers, whose evidence parts are plain text.
FORMAT_TWO = "b838e607948da5e01ee1f5ec496bc6ff86c9435b"
TEST_APP = (
    "import unittest\nimport app\n\n\nclass AppTest(unittest.TestCase):\n"
    "    def test_value(self):\n        self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n"
)
TEST_OTHER = (
    "import unittest\nimport app\n\n\nclass OtherTest(unittest.TestCase):\n"
    "    def test_other(self):\n        self.assertEqual(app.other, 2, 'OTHER_NOT_TWO')\n"
)
STUB_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["STUB_PROMPT"], "w").write(sys.stdin.read())
mode = os.environ.get("STUB_MODE", "big")
sys.stderr.write("session id: 11111111-2222-3333-4444-555555555555\n" + "provider noise line\n" * 4000)
if mode == "fail":
    sys.exit(1)
if mode in ("approved", "commit-ready", "context-mismatch", "fix-before-commit"):
    print(json.dumps({"schemaVersion": 1, "verdict": mode, "findings": [{"id": "F-1", "claim": "fixture finding",
        "material": True, "kind": "nonbehavioral"}] if mode == "fix-before-commit" else []}))
    sys.exit(0)
if mode == "garbage":
    print("GARBAGE_ANSWER " + "g" * 3000)
    sys.exit(0)
print(json.dumps({"schemaVersion": 1, "verdict": "changes-required", "findings": [
    {"id": f"SPEC-{i}", "claim": "c" * 400 + f" finding {i}", "material": True, "kind": "nonbehavioral"}
    for i in range(1, 21)]}))
'''


def item(identifier: str, refs=(), **extra) -> dict[str, object]:
    return {"id": identifier, "kind": "contract", "basis": "fixture contract", "behavior": f"{identifier} behavior",
            "seam": "fixture app module", "expected": "app.value is 2",
            "sourceRefs": list(refs), **extra}


class Ceremony(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ledger-ceremony-"))
        self.previous = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                   "CODEX_ADVISOR_ACTIVE", "ADVISOR_ACTIVE"}}
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                         "PYTHONDONTWRITEBYTECODE": "1", "CODEX_THREAD_ID": "ceremony-lead"})
        self.repo = self.make_repo("repo")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CODEX_WORKFLOW_STATE_ROOT"] = self.previous
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, name: str) -> Path:
        repo = self.tmp / name
        repo.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "c@example.invalid"), ("config", "user.name", "C")):
            self.git(repo, *args)
        (repo / "app.py").write_text("value = 1\nother = 1\n", encoding="utf-8")
        (repo / "test_app.py").write_text(TEST_APP, encoding="utf-8")
        (repo / "test_other.py").write_text(TEST_OTHER, encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "base")
        return repo

    def git(self, repo: Path, *args: str) -> None:
        result = subprocess.run(["git", *args], cwd=repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def cli(self, *args: str, input: str | None = None, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), *args], cwd=cwd or self.repo, env=env or self.env,
                              input=input, capture_output=True, text=True)

    def ok(self, *args: str, input: str | None = None) -> dict[str, object]:
        result = self.cli(*args, input=input)
        self.assertEqual(result.returncode, 0, " ".join(args[:2]) + ": " + result.stdout[-500:] + result.stderr[-500:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def record_preflight(self, document: dict[str, object]) -> dict[str, object]:
        approve_preflight(self.repo, document)
        return self.ok("record", "preflight", "--input", "-", input=json.dumps(document))

    def ok_raw(self, *args: str) -> str:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        return result.stdout

    def begin(self, repo: Path | None = None, slug: str = "ceremony") -> str:
        repo = repo or self.repo
        begun = self.cli("begin", "--slug", slug, "--intent", "issue 96 fixture intent.", cwd=repo)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        record_context_forge(repo, self.tmp)
        return str(json.loads(begun.stdout)["workflowId"])

    def state(self, repo: Path | None = None) -> dict[str, object]:
        return read_workflow(resolve_repo_identity(repo or self.repo)) or {}

    def rows(self, repo: Path | None = None) -> dict[str, int]:
        path = database_path(resolve_repo_identity(repo or self.repo))
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def wrapper(self, mode: str, *extra: str, phase: str = "preflight-advice") -> tuple[subprocess.CompletedProcess[str], str]:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "codex"
        stub.write_text(STUB_CODEX, encoding="utf-8")
        stub.chmod(0o755)
        prompt = self.tmp / f"prompt-{mode}.txt"
        env = {**self.env, "PATH": f"{bin_dir}:{self.env['PATH']}", "STUB_MODE": mode, "STUB_PROMPT": str(prompt)}
        draft_args = []
        if phase == "preflight-advice":
            draft = self.tmp / "draft.json"
            draft.write_text(json.dumps({"authoritativeContract": "fixture", "behaviorMap": [item("BM_ONE")]}))
            draft_args = ["--preflight-file", str(draft)]
        result = subprocess.run(["bash", str(WRAPPER), "--slug", "ceremony", "--phase", phase,
                                 "--cwd", str(self.repo), "--design-absent", "fixture has no design", *draft_args, *extra,
                                 "--", "scope question"], env=env, capture_output=True, text=True)
        return result, prompt.read_text(encoding="utf-8") if prompt.exists() else ""


class TerseReceipts(Ceremony):
    def test_mutation_receipts_and_evidence_reads_are_terse(self) -> None:
        marker = "RECEIPT_NOT_TERSE"
        wid = self.begin()
        paused = self.cli("pause", "--slug", "ceremony", "--workflow-id", wid, "--reason", "waiting on a reviewer")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        self.assertEqual(json.loads(paused.stdout)["workflowId"], wid, marker)
        self.assertLessEqual(len(paused.stdout.encode()), 1024, marker)
        evidence_id = str(self.state()["repoContextForgeEvidence"])
        meta = self.cli("evidence", "--evidence-id", evidence_id)
        self.assertEqual(meta.returncode, 0, meta.stderr)
        self.assertNotIn('"document"', meta.stdout, f"{marker}: evidence read echoes the document")
        self.assertLessEqual(len(meta.stdout.encode()), 1024, marker)
        full = self.cli("evidence", "--full", "--evidence-id", evidence_id)
        self.assertEqual(full.returncode, 0, f"{marker}: {full.stderr}")
        self.assertEqual(json.loads(full.stdout)["document"], evidence_document(resolve_repo_identity(self.repo), evidence_id), marker)


class PreflightContinuation(Ceremony):
    def advice(self, verdict: str = "approved", contract: str = "continuation repair") -> dict[str, object]:
        # These are recorder inputs, not a substituted advisor transport.
        draft = self.tmp / "draft.json"
        draft.write_text(json.dumps({"authoritativeContract": contract,
                                     "behaviorMap": [item("BM_ONE")]}))
        envelope = self.tmp / "advice.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "verdict": verdict, "findings": [
            {"id": "SPEC-1", "claim": "revise the draft", "material": True, "kind": "nonbehavioral"}
        ] if verdict == "changes-required" else []}))
        design = self.tmp / "design.json"
        design.write_text(json.dumps({"schemaVersion": 1, "status": "absent", "reason": "single owner repair"}))
        return self.ok("record", "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
                       "--input", str(envelope), "--preflight-file", str(draft), "--design-declaration", str(design))

    def test_unapproved_guidance_requests_the_missing_consult_inputs(self) -> None:
        self.begin()
        marker = "PREFLIGHT_CONSULT_NOT_GUIDED"
        identity = resolve_repo_identity(self.repo)
        for status in ("pending", "unavailable", "completed", "changes-required", "approved"):
            state = {**self.state(), "advisorPreflight": {"status": status}}
            operation = next_operation(identity, state)
            args = shlex.split(operation["command"])
            self.assertIn("preflight-advice", args, marker)
            self.assertEqual(args[args.index("--cwd") + 1], str(self.repo), marker)
            self.assertEqual(args[args.index("--slug") + 1], "ceremony", marker)
            self.assertNotIn("--reconsult", args, marker)
            for required in ("--preflight-file", "--design-file", "--design-absent", "stdin"):
                self.assertIn(required, operation["input"], marker)
        self.assertIn("preflight-advice", self.ok_raw("summary"), marker)
        self.assertIn('"authoritativeContract"', self.ok_raw("summary"), marker)

    def test_changes_required_guidance_resumes_with_the_recorded_design(self) -> None:
        self.begin()
        receipt = self.advice("changes-required")
        marker = "PREFLIGHT_RECONSULT_NOT_GUIDED"
        identity, state = resolve_repo_identity(self.repo), self.state()
        for status in ("changes-required", "unavailable", "approved"):
            operation = next_operation(identity, {**state, "advisorPreflight": {
                **state["advisorPreflight"], "status": status}})
            args = shlex.split(operation["command"])
            self.assertIn("--reconsult", args, marker)
            self.assertIn("preflight-advice", args, marker)
            self.assertEqual(args[args.index("--design-absent") + 1], "single owner repair", marker)
            self.assertIn("--preflight-file", operation["input"], marker)
        self.assertIn("--reconsult", receipt["next"]["command"], marker)
        self.assertNotIn('"authoritativeContract"', receipt["next"]["input"], marker)
        self.assertIn(receipt["next"]["command"], self.ok_raw("summary"), marker)

    def test_approved_command_records_its_retained_draft_from_scratch(self) -> None:
        wid = self.begin()
        receipt = self.advice()
        marker = "APPROVED_DRAFT_NOT_REUSED"
        args = shlex.split(receipt["next"]["command"])
        self.assertNotIn("--input", args, marker)
        self.assertIn(wid, args, marker)
        before = self.rows(), self.state()
        checked = subprocess.run([*args, "--check"], cwd=self.tmp, env=self.env, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, marker + checked.stderr)
        self.assertEqual((self.rows(), self.state()), before, marker)
        approved = evidence_document(resolve_repo_identity(self.repo), self.state()["advisorPreflight"]["intakeEvidence"])
        (self.tmp / "draft.json").unlink()
        recorded = subprocess.run(args, cwd=self.tmp, env=self.env, capture_output=True, text=True)
        self.assertEqual(recorded.returncode, 0, marker + recorded.stderr)
        result = json.loads(recorded.stdout)
        self.assertEqual(result["nextAction"], "tdd", marker)
        stored = self.ok("evidence", "--full", "--evidence-id", result["evidenceId"])
        self.assertEqual(stored["document"]["document"], approved["preflightDraft"], marker)

    def test_the_second_consult_is_the_advisors_own_approved_draft(self) -> None:
        marker = "PREFLIGHT_ROUNDS_UNBOUNDED"
        self.begin()
        self.advice("changes-required")
        record = ("record", "advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--input",
                  str(self.tmp / "advice.json"), "--preflight-file", str(self.tmp / "draft.json"),
                  "--design-declaration", str(self.tmp / "design.json"))
        refused = self.cli(*record)
        self.assertEqual(refused.returncode, 2, marker + ": a third preflight round was opened: " + refused.stdout)
        unedited = (self.tmp / "draft.json").read_text()  # the lead's own file, which the advisor does not edit
        edited = {"authoritativeContract": "the advisor's corrected contract", "behaviorMap": [item("BM_ONE"), item("BM_TWO")]}
        answer = {"schemaVersion": 1, "verdict": "approved",
                  "findings": [{"id": "SPEC-1", "claim": "BM_TWO was missing; added to the draft", "material": False}]}
        (self.tmp / "advice.json").write_text(json.dumps({**answer, "preflightDraft": edited}))
        self.assertEqual(self.cli(*record).returncode, 2, "FULL_REWRITE_ACCEPTED: the advisor replaced the draft wholesale")
        (self.tmp / "draft.json").write_text(json.dumps(edited))  # the advisor's in-place edit of the recorded copy
        (self.tmp / "advice.json").write_text(json.dumps(answer))
        receipt = self.ok(*record)
        misled = self.cli("record", "preflight", "--input", "-", input=unedited)
        guidance = json.loads(misled.stdout)["next"]["command"]
        self.assertEqual((misled.returncode, "ask-codex-advisor" in guidance, shlex.split(guidance)[-2:]),
                         (2, False, shlex.split(receipt["next"]["command"])[-2:]),
                         "PREFLIGHT_RECORD_MISGUIDED: after the last round the refusal must record the approved draft: " + guidance)
        recorded = subprocess.run(shlex.split(guidance), cwd=self.tmp, env=self.env, capture_output=True, text=True)
        self.assertEqual(recorded.returncode, 0, marker + recorded.stderr)
        stored = self.ok("evidence", "--full", "--evidence-id", json.loads(recorded.stdout)["evidenceId"])["document"]["document"]
        self.assertEqual((stored["authoritativeContract"], [entry["id"] for entry in stored["behaviorMap"]]),
                         ("the advisor's corrected contract", ["BM_ONE", "BM_TWO"]), marker)

    def test_explicit_draft_binding_and_record_atomicity(self) -> None:
        self.begin()
        marker = "PREFLIGHT_BINDING_CHANGED"
        before = self.rows(), self.state()
        self.assertEqual(self.cli("record", "preflight").returncode, 2, marker)
        self.assertEqual((self.rows(), self.state()), before, marker)
        self.advice()
        draft = json.loads((self.tmp / "draft.json").read_text())
        before = self.rows(), self.state()
        for document, extra in (({**draft, "authoritativeContract": "changed"}, []),
                                (draft, ["--input", ""]),
                                (draft, ["--workflow-id", "foreign-pass"])):
            result = self.cli("record", "preflight", "--input", "-", *extra, input=json.dumps(document))
            self.assertEqual(result.returncode, 2, marker + result.stderr)
            self.assertEqual((self.rows(), self.state()), before, marker)
        self.ok("record", "preflight", "--check", "--input", "-", input=json.dumps(draft, sort_keys=True, indent=4))
        self.assertEqual((self.rows(), self.state()), before, marker)
        self.ok("record", "preflight", "--input", "-", input=json.dumps(draft, sort_keys=True))
        before = self.rows(), self.state()
        self.assertEqual(self.cli("record", "preflight", "--input", "-", input=json.dumps(draft)).returncode, 2, marker)
        self.assertEqual((self.rows(), self.state()), before, marker)

    def test_advisor_revision_between_read_and_commit_rejects_the_old_draft(self) -> None:
        wid = self.begin()
        self.advice()
        identity = resolve_repo_identity(self.repo)
        old = evidence_document(identity, self.state()["advisorPreflight"]["intakeEvidence"])["preflightDraft"]
        self.advice(contract="revised continuation repair")
        before = self.rows(), self.state()
        marker = "STALE_APPROVAL_RECORDED"
        with self.assertRaisesRegex(WorkflowError, "approval bound to this exact draft", msg=marker):
            commit_evidence_phase(identity, "ceremony", wid, "preflight", {"document": old})
        self.assertEqual((self.rows(), self.state()), before, marker)
        self.ok("record", "preflight", "--input", str(self.tmp / "draft.json"))
        stored = evidence_document(identity, self.state()["preflightLatestEvidence"])
        self.assertEqual(stored["document"]["authoritativeContract"], "revised continuation repair", marker)

    def test_refusal_guides_reconsultation_of_the_current_draft(self) -> None:
        self.begin()
        marker = "PREFLIGHT_RECOVERY_MISSING"
        for approved in (False, True):
            if approved:
                self.advice()
            draft = self.tmp / "current draft.json"
            draft.write_text(json.dumps({"authoritativeContract": "current revised contract",
                                         "behaviorMap": [item("BM_ONE")]}))
            before = self.rows(), self.state()
            result = self.cli("record", "preflight", "--input", str(draft))
            self.assertEqual(result.returncode, 2, marker)
            self.assertEqual((self.rows(), self.state()), before, marker)
            self.assertTrue(result.stdout.strip(), marker)
            recovery = json.loads(result.stdout)
            self.assertIn("approval", recovery["error"], marker)
            args = shlex.split(recovery["next"]["command"])
            self.assertIn("preflight-advice", args, marker)
            self.assertEqual("--reconsult" in args, approved, marker)
            self.assertEqual(args[args.index("--preflight-file") + 1], str(draft), marker)
        self.advice(contract="current revised contract")
        self.ok("record", "preflight")
        stored = evidence_document(resolve_repo_identity(self.repo), self.state()["preflightLatestEvidence"])
        self.assertEqual(stored["document"]["authoritativeContract"], "current revised contract", marker)

    def test_other_continuations_keep_their_bound_operations(self) -> None:
        self.begin()
        self.advice()
        identity, state = resolve_repo_identity(self.repo), self.state()
        marker = "CONTINUATION_CHANGED"
        for action, expected in (("tdd", " tdd "), ("verification", "--kind quality-gate"),
                                 ("code-review", "record review"), ("repo-context-forge", "--revalidate"),
                                 ("complete-workflow", " complete ")):
            command = next_operation(identity, {**state, "nextAction": action})["command"]
            self.assertIn(expected, command, marker)
            self.assertIn(str(self.repo), command, marker)
        for action in ("final-review", "re-consult-final-review", "appeal-final-review"):
            command = next_operation(identity, {**state, "nextAction": action})["command"]
            self.assertIn("--phase final-review", command, marker)
            self.assertNotIn("--design-absent", command, marker)
            self.assertNotIn("--reconsult", command, marker)
        self.assertIsNone(next_operation(identity, {**state, "phase": "complete"})["command"], marker)
        observed = {"kind": "observed", "valid": True, "evidenceId": "observed-proof", "runIndex": 0}
        self.assertIn("--from-evidence observed-proof:0", next_operation(identity, state, observed)["command"], marker)
        self.assertIsNone(next_operation(identity, state, {**observed, "valid": False})["command"], marker)


class AdvisorBounded(Ceremony):
    def test_consult_returns_a_bounded_digest_and_records_the_whole_envelope(self) -> None:
        marker = "ADVISOR_OUTPUT_UNBOUNDED"
        self.begin()
        result, _ = self.wrapper("big")
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertLessEqual(len(result.stdout.encode()), 2048, f"{marker}: stdout {len(result.stdout)} bytes")
        self.assertLessEqual(len(result.stderr.encode()), 8192, f"{marker}: stderr {len(result.stderr)} bytes")
        self.assertIn("SPEC-20", result.stdout, marker)
        intake = evidence_document(resolve_repo_identity(self.repo), str(self.state()["advisorPreflight"]["intakeEvidence"]))
        self.assertEqual(len(intake["findings"]), 20, marker)
        self.assertEqual(intake["findings"][0]["claim"][:400], "c" * 400, marker)
        failed = self.make_repo("failing")
        self.repo = failed
        self.begin(failed)
        result, _ = self.wrapper("fail")
        self.assertNotEqual(result.returncode, 0, marker)
        self.assertLessEqual(len(result.stderr.encode()), 8192, f"{marker}: failing provider stderr {len(result.stderr)} bytes")
        self.repo = self.make_repo("garbage")
        self.begin()
        result, _ = self.wrapper("garbage")
        self.assertNotEqual(result.returncode, 0, marker)
        self.assertIn("GARBAGE_ANSWER " + "g" * 3000, result.stdout, f"{marker}: an unrecordable answer must still reach the lead")


class AdvisorDiffBounded(Ceremony):
    def test_deleted_files_are_headers_and_test_hunks_keep_ordinary_context(self) -> None:
        marker = "ADVISOR_DIFF_UNBOUNDED"
        test_module = ("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_far(self):  # ENCLOSING-DEF\n"
                       + "".join(f"        pad_{i} = {i}\n" for i in range(40)) + "        self.assertTrue(True)\n")
        (self.repo / "gone.py").write_text("".join(f"line_{i} = {i}  # DELETED-BODY\n" for i in range(50)), encoding="utf-8")
        (self.repo / "test_far.py").write_text(test_module, encoding="utf-8")
        (self.repo / "config").write_text("old = 1\n", encoding="utf-8")
        (self.repo / "evil\n+FORGED").write_text("x\n", encoding="utf-8")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-q", "-m", "fixtures")
        self.begin()
        (self.repo / "gone.py").unlink()
        (self.repo / "test_far.py").write_text(test_module + "        self.assertFalse(False)  # ADDED\n", encoding="utf-8")
        (self.repo / "config").unlink()
        (self.repo / "evil\n+FORGED").unlink()
        (self.repo / "config").mkdir()
        (self.repo / "config" / "default.yaml").write_text("replaced: 2\n", encoding="utf-8")
        record_context_forge(self.repo, self.tmp)
        diff = checkpoint_channels(self.repo, self.env, "preflight-advice")["diff"]
        self.assertIn("diff --git a/gone.py b/gone.py\ndeleted file: 50 lines\n", diff, f"{marker}: {diff[:600]}")
        self.assertNotIn("DELETED-BODY", diff, f"{marker}: a deleted file's body was sent")
        self.assertIn("+        self.assertFalse(False)  # ADDED", diff, marker)
        self.assertNotIn("ENCLOSING-DEF", diff, f"{marker}: a test hunk carried its whole definition")
        self.assertIn("+replaced: 2", diff, f"REPLACEMENT_ADDITION_HIDDEN: {diff[-600:]}")
        self.assertIn('diff --git a/"evil\\n+FORGED" b/"evil\\n+FORGED"\ndeleted file: 1 lines\n', diff, "DELETED_PATH_FORGES_DIFF")

    def test_a_resumed_final_is_sent_only_the_change_since_its_commit_verdict(self) -> None:
        marker = "RESUMED_BASE_NOT_APPROVED"
        (self.repo / "app.py").write_text("value = 1\nother = 1\nJUDGED = 1\n", encoding="utf-8")
        wid, identity = self.begin(), resolve_repo_identity(self.repo)

        def final(mode: str, change: str) -> str:
            (self.repo / "test_app.py").write_text(TEST_APP + change, encoding="utf-8")
            invalidate_after_edit(identity, "test_app.py")
            record_context_forge(self.repo, self.tmp)
            self.ok("verify", "--", sys.executable, "-c", "pass")
            self.ok("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
            set_phase(identity, "code-review", "passed", findings="none")
            result, prompt = self.wrapper(mode, phase="final-review")
            self.assertEqual(result.returncode, 0, result.stderr[-600:])
            self.assertIn("--- original request: the completeness oracle", prompt, f"{marker}: a channel lost its heading")
            return "".join(line for line in prompt.splitlines(keepends=True) if line.startswith("diff> "))

        self.assertEqual(self.wrapper("approved")[0].returncode, 0, marker)  # a preflight answer judges no commit
        advisor_disposition(identity, "ceremony", wid, "preflight", "none")
        self.record_preflight({"authoritativeContract": "c",
                "behaviorMap": []})
        set_phase(identity, "tdd", "not-required")
        self.assertIn("JUDGED = 1", final("fix-before-commit", "# FIRST\n"), f"{marker}: a preflight advanced the judged tree")
        self.ok("record", "advisor-disposition", "--finding", "F-1", "--report-only",
                "--reason", "The finding has no runnable behavior claim.")
        mismatch = final("context-mismatch", "# SECOND\n")
        self.assertNotIn("JUDGED = 1", mismatch, f"REJUDGED_WHOLE_PASS: {mismatch[:600]}")
        self.assertIn("diff> 1\t0\tapp.py\n", mismatch, f"RESUMED_PASS_UNLISTED: {mismatch[:600]}")
        retry = final("commit-ready", "# SECOND\n")
        self.assertIn("+# SECOND", retry, f"{marker}: a context-mismatch advanced the judged tree")
        self.assertNotIn("JUDGED = 1", retry, f"{marker}: the approved base was lost")
        for pointer in (self.tmp / "state" / "_advisor-sessions").glob("*.sid"):
            pointer.unlink()  # Resumed final diff anchors belong to the ledger.
        fresh = final("commit-ready", "# THIRD\n")
        self.assertNotIn("JUDGED = 1", fresh, f"APPROVAL_NOT_FROM_LEDGER: {fresh[:600]}")
        self.assertIn("+# THIRD", fresh, "APPROVAL_NOT_FROM_LEDGER")
        self.assertIn("+# FLAGGED", final("fix-before-commit", "# FLAGGED\n"), marker)
        self.ok("record", "advisor-disposition", "--finding", "F-1", "--rejected", "--reason", "r")
        appeal = self.wrapper("commit-ready", phase="final-review")[1]
        self.assertIn("diff> +# FLAGGED", appeal, f"APPEAL_WITHOUT_DISPUTED_CODE: {appeal[-600:]}")

    def test_an_oversized_prompt_is_refused_before_the_provider(self) -> None:
        marker = "OVERSIZED_PROMPT_SENT"
        (self.repo / "big.txt").write_text("x" * 1_100_000 + "\n", encoding="utf-8")
        self.begin()
        result, prompt = self.wrapper("big")
        self.assertEqual((result.returncode, prompt), (2, ""), f"{marker}: {result.stderr[-400:]}")
        self.assertRegex(result.stderr, r"prompt is [0-9]{7} characters; the codex transport accepts at most 1048576",
                         marker)


class ChannelManifest(Ceremony):
    def test_checkpoint_owns_ordered_channels_the_wrapper_frames(self) -> None:
        marker = "CHANNEL_MANIFEST_MISSING"
        (self.repo / "app.py").write_text("value = 3\nother = 1\n", encoding="utf-8")
        self.begin()
        channels_dir = self.tmp / "channels" / "missing"
        for _ in range(2):  # created when missing, reused when present
            point = self.cli("checkpoint", "--phase", "preflight-advice", "--channel-dir", str(channels_dir))
            self.assertEqual(point.returncode, 0, f"{marker}: {point.stderr[-300:]}")
        document = json.loads(point.stdout)
        channels = document.get("channels")
        self.assertIsInstance(channels, list, marker)
        self.assertEqual([c["name"] for c in channels], ["intent", "advisor-projection", "diff"], marker)
        for channel in channels:
            self.assertEqual(set(channel), {"name", "evidenceId", "bytes", "contentPath", "description"}, marker)
            self.assertEqual(Path(channel["contentPath"]).stat().st_size, channel["bytes"], marker)
        for inline in ("intent", "advisorProjection", "findingLedger", "lateRed", "governedDesign"):
            self.assertNotIn(inline, document, f"{marker}: {inline} still inline")
        self.assertEqual(channels[1]["evidenceId"], self.state()["repoContextForgeEvidence"], marker)
        result, prompt = self.wrapper("big")
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        positions = [prompt.find(f"{c['name']}> " + Path(c["contentPath"]).read_text(encoding="utf-8").splitlines()[0])
                     for c in channels]
        self.assertTrue(all(p >= 0 for p in positions) and positions == sorted(positions), f"{marker}: {positions}")

    def test_json_channels_are_compact(self) -> None:
        marker = "CHANNEL_JSON_INDENTED"
        self.begin()
        channels_dir = self.tmp / "channels"
        channels_dir.mkdir()
        point = json.loads(self.ok_raw("checkpoint", "--phase", "preflight-advice", "--channel-dir", str(channels_dir)))
        [projection] = [c for c in point["channels"] if c["name"] == "advisor-projection"]
        text = Path(projection["contentPath"]).read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")), marker)


class NoEventSnapshots(Ceremony):
    def snapshot_era_cli(self, commit: str = SNAPSHOT_ERA) -> Path:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", commit, "hooks/lib",
                                  "skills/repo-production-workflow/scripts/workflow.py"],
                                 capture_output=True, check=True).stdout
        target = self.tmp / f"cli-{commit[:7]}"
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(target, filter="data")
        return target / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"

    def columns(self, table: str) -> list[str]:
        with sqlite3.connect(database_path(resolve_repo_identity(self.repo))) as connection:
            return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]

    def test_events_carry_no_state_and_snapshot_ledgers_migrate_atomically(self) -> None:
        marker = "EVENT_SNAPSHOT_PRESENT"
        wid = self.begin()
        self.assertNotIn("state_json", self.columns("workflow_events"), marker)
        self.assertEqual(self.ok("status")["workflowId"], wid, marker)
        self.repo = self.make_repo("snapshot-era-repo")
        old = self.snapshot_era_cli()
        begun = subprocess.run([sys.executable, str(old), "begin", "--slug", "old", "--intent", "n"],
                               cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        old_wid = json.loads(begun.stdout)["workflowId"]
        path = database_path(resolve_repo_identity(self.repo))
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TRIGGER interrupt BEFORE UPDATE ON workflows "
                               "BEGIN SELECT RAISE(ABORT, 'interrupted migration'); END")
        interrupted = self.cli("status")
        self.assertNotEqual(interrupted.returncode, 0, marker)
        self.assertIn("state_json", self.columns("workflow_events"), f"{marker}: interrupted migration committed")
        survivor = subprocess.run([sys.executable, str(old), "status"], cwd=self.repo, env=self.env,
                                  capture_output=True, text=True)
        self.assertEqual(json.loads(survivor.stdout)["workflowId"], old_wid, marker + survivor.stderr)
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER interrupt")
        self.assertEqual(self.ok("status")["workflowId"], old_wid, marker)
        self.assertNotIn("state_json", self.columns("workflow_events"), marker)
        self.assertEqual(self.ok("status")["slug"], "old", marker)


    def test_prune_reports_an_unmigrated_snapshot_ledger(self) -> None:
        marker = "SNAPSHOT_LEDGER_UNPRUNABLE"
        old = self.snapshot_era_cli()
        for index in range(6):
            begun = subprocess.run([sys.executable, str(old), "begin", "--slug", f"old-{index}", "--intent", "n"],
                                   cwd=self.repo, env=self.env, capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)
        report = self.ok("prune")
        [slot] = [slot for slot in report["slots"] if slot["slot"] == resolve_repo_identity(self.repo).key]
        self.assertEqual((slot["store"], [w["decision"] for w in slot["workflows"]].count("removable")), ("sqlite", 1),
                         f"{marker}: {slot}")
        self.assertIn("state_json", self.columns("workflow_events"), f"{marker}: a report migrated the ledger")


    def test_an_older_reader_refuses_the_migrated_ledger(self) -> None:
        marker = "OLDER_READER_MISREADS_LEDGER"
        pre96, format_two = self.snapshot_era_cli(), self.snapshot_era_cli(FORMAT_TWO)
        self.begin()
        legs = [(self.repo, pre96), (self.repo, format_two)]  # each reader: a fresh ledger and the one it wrote
        for name, old in (("migrated", pre96), ("format-two", format_two)):
            legs.append((self.make_repo(name), old))
            begun = subprocess.run([sys.executable, str(old), "begin", "--slug", "old", "--intent", "n"],
                                   cwd=legs[-1][0], env=self.env, capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)
        for self.repo, old in legs:
            evidence_id = self.ok("verify", "--", sys.executable, "-c", "pass")["evidenceId"]
            stored = self.ok("evidence", "--full", "--evidence-id", evidence_id)
            for args in (("status",), ("history",), ("summary",), ("evidence", "--evidence-id", evidence_id),
                         ("begin", "--slug", "again", "--intent", "n")):
                result = subprocess.run([sys.executable, str(old), *args], cwd=self.repo, env=self.env,
                                        capture_output=True, text=True)
                where = f"{marker}: {old.parents[3].name} reader, {self.repo.name} {args[0]}"
                self.assertEqual(result.returncode, 2, f"{where} exit {result.returncode}")
                self.assertIn(FORMAT_REFUSAL, result.stderr, f"{where}: {result.stderr[-300:]}")
                self.assertNotIn("parts", result.stdout, f"{where} returned stored part references")
            self.assertEqual(self.ok("evidence", "--full", "--evidence-id", evidence_id), stored, marker)

    def test_a_format_two_ledger_reads_back_its_text_parts(self) -> None:
        marker = "TEXT_PART_UNREAD"
        old = self.snapshot_era_cli(FORMAT_TWO)

        def run(*args: str) -> dict[str, object]:
            result = subprocess.run([sys.executable, str(old), *args], cwd=self.repo, env=self.env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{args[0]}: {result.stderr[-300:]}")
            return json.loads(result.stdout.strip().splitlines()[-1])
        run("begin", "--slug", "old", "--intent", "n")
        evidence_id = str(run("verify", "--", sys.executable, "-c", "print('TEXT_PART')")["evidenceId"])
        stored = run("evidence", "--full", "--evidence-id", evidence_id)
        with sqlite3.connect(database_path(resolve_repo_identity(self.repo))) as connection:
            kinds = {row[0] for row in connection.execute("SELECT typeof(part_json) FROM evidence_parts")}
        self.assertEqual(kinds, {"text"}, "the format-2 writer stored no text part")
        self.assertIn("TEXT_PART", json.dumps(stored), marker)
        self.assertEqual(self.ok("evidence", "--full", "--evidence-id", evidence_id), stored, marker)

    def test_racing_first_opens_migrate_once(self) -> None:
        marker = "MIGRATION_RACE_LOST"
        old = self.snapshot_era_cli()
        begun = subprocess.run([sys.executable, str(old), "begin", "--slug", "old", "--intent", "n"],
                               cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        path = database_path(resolve_repo_identity(self.repo))
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("CREATE TRIGGER interrupt BEFORE UPDATE ON workflows "
                       "BEGIN SELECT RAISE(ABORT, 'interrupted migration'); END")
        self.assertNotEqual(self.cli("status").returncode, 0, marker)  # leaves the new tables, unmigrated
        holder.execute("DROP TRIGGER interrupt")
        holder.execute("BEGIN IMMEDIATE")
        racers = [subprocess.Popen([sys.executable, str(WORKFLOW), "status"], cwd=self.repo, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        time.sleep(1)
        holder.execute("COMMIT")
        for racer in racers:
            out, err = racer.communicate(timeout=60)
            self.assertEqual(racer.returncode, 0, f"{marker}: {err[-300:]}")
            self.assertEqual(json.loads(out)["slug"], "old", marker)
        self.assertEqual(holder.execute("SELECT value FROM ledger_metadata WHERE key = 'format'").fetchone(),
                         (LEDGER_FORMAT,), marker)
        holder.close()
        refused = subprocess.run([sys.executable, str(old), "status"], cwd=self.repo, env=self.env,
                                 capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2, f"{marker}: {refused.stderr[-300:]}")

    def test_a_migration_between_prelock_reads_is_survived(self) -> None:
        marker = "PRELOCK_READ_RACE"
        old = self.snapshot_era_cli()
        begun = subprocess.run([sys.executable, str(old), "begin", "--slug", "old", "--intent", "n"],
                               cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        identity = resolve_repo_identity(self.repo)
        connection = _open_connection(database_path(identity), read_only=False)
        _schema(connection)
        statements: list[str] = []
        raced: list[int] = []

        def racer(sql: str) -> None:  # another opener migrates before this opener's second statement runs
            statements.append(sql)
            if len(statements) == 2:  # an exception here would be swallowed by sqlite: record the result
                raced.append(subprocess.run([sys.executable, str(WORKFLOW), "status"], cwd=self.repo, env=self.env,
                                            capture_output=True).returncode)
        connection.set_trace_callback(racer)
        try:
            _ensure_authority(connection, identity)
        except sqlite3.Error as exc:
            self.fail(f"{marker}: {exc}")
        finally:
            connection.close()
        self.assertEqual(raced, [0], f"{marker}: the racing migration did not run")
        self.assertEqual(self.ok("status")["slug"], "old", marker)


class EvidenceParts(Ceremony):
    def verify(self, text: str, repo: Path | None = None) -> str:
        result = self.cli("verify", "--", sys.executable, "-c", f"print({text!r})", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return str(json.loads(result.stdout.strip().splitlines()[-1])["evidenceId"])

    def rows_containing(self, needle: str) -> int:
        path = database_path(resolve_repo_identity(self.repo))
        count = 0
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for row in connection.execute(f"SELECT * FROM {table}"):
                    count += any(needle in (zlib.decompress(value).decode() if isinstance(value, bytes) else value)
                                 for value in row if isinstance(value, (str, bytes)))
        return count

    def test_the_gate_report_is_stored_compressed(self) -> None:
        marker = "REPORT_STORED_UNCOMPRESSED"
        self.begin()
        for index in range(11):
            (self.repo / f"escape{index:02}.py").write_text(f"X = {index}  # TO" + "DO later\n", encoding="utf-8")
        self.cli("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
        with sqlite3.connect(f"file:{database_path(resolve_repo_identity(self.repo))}?mode=ro", uri=True) as connection:
            plain = connection.execute("SELECT COUNT(*) FROM evidence WHERE document_json LIKE '%escape10.py:1%'").fetchone()[0]
        self.assertEqual((plain, self.rows_containing("escape10.py:1") > 0), (0, True), marker)

    def test_items_and_runs_are_stored_once_and_history_keeps_its_meaning(self) -> None:
        marker = "EVIDENCE_NOT_CONTENT_ADDRESSED"
        self.begin()
        identity = resolve_repo_identity(self.repo)
        first = self.verify("PART_MARKER_ONE")
        captured = {first: evidence_document(identity, first)}
        second = self.verify("PART_MARKER_TWO")
        captured[second] = evidence_document(identity, second)
        self.assertEqual(self.rows_containing("PART_MARKER_ONE"), 1, f"{marker}: a run row is stored per document")
        document = {"authoritativeContract": "fixture", "behaviorMap": [
            item("BM_KEEP", behavior="unchanged item"), item("BM_MAP_ITEM_ONE")]}
        evidence_id = self.record_preflight(document)["evidenceId"]
        captured[evidence_id] = evidence_document(identity, evidence_id)
        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [item("BM_MAP_ITEM_TWO", kind="preservation")]}))
        evidence_id = self.state()["tddEvidence"]
        captured[evidence_id] = evidence_document(identity, evidence_id)
        self.assertEqual(self.rows_containing("unchanged item"), 1, f"{marker}: a map item is stored per document")
        threads = [threading.Thread(target=self.verify, args=(f"CONCURRENT_{n}",)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for evidence_id, document in captured.items():
            self.assertEqual(evidence_document(identity, evidence_id), document, f"{marker}: {evidence_id} changed")
        runs = evidence_document(identity, str(self.state()["verificationLatestEvidence"]))["runs"]
        self.assertEqual(len(runs), 4, marker)
        for n in range(6):
            self.begin(slug=f"later-{n}")
            self.verify(f"LATER_{n}")
        latest = str(self.state()["verificationLatestEvidence"])
        kept = evidence_document(identity, latest)
        pruned = subprocess.run([sys.executable, str(WORKFLOW), "prune", "--apply"], env=self.env, capture_output=True, text=True)
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertEqual(evidence_document(identity, latest), kept, f"{marker}: retention broke a live part")


class StepRegistry(Ceremony):
    """The pass-start predicates, captured on the snapshot-era implementation."""

    def snapshot(self) -> dict[str, object]:
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        ready, missing = ready_for_edit(identity, "app.py")
        try:
            complete(identity)
            completion = "complete"
        except Exception as exc:  # the refused requirements are the observation
            completion = str(exc)
        head, _, names = completion.partition(": ")
        completion = [head, *sorted(names.split(", "))] if names else [completion]
        state = read_workflow(identity) or state
        return {"next": state.get("nextAction"), "blockers": review_blockers(identity, state),
                "edit": [ready, missing], "complete": completion}

    def test_sequence_readiness_blockers_and_completion_are_preserved(self) -> None:
        marker = "STEP_PREDICATE_DRIFT"
        observed = []
        wid = self.cli("begin", "--slug", "ceremony", "--intent", "matrix")
        wid = str(json.loads(wid.stdout)["workflowId"])
        identity = resolve_repo_identity(self.repo)
        observed.append(self.snapshot())
        record_context_forge(self.repo, self.tmp)
        observed.append(self.snapshot())
        record_advisor_result(identity, "ceremony", wid, "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, "ceremony", wid, "preflight", "none")
        observed.append(self.snapshot())
        self.record_preflight({"authoritativeContract": "matrix", "behaviorMap": []})
        observed.append(self.snapshot())
        set_phase(identity, "tdd", "not-required")
        observed.append(self.snapshot())
        self.cli("verify", "--slug", "ceremony", "--", sys.executable, "-c", "raise SystemExit(1)")
        observed.append(self.snapshot())
        self.ok("verify", "--slug", "ceremony", "--", sys.executable, "-c", "raise SystemExit(0)")
        observed.append(self.snapshot())
        self.cli("verify", "--slug", "ceremony", "--kind", "quality-gate", "--base-ref", "HEAD")
        observed.append(self.snapshot())
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        invalidate_after_edit(identity, "app.py")
        observed.append(self.snapshot())
        self.cli("verify", "--slug", "ceremony", "--", sys.executable, "-c", "raise SystemExit(0)")
        self.cli("verify", "--slug", "ceremony", "--kind", "quality-gate", "--base-ref", "HEAD")
        observed.append(self.snapshot())
        set_phase(identity, "code-review", "passed", findings="none")
        observed.append(self.snapshot())
        envelope = self.tmp / "final.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "verdict": "fix-before-commit", "findings": [
            {"id": "F-1", "claim": "matrix finding", "material": True, "kind": "nonbehavioral"}]}), encoding="utf-8")
        intake, verdict = advisor_envelope(str(envelope), slug="ceremony", workflow_id=wid, stage="final", producer="codex-advisor")
        record_advisor_result(identity, "ceremony", wid, "final", "codex-advisor", verdict, intake=intake)
        observed.append(self.snapshot())
        if os.environ.get("CEREMONY_CAPTURE_MATRIX"):
            print(json.dumps(observed))
        expected = [{**row, "complete": [row["complete"].partition(": ")[0],
                                          *sorted(row["complete"].partition(": ")[2].split(", "))]}
                    for row in EXPECTED_MATRIX]
        self.assertEqual(observed, expected, f"{marker}: {json.dumps(observed)}")


EXPECTED_MATRIX: list[dict[str, object]] = json.loads(r'''[
 {"next":"repo-context-forge","blockers":["repo-context-forge","preflight","tdd","verification"],"edit":[false,["repo-context-forge","production preflight"]],"complete":"workflow incomplete: repoContextForge, preflight, verification, tdd, codeReview, finalReview"},
 {"next":"preflight","blockers":["preflight","tdd","verification"],"edit":[false,["production preflight"]],"complete":"workflow incomplete: preflight, verification, tdd, codeReview, finalReview"},
 {"next":"preflight","blockers":["preflight","tdd","verification"],"edit":[false,["production preflight"]],"complete":"workflow incomplete: preflight, verification, tdd, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview"},
 {"next":"verification","blockers":["verification"],"edit":[true,[]],"complete":"workflow incomplete: verification, codeReview, finalReview, repoContextForge"},
 {"next":"code-review","blockers":[],"edit":[true,[]],"complete":"workflow incomplete: codeReview, finalReview"},
 {"next":"final-review","blockers":[],"edit":[true,[]],"complete":"workflow incomplete: finalReview"},
 {"next":"address-review-findings","blockers":[],"edit":[true,[]],"complete":"workflow incomplete: pending findings: final:F-1, finalReview"}
]''')


class RecordSeam(Ceremony):
    def test_record_check_rolls_back_and_each_kind_describes_itself(self) -> None:
        marker = "RECORD_CHECK_MISSING"
        self.begin()
        document = {"authoritativeContract": "fixture contract", "behaviorMap": [item("BM_ONE")]}
        approve_preflight(self.repo, document)
        before = self.rows()
        checked = self.cli("record", "preflight", "--check", "--input", "-", input=json.dumps(document))
        self.assertEqual(checked.returncode, 0, f"{marker}: {checked.stderr[-300:]}")
        self.assertEqual(json.loads(checked.stdout), {"status": "passed", "checked": True}, "CHECK_RECEIPT_PHANTOM_ID")
        self.assertEqual(self.rows(), before, f"{marker}: --check persisted")
        self.assertEqual(self.state()["preflight"], "pending", marker)
        for kind, expected in (("preflight", "authoritativeContract"), ("review", "claim"),
                               ("advisor-result", "verdict"), ("advisor-disposition", "--finding"),
                               ("tdd-map", "sourceRefs")):
            shape = self.cli("record", kind, "--help")
            self.assertEqual(shape.returncode, 0, f"{marker}: {kind} {shape.stderr}")
            self.assertIn(expected, shape.stdout, f"{marker}: {kind} help omits its shape")
        envelope = {"schemaVersion": 1, "verdict": "completed", "findings": []}
        self.ok("record", "advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--input", "-",
                input=json.dumps(envelope))
        self.assertEqual(self.state()["advisorPreflight"]["status"], "completed", marker)
        self.ok("record", "advisor-disposition", "--findings", "none")


class EveryViolation(Ceremony):
    """One refusal names every violation: for each agent-written document, every pair of independent
    single faults is refused naming each fault's own messages, and nothing is recorded."""

    def refusal(self, kind: str, document: object, *extra: str) -> str:
        refused = self.cli("record", kind, "--check", *extra, "--input", "-", input=json.dumps(document))
        self.assertIn(refused.returncode, (0, 2), f"VIOLATION_CRASHED: {kind}: {refused.stderr[-400:]}")
        return refused.stderr.removeprefix("error: ").strip() if refused.returncode else ""

    def pairs(self, kind: str, base: object, faults: dict[str, tuple[tuple, object]], *extra: str) -> list[str]:
        """`base` is a valid document; a refusal it still earns is a state check, which runs after validation."""
        marker = "VIOLATION_HIDDEN"
        state_refusal = self.refusal(kind, base, *extra)
        singles = {}
        for name, fault in faults.items():
            document = copy.deepcopy(base)
            _put(document, *fault)
            singles[name] = self.refusal(kind, document, *extra)
            self.assertNotIn(singles[name], ("", state_refusal), f"{marker}: {kind} {name} passed validation")
        lost = []
        for first, second in itertools.combinations(faults, 2):
            (one, _), (two, _) = faults[first], faults[second]
            if one[:len(two)] == two[:len(one)]:  # the same field: not independent
                continue
            document = copy.deepcopy(base)
            _put(document, *faults[first])
            _put(document, *faults[second])
            combined = _unlabelled(self.refusal(kind, document, *extra))
            lost += [f"{first}+{second} lost {name}" for name in (first, second) if any(
                _unlabelled(part) not in combined
                for part in re.split(r"; |, |: ", singles[name].split(" expected shape: ")[0]))]
        return [f"{kind} {pair}" for pair in lost]

    def test_independent_violations_are_named_together_in_every_document(self) -> None:
        self.begin()
        self.ok("record", "advisor-result", "--stage", "preflight", "--input", "-", input=json.dumps({"schemaVersion": 1,
                "verdict": "completed", "findings": [{"id": "S-1", "claim": "c", "material": True, "kind": "nonbehavioral"}]}))
        state = self.ok("status", "--fields", "workflowId,activeCandidateTree")
        context = {"workflowId": state["workflowId"], "candidateTree": state["activeCandidateTree"]}
        design = {"type": "design", "evidenceId": "e", "id": "D"}
        lost = self.pairs("preflight", {"authoritativeContract": "c", "behaviorMap": [
            item("BM_ONE", refs=[design]), item("BM_TWO"), item("BM_THREE")]}, {
            "contract": (("authoritativeContract",), _DROP), "extra": (("extra",), 1),
            "kind": (("behaviorMap", 0, "kind"), []), "basis": (("behaviorMap", 0, "basis"), _DROP),
            "behavior": (("behaviorMap", 0, "behavior"), ""),
            "status": (("behaviorMap", 0, "status"), "sideways"), "ref": (("behaviorMap", 0, "sourceRefs", 0), 5),
            "field": (("behaviorMap", 0, "bogus"), 1), "id": (("behaviorMap", 2, "id"), "bad id"),
            "duplicate": (("behaviorMap", 1, "id"), "BM_ONE"), "seam": (("behaviorMap", 1, "seam"), "")})
        finding = {"id": "R-1", "claim": "c", "material": True, "kind": "behavioral"}
        succession = {"context": dict(context), "findings": [{"evidenceId": "e", "id": "R-1"}], "evidence": "e",
                      "previousOwner": {"implementerContextId": "a", "reviewerContextId": "b"}}
        lost += self.pairs("review", {"findings": [dict(finding), {**finding, "id": "R-2"}],
                                      "implementationContextId": "impl", "repairSuccession": succession}, {
            "extra": (("extra",), 1), "implementer": (("implementationContextId",), ""),
            "no-implementer": (("implementationContextId",), _DROP),
            "succession-evidence": (("repairSuccession", "evidence"), ""),
            "succession-owner": (("repairSuccession", "previousOwner", "reviewerContextId"), ""),
            "succession-refs": (("repairSuccession", "findings", 0, "id"), ""),
            "succession-tree": (("repairSuccession", "context", "candidateTree"), "bad"),
            "succession-context-extra": (("repairSuccession", "context", "extra"), 1),
            "claim": (("findings", 0, "claim"), ""),
            "material": (("findings", 0, "material"), "yes"), "prior": (("findings", 0, "priorFinding"), 5),
            "duplicate": (("findings", 1, "id"), "R-1"), "second": (("findings", 1, "claim"), 7)})
        self.record_preflight({"authoritativeContract": "c", "behaviorMap": [
            item("BM_ONE"), item("BM_KEEP"),
            item("BM_HOLD")]})
        stored = self.ok("evidence", "--full", "--evidence-id", str(self.state()["preflightEvidence"]))["document"]
        self.assertEqual(stored["document"]["behaviorMap"][0]["basis"], "fixture contract")
        lost += self.pairs("tdd-map", {"items": [item("BM_NEW", kind="preservation"), item("BM_KEEP")]}, {
            "item": (("items", 0, "behavior"), ""), "item-basis": (("items", 0, "basis"), _DROP),
            "item-id": (("items", 1, "id"), "BM_NEW"),
            "unknown": (("items", 0, "bogus"), 1),
            "refs": (("items", 1, "sourceRefs"), 5)})
        self.assertEqual(lost, [], "VIOLATION_HIDDEN: " + "; ".join(lost))


_DROP = object()
_IDENTIFIERS = re.compile(r"\s*\((?:BM_[A-Z0-9_-]+)\)|\b(?:BM_[A-Z0-9_-]+|[RS]-\d+|None)\b")


def _unlabelled(text: str) -> str:
    """A refusal without its item labels: an invalid id changes the label, not the violation."""
    return " ".join(_IDENTIFIERS.sub("", text).split())


def _put(document: dict, path: tuple, value: object) -> None:
    for key in path[:-1]:
        document = document[key]
    if value is _DROP:
        document.pop(path[-1])
    else:
        document[path[-1]] = value


class RearmOpenOnly(Ceremony):
    def test_the_rearm_names_open_work_and_omits_settled_items(self) -> None:
        marker = "REARM_REPEATS_SETTLED_WORK"
        self.begin()
        self.record_preflight({"authoritativeContract": "c", "behaviorMap": [
            item("BM_OPEN_WORK")]})
        rearmed = subprocess.run([sys.executable, str(REARM)], env=self.env, capture_output=True, text=True,
                                 input=json.dumps({"cwd": str(self.repo), "source": "compact",
                                                   "hook_event_name": "SessionStart"}))
        context = json.loads(rearmed.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("BM_OPEN_WORK", context, marker)
        self.assertIn("tdd=pending", context, marker)
        self.cli("tdd", "--behavior-id", "BM_OPEN_WORK", "--", sys.executable, "-m", "unittest", "test_app")
        self.assertIn("tdd=in-progress", self.cli("summary").stdout, f"{marker}: step status misreported")
        for settled in ("BM_SETTLED_KEEP", "=ready", "repo-context-forge=", "preflight=passed"):
            self.assertNotIn(settled, context, f"{marker}: {settled!r} in {context}")
        self.assertIn("Missing state is pending, never success.", context, marker)

    def test_the_rearm_omits_settled_comparison(self) -> None:
        marker = "REARM_REPEATS_SETTLED_WORK"
        self.begin()
        self.record_preflight({"authoritativeContract": "c", "behaviorMap": [item("BM_LATE_SETTLED")]})
        (self.repo / "app.py").write_text("value = 1\nother = 1\n# changed before RED\n", encoding="utf-8")
        command = ("--", sys.executable, "-m", "unittest", "test_app")
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.ok("tdd", "--behavior-id", "BM_LATE_SETTLED", *command)
        rearmed = subprocess.run([sys.executable, str(REARM)], env=self.env, capture_output=True, text=True,
                                 input=json.dumps({"cwd": str(self.repo), "source": "compact",
                                                   "hook_event_name": "SessionStart"}))
        self.assertNotIn("BM_LATE_SETTLED", json.loads(rearmed.stdout)["hookSpecificOutput"]["additionalContext"], marker)


class AdvisoryDedup(Ceremony):
    def advise(self, hook: Path, session: str, **payload: object) -> str:
        result = subprocess.run([sys.executable, str(hook)], env=self.env, capture_output=True, text=True,
                                input=json.dumps({"session_id": session, "cwd": str(self.repo), **payload}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "") if result.stdout.strip() else ""

    def edit(self, hook: Path, session: str = "dedup-session") -> str:
        return self.advise(hook, session, tool_name="apply_patch", hook_event_name="PreToolUse",
                           tool_input={"command": f"*** Begin Patch\n*** Update File: {self.repo / 'app.py'}\n"})

    def test_identical_advisories_repeat_only_after_a_change_or_compaction(self) -> None:
        marker = "ADVISORY_REPEATED_OR_LOST"
        self.begin()
        first = self.edit(PRE_TOOL)
        self.assertIn("workflow intake", first, marker)
        self.assertEqual(self.edit(PRE_TOOL), "", f"{marker}: identical intake advice repeated")
        self.assertEqual(self.edit(PRE_TOOL, "other-session"), first, f"{marker}: another session lost it")
        self.record_preflight({"authoritativeContract": "c", "behaviorMap": [item("BM_ONE")]})
        self.cli("tdd", "--behavior-id", "BM_ONE", "--", sys.executable, "-m", "unittest", "test_app")
        changed = self.edit(PRE_TOOL)
        self.assertIn("Probe obligations", changed, f"{marker}: changed advice was suppressed")
        self.assertEqual(self.edit(PRE_TOOL), "", f"{marker}: identical obligations repeated")
        (self.repo / "app.py").write_text("value = undefined_name\nother = 1\n", encoding="utf-8")
        post = {"tool_name": "apply_patch", "hook_event_name": "PostToolUse",
                "tool_input": {"command": f"*** Begin Patch\n*** Update File: {self.repo / 'app.py'}\n"}}
        lint = self.advise(POST_TOOL, "dedup-session", **post)
        self.assertIn("undefined_name", lint, marker)
        self.assertNotIn("undefined_name", self.advise(POST_TOOL, "dedup-session", **post), f"{marker}: lint repeated")
        settings = json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn(REARM.name, json.dumps(settings.get("PostCompact")), f"{marker}: no PostCompact reset hook")
        self.assertEqual(self.advise(REARM, "dedup-session", hook_event_name="PostCompact", trigger="auto"), "", marker)
        self.assertEqual(self.edit(PRE_TOOL), changed, f"{marker}: compaction did not restore the advice")
        self.assertIn("undefined_name", self.advise(POST_TOOL, "dedup-session", **post), f"{marker}: lint lost")
        shutil.rmtree(self.tmp / "state" / "_advisories")
        (self.tmp / "state" / "_advisories").write_text("", encoding="utf-8")  # every record write now fails
        self.assertEqual([self.edit(PRE_TOOL), self.edit(PRE_TOOL)], [changed] * 2, "ADVISORY_LOST_ON_RECORD_FAILURE")

    def test_compaction_reset_survives_an_unwritable_record(self) -> None:
        (self.tmp / "state").mkdir()
        (self.tmp / "state" / "_advisories").write_text("", encoding="utf-8")
        self.advise(REARM, "dedup-session", hook_event_name="PostCompact", trigger="auto")  # asserts exit 0

    def test_the_edit_survives_a_closed_stdout_reader(self) -> None:
        # Lint feedback rides the hook's stdout; a broken reader loses it, never the edit.
        (self.repo / "app.py").write_text("value = undefined_name\nother = 1\n", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(POST_TOOL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=self.repo, env=self.env)
        proc.stdout.close()
        _, err = proc.communicate(input=json.dumps({"tool_input": {"file_path": str(self.repo / "app.py")},
                                                    "session_id": "closed-reader"}), timeout=120)
        self.assertEqual((proc.returncode, "BrokenPipeError" in err), (0, False), "EDIT_FAILS_ON_CLOSED_STDOUT: " + err[-200:])


class ObservedCapture(Ceremony):
    def hook(self, command: str, cwd: Path, session: str = "ceremony-lead") -> dict[str, object]:
        result = subprocess.run([sys.executable, str(PRE_TOOL)], env=self.env, capture_output=True, text=True,
                                input=json.dumps({"tool_name": "Bash", "session_id": session, "cwd": str(cwd),
                                                  "tool_input": {"command": command}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout.strip() else {}

    def test_lone_test_command_becomes_a_receipt_in_its_own_checkout(self) -> None:
        marker = "OBSERVED_RECEIPT_MISSING"
        command = f"{sys.executable} -m unittest -v test_app"
        session_root = self.make_repo("session-root")
        self.begin(session_root, slug="session")
        session_rows = self.rows(session_root)
        self.begin()
        self.record_preflight({"authoritativeContract": "c", "behaviorMap": [item("BM_ONE")]})
        spec = self.hook(command, session_root)
        self.assertEqual(spec.get("permissionDecision"), "allow", f"{marker}: {spec}")
        rewritten = str(spec["updatedInput"]["command"])
        verification = self.state()["verification"]
        observed = subprocess.run(["bash", "-c", rewritten], cwd=self.repo, env=self.env, capture_output=True, text=True)
        direct = subprocess.run(["bash", "-c", command], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(observed.returncode, direct.returncode, marker)
        self.assertIn("VALUE_NOT_TWO", observed.stdout + observed.stderr, marker)
        self.assertIn("workflow.py tdd --repo", observed.stderr, "OBSERVATION_BYPASSES_TDD")
        self.assertIn("--behavior-id ID (repeatable; recorded: BM_ONE) -- COMMAND", observed.stderr, "COMPARISON_INPUTS_HIDDEN")
        state = self.state()
        self.assertEqual(state["verification"], verification, f"{marker}: observation changed verification")
        runs = evidence_document(resolve_repo_identity(self.repo), str(state["verificationLatestEvidence"]))["runs"]
        self.assertEqual((runs[-1]["kind"], runs[-1]["command"]), ("observed", command), marker)
        self.assertTrue(runs[-1].get("treeManifestId"), marker)
        self.assertEqual(self.rows(session_root), session_rows, f"{marker}: receipt landed in the session root")
        codex_home = self.tmp / "codex"
        codex_home.mkdir()
        with sqlite3.connect(codex_home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT)")
            connection.executemany("INSERT INTO threads VALUES (?, ?)", [
                ("resumed-lead", "cli"), ("reviewer", '{"subagent":"review"}')])
        self.env["CODEX_HOME"] = str(codex_home)
        resumed = subprocess.run(["bash", "-c", rewritten], cwd=self.repo, capture_output=True, text=True,
                                 env={**self.env, "CODEX_THREAD_ID": "resumed-lead"})
        self.assertIn("VALUE_NOT_TWO", resumed.stdout + resumed.stderr, marker)
        self.assertIn("workflow observed receipt", resumed.stderr, "RESUMED_LEAD_RECEIPT_LOST")
        rows = self.rows()
        reviewer = subprocess.run(["bash", "-c", rewritten], cwd=self.repo, capture_output=True, text=True,
                                  env={**self.env, "CODEX_THREAD_ID": "reviewer"})
        self.assertIn("VALUE_NOT_TWO", reviewer.stdout + reviewer.stderr, f"{marker}: reviewer run refused")
        self.assertEqual(self.rows(), rows, f"{marker}: reviewer run recorded in the lead's ledger")
        with closing(sqlite3.connect(codex_home / "state_5.sqlite")) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            locked = subprocess.run(["bash", "-c", rewritten], cwd=self.repo, capture_output=True, text=True,
                                    env={**self.env, "CODEX_THREAD_ID": "resumed-lead"})
            self.assertEqual(locked.returncode, direct.returncode)
            self.assertIn("VALUE_NOT_TWO", locked.stdout + locked.stderr, "IDENTITY_LOOKUP_BLOCKED_COMMAND")
            self.assertEqual(self.rows(), rows, "UNAVAILABLE_IDENTITY_RECORDED_A_RECEIPT")
        self.assertNotIn("permissionDecision", self.hook(f"git status; {command}", self.repo),
                         f"{marker}: composed test command refused")
        reference = f"{state['verificationLatestEvidence']}:{runs[-1]['runIndex']}"
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        passing = subprocess.run(["bash", "-c", rewritten], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn("workflow.py tdd --repo", passing.stderr, "OBSERVATION_BYPASSES_TDD")
        state = self.state()
        latest = evidence_document(resolve_repo_identity(self.repo), str(state["verificationLatestEvidence"]))["runs"][-1]
        self.ok("verify", "--from-evidence", f"{state['verificationLatestEvidence']}:{latest['runIndex']}")
        self.assertEqual(self.state()["verification"], "passed", marker)
        refused = self.cli("verify", "--from-evidence", reference)
        self.assertNotEqual(refused.returncode, 0, f"{marker}: a receipt of another tree was bound")
        for unchanged in (f"cd {self.repo} && {command}", rewritten, f"{sys.executable} {WORKFLOW} tdd -- {command}",
                          f"{command} test_*.py", f"{command} # note", f"{command} ~/tests", f"{command} test_{{a,b}}"):
            self.assertNotIn("updatedInput", self.hook(unchanged, self.repo), f"{marker}: rewrote {unchanged!r}")

    def test_a_carriage_return_stays_with_the_shell(self) -> None:
        # bash passes `-q\rtests` to pytest as one argument; split, it would select tests
        self.assertNotIn("updatedInput", self.hook("pytest -q\rtests", self.repo), "CARRIAGE_RETURN_REWRITTEN")

class ObservedDrift(Ceremony):
    def test_a_run_spanning_an_edit_binds_nothing(self) -> None:
        marker = "OBSERVED_DRIFT_BOUND"
        self.begin()
        edit = "open('app.py', 'a').write('# drift\\n')"
        observed = self.cli("verify", "--observed", "--", sys.executable, "-c", edit)
        self.assertEqual(observed.returncode, 0, f"{marker}: {observed.stderr[-300:]}")
        state = self.state()
        run = evidence_document(resolve_repo_identity(self.repo), str(state["verificationLatestEvidence"]))["runs"][-1]
        self.assertTrue(run.get("bindingError"), f"{marker}: {run}")
        bound = self.cli("verify", "--from-evidence", f"{state['verificationLatestEvidence']}:{run['runIndex']}")
        self.assertNotEqual(bound.returncode, 0, marker)
        self.assertEqual(self.state()["verification"], "pending", marker)

    def test_a_run_that_executed_no_tests_binds_nothing(self) -> None:
        marker = "ZERO_TEST_RUN_PROMOTED"
        self.begin()
        (self.repo / ".git" / "info" / "exclude").write_text(".pytest_cache/\n", encoding="utf-8")
        (self.tmp / "empty").mkdir()
        pytest = (sys.executable, "-m", "pytest")
        for command in ((*pytest, "--collect-only", "-q"), (*pytest, "--co"), (*pytest, "--collectonly"),
                        (*pytest, "--cache-show"), (*pytest, "--fixtures"), (*pytest, "-VV"),
                        (sys.executable, "-m", "unittest", "discover", "-s", str(self.tmp / "empty"))):
            direct = self.cli("verify", "--", *command)
            self.cli("verify", "--observed", "--", *command)
            receipt = str(self.state()["verificationLatestEvidence"])
            run = evidence_document(resolve_repo_identity(self.repo), receipt)["runs"][-1]
            self.assertNotIn("bindingError", run, f"fixture drift: {run.get('bindingError')}")
            bound = self.cli("verify", "--from-evidence", f"{receipt}:{run['runIndex']}")
            self.assertEqual((run["valid"], bound.returncode, direct.returncode), (False, 2, 2),
                             f"{marker}: {' '.join(command[1:])} exit {run['exitCode']}")
            self.assertNotEqual(self.state()["verification"], "passed", marker)

    def test_a_command_naming_a_runner_is_not_a_runner_run(self) -> None:
        self.begin()
        for command in (("echo", "pytest"), ("echo", sys.executable, "-m", "unittest"), ("env", "echo", "pytest"),
                        ("timeout", "5", "echo", "pytest"), (sys.executable, "-c", "print('ok')", "pytest")):
            self.assertEqual(self.cli("verify", "--", *command).returncode, 0, f"RUNNER_ARGUMENT_REFUSED: {command}")

    def test_a_run_binds_to_the_pass_it_names_or_ran_under(self) -> None:
        marker = "RUN_CREDITED_TO_ANOTHER_PASS"
        self.begin()
        before = self.rows()
        named = self.cli("verify", "--workflow-id", "another-pass", "--", sys.executable, "-c", "pass")
        self.assertEqual(named.returncode, 2, f"{marker}: {named.stdout[-300:]}")
        self.assertIn("--workflow-id does not match", named.stderr, marker)
        self.assertEqual(self.rows(), before, marker)
        observed = self.cli("verify", "--observed", "--", sys.executable, str(WORKFLOW), "begin", "--slug", "later",
                            "--intent", "n")
        self.assertEqual(observed.returncode, 0, observed.stderr)
        state = self.state()
        self.assertEqual(state["slug"], "later", marker)
        self.assertIsNone(state.get("verificationLatestEvidence"), f"{marker}: the new pass got the earlier run")

    def test_a_bound_receipt_replaces_its_failed_invocation(self) -> None:
        marker = "BOUND_REPLACEMENT_DROPPED"
        self.begin()
        failed = self.cli("verify", "--", sys.executable, "-c", "raise SystemExit(1)")
        failure = json.loads(failed.stdout.strip().splitlines()[-1])
        self.cli("verify", "--observed", "--", sys.executable, "-c", "pass")
        receipt = str(self.state()["verificationLatestEvidence"])
        index = evidence_document(resolve_repo_identity(self.repo), receipt)["runs"][-1]["runIndex"]
        self.ok("verify", "--from-evidence", f"{receipt}:{index}", "--replaces",
                f"{failure['evidenceId']}:{failure['runIndex']}", "--reason", "the rerun passes")
        state = self.state()
        run = evidence_document(resolve_repo_identity(self.repo), str(state["verificationLatestEvidence"]))["runs"][-1]
        self.assertEqual(run.get("replaces"), f"{failure['evidenceId']}:{failure['runIndex']}", marker)
        self.assertEqual(state["verification"], "passed", marker)


class ObservedPassthrough(Ceremony):
    def test_without_a_readable_workflow_the_command_is_transparent(self) -> None:
        marker = "OBSERVED_NOT_TRANSPARENT"
        probe = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
        for cwd in (self.repo, self.tmp):
            result = self.cli("verify", "--observed", "--", sys.executable, "-c", probe, cwd=cwd)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (3, "out\n", "err\n"), f"{marker}: {result}")
        self.assertFalse((self.tmp / "state").exists(), f"{marker}: recorded outside a workflow")
        self.begin()
        with sqlite3.connect(database_path(resolve_repo_identity(self.repo))) as connection:
            connection.execute("UPDATE workflows SET state_json = json_set(state_json, '$.schemaVersion', 99)")
        result = self.cli("verify", "--observed", "--", sys.executable, "-c", probe)
        self.assertEqual((result.returncode, result.stdout), (3, "out\n"), f"{marker}: unreadable ledger {result}")

    def test_other_flags_are_refused_beside_observed(self) -> None:
        marker = "OBSERVED_FLAG_OVERRIDDEN"
        self.begin()
        before = self.rows()
        for flags in (("--workflow-id", "another-pass"), ("--slug", "other"), ("--repo", str(self.tmp)),
                      ("--timeout", "1"), ("--kind", "quality-gate")):
            refused = self.cli("verify", "--observed", *flags, "--", sys.executable, "-c", "pass")
            self.assertEqual(refused.returncode, 2, f"{marker}: {flags} {refused.stderr[-300:]}")
            self.assertIn("--observed", refused.stderr, f"{marker}: {flags}")
        self.assertEqual(self.rows(), before, f"{marker}: a refused observed run recorded")


class ObservedInWorkflow(Ceremony):
    def test_observed_runs_stream_die_with_their_group_and_leave_replacement_intact(self) -> None:
        marker = "OBSERVED_RUN_DIVERGES_FROM_DIRECT"
        self.begin()
        done = self.tmp / "survived"
        slow = f"import time; print('early', flush=True); time.sleep(3); open({str(done)!r}, 'w').close()"
        process = subprocess.Popen([sys.executable, str(WORKFLOW), "verify", "--observed", "--", sys.executable, "-c", slow],
                                   cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        early, streamed = process.stdout.readline(), not done.exists()
        os.killpg(process.pid, 9)  # Codex stops a command by killing its process group
        process.wait()
        time.sleep(4)
        self.assertEqual((early, streamed, done.exists()), (b"early\n", True, False),
                         f"{marker}: {early!r} streamed={streamed} survived={done.exists()}")
        failing = [sys.executable, "-c", "import sys; sys.exit(1)"]
        failed = self.cli("verify", "--", *failing)
        self.assertEqual(failed.returncode, 2, marker)
        reference = json.loads(failed.stdout.strip().splitlines()[-1])
        self.cli("verify", "--observed", "--", *failing)
        replaced = self.cli("verify", "--replaces", f"{reference['evidenceId']}:{reference['runIndex']}",
                            "--reason", "corrected command", "--", sys.executable, "-c", "pass")
        self.assertEqual(replaced.returncode, 0, f"{marker}: {replaced.stderr[-300:]}")
        self.assertEqual(self.state()["verification"], "passed", marker)

    def gate(self, copies: int = 0, *flags: str) -> subprocess.CompletedProcess[str]:
        """Typed verify over three duplicated sources, eleven escapes and `copies` long-named copies."""
        body = "    total = 0\n    for item in items:\n        total += item * 2\n        total -= 1\n        total += 3\n        total *= 2\n    return total\n"
        for name in ("alpha", "beta", "gamma", *(f"copy_{index:03d}_{'x' * 60}" for index in range(copies))):
            (self.repo / f"{name}.py").write_text(f"def {name}(items):\n" + body, encoding="utf-8")
        for index in range(11):
            (self.repo / f"escape{index:02}.py").write_text(f"X = {index}  # TO" + "DO later\n", encoding="utf-8")
        self.env["TMPDIR"] = str(self.tmp)  # where the printed retrieval saves the report
        record_context_forge(self.repo, self.tmp)
        return self.cli("verify", "--kind", "quality-gate", "--base-ref", "HEAD", *flags)

    def retrieval(self, stdout: str, marker: str) -> tuple[dict[str, object], str]:
        """Runs the printed save, which must print nothing, and returns the saved report and the printed projection."""
        saves = [line.strip() for line in stdout.splitlines() if " evidence " in line and " > " in line]
        projections = [line.strip() for line in stdout.splitlines() if line.strip().startswith("jq ")]
        self.assertTrue(len(saves) == len(projections) == 1, f"{marker}: {stdout[-700:]}")
        saved = subprocess.run(saves[0], shell=True, env=self.env, capture_output=True, text=True)
        self.assertEqual((saved.returncode, saved.stdout, saved.stderr), (0, "", ""), marker)
        return json.loads(Path(shlex.split(saves[0])[-1]).read_text(encoding="utf-8"))["document"]["report"][0], projections[0]

    def test_the_typed_gate_summary_stays_bounded(self) -> None:
        marker = "VERIFY_OUTPUT_UNBOUNDED"
        self.begin()
        gated = self.gate(150)
        printed = gated.stderr  # 153 duplicated regions: 24,748 bytes printed whole before
        self.assertTrue(printed.startswith("Production Code Quality Gate\nverdict: fail")
                        and "- QG54-OWNER-COMPETITION-PRODUCTION [" in printed, f"GATE_SUMMARY_NOT_SHOWN: {printed[:80]}")
        self.assertLessEqual(len(printed.encode()), 6000, marker)
        self.assertIn("- no-quality-escapes: fail (escape00.py:1, escape01.py:1, escape02.py:1, +8 more)", printed, marker)
        self.assertIn(", +150 more", printed, marker)
        receipt = json.loads(gated.stdout)
        self.assertTrue(receipt["valid"] is False and receipt["next"]["command"], f"{marker}: {receipt}")
        run = evidence_document(resolve_repo_identity(self.repo), str(self.state()["verificationLatestEvidence"]))["runs"][-1]
        self.assertEqual(set(run["gate"]), {"ok", "errors"}, "GATE_WARNINGS_HIDDEN")

    def multibyte(self, count: int, part: str = chr(0x1F9EA) * 10, name: str = chr(0x1F525) * 30) -> None:
        """`count` duplicate, escape, conflict and temp files per `name` under eight `part` directories (kilobytes)."""
        deep = self.repo.joinpath(*[part] * 8)
        deep.mkdir(parents=True)
        body = "    total = 0\n    for item in items:\n        total += item * 2\n        total -= 1\n    return total\n"
        for index in range(count):
            stem = deep / f"{name}{index}"
            Path(f"{stem}a.py").write_text(f"def f{index}(items):\n" + body, encoding="utf-8")
            Path(f"{stem}b.py").write_text(f"X = {index}  # TO" + "DO later\n", encoding="utf-8")
            Path(f"{stem}c.py").write_text("<" * 7 + " ours\n" + "=" * 7 + "\n" + ">" * 7 + " theirs\n", encoding="utf-8")
            Path(f"{stem}d.tmp").write_text("tmp\n", encoding="utf-8")

    def test_the_typed_gate_summary_is_bounded_in_bytes(self) -> None:
        marker = "VERIFY_OUTPUT_OVER_BYTE_BOUND"
        self.begin()
        self.multibyte(4)
        printed = self.gate().stderr
        self.assertLessEqual(len(printed.encode()), 6000, f"{marker}: {len(printed)} characters")

    def test_the_printed_projection_is_bounded_in_bytes(self) -> None:
        marker = "PROJECTION_OVER_BYTE_BOUND"
        self.begin()  # just past each cutoff: 7 locations per check and finding, locations over 100 JSON-escaped bytes
        self.multibyte(7, chr(0x1F9EA) * 10, '"\\\x01' * 12)
        ran = subprocess.run(self.retrieval(self.gate().stderr, marker)[1], shell=True, env=self.env,
                             capture_output=True, text=True)
        shown = ran.stdout
        at = [len(json.dumps(item, ensure_ascii=False).encode()) for line in shown.splitlines() for item in json.loads(line)["at"]]
        self.assertTrue(ran.returncode == 0 < len(shown.encode()) <= 4200 and max(at) <= 100, f"{marker}: {len(shown.encode())} bytes, {max(at)}")

    def test_the_typed_gate_names_its_complete_retained_report(self) -> None:
        marker = "VERIFY_REPORT_NOT_RETAINED"
        self.begin()
        printed = self.gate(150).stderr
        report, projection = self.retrieval(printed, marker)  # the save prints nothing
        shown = subprocess.run(projection, shell=True, env=self.env, capture_output=True, text=True)
        direct = subprocess.run([sys.executable, str(ROOT / "skills/production-code/scripts/code_quality_gate.py"), "check",
                                 "--repo", str(self.repo), "--base-ref", "HEAD", "--json"], env=self.env, capture_output=True, text=True)
        escapes = next(check for check in report["checks"] if check["name"] == "no-quality-escapes")
        self.assertTrue(report == json.loads(direct.stdout) and "escape10.py:1" in escapes["sample"]
                        and shown.returncode == 0 < len(shown.stdout.encode()) <= 4200
                        and "QG54-DUPLICATE-ADDED-BLOCK" in shown.stdout, marker)
        cut = self.gate(150, "--timeout", "0")  # no verdict, so no report to name
        self.assertTrue(cut.returncode == 2 and "complete report" not in cut.stderr and json.loads(cut.stdout)["valid"] is False, f"{marker}: {cut.stdout[-300:]}")
        self.git(self.repo, "commit", "--allow-empty", "-qm", "second base")  # overlapping runs over distinct bases
        bases = [subprocess.run(["git", "rev-parse", ref], cwd=self.repo, env=self.env, capture_output=True, text=True).stdout.strip()
                 for ref in ("HEAD", "HEAD~1")]
        racing = [subprocess.Popen([sys.executable, str(WORKFLOW), "verify", "--kind", "quality-gate", "--base-ref", base],
                                   cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for base in bases]
        self.assertEqual([self.retrieval(run.communicate()[1], marker)[0]["evaluation"]["base"]["commit"] for run in racing], bases,
                         f"{marker}: overlapping runs")
        (self.repo / "later.py").write_text("LATER = 1\n", encoding="utf-8")
        later = self.retrieval(self.cli("verify", "--kind", "quality-gate", "--base-ref", "HEAD").stderr, marker)[0]
        self.assertTrue(later["candidateTree"] != report["candidateTree"] and self.retrieval(printed, marker)[0] == report, f"{marker}: earlier locator moved")

class FlagDisposition(Ceremony):
    def comparison(self, expected_exit: int, behavior: str, module: str = "test_app") -> str:
        raw = self.cli("tdd", "--slug", "ceremony", "--behavior-id", behavior, "--",
                       sys.executable, "-m", "unittest", "-v", module)
        self.assertEqual(raw.returncode, expected_exit, repr(raw.stdout + raw.stderr))
        result = json.loads(raw.stdout)
        return f"{result['summaryId']}:{result['runIndex']}"

    def intake(self, identity, wid: str, identifier: str) -> str:
        envelope = self.tmp / f"{identifier}.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "verdict": "completed", "findings": [
            {"id": identifier, "claim": f"{identifier} claim", "material": True, "kind": "behavioral"}]}), encoding="utf-8")
        intake, verdict = advisor_envelope(str(envelope), slug="ceremony", workflow_id=wid, stage="preflight", producer="codex-advisor")
        record_advisor_result(identity, "ceremony", wid, "preflight", "codex-advisor", verdict, intake=intake)
        return str(read_workflow(identity)["advisorPreflight"]["intakeEvidence"])

    def test_behavioral_findings_close_from_flags_alone(self) -> None:
        marker = "FLAG_DISPOSITION_REFUSED"
        wid = self.begin()
        identity = resolve_repo_identity(self.repo)
        first = self.intake(identity, wid, "SPEC-1")
        ref = {"type": "finding", "evidenceId": first, "id": "SPEC-1"}
        self.record_preflight({
                "authoritativeContract": "fixture", "behaviorMap": [
                    item("BM_ATTACK", refs=[ref], basis="fixture"),
                    item("BM_LATER", basis="fixture")]})
        self.intake(identity, wid, "SPEC-2")
        self.comparison(2, "BM_ATTACK")
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.comparison(0, "BM_ATTACK")
        fixed = self.cli("record", "advisor-disposition", "--finding", "SPEC-1", "--fixed")
        self.assertEqual(fixed.returncode, 0, f"{marker}: {fixed.stderr[-400:]}")
        self.comparison(2, "BM_LATER", "test_other")
        (self.repo / "app.py").write_text("value = 2\nother = 2\n", encoding="utf-8")
        later = self.comparison(0, "BM_LATER", "test_other")
        minted = self.cli("record", "advisor-disposition", "--finding", "SPEC-2", "--fixed",
                          "--behavior-id", "BM_LATER", "--reason", "the later attack owns the second claim")
        self.assertEqual(minted.returncode, 0, f"FLAG_FIXED_NEEDS_EVIDENCE_REF: {minted.stderr[-400:]}")
        states = {entry["findingId"]: entry["status"] for entry in self.state()["findingStates"]}
        self.assertEqual(states, {"SPEC-1": "fixed", "SPEC-2": "fixed"}, marker)
        tdd = evidence_document(identity, str(self.state()["tddEvidence"]))
        owners = {entry["id"]: entry.get("sourceRefs") for entry in tdd["behaviorMap"]}
        linked = {ref for event in self.ok("history")["events"] for ref in event["evidenceIds"]}
        self.assertIn(self.state()["tddEvidence"], linked, f"{marker}: the minted map revision has no event")
        self.assertIn({"type": "finding", "evidenceId": str(self.state()["advisorPreflight"]["intakeEvidence"]), "id": "SPEC-2"},
                      owners["BM_LATER"], marker)
        disposition = evidence_document(identity, str(next(entry["dispositionEvidenceId"] for entry in self.state()["findingStates"]
                                                            if entry["findingId"] == "SPEC-2")))
        self.assertEqual(disposition["dispositions"][0]["evidenceRefs"], [f"{self.state()['tddEvidence']}:{later.rsplit(':', 1)[1]}"],
                         "FLAG_FIXED_NEEDS_EVIDENCE_REF")

    def test_finding_relink_keeps_proof_after_its_rerun(self) -> None:
        wid = self.begin()
        identity = resolve_repo_identity(self.repo)
        self.record_preflight({"authoritativeContract": "fixture", "behaviorMap": [item("BM_ATTACK", basis="fixture")]})
        (self.repo / "app.py").write_text("value = 3\nother = 1\n", encoding="utf-8")
        ref = {"type": "finding", "evidenceId": self.intake(identity, wid, "SPEC-1"), "id": "SPEC-1"}
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.comparison(0, "BM_ATTACK")
        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [item("BM_ATTACK", refs=[ref], basis="fixture")]}))
        self.comparison(0, "BM_ATTACK")
        self.ok_raw("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
        fixed = self.cli("record", "advisor-disposition", "--finding", "SPEC-1", "--fixed", "--reason", "relinked owner")
        self.assertEqual(fixed.returncode, 0, "RELINK_DROPPED_PROOF: " + fixed.stderr[-400:])

    def test_map_delta_keeps_new_obligations_pending_until_compared(self) -> None:
        self.begin()
        self.record_preflight({"authoritativeContract": "fixture", "behaviorMap": [item("BM_ATTACK", basis="fixture")]})
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.comparison(0, "BM_ATTACK")
        (self.repo / "app.py").write_text("value = 4\nother = 1\n", encoding="utf-8")
        for items in ([item("BM_ATTACK", basis="fixture"), item("BM_EXTRA", basis="issue 96 fixture intent.", boundaryInputs=["test_value"])],
                      [item("BM_ATTACK", basis="issue 96 fixture intent.", boundaryInputs=["test_value"])]):
            self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": items}))
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.ok_raw("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(self.state()["tdd"], "in-progress", "DELTA_DROPPED_PENDING_OBLIGATION")
        self.comparison(0, "BM_EXTRA")
        self.assertEqual(self.state()["tdd"], "passed", "COMPARED_OBLIGATION_LEFT_PENDING")

    def test_reviewer_package_carries_the_contract_and_comparison_outcomes(self) -> None:
        self.begin()
        self.record_preflight({"authoritativeContract": "Create keeps its suppression", "behaviorMap": [item("BM_ATTACK", basis="fixture")]})
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        self.comparison(0, "BM_ATTACK")
        gate = self.cli("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
        handoff = json.loads(gate.stdout)["next"]  # a passing gate's stdout is its receipt alone
        self.assertTrue(gate.returncode == 0 and '{"findings":' in handoff["input"] and "help" not in handoff and "complete report" not in gate.stderr)
        package = self.cli("checkpoint", "--phase", "code-review", "--channel-dir", str(self.tmp))
        self.assertEqual(package.returncode, 0, "REVIEW_PACKAGE_MISSING: " + package.stderr[-300:])
        channels = {channel["name"]: Path(channel["contentPath"]).read_text() for channel in json.loads(package.stdout)["channels"]}
        self.assertLessEqual({"behavior-map", "diff", "intent"}, set(channels), "REVIEW_PACKAGE_MISSING")
        work = json.loads(channels["behavior-map"])
        self.assertEqual((work["preflightInterpretation"], [arm["outcome"] for arm in work["items"][0]["comparison"]["arms"]]),
                         ("Create keeps its suppression", ["failed", "passed"]), "REVIEW_PACKAGE_MISSING")


class FlagRefusal(Ceremony):
    def test_refused_flag_dispositions_mutate_nothing(self) -> None:
        marker = "FLAG_REFUSAL_MUTATED"
        wid = self.begin()
        identity = resolve_repo_identity(self.repo)
        intake = FlagDisposition.intake(self, identity, wid, "SPEC-1")
        self.record_preflight({
                "authoritativeContract": "fixture", "behaviorMap": [
                    item("BM_ATTACK", refs=[{"type": "finding", "evidenceId": intake, "id": "SPEC-1"}],
                         basis="fixture")]})
        FlagDisposition.comparison(self, 2, "BM_ATTACK")
        def refuse(*cases):
            before = self.rows()
            for reason, extra in cases:
                refused = self.cli("record", "advisor-disposition", *extra, "--reason", "attempt")
                self.assertEqual(refused.returncode, 2, f"{marker}: {extra} {refused.stdout}")
                self.assertIn(reason, refused.stderr, f"{marker}: refused for another reason: {refused.stderr[-300:]}")
                self.assertEqual(self.rows(), before, f"{marker}: {extra} mutated the ledger")
            self.assertEqual(self.state()["findingStates"][0]["status"], "pending", marker)
        refuse(("SPEC-9", ("--finding", "SPEC-9", "--fixed")),
               ("BM_NOPE", ("--finding", "SPEC-1", "--fixed", "--behavior-id", "BM_NOPE")))
        (self.repo / "app.py").write_text("value = 2\nother = 1\n", encoding="utf-8")
        refuse(("no current comparison", ("--finding", "SPEC-1", "--fixed")))
        FlagDisposition.comparison(self, 0, "BM_ATTACK")
        self.ok("record", "advisor-disposition", "--finding", "SPEC-1", "--fixed")


class RemovedSurfaces(Ceremony):
    def test_retired_verbs_and_flags_refuse(self) -> None:
        marker = "REMOVED_SURFACE_ACCEPTED"
        wid = self.begin()
        for args in (("set-phase", "--phase", "implementation", "--status", "in-progress", "--slug", "ceremony", "--workflow-id", wid),
                     ("record-production-code", "--slug", "ceremony", "--workflow-id", wid, "--input", "-"),
                     ("pause", "--slug", "ceremony", "--workflow-id", wid, "--reason", "r", "--compact"),
                     ("record-review", "--input", "-")):
            result = self.cli(*args, input=json.dumps({"ok": True, "findings": []}))
            self.assertEqual(result.returncode, 2, f"{marker}: {args[0]} accepted")
        for flag in ("--packet", "--base-ref"):
            wrapper = subprocess.run(["bash", str(WRAPPER), "--slug", "ceremony", flag, "p", "--", "q"],
                                     env=self.env, capture_output=True, text=True)
            self.assertIn("unknown argument", wrapper.stderr, f"{marker}: {flag}")
        for shim in ("pass-state.py", "verify-run.py"):
            self.assertFalse((WORKFLOW.parent / shim).exists(), f"{marker}: {shim}")


class MinimalDocuments(Ceremony):
    def test_documents_carry_only_consumed_fields(self) -> None:
        marker = "MINIMAL_DOCUMENT_REFUSED"
        self.begin()
        document = {"authoritativeContract": "fixture", "behaviorMap": []}
        approve_preflight(self.repo, document)
        recorded = self.cli("record", "preflight", "--input", "-", input=json.dumps(document))
        self.assertEqual(recorded.returncode, 0, f"{marker}: {recorded.stderr[-300:]}")
        self.assertNotIn("intent", recorded.stdout, f"{marker}: the preflight receipt echoes the intent")
        self.ok("verify", "--", sys.executable, "-c", "pass")
        self.ok("verify", "--kind", "quality-gate", "--base-ref", "HEAD")
        review = {"findings": [{"id": "R-1", "claim": "a real claim", "material": True,
                                "axis": "Spec", "location": "extra context is dropped"}]}
        self.ok("record", "review", "--input", "-", input=json.dumps(review))
        self.assertEqual(self.state()["codeReview"]["findings"], "pending", marker)
        self.ok("record", "advisor-disposition", "--finding", "R-1", "--report-only",
                "--reason", "The measured condition has no material consequence on this task.")
        self.assertEqual(self.state()["findingStates"][-1]["status"], "report-only", marker)

        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [item("BM_TWO", kind="preservation")]}))
        self.assertEqual(self.state()["tdd"], "in-progress", marker)

    def test_a_map_update_names_only_what_changes(self) -> None:
        marker = "PARTIAL_MAP_UPDATE"
        self.begin()
        self.record_preflight({"authoritativeContract": "fixture", "behaviorMap": [
            item("BM_ONE", interpretations=["a", "b"], interpretation="a", authority="spec", boundaryInputs=["test_old"])]})
        current = lambda: evidence_document(resolve_repo_identity(self.repo), self.state()["tddEvidence"])["behaviorMap"]
        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [{"id": "BM_ONE", "boundaryInputs": ["test_y"]}]}))
        [one] = current()
        self.assertEqual((one["boundaryInputs"], one["interpretations"], one["behavior"]), (["test_y"], ["a", "b"], "BM_ONE behavior"), marker)
        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [{"id": "BM_ONE", "interpretations": ["c", "d"]}]}))
        self.assertNotIn("interpretation", current()[0], marker + ": new readings kept the old choice")
        self.ok("record", "tdd-map", "--input", "-", input=json.dumps({"items": [{"id": "BM_ONE", "interpretations": None}]}))
        self.assertNotIn("interpretations", current()[0], marker)
        added = self.cli("record", "tdd-map", "--input", "-", input=json.dumps({"items": [{"id": "BM_NEW", "boundaryInputs": ["test_z"]}]}))
        self.assertEqual(added.returncode, 2, marker + ": a new partial item was recorded")

    def test_the_dry_run_refuses_a_description_as_a_case_name(self) -> None:
        marker = "DESCRIPTION_RECORDED_AS_CASE"
        self.begin()
        draft, envelope = self.tmp / "draft.json", json.dumps({"schemaVersion": 1, "verdict": "approved", "findings": []})
        for names, code in ((["test_kept: kept stays one"], 2), (["t.py::test_kept: kept stays one"], 2), (["t.py::test_kept[a: b]", "kept"], 0)):
            draft.write_text(json.dumps({"authoritativeContract": "c", "behaviorMap": [item("BM_ONE", boundaryInputs=names)]}))
            checked = self.cli("record", "advisor-result", "--check", "--stage", "preflight", "--input", "-",
                               "--preflight-file", str(draft), input=envelope)
            self.assertEqual(checked.returncode, code, f"{marker}: {names}: {checked.stderr[-300:]}")
            self.assertEqual("into expected" in checked.stderr, code == 2, marker)


class DerivedIdentity(Ceremony):
    def test_the_active_workflow_supplies_identity(self) -> None:
        marker = "IDENTITY_NOT_DERIVED"
        self.begin()
        for fields in (("slug,workflowId",), ("slug workflowId",), ("slug", "workflowId")):
            result = self.cli("status", "--slug", "ceremony", "--fields", *fields)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {k: self.state()[k] for k in ("slug", "workflowId")})
        paused = self.cli("pause", "--reason", "waiting")
        self.assertEqual(paused.returncode, 0, f"{marker}: {paused.stderr[-300:]}")
        self.assertEqual(self.cli("pause", "--slug", "another", "--reason", "x").returncode, 2, marker)
        self.ok("verify", "--", sys.executable, "-c", "pass")
        self.ok("record", "advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--input", "-",
                input=json.dumps({"schemaVersion": 1, "verdict": "completed", "findings": []}))
        self.assertEqual(self.state()["advisorPreflight"]["status"], "completed", marker)
        self.ok("record", "advisor-disposition", "--findings", "none")


if __name__ == "__main__":
    unittest.main()
