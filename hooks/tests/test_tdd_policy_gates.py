#!/usr/bin/env python3
"""Workflow gate contracts around mapped TDD proof completion."""
from __future__ import annotations

import importlib.util
import sys
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

EDIT_HOOK = ROOT / "hooks" / "code-quality-gate.py"
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None
PYTEST_COMMAND = (sys.executable, "-m", "pytest")


class MappedTddPolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = tdd_repairs.MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

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
