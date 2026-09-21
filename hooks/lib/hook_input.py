"""Parse Codex hook input at one boundary."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .workflow_state import safe_slug

_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE
)
# Shell writes observed from the Codex Bash tool: > / >> redirects and tee.
# Deliberately narrow — cp/mv/sed -i are not claimed until seen from Codex.
_BASH_WRITE = re.compile(
    r'(?:>>?|tee\s+(?:-\S+\s+)*)\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s;|&]+))'
)


def read_hook_payload() -> dict[str, object]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve(value: str, cwd: str | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path.resolve(strict=False)


def edited_path(payload: dict[str, object]) -> Path | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("file_path") or tool_input.get("notebook_path")
    if isinstance(value, str) and value:
        return Path(value).expanduser().resolve(strict=False)
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else None
    tool_name = payload.get("tool_name")
    if tool_name == "apply_patch":
        match = _PATCH_PATH.search(command)
        return _resolve(match.group(1).strip(), cwd) if match else None
    if tool_name == "Bash":
        match = _BASH_WRITE.search(command)
        target = next((g for g in match.groups() if g), None) if match else None
        return _resolve(target, cwd) if target else None
    return None


def working_directory(payload: dict[str, object]) -> str:
    """Working directory across hook transports.

    Codex payloads carry `cwd`; `working_directory` is accepted for parity with
    other harnesses. Falls back to the hook's own process cwd.
    """
    for key in ("cwd", "working_directory"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    env = os.environ.get("CODEX_PROJECT_DIR")
    return env if env else os.getcwd()


def is_explorer_continuation(payload: dict[str, object]) -> bool:
    """Resolve the target in Codex's existing thread metadata, without recording it."""
    inputs = payload.get("tool_input")
    target = (inputs.get("target") or inputs.get("id")) if isinstance(inputs, dict) else None
    session = payload.get("session_id")
    if not isinstance(target, str) or not isinstance(session, str):
        return False
    database = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "state_5.sqlite"
    if not database.is_file():
        return False
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)) as connection:
        caller = connection.execute("SELECT agent_path FROM threads WHERE id = ?", (session,)).fetchone()
        if caller is None:
            return False
        path = target if target.startswith("/") else f"{caller[0] or '/root'}/{target}"
        roles = connection.execute(
            "SELECT agent_role FROM threads WHERE (id = ? OR agent_path = ?) AND "
            "CASE WHEN json_valid(source) THEN "
            "json_extract(source, '$.subagent.thread_spawn.parent_thread_id') END = ?",
            (target, path, session),
        ).fetchall()
    return roles == [("explorer",)]


def session_key(payload: dict[str, object]) -> str | None:
    """The session identifier as one state path segment, or None when absent.

    Derived here so the hook that records an association and the hook that reads
    it cannot drift, and so a hostile `session_id` is bounded to a single safe
    segment before it ever reaches the filesystem.

    Absence is returned rather than defaulted. A session key names a per-session
    set, so defaulting a missing id to any shared literal would file every
    anonymous payload under one identity and let one repository's pass reach
    another's Stop. Callers that want a display name for repository-scoped
    storage supply their own fallback.
    """
    value = payload.get("session_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return safe_slug(value)[:40]
