"""Parse Codex hook input at one boundary."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import suppress
from pathlib import Path


_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE
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


def emit(event: str, **fields: object) -> None:
    """Write one hook's JSON answer; a closed reader never fails the tool call."""
    try:
        os.write(sys.stdout.fileno(), (json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}) + "\n").encode())
    except (OSError, ValueError):
        pass


def _heard(session: object) -> Path | None:
    """This session's record of what each advisory last said; no session, no record."""
    if not isinstance(session, str) or not session.strip():
        return None
    from .state_store import state_root
    return state_root() / "_advisories" / (hashlib.sha256(session.encode()).hexdigest()[:32] + ".json")


def advise(event: str, session: object, advisories: dict[str, str]) -> None:
    """Emit each named advisory only when its text changed since this session last
    heard it in the current compaction epoch; an empty text records silence, so a
    cleared advisory that returns is announced again. A failed record repeats, never loses."""
    from .state_store import atomic_write_json, read_json
    record = _heard(session)
    heard = (read_json(record) or {}) if record is not None else {}
    digests = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in advisories.items()}
    fresh = [name for name in advisories if heard.get(name) != digests[name]]
    if record is not None and fresh:
        try:
            atomic_write_json(record, {**heard, **digests})
        except OSError:
            pass
    if text := "\n".join(advisories[name] for name in fresh if advisories[name]):
        emit(event, additionalContext=text)


def reset_advisories(session: object) -> None:
    """Compaction spends every held advisory; the next one is heard again."""
    if (record := _heard(session)) is not None:
        with suppress(OSError):  # an unwritable record fails open, as in advise()
            record.unlink(missing_ok=True)
