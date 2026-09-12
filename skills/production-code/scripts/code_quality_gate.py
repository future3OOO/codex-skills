#!/usr/bin/env python3
"""Generic production code quality gate for changed source scope.

Never modifies the working tree, staged content, refs, or tracked state.
Staged-mode capture may refresh the index's cache-tree extension, and
capturing the candidate tree can leave unreferenced loose objects in the
object database; git gc prunes them and no repository state references them.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[2]
for path in (SCRIPT_DIR, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _quality_gate.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
