#!/usr/bin/env python3
"""Real Repo Context Forge bootstrap integration with workflow state."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
BOOTSTRAP = ROOT / "skills" / "repo-context-forge" / "scripts" / "bootstrap.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
CANONICAL_BOOTSTRAP = Path("/home/prop_/.local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py")
GITNEXUS = shutil.which("gitnexus")
OWNER_RULES = ("QG54-OWNER-COMPETITION-PRODUCTION", "QG54-OWNER-COMPETITION-TEST")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.tests.support import build_no_change_document, empty_advisor_envelope, fixture_env, graph_packet  # noqa: E402


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
class RepoForgeWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-repoforge-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.intent = "record the real rendered intake packet"
        self.slug = "repoforge-workflow"
        # The shared fixture environment, not a second copy of it: it also isolates
        # HOME, which is what keeps these real intakes out of the caller's GitNexus
        # registry, analysis cache and machine-wide intake lock.
        self.env = fixture_env(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("remote", "add", "origin", "https://example.invalid/workflow-fixture.git")
        # A callable symbol and a dependent, so the producer's graph plan has real
        # context and impact to resolve rather than an empty single-file surface.
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
        )
        self.git("add", "app.py", "caller.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.pass_state("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def pass_state(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def bootstrap_command(
        self,
        *,
        intent: str | None = None,
        out: Path | None = None,
        mode: str = "intent",
        gitnexus_mode: str = "off",
        map_build: str = "never",
        base: str | None = None,
    ) -> list[str]:
        described = self.intent if intent is None else intent
        command = [
            sys.executable, str(BOOTSTRAP), "--repo", str(self.repo),
            "--workflow-slug", self.slug, "--mode", mode,
            "--map-build", map_build, "--gitnexus-mode", gitnexus_mode, "--top", "5",
        ]
        command += ["--base", base] if base else []
        # An empty intent is passed as no intent at all, which is what leaves a clean
        # local checkout with no target surface for the producer to block on.
        command += ["--intent", described] if described else []
        return command + (["--out", str(out)] if out is not None else [])

    def bootstrap(self, *, timeout: int = 120, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.bootstrap_command(**kwargs), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )

    def graph_command(self, **kwargs: object) -> list[str]:
        """The same public Adapter over a real graph plan instead of `--gitnexus-mode off`.

        Local mode against a dirty dependent, because that is what gives the producer a
        target to resolve: with no target the packet plans no checks, and a resolved
        result over an empty plan carries no graph facts to record.

        The producer is real and GitNexus still indexes; only the SoulForge map is
        skipped, because nothing these tests assert reads it. Measured on this
        fixture: 11.7s per run with the map, 1.8s without, across 30 runs.
        """
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )
        return self.bootstrap_command(mode="local", gitnexus_mode="auto", map_build="never", **kwargs)

    def graph_bootstrap(self, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.graph_command(**kwargs), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )

    def status(self) -> dict[str, object]:
        result = self.pass_state("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def evidence(self, evidence_id: str) -> dict[str, object]:
        result = self.pass_state("evidence", "--full", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_sha256_repo_records_projection_and_reaches_advisor_checkpoint(self) -> None:
        marker = "SHA256_WORKFLOW_NOT_READY"
        repo = self.tmp / "sha256-repo"
        repo.mkdir()
        env = self.env | {"CODEX_WORKFLOW_STATE_ROOT": str(self.tmp / "sha256-state")}

        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args], cwd=repo, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return result.stdout.strip()

        git("init", "-q", "--object-format=sha256")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "Workflow Harness")
        git("remote", "add", "origin", "https://example.invalid/workflow-sha256.git")
        (repo / "app.py").write_text(
            "def compute(value):\n    return value + 1\n", encoding="utf-8"
        )
        (repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
        )
        git("add", "app.py", "caller.py")
        git("commit", "-q", "-m", "base")
        head = git("rev-parse", "HEAD")
        self.assertEqual(len(head), 64)

        slug = "repoforge-sha256"
        intent = "record the real SHA-256 compute projection"
        begun = subprocess.run(
            [sys.executable, str(WORKFLOW), "begin", "--repo", str(repo),
             "--slug", slug, "--intent", intent],
            cwd=repo, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        (repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )

        forged = subprocess.run(
            [sys.executable, str(BOOTSTRAP), "--repo", str(repo),
             "--workflow-slug", slug, "--mode", "local", "--map-build", "auto",
             "--gitnexus-mode", "auto", "--top", "5", "--intent", intent],
            cwd=repo, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )
        self.assertEqual(forged.returncode, 0, marker + "\n" + forged.stdout + forged.stderr)

        checkpoint = subprocess.run(
            [sys.executable, str(WORKFLOW), "checkpoint", "--repo", str(repo),
             "--phase", "preflight-advice"],
            cwd=repo, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(
            checkpoint.returncode, 0, marker + "\n" + checkpoint.stdout + checkpoint.stderr,
        )
        payload = json.loads(checkpoint.stdout)
        self.assertTrue(payload["ready"], marker)
        self.assertEqual(payload["passStartOid"], head, marker)
        self.assertEqual(len(payload["activeCandidateTree"]), 64, marker)

    def test_corrupt_authoritative_ledger_refuses_before_the_bootstrap_runs(self) -> None:
        state = self.status()
        database = (Path(self.env["CODEX_WORKFLOW_STATE_ROOT"])
                    / str(state["repo"]["key"]) / "workflow.sqlite3")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'repo_key'",
                ("different-repository",),
            )
            connection.commit()
        finally:
            connection.close()
        before = database.read_bytes()

        refused = self.bootstrap()

        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertEqual(refused.stdout, "", "the external bootstrap ran for corrupt state")
        self.assertEqual(
            refused.stderr,
            "<blocker>cannot bind Repo Context Forge to the active workflow: "
            "workflow database repository identity does not match this checkout</blocker>\n",
        )
        self.assertEqual(database.read_bytes(), before)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_real_bootstrap_advances_workflow_without_extra_persisted_records(self) -> None:
        direct = self.graph_bootstrap()
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", direct.stdout)
        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        self.assertEqual(state["phase"], "repo-context-forge")
        state_dir = Path(self.env["CODEX_WORKFLOW_STATE_ROOT"])
        self.assertFalse(any(path.name in {"packets", "repoforge"} for path in state_dir.rglob("*")))

        output = self.tmp / "packet.txt"
        redirected = self.graph_bootstrap(out=output)
        self.assertEqual(redirected.returncode, 0, redirected.stdout + redirected.stderr)
        self.assertEqual(redirected.stdout, "")
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", output.read_text(encoding="utf-8"))
        self.assertEqual(self.status()["repoContextForge"], "passed")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_no_remote_source_gap_reaches_advisor_checkpoint(self) -> None:
        marker = "NO_REMOTE_SOURCE_PROVENANCE_BLOCKED"
        self.git("remote", "remove", "origin")
        self.git("branch", "-M", "main")

        forged = self.graph_bootstrap(base="main")

        self.assertEqual(forged.returncode, 0, marker + "\n" + forged.stdout + forged.stderr)
        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed", marker)
        evidence = self.evidence(str(state["repoContextForgeEvidence"]))["document"]
        projection = evidence["advisorProjection"]
        self.assertEqual(projection["sourceRepo"], {"gap": "source_repo_unavailable"}, marker)
        self.assertEqual(projection["expectedCandidateTree"], projection["indexedCandidateTree"], marker)
        checkpoint = self.pass_state("checkpoint", "--phase", "preflight-advice")
        self.assertEqual(checkpoint.returncode, 0, marker + "\n" + checkpoint.stdout + checkpoint.stderr)
        self.assertTrue(json.loads(checkpoint.stdout)["ready"], marker)

    def governed_bootstrap(self, *, dirty: bool, mode: str | None = None) -> subprocess.CompletedProcess[str]:
        """The public wrapper on a branch one commit past main, with or without an
        uncommitted candidate on top: the producer's own mode choice, or the caller's."""
        # A tracked .gitignore: without one the producer's pr-mode receipt never
        # publishes (its SoulForge ignore-line cleanup cannot check out an untracked file).
        (self.repo / ".gitignore").write_text(".gitnexus/\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore the graph index")
        self.git("branch", "-M", "main")
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 2\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "feature")
        if dirty:
            (self.repo / "probe.py").write_text(
                "from app import compute\n\n\ndef probe():\n    return compute(3)\n", encoding="utf-8"
            )
        return subprocess.run(
            [sys.executable, str(BOOTSTRAP), "--repo", str(self.repo), "--workflow-slug", self.slug,
             "--map-build", "auto", "--gitnexus-mode", "auto", "--top", "5", "--base", "main",
             "--intent", self.intent, *(["--mode", mode] if mode else [])],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_clean_governed_branch_keeps_the_producers_mode(self) -> None:
        marker = "CLEAN_GOVERNED_MODE_CHANGED"
        forged = self.governed_bootstrap(dirty=False)
        self.assertEqual(forged.returncode, 0, marker + "\n" + forged.stdout + forged.stderr)
        self.assertIn("mode: pr", forged.stdout, marker)
        checkpoint = self.pass_state("checkpoint", "--phase", "preflight-advice")
        self.assertTrue(json.loads(checkpoint.stdout)["ready"], marker + ": " + checkpoint.stdout)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_dirty_governed_branch_reaches_the_advisor_checkpoint(self) -> None:
        marker = "DIRTY_GOVERNED_CHECKOUT_BLOCKS_ADVISOR_CHECKPOINT"
        forged = self.governed_bootstrap(dirty=True)
        self.assertEqual(forged.returncode, 0, marker + "\n" + forged.stdout + forged.stderr)
        checkpoint = self.pass_state("checkpoint", "--phase", "preflight-advice")
        self.assertEqual(checkpoint.returncode, 0, marker + "\n" + checkpoint.stdout + checkpoint.stderr)
        self.assertTrue(json.loads(checkpoint.stdout)["ready"], marker + ": " + checkpoint.stdout)
        self.assertIn("mode: local", forged.stdout, marker)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_dirty_governed_branch_overrides_an_explicit_pr_mode(self) -> None:
        """The producer honors its last --mode: a caller's pr on a dirty governed
        checkout would bind the projection to HEAD, so the wrapper's local wins."""
        marker = "EXPLICIT_PR_MODE_BINDS_A_DIRTY_GOVERNED_CHECKOUT_TO_HEAD"
        forged = self.governed_bootstrap(dirty=True, mode="pr")
        self.assertEqual(forged.returncode, 0, marker + "\n" + forged.stdout + forged.stderr)
        self.assertIn("mode: local", forged.stdout, marker)
        checkpoint = self.pass_state("checkpoint", "--phase", "preflight-advice")
        self.assertTrue(json.loads(checkpoint.stdout)["ready"], marker + ": " + checkpoint.stdout)

    def ledger_bytes(self, state: dict[str, object]) -> bytes:
        return (Path(self.env["CODEX_WORKFLOW_STATE_ROOT"])
                / str(state["repo"]["key"]) / "workflow.sqlite3").read_bytes()

    def test_an_unresolved_producer_result_refuses_and_mutates_nothing(self) -> None:
        """A planned graph the producer could not resolve is not evidence."""
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )
        before = self.status()
        ledger = self.ledger_bytes(before)

        # A real two-check plan with the graph engine disabled: the producer reports
        # the analysis blocked rather than resolved, and still exits zero.
        refused = self.bootstrap(mode="local", map_build="auto", gitnexus_mode="off")

        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", refused.stdout)
        self.assertIn("no resolved graph result", refused.stderr)
        self.assertIn("rerun the bootstrap", refused.stderr)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ledger_bytes(before), ledger, "a refused producer changed the ledger")

    def test_a_blocked_packet_never_reaches_workflow_state(self) -> None:
        """The producer's own blocker exits non-zero, so nothing is recorded from it."""
        before = self.status()
        ledger = self.ledger_bytes(before)

        blocked = self.bootstrap(mode="local", intent="")

        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertIn("blocker", blocked.stdout)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ledger_bytes(before), ledger, "a blocked packet changed the ledger")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_packet_that_planned_no_checks_still_records_its_resolved_result(self) -> None:
        """How many checks a packet plans is the producer's call, not a refusal here."""
        # The producer plans a file_context check for any content-bearing file and
        # blocks an intent that matches no symbol, so a zero-check packet needs a
        # .gitignore-only tree in repo mode. Committing the .gitnexus/ ignore rule
        # keeps GitNexus's own ignore write from mutating the analysis candidate.
        (self.repo / ".gitignore").write_text(".gitnexus/\n", encoding="utf-8")
        self.git("rm", "-q", "app.py", "caller.py")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "zero-checkable surface")

        recorded = self.bootstrap(mode="repo", intent="", gitnexus_mode="auto", timeout=600)

        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        document = self.evidence(str(state["repoContextForgeEvidence"]))["document"]
        self.assertEqual(document["advisorProjection"]["graph"]["status"], "resolved")
        self.assertNotIn("graph", document)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_same_slug_replacement_rejects_the_stale_producer(self) -> None:
        """A pass replaced while the producer runs never receives its graph result."""
        process = subprocess.Popen(
            self.graph_command(), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            # Popen returning only means the child was forked, and the bootstrap resolves
            # repository identity — git, realpath and cksum, three subprocesses of its own —
            # before it captures the workflow id. Only the producer starting proves the
            # capture already happened, so match the child's command line instead of
            # accepting any child; otherwise the replacement below can still land first and
            # become the instance the child captures, which fails on exit 0.
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            producer = ""
            deadline = time.monotonic() + 300
            while not producer and time.monotonic() < deadline:
                for pid in children.read_text().split():
                    try:
                        command = Path(f"/proc/{pid}/cmdline").read_bytes()
                    except OSError:
                        continue  # an identity subprocess that exited between the two reads
                    if str(CANONICAL_BOOTSTRAP).encode("utf-8") in command:
                        producer = pid
                        break
                if not producer:
                    time.sleep(0.001)
            self.assertTrue(producer, "the real producer never started, so no capture was observed")

            replaced = self.pass_state("begin", "--slug", self.slug, "--intent", "replacement pass")
            self.assertEqual(replaced.returncode, 0, replaced.stdout + replaced.stderr)
            self.assertIsNone(
                process.poll(),
                "the producer finished before the replacement landed; the stale path was not exercised",
            )
            stdout, stderr = process.communicate(timeout=600)
        finally:
            process.kill()

        self.assertEqual(process.returncode, 2, stdout + stderr)
        self.assertIn("cannot record Repo Context Forge graph evidence", stderr)
        # The specific cause, not just the adapter's wrapper: any WorkflowError produces
        # the line above, so only this one proves the stale instance was what refused.
        self.assertIn("--workflow-id does not match the active workflow instance", stderr)
        state = self.status()
        self.assertEqual(state["workflowId"], json.loads(replaced.stdout)["workflowId"])
        self.assertEqual(state["repoContextForge"], "pending")
        self.assertNotIn("repoContextForgeEvidence", state)

    def advance_to_tdd(self) -> None:
        """The real recorders between recorded context evidence and the TDD gate."""
        state = self.status()
        slug, wid = str(state["slug"]), str(state["workflowId"])
        declaration = self.tmp / "design-absent.json"
        declaration.write_text(json.dumps({"schemaVersion": 1, "status": "absent", "reason": "test pass has no governing design"}), encoding="utf-8")
        for step in (
            ("record", "advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
             "--source", "codex-advisor", "--input", empty_advisor_envelope(self.tmp, "completed"), "--design-declaration", str(declaration)),
            ("record", "advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        ):
            result = self.pass_state(*step)
            self.assertEqual(result.returncode, 0, " ".join(step) + "\n" + result.stdout + result.stderr)
        document = build_no_change_document("issue-106 typed verification fixture")
        doc_path = self.tmp / "preflight.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        committed = self.pass_state("record", "preflight", "--slug", slug,
                                    "--workflow-id", wid, "--input", str(doc_path))
        self.assertEqual(committed.returncode, 0, committed.stdout + committed.stderr)

    def advance_to_typed_verification(self) -> None:
        """The real recorders between recorded context evidence and typed verification."""
        self.advance_to_tdd()
        result = self.pass_state("tdd", "--slug", self.slug, "--not-required",
                                 "fixture pass proves evidence wiring, not a fixture behavior change")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def typed_quality_gate_run(self, base_ref: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        """One typed quality-gate verification and the run entry it recorded."""
        verified = self.pass_state(
            "verify", "--slug", self.slug, "--kind", "quality-gate", "--base-ref", base_ref,
        )
        state = self.status()
        document = self.evidence(str(state["verificationLatestEvidence"]))["document"]
        runs = document["runs"]
        self.assertTrue(runs, "typed verification recorded no run")
        return verified, runs[-1]

    def owner_states(self, gate_payload: dict[str, object]) -> dict[str, dict[str, object]]:
        """Each owner rule's per-evaluation state finding from the gate verdict."""
        states = {
            str(item["ruleId"]): item
            for item in gate_payload["findings"]
            if str(item["ruleId"]) in OWNER_RULES and item["region"]["scope"] == "evaluation"
        }
        self.assertEqual(sorted(states), sorted(OWNER_RULES), gate_payload["findings"])
        return states

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_typed_verification_hands_recorded_graph_evidence_to_the_owner_rules(self) -> None:
        """A governed pass with uncommitted edits reaches a complete owner-rule verdict.

        The whole chain is real: the producer analyzes the dirty candidate, the
        bootstrap records the evidence, and typed verification must hand that
        recorded evidence to the gate so both owner-competition rules evaluate
        instead of reporting the unestablished-scope gap.
        """
        self.git("branch", "-M", "main")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_typed_verification()

        verified, run = self.typed_quality_gate_run("main")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIsNone(run["bindingError"], run["bindingError"])
        gate = json.loads("\n".join(verified.stdout.splitlines()[:-1]))
        for rule_id, finding in sorted(self.owner_states(gate).items()):
            gaps = finding["completeness"]["gaps"]
            self.assertNotEqual(finding["status"], "incomplete", f"{rule_id} could not evaluate: {gaps}")
            self.assertTrue(finding["completeness"]["complete"], f"{rule_id} gaps: {gaps}")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_evidence_bound_to_a_different_snapshot_keeps_the_owner_rules_incomplete(self) -> None:
        """Falsification: an edit after the recorded analysis is named as staleness.

        The gate captures the moved tree, the recorded evidence still names the
        analyzed one, and its own binding check must report the stale gap —
        never silently accept, never rebind.
        """
        self.git("branch", "-M", "main")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_typed_verification()
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(3)\n", encoding="utf-8"
        )

        verified, run = self.typed_quality_gate_run("main")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual(run["exitCode"], 0)
        self.assertIn("external graph evidence is stale: it does not name the evaluated snapshot",
                      verified.stdout, "owner rules did not name the stale binding")
        findings = json.JSONDecoder().raw_decode(verified.stdout.split('"findings": ', 1)[1])[0]
        for finding in self.owner_states({"findings": findings}).values():
            self.assertEqual(finding["status"], "incomplete", "stale owner rule passed")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_bootstrap_records_the_producer_graph_result_as_workflow_evidence(self) -> None:
        """One public bootstrap binds the producer's own resolved graph result to this pass."""
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", forged.stdout)

        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        evidence_id = state.get("repoContextForgeEvidence")
        self.assertIsInstance(evidence_id, str, f"no producer evidence was recorded: {state}")

        record = self.evidence(str(evidence_id))
        self.assertEqual(record["kind"], "repo-context-forge")
        self.assertEqual(record["workflowId"], state["workflowId"])
        self.assertNotIn("graph", record["document"])
        self.assertRegex(record["document"]["packetSha256"], r"^[0-9a-f]{64}$")
        projection = record["document"]["advisorProjection"]
        self.assertEqual(projection["schemaVersion"], 1)
        self.assertEqual(projection["graph"]["status"], "resolved")
        self.assertEqual(
            (projection["expectedCandidateTree"], projection["indexedCandidateTree"]),
            (state["activeCandidateTree"], state["activeCandidateTree"]),
        )
        checkpoint_result = self.pass_state("checkpoint", "--phase", "preflight-advice")
        self.assertEqual(
            checkpoint_result.returncode, 0,
            checkpoint_result.stdout + checkpoint_result.stderr,
        )
        checkpoint = json.loads(checkpoint_result.stdout)
        self.assertEqual(checkpoint["advisorProjectionEvidence"], evidence_id)
        self.assertEqual(checkpoint["advisorProjection"], projection)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_brief_mutation_receipt_reports_graph_recovery(self) -> None:
        marker = "MUTATION_STATUS_GRAPH_READINESS_DIVERGED"
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        analyzed = self.status()
        workflow_id = str(analyzed["workflowId"])
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(3)\n", encoding="utf-8"
        )

        paused = self.pass_state(
            "pause", "--slug", self.slug, "--workflow-id", workflow_id,
            "--reason", "measure candidate readiness",
        )
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)
        mutation, status = json.loads(paused.stdout), self.status()
        self.assertEqual((mutation["nextAction"], status["repoContextForge"]),
                         ("repo-context-forge", "pending"), marker)
        self.assertNotIn("activeCandidateTree", mutation, marker)

    def git_out(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.strip()

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_rerun_keeps_the_first_recorded_base_and_reports_the_conflict(self) -> None:
        """The recorded base is immutable for the pass: a rerun that resolves a
        different commit keeps the original and says so, because a moving base
        would make successive per-edit measurements incoherent."""
        fork = self.git_out("rev-parse", "HEAD")
        self.git("branch", "base-main")
        forged = self.graph_bootstrap(base="base-main")
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), fork)

        (self.repo / "feature.py").write_text("def grown():\n    return 1\n", encoding="utf-8")
        self.git("add", "feature.py")
        self.git("commit", "-q", "-m", "advance the branch")
        self.git("branch", "-f", "base-main")
        moved = self.git_out("rev-parse", "base-main")
        self.assertNotEqual(moved, fork)

        rerun = self.graph_bootstrap(base="base-main")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(self.status().get("baseOid"), fork, "a rerun replaced the immutable base")
        self.assertIn(f"pass base already recorded as {fork}", rerun.stderr)
        self.assertIn(f"this bootstrap resolved {moved}", rerun.stderr)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_pass_without_a_resolvable_base_records_no_base_oid(self) -> None:
        """Honest absence: when the producer resolves no base, nothing is recorded."""
        self.git("branch", "-m", "feature-work")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertNotIn("baseOid", self.status())

    def stack(self) -> str:
        """A two-branch stack: feat1 is the lower PR, feat2 the leaf whose PR base
        is feat1 (gh's per-branch `gh-merge-base` config). Returns feat1's tip,
        the merge-base the leaf's pass must record."""
        self.git("checkout", "-q", "-b", "feat1")
        (self.repo / "lower.py").write_text("def lower():\n    return 1\n", encoding="utf-8")
        self.git("add", "lower.py")
        self.git("commit", "-q", "-m", "feat1 carries an escape")
        self.git("checkout", "-q", "-b", "feat2")
        (self.repo / "leaf.py").write_text("def leaf():\n    return 2\n", encoding="utf-8")
        self.git("add", "leaf.py")
        self.git("commit", "-q", "-m", "feat2 is clean")
        self.git("config", "branch.feat2.gh-merge-base", "feat1")
        return self.git_out("rev-parse", "feat1")

    def gate(self, base: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--base-ref", base, "--json"],
            cwd=self.repo, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        # A failed gate must fail here, not as a parse error or an empty verdict.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_run_cancelled_after_its_packet_records_no_base(self) -> None:
        """Cancellation, not refusal: the packet is already rendered when the run
        is killed, which is the window the recording sits in."""
        feat1 = self.stack()
        # stderr goes to the void rather than an undrained pipe, and the marker
        # wait has a deadline, so a stalled producer fails this test rather than
        # holding the suite open.
        cancelled = subprocess.Popen(
            self.graph_command(), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        # The watchdog, not the loop, bounds the wait: a child that emits nothing
        # at all would otherwise block the iterator before any check.
        watchdog = threading.Timer(600, cancelled.kill)
        watchdog.start()
        emitted = False
        try:
            for line in cancelled.stdout:
                if "END_REPO_CONTEXT_FORGE_REQUIRED_INTAKE" in line:
                    emitted = True
                    break
        finally:
            watchdog.cancel()
            cancelled.kill()
            cancelled.wait(timeout=120)
            cancelled.stdout.close()
        self.assertTrue(emitted, "CANCELLED_RUN_LEFT_A_BASE: the producer never emitted its packet")
        self.assertNotIn("baseOid", self.status(), "CANCELLED_RUN_LEFT_A_BASE")

        # The other way a run can end before recording: the producer refuses its
        # arguments, so no packet exists at all.
        refused = subprocess.run(
            self.graph_command() + ["--top", "not-a-number"], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertNotIn("baseOid", self.status(), "INTERRUPTED_RUN_LEFT_A_BASE")

        retry = self.graph_bootstrap()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self.status().get("baseOid"), feat1, "CANCELLED_RUN_LEFT_A_BASE")
        self.assertNotIn("pass base already recorded", retry.stderr, "CANCELLED_RUN_LEFT_A_BASE")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_an_abbreviated_base_option_is_not_overridden(self) -> None:
        """The producer accepts every unambiguous abbreviation of `--base`, so an
        explicit base spelled `--ba` is still the caller's choice."""
        root = self.git_out("rev-parse", "HEAD")
        self.git("branch", "caller-base", root)
        self.stack()
        forged = subprocess.run(
            self.graph_command() + ["--ba", "caller-base"], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), root, "ABBREVIATED_BASE_WAS_OVERRIDDEN")

    def resolve_base(self) -> str | None:
        """The adapter over this checkout, seeing only the `gh` the case placed:
        the search path is the case's own directory, so an absent `gh` is really
        absent and no installed one reaches the network."""
        binaries = self.tmp / "bin"
        binaries.mkdir(exist_ok=True)
        return self.adapter_module()._pr_base_ref(self.repo, env={"PATH": str(binaries)})

    def test_a_base_name_this_checkout_lacks_resolves_to_nothing(self) -> None:
        """The fall-through turns on this: a name no ref carries must resolve to
        nothing, so a caller holding it can still reach its next signal. Real
        refs in a real checkout; the name a pull request would supply is a
        string either way."""
        marker = "UNFETCHED_BASE_NAME_DID_NOT_RESOLVE_TO_NOTHING"
        bootstrap = self.adapter_module()
        self.git("branch", "lower")
        self.assertIsNone(bootstrap._branch_ref(self.repo, "never-fetched", ("origin",)), marker)
        self.assertEqual(
            bootstrap._branch_ref(self.repo, "lower", ("origin",)), "refs/heads/lower",
            "A_LOCAL_BRANCH_STOPPED_RESOLVING",
        )

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_stacked_branch_records_the_merge_base_with_its_pr_base_branch(self) -> None:
        """Measured on GitNexus #18: the producer's fallback base is the fork point
        from main, so a stacked pass measured the whole stack. The leaf's pass
        records the merge-base with the branch its PR merges into."""
        feat1 = self.stack()
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), feat1, "STACKED_BASE_NOT_RECORDED")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_the_typed_gate_on_a_stacked_branch_measures_only_its_own_delta(self) -> None:
        """The gate is given the base the pass recorded, never one chosen by
        hand, and it never sees the lower PR's files."""
        self.stack()
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        verdict = self.gate(str(self.status().get("baseOid")))
        self.assertNotIn("lower.py", verdict.get("changedFilesSample"), "STACKED_GATE_MEASURED_OTHER_PR")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_revalidation_on_a_stacked_branch_resolves_the_same_pr_base(self) -> None:
        feat1 = self.stack()
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        command = self.bootstrap_command(mode="local", gitnexus_mode="auto", map_build="never") + ["--revalidate"]
        rerun = subprocess.run(
            command, cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertNotIn("pass base already recorded", rerun.stderr, "STACKED_REVALIDATE_MOVED_BASE")
        self.assertIn("<base_ref>refs/heads/feat1</base_ref>", rerun.stdout, "STACKED_REVALIDATE_MOVED_BASE")
        self.assertEqual(self.status().get("baseOid"), feat1, "STACKED_REVALIDATE_MOVED_BASE")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_nothing_the_lookup_later_says_replaces_the_first_recorded_base(self) -> None:
        """One pass, every perturbation that could move the answer: the config
        repointed, the origin repointed, and the base branch itself advanced.
        The first recorded OID wins each time and the rerun says so."""
        marker = "REPOINTED_LOOKUP_REPLACED_FIRST_BASE"
        feat1 = self.stack()
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), feat1, marker)

        perturbations = (
            ("config", lambda: self.git("config", "branch.feat2.gh-merge-base", "main")),
            ("origin", lambda: self.git("remote", "set-url", "origin", "https://example.invalid/moved.git")),
            ("advance", lambda: self.git("branch", "-f", "feat1", "HEAD")),
        )
        for name, perturb in perturbations:
            perturb()
            rerun = self.graph_bootstrap()
            self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
            self.assertEqual(self.status().get("baseOid"), feat1, f"{marker}: after the {name} change")
            self.assertIn(f"pass base already recorded as {feat1}", rerun.stderr, f"{marker}: {name}")

    def test_an_upstream_targeted_branch_keeps_the_producers_upstream_base(self) -> None:
        """The producer prefers upstream/main over origin/main. A branch whose PR
        goes to the parent project has no PR in the origin fork, so only its
        `gh-merge-base` config names a base; that config must not flip the pass
        onto the fork's diverged main."""
        upstream = self.git_out("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/upstream/main", upstream)
        (self.repo / "fork.py").write_text("def forked():\n    return 1\n", encoding="utf-8")
        self.git("add", "fork.py")
        self.git("commit", "-q", "-m", "the fork's main moved on")
        self.git("update-ref", "refs/remotes/origin/main", self.git_out("rev-parse", "HEAD"))
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "work.py").write_text("def work():\n    return 2\n", encoding="utf-8")
        self.git("add", "work.py")
        self.git("commit", "-q", "-m", "work for the parent project")
        self.git("config", "branch.feature.gh-merge-base", "main")

        # Which ref the adapter picks is decided in _pr_base_ref; recording that
        # ref as the base is proved once, by the stacked-branch test below.
        self.assertEqual(self.resolve_base(), "refs/remotes/upstream/main", "UPSTREAM_MAIN_PREFERENCE_LOST")

    def test_a_tag_coexisting_with_the_base_branch_is_not_measured(self) -> None:
        """git resolves a bare name through refs/tags before refs/heads, so the
        name handed to the producer must carry the namespace the adapter checked."""
        self.git("checkout", "-q", "-b", "lower")
        (self.repo / "lower.py").write_text("def lower():\n    return 1\n", encoding="utf-8")
        self.git("add", "lower.py")
        self.git("commit", "-q", "-m", "the lower branch")
        branch = self.git_out("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "leaf")
        (self.repo / "leaf.py").write_text("def leaf():\n    return 2\n", encoding="utf-8")
        self.git("add", "leaf.py")
        self.git("commit", "-q", "-m", "the leaf branch")
        # A tag of the same name, on a different commit: git prefers it for a bare name.
        self.git("tag", "lower", "HEAD")
        self.git("config", "branch.leaf.gh-merge-base", "lower")

        self.assertEqual(self.resolve_base(), "refs/heads/lower", "TAG_SHADOWED_THE_BASE_BRANCH")
        self.assertEqual(self.git_out("rev-parse", "refs/heads/lower"), branch, "TAG_SHADOWED_THE_BASE_BRANCH")

    def test_an_impostor_host_supplies_no_github_slug(self) -> None:
        """The slug pattern names GitHub itself, not any host whose name ends in
        it: a lookup bound to the wrong project asks GitHub about a repository
        this checkout does not have."""
        bootstrap = self.adapter_module()
        self.assertTrue(hasattr(bootstrap, "github_slug"), "NON_GITHUB_HOST_TREATED_AS_GITHUB")
        self.assertIsNone(
            bootstrap.github_slug("https://notgithub.com/owner/repo"),
            "NON_GITHUB_HOST_TREATED_AS_GITHUB",
        )
        for origin in ("https://github.com/owner/repo.git", "git@github.com:owner/repo.git",
                       "ssh://git@github.com/owner/repo.git"):
            self.assertEqual(bootstrap.github_slug(origin), "owner/repo", "NON_GITHUB_HOST_TREATED_AS_GITHUB")

    def test_a_github_looking_path_supplies_no_slug(self) -> None:
        """The slug comes from the origin's host, not from a segment of its path."""
        bootstrap = self.adapter_module()
        self.assertTrue(hasattr(bootstrap, "github_slug"), "ORIGIN_PATH_TREATED_AS_GITHUB_HOST")
        for origin in ("https://example.invalid/mirror/@github.com/owner/repo",
                       "https://example.invalid/github.com/owner/repo",
                       "git@example.invalid:mirror/github.com/owner/repo.git"):
            self.assertIsNone(bootstrap.github_slug(origin), "ORIGIN_PATH_TREATED_AS_GITHUB_HOST")

    def test_a_tag_sharing_the_base_name_is_not_the_base_branch(self) -> None:
        """The recorded base names the branch the PR merges into; a tag that
        happens to carry that name resolves to a commit but is not that branch."""
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "work.py").write_text("def work():\n    return 2\n", encoding="utf-8")
        self.git("add", "work.py")
        self.git("commit", "-q", "-m", "work")
        self.git("tag", "release-base", "HEAD")
        self.git("config", "branch.feature.gh-merge-base", "release-base")

        # No branch carries the name, so the configured signal answers nothing
        # and the producer's own base selection stands.
        self.assertIsNone(self.resolve_base(), "TAG_ACCEPTED_AS_BASE_BRANCH")

    def test_a_local_base_branch_whose_name_holds_a_slash_is_resolved(self) -> None:
        """The estate's branches are `fix/...`; a base that exists only locally
        must be found under refs/heads, not read as a remote and its branch."""
        self.git("checkout", "-q", "-b", "fix/lower")
        (self.repo / "lower.py").write_text("def lower():\n    return 1\n", encoding="utf-8")
        self.git("add", "lower.py")
        self.git("commit", "-q", "-m", "the lower branch")
        lower = self.git_out("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "fix/leaf")
        (self.repo / "leaf.py").write_text("def leaf():\n    return 2\n", encoding="utf-8")
        self.git("add", "leaf.py")
        self.git("commit", "-q", "-m", "the leaf branch")
        self.git("config", "branch.fix/leaf.gh-merge-base", "fix/lower")

        self.assertEqual(self.resolve_base(), "refs/heads/fix/lower", "LOCAL_SLASHED_BASE_NOT_RESOLVED")
        self.assertEqual(self.git_out("rev-parse", "refs/heads/fix/lower"), lower, "LOCAL_SLASHED_BASE_NOT_RESOLVED")

    def analysis_repo(self, output: str) -> str:
        found = re.search(r"repo=([^;\s]+)", output)
        self.assertIsNotNone(found, f"no GitNexus repo in the intake:\n{output}")
        return found.group(1)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_reruns_of_an_indexed_pass_leave_its_pass_start_index_alone(self) -> None:
        """One retained pass, four intakes. The branch delta is committed before
        the first intake, so the pass starts clean in pr mode; the reruns then
        vary only the worktree, never HEAD. Each must analyse the candidate slot
        while the index the pass started against stays the one it recorded."""
        marker = "PASS_START_INDEX_WAS_REPOPULATED"
        base = self.git_out("rev-parse", "HEAD")
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )
        self.git("add", "caller.py")
        self.git("commit", "-q", "-m", "the branch delta, committed before the pass starts")

        first = self.pr_intake(base)
        baseline = self.status().get("passStartSnapshot")
        self.assertIsInstance(baseline, dict, f"{marker}: no pass-start snapshot recorded")
        self.assertIn("<mode>pr</mode>", first, "CLEAN_RERUN_LEFT_PR_MODE")
        delta = self.packet_targets(first)
        self.assertIn("caller.py", delta, "CLEAN_RERUN_LOST_ITS_BRANCH_DELTA_TARGETS")

        # A dirty overlay, never committed: the worktree changes, HEAD does not.
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(3)\n", encoding="utf-8"
        )
        dirty = self.run_intake(self.bootstrap_command(
            mode="local", gitnexus_mode="auto", map_build="never"))
        candidate = self.analysis_repo(dirty)
        self.assertNotEqual(candidate, baseline["indexRepo"], marker)

        revalidated = self.run_intake(self.bootstrap_command(
            mode="local", gitnexus_mode="auto", map_build="never") + ["--revalidate"])
        self.assertEqual(self.analysis_repo(revalidated), candidate,
                         "REVALIDATE_TOOK_A_THIRD_CHECKOUT")

        # The overlay restored, so the pass is clean again on the same HEAD.
        self.git("checkout", "--", "caller.py")
        clean = self.pr_intake(base)
        self.assertIn("<mode>pr</mode>", clean, "CLEAN_RERUN_LEFT_PR_MODE")
        self.assertEqual(self.packet_targets(clean), delta,
                         "CLEAN_RERUN_LOST_ITS_BRANCH_DELTA_TARGETS")
        # One slot per pass, whatever the mode: the clean rerun rejoins the
        # candidate the dirty one opened rather than taking a third checkout.
        self.assertEqual(self.analysis_repo(clean), candidate, marker)

        self.assertEqual(self.status().get("passStartSnapshot"), baseline, marker)
        # The recorded pathname surviving is not the promise; the index it names
        # must still be the one the pass started against, read from the index
        # itself rather than from the workflow record that describes it.
        live = json.loads((Path(baseline["indexPath"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(str(live.get("lastCommit")), baseline["sourceCommit"], marker)
        self.assertEqual(str(live.get("indexedTree")), baseline["indexedTree"], marker)

    def pr_intake(self, base: str) -> str:
        return self.run_intake(self.bootstrap_command(
            mode="pr", gitnexus_mode="auto", map_build="never", base=base, intent=""))

    def run_intake(self, command: list[str]) -> str:
        result = subprocess.run(
            command, cwd=self.repo, env=self.env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=600,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def packet_targets(self, output: str) -> list[str]:
        """The paths the packet actually selected, not a substring of its prose."""
        block = re.search(r"<targets>(.*?)</targets>", output, re.S)
        self.assertIsNotNone(block, f"no <targets> in the intake:\n{output}")
        return re.findall(r'<file path="([^"]+)"', block.group(1))

    def adapter_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("repoforge_bootstrap_under_test", BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_branch_with_no_pr_base_signal_keeps_the_producers_base(self) -> None:
        main = self.git_out("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "feature")
        (self.repo / "feature.py").write_text("def grown():\n    return 1\n", encoding="utf-8")
        self.git("add", "feature.py")
        self.git("commit", "-q", "-m", "feature off main")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), main, "MAIN_BASED_PASS_CHANGED_BASE")


class GraphEvidenceContractTests(unittest.TestCase):
    """The producer-result contract, at the validation Interface the Adapter uses.

    The bootstrap drives identity resolution and the producer from one `--repo`, so a
    packet naming a different checkout cannot be produced through it. The check still
    has to hold, so it is exercised where it lives.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="graph-evidence-"))
        self.root = self.tmp / "repo"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def document_for(self, packet: dict[str, object], **binding: object) -> dict[str, object]:
        path = self.tmp / "packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return graph_evidence_document(
            str(path), slug="contract", workflow_id="wid", source_root=str(self.root),
            canonical_source_repo="example.invalid/workflow-fixture", **binding,
        )

    def packet(self) -> dict[str, object]:
        packet = graph_packet(str(self.root), "c" * 40, "a" * 40)
        packet["git"]["merge_base"] = "b" * 40
        packet["advisorProjection"]["sourceBaseOid"] = "b" * 40
        return packet

    def test_recorded_projection_targets_keep_only_the_advisor_fields(self) -> None:
        # Issue #191: a real projection carried 154,449 bytes of per-file ranking
        # metadata in the wrapper's format against an 8,000-byte cap.
        marker = "PROJECTION_TARGETS_UNTRIMMED"
        packet = self.packet()
        packet["advisorProjection"]["targets"] = [{
            "path": "hooks/lib/tdd_workflow.py", "surface_role": "production", "rank": 4,
            "changed_symbols": [{"name": "_map_update", "kind": "function", "line": 672, "end_line": 783}],
            "symbols": [{"name": "_map_update"}], "why_selected": ["changed in base...HEAD diff"],
            "analysis_repo": "/cache/analysis-worktrees/x", "changed_ranges": [[678, 679]],
            "cochanges": [], "dependent_count": 0, "dirty_kinds": ["unstaged"], "graph_neighbors": [],
            "in_soulforge_map": True, "intent_required_file": False, "intent_required_symbols": [],
            "line_count": 791, "pagerank": 0.159, "priority_score": 2500.0,
            "rank_signals": ["changed_file"], "scope_contaminated": True,
            "soulforge_impact": {"dependents": []}, "source_dirty_overlap": True, "symbol_count": 20,
        }]
        document = self.document_for(packet)
        self.assertNotIn("graph", document, "RCF_UNUSED_GRAPH_RETAINED")
        self.assertRegex(document["packetSha256"], r"^[0-9a-f]{64}$", "RCF_PACKET_UNREFERENCED")
        target = document["advisorProjection"]["targets"][0]
        self.assertEqual(target, {
            "path": "hooks/lib/tdd_workflow.py", "surface_role": "production", "rank": 4,
            "changed_symbols": ["_map_update"], "why_selected": ["changed in base...HEAD diff"],
        }, marker + ": " + json.dumps(target)[:300])

    def test_recorded_projection_targets_keep_why_selected(self) -> None:
        marker = "PROJECTION_WHY_SELECTED_DROPPED"
        packet = self.packet()
        packet["advisorProjection"]["targets"] = [{
            "path": "hooks/lib/tdd_workflow.py", "surface_role": "production", "rank": 4,
            "changed_symbols": [], "symbols": [], "pagerank": 0.1,
            "why_selected": ["changed in base...HEAD diff", "contains symbols overlapping changed hunks"],
        }]
        target = self.document_for(packet)["advisorProjection"]["targets"][0]
        self.assertIn("why_selected", target, marker)
        self.assertEqual(target, {
            "path": "hooks/lib/tdd_workflow.py", "surface_role": "production", "rank": 4, "changed_symbols": [],
            "why_selected": ["changed in base...HEAD diff", "contains symbols overlapping changed hunks"],
        }, marker + ": " + json.dumps(target)[:300])

    def test_a_packet_for_another_checkout_is_refused(self) -> None:
        foreign = self.tmp / "elsewhere"
        packet = self.packet()
        packet["target_state"] = {"source_repo": str(foreign)}
        with self.assertRaises(ValueError) as refusal:
            self.document_for(packet)
        # Both halves, so the refusal has to name the checkout it rejected as well
        # as the one it wanted; matching the expected root alone would survive a
        # message that never says what it actually read.
        self.assertEqual(
            str(refusal.exception),
            f"the packet was produced for {str(foreign)!r}, not {self.root}",
        )

        packet = self.packet()
        packet["gitnexus"]["analysis"]["authority"]["source_repository"] = str(foreign)
        with self.assertRaisesRegex(ValueError, str(foreign), msg="FOREIGN_GRAPH_IDENTITY_ACCEPTED"):
            self.document_for(packet)

    def test_a_projection_for_another_canonical_source_is_refused(self) -> None:
        packet = self.packet()
        packet["advisorProjection"]["sourceRepo"] = "github.com/foreign-owner/foreign-repo"
        marker = "FOREIGN_ADVISOR_SOURCE_REPO_ACCEPTED"
        with self.assertRaises(ValueError, msg=marker) as refusal:
            self.document_for(packet)
        self.assertEqual(
            str(refusal.exception),
            "the advisor projection was produced for 'github.com/foreign-owner/foreign-repo', "
            "not 'example.invalid/workflow-fixture'",
            marker,
        )

    def test_a_projection_for_another_merge_base_is_refused(self) -> None:
        packet = self.packet()
        packet["advisorProjection"]["sourceBaseOid"] = "d" * 40
        marker = "FOREIGN_SOURCE_BASE_OID_ACCEPTED"
        with self.assertRaises(ValueError, msg=marker) as refusal:
            self.document_for(packet)
        self.assertEqual(
            str(refusal.exception),
            f"the advisor projection was produced for source base {'d' * 40!r}, "
            f"not {'b' * 40!r}",
            marker,
        )

    def test_a_projection_for_another_committed_head_is_refused(self) -> None:
        packet = self.packet()
        packet["advisorProjection"]["committedHeadOid"] = "d" * 40
        marker = "FOREIGN_COMMITTED_HEAD_OID_ACCEPTED"
        with self.assertRaises(ValueError, msg=marker) as refusal:
            self.document_for(packet)
        self.assertEqual(
            str(refusal.exception),
            f"the advisor projection was produced for committed head {'d' * 40!r}, "
            f"not {'a' * 40!r}",
            marker,
        )

    def test_a_projection_for_the_packet_head_is_accepted_with_distinct_merge_base(self) -> None:
        marker = "VALID_COMMITTED_HEAD_REJECTED"
        document = self.document_for(self.packet())
        projection = document["advisorProjection"]
        self.assertEqual(projection["committedHeadOid"], "a" * 40, marker)
        self.assertEqual(projection["sourceBaseOid"], "b" * 40, marker)
        self.assertEqual(document["advisorProjection"]["graph"]["status"], "resolved", marker)
        self.assertEqual(document["workflowId"], "wid", marker)
        self.assertNotIn("gateContext", document, marker)
        self.assertNotIn("gateContextGap", document, marker)

    def test_a_diverged_base_tip_does_not_replace_merge_base_provenance(self) -> None:
        marker = "DIVERGED_BASE_TIP_REJECTED"
        document = self.document_for(
            self.packet(),
            snapshot={"base": "a" * 40, "candidate": "c" * 40},
        )
        self.assertEqual(document["advisorProjection"]["sourceBaseOid"], "b" * 40, marker)
        self.assertEqual(document["gateContext"]["base"], "a" * 40, marker)

    def test_invalid_advisor_projections_are_refused_before_recording(self) -> None:
        mutations = {
            "unsupported schema": lambda projection: projection.__setitem__("schemaVersion", 2),
            "missing producer": lambda projection: projection.__setitem__("producerRevision", {}),
            "missing source": lambda projection: projection.__setitem__("sourceRepo", ""),
            "missing base": lambda projection: projection.__setitem__("sourceBaseOid", ""),
            "candidate mismatch": lambda projection: projection.__setitem__("indexedCandidateTree", "d" * 40),
            "unresolved graph": lambda projection: projection["graph"].__setitem__("status", "blocked"),
            "required omission": lambda projection: projection["graph"].__setitem__("requiredOmissions", ["missing"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                packet = self.packet()
                projection = packet["advisorProjection"]
                self.assertIsInstance(projection, dict)
                mutate(projection)
                with self.assertRaises(ValueError):
                    self.document_for(packet)

    def test_advisory_coverage_gaps_remain_retained(self) -> None:
        packet = self.packet()
        projection = packet["advisorProjection"]
        self.assertIsInstance(projection, dict)
        projection["coverageGaps"] = [{"kind": "absent_symbol", "reference": "optional"}]
        document = self.document_for(packet)
        self.assertEqual(document["advisorProjection"]["coverageGaps"], projection["coverageGaps"])

    def test_a_snapshot_binding_records_the_gate_shaped_context(self) -> None:
        document = self.document_for(
            self.packet(),
            snapshot={"base": "b" * 40, "candidate": "c" * 40},
        )
        self.assertEqual(document["gateContext"], {
            "base": "b" * 40,
            "candidate": "c" * 40,
            "symbols": [{
                "name": "compute", "file": "app.py",
                "callers": ["Function:caller.py:run"],
            }],
        })
        self.assertNotIn("gateContextGap", document)

    def test_an_unbound_run_records_its_measured_gap_instead(self) -> None:
        document = self.document_for(
            self.packet(),
            snapshot_gap="the worktree changed during the producer run (aaaaaaaaaaaa then bbbbbbbbbbbb)",
        )
        self.assertNotIn("gateContext", document)
        self.assertIn("the worktree changed during the producer run", document["gateContextGap"])

    def test_a_binding_and_a_gap_together_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.document_for(
                self.packet(),
                snapshot={"base": "b" * 40, "candidate": "c" * 40},
                snapshot_gap="also a gap",
            )

    def test_a_binding_without_base_or_candidate_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.document_for(self.packet(), snapshot={"base": "b" * 40})


class BootstrapHelpTests(unittest.TestCase):
    def test_help_lists_the_wrapper_options(self) -> None:
        # X6R11 queried --help twice and then read the wrapper source to find these.
        marker = "WRAPPER_HELP_HIDES_ITS_OPTIONS"
        run = subprocess.run([sys.executable, str(BOOTSTRAP), "--help"], text=True, capture_output=True, check=False)
        self.assertIn("--workflow-slug", run.stdout, marker + ": " + run.stdout[-300:] + run.stderr[-300:])
        self.assertIn("--revalidate", run.stdout, marker)


class IntakeSerialisationTests(unittest.TestCase):
    """One intake at a time: GitNexus rewrites its global registry without an
    atomic replace (future3OOO/GitNexus#25), so two producers running together
    can tear it and break every later intake."""

    def serialise(self) -> None:
        """Hold the class coordinator for a case that really uses the account-wide
        slot directory or drives a real producer.

        Opt-in rather than setUp: the lock is held for a whole case, so applying
        it to every case queues the class end to end. At roughly 55s a case that
        queue outruns any deadline once a few cases sit in front of you, which is
        what SUITE_COORDINATOR_WEDGED was reporting rather than a stuck lock.
        Cases touching neither the real slot directory nor a producer need no
        coordination and run in parallel.
        """
        import fcntl

        slots = self.slot_dir()
        slots.mkdir(parents=True, exist_ok=True)
        self._coordinator = open(slots / "suite-serialisation.lock", "a+")
        self.addCleanup(self._coordinator.close)
        deadline = time.monotonic() + 900
        while True:
            try:
                fcntl.flock(self._coordinator, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.fail("SUITE_COORDINATOR_WEDGED: serialisation lock never released")
                time.sleep(0.25)

    def intake_rig(self) -> tuple[Path, list[str], dict[str, str]]:
        """A private HOME is where the real lock path lands, so this attack
        drives that computation rather than a way around it."""
        if not CANONICAL_BOOTSTRAP.is_file():
            self.skipTest("real Repo Context Forge source is unavailable")
        tmp = Path(tempfile.mkdtemp(prefix="intake-lock-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        lock = tmp / ".cache" / "repo-context-forge" / "intake.lock"
        lock.parent.mkdir(parents=True)
        env = {**os.environ, "HOME": str(tmp), "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, str(BOOTSTRAP), "--repo", str(repo), "--intent", "intake lock probe"]
        return lock, command, env

    def test_a_held_lock_stops_a_second_intake_before_its_producer(self) -> None:
        self.serialise()
        import fcntl

        marker = "HELD_LOCK_NO_LONGER_BLOCKS"
        lock, command, env = self.intake_rig()

        with open(lock, "a+", encoding="utf-8") as holder:  # closing releases the flock
            fcntl.flock(holder, fcntl.LOCK_EX)
            with self.assertRaises(subprocess.TimeoutExpired, msg=marker):
                subprocess.run(command, env=env, text=True, capture_output=True, timeout=8, check=False)

        # The held-lock budget proved blocking; the released run gets the
        # wider foreign-contention budget, since it also waits on a real
        # account slot once past the lock.
        released = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90, check=False)
        self.assertEqual(released.returncode, 1,
                         marker + ": released lock still blocked the intake: " + released.stderr[-300:])

    def producers(self, *processes: subprocess.Popen) -> list[list[int]]:
        """Every adapter's producers out of one snapshot. A ps call per adapter can
        catch one producer before a handover and its successor after, and sum the
        two readings into an overlap that never existed."""
        owners = [str(run.pid) for run in processes]
        listing = subprocess.run(["ps", "--ppid", ",".join(owners), "-o", "ppid=,pid=,args="],
                                 text=True, capture_output=True, timeout=5, check=False).stdout
        rows = [line.split(maxsplit=2) for line in listing.splitlines()]
        return [[int(pid) for parent, pid, args in rows
                 if parent == owner and str(CANONICAL_BOOTSTRAP) in args] for owner in owners]

    def stop_intake(self, process: subprocess.Popen) -> None:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.communicate(timeout=5)
            return
        process.communicate(timeout=5)

    def start_intake(self, repo: Path, home: Path, env_extra: dict[str, str | None] | None = None) -> subprocess.Popen:
        if not CANONICAL_BOOTSTRAP.is_file():
            self.skipTest("real Repo Context Forge source is unavailable")
        env = {**os.environ, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
        for name, value in (env_extra or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        process = subprocess.Popen(
            [sys.executable, str(BOOTSTRAP), "--repo", str(repo), "--mode", "repo",
             "--map-build", "never", "--gitnexus-mode", "auto"],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, pipesize=4096, start_new_session=True,
        )
        self.addCleanup(self.stop_intake, process)
        return process

    def await_producer(self, process: subprocess.Popen) -> int:
        # A foreign intake on this account may legitimately hold the low
        # slots for a whole producer lifetime; the deadline waits out a few
        # of those rather than misreporting correct admission as a failure.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            children = self.producers(process)[0]
            if children:
                return children[0]
            if process.poll() is not None:
                break
            time.sleep(0.05)
        self.fail("real intake did not start its producer within 90s")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_adapter_death_keeps_its_live_producer_locked(self) -> None:
        self.serialise()
        import fcntl
        import signal

        marker = "CANCELLED_PARENT_RELEASED_LIVE_WRITER"
        for death in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=death):
                home = Path(tempfile.mkdtemp(prefix="intake-cancel-"))
                self.addCleanup(shutil.rmtree, home, True)
                adapter = self.start_intake(ROOT, home)
                producer = self.await_producer(adapter)
                # Freeze the real producer, not a replacement, so parent death
                # cannot race its natural completion and conceal the lock loss.
                os.kill(producer, signal.SIGSTOP)
                adapter.send_signal(death)
                adapter.wait(timeout=5)
                with open(home / ".cache/repo-context-forge/intake.lock", "a+") as lock:
                    with self.assertRaises(BlockingIOError, msg=marker):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.stop_intake(adapter)

    def intake_pair(self) -> bool:
        marker = "TWO_PRODUCERS_RAN_AT_ONCE"
        started = time.monotonic()
        home = Path(tempfile.mkdtemp(prefix="intake-concurrency-"))
        self.addCleanup(shutil.rmtree, home, True)
        repos = [home / "repo-a", home / "repo-b"]
        for repo in repos:
            subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(repo)],
                           check=True, timeout=30)
        first = self.start_intake(repos[0], home)
        self.await_producer(first)
        second = self.start_intake(repos[1], home)
        peak, output_outside_lock, second_started = 0, False, False
        while time.monotonic() - started < 180:
            children = self.producers(first, second)
            peak = max(peak, sum(map(len, children)))
            second_started = second_started or bool(children[1])
            if first.poll() is None and not children[0] and children[1]:
                output_outside_lock = True
            if second_started and not any(children):
                # Both adapters may be blocked on their real output pipes now.
                break
            time.sleep(0.05)
        else:
            self.fail(marker + ": real intakes exceeded 180s")
        paths = []
        for run in (first, second):
            stdout, stderr = run.communicate(timeout=30)
            self.assertEqual(run.returncode, 0, marker + ": " + stderr.decode()[-300:])
            match = re.search(rb"<analysis_repo>([^<]+)</analysis_repo>", stdout)
            self.assertIsNotNone(match, marker + ": no analysis identity")
            paths.append(match.group(1).decode())
        self.assertEqual(peak, 1, marker + f": {peak} producers were alive at once")
        registry = json.loads((home / ".gitnexus/registry.json").read_text())
        self.assertEqual(sorted(row["path"] for row in registry), sorted(paths), marker)
        listing = subprocess.run([GITNEXUS, "list"], env={**os.environ, "HOME": str(home)},
                                 text=True, capture_output=True, timeout=30, check=True)
        for row in registry:
            self.assertIn(row["name"], listing.stdout, marker)
        elapsed = time.monotonic() - started
        print(f"INTAKE_RESOURCE target={ROOT} scale=2-full-repos limit=180s observed={elapsed:.3f}s peak={peak}")
        self.assertLess(elapsed, 180, marker)
        return output_outside_lock

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_two_real_intakes_never_run_two_producers(self) -> None:
        self.serialise()
        self.intake_pair()

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_output_does_not_hold_the_producer_lock(self) -> None:
        self.serialise()
        self.assertTrue(self.intake_pair(), "OUTPUT_HELD_PRODUCER_LOCK")

    def clone(self, parent: Path, name: str) -> Path:
        repo = parent / name
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(repo)],
                       check=True, timeout=30)
        return repo

    def available_mb(self) -> int:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        return 0

    def slot_dir(self) -> Path:
        """The capacity directory's contract path: the real account home, which
        a fixture HOME cannot relocate — the bound guards host memory, and host
        memory does not isolate with $HOME."""
        import pwd

        return (
            Path(pwd.getpwuid(os.getuid()).pw_dir)
            / ".cache" / "repo-context-forge" / "intake-slots"
        )

    def held_paths(self, process: subprocess.Popen, directory: Path) -> list[str]:
        """Paths under `directory` the process holds open right now, via /proc."""
        held = []
        try:
            for fd in Path(f"/proc/{process.pid}/fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if str(target).startswith(str(directory)):
                    held.append(str(target))
        except OSError:
            pass
        return held

    def foreign_slot_holders(self, children: list[list[int]], *adapters: subprocess.Popen) -> set[int]:
        """Pids outside these adapters' own trees holding a real slot lock.

        The capacity files live in the account home, so an unrelated intake on
        this host can legitimately shrink the permits a test sees; a cap-2
        assertion may only relax when that contention is observed, not assumed.
        Takes the caller's producer snapshot so the check adds no extra `ps`
        gap between the census and any same-iteration fd inspection.
        """
        inodes = {path.stat().st_ino for path in self.slot_dir().glob("slot-*.lock")}
        ours = {adapter.pid for adapter in adapters}
        for prods in children:
            ours.update(prods)
        foreign = set()
        try:
            for line in Path("/proc/locks").read_text().splitlines():
                fields = line.split()
                # "N: FLOCK  ADVISORY  WRITE  <pid> <maj>:<min>:<ino> ..."
                if len(fields) < 6 or fields[1] != "FLOCK":
                    continue
                try:
                    pid, inode = int(fields[4]), int(fields[5].rsplit(":", 1)[-1])
                except ValueError:
                    continue
                if inode in inodes and pid not in ours:
                    foreign.add(pid)
        except OSError:
            pass
        return foreign

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_isolated_home_intakes_respect_the_machine_bound(self) -> None:
        """Three isolated-HOME intakes under a cap of two: a bound, not a mutex."""
        self.serialise()
        marker = "CROSS_HOME_PRODUCER_BOUND_BROKEN"
        if self.available_mb() < 9400:
            self.skipTest("needs headroom for two real producers")
        tmp = Path(tempfile.mkdtemp(prefix="intake-bound-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cap = {"RCF_INTAKE_MAX_PARALLEL": "2"}
        repos = [self.clone(tmp, f"repo-{name}") for name in "abc"]
        homes = [tmp / f"home-{name}" for name in "abc"]
        started = time.monotonic()
        adapters = [self.start_intake(repo, home, cap) for repo, home in zip(repos, homes)]
        peak, seen, done, foreign = 0, set(), False, set()
        while time.monotonic() - started < 300:
            children = self.producers(*adapters)
            for prods in children:
                seen.update(prods)
            peak = max(peak, sum(map(len, children)))
            foreign.update(self.foreign_slot_holders(children, *adapters))
            if len(seen) == 3 and not any(children):
                # All producers observed and gone; the adapters only remain
                # to drain packet output through the narrow test pipes.
                done = True
                break
            time.sleep(0.05)
        finished = time.monotonic()
        for adapter in adapters:
            stdout, stderr = adapter.communicate(timeout=30)
            self.assertEqual(adapter.returncode, 0, marker + ": " + stderr.decode()[-300:])
        self.assertTrue(done, marker + ": producers outlived the 300s poll window")
        self.assertLessEqual(peak, 2, marker + f": {peak} producers exceeded cap 2")
        if peak < 2:
            if foreign:
                self.skipTest(marker + f": foreign intake held {sorted(foreign)}; permits were below cap")
            self.fail(marker + f": only {peak} producers ran at once under cap 2")
        for home in homes:
            registry = json.loads((home / ".gitnexus/registry.json").read_text())
            self.assertEqual(len(registry), 1, marker + ": per-home registry lost its intake")
        observed = finished - started
        print(f"INTAKE_RESOURCE target={ROOT} scale=3-full-repos limit=300s observed={observed:.3f}s peak={peak}")
        self.assertLess(observed, 300, marker)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_held_machine_slot_stops_a_new_intakes_producer(self) -> None:
        """An externally held permit blocks a fresh-HOME intake before its producer."""
        self.serialise()
        import fcntl

        marker = "ADMITTED_WHILE_SLOTS_HELD"
        slots = self.slot_dir()
        slots.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="intake-admission-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = self.clone(tmp, "repo")
        with open(slots / "slot-0.lock", "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            adapter = self.start_intake(repo, tmp / "home", {"RCF_INTAKE_MAX_PARALLEL": "1"})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                self.assertFalse(self.producers(adapter)[0],
                                 marker + ": producer started while slot-0 was held")
                self.assertIsNone(adapter.poll(), marker + ": adapter died instead of waiting")
                time.sleep(0.05)
        self.await_producer(adapter)
        adapter.communicate(timeout=30)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_adapter_death_keeps_its_live_producers_slot(self) -> None:
        """An orphaned producer retains its capacity reservation until it dies."""
        self.serialise()
        import signal

        marker = "ORPHANED_PRODUCER_LOST_CAPACITY"
        tmp = Path(tempfile.mkdtemp(prefix="intake-orphan-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cap = {"RCF_INTAKE_MAX_PARALLEL": "1"}
        home_a, home_b = tmp / "home-a", tmp / "home-b"
        repo_a, repo_b = self.clone(tmp, "repo-a"), self.clone(tmp, "repo-b")
        first = self.start_intake(repo_a, home_a, cap)
        producer = self.await_producer(first)
        # Freeze the real producer so parent death cannot race its natural
        # completion, then kill only the adapter: the slot must stay held.
        os.kill(producer, signal.SIGSTOP)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
        second = self.start_intake(repo_b, home_b, cap)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.assertFalse(self.producers(second)[0],
                             marker + ": second producer started while the orphan held the slot")
            time.sleep(0.05)
        os.killpg(first.pid, signal.SIGKILL)
        self.await_producer(second)
        second.communicate(timeout=60)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_queued_same_home_waiter_holds_no_capacity(self) -> None:
        """Capacity is taken only after the HOME lock is won: under cap 2 with
        homes A,A,B, B runs beside A1 while A2 queues holding nothing."""
        self.serialise()
        marker = "HOME_WAITER_HELD_CAPACITY"
        if self.available_mb() < 9400:
            self.skipTest("needs headroom for two real producers")
        tmp = Path(tempfile.mkdtemp(prefix="intake-order-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cap = {"RCF_INTAKE_MAX_PARALLEL": "2"}
        home_a, home_b = tmp / "home-a", tmp / "home-b"
        repo_a1 = self.clone(tmp, "repo-a1")
        repo_a2 = self.clone(tmp, "repo-a2")
        repo_b = self.clone(tmp, "repo-b")
        started = time.monotonic()
        first_a = self.start_intake(repo_a1, home_a, cap)
        self.await_producer(first_a)
        second_a = self.start_intake(repo_a2, home_a, cap)
        third = self.start_intake(repo_b, home_b, cap)
        peak, overlap_while_queued, seen, done, foreign = 0, False, set(), False, set()
        while time.monotonic() - started < 300:
            children = self.producers(first_a, second_a, third)
            for prods in children:
                seen.update(prods)
            peak = max(peak, sum(map(len, children)))
            self.assertFalse(children[0] and children[1],
                             marker + ": same-home producers overlapped")
            # While A1's producer lives, A1's adapter still holds the HOME
            # lock, so a correctly queued A2 cannot have reached the slots:
            # any slot fd it holds is proof of the wrong acquisition order.
            # A held fd read races the producer census by a few ms — A1's
            # producer may have exited and released the lock since — so only
            # a hold that survives a fresh census counts.
            if children[0] and not children[1]:
                held = self.held_paths(second_a, self.slot_dir())
                if held:
                    recheck = self.producers(first_a, second_a, third)
                    self.assertFalse(recheck[0] and not recheck[1],
                                     marker + f": queued waiter holds {held}")
            foreign.update(self.foreign_slot_holders(children, first_a, second_a, third))
            if children[0] and children[2] and not children[1]:
                overlap_while_queued = True
            if len(seen) == 3 and not any(children):
                # All producers observed and gone; the adapters only remain
                # to drain packet output through the narrow test pipes.
                done = True
                break
            time.sleep(0.05)
        for adapter in (first_a, second_a, third):
            stdout, stderr = adapter.communicate(timeout=30)
            self.assertEqual(adapter.returncode, 0, marker + ": " + stderr.decode()[-300:])
        self.assertTrue(done, marker + ": producers outlived the 300s poll window")
        self.assertLessEqual(peak, 2, marker + f": {peak} producers exceeded cap 2")
        if peak < 2 and foreign:
            self.skipTest(marker + f": foreign intake held {sorted(foreign)}; permits were below cap")
        self.assertTrue(overlap_while_queued,
                        marker + ": B never ran while A2 queued on the HOME lock")
        self.assertEqual(peak, 2, marker + f": {peak} producers ran at once under cap 2")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_isolated_homes_may_run_producers_concurrently(self) -> None:
        """The bound is not a mutex: under default permits with real headroom,
        two isolated-HOME intakes overlap — the anti-serialisation contract."""
        self.serialise()
        marker = "CROSS_HOME_OVERLAP_DENIED"
        if self.available_mb() < 9400:
            self.skipTest("needs headroom for two real producers")
        tmp = Path(tempfile.mkdtemp(prefix="intake-overlap-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        no_cap = {"RCF_INTAKE_MAX_PARALLEL": None}
        repos = [self.clone(tmp, f"repo-{name}") for name in "ab"]
        homes = [tmp / f"home-{name}" for name in "ab"]
        started = time.monotonic()
        first = self.start_intake(repos[0], homes[0], no_cap)
        self.await_producer(first)
        second = self.start_intake(repos[1], homes[1], no_cap)
        peak, windows, done, foreign = 0, {}, False, set()
        while time.monotonic() - started < 300:
            now = time.monotonic()
            children = self.producers(first, second)
            foreign.update(self.foreign_slot_holders(children, first, second))
            for owner, prods in zip((first, second), children):
                for pid in prods:
                    windows.setdefault((owner.pid, pid), [now, now])[1] = now
            peak = max(peak, sum(map(len, children)))
            if len(windows) == 2 and not any(children):
                # Both producers observed and gone; the adapters only remain
                # to drain their packet output through the narrow test pipes.
                done = True
                break
            time.sleep(0.05)
        finished = time.monotonic()  # producer-window end, before adapter drain
        for adapter in (first, second):
            stdout, stderr = adapter.communicate(timeout=30)
            self.assertEqual(adapter.returncode, 0, marker + ": " + stderr.decode()[-300:])
        self.assertTrue(done, marker + ": producers outlived the 300s poll window")
        self.assertLessEqual(peak, 2, marker + f": {peak} producers exceeded default permits")
        if peak < 2:
            if foreign:
                self.skipTest(marker + f": foreign intake held {sorted(foreign)}; permits were below cap")
            self.fail(marker + f": only {peak} producers ran under default permits")
        # Measured producer lifetimes, not adapter wall-clock: the producers'
        # [first-seen, last-seen] windows must overlap, and their union span
        # must beat the serialized floor sum(lifetimes). A serialised run —
        # cap 1 or a mutex — makes span ~= sum and fails this bound.
        self.assertEqual(len(windows), 2, marker + f": producer windows {windows}")
        (start_a, end_a), (start_b, end_b) = windows.values()
        self.assertLess(max(start_a, start_b), min(end_a, end_b),
                        marker + ": producer lifetimes never overlapped")
        lifetimes = (end_a - start_a) + (end_b - start_b)
        span = max(end_a, end_b) - min(start_a, start_b)
        self.assertLess(span, 0.9 * lifetimes,
                        marker + f": span {span:.1f}s ~= serialized {lifetimes:.1f}s")
        observed = finished - started  # measured producer window, adapter drain excluded
        print(f"INTAKE_RESOURCE target={ROOT} scale=2-full-repos limit=300s observed={observed:.3f}s peak={peak}")
        self.assertLess(observed, 300, marker)

    def load_intake_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("rcf_intake_bootstrap", BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_higher_slots_count_during_competing_admissions(self) -> None:
        import fcntl

        marker = "HELD_HIGHER_SLOTS_IGNORED"
        module = self.load_intake_module()
        tmp = Path(tempfile.mkdtemp(prefix="intake-higher-slots-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        meminfo = tmp / "meminfo"
        meminfo.write_text("MemAvailable: 9625600 kB\n")  # two permits
        module._MEMINFO = meminfo
        # The permit count is faked, so the real account's slot directory would let a
        # producer in another shard hold one of these two permits and admit nobody. The
        # property under attack is that a held higher slot counts, not where the slot
        # root lands, so this loaded module gets its own root.
        module._real_home = lambda: tmp
        slots = tmp / ".cache" / "repo-context-forge" / "intake-slots"
        slots.mkdir(parents=True)
        original_cap = os.environ.pop("RCF_INTAKE_MAX_PARALLEL", None)
        acquired: list[int] = []
        barrier = threading.Barrier(3)

        def acquire() -> None:
            barrier.wait(timeout=5)
            acquired.append(module._acquire_intake_slot())

        threads = [threading.Thread(target=acquire, daemon=True) for _ in range(2)]
        with open(slots / "slot-2.lock", "a+") as high:
            fcntl.flock(high, fcntl.LOCK_EX)
            try:
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=5)
                deadline = time.monotonic() + 10
                while not acquired and time.monotonic() < deadline:
                    time.sleep(0.05)
                time.sleep(0.6)  # cover multiple admission polls while the high slot stays held
                self.assertEqual(len(acquired), 1, marker)
                high.close()
                for thread in threads:
                    thread.join(timeout=5)
                self.assertEqual(len(acquired), 2, marker + ": release failed to admit the waiter")
            finally:
                high.close()
                for thread in threads:
                    thread.join(timeout=5)
                for fd in acquired:
                    os.close(fd)
                if original_cap is not None:
                    os.environ["RCF_INTAKE_MAX_PARALLEL"] = original_cap

    def test_coordinator_covers_registered_cleanups(self) -> None:
        self.serialise()
        import fcntl

        def check_cleanup() -> None:
            with open(self.slot_dir() / "suite-serialisation.lock", "a+") as probe:
                with self.assertRaises(BlockingIOError, msg="COORDINATOR_RELEASED_BEFORE_CLEANUP"):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

        self.addCleanup(check_cleanup)

    def test_cancelled_admission_waiter_allows_reentry(self) -> None:
        self.serialise()
        import fcntl

        marker = "ADMISSION_WAITER_DID_NOT_WAIT"
        command = [sys.executable, "-c",
                   "import os,runpy; ns=runpy.run_path(" + repr(str(BOOTSTRAP)) + "); "
                   "fd=ns['_acquire_intake_slot'](); print('ADMITTED'); os.close(fd)"]
        env = {**os.environ, "RCF_INTAKE_MAX_PARALLEL": "1"}
        with open(self.slot_dir() / "admission.lock", "a+") as admission:
            fcntl.flock(admission, fcntl.LOCK_EX)
            child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                with self.assertRaises(subprocess.TimeoutExpired, msg=marker):
                    child.wait(timeout=0.3)
                child.terminate()
                child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)
        later = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(later.returncode, 0, marker + ": " + later.stderr)
        self.assertIn("ADMITTED", later.stdout, marker)

    def test_permit_count_follows_available_memory(self) -> None:
        """The permit arithmetic itself: bounded below at 1, by the cap, and by
        (MemAvailable - reserve) / producer peak."""
        marker = "PERMIT_FORMULA_WRONG"
        module = self.load_intake_module()
        self.assertTrue(hasattr(module, "_permits_for_available"), marker)
        cases = [
            (24000, None, 10),
            (19380, None, 7),
            (9400, None, 2),
            (7700, None, 1),
            (5000, None, 1),
            (24000, 1, 1),
            (50000, 3, 3),
        ]
        for available, cap, want in cases:
            self.assertEqual(module._permits_for_available(available, cap), want,
                             f"{marker}: avail={available} cap={cap}")
        self.assertGreaterEqual(module._intake_permits(), 1, marker)

    def test_unreadable_meminfo_fails_closed_to_one_permit(self) -> None:
        """A lost meminfo source must narrow admission, never widen it: the
        fallback is one permit even under an explicit higher cap. The seam is
        the real file read — _MEMINFO is pointed at real missing and
        MemAvailable-less files so the real OSError/StopIteration path runs."""
        marker = "MEMINFO_FALLBACK_NOT_CLOSED"
        module = self.load_intake_module()
        tmp = Path(tempfile.mkdtemp(prefix="intake-meminfo-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        meminfo = tmp / "meminfo"
        original_path, original_cap = module._MEMINFO, os.environ.get("RCF_INTAKE_MAX_PARALLEL")
        try:
            module._MEMINFO = meminfo
            for cap in (None, "2"):
                if cap is None:
                    os.environ.pop("RCF_INTAKE_MAX_PARALLEL", None)
                else:
                    os.environ["RCF_INTAKE_MAX_PARALLEL"] = cap
                self.assertEqual(module._intake_permits(), 1,
                                 f"{marker}: missing meminfo with cap={cap}")
            meminfo.write_text("MemTotal:       32768 kB\n", encoding="utf-8")
            self.assertEqual(module._intake_permits(), 1,
                             marker + ": meminfo without a MemAvailable line")
        finally:
            module._MEMINFO = original_path
            if original_cap is None:
                os.environ.pop("RCF_INTAKE_MAX_PARALLEL", None)
            else:
                os.environ["RCF_INTAKE_MAX_PARALLEL"] = original_cap

    def test_a_shrinking_permit_count_narrows_admission(self) -> None:
        """Every poll re-reads the live permit count: once MemAvailable shrinks
        below the slots a waiter could otherwise take, those slots are out of
        range; restoring headroom lets the same call proceed. The seam is the
        real flock on real slot files plus a real meminfo file — held slots,
        shrink, release, and re-entry all run against the production code in
        this process."""
        self.serialise()
        import fcntl

        marker = "SHRUNK_CAPACITY_STILL_ADMITTED"
        module = self.load_intake_module()
        slots = self.slot_dir()
        slots.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="intake-shrink-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        meminfo = tmp / "meminfo"

        def available(mb: int) -> None:
            meminfo.write_text(f"MemAvailable:    {mb * 1024} kB\n", encoding="utf-8")

        original = module._MEMINFO
        module._MEMINFO = meminfo
        # An inherited caller cap would stay clamped over every meminfo
        # reading; this test owns the whole range.
        original_cap = os.environ.pop("RCF_INTAKE_MAX_PARALLEL", None)
        acquired: list[int] = []
        held: list = []
        waiter: threading.Thread | None = None
        try:
            # Three permits, all in range and all held: the waiter blocks.
            available(11500)
            held = [open(slots / f"slot-{index}.lock", "a+") for index in range(3)]
            for handle in held:
                fcntl.flock(handle, fcntl.LOCK_EX)
            waiter = threading.Thread(target=lambda: acquired.append(module._acquire_intake_slot()),
                                      daemon=True)
            waiter.start()
            # The budget shrinks to one permit; slot-1 and slot-2 leave the
            # range. Freeing out-of-range slot-1 must not unblock the waiter —
            # only slot-0 counts now, and it is still held — while slot-2
            # stays occupied through the transition. Let the waiter re-poll
            # under the shrunk count first: a waiter still running a stale
            # three-permit iteration could take the freed slot legitimately.
            available(5000)
            time.sleep(0.6)
            held[1].close()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                self.assertFalse(acquired, marker + ": waiter took a slot outside the shrunk range")
                time.sleep(0.05)
            self.assertTrue(waiter.is_alive(), marker + ": waiter exited without a slot")
            # Recovery: headroom returns, slot-1 is back in range and free.
            available(24000)
            waiter.join(timeout=10)
            self.assertFalse(waiter.is_alive(), marker + ": waiter never resumed after headroom returned")
            self.assertEqual(len(acquired), 1, marker)
            target = Path(os.readlink(f"/proc/self/fd/{acquired[0]}"))
            self.assertTrue(str(target).startswith(str(slots)), marker + f": held {target}")
            inode = target.stat().st_ino
            os.close(acquired[0])
            # Re-entry: the same slot inode persists (never unlinked) and
            # re-locks at once — a recreated file would carry a new inode.
            self.assertTrue(target.exists(), marker + ": slot file was unlinked")
            self.assertEqual(target.stat().st_ino, inode, marker + ": slot file was recreated")
            with open(target, "a+") as again:
                fcntl.flock(again, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held[0].close()
        finally:
            module._MEMINFO = original
            if original_cap is not None:
                os.environ["RCF_INTAKE_MAX_PARALLEL"] = original_cap
            for handle in held:
                try:
                    handle.close()
                except OSError:
                    pass
            if waiter is not None:
                waiter.join(timeout=5)
            for fd in acquired:
                try:
                    os.close(fd)
                except OSError:
                    pass

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_killing_a_queued_waiter_releases_everything_it_held(self) -> None:
        """A same-HOME waiter killed while queued on the HOME lock leaves no
        residue: it never reached the slots, and its death frees the flock it
        was blocked on without touching A1's producer or capacity."""
        self.serialise()
        import fcntl
        import signal

        marker = "KILLED_WAITER_LEFT_RESIDUE"
        tmp = Path(tempfile.mkdtemp(prefix="intake-cancel-queue-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cap = {"RCF_INTAKE_MAX_PARALLEL": "1"}
        home = tmp / "home"
        repo_a1 = self.clone(tmp, "repo-a1")
        repo_a2 = self.clone(tmp, "repo-a2")
        first = self.start_intake(repo_a1, home, cap)
        producer = self.await_producer(first)
        os.kill(producer, signal.SIGSTOP)
        queued = self.start_intake(repo_a2, home, cap)
        time.sleep(2)  # reach the HOME-lock wait
        self.assertIsNone(queued.poll(), marker + ": queued waiter died on its own")
        self.assertFalse(self.held_paths(queued, self.slot_dir()),
                         marker + ": queued waiter held a slot before dying")
        queued.send_signal(signal.SIGKILL)
        queued.wait(timeout=5)
        # The capacity picture is unchanged: A1's producer still holds the only
        # permit; nothing the dead waiter touched survives it.
        with open(self.slot_dir() / "slot-0.lock", "a+") as probe:
            with self.assertRaises(BlockingIOError, msg=marker):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.producers(first)[0], [producer],
                         marker + ": first intake lost its producer")
        os.killpg(first.pid, signal.SIGKILL)
        first.wait(timeout=5)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_producer_death_releases_its_slot_for_a_surviving_indexer(self) -> None:
        """The reservation is adapter+producer scoped: the producer's own
        children do not inherit the slot fd (the upstream spawn keeps
        close_fds), so a killed producer frees capacity even while a frozen
        indexer outlives it — the residue is the orphan's bounded remainder,
        not a stranded permit."""
        self.serialise()
        import fcntl
        import signal

        marker = "PRODUCER_DEATH_HELD_CAPACITY"
        tmp = Path(tempfile.mkdtemp(prefix="intake-pdeath-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home = tmp / "home"
        repo = self.clone(tmp, "repo")
        adapter = self.start_intake(repo, home, {"RCF_INTAKE_MAX_PARALLEL": "1"})
        producer = self.await_producer(adapter)
        indexer = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and indexer is None:
            listing = subprocess.run(["ps", "--ppid", str(producer), "-o", "pid=,args="],
                                     text=True, capture_output=True, timeout=5).stdout
            for row in listing.splitlines():
                pid, _, args = row.strip().partition(" ")
                if "gitnexus" in args:
                    indexer = int(pid)
                    break
            if adapter.poll() is not None:
                break
            time.sleep(0.5)
        if indexer is None:
            os.killpg(adapter.pid, signal.SIGKILL)
            adapter.wait(timeout=5)
            self.skipTest("the real producer never spawned a GitNexus indexer")
        os.kill(indexer, signal.SIGSTOP)
        os.kill(producer, signal.SIGKILL)
        # The dead producer's reservation is gone even though its frozen
        # indexer still holds memory — capacity tracks the producer, and the
        # orphan finishes its bounded work without blocking admission.
        freed = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not freed:
            with open(self.slot_dir() / "slot-0.lock", "a+") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    freed = True
                except BlockingIOError:
                    time.sleep(0.1)
        try:
            os.kill(indexer, 0)
            indexer_alive = True
        except ProcessLookupError:
            indexer_alive = False
        self.assertTrue(indexer_alive, marker + ": indexer died before the measurement")
        self.assertTrue(freed, marker + ": dead producer still held its slot")
        os.killpg(adapter.pid, signal.SIGKILL)
        adapter.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
