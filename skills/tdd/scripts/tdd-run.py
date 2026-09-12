#!/usr/bin/env python3
"""Compatibility shim for ``workflow tdd``."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.workflow_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["tdd", *sys.argv[1:]]))
