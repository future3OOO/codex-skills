#!/usr/bin/env python3
"""Deal the suite's work across N workers, one line per job.

A line of space-separated unittest ids is one shard; a bare path is a job that
unittest cannot load and runs whole. Dealing round-robin spreads the slow cases
instead of clustering them in one module-sized job, which is what makes a
targeted selection of the slow modules parallelise as well as the full suite.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS = Path("hooks/tests")
# Not unittest modules: one pytest file and one shell script.
WHOLE = (
    "skills/production-code/scripts/test_code_quality_gate.py",
    "skills/codex-advisor/tests/test-ask-codex-advisor.sh",
)
# The installed estate carries skills/ and hooks/ only, so this one is absent
# there. Every other job is required: a missing one fails the run, loudly.
OPTIONAL = ".github/scripts/test_pr_scope.py"


def cases(suite: unittest.TestSuite, into: list[str]) -> None:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            cases(item, into)
        else:
            into.append(item.id())


def main(argv: list[str]) -> int:
    workers = max(1, int(argv[0]))
    # Ids load against the tree being dealt, the same root TESTS is relative to;
    # running this script by path puts hooks/tests on sys.path instead, and every
    # id then collapses to unittest's loader stub.
    sys.path.insert(0, str(Path.cwd()))
    loader = unittest.TestLoader()
    ids: list[str] = []
    whole: list[str] = []

    if argv[1:]:
        for job in argv[1:]:
            # Ahead of the branch split: Path("") is the current directory, which
            # exists, so an empty job name would otherwise be dealt as a whole job
            # and emitted as a blank line the runner cannot run.
            if not job:
                raise SystemExit(f"{sys.argv[0]}: no tests selected by an empty job name")
            if "\n" in job:
                raise SystemExit(f"{sys.argv[0]}: newline in job name: {job!r}")
            path = Path(job)
            if path.exists() and not path.is_file():
                raise SystemExit(f"{sys.argv[0]}: not a regular file: {job}")
            if path.suffix == ".py" and path.resolve().parent == TESTS.resolve():
                selected = loader.discover(str(path.parent), pattern=path.name, top_level_dir=".")
            elif path.exists():
                whole.append(job)
                continue
            else:
                selected = loader.loadTestsFromName(job)
            # Both loaders answer an empty suite rather than raising - discover for
            # a file that is absent or holds no cases, loadTestsFromName for a module
            # that imports and defines none - so a job the caller named would
            # otherwise be dealt away in silence. An unloadable id is not this case:
            # it comes back as unittest's one-case stub and runs, and fails, as a job.
            if not selected.countTestCases():
                raise SystemExit(f"{sys.argv[0]}: no tests selected by {job}")
            cases(selected, ids)
    else:
        cases(loader.discover(str(TESTS), pattern="test_*.py", top_level_dir="."), ids)
        whole = [*WHOLE, *([OPTIONAL] if Path(OPTIONAL).exists() else [])]

    ids.sort()
    for shard in range(workers):
        batch = ids[shard::workers]
        if batch:
            print(" ".join(batch))
    for job in whole:
        print(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
