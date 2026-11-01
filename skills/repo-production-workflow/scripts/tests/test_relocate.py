"""Direct Linux/native-Codex probes; RELOCATION_ROLLOUT supplies a saved real session."""
import ast
import json
import os
from pathlib import Path
import pty
import re
import runpy
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
HOME_ESTATE = Path.home() / ".codex"
CLI = SCRIPTS / "codex-relocate"
LOOP = SCRIPTS / "codex-reloc-loop.bashrc"
PROTOCOL = runpy.run_path(str(CLI))


class Relocate(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="relocate-probe-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.seed("source")
        self.target = self.seed("target")
        fixture = Path(os.environ["RELOCATION_ROLLOUT"])
        with fixture.open() as saved:
            metadata = json.loads(saved.readline())["payload"]
        self.tid = metadata["id"]
        self.relative = Path("sessions", *metadata["timestamp"][:10].split("-"), fixture.name)
        self.rollout = self.source / self.relative
        self.rollout.parent.mkdir(parents=True)
        shutil.copy2(fixture, self.rollout)
        self.processes = []
        self.ready, self.tail = False, b""
        self.addCleanup(self.stop)

    def seed(self, name):
        estate = self.root / name
        estate.mkdir()
        for path in HOME_ESTATE.iterdir():
            if path.is_file() and path.suffix in (".json", ".toml"):
                shutil.copy2(path, estate / path.name)
        for name in ("skills", "hooks"):
            shutil.copytree(HOME_ESTATE / name, estate / name)
        for path in (CLI, LOOP):
            shutil.copy2(path, estate / "skills/repo-production-workflow/scripts" / path.name)
        (estate / "reloc").mkdir()
        with (estate / "config.toml").open("a") as config:
            config.write(f'\n[projects."{self.root}"]\ntrust_level = "trusted"\n')
        return estate

    def binding(self, estate=None):
        estate = estate or self.source
        return ["bwrap", "--dev-bind", "/", "/", "--bind", str(estate), str(HOME_ESTATE),
                "--setenv", "CODEX_HOME", str(HOME_ESTATE),
                "--setenv", "CODEX_ESTATE_PATH", str(estate),
                "--setenv", "CODEX_THREAD_ID", self.tid,
                "--unsetenv", "CODEX_WORKFLOW_STATE_ROOT", "--"]

    def call(self, args, env=None):
        return subprocess.run([*self.binding(), sys.executable, str(CLI), *args],
                              capture_output=True, text=True, env=env, timeout=15)

    def start(self, program):
        master, slave = pty.openpty()
        process = subprocess.Popen([*self.binding(), "bash", "-c", program],
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.processes.append((process, master))
        self.wait(lambda: (self.root / "controller").exists())
        controller = int((self.root / "controller").read_text())
        return process, controller

    def stop(self):
        for process, master in self.processes:
            if process.poll() is None:
                for pid, name in self.descendants(process.pid):
                    if name == "codex":
                        os.kill(pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            os.close(master)

    def wait(self, predicate, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            for process, master in self.processes:
                if process.poll() is None and select.select([master], [], [], 0.05)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        continue
                    self.tail = (self.tail + data)[-65536:]
                    if b"\x1b]7501;state=idle:app=codex\x1b\\" in self.tail:
                        self.ready = True
                    if b"\x1b[6n" in data:
                        os.write(master, b"\x1b[1;1R")
        self.fail("native process condition did not become true before deadline")

    def descendants(self, pid):
        result = []
        pending = [pid]
        while pending:
            parent = pending.pop()
            children = Path(f"/proc/{parent}/task/{parent}/children")
            if children.exists():
                for child in children.read_text().split():
                    entry = Path(f"/proc/{child}")
                    if entry.exists():
                        result.append((int(child), (entry / "comm").read_text().strip()))
                        pending.append(int(child))
        return result

    def native(self, controller, previous=0):
        return next((pid for pid, name in self.descendants(controller)
                     if name == "codex" and pid != previous
                     and b"exec-server" not in Path(f"/proc/{pid}/cmdline").read_bytes()), 0)

    def server(self, bound=False):
        socket = self.root / "server.sock"
        command = f'_codex_native app-server --listen unix://{socket}'
        if bound:
            command = (f'CODEX_RELOC_LOOP=1 CODEX_RELOC_ESTATE=1 CODEX_RELOC_SHELL_PID=$BASHPID '
                       f'bwrap --dev-bind / / -- codex --no-daemon app-server --listen unix://{socket}')
        program = (f'source "{LOOP}"; printf "%s" "$BASHPID" > "{self.root}/controller"; '
                   f'export CODEX_WORKFLOW_STATE_ROOT="{self.source}/workflow-state"; '
                   f'rc=0; {command} || rc=$?; '
                   '_codex_reloc_loop --no-daemon --dangerously-bypass-hook-trust --no-alt-screen; exit "$rc"')
        process, controller = self.start(program)
        self.wait(socket.exists)
        ws = PROTOCOL["init_ws"](str(socket))
        self.addCleanup(ws.close)
        return process, controller, ws

    def rpc(self, ws, method, params):
        return PROTOCOL["rpc"](ws, method, params, 50)

    def native_request(self, estate, method, params):
        socket = self.root / f"read-{estate.name}.sock"
        process = subprocess.Popen([*self.binding(estate), "codex", "app-server", "--listen", f"unix://{socket}"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait(socket.exists)
            ws = PROTOCOL["init_ws"](str(socket))
            try:
                response = self.rpc(ws, method, params)
                self.assertIn("result", response)
                return response["result"]
            finally:
                ws.close()
        finally:
            for pid, name in self.descendants(process.pid):
                if name == "codex":
                    os.kill(pid, signal.SIGTERM)
            process.wait(timeout=5)
            socket.unlink(missing_ok=True)

    def test_estate_handoff(self):
        process, controller, ws = self.server(bound=True)
        response = self.rpc(ws, "thread/resume", {"threadId": self.tid, "cwd": str(self.root)})
        self.assertIn("result", response)
        target_thread = self.rpc(ws, "thread/fork", {"threadId": self.tid, "cwd": str(self.root)})["result"]["thread"]["id"]
        saved = next((self.source / "sessions").glob(f"*/*/*/*{target_thread}.jsonl"))
        parent = self.tid
        for path in (self.rollout, saved):
            target_saved = self.target / path.relative_to(self.source)
            target_saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_saved)
        for path in self.source.glob("thread_history_*.sqlite"):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(self.target / path.name) as target:
                    source.backup(target)
        self.native_request(self.target, "thread/resume", {"threadId": target_thread, "cwd": str(self.root)})
        target_history = self.native_request(self.target, "thread/read", {"threadId": target_thread, "includeTurns": True})["thread"]["turns"]
        unrelated = self.rpc(ws, "thread/fork", {"threadId": self.tid, "cwd": str(self.root)})["result"]["thread"]["id"]
        self.assertTrue(list((self.source / "sessions").glob(f"*/*/*/*{unrelated}.jsonl")))
        intermediate = self.rpc(ws, "thread/fork", {"threadId": parent, "cwd": str(self.root)})["result"]["thread"]["id"]
        self.tid = self.rpc(ws, "thread/fork", {"threadId": intermediate, "cwd": str(self.root)})["result"]["thread"]["id"]
        self.assertIn("result", self.rpc(ws, "thread/archive", {"threadId": intermediate}))
        self.assertTrue(list((self.source / "archived_sessions").glob(f"*{intermediate}.jsonl")))
        self.rollout = next((self.source / "sessions").glob(f"*/*/*/*{self.tid}.jsonl"))
        self.relative = self.rollout.relative_to(self.source)
        with sqlite3.connect(self.source / "source-only.sqlite") as database:
            database.execute("CREATE TABLE preserved (value TEXT)")
        with sqlite3.connect(self.target / "target-only.sqlite") as database:
            database.execute("CREATE TABLE preserved (value TEXT)")
            database.execute("INSERT INTO preserved VALUES ('target')")
        old = self.native(controller)
        terminal = os.readlink(f"/proc/{old}/fd/0")
        arguments = [sys.executable, str(CLI), str(self.root), self.tid, "", "--estate", str(self.target)]
        command = [sys.executable, "-c", f'import os; os.environ.update(CODEX_THREAD_ID={self.tid!r}, '
                   'LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0"); '
                   f'os.execv({sys.executable!r}, {arguments!r})']
        response = self.rpc(ws, "command/exec", {"command": command, "cwd": str(self.root)})
        self.assertEqual(response["result"]["exitCode"], 0, response)
        self.assertIn(f"host TUI pid {old} ", response["result"]["stdout"])
        child = self.wait(lambda: not Path(f"/proc/{old}").exists() and self.native(controller, old))
        self.assertIsNone(process.poll())
        self.assertFalse(Path(f"/proc/{old}").exists())
        view = Path(f"/proc/{child}/root")
        self.assertTrue(os.path.samefile(view / str(HOME_ESTATE).lstrip("/"), self.target))
        self.assertTrue(os.path.samefile(view / str(HOME_ESTATE / "reloc").lstrip("/"), self.source / "reloc"))
        self.assertEqual(os.readlink(f"/proc/{child}/fd/0"), terminal)
        self.assertEqual(os.readlink(f"/proc/{child}/cwd"), str(self.root))
        arguments = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
        self.assertIn(b"--no-alt-screen", arguments)
        self.assertNotIn(b"--", arguments)
        environment = Path(f"/proc/{child}/environ").read_bytes().split(b"\0")
        self.assertIn(f"CODEX_RELOC_SHELL_PID={controller}".encode(), environment)
        self.assertFalse(any(entry.startswith(b"CODEX_WORKFLOW_STATE_ROOT=") for entry in environment))
        mounts = [line.split() for line in Path(f"/proc/{child}/mountinfo").read_text().splitlines()]
        self.assertTrue(any(row[4] == str(self.source) and "ro" in row[5].split(",") for row in mounts))
        self.assertEqual((self.target / self.relative).read_bytes(), self.rollout.read_bytes())
        self.wait(lambda: self.ready)
        expected, actual = [self.native_request(estate, "thread/read", {"threadId": self.tid, "includeTurns": True})
                            ["thread"]["turns"] for estate in (self.source, self.target)]
        self.assertTrue(expected == actual, f"native saved history differs: {len(expected)} versus {len(actual)} turns")
        unchanged = self.native_request(self.target, "thread/read", {"threadId": target_thread, "includeTurns": True})["thread"]["turns"]
        self.assertTrue(target_history == unchanged, "existing target thread history changed")
        self.assertFalse((self.target / "source-only.sqlite").exists())
        self.assertFalse(list((self.target / "sessions").glob(f"*/*/*/*{unrelated}.jsonl")))
        with sqlite3.connect(self.target / "target-only.sqlite") as database:
            self.assertEqual(database.execute("SELECT value FROM preserved").fetchone(), ("target",))
        with sqlite3.connect(next(self.target.glob("state_*.sqlite"))) as database:
            self.assertIsNone(database.execute("SELECT id FROM threads WHERE id = ?", (unrelated,)).fetchone())
            self.assertIsNotNone(database.execute("SELECT id FROM threads WHERE id = ?", (target_thread,)).fetchone())
        before = self.descendants(controller)
        second = self.seed("second")
        marker = self.source / "reloc" / str(controller)
        marker.write_text(f"{self.root} {self.tid} {int(time.time())} {second}\n\n")
        self.ready, self.tail = False, b""
        os.kill(child, signal.SIGTERM)
        following = self.wait(lambda: self.native(controller, child))
        self.wait(lambda: self.ready)
        self.assertFalse(Path(f"/proc/{child}").exists())
        self.assertTrue(os.path.samefile(Path(f"/proc/{following}/root") / str(HOME_ESTATE).lstrip("/"), second))
        self.assertEqual(sum(name == "bwrap" for _, name in before),
                         sum(name == "bwrap" for _, name in self.descendants(controller)))
        inode = (second / self.relative).stat().st_ino
        marker.write_text(f"{self.root} {self.tid} {int(time.time())}\n\n")
        self.ready, self.tail = False, b""
        os.kill(following, signal.SIGTERM)
        final = self.wait(lambda: self.native(controller, following))
        self.wait(lambda: self.ready)
        self.assertTrue(os.path.samefile(Path(f"/proc/{final}/root") / str(HOME_ESTATE).lstrip("/"), second))
        self.assertEqual((second / self.relative).stat().st_ino, inode)
        mounts = [line.split() for line in Path(f"/proc/{final}/mountinfo").read_text().splitlines()]
        for departure in (self.source, self.target):
            self.assertTrue(any(row[4] == str(departure) and "ro" in row[5].split(",") for row in mounts))

    def test_errexit_relocation(self):
        program = (f'source "{LOOP}"; printf "%s" "$BASHPID" > "{self.root}/controller"; set -e; '
                   f'codex --no-daemon --dangerously-bypass-hook-trust --no-alt-screen resume -C "{self.root}" {self.tid}')
        process, controller = self.start(program)
        child = self.wait(lambda: self.native(controller))
        for attempt in range(2):
            (self.source / "reloc" / str(controller)).write_text(f"{self.root} {self.tid} {int(time.time())}\n\n")
            os.kill(child, signal.SIGTERM)
            previous = child
            child = self.wait(lambda: self.native(controller, previous) or process.poll())
            self.assertIsNone(process.poll(), (attempt, process.returncode))
            self.assertFalse(Path(f"/proc/{previous}").exists())

    def test_estate_refusals(self):
        process, controller, ws = self.server()
        self.assertIn("result", self.rpc(ws, "thread/resume", {"threadId": self.tid, "cwd": str(self.root)}))
        binaries = self.root / "no-bwrap"
        binaries.mkdir()
        for name in ("bash", "sleep"):
            (binaries / name).symlink_to(shutil.which(name))
        blocked = self.root / "blocked-bwrap"
        shutil.copytree(binaries, blocked, symlinks=True)
        shutil.copy2(shutil.which("bwrap"), blocked / "bwrap")
        (blocked / "bwrap").chmod(0o600)
        marker = self.source / "reloc" / str(controller)

        def refuse(target, env=None):
            command = [sys.executable, "-c", "import os,subprocess,sys; os.environ.update(" + repr(env or {}) +
                       "); sys.exit(subprocess.call(" + repr([sys.executable, str(CLI), str(self.root),
                       self.tid, "", "--estate", str(target)]) + "))"]
            try:
                response = self.rpc(ws, "command/exec", {"command": command, "cwd": str(self.root)})["result"]
                self.assertNotEqual(response["exitCode"], 0, response)
                self.assertFalse(marker.exists())
                self.assertIsNone(process.poll())
            finally:
                marker.unlink(missing_ok=True)

        cases = [({"CODEX_RELOC_ESTATE": ""}, str(self.target)),
                 ({"CODEX_RELOC_SHELL_PID": "1"}, str(self.target)),
                 ({"CODEX_APP_SERVER_SOCK": str(self.root / "server.sock")}, str(self.target)),
                 ({}, "relative"), ({}, ""), ({}, str(self.root / "missing")),
                 ({"PATH": str(binaries)}, str(self.target)), ({"PATH": str(blocked)}, str(self.target))]
        for env, target in cases:
            with self.subTest(env=env, target=target):
                refuse(target, env)
        entrypoint = self.target / "skills/repo-production-workflow/scripts/codex-relocate"
        saved_entrypoint = entrypoint.read_bytes()
        entrypoint.unlink()
        try:
            with self.subTest(target="missing installed entrypoint"):
                refuse(self.target)
        finally:
            entrypoint.write_bytes(saved_entrypoint)
        broken = self.seed("broken")
        (broken / "config.toml").write_text("broken = [")
        with self.subTest(target="invalid TOML"):
            refuse(broken)
            self.assertEqual((broken / "config.toml").read_text(), "broken = [")
        blocked_parent = self.seed("blocked-parent")
        (blocked_parent / "sessions").write_text("existing target data")
        with self.subTest(target="history parent is a file"):
            refuse(blocked_parent)
            self.assertEqual((blocked_parent / "sessions").read_text(), "existing target data")
        physical = self.seed("target with spaces")
        linked = self.root / "clean-alias"
        linked.symlink_to(physical, target_is_directory=True)
        with self.subTest(target="resolved whitespace"):
            refuse(linked)
        dangling = self.seed("dangling-parent")
        (dangling / "sessions").symlink_to(dangling / "missing", target_is_directory=True)
        with self.subTest(target="dangling history parent"):
            refuse(dangling)
        unwritable = self.seed("unwritable-parent")
        (unwritable / "sessions").mkdir()
        (unwritable / "sessions").chmod(0o500)
        try:
            with self.subTest(target="unwritable history parent"):
                refuse(unwritable)
        finally:
            (unwritable / "sessions").chmod(0o700)
        renamed = self.rollout.with_suffix(".saved")
        self.rollout.rename(renamed)
        try:
            with self.subTest(source="missing rollout"):
                refuse(self.target)
        finally:
            renamed.rename(self.rollout)
        duplicate = self.rollout.parent / ("rollout-2026-11-01T00-00-00-" + self.tid + ".jsonl")
        shutil.copy2(self.rollout, duplicate)
        try:
            with self.subTest(source="ambiguous rollout"):
                refuse(self.target)
        finally:
            duplicate.unlink()
        collision = self.target / self.relative
        collision.parent.mkdir(parents=True)
        shutil.copy2(self.rollout, collision)
        with self.subTest(target="existing thread"):
            refuse(self.target)
            self.assertEqual(collision.read_bytes(), self.rollout.read_bytes())
        collision.unlink()
        for path in self.source.glob("thread_history_*.sqlite"):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(self.target / path.name) as target:
                    source.backup(target)
        with self.subTest(target="cached thread without rollout"):
            refuse(self.target)
        parent = self.tid
        self.tid = self.rpc(ws, "thread/fork", {"threadId": parent, "cwd": str(self.root)})["result"]["thread"]["id"]
        path = next((self.source / "sessions").glob(f"*/*/*/*{self.tid}.jsonl"))
        original = path.read_bytes()
        header, tail = original.split(b"\n", 1)
        metadata = json.loads(header)
        base = metadata["payload"]["history_base"].copy()
        for field, value, expected in [("end_byte_offset", -1, "boundary"),
                                       ("end_byte_offset", True, "boundary"),
                                       ("end_ordinal_exclusive", -1, "lineage"),
                                       ("thread_id", self.tid, "cyclic")]:
            with self.subTest(lineage_field=field, value=value):
                metadata["payload"]["history_base"] = base | {field: value}
                path.write_bytes(json.dumps(metadata, ensure_ascii=False).encode() + b"\n" + tail)
                command = [sys.executable, "-c", f'import os; os.environ["CODEX_THREAD_ID"]={self.tid!r}; '
                           f'os.execv({sys.executable!r}, {[sys.executable, str(CLI), str(self.root), self.tid, "", "--estate", str(self.target)]!r})']
                try:
                    response = self.rpc(ws, "command/exec", {"command": command, "cwd": str(self.root)})["result"]
                    self.assertNotEqual(response["exitCode"], 0, response)
                    self.assertIn(expected, response["stderr"])
                    self.assertFalse(marker.exists())
                finally:
                    marker.unlink(missing_ok=True)
                    path.write_bytes(original)

    def test_existing_validation(self):
        environment = os.environ | {"CODEX_RELOC_LOOP": "0", "CODEX_THREAD_ID": self.tid}
        environment.pop("CODEX_APP_SERVER_SOCK", None)
        spaced = self.root / "with space"
        spaced.mkdir()
        cases = [([], "Usage:"), (["relative"], "must be absolute"),
                 ([str(self.root / "missing")], "not a directory"), ([str(spaced)], "whitespace"),
                 ([str(self.root), "bad thread"], "thread id must"),
                 ([str(self.root), "other-thread"], "only its calling thread"),
                 ([str(self.root)], "no native resume loop")]
        for args, expected in cases:
            with self.subTest(args=args):
                result = self.call(args, environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout + result.stderr)

    def marker(self, epoch, note="", removable=True, flags=("--help",)):
        trace = self.root / "exec.log"
        trace.unlink(missing_ok=True)
        program = (f'source "{LOOP}"; m="$HOME/.codex/reloc/$BASHPID"; '
                   'printf "%s %s %s\\n%s\\n" "$1" "$2" "$3" "$4" > "$m"; '
                   + ('chmod 500 "${m%/*}"; ' if not removable else '') +
                   '_codex_reloc_loop "${@:5}"; ')
        try:
            result = subprocess.run([*self.binding(), "strace", "-f", "-e", "trace=execve,write", "-s", "10000",
                                     "-o", str(trace), "bash", "-c", program, "probe", str(self.root), self.tid,
                                     str(epoch), note, *flags], capture_output=True, text=True, timeout=10)
        finally:
            (self.source / "reloc").chmod(0o700)
        commands = []
        for line in trace.read_text().splitlines():
            match = re.search(r'execve\("[^\"]*/codex", (\[.*?\]),', line)
            if match:
                commands.append([value.encode("latin1").decode("utf8") for value in ast.literal_eval(match[1])])
        return result, commands

    def test_existing_markers(self):
        for epoch, runs in [("", 0), ("99999999999999999999", 0),
                            (int(time.time()) - 901, 0), (int(time.time()), 1),
                            ("0" + str(int(time.time())), 1), (int(time.time()) + 100, 1)]:
            with self.subTest(epoch=epoch):
                result, _ = self.marker(epoch)
                self.assertEqual(result.stdout.count("Codex CLI"), runs)
        result, _ = self.marker(int(time.time()), removable=False)
        self.assertEqual(result.stdout.count("Codex CLI"), 1)
        for attempt in range(3):
            epoch = int(time.time()) - 900
            result, _ = self.marker(epoch)
            observed = re.findall(r'write\(1, "([0-9]+)\\n", [0-9]+\)', (self.root / "exec.log").read_text())
            self.assertTrue(observed, "missing actual date output in syscall trace")
            if int(observed[0]) - epoch == 900:
                self.assertEqual(result.stdout.count("Codex CLI"), 1)
                break
        else:
            self.fail("could not execute the real inclusive expiry boundary")
        for args, expected in [(["--version"], 0), (["--no-daemon", "--invalid-relocation-probe"], 2)]:
            with self.subTest(no_marker=args):
                result = subprocess.run([*self.binding(), "bash", "-c", f'source "{LOOP}"; codex "$@"',
                                         "probe", *args], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected)

    def test_existing_notes(self):
        for note in ("", "plain", "--estate", "--literal café\nsecond line"):
            with self.subTest(note=note):
                _, commands = self.marker(int(time.time()), note)
                self.assertEqual(commands[-1][-2:] if note else commands[-1][-1:],
                                 ["--", note] if note else [self.tid])
        _, commands = self.marker(int(time.time()), "note", flags=("--help", "-m", "gpt-6.1-sol", "-p", "codexs"))
        self.assertIn("gpt-6.1-sol", commands[-1])
        self.assertIn("codexs", commands[-1])
        _, controller, ws = self.server()
        marker = self.source / "reloc" / str(controller)
        for note in ("  café\nsecond line  ", "", "--estate"):
            with self.subTest(producer_note=note):
                result = self.rpc(ws, "command/exec", {"command": [sys.executable, str(CLI), str(self.root),
                                  self.tid, note], "cwd": str(self.root)})["result"]
                self.assertEqual(result["exitCode"], 0, result)
                self.assertEqual(marker.read_text().split("\n", 1)[1], note.strip() + "\n")
                marker.unlink()

    def test_trust_preservation(self):
        config = self.source / "config.toml"
        header = f'[projects."{self.root}"]\n'
        for original, expected in [(header + 'trust_level = "trusted"\n', 'trust_level = "trusted"'),
                                   (header + 'trust_level = "untrusted"\n', 'trust_level = "trusted"'),
                                   ("broken = [", "broken = [")]:
            with self.subTest(original=original):
                config.write_text(original)
                subprocess.run([*self.binding(), sys.executable, "-c",
                                f'import runpy; runpy.run_path({str(CLI)!r})["_trust_dir"]({str(self.root)!r})'],
                               check=True, capture_output=True)
                self.assertIn(expected, config.read_text())

    def test_remote_routing(self):
        (self.source / "config.toml").write_text('model = "gpt-6.1-sol"\n')
        (self.source / "auth.json").unlink(missing_ok=True)
        _, _, ws = self.server()
        self.assertIn("result", self.rpc(ws, "thread/resume", {"threadId": self.tid, "cwd": str(self.root)}))
        env = os.environ | {"CODEX_APP_SERVER_SOCK": str(self.root / "server.sock"), "CODEX_THREAD_ID": self.tid}
        result = self.call([str(self.root), self.tid, ""], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("relocation requested", result.stdout)
        self.assertIn("pending", result.stdout)


if __name__ == "__main__":
    unittest.main()
