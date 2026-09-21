#!/usr/bin/env python3
"""Real-seam stub chain for codex-relocate's self-hosted path.

Runs the script under an ancestor chain of real processes:
    outer bash (the "interactive shell") -> this process renamed to
    comm="codex" (the "host TUI") -> the script.

The host stub catches SIGTERM instead of dying so the probe can assert the
kill was delivered. Prints one JSON result line.

Usage: reloc-stubchain.py <script> <worktree> <tid> [flag]
  flag = set CODEX_RELOC_LOOP (loop armed); omitted = loop absent.
"""
import ctypes, os, sys, json, signal, time

script, wt, tid = sys.argv[1], sys.argv[2], sys.argv[3]
want_flag = len(sys.argv) > 4 and sys.argv[4] == "flag"
shell_pid = os.getppid()
marker = os.path.expanduser(f"~/.codex/reloc/{shell_pid}")
try: os.remove(marker)
except FileNotFoundError: pass

env = dict(os.environ, CODEX_THREAD_ID=tid)
env.pop("CODEX_RELOC_LOOP", None); env.pop("CODEXS_RELOC_LOOP", None)
if want_flag: env["CODEX_RELOC_LOOP"] = "1"

got_sigterm = []
signal.signal(signal.SIGTERM, lambda *a: got_sigterm.append(1))

ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0)
pid = os.fork()
if pid == 0:
    os.execve(sys.executable, [sys.executable, script, wt, tid], env)
    os._exit(1)

deadline = time.time() + 20
status = None
while time.time() < deadline:
    w, s = os.waitpid(pid, os.WNOHANG)
    if w: status = s; break
    time.sleep(0.2)
print(json.dumps({
    "child_exited": status is not None,
    "sigterm_delivered_to_host": bool(got_sigterm),
    "exit_code": os.WEXITSTATUS(status) if status is not None and os.WIFEXITED(status) else None,
    "marker_exists": os.path.exists(marker),
    "marker_content": open(marker).read().strip() if os.path.exists(marker) else None,
    "marker_path": marker}))
if status is None:
    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
try: os.remove(marker)
except OSError: pass
