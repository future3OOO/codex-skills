#!/usr/bin/env python3
"""Run a lead's advisor consult detached and hand its result back when it finishes, so no model request is spent waiting.

PreToolUse: a root session's shell command that names the advisor wrapper runs through `launch`. One that ends at
once (a refusal, a usage error, a command that only reads the wrapper) returns exactly as if run directly. One still
running is left detached: the lead is told to end its turn and from then on every call of that turn a hook sees is refused.
Stop: holds the turn end until the command finishes, then continues the turn with its result. A detached command's
result is delivered once, by the first hook of its turn to find it finished.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import read_hook_payload  # noqa: E402
from hooks.lib import secure_dir, state_root  # noqa: E402

WRAPPER = "ask-codex-advisor.sh"
GRACE = 0.6  # seconds a launch waits for a command that ends at once; leads give the call a one-second yield
LIMIT = 16000  # characters of output a delivered result carries
WAIT = "The advisor consult is running detached. End your turn now: its result is delivered to you when it finishes. Do not wait, poll, sleep or run anything else."
RESUME = "Continue from this result. Calls refused since the consult started were not run; make them now if still needed."
REFUSED = "Not run: the advisor consult is still running. End your turn now. Its result is delivered to you when it finishes, and you make this call then."


def mailbox(turn: str) -> Path:
    return state_root() / "_consults" / Path(turn).name


def trailer(turn: str) -> re.Pattern[bytes]:
    """The launch's own last line: the turn id, which no command knows, and the command's exit status."""
    return re.compile(rb"\n" + re.escape(turn.encode()) + rb" exit=(\d+)\n\Z")


def finished(box: Path, turn: str) -> bool:
    """The detached command's exit status is written, or no process holds its output any more."""
    with open(box, "rb") as handle:
        handle.seek(max(0, os.fstat(handle.fileno()).st_size - len(turn) - 32))
        if trailer(turn).search(handle.read()):
            return True
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def read(output: Path, turn: str) -> tuple[str, bytes]:
    """A finished command's exit status and output bytes; its file is removed."""
    data = output.read_bytes()
    output.unlink()
    status = trailer(turn).search(data)
    return (status.group(1).decode() if status else "unknown"), trailer(turn).sub(b"", data)


def launch(turn: str) -> int:
    import subprocess

    box = mailbox(turn)
    for stale in secure_dir(box.parent).iterdir():
        with suppress(FileNotFoundError):  # another turn's launch or delivery removed it meanwhile
            if time.time() - stale.stat().st_mtime > 86400:
                stale.unlink()
    starting = box.with_name(box.name + ".starting")  # the launch owns the output until it leaves the command detached
    try:
        output = os.open(starting, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(REFUSED)
        return 0
    if box.exists():
        starting.unlink()
        print(REFUSED)
        return 0
    fcntl.flock(output, fcntl.LOCK_EX)  # held by the detached command's output until it exits
    command = subprocess.Popen(["bash", "-c", 'bash -c "$0"; printf "\\n%s exit=%s\\n" "$1" $?', sys.stdin.read(), turn],
                               stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
    try:
        command.wait(GRACE)
    except subprocess.TimeoutExpired:
        os.rename(starting, box)  # from here the turn's hooks deliver it
        print(WAIT)
        return 0
    status, data = read(starting, turn)
    sys.stdout.buffer.write(data)  # the bytes a direct run prints
    return int(status) if status.isdigit() else 1


def main() -> int:
    if sys.argv[1:2] == ["launch"]:
        return launch(sys.argv[2])
    payload = read_hook_payload()
    event, turn = payload.get("hook_event_name"), payload.get("turn_id")
    if not isinstance(turn, str) or event not in ("PreToolUse", "Stop"):
        return 0
    box = mailbox(turn)
    if box.is_file():
        try:
            while not finished(box, turn):
                if event == "PreToolUse":
                    print(json.dumps({"decision": "block", "reason": REFUSED}))
                    return 0
                time.sleep(0.5)
            mine = box.with_name(f"{box.name}.{os.getpid()}")
            os.rename(box, mine)  # claimed: no other hook delivers it
        except FileNotFoundError:  # another hook of this turn delivered it meanwhile
            return 0
        status, data = read(mine, turn)
        text = data.decode(errors="replace")
        omitted = f"[{len(text) - LIMIT} earlier characters omitted]\n" if len(text) > LIMIT else ""
        print(json.dumps({"decision": "block", "reason": f"The advisor command {'finished' if status == '0' else 'failed'} (exit {status}).\n{omitted}{text[-LIMIT:]}\n{RESUME}"}))
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if event == "PreToolUse" and payload.get("tool_name") == "Bash" and "agent_id" not in payload and isinstance(command, str) and WRAPPER in command:
        import shlex

        end = "CONSULT_" + re.sub(r"\W", "_", turn)  # a here-document end no command contains
        launcher = f"python3 {shlex.quote(str(Path(__file__).resolve()))} launch {shlex.quote(turn)} <<'{end}'\n{command}\n{end}"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"command": launcher}}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
