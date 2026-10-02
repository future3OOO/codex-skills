#!/usr/bin/env python3
"""The pass-start snapshot identity and the map's executed test selections.

Every attack here drives the real adapter or the real tdd producer as a
subprocess and reads the result back through a fresh `workflow.py status`,
never through the library.
"""
from __future__ import annotations

import importlib.util
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

from hooks.tests.support import (  # noqa: E402
    approve_preflight,
    build_document,
    fixture_env,
    pending_behavior,
    record_context_forge,
    run_git,
    run_intake,
    run_workflow,
)

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
CANONICAL_BOOTSTRAP = Path("/home/prop_/.local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py")
GITNEXUS = shutil.which("gitnexus")
PYTEST = importlib.util.find_spec("pytest") is not None


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
@unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
class PassStartSnapshotTests(unittest.TestCase):
    """A governed intake that really indexes, so the recorded identity is a real one."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-pass-start-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.slug = "pass-start-snapshot"
        self.intent = "record the pass-start snapshot identity"
        self.env = fixture_env(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("remote", "add", "origin", "https://example.invalid/workflow-fixture.git")
        # A callable symbol and its caller, so the index holds a real edge and a
        # later diff can attribute a change to something the graph knows.
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
        )
        self.git("add", "app.py", "caller.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = run_git(self.repo, self.env, *args)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_workflow(self.repo, self.env, *args)

    def intake(self, *extra: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
        return run_intake(self.repo, self.env, self.slug, self.intent, *extra, timeout=timeout)

    def status(self) -> dict[str, object]:
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_a_governed_intake_records_the_pass_start_snapshot(self) -> None:
        """The identity slice 3 needs to reach this pass's index, from state alone."""
        marker = "PASS_START_SNAPSHOT_NOT_RECORDED"
        intake = self.intake()
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)

        snapshot = self.status().get("passStartSnapshot")

        self.assertIsInstance(snapshot, dict, marker)
        for field in ("indexRepo", "indexPath", "analysisRepo", "sourceCommit", "indexedTree", "recordedAt"):
            self.assertTrue(str(snapshot.get(field) or "").strip(), f"{marker}: {field}")
        # The recorded tree is the one the consumer diffs against, which is the
        # index's own metadata, not the packet's candidate tree.
        meta = json.loads((Path(str(snapshot["indexPath"])) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["indexedTree"], meta.get("indexedTree"), marker)
        self.assertEqual(snapshot["sourceCommit"], meta.get("lastCommit"), marker)

    def test_revalidation_leaves_the_pass_start_snapshot_alone(self) -> None:
        """Revalidation re-indexes the dirty candidate; that graph is not this baseline."""
        marker = "REVALIDATE_OVERWROTE_THE_PASS_START_SNAPSHOT"
        self.assertEqual(self.intake().returncode, 0)
        before = self.status()["passStartSnapshot"]
        before_evidence = self.status()["repoContextForgeEvidence"]

        revalidated = self.intake("--revalidate")
        self.assertEqual(revalidated.returncode, 0, revalidated.stdout + revalidated.stderr)

        after = self.status()
        self.assertEqual(after["passStartSnapshot"], before, marker)
        # The refreshed run recorded its own evidence, which is what makes the
        # untouched baseline a decision rather than an accident of doing nothing.
        self.assertNotEqual(after["repoContextForgeEvidence"], before_evidence, marker)

    def test_a_differing_rerun_keeps_the_first_snapshot(self) -> None:
        """First recorded wins, and the conflict is reported rather than absorbed."""
        marker = "RERUN_OVERWROTE_THE_PASS_START_SNAPSHOT"
        self.assertEqual(self.intake().returncode, 0)
        before = self.status()["passStartSnapshot"]
        # A new commit moves the analysed head, so a second intake resolves a
        # different index identity for the same pass.
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 2\n", encoding="utf-8"
        )
        self.git("commit", "-q", "-am", "second")

        rerun = self.intake()
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)

        after = self.status()["passStartSnapshot"]
        self.assertEqual(after, before, marker)
        if after["indexedTree"] != json.loads(
            (Path(str(after["indexPath"])) / "meta.json").read_text(encoding="utf-8")
        ).get("indexedTree"):
            self.assertIn("keeping the immutable recorded snapshot", rerun.stderr, marker)

    def test_a_second_pass_records_its_own_snapshot(self) -> None:
        """First-write-wins is scoped to the pass, not to the repository."""
        marker = "SECOND_PASS_INHERITED_THE_PREVIOUS_IDENTITY"
        self.assertEqual(self.intake().returncode, 0)
        first = self.status()["passStartSnapshot"]

        self.slug = "pass-start-snapshot-second"
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.assertIsNone(self.status().get("passStartSnapshot"), marker)
        self.assertEqual(self.intake().returncode, 0)

        second = self.status()["passStartSnapshot"]
        self.assertIsInstance(second, dict, marker)
        self.assertTrue(str(second.get("indexedTree") or "").strip(), marker)
        self.assertEqual(second["indexRepo"], first["indexRepo"], marker)

    def test_an_index_without_a_recorded_tree_records_a_named_gap(self) -> None:
        """Absence has to be stated: a consumer cannot tell silence from no baseline.

        An index built before the producer recorded `indexedTree` is the real
        shape of this: the graph resolves and the intake succeeds, but the one
        field that identifies its tree is missing.
        """
        marker = "PARTIAL_IDENTITY_RECORDED_AS_COMPLETE"
        self.assertEqual(self.intake().returncode, 0)
        index_path = Path(str(self.status()["passStartSnapshot"]["indexPath"]))
        meta_file = index_path / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta.pop("indexedTree", None)
        meta_file.write_text(json.dumps(meta), encoding="utf-8")

        # A second pass on the same repository reads that older-shaped index.
        self.slug = "pass-start-snapshot-legacy-index"
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.assertEqual(self.intake().returncode, 0)

        state = self.status()

        self.assertIsNone(state.get("passStartSnapshot"), marker)
        self.assertTrue(str(state.get("passStartSnapshotGap") or "").strip(), marker)

    def detect_changes(self, snapshot: dict[str, object]) -> subprocess.CompletedProcess[str]:
        """The consumer, driven exactly as slice 3 will drive it: recorded identity only."""
        return subprocess.run(
            [
                GITNEXUS, "detect-changes", "-r", str(snapshot["indexRepo"]),
                "--worktree", str(self.repo),
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=300,
        )

    def test_the_recorded_tree_is_the_baseline_the_consumer_diffs_against(self) -> None:
        """The whole point of the record: it has to reach this pass's index."""
        marker = "RECORDED_TREE_IS_NOT_THE_BASELINE"
        self.assertEqual(self.intake().returncode, 0)
        snapshot = self.status()["passStartSnapshot"]
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 3\n", encoding="utf-8"
        )

        result = self.detect_changes(snapshot)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["analysis"]["baseline"]["tree"], snapshot["indexedTree"], marker)
        self.assertEqual(report["analysis"]["baseline"]["source_commit"], snapshot["sourceCommit"], marker)
        self.assertIn(
            "compute", [str(symbol.get("name")) for symbol in report["changed_symbols"]], marker,
        )

    def test_a_swept_index_is_not_a_clean_empty_result(self) -> None:
        """A baseline that is gone must not read as a candidate that changed nothing."""
        marker = "SWEPT_INDEX_READ_AS_A_CLEAN_EMPTY_RESULT"
        self.assertEqual(self.intake().returncode, 0)
        snapshot = self.status()["passStartSnapshot"]
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 5\n", encoding="utf-8"
        )
        shutil.rmtree(Path(str(snapshot["indexPath"])), ignore_errors=True)

        result = self.detect_changes(snapshot)

        # The identity itself is a workflow fact and survives the sweep.
        self.assertEqual(self.status()["passStartSnapshot"], snapshot, marker)
        if result.returncode == 0:
            report = json.loads(result.stdout)
            self.assertNotEqual(
                (report["summary"]["changed_count"], report["analysis"]["status"]), (0, "complete"), marker,
            )
        else:
            self.assertTrue((result.stdout + result.stderr).strip(), marker)


class ExecutedSelectionsTests(unittest.TestCase):
    """What the current map's recorded proofs actually selected, read from status.

    An undecidable selection has to stay undecidable rather than collapsing
    into "selects nothing" and silently owning the whole surface.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-selections-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.slug = "executed-selections"
        self.env = fixture_env(self.tmp / "state")
        previous = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")

        def restore_state_root() -> None:
            if previous is None:
                os.environ.pop("CODEX_WORKFLOW_STATE_ROOT", None)
            else:
                os.environ["CODEX_WORKFLOW_STATE_ROOT"] = previous

        # Registered before the variable is set and before anything else can
        # fail, so a setUp that raises still restores what the caller had.
        self.addCleanup(restore_state_root)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.environ["CODEX_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.workflow("begin", "--slug", self.slug, "--intent", "expose executed selections")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.workflow_id = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)

    def git(self, *args: str) -> None:
        result = run_git(self.repo, self.env, *args)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_workflow(self.repo, self.env, *args)

    def status(self) -> dict[str, object]:
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def record_map(self, *items: dict[str, object]) -> None:
        document = build_document("executed selections", behavior_map=list(items))
        path = self.tmp / "preflight.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        approve_preflight(self.repo, document)
        recorded = self.workflow(
            "record", "preflight", "--slug", self.slug, "--workflow-id", self.workflow_id,
            "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def probe(self, name: str, marker: str, *, passing: bool) -> str:
        """One real test file whose outcome the tdd producer reads."""
        body = "pass" if passing else f"self.fail({marker!r})"
        (self.repo / f"{name}.py").write_text(
            "import unittest\n\n\nclass Probe(unittest.TestCase):\n"
            f"    def test_behavior(self):\n        {body}\n",
            encoding="utf-8",
        )
        return f"{name}.Probe.test_behavior"

    def tdd(self, behavior_id: str, *command: str, support=()) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                "--slug", self.slug, "--behavior-id", behavior_id,
                *(token for path in support for token in ("--support", path)), "--", *command,
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def two_tests(self, name: str) -> None:
        """One file holding a selected test and an unrelated one beside it."""
        (self.repo / f"{name}.py").write_text(
            "import unittest\n\n\nclass Probe(unittest.TestCase):\n"
            "    def test_selected(self):\n        pass\n\n"
            "    def test_unrelated(self):\n        pass\n",
            encoding="utf-8",
        )

    # Command forms whose real-runner behaviour was measured separately; here
    # they are read through the projection owner directly, because driving each
    # one would need a primed pytest cache or a second fixture tree to make the
    # exclusion real, and a run that excludes nothing proves nothing.
    UNRESOLVED_FORMS = (
        "-m pytest -q suite/test_x.py -kselected",
        "-m pytest -q suite/test_x.py --deselect suite/test_x.py::Probe::test_unrelated",
        "-m pytest -q suite --ignore=suite/test_x.py",
        "-m pytest -q suite --ignore-glob=*_x.py",
        "-m pytest -q suite/test_x.py --lf",
        "-m pytest -q suite/test_x.py --sw",
        "-m pytest -q -m smoke suite",
        "-m unittest suite.test_x.Probe -kselected",
        "-m unittest discover suite test_x.py",
        "-m unittest discover -s suite -k test_one",
        "-m unittest discover -s suite -p test_x.py",
        "-m unittest discover -s suite --pattern test_x.py",
        "-m unittest discover -s suite -ptest_x.py",
        "-m pytest -q",
        "-m unittest",
        # A known-arity cluster is not enough: -h prints help and runs nothing.
        "-m pytest -xqh suite/test_x.py",
        # An exempt cluster does not carry the option beside it: the first still
        # prints a version and the second is not pytest's option at all.
        "-m pytest -xq --version suite/test_x.py",
        "-m pytest -xq --not-a-pytest-option suite/test_x.py",
    )
    RESOLVED_FORMS = (
        ("-m pytest -q suite/test_x.py", ["suite/test_x.py"]),
        ("-m pytest -x suite/test_x.py::Probe::test_selected", ["suite/test_x.py::Probe::test_selected"]),
        ("-m unittest -v suite.test_x.Probe.test_selected", ["suite.test_x.Probe.test_selected"]),
        ("-m unittest suite.test_x.Probe", ["suite.test_x.Probe"]),
        # Every letter is an option identify already treats as irrelevant.
        ("-m pytest -xq suite/test_x.py", ["suite/test_x.py"]),
        ("-m unittest discover -s suite", ["suite"]),
        ("-m unittest discover suite", ["suite"]),
    )

    @unittest.skipUnless(PYTEST, "the real pytest runner is unavailable")
    def test_a_filtered_path_selection_reports_unknown(self) -> None:
        """A filter decides which of a path's tests run, so the path stops saying.

        The recorded run really excludes: the file holds two tests and `-k`
        selects one. Publishing the path would claim both, including a test
        nothing exercised. The other forms carry the same claim and are checked through the projection
        owner below, since their exclusion needs runner state this fixture has
        no reason to build.
        """
        marker = "TEST_DEPENDENCY_LOST"
        self.record_map(pending_behavior("BM_FILTERED"))
        self.two_tests("test_filtered")
        recorded = self.tdd(
            "BM_FILTERED", sys.executable, "-m", "pytest", "-q",
            "test_filtered.py", "-k", "selected",
        )
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)

        record = self.selections(marker)["BM_FILTERED"]["comparison"]

        self.assertIsNone(record["targets"], marker)
        self.assertIn("-k", str(record.get("unknown") or ""), marker)

    def test_every_captured_form_resolves_or_reports_unknown(self) -> None:
        """The whole captured corpus, through the owner that publishes selections."""
        marker = "TEST_DEPENDENCY_LOST"
        from hooks.lib.workflow_state import _selection

        (self.repo / "suite").mkdir(exist_ok=True)
        (self.repo / "suite" / "test_x.py").write_text("", encoding="utf-8")
        for form in self.UNRESOLVED_FORMS:
            record = _selection(f"{sys.executable} {form}", self.repo)
            self.assertIsNone(record["targets"], f"{marker}: {form}")
            self.assertTrue(str(record.get("unknown") or "").strip(), f"{marker}: {form}")
        for form, expected in self.RESOLVED_FORMS:
            record = _selection(f"{sys.executable} {form}", self.repo)
            self.assertEqual(record["targets"], expected, f"{marker}: {form}")
            self.assertNotIn("unknown", record, f"{marker}: {form}")

    def test_a_cluster_of_only_irrelevant_options_still_resolves(self) -> None:
        """`-xq` is fail-fast plus quiet, so the path still says which tests ran."""
        from hooks.lib.workflow_state import _selection

        (self.repo / "suite").mkdir(exist_ok=True)
        (self.repo / "suite" / "test_x.py").write_text("", encoding="utf-8")
        record = _selection(f"{sys.executable} -m pytest -xq suite/test_x.py", self.repo)

        self.assertEqual(record["targets"], ["suite/test_x.py"], "ALL_IGNORED_CLUSTER_REPORTED_UNRESOLVED")

    def test_a_cluster_carrying_help_stays_unresolved(self) -> None:
        """`-xqh` prints help and runs nothing, so it owns nothing."""
        from hooks.lib.workflow_state import _selection

        (self.repo / "suite").mkdir(exist_ok=True)
        (self.repo / "suite" / "test_x.py").write_text("", encoding="utf-8")
        record = _selection(f"{sys.executable} -m pytest -xqh suite/test_x.py", self.repo)

        self.assertIsNone(record["targets"], "HELP_CLUSTER_PUBLISHED_AS_OWNERSHIP")

    def test_a_filtered_discovery_reports_unknown(self) -> None:
        """A discovery pattern decides which files are collected, so `.` stops saying.

        The exclusion is real at file scope: a second `test_*.py` holds a failing
        test the pattern leaves out. The run passing is the proof it was excluded,
        since discovery over the directory would have collected and failed it.
        """
        marker = "DISCOVERY_SUPPORT_BROKEN"
        self.record_map(pending_behavior("BM_DISCOVERED"))
        self.two_tests("test_discovered")
        self.probe("test_excluded", "EXCLUDED_MARKER", passing=False)
        recorded = self.tdd(
            "BM_DISCOVERED", sys.executable, "-m", "unittest", "discover",
            "-s", ".", "-p", "test_discovered.py",
        )
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)

        record = self.selections(marker)["BM_DISCOVERED"]["comparison"]

        self.assertIsNone(record["targets"], marker)
        self.assertIn("-p", str(record.get("unknown") or ""), marker)

    def selections(self, marker: str) -> dict[str, object]:
        """The marker travels in: absence of the projection is the mapped failure."""
        value = self.status().get("mapSelections")
        self.assertIsInstance(value, dict, marker)
        return value





    def test_status_exposes_red_green_and_baseline_selections(self) -> None:
        marker = "EXECUTED_SELECTIONS_ABSENT"
        self.record_map(
            pending_behavior("BM_PROVED"),
            pending_behavior("BM_BASELINED"),
        )
        failing = self.probe("test_proved", "PROVED_MARKER", passing=False)
        self.assertEqual(self.tdd("BM_PROVED", sys.executable, "-m", "unittest", failing).returncode, 2)
        passing = self.probe("test_proved", "PROVED_MARKER", passing=True)
        self.assertEqual(self.tdd("BM_PROVED", sys.executable, "-m", "unittest", passing).returncode, 0)
        baselined = self.probe("test_baselined", "BASELINE_MARKER", passing=True)
        self.assertEqual(self.tdd("BM_BASELINED", sys.executable, "-m", "unittest", baselined).returncode, 0)

        selections = self.selections(marker)

        self.assertEqual(selections["BM_PROVED"]["comparison"]["targets"], ["test_proved.Probe.test_behavior"], marker)
        self.assertEqual(selections["BM_PROVED"]["comparison"]["targets"], ["test_proved.Probe.test_behavior"], marker)
        self.assertEqual(
            selections["BM_BASELINED"]["comparison"]["targets"], ["test_baselined.Probe.test_behavior"], marker,
        )


    def test_the_checkpoint_payload_carries_none_of_the_new_fields(self) -> None:
        """These are machine-only graph details; the advisor's payload never sees them."""
        marker = "ADDED_FIELDS_LEAKED_INTO_ADVISOR_OR_COMPLETION"
        self.record_map(pending_behavior("BM_CHECKPOINT"))
        failing = self.probe("test_checkpoint", "CHECKPOINT_MARKER", passing=False)
        self.assertEqual(self.tdd("BM_CHECKPOINT", sys.executable, "-m", "unittest", failing).returncode, 2)
        self.assertIn("BM_CHECKPOINT", self.selections(marker), marker)

        result = self.workflow("checkpoint", "--phase", "preflight-advice")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        payload = json.dumps(json.loads(result.stdout))
        for field in ("passStartSnapshot", "passStartSnapshotGap", "mapSelections"):
            self.assertNotIn(field, payload, marker)

    def test_status_reads_change_no_workflow_state(self) -> None:
        """The projection is derived on read; reading it must not write."""
        marker = "STATUS_MUTATED_STATE"
        self.record_map(pending_behavior("BM_READONLY"))
        failing = self.probe("test_readonly", "READONLY_MARKER", passing=False)
        self.assertEqual(self.tdd("BM_READONLY", sys.executable, "-m", "unittest", failing).returncode, 2)

        def history() -> str:
            result = self.workflow("history")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        before = history()
        self.assertIn("BM_READONLY", json.dumps(self.selections(marker)), marker)
        self.assertEqual(history(), before, marker)


    @unittest.skipUnless(PYTEST, "the real pytest runner is unavailable")
    def test_ambiguous_and_implicit_selections_both_report_unknown(self) -> None:
        """Unknown ownership and owning nothing are different answers.

        The ambiguity is real rather than contrived: a conftest declares a real
        pytest option, so the command runs and passes while the selector tables,
        which cover pytest's own options, cannot tell that option's value from a
        target. That is exactly the shape a plugin produces in a real repository.
        """
        marker = "AMBIGUITY_AND_EMPTINESS_CONFLATED"
        self.record_map(
            pending_behavior("BM_AMBIGUOUS"),
            pending_behavior("BM_WHOLE_SUITE"),
        )
        (self.repo / "conftest.py").write_text(
            "def pytest_addoption(parser):\n"
            "    parser.addoption('--probe-label', action='store', default='')\n",
            encoding="utf-8",
        )
        self.probe("test_ambiguous", "AMBIGUOUS_MARKER", passing=True)
        ambiguous = self.tdd(
            "BM_AMBIGUOUS", sys.executable, "-m", "pytest", "-q",
            "--probe-label", "run-one", "test_ambiguous.py", support=("test_ambiguous.py", "conftest.py"),
        )
        self.assertEqual(ambiguous.returncode, 0, ambiguous.stdout + ambiguous.stderr)
        whole = self.tdd("BM_WHOLE_SUITE", sys.executable, "-m", "pytest", "-q", support=("test_ambiguous.py", "conftest.py"))
        self.assertEqual(whole.returncode, 0, whole.stdout + whole.stderr)

        selections = self.selections(marker)

        undecidable = selections["BM_AMBIGUOUS"]["comparison"]
        self.assertIsNone(undecidable["targets"], marker)
        self.assertTrue(str(undecidable.get("unknown") or "").strip(), marker)
        # Naming no target selects implicitly from pytest's own rootdir and
        # configuration, so no scope is named and [] would read as owning nothing.
        whole_suite = selections["BM_WHOLE_SUITE"]["comparison"]
        self.assertIsNone(whole_suite["targets"], marker)
        self.assertIn("implicit", str(whole_suite.get("unknown") or ""), marker)


if __name__ == "__main__":
    unittest.main()
