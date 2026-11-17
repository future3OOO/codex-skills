"""Shared implementation for repo-production-workflow lifecycle hooks."""

import os
import re
from pathlib import Path

# A Codex session's own record: its rollout log, which a live session holds open.
SESSION_LOG = re.compile(r"/sessions/.+/rollout-.+-([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def secure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def state_root() -> Path:
    override = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
    return secure_dir(Path(override).expanduser() if override else codex_home() / "state")
