#!/usr/bin/env python3
"""Request matching, safe observation storage, and preservation through real hooks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import context_evidence, hook_input, state_store  # noqa: E402
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
        self.assertEqual(self.candidates("rg --pre-glob '*.py' foo src/"), ["src/"], marker)
        self.assertEqual(self.candidates("rg -e --pre app.py"), ["app.py"], marker)
        self.assertEqual(self.candidates("cat AGENTS.md decisions.md"), ["AGENTS.md", "decisions.md"], marker)
        self.assertEqual(self.candidates("nl -ba tests/test_a.py | sed -n '1,230p'"), ["tests/test_a.py"], marker)
        self.assertEqual(self.candidates("jq -r '.x' logs/run.jsonl"), ["logs/run.jsonl"], marker)
        self.assertEqual(self.candidates("wc -l tests/test_a.py tests/test_b.py"),
                         ["tests/test_a.py", "tests/test_b.py"], marker)
        self.assertEqual(self.candidates("awk '{print $1}' data.csv"), ["data.csv"], marker)
        self.assertEqual(self.candidates("tail -100 ~/.codex/state/x/design.md"), ["~/.codex/state/x/design.md"], marker)
        self.assertEqual(self.candidates("python3 - < scripts/probe.py"), [], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nimport sqlite3\nc = sqlite3.connect('/home/u/state/workflow.sqlite3')\nPY"),
                         [], marker)
        self.assertEqual(self.candidates("python3 - <<'PY'\nopen('notes/out.md', 'w').write('x')\nPY"), [], marker)

    def test_corpus_prefixes_conditionals_and_substitutions(self) -> None:
        # BM_CORPUS_SHAPES_PINNED: the shapes the CX2 corpus replay taught, kept here
        # instead of a frozen 350-command fixture.
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("timeout 900 python3 -m unittest hooks.tests.test_x"), [], marker)
        self.assertEqual(self.candidates("PYTHONDONTWRITEBYTECODE=1 sed -n '1,5p' src/a.py"), ["src/a.py"], marker)
        # Existence cannot prove that the guarded branch executed.
        self.assertEqual(self.candidates("if [ -f decisions.md ]; then sed -n '1,240p' decisions.md; fi"),
                         [], marker)
        self.assertEqual(self.candidates('python3 tool.py --intent "$(< /tmp/intent.txt)"'), [], marker)
        # Nothing is expanded on the shell's behalf: a reference declines. Measured cost
        # on the CX2 corpus is 2 of 239 reads, against 39 lines of expansion machinery.
        self.assertEqual(self.candidates('f=/tmp/out.json\njq -r .a "$f"'), [], marker)

    def test_writes_options_and_patterns_are_not_reads(self) -> None:
        marker = "READ_CANDIDATES_WRONG"
        self.assertEqual(self.candidates("echo hi > notes.txt"), [], marker)
        self.assertEqual(self.candidates("rg -n 'BM_A|phase.?red' /tmp/rollout.jsonl > /tmp/out.txt"),
                         [], marker)
        self.assertEqual(self.candidates("sed -i 's/a/b/' src/a.py"), [], marker)
        self.assertEqual(self.candidates("git diff origin/main...HEAD -- decisions.md"), [], marker)
        self.assertEqual(self.candidates("python3 skills/x/scripts/workflow.py status --repo ."), [], marker)
        self.assertEqual(self.candidates("ls -la hooks/"), [], marker)

    def test_ambiguous_execution_and_writes_decline_the_whole_invocation(self) -> None:
        commands = [
            'f=a.py; f=b.py; cat "$f"',
            'cat "$f"; f=a.py',
            'f=a.py; cat "$f"; f=b.py; cat "$f"',
            'if false; then cat app.py; fi',
            'false && cat app.py',
            'true || cat app.py',
            'cat app.py &',
            'cat app.py; printf "NEW\\n" > app.py',
            'cat app.py; cp other.py app.py',
            'cat app.py; mv other.py app.py',
            'cat app.py; touch app.py',
            'rg --pre ./processor needle app.py',
            'rg --pre=./processor needle app.py',
            'cat app.py | tee app.py',
            'cd elsewhere; cat app.py',
            'cat app.py > /tmp/read-output.txt',
            'cat app.py >/dev/null',
            'sed -n "1p;w app.py" app.py',
            'sed -i.bak "s/a/b/" app.py',
            'awk \'{print $0 > "app.py"}\' app.py',
            'python3 - <<\'PY\'\nif False:\n    print(open("app.py").read())\nPY',
            'python3 - <<\'PY\'\nprint(open("app.py").read())\nopen("app.py", "w").write("new")\nPY',
            'python3 - <<\'PY\'\nprint(\nPY',
            'python3 - <<\'PY\'\nprint(1)\nPY',
            'python3 - <<\'PY\'\nprint(Path("app.py").read_text())\nPY',
            'python3 - <<\'PY\'\nprint(open(name).read())\nPY',
            'python3 - <<\'PY\'\nprint(open("app.py").close())\nPY',
            'python3 - <<\'PY\'\nprint(open("$HOME/app.py").read())\nPY',
            'cat "app.py',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), [])

    def test_stderr_suppression_does_not_discard_a_read(self) -> None:
        for command in ('cat app.py 2>/dev/null', 'sed -n 1p app.py 2>>/dev/null'):
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), ['app.py'])

    def test_an_edit_hidden_in_combined_options_is_not_an_inspection(self) -> None:
        # `-ni` edits in place exactly as `-i` does; only read-only letters may pass.
        for command in ("sed -ni '1p' app.py", "cat app.py; sed -ni '1p' app.py",
                        "sed -i '1p' app.py", "sed -i.bak '1p' app.py",
                        "sed --in-place '1p' app.py", "sed -e '1p' app.py"):
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), [])
        self.assertEqual(self.candidates("sed -n '1,40p' app.py"), ['app.py'])

    def test_jq_literal_arguments_and_null_input_are_not_reads(self) -> None:
        for command in ("jq -n --arg path app.py '$path'", "jq -n '.' app.py",
                        "jq --args '.' app.py extra"):
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), [])
        self.assertEqual(self.candidates("jq -r '.x' logs/run.jsonl"), ['logs/run.jsonl'])
        self.assertEqual(self.candidates("jq --arg k v '.x' logs/run.jsonl"), ['logs/run.jsonl'])


    def test_jq_options_do_not_turn_values_into_inputs(self) -> None:
        for command, expected in (
            ("jq -cn '.' app.py", []),
            ("jq -rnc '.' app.py", []),
            ("jq --null-input '.' app.py", []),
            ("jq --arg first ignored --arg second app.py '.' input.json", ['input.json']),
            ("jq --argjson first 1 --arg second app.py '.' input.json", ['input.json']),
            ("jq --arg flag -n '.' input.json", ['input.json']),
            ("jq --arg first app.py --arg second b.py '.' input.json", ['input.json']),
            ("jq --rawfile value app.py '.' input.json", ['input.json']),
            ("jq --slurpfile value input.json '.' other.json", ['other.json']),
            ("jq --tab '.' input.json", ['input.json']),
            ("jq --seq '.' input.json", ['input.json']),
            ("jq --indent 4 '.' input.json", ['input.json']),
            ("jq -cMrS '.' input.json", ['input.json']),
            ("jq -- '.' input.json", ['input.json']),
            ("jq --arg missing", []),
            ("jq --from-file filter.jq input.json", []),
            ("jq --run-tests app.py", []),
            ("jq --unknown app.py '.' input.json", []),
        ):
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), expected, "JQ_FALSE_INSPECTION")

    def test_redirected_input_requires_a_supported_stdin_consumer(self) -> None:
        for command, expected in (
            ('cat b.py < app.py', ['b.py']),
            ('cat < app.py', ['app.py']),
            ('cat - < app.py', ['app.py']),
            ('cat -n b.py - < app.py', ['app.py', 'b.py']),
            ('cat -- < app.py', ['app.py']),
            ('cat -e < app.py', ['app.py']),
            ('cat --help < app.py', []),
            ('cat --version b.py', []),
            ('cat - 3< app.py', []),
            ('cat < app.py < b.py', []),
            ('cat < app.py <<EOF\nUNREAD\nEOF', []),
            ('jq -n . < app.py', []),
            ('wc -l < app.py', []),
        ):
            with self.subTest(command=command):
                self.assertEqual(self.candidates(command), expected, "UNUSED_STDIN_INSPECTION")


class ReadCaptureHookTests(HookHarness):
    def requests(self, identity, wid):
        return {path: row for row in context_evidence.context_document(identity, wid)["records"]
                if row["kind"] == "request" for path in row["paths"]}

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

    def test_bash_request_records_scope_without_claiming_delivery_or_changing_state(self) -> None:
        # BM_READ_RECORDED
        begun = self.state("begin", "--slug", "reads")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        before = self.state("status").stdout
        result = self.read("sed -n '1,3p' app.py")
        self.assertEqual(result.returncode, 0, "READ_NOT_RECORDED: " + result.stdout + result.stderr)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(before)["workflowId"]
        observed = self.requests(identity, wid)["app.py"]
        self.assertEqual(observed["command"], "sed -n '1,3p' app.py")
        self.assertEqual(observed["delivery"], "unknown")
        self.assertEqual(observed["sourceBinding"], "unknown")
        self.assertNotIn("sourceDigest", observed)
        self.assertEqual(self.state("status").stdout, before, "READ_NOT_RECORDED: state changed")

    def test_rearm_does_not_promote_request_freshness_to_coverage(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        self.read("sed -n '1,1p' app.py 2>/dev/null")
        self.assertIn("history only", self.rearm())
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertNotIn("Inspected this pass", self.rearm())
        self.assertNotIn("sourceData", self.rearm())

    def test_executed_ambiguous_reads_do_not_create_sidecar_claims(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        (self.repo / "a.py").write_text("A_UNREAD\n", encoding="utf-8")
        (self.repo / "b.py").write_text("B_READ\n", encoding="utf-8")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for command, stdout in (
            ('if false; then cat app.py; fi', ''),
            ('f=a.py; f=b.py; cat "$f"', 'B_READ\n'),
        ):
            with self.subTest(command=command):
                executed = subprocess.run(["bash", "-c", command], cwd=self.repo,
                                          env=self.env, capture_output=True, text=True, check=True)
                self.assertEqual(executed.stdout, stdout)
                self.assertEqual(self.read(command).returncode, 0)
                self.assertEqual(self.requests(identity, wid), {})

    def test_executed_combined_option_edit_records_no_inspection(self) -> None:
        # Run it for real: the command succeeds, prints nothing, and replaces the file.
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        (self.repo / "app.py").write_text("one\ntwo\n", encoding="utf-8")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for command in ("sed -ni '1p' app.py", "cat app.py; sed -ni '1p' app.py"):
            with self.subTest(command=command):
                (self.repo / "app.py").write_text("one\ntwo\n", encoding="utf-8")
                subprocess.run(["bash", "-c", command], cwd=self.repo, env=self.env,
                               capture_output=True, text=True, check=True)
                self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "one\n")
                self.assertEqual(self.read(command).returncode, 0)
                self.assertEqual(self.requests(identity, wid), {})
        self.assertNotIn("Inspected this pass", self.rearm())

    def test_executed_jq_literal_argument_records_no_inspection(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for command, printed in (("jq -n --arg path app.py '$path'", '"app.py"\n'),
                                 ("jq -n '.' app.py", "null\n")):
            with self.subTest(command=command):
                executed = subprocess.run(["bash", "-c", command], cwd=self.repo, env=self.env,
                                          capture_output=True, text=True, check=False)
                if executed.returncode != 0:
                    self.skipTest("jq is unavailable")
                self.assertEqual(executed.stdout, printed)
                self.assertEqual(self.read(command).returncode, 0)
                self.assertEqual(self.requests(identity, wid), {})


    @unittest.skipUnless(shutil.which("jq"), "jq is unavailable")
    def test_executed_jq_option_boundaries_record_only_real_inputs(self) -> None:
        (self.repo / "input.json").write_text('{"source":"INPUT_ONLY"}\n', encoding="utf-8")
        identity = resolve_repo_identity(self.repo)
        for command, expected_output, expected_paths in (
            ("jq -cn '.' app.py", 'null', set()),
            ("jq -rnc '.' app.py", 'null', set()),
            ("jq --arg first ignored --arg second app.py '.' input.json", '{"source":"INPUT_ONLY"}', {'input.json'}),
            ("jq --arg flag -n '.' input.json", '{"source":"INPUT_ONLY"}', {'input.json'}),
            ("jq --rawfile value app.py '.' input.json", '{"source":"INPUT_ONLY"}', {'input.json'}),
            ("jq --tab '.' input.json", '{"source":"INPUT_ONLY"}', {'input.json'}),
            ("jq -cMrS '.' input.json", '{"source":"INPUT_ONLY"}', {'input.json'}),
        ):
            with self.subTest(command=command):
                begun = self.state("begin", "--slug", "reads")
                self.assertEqual(begun.returncode, 0, begun.stderr)
                wid = json.loads(begun.stdout)["workflowId"]
                executed = subprocess.run(["bash", "-c", command], cwd=self.repo, env=self.env,
                                          capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(executed.stdout), json.loads(expected_output))
                result = self.read(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                recorded = self.requests(identity, wid)
                self.assertEqual(set(recorded), expected_paths, "JQ_FALSE_INSPECTION")
                for row in recorded.values():
                    self.assertEqual(row["delivery"], "unknown")
                    self.assertNotIn("sourceDigest", row)
                self.assertNotIn('app.py', self.rearm(), "JQ_FALSE_INSPECTION")

    def test_executed_cat_stdin_is_claimed_only_when_consumed(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        (self.repo / "app.py").write_text('ONLY_APP\n', encoding="utf-8")
        (self.repo / "b.py").write_text('ONLY_B\n', encoding="utf-8")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for command, printed, expected_paths in (
            ("cat b.py < app.py", 'ONLY_B\n', {'b.py'}),
            ("cat < app.py <<'EOF'\nHEREDOC_ONLY\nEOF", 'HEREDOC_ONLY\n', {'b.py'}),
            ("cat < app.py", 'ONLY_APP\n', {'b.py', 'app.py'}),
            ("cat b.py - < app.py", 'ONLY_B\nONLY_APP\n', {'b.py', 'app.py'}),
        ):
            with self.subTest(command=command):
                executed = subprocess.run(["bash", "-c", command], cwd=self.repo, env=self.env,
                                          capture_output=True, text=True, check=True)
                self.assertEqual(executed.stdout, printed)
                self.assertEqual(self.read(command).returncode, 0)
                self.assertEqual(set(self.requests(identity, wid)), expected_paths,
                                 "UNUSED_STDIN_INSPECTION")
                if 'app.py' not in expected_paths:
                    self.assertNotIn('app.py', self.rearm(), "UNUSED_STDIN_INSPECTION")
                else:
                    self.assertIn('history only', self.rearm())

    def test_executed_pipeline_assignment_records_no_inspection(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        (self.repo / "b.py").write_text("B_READ\n", encoding="utf-8")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        command = 'f=app.py | cat "$f"'
        executed = subprocess.run(["bash", "-c", command], cwd=self.repo,
                                  env={**self.env, "f": "b.py"}, capture_output=True, text=True, check=True)
        self.assertEqual(executed.stdout, "B_READ\n")
        self.assertEqual(self.read(command).returncode, 0)
        self.assertEqual(self.requests(identity, wid), {})

    def test_executed_read_then_write_does_not_refresh_the_old_digest(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        self.read("cat app.py")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        before = self.requests(identity, wid)
        command = "cat app.py; printf 'NEW_UNSEEN\\n' > app.py"
        executed = subprocess.run(["bash", "-c", command], cwd=self.repo,
                                  env=self.env, capture_output=True, text=True, check=True)
        self.assertNotIn("NEW_UNSEEN", executed.stdout)
        self.assertEqual(self.read(command).returncode, 0)
        self.assertEqual(self.requests(identity, wid), before)
        self.assertIn("history only", self.rearm())
        self.assertNotIn("Inspected this pass, unchanged since", self.rearm())

    def test_recency_survives_the_cap(self) -> None:
        identity = resolve_repo_identity(self.repo)
        for index in range(context_evidence.RECORD_LIMIT + 1):
            context_evidence.remember_context(identity, "wid", {
                "kind": "request", "paths": [f"m{index:03d}.py"], "delivery": "unknown"})
        kept = self.requests(identity, "wid")
        self.assertNotIn("m000.py", kept)
        self.assertEqual(len(kept), 16)
        self.assertEqual(list(kept)[-1], f"m{context_evidence.RECORD_LIMIT:03d}.py")

    def test_unreadable_source_stays_unverified_history_and_hook_exits_zero(self) -> None:
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
        self.assertEqual(set(self.requests(identity, wid)), {"secret.txt", "app.py"})
        self.assertNotIn("sourceData", self.rearm())

    def test_large_file_observation_never_hashes_or_claims_content_identity(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        big = self.repo / "big.jsonl"
        with big.open("wb") as handle:
            handle.truncate(context_evidence.SOURCE_BYTES + 1)
        self.read("tail -c 10 big.jsonl")
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        observed = self.requests(identity, wid)["big.jsonl"]
        self.assertNotIn("sourceDigest", observed)
        stamp = big.stat()
        with big.open("r+b") as handle:
            handle.seek(context_evidence.SOURCE_BYTES // 2)
            handle.write(b"x")
        os.utime(big, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertIn("history only", self.rearm())
        self.assertNotIn("sourceData", self.rearm())

    def test_rearm_lists_newest_available_snapshots_under_the_byte_cap(self) -> None:
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        for index in range(70):
            name = f"p{index:02d}_" + "x" * 96 + ".py"
            (self.repo / name).write_text(f"v = {index}\n", encoding="utf-8")
            context_evidence.snapshot(identity, wid, name, 1, 1)
        context = self.rearm()
        self.assertIn("p69_", context)
        self.assertNotIn("p10_", context)
        self.assertNotIn("p09_", context)
        self.assertLessEqual(len(context_evidence.context_window(identity, wid).encode()), 1500)

    def test_malformed_sidecar_reads_as_empty_and_hooks_stay_silent(self) -> None:
        # BM_MALFORMED_SIDECAR_IS_EMPTY
        self.assertEqual(self.state("begin", "--slug", "reads").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        self.read("cat app.py")
        sidecar = state_store.repo_state_dir(identity) / "reads" / f"{wid}.json"
        for broken in ('{"reads": [null]}', '{"reads": [["a"]]}', '{"reads": "x"}'):
            sidecar.write_text(broken, encoding="utf-8")
            try:
                reads = self.requests(identity, wid)
            except (TypeError, ValueError) as exc:
                self.fail(f"SIDECAR_CRASHED_HOOK: {exc!r}")
            self.assertEqual(reads, {}, "SIDECAR_CRASHED_HOOK")
            self.assertIn("Discipline re-arm", self.rearm())
            result = self.read("cat app.py")
            self.assertEqual(result.returncode, 0, "SIDECAR_CRASHED_HOOK: " + result.stderr)
            self.assertIn("app.py", self.requests(identity, wid), "SIDECAR_CRASHED_HOOK")

    def test_request_path_replaced_by_fifo_never_blocks_or_claims_delivery(self) -> None:
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
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("history only", context)
        self.assertNotIn("sourceData", context)

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

    def test_prune_never_follows_a_symlinked_reads_directory(self) -> None:
        # BM_SYMLINKED_READS_NOT_FOLLOWED: a symlinked reads/ is retained, never walked,
        # so --apply cannot unlink a matching sidecar name outside the state slot.
        self.assertEqual(self.state("begin", "--slug", "oldest").returncode, 0)
        identity = resolve_repo_identity(self.repo)
        oldest = json.loads(self.state("status").stdout)["workflowId"]
        for index in range(5):
            self.assertEqual(self.state("begin", "--slug", f"later-{index}").returncode, 0)
        outside = self.tmp / "outside"
        outside.mkdir()
        target = outside / f"{oldest}.json"
        target.write_text('{"schemaVersion": 1, "reads": []}', encoding="utf-8")
        reads = state_store.repo_state_dir(identity) / "reads"
        shutil.rmtree(reads, ignore_errors=True)
        reads.symlink_to(outside, target_is_directory=True)
        pruned = subprocess.run([sys.executable, str(ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"), "prune", "--apply"],
                                cwd=self.repo, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(pruned.returncode, 0, pruned.stdout + pruned.stderr)
        self.assertTrue(target.is_file(), "SYMLINKED_READS_FOLLOWED")

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
        self.assertEqual(self.requests(identity, wid), {}, "WRITE_TREATED_AS_READ")
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
