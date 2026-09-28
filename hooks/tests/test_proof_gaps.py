#!/usr/bin/env python3
"""Advisory proof-gap check at GREEN, driven through the real `workflow.py tdd` CLI."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import site
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.tests.support import pending_behavior  # noqa: E402
from hooks.tests import test_tdd_repairs as tdd_repairs  # noqa: E402

PYTEST = importlib.util.find_spec("pytest") is not None
BRANCHY = "def scale(x):\n    if x > 0:\n        return x * 2\n    return 0\n"
WEAK = "[calc.scale(3)] is not None"  # runs scale, checks nothing it returns
FAILS = "calc.scale(3) == 6"  # the RED check: the base stub returns None
STRONG = "calc.scale(3) == 6"


class ProofGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = tdd_repairs.MappedTddRepairTests(methodName="runTest")
        self.h.setUp()
        home = self.h.tmp / "home"
        home.mkdir()
        # No ambient TypeSafe key: the suite never reaches the network.
        self.h.env.pop("TYPESAFE_API_KEY", None)
        self.h.env.update(HOME=str(home), PYTHONPATH=os.pathsep.join(
            p for p in (site.getusersitepackages(), self.h.env.get("PYTHONPATH")) if p))
        (self.h.repo / "calc.py").write_text("def scale(x):\n    return None\n")
        self.h.git("add", "calc.py")
        self.h.git("commit", "-q", "-m", "calc")
        self.calc = self.h.repo / "calc.py"

    def tearDown(self) -> None:
        self.h.tearDown()

    def item(self, identifier: str, marker: str) -> dict[str, object]:
        return pending_behavior(identifier, behavior="scale doubles a positive input",
                                expected="calc.scale(3) returns 6", red_failure=marker)

    def proof(self, name: str, check: str, marker: str, runner: str = "unittest") -> tuple[str, ...]:
        if runner == "child":  # the code runs only in a child process, so only the child's observer can see it
            check = f"subprocess.run([sys.executable, '-c', {f'import calc; assert {check}'!r}]).returncode == 0"
        if runner == "script":  # a plain script, not a test file
            (self.h.repo / f"{name}.py").write_text(f"import calc\nassert {check}, {marker!r}\n")
            return (sys.executable, f"{name}.py")
        (self.h.repo / f"{name}.py").write_text(
            "import subprocess, sys, unittest\nimport calc\n"
            f"class T(unittest.TestCase):\n    def test_it(self):\n"
            f"        self.assertTrue({check}, {marker!r})\n")
        if runner == "pytest":
            return (sys.executable, "-m", "pytest", "-q", f"{name}.py::T::test_it")
        if runner == "unittest-E":
            return (sys.executable, "-E", "-B", "-m", "unittest", f"{name}.T.test_it")
        return (sys.executable, "-m", "unittest", f"{name}.T.test_it")

    def cycle(self, slug: str, identifier: str, command: tuple[str, ...], code: str = BRANCHY,
              weaken: tuple[str, str] | None = None) -> tuple[object, dict]:
        """RED on the written proof, then land `code` and, when given, rewrite the proof's check to a weaker one."""
        red = self.h.tdd(slug, "red", identifier, command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        self.calc.write_text(code)
        if weaken:
            path = self.h.repo / f"{weaken[0]}.py"
            path.write_text(path.read_text().replace(FAILS, weaken[1]))
        green = self.h.tdd(slug, "green", identifier, command)
        return green, json.loads(green.stdout.splitlines()[-1])

    def gaps(self, payload: dict) -> list[str]:
        """The GREEN run entry's proofGaps: the summary and every surviving mutation."""
        return list(self.h.evidence()["runs"][-1].get("proofGaps") or [])

    def stray(self, marker: bytes) -> list[str]:
        """This test's processes (its HOME is in their environment) still running a proof after the GREEN returned."""
        def read(p: Path, name: str) -> bytes:
            try:
                return (p / name).read_bytes()
            except OSError:  # the process exited while being listed, or is not ours
                return b""
        home = f"HOME={self.h.env['HOME']}".encode()
        return [p.name for p in Path("/proc").iterdir()
                if p.name.isdigit() and marker in read(p, "cmdline") and home in read(p, "environ").split(b"\0")]

    def test_gap_is_named_for_each_runner(self) -> None:
        for runner in ["unittest", "child", "script", "src"] + (["pytest"] if PYTEST else []):
            with self.subTest(runner=runner):
                self.tearDown()
                self.setUp()
                if runner == "src":  # the code is imported through an absolute PYTHONPATH into the checkout
                    (self.h.repo / "src").mkdir()
                    self.h.git("mv", "calc.py", "src/calc.py")
                    self.h.git("commit", "-q", "-m", "src layout")
                    self.h.env["PYTHONPATH"] = os.pathsep.join([str(self.h.repo / "src"), self.h.env["PYTHONPATH"]])
                    self.calc = self.h.repo / "src" / "calc.py"
                name = "check_weak" if runner == "script" else "test_weak"
                slug, _ = self.h.begin_with_map([self.item("BM_W", "WEAK_FAILED")])
                green, payload = self.cycle(slug, "BM_W", self.proof(name, FAILS, "WEAK_FAILED", runner),
                                            weaken=(name, WEAK))
                lines, status = self.gaps(payload), self.h.evidence()["behaviorMap"][0]["status"]
                self.assertTrue(green.returncode == 0 and status == "green"
                                and any("calc.py:3" in line for line in lines[1:]),
                                "PROOF_GAP_NOT_REPORTED: " + json.dumps([green.returncode, status, lines]))

    def payload_green(self, slug: str, identifier: str, command: tuple[str, ...], code: str, weak: bool = True) -> dict:
        _, payload = self.cycle(slug, identifier, command, code, weaken=(command[-1].split(".")[0], WEAK) if weak else None)
        return payload

    def test_survivors_are_plain_lines_and_nothing_is_sent(self) -> None:
        # A listening socket stands in for the network: with a TypeSafe key set, nothing may connect to it.
        server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
        self.addCleanup(server.close)
        connections = []
        threading.Thread(target=lambda: connections.append(server.accept()), daemon=True).start()
        self.h.env.update(TYPESAFE_API_KEY="set-for-test", HTTPS_PROXY=f"http://127.0.0.1:{server.getsockname()[1]}")
        slug, _ = self.h.begin_with_map([self.item("BM_W", "WEAK_FAILED")])
        shown = self.payload_green(slug, "BM_W", self.proof("test_weak", FAILS, "WEAK_FAILED"), BRANCHY).get("proofGaps") or []
        plain = "calc.py:3 (returns None: `return x * 2`) survived" in shown
        leaked = [line for line in shown if any(word in line for word in ("p=", "judged", "TypeSafe", "before "))]
        self.assertTrue(plain and not leaked and not connections, "JEV_STILL_CALLED: " + json.dumps([len(connections), shown]))

    def test_every_survivor_is_recorded_once_and_counted(self) -> None:
        # One run with every outcome: dropping `a = 1` changes nothing observed, dropping `n = 0` fails the proof,
        # dropping `n += 1` hangs, and the negated loop, flipped comparison and returned None survive.
        slug, _ = self.h.begin_with_map([self.item("BM_P", "WEAK_FAILED")])
        code = "def scale(x):\n    a = 1\n    n = 0\n    while n < x:\n        n += 1\n    return n * 2\n"
        shown = self.payload_green(slug, "BM_P", self.proof("test_part", FAILS, "WEAK_FAILED"), code).get("proofGaps") or []
        recorded = self.gaps({})
        survivors = [line for line in recorded[1:] if line.endswith(") survived")]
        unlisted = [line for line in recorded[1:] if line.endswith("survived; no difference detected")]
        counted = ("1 caught, 3 survived, 1 survived; no difference detected (in the run evidence), 1 inconclusive (timed out)"
                   in recorded[0])
        self.assertTrue(counted and len(survivors) == 3 and len(unlisted) == 1 and len(recorded) == 5
                        and len(set(recorded)) == 5 and shown == recorded[:1] + survivors,
                        "PARTITION_WRONG: " + json.dumps([shown, recorded]))

    def test_the_lead_sees_the_summary_unless_every_break_was_caught(self) -> None:
        cases = {"unlisted": ("def scale(x):\n    a = 1\n    return x * 2\n", "calc.scale(3) == 6", "1 survived; no difference detected"),
                 "timeout": ("def scale(x):\n    n = 0\n    while n < x:\n        n += 2\n    return n\n", "calc.scale(3) == 4",
                             "1 inconclusive (timed out)"),
                 "caught": (BRANCHY, "calc.scale(3) == 6", None)}
        for case, (code, check, counted) in cases.items():
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                slug, _ = self.h.begin_with_map([self.item("BM_V", "WEAK_FAILED")])
                command = self.proof("test_vis", FAILS, "WEAK_FAILED")
                path = self.h.repo / "test_vis.py"
                red = self.h.tdd(slug, "red", "BM_V", command)
                self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
                self.calc.write_text(code)
                path.write_text(path.read_text().replace(FAILS, check))
                shown = json.loads(self.h.tdd(slug, "green", "BM_V", command).stdout.splitlines()[-1]).get("proofGaps")
                recorded = self.gaps({})
                visible = shown is None if counted is None else shown == recorded[:1] and counted in shown[0]
                self.assertTrue(visible and recorded and "caught" in recorded[0],
                                "UNLISTED_SUMMARY_HIDDEN: " + json.dumps([case, shown, recorded]))

    def test_clean_proof_reports_a_zero_gap_summary(self) -> None:
        # Run-to-run noise must not read as a gap: a copy root outside /tmp, a value naming that root,
        # set iteration order, dead statements, and a proof that creates a file exclusively on every run.
        scratch = Path(tempfile.mkdtemp(dir="/dev/shm" if Path("/dev/shm").is_dir() else None))
        self.addCleanup(shutil.rmtree, scratch, True)
        self.h.env["TMPDIR"] = str(scratch)
        home = "import os\ndef home():\n    return os.path.dirname(os.path.abspath(__file__))\n"
        self.calc.write_text(home + "def scale(x):\n    return None\n")
        (self.h.repo / ".gitignore").write_text("marker.txt\n")
        self.h.git("add", ".gitignore", "calc.py")
        self.h.git("commit", "-q", "-m", "home")
        slug, _ = self.h.begin_with_map([self.item("BM_S", "STRONG_FAILED")])
        check = "calc.home() and all(calc.scale(len(s)) == 2 * len(s) for s in {'alpha', 'beta', 'gamma', 'delta'})"
        code = home + "def scale(x):\n    a = 1\n    if x > 0:\n        return x * 2\n    return 0\n"
        _, payload = self.cycle(slug, "BM_S", self.proof("test_strong", f"{check} and {STRONG}", "STRONG_FAILED"), code,
                                weaken=("test_strong", f"open('marker.txt', 'x').close() is None and {STRONG}"))
        lines = self.gaps(payload)
        self.assertTrue(lines and ", 0 survived" in lines[0] and not any(line.endswith(") survived") for line in lines),
                        "CLEAN_SUMMARY_MISSING: " + json.dumps(lines))

    def test_exception_whose_text_raises_is_observed(self) -> None:
        bad = "class Bad(Exception):\n    def __str__(self):\n        raise RuntimeError('no text')\n"
        self.calc.write_text(bad + "def scale(x):\n    if x < 0:\n        raise Bad()\n")
        self.h.git("commit", "-q", "-am", "bad")
        slug, _ = self.h.begin_with_map([self.item("BM_X", "STR_FAILED")])
        check = "isinstance(self.assertRaises(calc.Bad, calc.scale, -1), object) and"
        _, payload = self.cycle(slug, "BM_X", self.proof("test_x", f"{check} {FAILS}", "STR_FAILED"),
                                bad + "def scale(x):\n    if x < 0:\n        raise Bad()\n    return x * 2\n")
        summary = (self.h.evidence()["runs"][-1].get("proofGaps") or [""])[0]
        self.assertTrue("breaks" in summary, "STR_EXCEPTION_NOT_OBSERVED: " + summary)

    def greens(self, slug: str, checks: dict[str, str], code: str) -> dict[str, list[str]]:
        """RED every item's proof, land `code`, then weaken each proof's FAILS check and record its GREEN."""
        commands = {i: self.proof(f"test_{i.lower()}", check, "OWNED_FAILED") for i, check in checks.items()}
        for identifier, command in commands.items():
            red = self.h.tdd(slug, "red", identifier, command)
            self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        self.calc.write_text(code)
        lines = {}
        for identifier, command in commands.items():
            path = self.h.repo / f"test_{identifier.lower()}.py"
            path.write_text(path.read_text().replace(FAILS, WEAK))
            lines[identifier] = self.gaps(json.loads(self.h.tdd(slug, "green", identifier, command).stdout.splitlines()[-1]))
        return lines

    def test_breaks_target_only_lines_this_proof_owns(self) -> None:
        # Two items' proofs already ran scale's changed lines, so a third proof that also runs them is checked
        # only on the changed lines it owns (offset), and its unchecked scale result is not reported.
        slug, _ = self.h.begin_with_map([self.item(i, "OWNED_FAILED") for i in ("BM_A", "BM_B", "BM_C")])
        self.calc.write_text("def scale(x):\n    return None\ndef offset(x):\n    return None\n")
        checks = {"BM_A": FAILS, "BM_B": FAILS, "BM_C": "[calc.scale(3)] is not None and calc.offset(4) == 5"}
        lines = self.greens(slug, checks, BRANCHY + "def offset(x):\n    if x < 100:\n        return x + 1\n    return x\n")
        summaries = {i: (found or [""])[0] for i, found in lines.items()}
        survived = lambda summary: re.search(r"[1-9]\d* survived", summary)
        self.assertTrue(survived(summaries["BM_A"]) and not survived(summaries["BM_C"]),
                        "FOREIGN_SITE_REPORTED: " + json.dumps(summaries))

    def test_an_earlier_pass_does_not_own_a_new_pass_lines(self) -> None:
        slug, _ = self.h.begin_with_map([self.item(i, "OWNED_FAILED") for i in ("BM_P1", "BM_P2")])
        self.greens(slug, {"BM_P1": FAILS, "BM_P2": FAILS}, BRANCHY)
        self.calc.write_text("def scale(x):\n    return None\n")
        self.h.git("add", "-A")
        self.h.git("commit", "-q", "-m", "pass 1 closed")
        slug, _ = self.h.begin_with_map([self.item("BM_N", "OWNED_FAILED")], slug="second-pass")
        lines = self.greens(slug, {"BM_N": FAILS}, BRANCHY)["BM_N"]
        self.assertTrue(any("calc.py:3" in line for line in lines[1:]), "EARLIER_PASS_OWNS_LINES: " + json.dumps(lines))

    def test_proof_owning_no_changed_line_is_told_so(self) -> None:
        slug, _ = self.h.begin_with_map([self.item(i, "OWNED_FAILED") for i in ("BM_A", "BM_B", "BM_C")])
        summary = self.greens(slug, {"BM_A": FAILS, "BM_B": FAILS, "BM_C": FAILS}, BRANCHY)["BM_C"][0]
        self.assertTrue("other items" in summary and "ran none" not in summary, "OWNS_NONE_MISREPORTED: " + summary)

    def test_only_items_still_green_own_lines(self) -> None:
        # A and B went GREEN on scale, then their proofs regressed: they no longer count toward C's ownership.
        slug, _ = self.h.begin_with_map([self.item(i, "OWNED_FAILED") for i in ("BM_A", "BM_B", "BM_C")])
        self.greens(slug, {"BM_A": FAILS, "BM_B": FAILS}, BRANCHY)
        for identifier in ("BM_A", "BM_B"):
            path = self.h.repo / f"test_{identifier.lower()}.py"
            path.write_text(path.read_text().replace(WEAK, "False"))
            self.h.tdd(slug, "green", identifier, (sys.executable, "-m", "unittest", f"test_{identifier.lower()}.T.test_it"))
        self.calc.write_text("def scale(x):\n    return None\n")
        lines = self.greens(slug, {"BM_C": FAILS}, BRANCHY)["BM_C"]
        self.assertTrue(any("calc.py:3" in line for line in lines[1:]), "STALE_OWNER_COUNTED: " + json.dumps(lines))

    def test_observer_absence_is_reported(self) -> None:
        slug, _ = self.h.begin_with_map([self.item("BM_E", "WEAK_FAILED")])
        _, payload = self.cycle(slug, "BM_E", self.proof("test_weak", FAILS, "WEAK_FAILED", "unittest-E"), weaken=("test_weak", WEAK))
        lines = self.gaps(payload)
        self.assertTrue(lines and "ran none" in lines[0] and "PYTHONPATH" in lines[0] and "gaps" not in lines[0],
                        "UNOBSERVED_NOT_FOLDED: " + json.dumps(lines))

    def test_slow_proof_is_bounded(self) -> None:
        # The proof sleeps past the check's budget only when the check reruns it.
        slug, _ = self.h.begin_with_map([self.item("BM_H", "HANG_FAILED")])
        command = self.proof("test_slow", FAILS, "HANG_FAILED")
        path = self.h.repo / "test_slow.py"
        path.write_text("import os, time\nif os.environ.get('PROOF_GAPS_OUT'):\n    time.sleep(70)\n" + path.read_text())
        started = time.monotonic()
        green, _ = self.cycle(slug, "BM_H", command)
        elapsed = time.monotonic() - started
        self.assertTrue(elapsed < 90 and green.returncode == 0, "SLOW_PROOF_UNBOUNDED: " + json.dumps(elapsed))
        summary = (self.h.evidence()["runs"][-1].get("proofGaps") or [""])[0]
        self.assertTrue("time budget" in summary and "124" not in summary, "BUDGET_MISREPORTED: " + summary)

    def test_proof_runs_once_without_a_check(self) -> None:
        # No production .py change, and the opt-out: each GREEN runs its proof once, never under the check.
        for case in ("no-python", "opt-out"):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                counter = self.h.tmp / "executions"
                self.h.env["PROBE_COUNTER"] = str(counter)
                if case == "opt-out":
                    self.h.env["WORKFLOW_PROOF_GAPS"] = "off"
                (self.h.repo / "limit.txt").write_text("1\n")
                slug, _ = self.h.begin_with_map([self.item("BM_T", "LIMIT_FAILED")])
                (self.h.repo / "test_limit.py").write_text(
                    "import os, unittest\nfrom pathlib import Path\np = Path(os.environ['PROBE_COUNTER'])\n"
                    "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
                    "class T(unittest.TestCase):\n    def test_it(self):\n"
                    "        self.assertEqual(Path('limit.txt').read_text(), '2\\n', 'LIMIT_FAILED')\n")
                command = (sys.executable, "-m", "unittest", "test_limit.T.test_it")
                red = self.h.tdd(slug, "red", "BM_T", command)
                self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
                (self.h.repo / "limit.txt").write_text("2\n")
                if case == "opt-out":
                    self.calc.write_text(BRANCHY)
                payload = json.loads(self.h.tdd(slug, "green", "BM_T", command).stdout.splitlines()[-1])
                retained = (self.h.evidence()["runs"][-1].get("proofGaps") or [""])[0]
                expected = "proofGaps" not in payload and (
                    not retained if case == "opt-out" else "no production .py" in retained)
                self.assertTrue(counter.read_text() == "xx" and expected,
                                "EXTRA_PROOF_RUN: " + json.dumps([counter.read_text(), retained]))

    def test_malformed_observer_report_keeps_green(self) -> None:
        slug, _ = self.h.begin_with_map([self.item("BM_M", "BAD_FAILED")])
        command = self.proof("test_bad", FAILS, "BAD_FAILED")
        path = self.h.repo / "test_bad.py"
        path.write_text("import os\nout = os.environ.get('PROOF_GAPS_OUT')\nif out:\n"
                        "    open(os.path.join(out, 'bad.json'), 'w').write('{\"lines\": []}')\n" + path.read_text())
        green, _ = self.cycle(slug, "BM_M", command)
        evidence = self.h.evidence()
        summary = (evidence["runs"][-1].get("proofGaps") or [""])[0]
        self.assertTrue(green.returncode == 0 and evidence["behaviorMap"][0]["status"] == "green" and "not run" in summary,
                        "MALFORMED_REPORT_ABORTED_GREEN: " + json.dumps([green.returncode, summary, green.stderr[-300:]]))

    def test_worktree_is_untouched(self) -> None:
        slug, _ = self.h.begin_with_map([self.item("BM_W", "WEAK_FAILED")])
        command = self.proof("test_weak", FAILS, "WEAK_FAILED")
        red = self.h.tdd(slug, "red", "BM_W", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.h.repo / "calc.py").write_text(BRANCHY)
        (self.h.repo / "test_weak.py").write_text((self.h.repo / "test_weak.py").read_text().replace(FAILS, WEAK))

        def snapshot() -> dict[str, bytes]:
            return {str(p.relative_to(self.h.repo)): p.read_bytes() for p in sorted(self.h.repo.rglob("*"))
                    if p.is_file() and ".git" not in p.relative_to(self.h.repo).parts}
        before = snapshot()
        self.h.tdd(slug, "green", "BM_W", command)
        self.assertEqual(snapshot(), before, "WORKTREE_CHANGED")

    def test_hanging_and_budget_cut_breaks_are_bounded(self) -> None:
        # "hang": dropping `n += 1` spins forever and is stopped by its own 5 s limit (dropping `n = 0` is caught).
        # "cut": the proof is slow only under the check, so the shared budget cuts runs short; a cut run proves nothing.
        # Either way the GREEN returns within 90 s, counts no stopped run as caught, and leaves no process behind.
        loop = "def scale(x):\n    n = 0\n    while n < x:\n        n += 1\n    return n\n"
        slow = "import os, time\nif os.environ.get('PROOF_GAPS_OUT'):\n    time.sleep(15)\n"
        dead = "def scale(x):\n" + "".join(f"    v{n} = {n}\n" for n in range(8)) + BRANCHY.split("\n", 1)[1]
        for case, code, prefix in (("hang", loop, ""), ("cut", dead, slow)):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                slug, _ = self.h.begin_with_map([self.item("BM_H", "HANG_FAILED")])
                command = self.proof(f"test_{case}", FAILS, "HANG_FAILED")
                path = self.h.repo / f"test_{case}.py"
                path.write_text(prefix + path.read_text())
                started = time.monotonic()
                green, _ = self.cycle(slug, "BM_H", command, code, weaken=(f"test_{case}", WEAK))
                elapsed = time.monotonic() - started
                summary = (self.h.evidence()["runs"][-1].get("proofGaps") or [""])[0]
                stray = self.stray(f"test_{case}.T".encode())
                bounded = (" 0 caught" in summary and "inconclusive (timed out)" in summary and "skipped (time budget)" in summary
                           if case == "cut" else " 1 caught" in summary and "1 inconclusive (timed out)" in summary and elapsed >= 5)
                self.assertTrue(elapsed < 90 and not stray and green.returncode == 0 and bounded,
                                "TIMEOUT_COUNTED_CAUGHT: " + json.dumps([case, elapsed, stray, summary]))


if __name__ == "__main__":
    unittest.main()
