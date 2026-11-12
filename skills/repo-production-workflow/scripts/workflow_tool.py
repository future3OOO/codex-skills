#!/usr/bin/env python3
"""The workflow as one blocking tool: a stdio MCP server whose `run` runs a workflow command to its end.

A shell call is handed back to the model after 30 seconds and then polled with model requests; a tool call returns
when the tool does. `run` starts `workflow.py <args>` in the repository of `cwd` and returns the command's whole
output; a failing command adds `exit status N` and marks the result an error. Without a verb the command is `next`,
the active pass's next operation, and `args` are added to it. The command gets the environment of the Codex process
that started this server: a server itself is given a handful of variables, without the workflow's state root or the
advisor's settings.

Each call has its own thread and process group. hooks/lib/command_runner owns the group and ends it when the request
is cancelled. When a session closes Codex can kill this server's own group at once (measured: SIGTERM, then SIGKILL
before a 50 ms wait returned), so nothing here may be needed for cleanup and the call's group ends itself: its leader
is this file again (`workflow_tool.py <args>`), which runs the command and watches a pipe only the server holds open.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

TOOL = {"name": "run", "description": "Run the active workflow pass's next step for the repository at `cwd` to its end and return its whole output. "
        "`args`: the arguments the step's `input` names, or another workflow.py verb with its arguments (summary, status, tdd, verify, consult, context).",
        "inputSchema": {"type": "object", "required": ["cwd"], "properties": {
            "cwd": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}}}


def lead(args: list[str]) -> int:
    """A call's leader: run the command with no input. Leading a session of its own, as the server starts it, it ends
    its whole group when the server is gone: the server's pipe, this stdin, then reads empty."""
    if os.getsid(0) == os.getpid():
        threading.Thread(target=lambda: os.read(0, 1) or os.killpg(0, signal.SIGKILL), daemon=True).start()
    return subprocess.run([sys.executable, str(Path(__file__).with_name("workflow.py")), *args], stdin=subprocess.DEVNULL, check=False).returncode


def serve() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from hooks.lib.command_runner import run
    from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity

    environment = dict(entry.split("=", 1) for entry in os.fsdecode(Path(f"/proc/{os.getppid()}/environ").read_bytes()).split("\0") if "=" in entry)
    messages = os.fdopen(os.dup(0), "rb")
    os.dup2(os.pipe()[0], 0)  # what every command inherits as stdin; the write end stays open, and only here, for as long as this process lives
    running: dict[object, threading.Event] = {}
    lock = threading.Lock()

    def send(ident: object, key: str, value: object) -> None:
        with lock:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, key: value}) + "\n")
            sys.stdout.flush()

    def call(ident: object, arguments: dict, cancel: threading.Event) -> None:
        try:
            cwd, args = arguments.get("cwd"), arguments.get("args", [])
            if not isinstance(cwd, str) or not isinstance(args, list) or not all(isinstance(value, str) for value in args):
                raise ValueError("run takes cwd, a path, and args, a list of strings")
            if not args or args[0].startswith("-"):
                args = ["next", *args]
            if args[0] == "begin":
                raise ValueError("begin reads the session's opening message: run it in the shell")
            raw, code, _ = run([sys.executable, __file__, *args], resolve_repo_identity(cwd), float("inf"), environment, cancel)
            text = raw.decode(errors="replace") + (f"exit status {code}" if code else "")
        except (RepoIdentityError, ValueError, OSError) as exc:
            code, text = 2, f"error: {exc}"
        running.pop(ident, None)
        if not cancel.is_set():
            send(ident, "result", {"content": [{"type": "text", "text": text}], "isError": bool(code)})

    for line in messages:
        try:
            message = json.loads(line)
        except ValueError:  # JSON-RPC's parse error, invalid UTF-8 and a blank line included; the next request is answered
            send(None, "error", {"code": -32700, "message": "parse error"})
            continue
        method, ident, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "tools/call":
            cancel = running[ident] = threading.Event()
            threading.Thread(target=call, args=(ident, params.get("arguments") or {}, cancel), daemon=True).start()
        elif method == "notifications/cancelled":
            running.get(params.get("requestId"), threading.Event()).set()
        elif method == "initialize":
            send(ident, "result", {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                                   "serverInfo": {"name": "workflow", "version": "1"}})
        elif method == "tools/list":
            send(ident, "result", {"tools": [TOOL]})
        elif ident is not None and method:
            send(ident, "error", {"code": -32601, "message": f"unknown method: {method}"})


if __name__ == "__main__":
    raise SystemExit(lead(sys.argv[1:]) if sys.argv[1:] else serve())
