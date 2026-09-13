#!/usr/bin/env python3
"""Option-routing contracts for the mapped public TDD entrypoint."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import read_workflow  # noqa: E402
from hooks.tests import test_tdd_repairs as tdd_repairs  # noqa: E402
from hooks.tests.support import pending_behavior  # noqa: E402


class MappedTddDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = tdd_repairs.MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def test_argparse_abbreviations_use_the_same_mapped_route(self) -> None:
        marker = "ABBREVIATED_PRODUCT_FAILURE"
        item = pending_behavior("BM_ABBREV", red_failure=marker)
        slug, _ = self.harness.begin_with_map([item], "abbreviated-options")
        command = self.harness.write_unittest(2, marker)
        result = self.harness.cli(
            "tdd",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--pha",
            "red",
            "--behavior-i",
            "BM_ABBREV",
            "--",
            *command,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.harness.repo))
        self.assertEqual(state["tddCycleCount"], 1)
        self.assertEqual(self.harness.evidence()["behaviorId"], "BM_ABBREV")

    def test_equals_form_not_required_cannot_bypass_a_pending_map(self) -> None:
        item = pending_behavior("BM_PENDING")
        slug, _ = self.harness.begin_with_map([item], "equals-not-required")
        result = self.harness.cli(
            "tdd",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--not-required=shortcut",
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("every mapped item", result.stderr)
        state = read_workflow(resolve_repo_identity(self.harness.repo))
        self.assertEqual(state["tdd"], "pending")
        self.assertNotIn("tddEvidence", state)

    def test_legacy_candidate_flags_cannot_bypass_a_recorded_map(self) -> None:
        marker = "LEGACY_FLAGS_MUST_NOT_OPEN_MAPPED_RED"
        item = pending_behavior("BM_MAPPED", red_failure=marker)
        slug, _ = self.harness.begin_with_map([item], "legacy-flags")
        command = self.harness.write_unittest(2, marker)
        result = self.harness.cli(
            "tdd",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--phase",
            "red",
            "--behavior",
            "legacy free-form candidate",
            "--seam",
            "legacy seam",
            "--expected-failure",
            marker,
            "--",
            *command,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--behavior-id", result.stderr)
        state = read_workflow(resolve_repo_identity(self.harness.repo))
        self.assertEqual(state["tdd"], "pending")
        self.assertNotIn("tddEvidence", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
