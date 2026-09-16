#!/usr/bin/env python3
"""Replay a captured command corpus through hook_input.read_candidates and compare
against the labels the transcript analysis produced. The labels are the spec: a later
relabelling is a spec change, not a matcher regression.

Input: a JSON list of {"command": str, "reads": [path, ...]} entries. Output: one
summary line plus every miss (labelled path the matcher did not return) and every
extra (matcher path the labels did not carry). Exit 1 on any miss."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hooks.lib.hook_input import read_candidates  # noqa: E402


def normalise(path: str, home: str, cwd: str) -> str:
    path = path.replace("$HOME", home).replace("${HOME}", home)
    path = path.replace('"$PWD"', cwd).replace("$PWD", cwd)
    return home + path[1:] if path.startswith("~") else path


def main(argv: list[str]) -> int:
    labels_path, home, cwd = argv[0], argv[1] if len(argv) > 1 else os.path.expanduser("~"), argv[2] if len(argv) > 2 else os.getcwd()
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    misses: list[tuple[str, str]] = []
    extras: list[tuple[str, str]] = []
    labelled = matched = 0
    for entry in labels:
        expected = set(entry["reads"])
        found = {normalise(path, home, cwd) for path in read_candidates(entry["command"])}
        labelled += len(expected)
        matched += len(expected & found)
        misses.extend((entry["command"][:100], path) for path in sorted(expected - found))
        extras.extend((entry["command"][:100], path) for path in sorted(found - expected))
    print(f"commands={len(labels)} labelled={labelled} matched={matched} misses={len(misses)} extras={len(extras)}")
    for kind, rows in (("MISS", misses), ("EXTRA", extras)):
        for command, path in rows:
            print(f"{kind} {path!r} <- {command!r}")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
