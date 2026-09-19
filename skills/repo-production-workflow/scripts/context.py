#!/usr/bin/env python3
"""Produce and recover bounded context without claiming prior model delivery."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.context_evidence import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
