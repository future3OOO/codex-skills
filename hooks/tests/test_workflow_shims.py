#!/usr/bin/env python3
"""Narrow forwarding contracts for temporary workflow compatibility scripts."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
CASES = (
    (ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py", ()),
    (ROOT / "skills" / "repo-production-workflow" / "scripts" / "verify-run.py", ("verify",)),
    (ROOT / "skills" / "tdd" / "scripts" / "tdd-run.py", ("tdd",)),
    (ROOT / "skills" / "code-review" / "scripts" / "record-review.py", ("record-review",)),
    (ROOT / "skills" / "production-preflight" / "scripts" / "record-preflight.py", ("record-preflight",)),
    (ROOT / "skills" / "production-code" / "scripts" / "record-production-code.py", ("record-production-code",)),
)


class WorkflowShimTests(unittest.TestCase):
    def run_command(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_shims_forward_without_owning_behavior(self) -> None:
        for shim, canonical_prefix in CASES:
            with self.subTest(shim=shim.name):
                forwarded = self.run_command(shim)
                canonical = self.run_command(WORKFLOW, *canonical_prefix)
                self.assertEqual(
                    (forwarded.returncode, forwarded.stdout, forwarded.stderr),
                    (canonical.returncode, canonical.stdout, canonical.stderr),
                )


class CompleteHelpContractTests(unittest.TestCase):
    def run_help(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_every_subcommand_serves_argparse_help(self) -> None:
        # The estate contract: every registered verb answers --help with
        # argparse usage at exit 0, no workflow state required.
        listing = self.run_help("--help")
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        import re
        verbs = re.search(r"\{([a-z0-9,-]+)\}", listing.stdout)
        self.assertIsNotNone(verbs, listing.stdout)
        names = verbs.group(1).split(",")
        self.assertIn("tdd-map", names, "TDDMAP_UNLISTED")
        for verb in names:
            with self.subTest(verb=verb):
                result = self.run_help(verb, "--help")
                self.assertEqual(
                    result.returncode, 0,
                    f"TDD_HELP_REGRESSED {verb}: " + result.stdout + result.stderr,
                )
                self.assertIn("usage:", result.stdout, f"TDD_HELP_REGRESSED {verb}")

    def test_positioned_help_matches_bare_help(self) -> None:
        # PRES-A: help tokens are honored anywhere in the recorder region -
        # positioned forms serve byte-identical usage to the bare form.
        bare = self.run_help("tdd", "--help")
        self.assertEqual(bare.returncode, 0, bare.stdout + bare.stderr)
        for form in (("tdd", "--repo", ".", "--help"), ("tdd", "--slug", "x", "-h")):
            with self.subTest(form=form):
                result = self.run_help(*form)
                self.assertEqual(
                    result.returncode, 0,
                    "POSITIONED_HELP_LOST: " + result.stdout + result.stderr,
                )
                self.assertEqual(result.stdout, bare.stdout, "POSITIONED_HELP_LOST")

    def test_tdd_help_presents_the_dual_flag_surface(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.run_help("tdd", flag)
                self.assertEqual(
                    result.returncode, 0,
                    "TDD_HELP_REGRESSED: " + result.stdout + result.stderr,
                )
                self.assertIn("usage:", result.stdout, "TDD_HELP_REGRESSED")
                self.assertIn("--behavior-id", result.stdout, "TDD_HELP_REGRESSED")
                self.assertIn("--behavior", result.stdout, "TDD_HELP_REGRESSED")
                self.assertIn("--seam", result.stdout, "TDD_HELP_REGRESSED")

    def test_json_emission_has_one_owner(self) -> None:
        # The reporting-failure policy lives once, in command_runner: neither
        # CLI module carries a private emitter copy.
        import pathlib
        lib = pathlib.Path(__file__).resolve().parents[1] / "lib"
        runner = (lib / "command_runner.py").read_text(encoding="utf-8")
        self.assertIn("def emit_json", runner, "EMITTER_DUPLICATED")
        for name in ("workflow_cli.py", "tdd_workflow.py"):
            text = (lib / name).read_text(encoding="utf-8")
            self.assertNotIn("def _emit_json", text, f"EMITTER_DUPLICATED: {name}")

    def test_help_equals_forms_stay_refused(self) -> None:
        # Only exact -h/--help tokens serve help: the equals forms are
        # malformed input and keep their pre-change refusal.
        for arg in ("--help=bogus", "-h=x"):
            with self.subTest(arg=arg):
                result = self.run_help("tdd", arg)
                self.assertEqual(
                    result.returncode, 2,
                    "HELP_EQUALS_ADMITTED: " + result.stdout + result.stderr,
                )
                self.assertNotIn("usage:", result.stdout, "HELP_EQUALS_ADMITTED")

    def test_complete_help_serves_argparse_usage(self) -> None:
        # The one-entry fusion restores main's public surface: `complete --help`
        # is ordinary argparse help (exit 0, usage on stdout). The #138 umbrella
        # briefly regressed this to an undocumented no-help error; that parser
        # is deleted and this pin keeps the estate contract stable.
        result = subprocess.run(
            [sys.executable, str(WORKFLOW), "complete", "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
