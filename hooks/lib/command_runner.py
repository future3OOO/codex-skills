"""Command execution and bounded reporting shared by workflow recorders."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .repo_identity import RepoIdentity
from .state_store import _flock, read_json, utc_timestamp

MAX_CAPTURE = 16000
HELD = os.environ.pop("CODEX_WORKFLOW_HELD", None)  # where the tool relaying this call takes what its reply gives; nothing this call starts inherits it
TERM_GRACE_SECONDS = 0.2
KILL_GRACE_SECONDS = 0.2
GROUP_POLL_SECONDS = 0.05


def _interrupt(signum, frame):
    raise KeyboardInterrupt


@contextmanager
def interruptible():
    """Let normal unwinding clean owned processes and snapshots on termination."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run(
    command: list[str], identity: RepoIdentity, timeout: float,
    env: dict[str, str] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[bytes, int, bool]:
    """Run one command; a command is complete when its owned process group is.

    Output goes to a regular file, so the timeout cannot be extended by a
    descendant that escaped the group while holding the inherited stdout.
    """
    deadline = time.monotonic() + timeout
    with interruptible(), tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=str(identity.root),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
            env=env,
        )
        timed_out = True
        try:
            timed_out = not _group_exits_by(process, deadline, cancel)
        finally:
            if timed_out:
                _terminate(process)
        output.seek(0)
        raw = output.read()
    return raw, 124 if timed_out else int(process.returncode), timed_out


def _group_exits_by(process: subprocess.Popen[bytes], deadline: float, cancel: threading.Event | None = None) -> bool:
    """Reap the leader, then wait for its whole group, bounded by the deadline."""
    while True:
        if cancel is not None and cancel.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            process.wait(timeout=min(remaining, GROUP_POLL_SECONDS) if cancel is not None else remaining)
            break
        except subprocess.TimeoutExpired:
            if cancel is None:
                return False
    while os.name == "posix":
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
            return False
        time.sleep(GROUP_POLL_SECONDS)
    return True


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """TERM the owned group, grace, then KILL with a bounded reap."""
    _signal(process, signal.SIGTERM)
    if _group_exits_by(process, time.monotonic() + TERM_GRACE_SECONDS):
        return
    _signal(process, signal.SIGKILL)
    _group_exits_by(process, time.monotonic() + KILL_GRACE_SECONDS)


def _signal(process: subprocess.Popen[bytes], value: signal.Signals) -> None:
    if os.name != "posix":
        process.kill() if value == signal.SIGKILL else process.terminate()
        return
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        pass


def _tail(raw: bytes) -> str:
    """Decode the last MAX_CAPTURE bytes, starting after a valid character the cut split."""
    start = max(0, len(raw) - MAX_CAPTURE)
    for lead in range(start - 1, max(start - 4, -1), -1):
        byte = raw[lead]
        if byte & 0xC0 == 0x80:
            continue
        width = 1 if byte < 0xC0 else 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
        try:
            raw[lead:lead + width].decode("utf-8")
        except UnicodeDecodeError:
            break
        start = max(start, lead + width)
        break
    return raw[start:].decode("utf-8", errors="replace")


def run_entry(raw: bytes, exit_code: int, timed_out: bool, **fields: object) -> dict[str, object]:
    return {
        **fields,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "at": utc_timestamp(),
    }


def emit_json(value: object, given: tuple[str, str, str] | None = None, *, sort_keys: bool = True, send=None) -> None:
    """Choose and record at the final write. Only a relay's pending first package needs that choice handed along."""
    marker = given and not given[2] and Path(given[0])
    with _flock(Path(f"{marker}.lock")) if marker and not HELD and not marker.exists() else nullcontext():
        if marker and marker.exists():
            value.pop("caseRule")
            given = None
        if marker and given and HELD:
            Path(HELD).write_text(json.dumps((value, given)), encoding="utf-8")
            return
        remember = given and (lambda: Path(HELD).write_text(json.dumps((None, given)), encoding="utf-8") if HELD else marker.touch() if marker else record(*given))
        (send or print_output)("" if send and value is None else json.dumps(value, sort_keys=sort_keys) + "\n", remember)


def record(path: str, before: str, after: str) -> None:
    """Write `after`, what a delivered reply gave its reader. A reply recorded since `before` was read keeps all this one did not change."""
    if before != (Path(path).read_text(encoding="utf-8", errors="replace") if os.path.exists(path) else ""):
        kept, old = read_json(Path(path)) or {}, json.loads(before or "{}")
        after = json.dumps({key: kept.get(key, value) if old.get(key, type(value)()) == value else value if not isinstance(value, dict) else {**{name: entry for name, entry in kept.get(key, {}).items() if name in value or name not in old.get(key, {})},
                            **{name: entry for name, entry in value.items() if old.get(key, {}).get(name) != entry}} for key, value in json.loads(after).items()}, sort_keys=True)
    Path(path).write_text(after, encoding="utf-8")


def print_output(raw: str | bytes, commit=None) -> None:
    if not (text := raw if isinstance(raw, str) else _tail(raw)): return
    try:
        print(text, end="" if text.endswith("\n") else "\n", flush=isinstance(raw, str))
    except OSError:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    else:
        if commit: commit()
