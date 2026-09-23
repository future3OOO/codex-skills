#!/usr/bin/env python3
"""PostCompact: let unchanged advisories return to the new context window."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import read_hook_payload, session_key  # noqa: E402
from hooks.lib.state_store import reset_advisory_epoch  # noqa: E402


if __name__ == "__main__":
    reset_advisory_epoch(session_key(read_hook_payload()))
