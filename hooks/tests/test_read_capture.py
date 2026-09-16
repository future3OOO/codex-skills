#!/usr/bin/env python3
"""Read capture through the real hook processes: a Bash read is recorded for the
active pass and the compaction re-arm names what is still unchanged."""
from __future__ import annotations

import hashlib
import json
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
