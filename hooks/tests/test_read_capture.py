#!/usr/bin/env python3
"""Read capture through the real hook processes: a Bash read is recorded for the
active pass and the compaction re-arm names what is still unchanged."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import hook_input, state_store  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.tests.test_workflow_hooks import HookHarness  # noqa: E402

POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
REARM = ROOT / "hooks" / "skill-discipline-rearm.py"


class ReadCandidateTests(unittest.TestCase):
    # BM_EXTRACTOR_MATCHES_CORPUS_SHAPES: the command shapes the CX2 lead used to read
    # files (sed 163, rg 38, python-open 23, cat 14, wc 13, jq 12, nl 9, tail 6, awk 3).
    def candidates(self, command: str) -> list[str]:
        return hook_input.read_candidates(command)

    def test_read_verbs_yield_their_file_arguments(self) -> None:
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("sed -n '1,40p' src/a.py"), ["src/a.py"], marker)
        self.assertEqual(self.candidates("sed -n '1,320p' docs/x.md\nsed -n '520,900p' src/db.py"),
                         ["docs/x.md", "src/db.py"], marker)
        self.assertEqual(self.candidates("rg -n 'record-production-code' hooks/lib/x.py | head -120"),
                         ["hooks/lib/x.py"], marker)
        self.assertEqual(self.candidates("rg -n -A3 'foo' -g '*.py' src/"), ["src/"], marker)
        self.assertEqual(self.candidates("cat AGENTS.md decisions.md"), ["AGENTS.md", "decisions.md"], marker)
        self.assertEqual(self.candidates("nl -ba tests/test_a.py | sed -n '1,230p'"), ["tests/test_a.py"], marker)
        self.assertEqual(self.candidates("jq -r '.x' logs/run.jsonl"), ["logs/run.jsonl"], marker)
        self.assertEqual(self.candidates("wc -l tests/test_a.py tests/test_b.py"),
                         ["tests/test_a.py", "tests/test_b.py"], marker)
        self.assertEqual(self.candidates("awk '{print $1}' data.csv"), ["data.csv"], marker)
        self.assertEqual(self.candidates("tail -100 ~/.codex/state/x/design.md"), ["~/.codex/state/x/design.md"], marker)
        self.assertEqual(self.candidates("python3 - < scripts/probe.py"), ["scripts/probe.py"], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nimport sqlite3\nc = sqlite3.connect('/home/u/state/workflow.sqlite3')\nPY"),
                         ["/home/u/state/workflow.sqlite3"], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nopen('notes/out.md', 'w').write('x')\nPY"), [], marker)

    def test_corpus_prefixes_conditionals_and_substitutions(self) -> None:
        # BM_CORPUS_SHAPES_PINNED: the shapes the CX2 corpus replay taught, kept here
        # instead of a frozen 350-command fixture.
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("timeout 900 python3 -m unittest hooks.tests.test_x"), [], marker)
        self.assertEqual(self.candidates("PYTHONDONTWRITEBYTECODE=1 sed -n '1,5p' src/a.py"), ["src/a.py"], marker)
        # A guarded read is claimed as written; the guard is not evaluated, and read_paths
        # drops a candidate that does not exist before anything is recorded.
        self.assertEqual(self.candidates("if [ -f decisions.md ]; then sed -n '1,240p' decisions.md; fi"),
                         ["decisions.md"], marker)
        self.assertEqual(self.candidates('python3 tool.py --intent "$(< /tmp/intent.txt)"'), [], marker)
        self.assertEqual(self.candidates('f=/tmp/out.json\njq -r .a "$f"'), ["/tmp/out.json"], marker)

    def test_substitution_is_prefix_safe_escape_safe_and_stops_at_separators(self) -> None:
        # BM_SUBSTITUTION_SAFE
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("f=/tmp/a.txt\nsed -n 1p $file"), [], marker)
        self.assertEqual(self.candidates("f=/tmp/a.txt\nsed -n 1p $f"), ["/tmp/a.txt"], marker)
        self.assertEqual(self.candidates('f=/tmp/a.txt\nsed -n 1p "${f}"'), ["/tmp/a.txt"], marker)
        self.assertEqual(self.candidates("x=1 && sed -n 1p real.py"), ["real.py"], marker)
        # Backslashes in a value are inserted verbatim, never interpreted as escapes.
        self.assertNotIn("\x0c", "".join(self.candidates("x='C:\\tmp\\f.txt'\nsed -n 1p $x")), marker)
        self.assertEqual(self.candidates("x=trailing\\\nsed -n 1p $x"), [], marker)

    def test_python_write_forms_are_not_reads(self) -> None:
        # BM_PY_WRITES_NOT_READS
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("python3 - <<'PY'\nfrom pathlib import Path\nPath('out.md').write_text('x')\nPY"), [], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nopen('f.txt', 'r+').write('x')\nPY"), [], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nopen('f.txt', 'w')\nPY"), [], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('a.md').read_text())\nPY"), ["a.md"], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nprint(open('b.md').read())\nPY"), ["b.md"], marker)

    def test_writes_options_and_patterns_are_not_reads(self) -> None:
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("echo hi > notes.txt"), [], marker)
        self.assertEqual(self.candidates("rg -n 'BM_A|phase.?red' /tmp/rollout.jsonl > /tmp/out.txt"),
                         ["/tmp/rollout.jsonl"], marker)
        self.assertEqual(self.candidates("sed -i 's/a/b/' src/a.py"), [], marker)
        self.assertEqual(self.candidates("git diff origin/main...HEAD -- decisions.md"), [], marker)
        self.assertEqual(self.candidates("python3 skills/x/scripts/workflow.py status --repo ."), [], marker)
        self.assertEqual(self.candidates("ls -la hooks/"), [], marker)


class ReadCaptureHookTests(HookHarness):
    def read(self, command: str, session: str = "sess-1") -> subprocess.CompletedProcess[str]:
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(self.repo), "session_id": session}
        return subprocess.run([str(POST_EDIT)], cwd=self.repo, env=self.env, text=True,
                              input=json.dumps(payload), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)

    def rearm(self) -> str:
        result = subprocess.run([str(REARM)], cwd=self.repo, env=self.env, text=True,
                                input=json.dumps({"cwd": str(self.repo)}),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_bash_read_records_the_path_and_hash_without_changing_state(self) -> None:
        # BM_READ_RECORDED
        begun = self.state("begin", "--slug", "reads")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        before = self.state("status").stdout
        result = self.read("sed -n '1,3p' app.py")
        self.assertEqual(result.returncode, 0, "READ_NOT_RECORDED: " + result.stdout + result.stderr)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(before)["workflowId"]
        digest = hashlib.sha256((self.repo / "app.py").read_bytes()).hexdigest()
        self.assertEqual(state_store.recorded_reads(identity, wid), {"app.py": digest}, "READ_NOT_RECORDED")
        self.assertEqual(self.state("status").stdout, before, "READ_NOT_RECORDED: state changed")

    def test_rearm_names_unchanged_reads_then_changed_ones(self) -> None:
        # BM_REARM_LISTS_UNCHANGED_AND_CHANGED
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        self.read("sed -n '1,1p' app.py 2>/dev/null")
        self.assertIn("Inspected this pass, unchanged since (1): app.py", self.rearm(), "REARM_OMITS_READS")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        context = self.rearm()
        self.assertIn("Changed since inspected (1): app.py", context, "REARM_OMITS_READS")
        self.assertNotIn("Inspected this pass, unchanged since", context, "REARM_OMITS_READS")

    def test_recency_survives_the_cap(self) -> None:
        # BM_READ_RECORDED: the most recent paths survive the cap, whatever their names sort to.
        identity = resolve_repo_identity(self.repo)
        state_store.record_reads(identity, "wid", {f"m{index:03d}.py": "h" for index in range(state_store._READS_KEPT)})
        state_store.record_reads(identity, "wid", {"aaa.py": "h"})
        kept = state_store.recorded_reads(identity, "wid")
        self.assertIn("aaa.py", kept, "READ_NOT_RECORDED")
        self.assertNotIn("m000.py", kept, "READ_NOT_RECORDED")
        self.assertEqual(list(kept)[-1], "aaa.py", "READ_NOT_RECORDED")

    def test_unreadable_file_is_skipped_and_the_hook_still_exits_zero(self) -> None:
        # BM_READ_RECORDED: fail-soft like every sibling recorder.
        if os.geteuid() == 0:
            self.skipTest("root reads mode-000 files")
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        secret = self.repo / "secret.txt"
        secret.write_text("x", encoding="utf-8")
        secret.chmod(0)
        try:
            result = self.read("cat secret.txt app.py")
        finally:
            secret.chmod(0o600)
        self.assertEqual(result.returncode, 0, "READ_NOT_RECORDED: " + result.stderr)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        self.assertEqual(set(state_store.recorded_reads(identity, wid)), {"app.py"}, "READ_NOT_RECORDED")

    def test_large_file_digest_is_size_and_mtime(self) -> None:
        # BM_LARGE_FILE_DIGEST: above the hash bound the digest is size:mtime_ns, the
        # re-arm still answers unchanged, and an mtime change flips it to changed.
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        self.assertTrue(hasattr(state_store, "HASH_BYTES"), "LARGE_FILE_HASHED")
        big = self.repo / "big.jsonl"
        with big.open("wb") as handle:
            handle.truncate(state_store.HASH_BYTES + 1)
        self.read("tail -c 10 big.jsonl")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        digest = state_store.recorded_reads(identity, wid).get("big.jsonl", "")
        self.assertTrue(digest.startswith("size:"), "LARGE_FILE_HASHED: " + digest)
        self.assertIn("Inspected this pass, unchanged since (1): big.jsonl", self.rearm(), "LARGE_FILE_HASHED")
        stamp = big.stat().st_mtime + 5
        os.utime(big, (stamp, stamp))
        self.assertIn("Changed since inspected (1): big.jsonl", self.rearm(), "LARGE_FILE_HASHED")

    def test_rearm_lists_the_newest_recorded_paths(self) -> None:
        # BM_REARM_LISTS_NEWEST
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for index in range(70):
            (self.repo / f"p{index:02d}.py").write_text(f"v = {index}\n", encoding="utf-8")
            state_store.record_reads(identity, wid, {f"p{index:02d}.py": state_store.content_digest(self.repo / f"p{index:02d}.py")})
        context = self.rearm()
        self.assertIn("p69.py", context, "REARM_OMITS_READS")
        self.assertNotIn("p09.py", context, "REARM_OMITS_READS")

    def test_malformed_sidecar_reads_as_empty_and_hooks_stay_silent(self) -> None:
        # BM_MALFORMED_SIDECAR_IS_EMPTY
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        state_store.record_reads(identity, wid, {"app.py": "h"})
        sidecar = state_store.repo_state_dir(identity) / "reads" / f"{wid}.json"
        for broken in ('{"reads": [null]}', '{"reads": [["a"]]}', '{"reads": "x"}'):
            sidecar.write_text(broken, encoding="utf-8")
            try:
                reads = state_store.recorded_reads(identity, wid)
            except (TypeError, ValueError) as exc:
                self.fail(f"SIDECAR_CRASHED_HOOK: {exc!r}")
            self.assertEqual(reads, {}, "SIDECAR_CRASHED_HOOK")
            self.assertIn("Discipline re-arm", self.rearm())
            result = self.read("cat app.py")
            self.assertEqual(result.returncode, 0, "SIDECAR_CRASHED_HOOK: " + result.stderr)
            self.assertIn("app.py", state_store.recorded_reads(identity, wid), "SIDECAR_CRASHED_HOOK")

    def test_recorded_path_replaced_by_a_fifo_reads_as_changed(self) -> None:
        # BM_NON_REGULAR_PATH_IS_CHANGED
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        outside = self.tmp / "outside.txt"
        outside.write_text("x\n", encoding="utf-8")
        self.read(f"cat {outside}")
        outside.unlink()
        os.mkfifo(outside)
        try:
            result = subprocess.run([str(REARM)], cwd=self.repo, env=self.env, text=True,
                                    input=json.dumps({"cwd": str(self.repo)}), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=False, timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("REARM_BLOCKED_ON_FIFO: the re-arm did not return within 20 s")
        self.assertEqual(result.returncode, 0, "REARM_BLOCKED_ON_FIFO: " + result.stderr)
        self.assertIn(f"Changed since inspected (1): {outside}",
                      json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"], "REARM_BLOCKED_ON_FIFO")

    def test_prune_retires_the_sidecar_with_its_workflow(self) -> None:
        # BM_SIDECAR_RETIRED_WITH_WORKFLOW
        self.assertEqual(self.state("begin", "--slug", "oldest").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        oldest = json.loads(self.state("status").stdout)["workflowId"]
        self.read("cat app.py")
        for index in range(5):
            self.assertEqual(self.state("begin", "--slug", f"later-{index}").returncode, 0)
        active = json.loads(self.state("status").stdout)["workflowId"]
        self.read("cat app.py")
        reads = state_store.repo_state_dir(identity) / "reads"
        self.assertTrue((reads / f"{oldest}.json").is_file() and (reads / f"{active}.json").is_file())
        pruned = subprocess.run([sys.executable, str(ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"), "prune", "--apply"],
                                cwd=self.repo, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(pruned.returncode, 0, pruned.stdout + pruned.stderr)
        self.assertFalse((reads / f"{oldest}.json").exists(), "SIDECAR_NOT_RETIRED")
        self.assertTrue((reads / f"{active}.json").is_file(), "SIDECAR_NOT_RETIRED")
        self.assertIn("follows-removed-workflow", pruned.stdout, "SIDECAR_NOT_RETIRED")

    def test_non_read_command_opens_no_state(self) -> None:
        # BM_NON_READ_OPENS_NO_STATE: the majority of Bash payloads read nothing and must
        # cost the hook no identity resolution or workflow lookup.
        state_root = Path(self.env["CODEX_WORKFLOW_STATE_ROOT"])
        self.assertFalse(state_root.exists())
        result = self.read("git status --short")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(state_root.exists(), "NON_READ_OPENED_STATE")

    def test_write_command_still_takes_the_edit_path_and_records_no_read(self) -> None:
        # BM_WRITE_PATH_UNCHANGED
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        kinds_before = [e["kind"] for e in json.loads(self.state("history").stdout)["events"]]
        result = self.read("printf 'value = 3\\n' > app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        self.assertEqual(state_store.recorded_reads(identity, wid), {}, "WRITE_TREATED_AS_READ")
        kinds_after = [e["kind"] for e in json.loads(self.state("history").stdout)["events"]]
        self.assertGreater(len(kinds_after), len(kinds_before), "WRITE_TREATED_AS_READ: no edit event appended")

    def test_rearm_keeps_the_discipline_text_and_summary(self) -> None:
        # BM_REARM_BASE_TEXT_UNCHANGED
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        context = self.rearm()
        self.assertIn("Discipline re-arm", context, "REARM_TEXT_LOST")
        self.assertIn("Active workflow:", context, "REARM_TEXT_LOST")


if __name__ == "__main__":
    unittest.main()
