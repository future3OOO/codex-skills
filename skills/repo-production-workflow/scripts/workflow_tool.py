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
import io
import os
import signal
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

TOOL = {"name": "run", "description": "Run the active workflow pass's next step for the repository at `cwd` to its end and return its whole output. "
        "`args`: the arguments the step's `input` names, or another workflow.py verb with its arguments (begin, summary, status, tdd, verify, consult, context).",
        "inputSchema": {"type": "object", "required": ["cwd"], "properties": {
            "cwd": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}}}


def session(parent: int) -> str | None:
    """The thread whose session log `parent`, the Codex process, holds open: a live thread holds its own. None unless
    exactly one is open."""
    from hooks.lib import SESSION_LOG
    threads = set()
    for descriptor in os.listdir(f"/proc/{parent}/fd"):
        try:
            match = SESSION_LOG.search(os.readlink(f"/proc/{parent}/fd/{descriptor}"))
        except OSError:  # closed since it was listed
            continue
        if match:
            threads.add(match[1])
    return threads.pop() if len(threads) == 1 else None


def lead(args: list[str]) -> int:
    """A call's leader: run the command here, with no input. Leading a session of its own, as the server starts it, it
    ends its whole group when the server is gone: the server's pipe, held on a copy of this stdin, then reads empty."""
    if os.getsid(0) == os.getpid():
        threading.Thread(target=lambda watched=os.dup(0): os.read(watched, 1) or os.killpg(0, signal.SIGKILL), daemon=True).start()
    with open(os.devnull, "rb") as empty:
        os.dup2(empty.fileno(), 0)
    from hooks.lib.workflow_cli import main
    if args[0] == "begin":  # its receipt names its next step, the bootstrap, which this same call runs and reports
        try:
            with redirect_stdout(io.StringIO()) as begun:
                code = main(args, replace=False)
        except SystemExit:  # --help: its text is the reply
            print(begun.getvalue(), end="")
            raise
        return code or main(["next"], replace=False)
    return main(args, replace=False)


def serve() -> None:
    from hooks.lib.command_runner import emit_json, run
    from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity
    from hooks.lib.workflow_state import LedgerError, abandon, operation_receipt

    environment = dict(entry.split("=", 1) for entry in os.fsdecode(Path(f"/proc/{os.getppid()}/environ").read_bytes()).split("\0") if "=" in entry)
    os.environ.update(environment)  # what this process reads itself, as abandon does, comes from where every command's does
    messages = os.fdopen(os.dup(0), "rb")
    os.dup2(os.pipe()[0], 0)  # what every command inherits as stdin; the write end stays open, and only here, for as long as this process lives
    running: dict[object, threading.Event] = {}
    asked: dict[str, list] = {}  # a question to the user by its request id: [answered, the host's result]
    host: dict = {}  # the capabilities the host declared
    lock, finishing, writer = threading.Lock(), threading.Lock(), None
    def send(ident: object, message, given: bytes = b"") -> None:
        def write(body="", commit=None):
            nonlocal writer
            wire = memoryview((json.dumps({"jsonrpc": "2.0", "id": ident, **(message(body) if callable(message) else message)}) + "\n").encode())
            with lock:
                if commit:
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGPIPE})
                    writer = threading.current_thread()  # published before any write; retired under finishing
                try:
                    while wire: wire = wire[os.write(1, wire):]
                    if commit: commit()
                except OSError:
                    if not wire or not os.path.sameopenfile(0, 1): raise  # not the write deliberately interrupted at input EOF
                finally:
                    if commit:
                        with finishing: writer = None
        if given: emit_json(*json.loads(given), send=write)
        else: write()

    def ask(message: str, cancel: threading.Event) -> str:
        """The user's answer, as the host asks them: accept only when they answer yes, else decline or cancel; unavailable
        when the host cannot ask. A host that may not interrupt its user declines a required answer (measured: Codex with
        approval_policy never declines it, and accepts a form with nothing to answer)."""
        modes = host.get("elicitation")
        if modes is None or modes and "form" not in modes:
            return "unavailable"
        ident = "ask-" + os.urandom(8).hex()
        waiting = asked[ident] = [threading.Event(), None]
        send(ident, {"method": "elicitation/create", "params": {"message": message, "requestedSchema": {"type": "object",
            "required": ["abandon"], "properties": {"abandon": {"type": "boolean", "title": "Abandon this pass"}}}}})
        while not waiting[0].wait(0.05):
            if cancel.is_set():
                asked.pop(ident, None)
                return "cancel"
        answer = waiting[1] if isinstance(waiting[1], dict) else {}
        if answer.get("action") == "accept":
            return "accept" if (answer.get("content") or {}).get("abandon") is True else "decline"
        return "decline" if answer.get("action") == "decline" else "cancel"

    def call(ident: object, arguments: dict, cancel: threading.Event) -> None:
        with os.fdopen(os.memfd_create("workflow-given"), "rb") as held:
            try:
                cwd, args, suffix = arguments.get("cwd"), arguments.get("args", []), ""
                if not isinstance(cwd, str) or not isinstance(args, list) or not all(isinstance(value, str) for value in args):
                    raise ValueError("run takes cwd, a path, and args, a list of strings")
                if not args or args[0].startswith("-"):
                    args = ["next", *args]
                env = environment
                if args[0] == "begin":  # it reads the calling session's opening message by CODEX_THREAD_ID; with no single session, none
                    thread = session(os.getppid())
                    env = {key: value for key, value in environment.items() if key != "CODEX_THREAD_ID"} | ({"CODEX_THREAD_ID": thread} if thread else {})
                identity = resolve_repo_identity(cwd)
                if args == ["abandon"]:  # the user is asked, by the host, whether to end the open pass; it ends here only on their acceptance
                    raw, code, _ = run([sys.executable, __file__, "status", "--fields", "slug,phase,workflowId"], identity, float("inf"), env, cancel)
                    state = json.loads(raw) if not code else {}
                    answer = ask(f"Abandon workflow pass {state['slug']} in {identity.root}? It ends without completing; its evidence stays.",
                                 cancel) if state.get("phase") not in {None, "complete", "abandoned"} else None
                    ended = abandon(identity, answer, state.get("workflowId"))
                    code, text = 0, json.dumps(operation_receipt(ended, identity, abandoned=ended["abandoned"]), sort_keys=True)
                else:
                    raw, code, _ = run([sys.executable, __file__, *args], identity, float("inf"), env | {"CODEX_WORKFLOW_HELD": f"/proc/{os.getpid()}/fd/{held.fileno()}"}, cancel)
                    text, suffix = raw.decode(errors="replace"), f"exit status {code}" if code else ""
            except (RepoIdentityError, LedgerError, ValueError, OSError) as exc:
                code, text = 2, f"error: {exc}"
            running.pop(ident, None)
            if not cancel.is_set():
                send(ident, lambda body: {"result": {"content": [{"type": "text", "text": text + body + suffix}], "isError": bool(code)}}, held.read())

    for line in messages:
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):  # JSON-RPC's parse error (invalid UTF-8, a blank line, nesting too deep); the next request is answered
            send(None, {"error": {"code": -32700, "message": "parse error"}})
            continue
        method, ident, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method is None and (waiting := asked.pop(ident, None)):  # the host's answer to a question to the user
            waiting[1] = message.get("result")
            waiting[0].set()
        elif method == "tools/call":
            cancel = running[ident] = threading.Event()
            threading.Thread(target=call, args=(ident, params.get("arguments") or {}, cancel), daemon=True).start()
        elif method == "notifications/cancelled":
            running.get(params.get("requestId"), threading.Event()).set()
        elif method == "initialize":
            host.update(params.get("capabilities") or {})
            send(ident, {"result": {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                                   "serverInfo": {"name": "workflow", "version": "1"}}})
        elif method == "tools/list":
            send(ident, {"result": {"tools": [TOOL]}})
        elif ident is not None and method:
            send(ident, {"error": {"code": -32601, "message": f"unknown method: {method}"}})
    with finishing:  # don't start another frame at EOF; interrupt a stalled write, not a record following a completed frame
        os.dup2(0, 1)
        if (thread := writer) is None: return
        signal.signal(signal.SIGPIPE, lambda *_: None)
        signal.pthread_kill(thread.ident, signal.SIGPIPE)
    thread.join()


if __name__ == "__main__":
    raise SystemExit(lead(sys.argv[1:]) if sys.argv[1:] else serve())
