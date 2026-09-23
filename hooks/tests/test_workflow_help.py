#!/usr/bin/env python3
"""Public workflow help contracts."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
ADVISOR = ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh"
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
        self.assertIn("record", names, "RECORD_UNLISTED")
        for old in ("record-preflight", "record-review", "tdd-map", "advisor-result", "advisor-disposition"):
            self.assertNotIn(old, names, f"OLD_VERB_STILL_LISTED: {old}")
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

    def test_tdd_help_presents_only_the_mapped_flag_surface(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.run_help("tdd", flag)
                self.assertEqual(
                    result.returncode, 0,
                    "TDD_HELP_REGRESSED: " + result.stdout + result.stderr,
                )
                self.assertIn("usage:", result.stdout, "TDD_HELP_REGRESSED")
                self.assertIn("--behavior-id", result.stdout, "TDD_HELP_REGRESSED")
                self.assertNotIn("--behavior ", result.stdout, "LEGACY_TDD_FLAG_RETAINED")
                self.assertNotIn("--seam", result.stdout, "LEGACY_TDD_FLAG_RETAINED")

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

    def test_advisor_rejects_retired_anchor_flags_and_keeps_fresh(self) -> None:
        result = subprocess.run(
            [str(ADVISOR), "--packet", "/tmp/unused-packet", "--fresh"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unknown argument: --packet", result.stderr, "ADVISOR_FLAGS_DRIFT")
        self.assertIn("--fresh", result.stderr, "ADVISOR_FLAGS_DRIFT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
