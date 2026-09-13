"""Command execution and bounded reporting shared by workflow recorders."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

from .repo_identity import RepoIdentity
from .state_store import utc_timestamp

MAX_CAPTURE = 16000
TERM_GRACE_SECONDS = 0.2
KILL_GRACE_SECONDS = 0.2
GROUP_POLL_SECONDS = 0.05


def run(
    command: list[str], identity: RepoIdentity, timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[bytes, int, bool]:
    """Run one command; a command is complete when its owned process group is.

    Output goes to a regular file, so the timeout cannot be extended by a
    descendant that escaped the group while holding the inherited stdout.
    """
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=str(identity.root),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
            env=env,
        )
        timed_out = not _group_exits_by(process, deadline)
        if timed_out:
            _terminate(process)
        output.seek(0)
        raw = output.read()
    return raw, 124 if timed_out else int(process.returncode), timed_out


def _group_exits_by(process: subprocess.Popen[bytes], deadline: float) -> bool:
    """Reap the leader, then wait for its whole group, bounded by the deadline."""
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return False
    while os.name == "posix":
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
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
        "outputTail": _tail(raw),
        "at": utc_timestamp(),
    }


def emit_json(value: object) -> None:
    try:
        print(json.dumps(value, sort_keys=True), flush=True)
    except OSError:
        mute_stdout()


def mute_stdout() -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(descriptor, sys.stdout.fileno())
    finally:
        os.close(descriptor)


def print_output(raw: bytes) -> None:
    output = _tail(raw)
    if output:
        try:
            print(output, end="" if output.endswith("\n") else "\n")
        except OSError:
            mute_stdout()
