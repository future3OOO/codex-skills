"""Real-seam attacks for the self-hosted relocation path.

Seams under test:
- marker file -> _codex_reloc_loop -> `codex resume` argv (codex stubbed via
  PATH so the outgoing process argv is captured, not mocked away);
- codex-relocate's _trust_dir -> ~/.codex/config.toml [projects.*];
- codex-relocate's socket path -> a real websocket handshake + JSON-RPC server.
"""
import importlib.machinery, importlib.util, json, os, re, socket, struct, subprocess
import tempfile, threading, unittest, base64, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.environ.get("RELOC_LOOP_FILE", os.path.join(HERE, "..", "scripts", "codex-reloc-loop.bashrc"))
RELOC = os.environ.get("RELOC_SCRIPT_FILE", os.path.join(HERE, "..", "scripts", "codex-relocate"))

# Behavior-map redFailure markers — the failing assertion must name the
# demonstrated product failure so the RED record binds to it.
NOTE_LOST = "resumed pane lands at composer with no continuation turn — agent never continues (demonstrated)"
TRUST_BLOCK = "resume blocks on interactive trust prompt; pane waits dead for input"
INTERRUPT_BANNER = "resumed pane shows 'Conversation interrupted' banner"
EPOCH_CRASH = "loop exits without resuming the thread, or crashes mid-loop leaving the pane dead"
TRUST_CORRUPT = "config.toml corrupted or trust silently missing — codex launches break or the prompt blocks the resume"
TRUST_ROBUST = "relocation aborts on an unusual but valid config, or silently destroys config content"
STUCK_RESUME = "stuck marker re-resumes the same thread forever — pane storms duplicate sessions"

# The parser carried by panes that sourced the loop before this change.
# Kept verbatim so the compat contract is tested against the actual old code,
# not a paraphrase.
OLD_LOOP = '''_codex_reloc_loop() {
  local m="$HOME/.codex/reloc/$$" wt tid epoch
  while [ -f "$m" ]; do
    { read -r wt tid epoch < "$m" && rm -f "$m"; } || break
    [ -n "$wt" ] && [ -n "$tid" ] || break
    [ -z "${epoch:-}" ] && continue
    [ $(( $(date +%s) - epoch )) -gt 900 ] && continue
    CODEX_RELOC_LOOP=1 command codex "$@" resume -C "$wt" "$tid"
  done
}'''


def _loop_snippet(path=LOOP):
    with open(path) as _f:
        m = re.search(r"^_codex_reloc_loop\(\) \{.*?^\}", _f.read(), re.S | re.M)
    assert m, "loop function not found"
    return m.group(0)


def _run_loop(marker_text, snippet=None):
    """Run _codex_reloc_loop against marker_text; return (stdout, stderr)."""
    with tempfile.TemporaryDirectory() as td:
        fakebin = os.path.join(td, "bin")
        os.makedirs(os.path.join(fakebin)); os.makedirs(os.path.join(td, ".codex", "reloc"))
        stub = os.path.join(fakebin, "codex")
        with open(stub, "w") as _f:
            _f.write('#!/usr/bin/env bash\nprintf "RESUME_ARGV:%s\\n" "$*"\n')
        os.chmod(stub, 0o755)
        body = (
            'export PATH="$1/bin:$PATH"; export HOME="$1"; shift\n'
            'mkdir -p "$HOME/.codex/reloc"\n'
            'source /dev/stdin\n'
            'printf "%s" "$MARKER" > "$HOME/.codex/reloc/$$"\n'
            '_codex_reloc_loop --profile codexs\n'
        )
        env = dict(os.environ); env["MARKER"] = marker_text
        r = subprocess.run(["bash", "-c", body, "_", td],
                           input=snippet or _loop_snippet(), capture_output=True,
                           text=True, env=env)
        return r.stdout, r.stderr


def _reloc_module():
    loader = importlib.machinery.SourceFileLoader("reloc", RELOC)
    spec = importlib.util.spec_from_loader("reloc", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class LoopResume(unittest.TestCase):
    def test_note_delivered_as_prompt(self):
        out, err = _run_loop("/tmp/wtx T1 %d\ncontinue from /tmp/wtx and finish\n" % __import__("time").time().__int__())
        self.assertIn("-- continue from /tmp/wtx and finish", out, f"{NOTE_LOST}\n{out}{err}")

    def test_flag_shaped_note_not_parsed_as_option(self):
        out, err = _run_loop("/tmp/wtx T2 %d\n--help\n" % int(__import__("time").time()))
        self.assertIn("-- --help", out, out + err)

    def test_marker_without_note_still_resumes(self):
        out, err = _run_loop("/tmp/wtx T3 %d\n" % int(__import__("time").time()))
        self.assertIn("resume -C /tmp/wtx T3", out, out + err)
        self.assertNotIn(" -- ", out, out)

    def test_garbage_epoch_never_resumes(self):
        out, err = _run_loop("/tmp/wtx T4 garbage\n")
        self.assertNotIn("RESUME_ARGV", out, out + err)

    def test_leading_zero_epoch_resumes(self):
        out, err = _run_loop("/tmp/wtx T5 0%d\n" % int(__import__("time").time()))
        self.assertIn("resume -C /tmp/wtx T5", out, f"{EPOCH_CRASH}\n{out}{err}")
        self.assertNotIn("too great for base", err, err)

    def test_stale_epoch_skipped(self):
        out, err = _run_loop("/tmp/wtx T6 1000000\n")
        self.assertNotIn("RESUME_ARGV", out, out + err)

    def test_stale_loop_baseline(self):
        """Old loop + original single-line marker — the pre-change contract."""
        out, err = _run_loop("/tmp/wtx T0 %d\n" % int(__import__("time").time()),
                             snippet=OLD_LOOP)
        self.assertIn("resume -C /tmp/wtx T0", out, out + err)

    def test_stale_loop_survives_new_marker(self):
        """Old 3-var in-memory loop + new line-2 note marker: resumes, no crash."""
        out, err = _run_loop("/tmp/wtx T7 %d\ncontinue from /tmp/wtx\n" % int(__import__("time").time()),
                             snippet=OLD_LOOP)
        self.assertIn("resume -C /tmp/wtx T7", out, out + err)
        self.assertNotIn("syntax error", err)

    def test_stuck_marker_resumes_once(self):
        """rm-denied marker: resume once, then stop — never storm
        re-resumes of the same thread (advisor-demonstrated: 1376
        duplicate `codex resume` launches in 4s before the fix)."""
        with tempfile.TemporaryDirectory() as td:
            fakebin = os.path.join(td, "bin")
            os.makedirs(fakebin); os.makedirs(os.path.join(td, ".codex", "reloc"))
            stub = os.path.join(fakebin, "codex")
            with open(stub, "w") as _f:
                _f.write('#!/usr/bin/env bash\nprintf "RESUME_ARGV:%s\\n" "$*"\n')
            os.chmod(stub, 0o755)
            body = (
                'export PATH="$1/bin:$PATH"; export HOME="$1"; shift\n'
                'source /dev/stdin\n'
                'printf "%s" "$MARKER" > "$HOME/.codex/reloc/$$"\n'
                'chmod 555 "$HOME/.codex/reloc"\n'
                '_codex_reloc_loop --profile codexs\n'
            )
            env = dict(os.environ)
            env["MARKER"] = "/tmp/wtx T1 %d\nnote\n" % int(__import__("time").time())
            r = subprocess.run(["timeout", "4", "bash", "-c", body, "_", td],
                               input=_loop_snippet(), capture_output=True,
                               text=True, env=env)
            n = r.stdout.count("RESUME_ARGV")
            self.assertEqual(1, n, f"{STUCK_RESUME}\n{n} resume invocations")
            self.assertEqual(0, r.returncode,
                             f"{STUCK_RESUME}\nloop did not exit (rc={r.returncode})")

    def test_multiline_note_single_argv(self):
        """Lines 2+ of the marker are one continuation note: they must reach
        `codex resume` as a single PROMPT argv after `--`, not split args."""
        with tempfile.TemporaryDirectory() as td:
            fakebin = os.path.join(td, "bin")
            os.makedirs(fakebin); os.makedirs(os.path.join(td, ".codex", "reloc"))
            stub = os.path.join(fakebin, "codex")
            with open(stub, "w") as _f:
                _f.write('#!/usr/bin/env bash\n'
                         'for a in "$@"; do printf "ARGV_BEGIN%sARGV_END\\n" "$a"; done\n')
            os.chmod(stub, 0o755)
            body = (
                'export PATH="$1/bin:$PATH"; export HOME="$1"; shift\n'
                'source /dev/stdin\n'
                'printf "%s" "$MARKER" > "$HOME/.codex/reloc/$$"\n'
                '_codex_reloc_loop --profile codexs\n'
            )
            env = dict(os.environ)
            env["MARKER"] = "/tmp/wtx T1 %d\nline one\nline two\n" % int(__import__("time").time())
            r = subprocess.run(["bash", "-c", body, "_", td],
                               input=_loop_snippet(), capture_output=True,
                               text=True, env=env)
            self.assertIn("ARGV_BEGINline one\nline twoARGV_END", r.stdout,
                          f"{NOTE_LOST}\n{r.stdout!r}")


class TrustDir(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = self.td.name
        os.makedirs(os.path.join(self.td.name, ".codex"))
        self.cfg = os.path.join(self.td.name, ".codex", "config.toml")
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/a"]\ntrust_level = "trusted"\n')

    def tearDown(self):
        if self._home is not None:
            os.environ["HOME"] = self._home

    def _read(self):
        with open(self.cfg) as _f:
            return _f.read()

    def test_append_and_idempotent(self):
        m = _reloc_module()
        trust = getattr(m, "_trust_dir", None)
        self.assertIsNotNone(trust, TRUST_BLOCK)
        trust("/x/y_z"); trust("/x/y_z")
        s = self._read()
        self.assertEqual(s.count('[projects."/x/y_z"]'), 1, s)
        self.assertIn('trust_level = "trusted"', s)

    def test_existing_untrusted_section_is_flipped(self):
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/x/y_z"]\ntrust_level = "untrusted"\n')
        _reloc_module()._trust_dir("/x/y_z")
        s = self._read()
        self.assertEqual(s.count('[projects."/x/y_z"]'), 1, s)
        self.assertNotIn("untrusted", s)

    def test_section_without_key_gets_key_inserted(self):
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/q"]\nother_key = 1\n[projects."/r"]\ntrust_level = "untrusted"\n')
        _reloc_module()._trust_dir("/q")
        s = self._read()
        qsec = s.split('[projects."/q"]')[1].split("[projects")[0]
        self.assertIn('trust_level = "trusted"', qsec, s)
        self.assertIn('untrusted', s.split('[projects."/r"]')[1])  # sibling untouched

    def test_result_is_valid_toml(self):
        import tomllib
        _reloc_module()._trust_dir("/new")
        tomllib.loads(self._read())

    def test_eof_section_without_trailing_newline(self):
        """Matched section ending the file unterminated must not glue the key."""
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/x"]\nother_key = 1')
        trust = getattr(_reloc_module(), "_trust_dir", None)
        self.assertIsNotNone(trust, TRUST_CORRUPT)
        trust("/x")
        import tomllib
        parsed = tomllib.loads(self._read())
        self.assertEqual(parsed["projects"]["/x"]["trust_level"], "trusted", TRUST_CORRUPT)
        self.assertEqual(parsed["projects"]["/x"]["other_key"], 1)

    def test_noncanonical_spelling_fails_closed(self):
        """A differently-spelled existing table: warn and leave the file valid,
        never append a duplicate table."""
        with open(self.cfg, "w") as _f:
            _f.write('[ projects."/x" ]\ntrust_level = "untrusted"\n')
        before = self._read()
        _reloc_module()._trust_dir("/x")
        after = self._read()
        import tomllib
        tomllib.loads(after)  # must still parse — duplicate table is the bug
        self.assertEqual(before, after, TRUST_CORRUPT)

    def test_backslash_path_refused(self):
        """A path needing TOML escaping is refused, never written as an
        invalid basic-string escape."""
        before = self._read()
        _reloc_module()._trust_dir("/tmp/a\\/b")
        import tomllib
        tomllib.loads(self._read())
        self.assertEqual(before, self._read(), TRUST_CORRUPT)

    def test_commented_untrusted_is_flipped(self):
        """A comment containing 'trusted' must not suppress the flip."""
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/x"]\ntrust_level = "untrusted" # was "trusted"?\n')
        _reloc_module()._trust_dir("/x")
        import tomllib
        parsed = tomllib.loads(self._read())
        self.assertEqual(parsed["projects"]["/x"]["trust_level"], "trusted", TRUST_CORRUPT)

    def test_unparseable_config_untouched(self):
        before = self._read()
        with open(self.cfg, "w") as _f:
            _f.write(before + 'not = [valid\n')
        _reloc_module()._trust_dir("/x")
        self.assertEqual(before + 'not = [valid\n', self._read(), TRUST_CORRUPT)

    def test_odd_projects_shapes_fail_closed(self):
        """Non-table `projects` shapes are legal TOML: warn + leave the file
        byte-identical — never crash the relocation or guess."""
        for body in ('projects = []\n', 'projects = "x"\n',
                     '[[projects]]\nname = "t"\n', '[projects]\n"/x" = 5\n'):
            with open(self.cfg, "w") as _f:
                _f.write(body)
            try:
                _reloc_module()._trust_dir("/x")
            except Exception as e:
                self.fail(f"{TRUST_ROBUST}\n{body!r} -> {e!r}")
            self.assertEqual(body, self._read(), TRUST_CORRUPT)

    def test_unreadable_config_untouched(self):
        """A read failure is not an empty file: an unreadable config must be
        left alone — never wiped to just the new table."""
        with open(self.cfg, "w") as _f:
            _f.write('[projects."/a"]\ntrust_level = "trusted"\nother = 1\n')
        os.chmod(self.cfg, 0o200)
        try:
            _reloc_module()._trust_dir("/x")
        finally:
            os.chmod(self.cfg, 0o644)
        self.assertEqual('[projects."/a"]\ntrust_level = "trusted"\nother = 1\n',
                         self._read(), TRUST_ROBUST)

    def test_non_utf8_config_warns(self):
        """A non-UTF-8 config is legal on disk and tolerated by codex; the
        decode failure must warn + skip, never abort the relocation."""
        with open(self.cfg, "wb") as _f:
            _f.write(b'\xff\xfe[\x00p\x00]\x00')
        try:
            _reloc_module()._trust_dir("/x")
        except Exception as e:
            self.fail(f"{TRUST_ROBUST}\nnon-UTF-8 config -> {e!r}")
        with open(self.cfg, "rb") as _f:
            self.assertEqual(b'\xff\xfe[\x00p\x00]\x00', _f.read(), TRUST_CORRUPT)

    def test_deeply_nested_config_warns(self):
        """tomllib raises RecursionError — neither TOMLDecodeError nor
        OSError — on deeply nested input. Any unexpected exception class
        must degrade to warn + skip, never abort the relocation."""
        with open(self.cfg, "w") as _f:
            _f.write('a = ' + '[' * 1500 + ']' * 1500 + '\n')
        try:
            _reloc_module()._trust_dir("/x")
        except Exception as e:
            self.fail(f"{TRUST_ROBUST}\ndeeply nested config -> {e!r}")
        self.assertEqual('a = ' + '[' * 1500 + ']' * 1500 + '\n',
                         self._read(), TRUST_CORRUPT)



class _FakeAppServer(threading.Thread):
    """Minimal websocket+JSON-RPC server asserting turn/start receives cwd+note."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_UNIX)
        self.path = os.path.join(tempfile.mkdtemp(), "a.sock")
        self.sock.bind(self.path); self.sock.listen(1)
        self.seen = {}

    @staticmethod
    def _frame(s, payload, masked=False):
        data = payload.encode()
        hdr = struct.pack("!BB", 0x81, (0x80 if masked else 0) | (126 if len(data) >= 126 else len(data)))
        if len(data) >= 126:
            hdr = struct.pack("!BBH", 0x81, (0x80 if masked else 0) | 126, len(data))
        if masked:
            mask = os.urandom(4)
            return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return hdr + data

    @staticmethod
    def _read_frame(c):
        hdr = c.recv(2)
        if len(hdr) < 2:
            return None
        n = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", c.recv(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", c.recv(8))[0]
        mask = c.recv(4) if hdr[1] & 0x80 else b""
        data = b""
        while len(data) < n:
            chunk = c.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return data.decode()

    def run(self):
        c, _ = self.sock.accept()
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += c.recv(4096)
            key = re.search(rb"Sec-WebSocket-Key: (\S+)", buf).group(1).decode()
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
            while True:
                msg = self._read_frame(c)
                if msg is None:
                    return
                try:
                    req = json.loads(msg)
                except ValueError:
                    continue
                mid = req.get("id"); method = req.get("method")
                if method == "initialize":
                    c.sendall(self._frame(c, json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}})))
                elif method == "thread/loaded/list":
                    c.sendall(self._frame(c, json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"data": ["tid-1"]}})))
                elif method == "turn/start":
                    self.seen = req.get("params", {})
                    c.sendall(self._frame(c, json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}})))
                    return
        finally:
            c.close()
            self.sock.close()


class DeferredKill(unittest.TestCase):
    def test_host_kill_is_deferred_detached(self):
        """The instant os.kill(SIGTERM) mid-turn leaves an interrupted banner.
        The deferral is measured on the real process chain: the host stub
        receives SIGTERM well after the relocating process exits."""
        with tempfile.TemporaryDirectory() as td:
            wt = os.path.join(td, "wtA"); home = os.path.join(td, "home")
            os.makedirs(wt); os.makedirs(os.path.join(home, ".codex", "reloc"))
            env = dict(os.environ, HOME=home)
            r = subprocess.run(
                # A persistent bash wrapper is the designed "interactive shell"
                # ancestor: a bare `bash -c 'python3 ...'` execs away, so the
                # trailing `exit $?` keeps bash alive as the marker's shell pid.
                ["bash", "-c", 'python3 "$@"; exit $? ', "_",
                 os.path.join(HERE, "reloc-stubchain.py"), RELOC, wt, "tid-1", "flag"],
                capture_output=True, text=True, timeout=40, env=env)
            reports = [l for l in r.stdout.splitlines() if l.startswith("{")]
            self.assertTrue(reports, r.stdout + r.stderr)
            rep = json.loads(reports[-1])
            self.assertTrue(rep["sigterm_delivered_to_host"], INTERRUPT_BANNER)
            self.assertIsNotNone(rep["sigterm_delay"], INTERRUPT_BANNER)
            self.assertGreaterEqual(rep["sigterm_delay"], 5, INTERRUPT_BANNER)


class SocketPathPreserved(unittest.TestCase):
    def test_socket_path_still_uses_turn_start_with_note(self):
        srv = _FakeAppServer(); srv.start()
        m = _reloc_module()
        import sys
        argv = sys.argv
        env_sock = os.environ.get("CODEX_APP_SERVER_SOCK")
        os.environ["CODEX_APP_SERVER_SOCK"] = srv.path
        try:
            sys.argv = ["codex-relocate", tempfile.gettempdir(), "tid-1", "note-via-socket"]
            m.main()
        finally:
            sys.argv = argv
            if env_sock is None:
                os.environ.pop("CODEX_APP_SERVER_SOCK", None)
            else:
                os.environ["CODEX_APP_SERVER_SOCK"] = env_sock
        srv.join(5)
        self.assertEqual(srv.seen.get("cwd"), tempfile.gettempdir(), srv.seen)
        self.assertEqual(srv.seen.get("threadId"), "tid-1", srv.seen)
        inputs = srv.seen.get("input") or []
        self.assertEqual(inputs[0].get("text"), "note-via-socket", srv.seen)


if __name__ == "__main__":
    unittest.main()
