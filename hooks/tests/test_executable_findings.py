"""Direct probes of executable findings: each calls the changed function on a two-file repository."""
from __future__ import annotations

import difflib
import inspect
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hooks.lib import tdd_workflow, workflow_documents, workflow_state
from hooks.lib._workflow_db import evidence_write, manifest_write
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.state_store import tree_manifest

ROOT = Path(__file__).resolve().parents[2]
APP = "value = 2\n\ndef unused():\n    return 3\n\ndef total(a, b):\n    return a + b\n"
BATCH = ("import app, unittest\nclass B(unittest.TestCase):\n"
         "    def test_value(self): self.assertEqual(app.value, 2)\n"
         "    def test_total(self): self.assertEqual(app.total(1, 2), 3)\n")


STILL_FAILS = {"id": "F", "claim": "still fails", "material": True, "kind": "behavioral"}


class Repo(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "repo"
        self.repo.mkdir()
        previous = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(Path(temporary.name) / "state")
        self.addCleanup(lambda: os.environ.pop("CODEX_WORKFLOW_STATE_ROOT") if previous is None
                        else os.environ.__setitem__("CODEX_WORKFLOW_STATE_ROOT", previous))
        self.git("init", "-q")
        self.original = self.tree({"app.py": APP})
        self.git("-c", "user.email=p@p", "-c", "user.name=p", "commit", "-qm", "base")
        self.identity = resolve_repo_identity(self.repo)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def tree(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        return self.git("write-tree")

    def probe(self, name: str, body: str, head: str = "import app, unittest\n") -> dict[str, str]:
        return {"path": f"test_{name}.py", "command": f"{sys.executable} -m unittest test_{name}.P.test_it",
                "content": head + "class P(unittest.TestCase):\n" + body}

    def change(self, old: str, new: str, path: str = "app.py") -> str:
        return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))

    def batch(self, tree: str, timeout: float = 10.0) -> dict[str, object]:
        return {"command": shlex.join([sys.executable, "-m", "unittest", "test_batch.B.test_value"]), "candidateTree": tree,
                "probeFiles": ["test_batch.py"], "timeout": timeout, "named": {}}


class Binds(unittest.TestCase):
    def test_binds(self) -> None:
        for execution, bound in (({"kind": "probe", "candidate": "failed"}, True), ({"kind": "probe", "candidate": "passed"}, False),
                                 ({"kind": "probe", "candidate": "incomplete"}, False), ({"kind": "probe", "candidate": "timed-out"}, True),
                                 ({"kind": "change", "candidate": "passed"}, True), ({"kind": "change", "candidate": "failed"}, False),
                                 ({"kind": "change", "candidate": "timed-out"}, True), ({"kind": "change", "error": "x"}, False),
                                 ({"kind": "claim", "error": "no probe or change: a report line"}, False)):
            self.assertEqual(tdd_workflow.binds(execution), bound, execution)


class FinalEnvelope(unittest.TestCase):
    def test_a_contract_without_objective_adds_and_removes_is_refused(self) -> None:
        draft = Path(tempfile.mkdtemp()) / "preflight.json"
        self.addCleanup(draft.unlink)
        for contract, refused in (("keep the rest", True), ("Objective: o\nRemoves: r\nAdds: a", True), ("Objective\nAdds\nRemoves", True),
                                  ("Objective: o\nAdds: nothing\nRemoves", True), ("Objective: o\nAdds: a", True),
                                  ("Objective: o\nAdds: nothing\nRemoves: `b` (app.py:2)\nmore", False)):
            draft.write_text('{"authoritativeContract": %s, "behaviorMap": []}' % repr(contract).replace("'", '"'))
            with self.subTest(contract=contract):
                if refused:
                    with self.assertRaisesRegex(ValueError, "must open with Objective:, Adds: and Removes: lines"):
                        workflow_documents.preflight_document(str(draft))
                else:
                    self.assertEqual(workflow_documents.preflight_document(str(draft))["authoritativeContract"], contract)

    def test_a_final_finding_keeps_only_executable_evidence(self) -> None:
        envelope = Path(tempfile.mkdtemp()) / "final.json"
        self.addCleanup(envelope.unlink)
        envelope.write_text('{"schemaVersion": 1, "verdict": "fix-before-commit", "findings": [{"id": "F", "claim": "c", '
                            '"material": true, "fixSketch": {"change": "x", "probe": "y"}, "probe": {"path": "test_f.py"}}, '
                            '{"id": "G", "claim": "c", "material": false, "change": "x"}]}')
        document = workflow_documents.advisor_envelope(str(envelope), slug="s", workflow_id="w", stage="final", producer="codex-advisor")[0]
        self.assertEqual([sorted(finding) for finding in document["findings"]],
                         [["claim", "id", "kind", "material", "probe"], ["change", "claim", "id", "kind", "material"]])

    def test_the_advisor_result_shape_names_each_stage_finding_and_verdicts(self) -> None:
        for part in ("at preflight FINDING is", "VERDICT approved|changes-required|completed", "at final FINDING is "
                     + workflow_documents.EXECUTABLE, "VERDICT commit-ready|fix-before-commit|context-mismatch"):
            self.assertIn(part, workflow_documents.RECORD_SHAPES["advisor-result"])

    def test_advisor_result_help_keeps_preflight_and_unavailable_forms(self) -> None:
        for part in ('{"id":"SPEC-1","claim":"...","material":true}', "--verdict unavailable --reason TEXT"):
            self.assertIn(part, workflow_documents.RECORD_SHAPES["advisor-result"])


class NamedSelection(unittest.TestCase):
    """_named_command adds only the tests, in a file named for a changed function, that name the function."""

    def selected(self, runner: list[str], files: dict[str, str], named: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        for name, text in files.items():
            Path(root.name, name).parent.mkdir(parents=True, exist_ok=True)
            Path(root.name, name).write_bytes(text if isinstance(text, bytes) else text.encode())
        command = [sys.executable, "-m", *runner]
        return tdd_workflow._named_command(tdd_workflow.tdd_surface.identify(command), command, named, root.name)

    def test_a_file_whose_tests_never_name_the_function_adds_nothing(self) -> None:
        command = ["unittest", "test_batch.B.test_value"]
        files = {"test_batch.py": BATCH, "hooks/tests/test_review_summary.py": (ROOT / "hooks/tests/test_review_summary.py").read_text()}
        self.assertEqual(self.selected(command, files, {"hooks/tests/test_review_summary.py": "review_summary"})[1], {})
        unit = "import unittest\nfrom app import total\nclass T(unittest.TestCase):\n    def test_sum(self): total(1, 2)\n    def test_other(self): pass\n"
        self.assertEqual(self.selected(command, {**files, "test_total.py": unit}, {"test_total.py": "total"}),
                         ([sys.executable, "-m", *command, "test_total.T.test_sum"], {"test_total.T.test_sum": "total"}))

    def test_a_named_file_that_does_not_parse_is_a_workflow_error(self) -> None:
        for broken in ("def test_sum(:\n", b"\xff\n"):
            with self.subTest(broken=broken):
                with self.assertRaises(Exception) as caught:
                    self.selected(["unittest", "test_batch.B.test_value"], {"test_batch.py": BATCH, "test_total.py": broken},
                                  {"test_total.py": "total"})
                self.assertIsInstance(caught.exception, workflow_state.WorkflowError)
                self.assertRegex(str(caught.exception), "test_total.py.*does not parse")


class RunProbe(Repo):
    def test_outcomes(self) -> None:
        candidate = self.tree({"app.py": APP.replace("value = 2", "value = 1")})
        cases = {"regression": ("    def test_it(self): self.assertEqual(app.value, 2)\n", "passed", "failed"),
                 "behaviour": ("    def test_it(self): self.assertTrue(hasattr(app, 'note'))\n", "failed", "failed"),
                 "passes": ("    def test_it(self): self.assertEqual(app.total(1, 2), 3)\n", "passed", "passed"),
                 "operation": ("    def test_it(self): self.assertEqual({2: 'two'}[app.value], 'two')\n", "passed", "failed"),
                 "setup": ("    def setUp(self): raise RuntimeError\n    def test_it(self): pass\n", "incomplete", "incomplete")}
        seen = {name: tdd_workflow._run_probe(self.identity, self.probe(name, body), candidate, self.original)
                for name, (body, *_) in cases.items()}
        self.assertEqual({name: (run.get("original"), run.get("candidate")) for name, run in seen.items()},
                         {name: tuple(expected) for name, (_, *expected) in cases.items()}, "PROBE_OUTCOME")

    def test_refusals(self) -> None:
        candidate = self.tree({"test_batch.py": BATCH})
        good = self.probe("x", "    def test_it(self): pass\n")
        for probe in ({**good, "path": "check_x.py"}, {**good, "path": "test_batch.py"}, {**good, "path": "test_\0x.py"},
                      {**good, "path": "app.py/test_x.py"}, {**good, "command": "python3 -m unittest 'test_x.P.test_it"},
                      {**good, "command": "echo test_x"}, {**good, "content": ""}, "not a probe"):
            run = tdd_workflow._run_probe(self.identity, probe, candidate, self.original)
            self.assertEqual((sorted(run), tdd_workflow.binds(run)), (["error", "kind"], False), probe)

    def test_a_slow_probe_is_a_visible_binding_timeout(self) -> None:
        self.addCleanup(setattr, tdd_workflow, "PROBE_TIMEOUT", getattr(tdd_workflow, "PROBE_TIMEOUT", None))
        tdd_workflow.PROBE_TIMEOUT = 0.5
        run = tdd_workflow._run_probe(self.identity, self.probe("slow", "    def test_it(self): import time; time.sleep(3)\n"),
                                      self.original, None)
        self.assertEqual((run.get("candidate"), tdd_workflow.binds(run)), ("timed-out", True), run)
        self.assertIn("not a direct probe; rewrite it to call the changed code", run["timedOut"])


class RunChange(Repo):
    def test_bloat_is_measured_on_the_candidate_with_the_diff_applied(self) -> None:
        candidate = self.tree({"test_batch.py": BATCH})
        batches = [self.batch(candidate)]
        cases = {"deletion": (APP.replace("def unused():\n    return 3\n\n", ""), {"candidate": "passed", "applied": False}),
                 "breaks": (APP.replace("value = 2\n", ""), {"candidate": "failed", "applied": False}),
                 "growth": (APP + "extra = 1\nmore = 2\n", {"error": "a change must delete more lines than it adds"})}
        for name, (new, expected) in cases.items():
            run = tdd_workflow._run_change(self.identity, batches, self.change(APP, new), candidate)
            self.assertEqual({key: run.get(key) for key in expected}, expected, name)
        refused = [tdd_workflow._run_change(self.identity, batches, change, candidate).get("error") for change in
                   (self.change(BATCH, "import app\n", "test_batch.py"), "not a diff\n", "\ud800")]
        self.assertEqual(refused, ["a change may not touch test files: test_batch.py",
                                   "change does not apply to the candidate", "change does not apply to the candidate"])
        self.assertIn("no recorded comparison batch", tdd_workflow._run_change(
            self.identity, [], self.change(APP, cases["deletion"][0]), candidate)["error"])

    def test_an_applied_deletion_is_measured_in_place(self) -> None:
        removed = APP.replace("def unused():\n    return 3\n\n", "")
        candidate = self.tree({"app.py": removed, "test_batch.py": BATCH})
        run = tdd_workflow._run_change(self.identity, [self.batch(candidate)], self.change(APP, removed), candidate)
        self.assertEqual((run.get("applied"), run.get("candidate")), (True, "passed"), run)

    def test_an_incomplete_replay_is_an_error_and_a_slow_one_binds_visibly(self) -> None:
        guard = "    def setUp(self):\n        if not hasattr(app, 'unused'): {}\n    def test_value(self): pass\n"
        deletion = self.change(APP, APP.replace("def unused():\n    return 3\n\n", ""))
        for action, timeout, key in (("self.skipTest('x')", 10.0, "error"), ("__import__('time').sleep(3)", 0.5, "timedOut")):
            candidate = self.tree({"test_batch.py": "import app, unittest\nclass B(unittest.TestCase):\n" + guard.format(action)})
            run = tdd_workflow._run_change(self.identity, [self.batch(candidate, timeout)], deletion, candidate)
            self.assertIn(key, run, run)
            self.assertEqual(tdd_workflow.binds(run), key == "timedOut", run)


class Ledger(Repo):
    """Direct calls inside a real workflow ledger transaction."""

    def setUp(self) -> None:
        super().setUp()
        subprocess.run([sys.executable, str(ROOT / "skills/repo-production-workflow/scripts/workflow.py"), "begin",
                        "--repo", str(self.repo), "--slug", "direct", "--intent", "direct probes"],
                       check=True, capture_output=True, env=os.environ)

    def intake(self, transaction, stage: str, *ids: str) -> str:
        document = {"workflowId": transaction.state["workflowId"], "stage": stage, "producer": "codex-advisor",
                    "findings": [{"id": i, "claim": "c", "material": True, "kind": "behavioral"} for i in ids]}
        write = evidence_write(transaction.state["workflowId"], f"finding-intake-{stage}", document)
        transaction.write([write])
        return write.evidence_id

    def test_settle_requires_every_case_to_settle_by_execution(self) -> None:
        names = "ABCDEFGHK"
        with workflow_state.mutation(self.identity) as transaction:
            state, first = transaction.state, self.intake(transaction, "code-review", *names)
            second = self.intake(transaction, "code-review", "C")
            proved = evidence_write(state["workflowId"], "tdd", {"behaviorMap": [{
                "id": "BM_COUNTER", "kind": "preservation", "basis": "lead counter-probe", "behavior": "engine answer stands",
                "seam": "sqlite3", "expected": "zero", "comparison": {"valid": True},
                "sourceRefs": [{"type": "finding", "evidenceId": first, "id": "K"}]}]})
            transaction.write([proved])
            state["tddEvidence"] = proved.evidence_id
            state["findingStates"] = [{"intakeEvidenceId": first, "findingId": name, "status": "pending", "material": True,
                                       "kind": "behavioral", "stage": "code-review", "producer": "code-review",
                                       **({"observations": [{"evidenceId": second, "id": "C"}]} if name == "C" else {})}
                                      for name in names]
            probe = lambda candidate, **more: {"kind": "probe", "candidate": candidate, **more}
            change = lambda candidate, applied: {"kind": "change", "candidate": candidate, "applied": applied}
            runs = [("A", first, probe("passed")), ("B", first, probe("failed")), ("C", first, probe("passed")),
                    ("C", second, probe("failed")), ("D", first, probe("timed-out")), ("E", first, change("passed", True)),
                    ("F", first, change("failed", False)), ("G", first, change("passed", False)),
                    ("H", first, {"kind": "probe", "error": "x"}), ("K", first, probe("failed", original="failed"))]
            execution = {"measuredEvidence": proved.evidence_id, "findings": [],
                         "reexecuted": [{"evidenceId": ref, "id": name, "execution": run} for name, ref, run in runs]}
            workflow_state._settle_executed(transaction, state, execution, "ev-recheck", False)
            self.assertEqual({entry["findingId"]: entry["status"] for entry in state["findingStates"]},
                             {"A": "resolved", "B": "pending", "C": "pending", "D": "pending", "E": "resolved",
                              "F": "resolved", "G": "pending", "H": "pending", "K": "resolved"}, "SETTLED_WITHOUT_EXECUTION")

    def dispose(self, entry: dict[str, object], status: str) -> None:
        with workflow_state.mutation(self.identity) as transaction:
            state = transaction.state
            intake = self.intake(transaction, "final", "F")
            state["findingStates"] = [{"intakeEvidenceId": intake, "findingId": "F", "kind": "behavioral", "material": True,
                                       "stage": "final", "producer": "codex-advisor", **entry}]
            with self.assertRaisesRegex(workflow_state.WorkflowError, "executed evidence"):
                workflow_state._apply_finding_dispositions(transaction, state, intake, [
                    {"finding_id": "F", "status": status, "kind": "behavioral"}], "final", "codex-advisor", "ev-disposition")

    def test_removed_pending_status_condition_no_longer_admits_a_prose_answer(self) -> None:
        self.dispose({"status": "accepted-follow-up", "appealStatus": "disagreement"}, "report-only")

    def test_removed_appeal_condition_no_longer_admits_a_prose_rejection(self) -> None:
        self.dispose({"status": "pending"}, "rejected-with-evidence")

    def final_over(self, record: dict[str, object], findings: list[dict[str, object]], raised: tuple[dict[str, object], ...] = (),
                   **state_fields: object) -> dict[str, object]:
        """record_advisor_result over a recorded final result, the way the removed appeal guard saw it."""
        with workflow_state.mutation(self.identity) as transaction:
            state = transaction.state
            gate = manifest_write(state["workflowId"], "quality-gate-tree", tree_manifest(self.identity))
            state.update(codeReview={"status": "passed", "findings": "none"}, finalStarted=True, qualityGateManifestId=gate.manifest_id,
                         finalReview=record, findingStates=[{"intakeEvidenceId": self.intake(transaction, "final", "F"), **finding}
                                                            for finding in findings], **state_fields)
            transaction.append(state, "advisor-result", manifests=[gate])
        intake = {"schemaVersion": 1, "workflowId": state["workflowId"], "stage": "final", "producer": "codex-advisor",
                  "verdict": "fix-before-commit" if raised else "commit-ready", "findings": list(raised)}
        executed = {"measuredTree": None, "measuredEvidence": None, "findings": list(raised), "reexecuted": []}
        accepted = inspect.signature(workflow_state.record_advisor_result).parameters
        return workflow_state.record_advisor_result(self.identity, "direct", state["workflowId"], "final", "codex-advisor",
                                                    intake["verdict"], intake=intake,
                                                    **({"execution": executed} if "execution" in accepted else {}))

    def round_claims(self, *raised: dict[str, object]) -> tuple[list[object], dict[str, object]]:
        """The claims of the intake finalReview names after a final round over one pending final finding F."""
        recorded = self.final_over({"status": "pending", "source": None, "findings": "pending"},
                                   [{"findingId": "F", "status": "pending", "kind": "behavioral", "material": True,
                                     "stage": "final", "producer": "codex-advisor"}], tuple(map(dict, raised)))
        intake = workflow_state.evidence_document(self.identity, recorded["finalReview"]["intakeEvidence"])
        return [finding["claim"] for finding in intake["findings"]], recorded["findingStates"][0]

    def test_a_final_round_records_its_own_executed_intake(self) -> None:
        self.assertEqual(self.round_claims(STILL_FAILS)[0], ["still fails"], "DIGEST_SHOWS_AN_EARLIER_ROUND")

    def test_a_reraised_finding_keeps_its_canonical_identity(self) -> None:
        kept = self.round_claims(STILL_FAILS)[1]
        self.assertEqual((kept["status"], kept["observations"][-1]["evidenceId"] != kept["intakeEvidenceId"]), ("pending", True))

    def test_a_clean_final_round_records_empty_current_intake(self) -> None:
        self.assertEqual(self.round_claims()[0], [])

    def test_removed_appeal_bypass_no_longer_rerecords_a_final_result(self) -> None:
        with self.assertRaisesRegex(workflow_state.WorkflowError, "final review result already recorded"):
            self.final_over({"status": "fix-before-commit", "source": "codex-advisor", "findings": "pending"},
                            [{"findingId": "F", "status": "rejected-with-evidence", "appealStatus": "pending", "dispositionFindingId": "F",
                              "kind": "behavioral", "material": True, "stage": "final", "producer": "codex-advisor"}])

    def test_a_pending_final_result_records_after_a_consumed_appeal(self) -> None:
        recorded = self.final_over({"status": "pending", "source": None, "findings": "pending"},
                                   [{"findingId": "F", "status": "rejected-with-evidence", "appealStatus": "pending", "dispositionFindingId": "F",
                                     "kind": "behavioral", "material": True, "stage": "final", "producer": "codex-advisor"}],
                                   finalAppealConsumed=True)
        self.assertEqual(recorded["finalReview"]["status"], "commit-ready")

    def rerate(self, stage: str, producer: str) -> object:
        """The materiality left after the producer re-rates its own pending finding as not material."""
        with workflow_state.mutation(self.identity) as transaction:
            state = transaction.state
            gate = manifest_write(state["workflowId"], "quality-gate-tree", tree_manifest(self.identity))
            state["qualityGateManifestId"] = gate.manifest_id
            transaction.append(state, "verification", manifests=[gate])
            documents = [{"workflowId": state["workflowId"], "stage": stage, "producer": producer,
                          "findings": [{"id": "F", "claim": "value changed", "material": material, "kind": "behavioral"}]}
                         for material in (True, False)]
            raised, rerated = (evidence_write(state["workflowId"], f"finding-intake-{stage}", document) for document in documents)
            transaction.write([raised, rerated])
            state["findingStates"] = [{"intakeEvidenceId": raised.evidence_id, "findingId": "F", "kind": "behavioral",
                                       "material": True, "status": "pending", "stage": stage, "producer": producer}]
            workflow_state._register_finding_intake(transaction, state, rerated.evidence_id, documents[1], {})
            return state["findingStates"][0]["material"]

    def test_a_preflight_producer_concedes_its_own_finding(self) -> None:
        self.assertIs(self.rerate("preflight", "codex-advisor"), False)

    def test_an_executed_review_result_is_not_rerated(self) -> None:
        self.assertIs(self.rerate("code-review", "code-review"), True)

    def test_a_rewrite_that_runs_replaces_a_timed_out_case_and_an_unexecuted_finding_is_a_report_line(self) -> None:
        with workflow_state.mutation(self.identity) as transaction:
            state = transaction.state
            case = lambda candidate, material: {"workflowId": state["workflowId"], "stage": "final", "producer": "codex-advisor",
                                                "findings": [{"id": "T", "claim": "c", "kind": "behavioral", "material": material,
                                                              "probe": self.probe("t", "    def test_it(self): pass\n"),
                                                              "execution": {"kind": "probe", "candidate": candidate}}]}
            slow, rewrite = (evidence_write(state["workflowId"], "finding-intake-final", document)
                             for document in (case("timed-out", True), case("passed", False)))
            legacy = evidence_write(state["workflowId"], "finding-intake-final", {"workflowId": state["workflowId"], "stage": "final",
                                    "producer": "codex-advisor", "findings": [{"id": "L", "claim": "c", "kind": "behavioral", "material": True}]})
            entry = lambda intake, name, **more: {"intakeEvidenceId": intake.evidence_id, "findingId": name, "status": "pending",
                                                  "material": True, "kind": "behavioral", "stage": "final", "producer": "codex-advisor", **more}
            state["findingStates"] = [entry(slow, "T", observations=[{"evidenceId": rewrite.evidence_id, "id": "T"}]), entry(legacy, "L")]
            transaction.append(state, "advisor-result", evidence=[slow, rewrite, legacy])
        state = workflow_state.read_workflow(self.identity)
        self.assertEqual([finding["execution"]["candidate"] for _, finding in tdd_workflow._retained(self.identity, state)], ["passed"])
        rerun = tdd_workflow.execute_findings(self.identity, state, [])["reexecuted"]
        self.assertEqual([(result["id"], result["execution"]["kind"]) for result in rerun], [("T", "probe"), ("L", "claim")])
        with workflow_state.mutation(self.identity) as transaction:
            workflow_state._settle_executed(transaction, transaction.state, {"measuredEvidence": transaction.state.get("tddEvidence"),
                                                                             "findings": [], "reexecuted": rerun}, "ev-recheck", False)
            self.assertEqual([entry["status"] for entry in transaction.state["findingStates"]], ["resolved", "resolved"])


if __name__ == "__main__":
    unittest.main()
