#!/usr/bin/env python3
"""The lane decision through the real script over real repositories.

Only what the gate's path policy does not prove: which delta shapes reach a lane,
and that one non-documentation path takes the whole delta to production.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("pr_scope.py")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


class PrScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-scope-"))
        self.repo = self.tmp / "repo"
        (self.repo / "docs").mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "T")
        (self.repo / "docs" / "a.md").write_text("a\n", encoding="utf-8")
        (self.repo / "app.py").write_text("def compute():\n    return 1\n", encoding="utf-8")
        self.base = self.commit()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=self.repo, env=ENV, text=True, capture_output=True, check=True)
        return done.stdout.strip()

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "delta")
        return self.git("rev-parse", "HEAD")

    def scope(self, *, base: str | None = None, repo: Path | None = None) -> tuple[int, str]:
        done = subprocess.run([sys.executable, str(SCRIPT), str(repo or self.repo), base or self.base],
                              cwd=self.tmp, env=ENV, text=True, capture_output=True, check=False)
        return done.returncode, done.stdout.strip()

    def test_documentation_alone_takes_the_cheap_lane_whatever_its_name(self) -> None:
        (self.repo / "docs" / "b.md").write_text("b\n", encoding="utf-8")
        (self.repo / "docs" / "a.md").unlink()
        with open(os.fsencode(os.fsdecode(bytes(self.repo / "docs") + b"/br\xffken.md")), "wb") as handle:
            handle.write(b"prose\n")
        self.commit()
        self.assertEqual(self.scope(), (0, "lane=docs"), "DOCS_ONLY_DELTA_NOT_CHEAP_LANE")

    def test_one_non_documentation_path_takes_the_whole_delta_to_production(self) -> None:
        marker = "LANE_WAS_NOT_ALL_OR_NOTHING"
        (self.repo / "requirements").mkdir()
        (self.repo / "docs" / "a.md").write_text("a\n\nmore\n", encoding="utf-8")
        (self.repo / "requirements" / "dev.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
        self.commit()
        self.assertEqual(self.scope(), (0, "lane=production"), marker)
        base = self.git("rev-parse", "HEAD")
        self.git("mv", "docs/a.md", "notes.py")
        self.commit()
        self.assertEqual(self.scope(base=base), (0, "lane=production"), marker)

    def test_no_usable_delta_takes_the_normal_lane(self) -> None:
        marker = "NO_DELTA_TOOK_THE_CHEAP_LANE"
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        self.assertEqual(self.scope(), (0, "lane=production"), marker)
        self.assertEqual(self.scope(base="0" * 40), (0, "lane=production"), marker)
        self.assertEqual(self.scope(repo=outside), (0, "lane=production"), marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
