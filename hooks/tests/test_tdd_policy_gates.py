#!/usr/bin/env python3
"""Workflow gate contracts around mapped TDD proof completion."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.tdd_workflow import completion_blockers, edit_blockers  # noqa: E402
from hooks.lib.workflow_state import read_workflow  # noqa: E402
from hooks.tests.support import pending_behavior  # noqa: E402
# Module alias only: binding the TestCase name here would make unittest.main
# rediscover and re-run the whole repair suite inside this file.
from hooks.tests import test_tdd_repairs as tdd_repairs  # noqa: E402
from hooks.tests import test_finding_attacks as finding_attacks  # noqa: E402

EDIT_HOOK = ROOT / "hooks" / "code-quality-gate.py"
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None
PYTEST_COMMAND = (sys.executable, "-m", "pytest")


class MappedTddPolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = tdd_repairs.MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def test_preflight_retains_interpretation_inputs(self) -> None:
        h = self.harness
        slug, workflow_id = h.begin_with_map([pending_behavior("BM_CHOICE")])
        path = h.tmp / f"{slug}-preflight.json"
        document = json.loads(path.read_text())
        choice = {"boundaryInputs": ["SELECT 'x'", 0, False],
                  "interpretations": ["host truthiness", "database truthiness"],
                  "interpretation": "database truthiness", "authority": "governing requirement"}
        document["behaviorMap"][0].update(choice)
        path.write_text(json.dumps(document))
        result = h.cli("record-preflight", "--repo", str(h.repo), "--slug", slug,
                       "--workflow-id", workflow_id, "--input", str(path))
        self.assertEqual(result.returncode, 0, "INTERPRETATION_METADATA_REFUSED: " + result.stdout + result.stderr)
        evidence_id = json.loads(result.stdout)["evidenceId"]
        recorded = h.cli("evidence", "--repo", str(h.repo), "--evidence-id", evidence_id)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        retained = json.loads(recorded.stdout)["document"]["document"]["behaviorMap"][0]
        self.assertEqual(json.dumps({key: retained[key] for key in choice}), json.dumps(choice))

    def test_nonrunner_baseline_preserves_observation_and_silence_refusal(self) -> None:
        h = self.harness
        slug, _ = h.begin_with_map([pending_behavior("BM_OBS"), pending_behavior("BM_SILENT")])
        observed = h.tdd(slug, "red", "BM_OBS", ("git", "symbolic-ref", "HEAD"))
        self.assertEqual(observed.returncode, 0, observed.stdout + observed.stderr)
        proof = h.evidence()["behaviorMap"][0]["baselineProof"]
        self.assertEqual(proof["quality"], "operation-succeeded")
        self.assertTrue(proof["observation"])
        self.assertIn("symbolic-ref", proof["site"])
        silent = h.tdd(slug, "red", "BM_SILENT", ("git", "diff", "--exit-code"))
        self.assertEqual(silent.returncode, 2, silent.stdout + silent.stderr)
        self.assertEqual(h.evidence()["behaviorMap"][1]["status"], "pending")

    def test_missing_selected_input_refuses_baseline(self) -> None:
        h = self.harness
        item = pending_behavior("BM_INPUT")
        item.update(boundaryInputs=["--quiet"], interpretations=["preserve verbosity", "ignore verbosity"],
                    interpretation="ignore verbosity", authority="tdd_surface Interface")
        slug, _ = h.begin_with_map([item])
        (h.repo / "test_input.py").write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
            "from hooks.lib.tdd_surface import identify\n"
            "def test_selected():\n"
            "    assert identify(['pytest', '-q', 'test_a.py'])['runner'] == 'pytest'\n"
            "def test_unselected():\n"
            "    assert identify(['pytest', '--quiet', 'test_a.py'])['runner'] == 'pytest'\n")
        result = h.tdd(slug, "red", "BM_INPUT", (*PYTEST_COMMAND, "-q", "test_input.py::test_selected"))
        self.assertEqual(result.returncode, 2, "MISSING_INPUT_PROOF_ACCEPTED: " + result.stdout + result.stderr)
        self.assertIn("--quiet", result.stdout + result.stderr)
        self.assertIn("missing", (result.stdout + result.stderr).lower())
        self.assertNotIn("RED must fail", result.stderr, "INPUT_COVERAGE_REFUSAL_MISDIRECTS_REPAIR")
        self.assertIn("--from-evidence", result.stderr)
        self.assertEqual(h.evidence()["behaviorMap"][0]["status"], "pending")

    def test_sql_boundary_and_extraction_limits(self) -> None:
        h = self.harness
        query = "SELECT 'x'"
        items = []
        for name in ("MISSING", "PRESENT", "VARIABLE_MISSING", "VARIABLE_PRESENT", "INDIRECT", "LARGE"):
            item = pending_behavior("BM_" + name)
            item.update(boundaryInputs=[query], interpretations=["host truthiness", "database truthiness"],
                        interpretation="host truthiness", authority="explicit host conversion contract")
            items.append(item)
        slug, _ = h.begin_with_map(items)
        samples = {
            "MISSING": "    row = connection.execute('SELECT 1').fetchone()\n    assert row[0] == 1, \"SELECT 'x'\"\n",
            "PRESENT": "    row = connection.execute(\"SELECT 'x'\").fetchone()\n    assert row[0] == 'x'\n",
            "VARIABLE_MISSING": "    query = 'SELECT 1'\n    row = connection.execute(query).fetchone()\n    assert row[0] == 1, \"SELECT 'x'\"\n",
            "VARIABLE_PRESENT": "    query = \"SELECT 'x'\"\n    row = connection.execute(query).fetchone()\n    assert row[0] == 'x'\n",
            "INDIRECT": "    query = 'SELECT ' + repr('x')\n    row = connection.execute(query).fetchone()\n    assert row[0] == 'x'\n",
            "LARGE": "    row = connection.execute(\"SELECT 'x'\").fetchone()\n    assert row[0] == 'x'\n" + "#" * 262144 + "\n",
        }
        for name, body in samples.items():
            with self.subTest(name=name):
                path = h.repo / ("test_" + name.lower() + ".py")
                path.write_text("import sqlite3\ndef test_selected():\n    connection = sqlite3.connect(':memory:')\n" + body)
                result = h.tdd(slug, "red", "BM_" + name, (*PYTEST_COMMAND, "-q", path.name + "::test_selected"))
                self.assertEqual(result.returncode, 2 if name == "MISSING" else 0,
                                 "INPUT_REPRESENTATION_FALSE_CLAIM: " + result.stdout + result.stderr)
                evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
                field = "missing" if name == "MISSING" else "represented" if name == "PRESENT" else "unresolved"
                self.assertEqual(evidence[field], [query], "INPUT_REPRESENTATION_FALSE_CLAIM")
                if name == "LARGE":
                    self.assertIn("256 KiB", " ".join(evidence["limits"]))
        print(f"input-screen target={ROOT} scale=6 source-forms limit=262144 bytes oversized-source=unresolved")

    def test_printed_operation_keeps_inputs_but_not_output_literals(self) -> None:
        h = self.harness
        items = []
        for name in ("PRESENT", "MISSING"):
            item = pending_behavior("BM_" + name)
            item.update(boundaryInputs=["--quiet"], interpretations=["retain", "ignore"],
                        interpretation="ignore", authority="existing verbosity contract")
            items.append(item)
        slug, _ = h.begin_with_map(items)
        for name, expression in (("PRESENT", "print(identify(['pytest', '--quiet', 'test_a.py']))"),
                                 ("MISSING", "print('--quiet', identify(['pytest', '-q', 'test_a.py']))")):
            with self.subTest(name=name):
                path = h.repo / ("test_print_" + name.lower() + ".py")
                path.write_text(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
                                "from hooks.lib.tdd_surface import identify\n" + expression + "\n")
                result = h.tdd(slug, "red", "BM_" + name, (sys.executable, path.name))
                self.assertEqual(result.returncode, 0 if name == "PRESENT" else 2,
                                 "EXECUTED_INPUT_REFUSED: " + result.stdout + result.stderr)
                data = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
                self.assertEqual(data["represented" if name == "PRESENT" else "missing"], ["--quiet"])

    def test_mutated_inputs_are_unresolved(self) -> None:
        self.assert_indirect_inputs({
            "augmented": "    flag = '--qui'\n    flag += 'et'\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "mutated": "    flags = ['pytest', '--quiet', 'test_a.py']\n    flags[1] = '-q'\n    assert identify(flags)['runner'] == 'pytest'\n",
        }, "MUTATED_INPUT_FALSE_CLAIM")

    def test_rebound_inputs_are_unresolved(self) -> None:
        self.assert_indirect_inputs({
            "annotated": "    flag = '--quiet'\n    flag: str = '-q'\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "conditional": "    flag = '--quiet'\n    if True:\n        flag = '-q'\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
        }, "REBOUND_INPUT_FALSE_CLAIM")

    def test_loop_else_inputs_are_unresolved(self) -> None:
        self.assert_indirect_inputs({
            "loop_else": "    for flag in ['-q']:\n        assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n    else:\n        assert identify(['pytest', '--quiet', 'test_a.py'])['runner'] == 'pytest'\n",
        }, "LOOP_ELSE_FALSE_ABSENCE")

    def test_scoped_input_bindings_are_unresolved(self) -> None:
        self.assert_indirect_inputs({
            "loop_rebinding": "    flag = '--quiet'\n    for unused in [1]:\n        flag = '-q'\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "named_expression": "    flag = '--quiet'\n    (flag := '-q')\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
        }, "SCOPED_INPUT_FALSE_CLAIM")

    def test_mutable_receiver_inputs_are_unresolved(self) -> None:
        self.assert_indirect_inputs({
            "receiver": "    flags = ['pytest', '--quiet', 'test_a.py']\n    flags.pop(1)\n    assert identify(flags)['runner'] == 'pytest'\n",
            "subscript_receiver": "    case = {'args': ['pytest', '--quiet', 'test_a.py']}\n    case['args'].pop(1)\n    assert identify(case['args'])['runner'] == 'pytest'\n",
        }, "RECEIVER_INPUT_FALSE_CLAIM")

    def test_input_binding_lifetime_is_bounded(self) -> None:
        self.assert_indirect_inputs({
            "assert_truth": "    class Mutator:\n        def __bool__(self):\n            flags.pop(1)\n            return True\n    m = Mutator()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    assert m\n    assert identify(flags)['runner'] == 'pytest'\n",
            "module_effect": "    assert identify(flags)['runner'] == 'pytest'\nflags = ['pytest', '--quiet', 'test_a.py']\nflags.pop(1)\n",
            "closure_effect": "    flag = '--quiet'\n    def mutate():\n        nonlocal flag\n        flag = '-q'\n    mutate()\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "argument_effect": "    flags = ['pytest', '--quiet', 'test_a.py']\n    list(zip(flags, flags.clear() or []))\n    assert identify(['pytest', '-q', 'test_a.py'])['runner'] == 'pytest'\n",
            "comparison_effect": "    def mutate():\n        nonlocal flag\n        flag = '-q'\n        return True\n    flag = '--quiet'\n    assert True == mutate()\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "unsupported_alternatives": "    flag = '--quiet'\n    match 'first':\n        case 'first':\n            flag = '-q'\n        case _:\n            flag = '--quiet'\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n",
            "attribute_effect": "    class Mutator:\n        @property\n        def value(self):\n            flags.pop(1)\n    m = Mutator()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    m.value\n    assert identify(flags)['runner'] == 'pytest'\n",
            "subscript_effect": "    class Mutator:\n        def __getitem__(self, key):\n            flags.pop(1)\n    m = Mutator()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    m[0]\n    assert identify(flags)['runner'] == 'pytest'\n",
            "callable_lookup_effect": "    class Mutator:\n        @property\n        def run(self):\n            flags.pop(1)\n            return identify\n    m = Mutator()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    assert m.run(flags)['runner'] == 'pytest'\n",
            "hash_effect": "    class Mutator:\n        def __hash__(self):\n            flags.pop(1)\n            return 1\n    m = Mutator()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    {m: 1}\n    assert identify(flags)['runner'] == 'pytest'\n",
            "keyword_expansion": "    from json import dumps\n    class Arguments:\n        def keys(self):\n            flags.pop(1)\n            return []\n    arguments = Arguments()\n    flags = ['pytest', '--quiet', 'test_a.py']\n    dumps(flags, **arguments)\n    assert identify(flags)['runner'] == 'pytest'\n",
        }, "INPUT_BINDING_LIFETIME_BROKEN")

    def assert_indirect_inputs(self, samples: dict[str, str], marker: str) -> None:
        h = self.harness
        items = [dict(pending_behavior("BM_" + name.upper()), boundaryInputs=["--quiet"],
                      interpretations=["retain", "ignore"], interpretation="ignore",
                      authority="existing verbosity contract") for name in samples]
        slug, _ = h.begin_with_map(items)
        for name, body in samples.items():
            with self.subTest(name=name):
                path = h.repo / ("test_" + name + ".py")
                path.write_text(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
                                "from hooks.lib.tdd_surface import identify\ndef test_selected():\n" + body)
                result = h.tdd(slug, "red", "BM_" + name.upper(),
                               (*PYTEST_COMMAND, "-q", path.name + "::test_selected"))
                self.assertEqual(result.returncode, 0, marker + result.stdout + result.stderr)
                evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
                self.assertEqual(evidence["represented"], [], marker + name)
                self.assertEqual(evidence["unresolved"], ["--quiet"], marker)

    def test_type_distinct_input_update_reopens_proof(self) -> None:
        h = self.harness
        for number, boolean in ((0, False), ({"value": 1}, {"value": True})):
            with self.subTest(number=number):
                item = dict(pending_behavior("BM_TYPED"), boundaryInputs=[number],
                            interpretations=["numeric", "boolean"], interpretation="numeric",
                            authority="typed input contract")
                slug, wid = h.begin_with_map([item])
                (h.repo / "test_typed.py").write_text(
                    f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
                    "from hooks.lib.tdd_surface import identify\ndef test_selected():\n"
                    f"    assert identify(['pytest', str({number!r})])['runner'] == 'pytest'\n")
                result = h.tdd(slug, "red", "BM_TYPED", (*PYTEST_COMMAND, "-q", "test_typed.py::test_selected"))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                before = h.evidence()
                result = h.update_map(slug, wid, dict(reassessment="governing typed-input correction", dispositions=[
                    dict(id="BM_TYPED", boundaryInputs=[boolean], evidence="explicit governing correction")]))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                after = h.evidence()
                self.assertEqual(json.dumps(after["behaviorMap"][0]["boundaryInputs"]),
                                 json.dumps([boolean]), "TYPED_INPUT_UPDATE_LOST")
                self.assertIn("BM_TYPED", json.loads(result.stdout)["pending"], "TYPED_INPUT_UPDATE_LOST")
                self.assertEqual(before["runs"], after["runs"], "TYPED_INPUT_UPDATE_LOST")

    def test_unselected_script_body_is_not_input_evidence(self) -> None:
        h = self.harness
        item = pending_behavior("BM_SCRIPT")
        item.update(boundaryInputs=["--quiet"], interpretations=["retain", "ignore"],
                    interpretation="ignore", authority="existing verbosity contract")
        slug, _ = h.begin_with_map([item])
        (h.repo / "test_probe.py").write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
            "from hooks.lib.tdd_surface import identify\n"
            "def unused():\n    return identify(['pytest', '--quiet', 'test_a.py'])\n"
            "print(identify(['pytest', '-q', 'test_a.py']))\n")
        result = h.tdd(slug, "red", "BM_SCRIPT", (sys.executable, "test_probe.py"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
        self.assertEqual(evidence["represented"], [], "UNSELECTED_BODY_COUNTED")
        self.assertEqual(evidence["unresolved"], ["--quiet"])

    def test_superseded_hold_moves_to_proved_replacement(self) -> None:
        h = self.harness
        item = pending_behavior("BM_ORIGIN")
        slug, workflow_id = h.begin_with_map([item])
        command = h.write_unittest(2, "VALUE_NOT_TWO")
        test = h.repo / "test_app.py"
        test.write_text(test.read_text().replace("app.value", "getattr(app, 'value')"))
        self.assertEqual(h.tdd(slug, "red", "BM_ORIGIN", command).returncode, 0)
        (h.repo / "app.py").write_text("value = 2\n")
        self.assertEqual(h.tdd(slug, "green", "BM_ORIGIN", command).returncode, 0)
        before = h.evidence()
        held = h.update_map(slug, workflow_id, dict(reassessment="late unresolved GREEN interpretation", dispositions=[
            dict(id="BM_ORIGIN", boundaryInputs=["value"], interpretations=["alpha", "beta"])]))
        self.assertEqual(held.returncode, 0, held.stdout + held.stderr)
        self.assertEqual(h.evidence()["behaviorMap"][0]["status"], "green")
        self.assertEqual(behavior_map.unresolved(h.evidence()["behaviorMap"]), ["BM_ORIGIN"])
        settled = h.update_map(slug, workflow_id, dict(reassessment="existing proof remains sufficient", dispositions=[
            dict(id="BM_ORIGIN", interpretation="beta", authority="governing read contract")]))
        self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        self.assertEqual(behavior_map.unresolved(h.evidence()["behaviorMap"]), [])
        self.assertEqual(h.evidence()["runs"], before["runs"])
        reopened = h.update_map(slug, workflow_id, dict(reassessment="original interpretation is inadequate", dispositions=[
            dict(id="BM_ORIGIN", status="pending", evidence="replace the inadequate obligation with its corrected contract")]))
        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        replacement = pending_behavior("BM_REPLACEMENT")
        replacement.update(boundaryInputs=["value"], interpretations=["one", "two"],
                           interpretation="two", authority="replacement contract")
        moved = h.update_map(slug, workflow_id, dict(reassessment="adjudicated replacement", items=[replacement],
            dispositions=[dict(id="BM_ORIGIN", status="superseded", supersededBy="BM_REPLACEMENT", evidence="same obligation", boundaryInputs=["value"], interpretations=["one", "two"])]))
        self.assertEqual(moved.returncode, 0, "REOPENED_SUPERSESSION_REFUSED: " + moved.stdout + moved.stderr)
        carried = h.evidence()["behaviorMap"]
        self.assertEqual(carried[0]["supersededFrom"], "pending")
        self.assertEqual(carried[0]["redProof"], before["behaviorMap"][0]["redProof"])
        self.assertFalse(behavior_map.producer_proved(carried[0]))
        self.assertIn("BM_REPLACEMENT", behavior_map.unresolved(carried))
        command = h.write_unittest(3, "VALUE_NOT_TWO")
        test = h.repo / "test_app.py"
        test.write_text(test.read_text().replace("app.value", "getattr(app, 'value')"))
        red = h.tdd(slug, "red", "BM_REPLACEMENT", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (h.repo / "app.py").write_text("value = 3\n")
        green = h.tdd(slug, "green", "BM_REPLACEMENT", command)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertEqual(behavior_map.unresolved(h.evidence()["behaviorMap"]), [], "SUPERSEDED_HOLD_STRANDED")

    def test_indirect_zero_argument_helper_is_unresolved(self) -> None:
        h = self.harness
        item = pending_behavior("BM_HELPER")
        item.update(boundaryInputs=["--quiet"], interpretations=["retain", "ignore"],
                    interpretation="ignore", authority="existing verbosity contract")
        slug, _ = h.begin_with_map([item])
        (h.repo / "test_helper.py").write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
            "from hooks.lib.tdd_surface import identify\n"
            "def helper():\n    return identify(['pytest', '--quiet', 'test_a.py'])\n"
            "def test_selected():\n    assert helper()['runner'] == 'pytest'\n")
        result = h.tdd(slug, "red", "BM_HELPER", (*PYTEST_COMMAND, "-q", "test_helper.py::test_selected"))
        self.assertEqual(result.returncode, 0, "INDIRECT_INPUT_FALSE_MISSING: " + result.stdout + result.stderr)
        evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
        self.assertEqual(evidence["represented"], [])
        self.assertEqual(evidence["unresolved"], ["--quiet"])

    def test_literal_fixture_is_input_evidence(self) -> None:
        h = self.harness
        item = pending_behavior("BM_FIXTURE")
        item.update(boundaryInputs=["--quiet"], interpretations=["preserve verbosity", "ignore verbosity"],
                    interpretation="ignore verbosity", authority="tdd_surface Interface")
        slug, workflow_id = h.begin_with_map([item])
        path = h.repo / "test_fixture.py"
        for decorator in ("", "()"):
            path.write_text(
                f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport pytest\n"
                "from hooks.lib.tdd_surface import identify\n"
                f"@pytest.fixture{decorator}\ndef flags():\n    return ['pytest', '--quiet', 'test_a.py']\n"
                "def test_selected(flags):\n    assert identify(flags)['runner'] == 'pytest'\n")
            result = h.tdd(slug, "red", "BM_FIXTURE", (*PYTEST_COMMAND, "-q", "test_fixture.py::test_selected"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
            self.assertEqual(evidence["represented"], ["--quiet"], "INPUT_REPRESENTATION_FALSE_CLAIM")
            self.assertEqual(evidence["unresolved"], [])
            reassessed = h.update_map(slug, workflow_id, dict(reassessment="equivalent fixture spelling", dispositions=[
                dict(id="BM_FIXTURE", revalidate=True, evidence="changed selected fixture source")]))
            self.assertEqual(reassessed.returncode, 0, reassessed.stdout + reassessed.stderr)
        path.write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport pytest\n"
            "from hooks.lib.tdd_surface import identify\n"
            "@pytest.mark.parametrize('flag', ['-q', '--quiet'])\n"
            "def test_selected(flag):\n    assert identify(['pytest', flag, 'test_a.py'])['runner'] == 'pytest'\n")
        reassessed = h.update_map(slug, workflow_id, dict(reassessment="parameterized proof", dispositions=[
            dict(id="BM_FIXTURE", revalidate=True, evidence="changed selected input source")]))
        self.assertEqual(reassessed.returncode, 0, reassessed.stdout + reassessed.stderr)
        result = h.tdd(slug, "red", "BM_FIXTURE", (*PYTEST_COMMAND, "-q", "test_fixture.py::test_selected"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
        self.assertEqual(evidence["represented"], ["--quiet"], "INPUT_REPRESENTATION_FALSE_CLAIM")

    def test_mixed_key_input_reports_extraction_limit(self) -> None:
        h = self.harness
        item = pending_behavior("BM_MIXED")
        item.update(boundaryInputs=["absent"], interpretations=["mapping input", "output text"],
                    interpretation="mapping input", authority="JSON serialization Interface")
        slug, _ = h.begin_with_map([item])
        (h.repo / "test_mixed.py").write_text(
            "import json\ndef test_selected():\n"
            "    assert json.loads(json.dumps({1: 'a', 'x': 'b'}))['1'] == 'a'\n")
        result = h.tdd(slug, "red", "BM_MIXED", (*PYTEST_COMMAND, "-q", "test_mixed.py::test_selected"))
        self.assertEqual(result.returncode, 0, "MIXED_INPUT_CRASH: " + result.stdout + result.stderr)
        evidence = json.loads(result.stdout.splitlines()[-1])["inputEvidence"]
        self.assertEqual(evidence["unresolved"], ["absent"], "MIXED_INPUT_CRASH")
        self.assertEqual(evidence["represented"], [])

    def test_unsettled_preflight_is_recoverable(self) -> None:
        h = self.harness
        slug, workflow_id = h.begin_with_map([pending_behavior("BM_CHOICE")])
        path = h.tmp / f"{slug}-preflight.json"
        document = json.loads(path.read_text())
        document["openQuestions"] = "Which truthiness governs?"
        document["behaviorMap"][0].update(boundaryInputs=["SELECT 'x'"],
                                        interpretations=["host truthiness", "database truthiness"])
        path.write_text(json.dumps(document))
        result = h.cli("record-preflight", "--repo", str(h.repo), "--slug", slug,
                       "--workflow-id", workflow_id, "--input", str(path))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = json.loads(h.cli("status", "--repo", str(h.repo)).stdout)
        self.assertEqual(state["preflight"], "pending", "UNSETTLED_CHOICE_LOST")
        summary = h.cli("summary", "--repo", str(h.repo))
        self.assertIn(state["preflightLatestEvidence"], summary.stdout)
        stored = h.cli("evidence", "--repo", str(h.repo), "--evidence-id", state["preflightLatestEvidence"])
        self.assertEqual(json.loads(stored.stdout)["document"]["document"], document)
        from hooks.lib.workflow_state import ready_for_edit
        ready, missing = ready_for_edit(resolve_repo_identity(h.repo), "app.py")
        self.assertFalse(ready)
        self.assertIn("unsettled interpretation: BM_CHOICE", missing, "PENDING_CHOICE_DIAGNOSIS_LOST")

    def test_late_inputs_reuse_or_reopen_baseline(self) -> None:
        h = self.harness
        slug, workflow_id = h.begin_with_map([pending_behavior("BM_LATE")])
        command = ("git", "symbolic-ref", "HEAD")
        result = h.tdd(slug, "red", "BM_LATE", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        original = h.evidence()
        choice = dict(id="BM_LATE", boundaryInputs=["HEAD"],
                      interpretations=["symbolic name", "resolved object"],
                      interpretation="symbolic name", authority="git symbolic-ref contract")
        result = h.update_map(slug, workflow_id, dict(reassessment="late material choice", dispositions=[choice]))
        self.assertEqual(result.returncode, 0, "INTERPRETATION_REASSESSMENT_WRONG: " + result.stdout + result.stderr)
        self.assertEqual(h.evidence()["behaviorMap"][0]["status"], "already-satisfied")
        self.assertEqual(h.evidence()["runs"], original["runs"])
        result = h.update_map(slug, workflow_id, dict(reassessment="new missing input", dispositions=[
            dict(id="BM_LATE", boundaryInputs=["HEAD", "refs/heads/other"])]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BM_LATE", json.loads(result.stdout)["pending"])
        self.assertEqual(h.evidence()["runs"], original["runs"])

    def test_supersession_cannot_drop_discriminating_inputs(self) -> None:
        h = self.harness
        item = pending_behavior("BM_ORIGIN")
        item.update(boundaryInputs=["HEAD"], interpretations=["symbolic name", "object id"],
                    interpretation="symbolic name", authority="git contract")
        slug, workflow_id = h.begin_with_map([item])
        baseline = h.tdd(slug, "red", "BM_ORIGIN", ("git", "symbolic-ref", "HEAD"))
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        result = h.update_map(slug, workflow_id, dict(reassessment="replace obligation", items=[pending_behavior("BM_REPLACEMENT")],
            dispositions=[dict(id="BM_ORIGIN", status="superseded", supersededBy="BM_REPLACEMENT", evidence="same contract")]))
        self.assertEqual(result.returncode, 2, "UNCOVERED_FINDING_CLOSED: " + result.stdout + result.stderr)
        self.assertIn("boundaryInputs", result.stderr)

    def test_receipt_input_admission_reuses_selected_case_without_execution(self) -> None:
        h = self.harness
        item = pending_behavior("BM_RECEIPT")
        item.update(boundaryInputs=["--quiet"], interpretations=["preserve verbosity", "ignore verbosity"],
                    interpretation="ignore verbosity", authority="tdd_surface Interface")
        slug, _ = h.begin_with_map([item])
        counter = h.tmp / "executions"
        (h.repo / "test_receipt.py").write_text(
            f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nimport unittest\nfrom pathlib import Path\n"
            "from hooks.lib.tdd_surface import identify\n"
            f"counter = Path({str(counter)!r})\ncounter.write_text(counter.read_text() + 'x' if counter.exists() else 'x')\n"
            "class Cases(unittest.TestCase):\n"
            "    def test_missing(self):\n"
            "        case = {'args': ['pytest', '-q', 'test_a.py']}\n"
            "        self.assertEqual(identify(case['args'])['runner'], 'pytest')\n"
            "    def test_present(self):\n"
            "        case = {'args': ['pytest', '--quiet', 'test_a.py']}\n"
            "        self.assertEqual(identify(case['args'])['runner'], 'pytest')\n")
        executed = h.cli("verify", "--repo", str(h.repo), "--slug", slug, "--",
                         sys.executable, "-m", "unittest", "-v", "test_receipt")
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        receipt = json.loads(executed.stdout.splitlines()[-1])
        reference = receipt["evidenceId"] + ":" + str(receipt["runIndex"])
        for test, expected in (("test_missing", 2), ("test_present", 0)):
            result = h.cli("tdd", "--repo", str(h.repo), "--slug", slug, "--phase", "red",
                           "--behavior-id", "BM_RECEIPT", "--from-evidence", reference,
                           "--test-id", "test_receipt.Cases." + test)
            self.assertEqual(result.returncode, expected, "REUSED_INPUT_PROOF_WRONG: " + result.stdout + result.stderr)
            if expected == 2:
                self.assertNotIn("RED must fail", result.stderr, "INPUT_COVERAGE_REFUSAL_MISDIRECTS_REPAIR")
                self.assertIn("--from-evidence", result.stderr)
        self.assertEqual(counter.read_text(), "x")

    def test_reentry_recovers_late_interpretation_hold(self) -> None:
        h = self.harness
        slug, workflow_id = h.begin_with_map([pending_behavior("BM_HOLD")])
        executed = h.tdd(slug, "red", "BM_HOLD", ("git", "symbolic-ref", "HEAD"))
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        update = dict(reassessment="authority is missing", dispositions=[dict(id="BM_HOLD", boundaryInputs=["HEAD"],
                      interpretations=["symbolic name", "object id"])])
        path = h.tmp / "interrupted-update.json"
        path.write_text(json.dumps(update))
        command = [sys.executable, str(tdd_repairs.WORKFLOW), "tdd-map", "--repo", str(h.repo),
                   "--slug", slug, "--workflow-id", workflow_id, "--input", str(path)]
        before = h.evidence()
        # Hold a real competing writer so cancellation observes an open ledger,
        # rather than racing a complete transaction between /proc observations.
        database = next((h.tmp / "state").rglob("workflow.sqlite3"))
        connection = sqlite3.connect(database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            process = subprocess.Popen(command, cwd=h.repo, env=h.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 2
            opened = False
            while process.poll() is None and time.monotonic() < deadline:
                descriptors = Path(f"/proc/{process.pid}/fd")
                try:
                    opened = any(os.readlink(fd).endswith("workflow.sqlite3") for fd in descriptors.iterdir())
                except FileNotFoundError:
                    opened = False
                if opened:
                    break
                time.sleep(0.01)
            process.kill()
            process.communicate(timeout=10)
            self.assertTrue(opened, "cancellation did not reach the open ledger")
            self.assertEqual(process.returncode, -9)
        finally:
            connection.close()
        recovered = h.evidence()
        self.assertEqual(recovered["runs"], before["runs"])
        # Drop the response pipe: persistence must survive the real broken pipe.
        process = subprocess.Popen(command, cwd=h.repo, env=h.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        process.wait(timeout=10)
        process.stderr.close()
        # A fresh process recovers durable state independently of the response.
        recovered = json.loads(h.cli("status", "--repo", str(h.repo)).stdout)
        self.assertEqual(recovered["tdd"], "in-progress", "INTERPRETATION_REENTRY_LOST")
        before = h.evidence()
        retried = h.update_map(slug, workflow_id, update)
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(h.evidence(), before)
        settled = h.update_map(slug, workflow_id, dict(reassessment="governing authority supplied", dispositions=[
            dict(id="BM_HOLD", interpretation="symbolic name", authority="git symbolic-ref Interface")]))
        self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        self.assertEqual(json.loads(h.cli("status", "--repo", str(h.repo)).stdout)["tdd"], "passed")
        self.assertEqual(h.evidence()["runs"], before["runs"])
        updates = [
            dict(id="BM_HOLD", boundaryInputs=["HEAD", "refs/heads/missing"]),
            dict(id="BM_HOLD", authority="confirmed symbolic-ref governing contract"),
        ]
        commands = []
        for index, disposition in enumerate(updates):
            path = h.tmp / f"concurrent-{index}.json"
            path.write_text(json.dumps(dict(reassessment="concurrent input and authority updates", dispositions=[disposition])))
            commands.append([*command[:-1], str(path)])
        processes = [subprocess.Popen(argv, cwd=h.repo, env=h.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for argv in commands]
        for argv, process in zip(commands, processes):
            stdout, stderr = process.communicate(timeout=10)
            if process.returncode:
                self.assertIn(b"tdd evidence changed during the run", stderr.lower(), stdout + stderr)
                retry = subprocess.run(argv, cwd=h.repo, env=h.env, capture_output=True, timeout=10)
                self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        item = h.evidence()["behaviorMap"][0]
        self.assertEqual(item["boundaryInputs"], ["HEAD", "refs/heads/missing"])
        self.assertEqual(item["authority"], "confirmed symbolic-ref governing contract")
        self.assertIn("BM_HOLD", behavior_map.unresolved(h.evidence()["behaviorMap"]))
        self.assertEqual(h.evidence()["runs"], before["runs"])

    def test_late_missing_input_blocks_linked_finding_closure(self) -> None:
        h = finding_attacks.AttackHarness(methodName="runTest")
        h.setUp()
        try:
            (h.repo / "app.py").write_text("def read(name): return 1\n")
            h.git("add", "app.py")
            h.git("commit", "-qm", "input-bearing existing Interface")
            slug = "linked-input"
            wid = h.begin(slug)
            intake = h.behavioral_intake(slug, wid, "the reviewed value is wrong")
            recorded = h.record_preflight(slug, wid, h.owned_map(intake, marker="VALUE_NOT_TWO"))
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            probe = h.repo / "test_read.py"
            source = ("import unittest, app\nclass Read(unittest.TestCase):\n"
                      "    def test_read(self):\n        self.assertEqual(app.read('value'), 2, 'VALUE_NOT_TWO')\n")
            probe.write_text(source)
            command = [sys.executable, "-m", "unittest", "test_read.Read.test_read"]
            red = h.mapped_tdd(slug, "red", command)
            self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
            (h.repo / "app.py").write_text("def read(name): return 2\n")
            green = h.mapped_tdd(slug, "green", command)
            self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
            historical_id = h.status()["tddEvidence"]
            historical = h.ok("evidence", "--evidence-id", historical_id)
            update = h.json_file("late-input.json", dict(reassessment="new divergent input", dispositions=[
                dict(id="BM_ATTACK", boundaryInputs=["value", "other"], interpretations=["one key", "all keys"],
                     interpretation="all keys", authority="requested read contract")]))
            changed = h.ok("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
            self.assertEqual(changed["inputEvidence"]["BM_ATTACK"]["missing"], ["other"])
            disposition = h.fixed_disposition(wid, intake, dict(h.ZERO_DOMAIN))
            refused = h.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                            "--findings", "addressed", "--input", str(disposition))
            self.assertEqual(refused.returncode, 2, "UNCOVERED_FINDING_CLOSED: " + refused.stdout + refused.stderr)
            self.assertIn("BM_ATTACK", refused.stderr)
            probe.write_text(source + "        self.assertEqual(app.read('other'), 2)\n")
            green = h.mapped_tdd(slug, "green", command)
            self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
            disposition = h.fixed_disposition(wid, intake, dict(h.ZERO_DOMAIN))
            closed = h.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                           "--findings", "addressed", "--input", str(disposition))
            self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
            self.assertEqual(h.ok("evidence", "--evidence-id", historical_id), historical)
        finally:
            h.tearDown()

    def green_and_reassess(self, slug: str) -> tuple[str, str]:
        item = pending_behavior("BM_FINAL")
        slug, workflow_id = self.harness.begin_with_map([item], slug)
        command = self.harness.write_unittest(2, "VALUE_NOT_TWO")
        red = self.harness.tdd(slug, "red", "BM_FINAL", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.harness.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.harness.tdd(slug, "green", "BM_FINAL", command)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        assessed = self.harness.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_FINAL",
                "reassessment": "No new shared Seam, state boundary, or assumption.",
                "items": [],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        return slug, workflow_id


    def test_next_red_proceeds_without_a_map_update_after_green(self) -> None:
        # A proof that changes nothing records nothing: the next item's RED opens
        # without a tdd-map acknowledgement and completion demands none.
        marker = "GREEN_STILL_DEMANDS_EMPTY_REASSESSMENT"
        slug, _ = self.harness.begin_with_map([pending_behavior("BM_A"), pending_behavior("BM_B")], "no-empty-reassessment")
        command = self.harness.write_unittest(2, "VALUE_NOT_TWO")
        red = self.harness.tdd(slug, "red", "BM_A", command)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        (self.harness.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.harness.tdd(slug, "green", "BM_A", command)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        identity = resolve_repo_identity(self.harness.repo)
        state = read_workflow(identity)
        self.assertEqual([b for b in completion_blockers(identity, state) if "reassess" in b.lower()], [], marker)
        self.assertEqual([b for b in edit_blockers(identity, state) if "reassess" in b.lower()], [], marker)
        second = self.harness.tdd(slug, "red", "BM_B", self.harness.write_unittest(3, "VALUE_NOT_TWO"))
        self.assertEqual(second.returncode, 0, marker + ": " + second.stdout + second.stderr)

    def test_resolved_map_reopens_the_production_edit_window(self) -> None:
        # Refactor-while-green and the workflow's non-behavioral return edge
        # stay open once every mapped item is resolved and reassessed; a later
        # behavioral finding re-enters through a new mapped item at review.
        self.green_and_reassess("reopened-edit-window")
        identity = resolve_repo_identity(self.harness.repo)
        state = read_workflow(identity)
        self.assertEqual(edit_blockers(identity, state), [])

    def test_forced_color_pytest_assertion_is_valid_red(self) -> None:
        marker = "COLORED_PYTEST_PRODUCT_ASSERTION"
        item = pending_behavior("BM_COLOR", red_failure=marker)
        slug, _ = self.harness.begin_with_map([item], "pytest-color")
        (self.harness.repo / "test_color_pytest.py").write_text(
            f"def test_value():\n    assert False, {marker!r}\n", encoding="utf-8"
        )
        result = self.harness.tdd(
            slug,
            "red",
            "BM_COLOR",
            (*PYTEST_COMMAND, "--color=yes", "-q", "test_color_pytest.py"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.harness.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    def test_sentence_form_generic_marker_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "product behavior"):
            behavior_map.initial_items(
                [
                    pending_behavior(
                        "BM_SENTENCE",
                        red_failure="expected AttributeError because method is missing",
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
