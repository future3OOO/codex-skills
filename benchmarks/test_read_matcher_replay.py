#!/usr/bin/env python3
"""The read matcher reproduces the CX2 corpus labels: 350 shell commands from the
CX2 lead rollout with the 239 read paths the transcript analysis attributed. The
labels are the spec; relabelling is a spec change, never a matcher regression."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks import read_matcher_replay  # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "cx2_read_labels.json"


class ReadMatcherReplayTests(unittest.TestCase):
    def test_every_labelled_read_is_matched(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = read_matcher_replay.main([str(FIXTURE), "/home/prop_", "/home/prop_/dswe-run-issue95/armCX2/app"])
        summary = out.getvalue().splitlines()[0]
        self.assertEqual(code, 0, "READ_CANDIDATES_WRONG: " + out.getvalue()[:2000])
        self.assertIn("labelled=239 matched=239 misses=0", summary, "READ_CANDIDATES_WRONG: " + summary)


if __name__ == "__main__":
    unittest.main()
