#!/usr/bin/env python3
"""Hold a lead's poll of a running workflow command until the command has ended, so it polls once and that poll never waits.

workflow-command.sh starts this only for a shell call that names a workflow entry point and for a model answer of a
turn that has such a command. PreToolUse: the root session's command gets one `exec` put before it on its first line,
which keeps a FIFO open for as long as the command or anything it started runs; the command itself runs as written
and returns what a direct run returns. PostModelRequest (after the model answers, before the answer's tool calls
start): when the command still runs and the answer only waits on what that command's own call handed back, return
when the command has ended, so the poll collects the finished result at once. No other answer is held, and a wait
on something else only when the same call's result also handed that back.
"""
from __future__ import annotations

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


def ended(reader: int, wait: bool) -> bool:
    """No process holds the FIFO open any more: the command and all it started have ended. With `wait`, return only then."""
    import select

    while True:
        try:
            if not os.read(reader, 65536):
                return True
        except BlockingIOError:
            if not wait:
                return False
            select.select([reader], [], [])


def waits(name: object, text: str) -> str:
    """What one tool call of an answer waits on, as a pattern for the earlier result that named it: a code cell given to the `wait` tool, or
    the bare numbers of the sessions a code cell polls with no characters written (a lead may print `SESSION 60607` in place of the raw result).
    Empty when the call does anything else."""
    cells, polled, tools = re.findall(r"cell_id\W+(\d+)", text), re.findall(r"session_id\W+(\d+)", text), re.findall(r"\btools\.(\w+)\s*\(", text)
    if name == "wait" and cells and not re.search(r"terminate\W+true", text):  # ending a cell is not waiting on it
        return rf"cell ID (?:{'|'.join(cells)})\b"
    if name == "exec" and polled and set(tools) == {"write_stdin"} and len(tools) == len(re.findall(r"""\bchars["']?\s*:\s*(?:""|''|``)""", text)):
        return rf"\b(?:{'|'.join(polled)})\b"
    return ""


def awaited(transcript: object) -> list[tuple[float, ...]]:
    """When the calls ran (made, returned) whose results named what the answer just given, the last items of its session's transcript, waits on.
    Empty unless that answer makes tool calls and every one only waits."""
    from datetime import datetime

    targets, returned, spans, answer = [], {}, [], True
    with suppress(OSError, ValueError, AttributeError), open(str(transcript), "rb") as log:  # an unreadable transcript: not known to wait, so not held
        log.seek(max(0, os.fstat(log.fileno()).st_size - 262144))
        for line in reversed(log.read().decode(errors="replace").splitlines()):
            row = json.loads(line)
            item = row.get("payload") or {}
            kind = str(item.get("type")) if row.get("type") == "response_item" else ""  # token and event records lie around the answer
            if kind.endswith("_call") and answer:
                targets.append(waits(item.get("name"), str(item.get("input") or item.get("arguments") or "")))
            elif kind.endswith("_call") and item.get("call_id") in returned:
                spans.append(tuple(datetime.fromisoformat(str(stamp)).timestamp() for stamp in (row.get("timestamp"), returned[item["call_id"]])))
            elif kind.endswith("_output"):
                answer = False  # an earlier result: the answer starts after it
                if not (targets and all(targets)):
                    break
                if re.search("|".join(targets), str(item.get("output"))):
                    returned[item.get("call_id")] = row.get("timestamp")
    return spans


def main() -> int:
    payload = read_hook_payload()
    turn = payload.get("turn_id")
    if not isinstance(turn, str) or not re.fullmatch(r"[\w-]+", turn):  # it names this turn's FIFOs
        return 0
    running = state_root() / "_running"
    if payload.get("hook_event_name") == "PostModelRequest":
        spans = None  # the answer is read once, and only if a command of the turn still runs
        for fifo in running.glob(turn + ".*"):
            with suppress(FileNotFoundError):  # the command never opened it and a sweep removed it
                reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                over = ended(reader, False)
                if not over:
                    spans = awaited(payload.get("transcript_path")) if spans is None else spans
                    made = fifo.stat().st_mtime  # held only for the call that started this command: made after that call, before its result
                    over = any(start <= made <= end for start, end in spans) and ended(reader, True)
                os.close(reader)
                if over:
                    fifo.unlink()
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if payload.get("hook_event_name") == "PreToolUse" and "agent_id" not in payload and isinstance(command, str):
        import shlex

        for stale in secure_dir(running).iterdir():
            with suppress(FileNotFoundError):  # another turn's hook removed it meanwhile
                if time.time() - stale.stat().st_mtime > 86400:
                    stale.unlink()
        fifo = running / f"{turn}.{os.urandom(8).hex()}"
        os.mkfifo(fifo, 0o600)
        held = f"exec 8<>{shlex.quote(str(fifo))}; {command}"  # descriptor 8 stays open in the command's shell and all it starts; the advisor wrapper takes 9 for its own lock
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"command": held}}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
