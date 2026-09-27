#!/usr/bin/env python3
"""Tests for the generic production code quality gate."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("code_quality_gate.py")
SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The last commit before #75 reshaped classification. Its shipped predicate is
# the oracle for the standalone truth workflow state still depends on.
PINNED_PRE_75 = "9f01e5f"
POLICY_PATH = "skills/production-code/scripts/_quality_gate/path_policy.py"

# The captured PR #68 round-six corpus, not the merged PR's final head.


def _load_path_policy(path: Path):
    """Load a path_policy module standalone, the way workflow state loads it."""
    spec = importlib.util.spec_from_file_location(f"_path_policy_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SourceRepositoryUnavailable(Exception):
    """Raised only when no source checkout is found — a repository that is
    present but missing something a test needs stays a hard failure."""


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, encoding="utf-8", errors="surrogateescape", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def source_repo() -> Path:
    """The repository whose history the characterization tests read; the
    estate installs these scripts outside any checkout, so ask Git."""
    res = run(["git", "rev-parse", "--show-toplevel"], SCRIPT_DIR)
    if res.returncode != 0:
        raise _SourceRepositoryUnavailable(f"{SCRIPT_DIR} is not inside a git checkout")
    return Path(res.stdout.strip())


def git(repo: Path, *args: str) -> None:
    res = run(["git", *args], repo)
    if res.returncode != 0:
        raise AssertionError(res.stderr or res.stdout)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="production-code-gate-"))
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    write(repo / "src" / "base.py", "def ok() -> int:\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def run_gate(repo: Path, *args: str) -> tuple[int, dict[str, object], str]:
    """Text mode: the exit code, the JSON verdict (last line), and the Warnings section alone."""
    res = run(["python3", str(SCRIPT), "check", "--repo", str(repo), *args], repo)
    assert res.stdout, res.stderr
    return res.returncode, json.loads(res.stdout.splitlines()[-1]), res.stdout.split("Warnings:", 1)[1].split("\n\n", 1)[0]


def growth_totals(payload: dict[str, object]) -> dict[str, object]:
    return payload["evaluation"]["growth"]


def growth_finding(payload: dict[str, object]) -> dict[str, object]:
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG54-GROWTH-CUMULATIVE"]
    assert len(findings) == 1, findings
    return findings[0]


def check_named(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in payload["checks"] if item["name"] == name)


def snapshot_paths(repo: Path) -> set[str]:
    return {
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts
    }


def in_repo(fn) -> None:
    """Run fn against a fresh real repository, always cleaned up."""
    repo = create_repo()
    try:
        fn(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def with_repo(fn):
    """Decorator form for the common single-scenario test."""
    def wrapper() -> None:
        in_repo(fn)

    wrapper.__name__ = fn.__name__
    return wrapper


@with_repo
def test_clean_pass(repo: Path) -> None:
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True
    assert payload["schemaVersion"] == 3, payload["schemaVersion"]
    assert set(payload["hardRules"]) == {"cleanup", "noMergeConflictMarkers"}
    hard_rules = payload["hardRules"]
    evaluated = [tuple(item["checks"]) for item in hard_rules.values() if item["status"] == "evaluated"]
    assert len(evaluated) == len(set(evaluated))
    assert hard_rules["noMergeConflictMarkers"]["checks"] == ["no-merge-conflict-markers"]


@with_repo
def test_gate_creates_no_repo_artifacts(repo: Path) -> None:
    # The non-mutation contract this pins: working tree, staged content, and
    # refs are untouched. Capture may refresh the index's cache-tree extension
    # and leave unreferenced loose objects that git gc prunes; nothing
    # references those, so they are out of scope here.
    write(repo / "src" / "candidate.py", "def candidate() -> int:\n    return 2\n")
    git(repo, "add", "src/candidate.py")
    worktree_only = "# TO" + "DO worktree-only text must not enter index evidence\n"
    write(repo / "src" / "candidate.py", worktree_only + "def candidate() -> int:\n    return 3\n")
    before = snapshot_paths(repo)
    status_before = run(["git", "status", "--porcelain=v1"], repo).stdout
    refs_before = run(["git", "for-each-ref"], repo).stdout
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    after = snapshot_paths(repo)
    assert code == 0
    assert payload["ok"] is True
    assert payload["candidateSource"] == "index"
    assert payload["candidateTree"] == run(["git", "write-tree"], repo).stdout.strip()
    assert after == before
    assert run(["git", "status", "--porcelain=v1"], repo).stdout == status_before
    assert run(["git", "for-each-ref"], repo).stdout == refs_before


# One quality-escape payload per row: typed test fakes stay green, fake-green
# swallowed asserts do not. Markers are assembled so this file is not flagged.
_ESCAPE_ROWS = (
    ("bare-noqa", "src/sloppy.py",
     "import os  # no" + "qa\n\n\ndef sloppy() -> str:\n    return os.sep\n", True),
    ("js-ts-escape", "src/bad.ts",
     "export function bad(value: any) {\n  // es" + "lint-disable-next-line\n  return value as any;\n}\n", True),
    ("python-escape", "src/bad.py",
     "from typing import Any\n\ndef bad(value: Any):\n    try:\n        return value\n    except Exception:\n        pass\n", True),
    ("test-any-annotation-is-allowed", "tests/test_fake.py",
     "from typing import Any\n\nclass Fake:\n    value: Any\n", False),
    ("test-fake-green-still-fails", "tests/test_bad.py",
     "def test_bad():\n    try:\n        assert False\n    except Exception:\n        pass\n", True),
    # A helper crash returned as a value reads as the checked failure (armSX-none db.py:1916).
    ("swallowed-catch-return", "src/check.py",
     "def check(sql):\n    try:\n        return run(sql)\n    except Exception as ex:\n        return str(ex)\n", True),
    # Hook entry points put the repo root on sys.path before importing, so E402 is unavoidable there.
    ("entrypoint-e402-is-allowed", "hooks/gate_one.py",
     "import sys\nsys.path.insert(0, '..')\nfrom lib.shared import helper  # noqa: E402\n", False),
    ("narrow-catch-return-is-allowed", "src/lookup.py",
     "def lookup(table, key):\n    try:\n        return table[key]\n    except KeyError:\n        return None\n", False),
)


def test_quality_escape_verdict_holds_for_every_payload() -> None:
    for name, path, content, fails in _ESCAPE_ROWS:
        in_repo(lambda repo, p=path, c=content, f=fails, label=name: _escape_row(repo, p, c, f, label))


def _escape_row(repo: Path, path: str, content: str, fails: bool, name: str) -> None:
    write(repo / path, content)
    code, payload, _ = run_gate(repo)
    if fails:
        assert code == 2, f"{'GATE_SWALLOWED_CATCH_PASSED' if name == 'swallowed-catch-return' else ''} {name} {code} {payload['errors']}"
        assert payload["hardRules"]["cleanup"]["passed"] is False, (name, payload["hardRules"]["cleanup"])
    else:
        assert code == 0 and payload["ok"] is True, (name, code, payload["errors"])


# One merge-conflict payload per row. A conflict is evidenced by either outer
# marker alone, each carrying a trailing space and a label that markup never
# produces; a bare separator line is legitimate reStructuredText and is not
# evidence of anything.
# name, path, content, expected conflict failure.
_CONFLICT_ROWS = (
    ("rst-section-underline", "docs/cli-reference.rst", "Options\n=======\n\nRun a query.\n", False),
    # Half-resolved files: each outer marker is evidence on its own, so both are
    # asserted separately — the full-triad fixtures cannot catch the loss of
    # either alternation, because the surviving one still fails the file.
    ("open-marker-alone", "src/half.py", "<" * 7 + " HEAD\nA = 1\n", True),
    ("close-marker-alone", "src/half.py", ">" * 7 + " theirs\nA = 1\n", True),
)


def test_conflict_verdict_holds_for_every_marker_shape() -> None:
    for name, path, content, fails in _CONFLICT_ROWS:
        in_repo(lambda repo, p=path, c=content, f=fails, label=name: _conflict_row(repo, p, c, f, label))


def _conflict_row(repo: Path, path: str, content: str, fails: bool, name: str) -> None:
    write(repo / path, content)
    code, payload, _ = run_gate(repo)
    rule = payload["hardRules"]["noMergeConflictMarkers"]
    if fails:
        assert code == 2, (name, code, payload["errors"])
        assert rule["passed"] is False, (name, rule)
        assert any("merge conflict markers" in item for item in payload["errors"]), (name, payload["errors"])
    else:
        assert code == 0 and payload["ok"] is True, (name, code, payload["errors"])
        assert rule["passed"] is True, (name, rule)


@with_repo
def test_distant_edits_do_not_fabricate_an_empty_catch(repo: Path) -> None:
    # Added lines from separate hunks are not adjacent in the candidate: an
    # except header edited in one place and a real `pass` added far below must
    # not join into an empty-catch escape that exists nowhere in the file.
    original = (
        "def parse(text):\n"
        "    try:\n"
        "        return int(text)\n"
        "    except ValueError:\n"
        "        return 0\n"
        "\n"
        "\n"
        "def audit(flag):\n"
        "    if flag:\n"
        "        log(flag)\n"
        "    return flag\n"
    )
    write(repo / "src" / "loader.py", original)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "loader")
    edited = original.replace("except ValueError:", "except Exception:").replace("        log(flag)", "        pass")
    write(repo / "src" / "loader.py", edited)
    code, payload, _ = run_gate(repo)
    assert code == 0, (code, payload["errors"])
    assert payload["ok"] is True


@with_repo
def test_large_growth_is_warning_only(repo: Path) -> None:
    # Cumulative human-authored growth over the review budget warns, never fails, and the finding keeps its intrinsic
    # pass; every rule starts promotion-ineligible, so even --fail-on-warnings cannot fail it until #54 approves an ID.
    write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
    code, payload, warnings = run_gate(repo, "--base-ref", "HEAD", "--fail-on-warnings")
    assert code == 0, (code, payload["errors"])
    assert payload["ok"] is True
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG54-GROWTH-CUMULATIVE"]
    assert len(findings) == 1, payload.get("findings")
    assert findings[0]["status"] == "finding" and findings[0]["passed"] is True, findings[0]
    assert "QG54-GROWTH-CUMULATIVE" in warnings, "TEXT_WARNINGS_HIDDEN"


# Every way scope can go missing: the affected rule reports incomplete and
# names the gap, its projections drop to unknown, and error-class capture
# failures fail the run outright. "*" sweeps all checks and every hard rule
# except the two not_evaluated policy keys, which evaluate caller input only
# and are legitimately untouched by capture gaps.
#
# name, git config, baseline files, candidate files (bytes stay unmeasured
# binary), staged, gate args, expectations.
_BINARY = b"def ok() -> int:\n    return 1\n\x00\x00binary\n"
_SCOPE_ROWS = (
    ("unknown-numstat", None, {}, {"src/base.py": _BINARY}, False, (),
     {"code": 0, "growth": "src/base.py"}),
    ("unmeasured-production-file", None, {}, {"src/base.py": _BINARY}, False, ("--base-ref", "HEAD"),
     {"code": 0, "growth": "src/base.py: Git reported no line counts", "text": "no line counts", "checks": ("no-quality-escapes",)}),
    # Incompleteness qualifies the measured growth warning; it never suppresses it.
    ("unbased-run", None, {}, {"src/app.py": "".join(f"VALUE_{i} = {i}\n" for i in range(600))}, False, (),
     {"code": 0, "growth": "no caller-supplied base", "incomplete": "QG54-GROWTH-CUMULATIVE",
      "evalGap": "no caller-supplied base", "text": "QG54-GROWTH-CUMULATIVE: human-authored net growth 600"}),
    # Unseen scope never un-sees a violation already found, and an established failure dominates an unknown sibling
    # in the hard rule, while the sibling itself stays unknown.
    ("witnessed-violation", None, {}, {"src/unmeasured.py": _BINARY, "src/escape.py": "def f():\n    try:\n        return 2\n    except Exception:\n        pass\n"},
     False, ("--base-ref", "HEAD"), {"code": 2, "failed": ("no-quality-escapes",)}),
    ("failed-child-outranks-unknown", None, {}, {"src/unmeasured.py": _BINARY, "tmp/leftover.py": "VALUE = 1\n"}, False, ("--base-ref", "HEAD"),
     {"code": 2, "failed": ("no-temp-artifacts",), "checks": ("no-quality-escapes",)}),
    ("missing-base-ref", None, {}, {"src/app.py": "VALUE = 1\n"}, False, ("--base-ref", "deadbeef"),
     {"code": 2, "error": "base-ref not found", "growth": "", "checks": "*", "hardRules": "*"}),
    # A clean filter that emits different bytes on every read stages different
    # content per capture pass: the gate must report drift, never evaluate a
    # state that never existed.
    ("capture-drift", ("filter.drift.clean", "sh -c 'cat >/dev/null; date +%s%N'"), {},
     {".gitattributes": "drifty.txt filter=drift\n", "drifty.txt": "content the filter rewrites every read\n"},
     False, ("--base-ref", "HEAD"),
     {"code": 2, "error": "capture drift", "evalGap": "capture drift", "checksNotTrue": True}),
    # A rejected repo-level diff config fails every diff read with empty
    # output while base resolution and capture stay healthy; read that failure
    # as "" and every rule passes over a change nobody looked at.
    ("failed-diff-read", ("diff.algorithm", "bogus"), {},
     {"src/app.py": "def one():\n    return 1\n"}, True, ("--base-ref", "HEAD"),
     {"code": 2, "error": "read failed", "evalGap": "", "checksNotTrue": True}),
)


def test_missing_scope_never_reads_as_a_clean_pass() -> None:
    for name, config, baseline, candidate, staged, args, expect in _SCOPE_ROWS:
        in_repo(lambda repo, cf=config, b=baseline, c=candidate, s=staged, a=args, e=expect, label=name:
                _scope_row(repo, cf, b, c, s, a, e, label))


def _scope_row(repo, config, baseline, candidate, staged, args, expect, name: str) -> None:
    if config:
        git(repo, "config", *config)
    for path, text in baseline.items():
        write(repo / path, text)
    if baseline:
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "baseline")
    for path, content in candidate.items():
        if isinstance(content, bytes):
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_bytes(content)
        else:
            write(repo / path, content)
    if staged:
        git(repo, "add", ".")
    code, payload, warnings = run_gate(repo, *args)
    assert code == expect["code"], (name, code, payload["errors"])
    assert payload["ok"] is (code == 0), (name, payload["errors"])
    if "error" in expect:
        assert any(expect["error"] in item for item in payload["errors"]), (name, payload["errors"])
    if "incomplete" in expect:
        assert f"for {expect['incomplete']}: " in warnings, (name, warnings)
    assert expect.get("text", "") in warnings, (name, warnings)
    if "growth" in expect:
        finding = growth_finding(payload)
        assert finding["status"] == "incomplete" and finding["completeness"]["complete"] is False, (name, finding)
        assert any(expect["growth"] in gap for gap in finding["completeness"]["gaps"]), (name, finding)
    checks = expect.get("checks", ())
    if checks == "*":
        checks = [item["name"] for item in payload["checks"]]
    for check_name in checks:
        item = check_named(payload, check_name)
        assert item["passed"] is None and item["status"] == "incomplete", (name, item)
    hard_rules = expect.get("hardRules", ())
    if hard_rules == "*":
        hard_rules = list(payload["hardRules"])
    for rule_name in hard_rules:
        rule = payload["hardRules"][rule_name]
        assert rule["status"] == "incomplete" and rule["passed"] is None, (name, rule_name, rule)
    for check_name in expect.get("failed", ()):
        assert check_named(payload, check_name)["passed"] is False, (name, check_named(payload, check_name))
        assert payload["hardRules"]["cleanup"]["status"] == "evaluated" and payload["hardRules"]["cleanup"]["passed"] is False, (name, payload["hardRules"])
    if expect.get("checksNotTrue"):
        for item in payload["checks"]:
            assert item["passed"] is not True, (name, item)
    if "evalGap" in expect:
        assert payload["evaluation"]["complete"] is False, (name, payload["evaluation"])
        assert any(expect["evalGap"] in gap for gap in payload["evaluation"]["gaps"]), (name, payload["evaluation"]["gaps"])


# Each row is one decoder or transport branch Git can put in front of the
# gate: a path the decoder mishandles must never silently drop out of the
# measured change.
#
# name, git config, baseline files, candidate files, expected production
# growth, expected error substring, expected sample path.
_DECODER_ROWS = (
    ("c-quoted-tab", None, {"src/we\tird.py": "A = 1\n"}, {"src/we\tird.py": "A = 1\nB = 2\n"},
     {"added": 1, "deleted": 0, "net": 1}, None, None),
    ("literal-leading-quote", None, {}, {'src/"weird.py': "A = 1\nB = 2\n"},
     {"added": 2, "deleted": 0, "net": 2}, None, None),
    ("non-utf8-with-quotepath-off", ("core.quotePath", "false"), {},
     {b"src/we\tir\xe9.py": b"def f():\n    return 1\n"},
     {"added": 2, "deleted": 0, "net": 2}, None, b"src/we\tir\xe9.py"),
    ("leading-whitespace-dir", None, {" pad/app.py": "A = 1\n"},
     {" pad/app.py": "A = 1\n<<<<<<< theirs\nB = 2\n=======\nC = 3\n>>>>>>> ours\n"},
     None, "merge conflict markers", b" pad/app.py"),
    ("control-char-payload", None, {},
     {"src/ctl.py": "Z = 'a\x0bb'  # " + "TO" + "DO: hidden after a control character\n"},
     None, "quality escapes", None),
    # A lossy decode would make the blob unaddressable; the unread file must
    # never read as a clean pass, and its five escape lines stay measured.
    ("non-utf8-escape-payload", None, {},
     {b"src/caf\xe9.py": b"def f():\n    try:\n        g()\n    except Exception:\n        pass\n"},
     {"added": 5, "deleted": 0, "net": 5}, "quality escapes", None),
)


def test_every_decoder_branch_stays_fully_measured() -> None:
    for name, config, baseline, candidate, growth, error, sample in _DECODER_ROWS:
        in_repo(lambda scratch, c=config, b=baseline, n=candidate, g=growth, e=error, s=sample, label=name:
                _decoder_row(scratch, c, b, n, g, e, s, label))


def _decoder_row(repo: Path, config, baseline: dict, candidate: dict, growth, error, sample, name: str) -> None:
    if config:
        git(repo, "config", *config)
    for path, content in baseline.items():
        write(repo / path, content)
    if baseline:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "baseline")
    for path, content in candidate.items():
        target = repo / (os.fsdecode(path) if isinstance(path, bytes) else path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    code, payload, warnings = run_gate(repo, "--base-ref", "HEAD")

    if error:
        assert any(error in item for item in payload["errors"]), (name, payload["errors"])
        assert code == 2, (name, code, warnings)
    else:
        assert payload["errors"] == [], (name, payload["errors"])
        assert code == 0, (name, code, warnings)
    if growth is not None:
        assert growth_totals(payload)["production"] == growth, (name, growth_totals(payload))
        # The odd name is unusual, not unmeasurable: it contributes no gap of
        # its own — only the universal graph-evidence gap may remain.
        assert all("graph evidence" in gap for gap in payload["evaluation"]["gaps"]), (name, payload["evaluation"]["gaps"])
    if sample is not None:
        encoded = [item.encode("utf-8", "surrogateescape") for item in payload["changedFilesSample"]]
        assert sample in encoded, (name, payload["changedFilesSample"])


# Growth accounting per row: which bucket counts each change, deletions and
# staged-deletion-plus-recreation measured rather than vanishing, and
# intermediate-only content neither leaking into escape rules nor
# double-counting.
# name, baseline files, ops after the baseline commit, candidate files,
# base-bound, expected buckets, clean check that must stay green.
_GROWTH_ROWS = (
    ("deleting-a-production-file-counts-as-deletions",
     {"src/legacy.py": "\n".join(f"OLD_{i} = {i}" for i in range(40)) + "\n"},
     (("unlink", "src/legacy.py"),), {}, True,
     {"production": {"added": 0, "deleted": 40, "net": -40}}, None),
    ("staged-deletion-with-unstaged-recreation-measures-the-candidate",
     {"src/thing.py": "OLD_A = 1\nOLD_B = 2\nOLD_C = 3\n"},
     (("rm", "src/thing.py"),),
     {"src/thing.py": "NEW_A = 1\nNEW_B = 2\nNEW_C = 3\nNEW_D = 4\n"}, True,
     {"production": {"added": 4, "deleted": 3, "net": 1}}, None),
    ("each-role-counts-separately", {}, (),
     {"src/app.py": "\n".join(f"VALUE_{i} = {i}" for i in range(10)) + "\n",
      "tests/test_app.py": "\n".join(f"def test_{i}():\n    assert {i} == {i}" for i in range(4)) + "\n",
      "tests/fixtures/sample.py": "SAMPLE = {'a': 1}\n"}, True,
     {"production": {"added": 10, "deleted": 0, "net": 10}, "test": {"added": 8, "deleted": 0, "net": 8},
      "testSupport": {"added": 1, "deleted": 0, "net": 1},
      "humanAuthored": {"added": 19, "deleted": 0, "net": 19}}, None),
    ("generated-and-non-source-stay-out-of-human-authored", {}, (),
     {"src/real.py": "REAL = 1\nREAL_TWO = 2\n",
      "src/generated/client.py": "\n".join(f"GEN_{i} = {i}" for i in range(30)) + "\n",
      "src/payload.schema.json": '{"type": "object"}\n',
      "docs/notes.md": "# notes\n\nprose\n"}, False,
     {"production": {"added": 2, "deleted": 0, "net": 2}, "generated": {"added": 30, "deleted": 0, "net": 30},
      "humanAuthored": {"added": 2, "deleted": 0, "net": 2}}, None),
    ("intermediate-commits-do-not-leak", {},
     (("commit", {"src/base.py": "def ok() -> int:  # TO" + "DO: temporary\n    return 1\n"}),),
     {"src/base.py": "def ok() -> int:\n    return 2\n"}, True,
     {"production": {"added": 1, "deleted": 1, "net": 0}}, "no-quality-escapes"),
)


def test_growth_accounting_holds_for_every_bucket() -> None:
    for name, baseline, ops, candidate, based, buckets, clean_check in _GROWTH_ROWS:
        in_repo(lambda repo, b=baseline, o=ops, c=candidate, bb=based, x=buckets, k=clean_check, label=name:
                _growth_row(repo, b, o, c, bb, x, k, label))


def _growth_row(repo, baseline, ops, candidate, based, buckets, clean_check, name: str) -> None:
    for path, text in baseline.items():
        write(repo / path, text)
    if baseline:
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "baseline")
    base = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    for op, arg in ops:
        if op == "unlink":
            (repo / arg).unlink()
        elif op == "rm":
            git(repo, "rm", "-q", arg)
        else:
            for path, text in arg.items():
                write(repo / path, text)
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "intermediate")
    for path, text in candidate.items():
        write(repo / path, text)
    code, payload, _ = run_gate(repo, *(("--base-ref", base) if based else ()))
    growth = growth_totals(payload)
    for bucket, expected in buckets.items():
        assert growth[bucket] == expected, (name, bucket, growth)
    if clean_check:
        assert check_named(payload, clean_check)["passed"] is True, (name, check_named(payload, clean_check))
    assert code == 0, (name, code, payload["errors"])


# The parent decision of 2026-08-12 binds every state-changing disposition
# record by its canonical content digest; a record cannot mint its own root.


# Captured historical bytes, not an installed-estate path to port.


@with_repo
def test_explicit_base_is_evaluated_as_the_commit_the_caller_supplied(repo: Path) -> None:
    # Base selection belongs to the caller; this Module captures the base it is
    # given. When the supplied base is not an ancestor of HEAD, resolving it to
    # anything else drops the very difference the caller asked about.
    write(repo / "src" / "shared.py", "SHARED = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "shared")
    fork = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    # The caller's chosen base drops shared.py and carries a file of its own;
    # a merge-base reading sees neither, and can never report a deletion.
    git(repo, "checkout", "-q", "-b", "side")
    git(repo, "rm", "-q", "src/shared.py")
    write(repo / "src" / "sideonly.py", "SIDE = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "side drops shared and adds its own")
    side = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    git(repo, "checkout", "-q", "-b", "feature", fork)
    write(repo / "src" / "feature.py", "FEATURE = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feature")

    for extra in ((), ("--staged-only",)):
        _, payload, _ = run_gate(repo, "--base-ref", side, *extra)
        base = payload["evaluation"]["base"]
        assert base["commit"] == side, (extra, base, fork)
        assert base["source"] == "caller", (extra, base)
        assert "src/shared.py" in payload["changedFilesSample"], (extra, payload["changedFilesSample"])
        assert "src/sideonly.py" in payload["changedFilesSample"], (extra, payload["changedFilesSample"])
        assert growth_totals(payload)["production"] == {"added": 2, "deleted": 1, "net": 1}, (extra, growth_totals(payload))


@with_repo
def test_rename_detection_ignores_repository_rename_limits(repo: Path) -> None:
    # A rename is not new content, so a marker that predates it is not introduced; diff.renameLimit=1 makes Git skip
    # exhaustive detection for two inexact renames, and the gate's own rename budget still holds. An edit riding on
    # the rename is evaluated at the new path.
    marker = "# TO" + "DO: predates the rename"
    write(repo / "src" / "a.py", f"{marker} A\nA = 1\nAA = 2\n")
    write(repo / "src" / "b.py", f"{marker} B\nB = 1\nBB = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    git(repo, "config", "diff.renameLimit", "1")
    git(repo, "mv", "src/a.py", "src/a2.py")
    git(repo, "mv", "src/b.py", "src/b2.py")
    with (repo / "src" / "a2.py").open("a", encoding="utf-8") as handle:
        handle.write("A = 9\n")
    with (repo / "src" / "b2.py").open("a", encoding="utf-8") as handle:
        handle.write("B = 9\n")
    code, payload, warnings = run_gate(repo, "--base-ref", "HEAD")
    assert payload["errors"] == [], payload["errors"]
    assert code == 0, (code, warnings)
    with (repo / "src" / "a2.py").open("a", encoding="utf-8") as handle:
        handle.write("X = 1  # " + "FIX" + "ME later\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2 and any("src/a2.py" in sample for sample in check_named(payload, "no-quality-escapes")["sample"]), payload["errors"]


@with_repo
def test_staged_quoted_path_escape_is_evaluated(repo: Path) -> None:
    # A staged filename holding a tab arrives C-quoted on Git's line-based
    # name transports; the gate must decode it back to the literal path and
    # evaluate the staged content, or an escape inside it silently passes.
    marker = "# TO" + "DO: staged escape behind a quoted path"
    write(repo / "src" / "we\tird.py", f"{marker}\ndef f() -> int:\n    return 1\n")
    git(repo, "add", "-A")
    code, payload, warnings = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    assert code == 2, (code, payload["errors"], warnings)
    escapes = check_named(payload, "no-quality-escapes")
    assert any("src/we\tird.py" in sample for sample in escapes["sample"]), escapes


@with_repo
def test_snapshot_reads_the_captured_tree_not_the_moving_worktree(repo: Path) -> None:
    # Concurrent mutation between capture and evaluation cannot produce a mixed
    # snapshot: every byte comes from the captured candidate tree object.
    #
    # DELIBERATE PROOF-CLASS EXCEPTION, operator-approved: this drives the
    # internal capture/freeze Seam directly and is not claimed as public-CLI
    # RED/GREEN. The CLI captures and evaluates in one process, so the public
    # Interface offers no window in which to mutate between the two, and none
    # was added solely for testing.
    import sys

    sys.path.insert(0, str(SCRIPT_DIR))
    from _quality_gate.git_scope import collect_scope
    from _quality_gate.snapshot import EvaluationSnapshot

    write(repo / "src" / "base.py", "def ok() -> int:\n    return 99\n")
    scope = collect_scope(repo, "HEAD")
    write(repo / "src" / "base.py", "def mutated() -> int:\n    return -1\n")
    snapshot = EvaluationSnapshot.from_scope(repo, scope)
    entry = next(entry for entry in snapshot.entries if entry.path == "src/base.py")
    assert entry.current_text == "def ok() -> int:\n    return 99\n", entry.current_text
    assert [text for _, text in entry.added_lines()] == ["    return 99"], entry.hunks
    assert entry.added == 1 and entry.deleted == 1, (entry.added, entry.deleted)


def test_detectors_cannot_read_git_or_the_filesystem_after_the_freeze() -> None:
    # DELIBERATE PROOF-CLASS EXCEPTION, operator-approved. This is structural
    # enforcement, not public-CLI RED/GREEN, and is not claimed as the latter:
    # detector reads run after the snapshot freezes, and the CLI captures and
    # evaluates in one process, so the public Interface offers no window in
    # which to observe such a read, and none was added solely for testing.
    detectors = ("checks.py", "bloat.py", "symbols.py", "findings.py")
    banned_calls = {"run_git", "git_read", "read_git_file", "open", "read_text", "read_bytes"}
    for name in detectors:
        tree = ast.parse((SCRIPT_DIR / "_quality_gate" / name).read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.Import) for alias in node.names
        }
        assert not {"git_scope", ".git_scope", "subprocess", "os"} & imported, (name, sorted(imported))
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert not banned_calls & called, (name, sorted(banned_calls & called))

    snapshot_source = (SCRIPT_DIR / "_quality_gate" / "snapshot.py").read_text(encoding="utf-8")
    assert "def read_baseline" not in snapshot_source, "read_baseline is a detector-time Git read"
    class_def = next(
        node for node in ast.parse(snapshot_source).body
        if isinstance(node, ast.ClassDef) and node.name == "EvaluationSnapshot"
    )
    fields = {
        node.target.id
        for node in class_def.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "repo" not in fields, "EvaluationSnapshot must hold no repository handle after the freeze"


@with_repo
def test_a_manifest_change_counts_as_production(repo: Path) -> None:
    # Dependency pins are configuration, not prose, whatever their spelling: the gate and the CI lane read one answer.
    marker = "MANIFEST_CHANGE_NOT_COUNTED_AS_PRODUCTION"
    write(repo / "requirements.txt", "ruff==0.16.2\n")
    _, payload, _ = run_gate(repo)
    growth = payload["evaluation"]["growth"]
    assert growth["production"]["added"] >= 1, f"{marker}: {growth}"
    assert growth["humanAuthored"]["added"] >= 1, f"{marker}: {growth}"
    module = _load_path_policy(SCRIPT_DIR / "_quality_gate" / "path_policy.py")
    for path in ("requirements-dev.txt", "dev-requirements.txt", "requirements.dev.txt", "requirements/dev.txt", "constraints/base.txt"):
        found = module.classify_path(path)
        assert found.role == module.ROLE_PRODUCTION and found.human_authored is True, f"{marker}: {path} is {found}"
    for path in ("docs/notes.md", "skills/guide.txt", "README.md"):
        assert module.classify_path(path).role == module.ROLE_DOCS, f"GATE_CALLED_A_MANIFEST_DOCUMENTATION: {path}"
    # A name is a manifest by its literal bytes: whitespace names, and one Git actually keeps in the index, are not.
    for path in (" requirements.txt", "requirements.txt\t", "requirements /dev.txt"):
        assert module.classify_path(path).role != module.ROLE_PRODUCTION, f"WHITESPACE_NAME_READ_AS_MANIFEST: {path!r}"
    write(repo / "requirements.txt ", "ruff==0.16.2\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    listed = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                            check=True, capture_output=True).stdout
    tracked = [os.fsdecode(name) for name in listed.split(b"\0") if name]
    spaced = [name for name in tracked if name != name.strip()]
    assert spaced, f"{marker}: git did not keep the whitespace name"
    for name in spaced:
        assert module.classify_path(name).role != module.ROLE_PRODUCTION, (
            f"WHITESPACE_NAME_READ_AS_MANIFEST: {name!r} counted as a manifest"
        )


def test_full_history_test_like_classification_is_unchanged() -> None:
    # The standalone predicate workflow state loads must keep the exact
    # pre-snapshot truth table over every path that ever existed here. The
    # oracle is the real predicate shipped at the pinned pre-#75 commit, never
    # a copy of its regexes: a copied oracle can be wrong in exactly the way
    # the implementation is wrong and still agree with it.
    module = _load_path_policy(SCRIPT_DIR / "_quality_gate" / "path_policy.py")
    repo = source_repo()
    pinned = run(["git", "show", f"{PINNED_PRE_75}:{POLICY_PATH}"], repo)
    assert pinned.returncode == 0, f"pinned {PINNED_PRE_75} {POLICY_PATH} unreachable: {pinned.stderr}"
    pinned_dir = Path(tempfile.mkdtemp(prefix="pinned-path-policy-"))
    try:
        write(pinned_dir / "path_policy.py", pinned.stdout)
        reference = _load_path_policy(pinned_dir / "path_policy.py").is_test_like_path
    finally:
        shutil.rmtree(pinned_dir, ignore_errors=True)

    listed = run(["git", "log", "--all", "--name-only", "--format="], repo)
    paths = {line for line in listed.stdout.splitlines() if line.strip()}
    paths |= set(run(["git", "ls-files"], repo).stdout.splitlines())
    assert len(paths) > 100, "full-history path enumeration failed"
    for path in sorted(paths):
        expected = reference(path)
        assert module.is_test_like_path(path) is expected, path
        assert module.classify_path(path).test_like_compat is expected, path
    assert module.is_test_like_path("src/generated/client.py") is True
    assert module.is_test_like_path("api/payload.schema.json") is True
    # The stored language stays inside the classification enum: real parser
    # names for source entries, "other" for everything else.
    assert module.classify_path("docs/notes.md").language == "other"
    assert module.classify_path("api/data.json").language == "other"
    assert module.classify_path("src/app.py").language == "python"


def test_gate_completes_on_an_unborn_repo_with_open_stdin() -> None:
    # git mktree reads stdin by design; the gate must not let any git child
    # inherit an open stdin, or the first run in a freshly initialized repo
    # hangs at a terminal until EOF.
    repo = Path(tempfile.mkdtemp(prefix="production-code-gate-unborn-"))
    read_fd, write_fd = os.pipe()
    try:
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        write(repo / "app.py", "VALUE = 1\n")
        proc = subprocess.Popen(
            ["python3", str(SCRIPT), "check", "--repo", str(repo), "--json"],
            stdin=read_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        )
        try:
            stdout, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError("gate hung on an unborn repository while stdin stayed open")
        payload = json.loads(stdout)
        assert proc.returncode == 0, stdout
        assert payload["ok"] is True, payload["errors"]
    finally:
        os.close(write_fd)
        os.close(read_fd)
        shutil.rmtree(repo, ignore_errors=True)


@with_repo
def test_growth_finding_carries_stable_identity_and_evidence(repo: Path) -> None:
    write(repo / "src" / "app.py", "VALUE = 1\n")
    first = growth_finding(run_gate(repo)[1])
    repeated = growth_finding(run_gate(repo)[1])
    assert first["ruleId"] == "QG54-GROWTH-CUMULATIVE"
    assert first["findingId"] == repeated["findingId"]
    assert first["severity"] == "warning"
    assert "passed" in first
    assert first["base"] and first["candidate"]
    assert first["region"]["scope"] == "evaluation"
    assert first["evidence"]["humanAuthored"] == {"added": 1, "deleted": 0, "net": 1}
    assert first["action"] and first["passCondition"]["kind"] == "growth-below"
    assert set(first["passCondition"]) == {"kind", "requires", "statement"}, first["passCondition"]
    write(repo / "src" / "app.py", "VALUE = 1\nOTHER = 2\n")
    assert growth_finding(run_gate(repo)[1])["findingId"] != first["findingId"]


@with_repo
def test_a_non_utf8_path_reaches_a_stable_finding(repo: Path) -> None:
    # A path whose bytes are not valid UTF-8 survives the whole pipeline: it is
    # read, its real bytes are serialized back out, and the finding hashes to
    # the same ID on a repeat run instead of raising.
    commit_change(repo, {"src/ids.py": "A = 1\n"})
    (repo / "src" / os.fsdecode(b"caf\xe9.py")).write_bytes(b"def cafe():\n    return 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    res, payload = bloat_run(repo, "--bloat-review")
    assert payload is not None, res.stderr
    paths = {unit["path"].encode("utf-8", "surrogateescape") for unit in bloat_rule(payload)["evidence"]["units"]}
    assert b"src/caf\xe9.py" in paths, paths
    first = bloat_rule(payload)["findingId"]
    assert len(first) == 16 and bloat_rule(bloat_run(repo, "--bloat-review")[1])["findingId"] == first


class _NoTypeSafeKey(Exception):
    """Raised by a live test when no TypeSafe key is configured."""


def bloat_run(repo: Path, *args: str, base: str = "HEAD~1", **env: str):
    """The real gate CLI in JSON mode with a keyless home unless `env` supplies one; payload None when no JSON."""
    clean = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "HTTPS_PROXY", "https_proxy")}
    res = subprocess.run(["python3", str(SCRIPT), "check", "--repo", str(repo), "--base-ref", base, "--json", *args], cwd=repo,
                         env={**clean, "HOME": str(repo / ".no-home"), **env}, encoding="utf-8", capture_output=True, check=False)
    try:
        return res, json.loads(res.stdout)
    except json.JSONDecodeError:
        return res, None


def bloat_rule(payload: dict[str, object] | None) -> dict[str, object]:
    found = [item for item in (payload or {}).get("findings", []) if item["ruleId"] == "QG-BLOAT" and item["region"]["scope"] == "evaluation"]
    return found[0] if found else {"evidence": {"units": []}, "completeness": {"gaps": []}}


def commit_change(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        write(repo / rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")


# Which code becomes a unit: (path, base text or None, changed text, {unit: kind} that must be judged, units that must not).
_UNIT_ROWS = (
    ("src/calc.py", "def total(items):\n    return sum(items)\n",
     "def total(items):\n    return sum(items)\n\n\ndef average(items):\n    return total(items) / len(items)\n", {"average": "function"}, {"total"}),
    ("scripts/deploy.sh", None, "#!/usr/bin/env bash\nrsync -a build/ \"$DEST/\"\n", {"<region>": "function"}, set()),
    ("src/Main.java", None, "class Main {\n  int twice(int x) { return 2 * x; }\n}\n", {"<region>": "function"}, set()),
    ("bin/release", None, "#!/bin/sh\nexec make release\n", {"<region>": "function"}, set()),
    ("package-lock.json", None, "{\"lockfileVersion\": 3}\n", {}, {"<region>"}),
    ("src/config.py", "class Config:\n    def reset(self):\n        self.limit = 5\n        self.debug = True\n",
     "class Config:\n    def reset(self):\n        self.limit = 5\n", {"reset": "function"}, {"Config"}),
    ("src/shapes.py", None, "class Square:\n    def area(self):\n        return 1\n\n\nclass Circle:\n    def area(self):\n        return 3\n", {"area@2": "function", "area@7": "function"}, set()),
    ("tests/test_div.py", None, "import pytest\n\n\ndef test_div_zero():\n    with pytest.raises(ZeroDivisionError):\n        1 / 0\n\n\n"
     "def test_both():\n    test_div_zero()\n\n\n@pytest.fixture\ndef test_data():\n    assert True\n    return [2]\n\n\n"
     "def items():\n    return [1]\n\n\ndef test_sum():\n    assert sum(items()) == 1\n",
     {"test_div_zero": "test", "test_both": "test", "test_sum": "test", "test_data": "function", "items": "function"}, set()),
    ("tests/test_fixture_text.py", None, "FIXTURE = \"\"\"\ndef test_inside():\n    assert 1 == 1\n\"\"\"\n\n\ndef test_real():\n    assert FIXTURE\n",
     {"test_real": "test"}, {"test_inside"}),
    ("tests/test_case.py", "import unittest\n\n\nclass Case(unittest.TestCase):\n    def test_one(self):\n        self.assertEqual(1, 1)\n",
     "import unittest\n\n\nclass Case(unittest.TestCase):\n    def test_one(self):\n        self.assertEqual(1, 1)\n\n    def test_two(self):\n        self.assertEqual(2, 2)\n\n",
     {"test_two": "test"}, {"Case", "test_one"}),
    ("tests/test_tool.sh", None, "#!/bin/sh\nout=$(./tool)\n[ \"$out\" = ok ] || exit 1\n", {"<region>": "test"}, set()),
    ("calc_test.go", None, "package calc\n\nfunc TestAddGo(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal(\"sum\")\n\t}\n}\n", {"TestAddGo": "test"}, set()),
    ("web/app.test.js", None, "it(\"works\", () => {\n  check(1);\n});\n", {"works": "test"}, set()),
    ("src/tune.py", "OLD_LIMIT = 5\n", "def tune(obj):\n    obj.limit = OLD_LIMIT\n    return obj\n", {"tune": "function"}, set()),
    ("src/Legacy.java", "class Legacy {\n  static final String LEGACY_FLAG = \"x\";\n  int keep() { return 1; }\n}\n",
     "class Legacy {\n  int keep() { return 1; }\n}\n", {}, set()),  # deletion only: its unit is asserted below
    ("src/imports.py", "import sys\nimport os\n\n\ndef here():\n    return os.getcwd()\n", "import os\n\n\ndef here():\n    return os.getcwd()\n", {}, {"<region>"}),
)


@with_repo
def test_bloat_units_follow_the_change_in_any_language(repo: Path) -> None:
    commit_change(repo, {path: base for path, base, *_ in _UNIT_ROWS if base is not None} | {"src/sweep.py": "def old():\n    return 1\n"})
    commit_change(repo, {path: changed for path, _, changed, *_ in _UNIT_ROWS})
    _, payload = bloat_run(repo, "--bloat-review", "--bloat-paths", "src/sweep.py")
    evidence = bloat_rule(payload)["evidence"]
    kinds = {(u["path"], u["symbol"]): u["kind"] for u in evidence["units"]} | {(u["path"], f"{u['symbol']}@{u['line']}"): u["kind"] for u in evidence["units"]}
    wrong = [(path, name, kinds.get((path, name))) for path, _, _, judged, skipped in _UNIT_ROWS
             for name in [*judged, *skipped] if kinds.get((path, name)) != judged.get(name)]
    touched = {u["symbol"] for u in bloat_rule(bloat_run(repo, "--bloat-review", "--bloat-touched")[1])["evidence"]["units"]}
    # Only `debug` left production (OLD_LIMIT is still named); every function of a changed file is judged with --bloat-touched.
    facts = {"sweep": ("src/sweep.py", "old") in kinds, "touched": "total" in touched}
    assert not wrong and all(facts.values()), f"BLOAT_UNITS_WRONG {wrong} {facts}"
    assert ("src/Legacy.java", "<region>") in kinds, f"BLOAT_UNITS_WRONG deletion-only Java change has no unit: {sorted(kinds)}"
    # A name removed from a file in any language (Java: static, final, String, LEGACY_FLAG; Python: debug) counts too.
    assert evidence.get("removedNames") == 5, f"BLOAT_UNITS_WRONG removed names {evidence.get('removedNames')}"


@with_repo
def test_bloat_review_fails_closed_and_never_moves_the_verdict(repo: Path) -> None:
    import socket
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    accepted: list[int] = []
    threading.Thread(target=lambda: [accepted.append(1) or c.close() for c, _ in iter(listener.accept, None)], daemon=True).start()
    keyhome = repo.parent / f"{repo.name}-keyhome"
    (keyhome / ".config" / "typesafe").mkdir(parents=True)
    (keyhome / ".config" / "typesafe" / "key").write_text("k", encoding="utf-8")
    (keyhome / ".config" / "typesafe" / "key").chmod(0)
    commit_change(repo, {"src/calc.py": "def total(items):\n    return sum(items)\n"})
    commit_change(repo, {"src/calc.py": "def total(items):\n    return sum(items)  # TO" "DO later\n", "src/sums.py": "def summed(values):\n    return sum(values)\n", "src/blob.py": "def ok():\n    return 1\n\x00\x00\n",
                         "src/odd\nname.py": "def odd():\n    return 1\n"})
    plain, verdict = bloat_run(repo)
    proxy = {"TYPESAFE_API_KEY": "present-but-never-delivered", "HTTPS_PROXY": f"http://127.0.0.1:{listener.getsockname()[1]}", "NO_PROXY": ""}
    silent = (bloat_run(repo, **proxy), len(accepted))[1]
    rows = (("no key", (), {}, "no TypeSafe key"), ("unreadable key", (), {"HOME": str(keyhome)}, "no TypeSafe key"),
            ("binary", (), {}, "src/blob.py"), ("newline path", (), {}, "newline"),
            ("unreadable graph", ("--gitnexus-context-json", str(repo.parent / "absent.json")), {}, "graph"), ("unreachable", (), proxy, "unreachable"),
            ("over budget", ("--bloat-budget", "1"), proxy, "over the 1-token budget"))
    wrong, sent = [], 0
    for label, args, env, gap in rows:
        res, got = bloat_run(repo, "--bloat-review", *args, **env)
        if not got or not any(gap in g for g in bloat_rule(got)["completeness"]["gaps"]) or res.returncode != plain.returncode \
                or (got["ok"], got["errors"]) != (verdict["ok"], verdict["errors"]):
            wrong.append(label)
        if label == "over budget" and len(accepted) != sent:  # sized first: nothing is sent over the budget
            wrong.append("sent over budget")
        sent = len(accepted)
    listener.close()  # no connection is made unless review is enabled
    assert not wrong and silent == 0 and len(accepted) >= 1 and verdict["ok"] is False, f"BLOAT_FAIL_OPEN {wrong} {silent} {len(accepted)}"


@with_repo
def test_new_tests_need_a_recorded_failing_run(repo: Path) -> None:
    moved = ("def test_kept_first():\n    assert True\n\n\n", "def test_kept_second():\n    assert True\n")
    commit_change(repo, {"src/calc.py": "def total(items):\n    return sum(items)\n", "tests/new/test_moved.py": moved[0] + moved[1],
                         "tests/new/test_old.sh": "#!/bin/sh\n./tool\n", "src/Gone.java": "class Gone {\n  static final String GONE_FLAG = \"y\";\n}\n"})
    (repo / "src" / "Gone.java").unlink()  # a whole file deleted: its names are removed names
    commit_change(repo, {"tests/new/test_more.py": "from calc import total\n\n\ndef test_total_again():\n    assert total([1, 2]) == 3\n\n\n"
                                                   "def test_total_twice():\n    assert total([2]) == 2\n\n\ndef test_total_thrice():\n    assert total([2]) == 2\n\n\n"
                                                   "def test_total_slow():\n    assert total([4]) == 4\n\n\ndef test_Case_mixed():\n    assert total([6]) == 6\n\n\n"
                                                   "def test_param_one():\n    assert total([7]) == 7\n",
                         "tests/new/test_old.sh": "#!/bin/sh\n[ \"$(./tool --check)\" = ok ] || exit 1\n",  # rewritten, not new
                         "tests/new/test_tool.sh": "#!/bin/sh\n[ \"$(./tool)\" = ok ] || exit 1\n", "tests/new/test_ran.sh": "#!/bin/sh\n[ \"$(./tool -v)\" = 1 ] || exit 1\n",
                         "tests/new/test_moved.py": moved[1] + "\n\n\n" + moved[0],  # reordered, not new
                         "tests/new/test_extra.py": "import pytest\nfrom calc import total\n\n\ndef test_extra():\n    assert total([3]) == 3\n\n\n"
                                                    "@pytest.fixture\ndef test_rows():\n    return [3]\n",
                         "tests/new/test_other.py": "from calc import total\n\n\ndef test_other_path():\n    assert total([5]) == 5\n",
                         "go/calc_test.go": "package calc\n\nfunc TestAddGo(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal(\"sum\")\n\t}\n}\n",
                         "web/more.test.js": "test(\"sums again\", () => {\n  expect(sum([1, 2])).toBe(3);\n});\n\ntest(\"sums thrice\", () => {\n  expect(sum([3])).toBe(3);\n});\n"})
    # A run proves the tests it selects by name in that same file (pytest -k as pytest evaluates it, node and dotted ids,
    # go -run, jest -t), or the one test holding its failing line; an excluded, elsewhere, shared-line or whole-file run proves nothing.
    reds = repo.parent / f"{repo.name}-reds.json"
    reds.write_text(json.dumps({"reds": [{"command": c, "site": s} for c, s in (
        ("python3 -m pytest -q tests/new/test_more.py -k 'total_again and not slow'", ""), ("python3 -m pytest -q tests/new/test_more.py -k 'not (thrice or slow)'", ""),
        ("python3 -m unittest tests.new.test_extra.test_extra", ""), ("python3 -m pytest -q other/test_other.py -k other_path", ""),
        ("python3 -m pytest -q tests/new/test_other.py", ""), ("", f"{repo}/tests/new/test_more.py:9 assert total([2]) == 2"),
        ("go test ./... -run 'TestAddGo$'", "sum"), ("go test ./... -run '*TestAdd'", ""), ("npx jest web/more.test.js -t 'sums thrice'", ""),
        ("python3 -m pytest tests -k case_mixed", ""), ("python3 -m pytest tests/new/test_more.py::test_param_one[1]", ""))]}), encoding="utf-8")
    res, proved = bloat_run(repo, "--bloat-review", "--tdd-evidence-json", str(reds))
    assert proved is not None, f"BLOAT_NEW_TEST_UNPROVEN the gate crashed on a recorded run: {res.stderr[-300:]}"
    unproven = sorted(item["evidence"]["owners"][0] for item in proved["findings"] if item["region"].get("category") == "test-unproven")
    assert bloat_rule(proved)["evidence"]["removedNames"] == 6, f"BLOAT_UNITS_WRONG deleted file {bloat_rule(proved)['evidence']['removedNames']}"
    assert unproven == ["tests/new/test_more.py:12 test_total_thrice", "tests/new/test_more.py:16 test_total_slow",
                        "tests/new/test_other.py:4 test_other_path", "web/more.test.js:1 sums again"], f"BLOAT_NEW_TEST_UNPROVEN {unproven}"
    # A new script test is proven by a run naming its file or folder; a script the base already had is not new.
    reds.write_text(json.dumps({"reds": [{"command": "bash tests/new/test_ran.sh", "site": ""}]}), encoding="utf-8")
    scripts = [item["evidence"]["owners"][0] for item in bloat_run(repo, "--bloat-review", "--tdd-evidence-json", str(reds))[1]["findings"]
               if item["region"].get("category") == "test-unproven" and item["evidence"]["owners"][0].endswith("<region>")]
    assert scripts == ["tests/new/test_tool.sh:1 <region>"], f"BLOAT_NEW_TEST_UNPROVEN scripts {scripts}"


def live_key() -> str:
    """The configured TypeSafe key, or the test is skipped: no key, no live proof."""
    from _quality_gate.jev import api_key
    if not api_key():
        if "pytest" in sys.modules:
            import pytest
            pytest.skip("no TypeSafe key configured")
        raise _NoTypeSafeKey("no TypeSafe key configured")
    return api_key()


# Live: code shortlists each changed function's counterparts and Jev judges each pair: a copied function, a copied shell
# region and a bound-method copy of a free helper print (question same); a new test whose check is one more row of an
# existing test in its folder prints against it (question host); an assertion guarding only removed code and a caller
# rewriting a key the changed function writes print; the unrelated function never does. A second run of the same change
# answers from the kept answers and sends nothing.
_DUPLICATE_BASE = {
    "src/calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
    "src/maps.py": "def recorded_map(tdd, preflight):\n    value = tdd.get('behaviorMap') if isinstance(tdd, dict) else None\n"
                   "    if value is None and isinstance(preflight, dict):\n        inner = preflight.get('document')\n"
                   "        value = inner.get('behaviorMap') if isinstance(inner, dict) else None\n    return value\n\n\n"
                   "def settings(document):\n    return document.get('settings') or {}\n",
    "scripts/a.sh": "sync_dirs() {\n  rsync -a \"$SRC\" \"$DEST\"\n  chmod +x \"$DEST\"/*\n}\n",
    "src/modes.py": "LEGACY_MODE = 'legacy-mode'\nCURRENT_MODE = 'current'\n",
    "src/report.py": "def report(items):\n    return {'items': items}\n",
    "src/main.py": "from report import report\n\n\ndef main(items, notes):\n    handler = report\n    result = handler(items)\n    result['warnings'] = [note for note in notes if note]\n    return result\n",
    "src/other.py": "A = {'warnings': 1}\nB = {'warnings': 2}\nC = {'warnings': 3}\nD = {'warnings': 4}\n",  # a key written everywhere
    "src/norm.py": "def normalize(values):\n    total = sum(values)\n    return [value / total for value in values] if total else list(values)\n",
    "tests/conftest.py": "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n",
    "tests/support.py": "import subprocess\n\n\ndef run_tool(repo, *args):\n    return subprocess.run(['tool', *args], cwd=repo, capture_output=True, text=True, check=False)\n",
    "tests/test_calc.py": "from calc import add, sub\n\n\ndef test_add_cases():\n    for a, b, want in ((2, 3, 5), (0, 0, 0), (-1, 1, 0)):\n        assert add(a, b) == want\n\n\n"
                          "def test_sub_small():\n    assert sub(5, 3) == 2\n",
}
_DUPLICATE_CHANGE = {
    "src/calc.py": "def add(a, b):\n    if not isinstance(a, int) or not isinstance(b, int):\n        raise TypeError('add takes integers')\n    return a + b\n\n\n"
                   "def sub(a, b):\n    return a - b\n",
    "src/state.py": "def map_items(document):\n    if not isinstance(document, dict):\n        return None\n    found = document.get('behaviorMap')\n"
                    "    if found is None:\n        nested = document.get('document')\n        found = nested.get('behaviorMap') if isinstance(nested, dict) else None\n    return found\n",
    "scripts/b.sh": "copy_tree() {\n  rsync -a \"$SRC\" \"$DEST\"\n  chmod +x \"$DEST\"/*\n}\n",
    "src/modes.py": "CURRENT_MODE = 'current'\n",
    "src/report.py": "def report(items):\n    return {'items': items, 'warnings': []}\n",
    "src/scales.py": "class Weights:\n    def normalized(self, values):\n        total = sum(values)\n        return [value / total for value in values] if total else list(values)\n\n\n"
                     "class Shares:\n    def normalized(self, values):\n        total = sum(values)\n        return [value / total for value in values] if total else list(values)\n",
    "tests/test_modes.py": "import modes\n\n\ndef test_legacy_mode_is_gone():\n    assert not hasattr(modes, 'LEGACY_MODE')\n\n\n"
                           "def test_mode_notes():\n    # an assertion on LEGACY_MODE would guard removed code\n    assert modes.CURRENT_MODE == 'current'\n    notes = (\n        'should read', 'LEGACY_MODE',\n    )\n    assert modes.CURRENT_MODE not in notes\n",
    "tests/test_calc_more.py": "import subprocess\n\nfrom calc import add\n\n\nclass Harness:\n    def __init__(self, repo):\n        self.repo = repo\n\n"
                               "    def run(self, *args):\n        return subprocess.run(['tool', *args], cwd=self.repo, capture_output=True, text=True, check=False)\n\n\n"
                               "def test_add_other_inputs():\n    for a, b, want in ((10, 20, 30),):\n        assert add(a, b) == want\n",
}


@with_repo
def test_jev_finds_duplicates_across_the_repo(repo: Path) -> None:
    key = live_key()
    commit_change(repo, _DUPLICATE_BASE)
    commit_change(repo, _DUPLICATE_CHANGE)
    graph = repo.parent / f"{repo.name}-graph.json"
    graph.write_text(json.dumps({"base": run(["git", "rev-parse", "HEAD~1"], repo).stdout.strip(), "candidate": run(["git", "rev-parse", "HEAD^{tree}"], repo).stdout.strip(),
                                 "symbols": [{"file": "src/report.py", "name": "report", "callers": ["Function:src/main.py:main"]}]}), encoding="utf-8")
    res, rejected = bloat_run(repo, "--bloat-review", TYPESAFE_API_KEY="invalid-key")  # before any answer is kept
    _, payload = bloat_run(repo, "--bloat-review", "--gitnexus-context-json", str(graph), TYPESAFE_API_KEY=key)
    _, again = bloat_run(repo, "--bloat-review", "--gitnexus-context-json", str(graph), TYPESAFE_API_KEY=key)
    lines = lambda got: sorted(json.dumps([f["region"], f["evidence"]], sort_keys=True) for f in got["findings"] if f["region"]["scope"] == "unit")
    evidence, cached = bloat_rule(payload)["evidence"], bloat_rule(again)["evidence"]
    # Same-named methods each print against the function they copy; identical requests are sent once.
    copies = {item["evidence"]["owners"][0] for item in payload["findings"] if item["region"].get("category") == "duplicate"
              and item["evidence"]["owners"][1:] == ["src/norm.py:1 normalize"]}
    assert evidence["requests"] < sum(evidence["questions"].values()), f"BLOAT_DUPLICATE_SENDS {evidence['requests']} {evidence['questions']}"
    assert copies == {"src/scales.py:2 normalized", "src/scales.py:8 normalized"}, f"BLOAT_SAME_NAME_COLLAPSED {copies}"
    problems = [] if cached.get("requests") == 0 and cached.get("cached", 0) > 0 and lines(again) == lines(payload) else [f"BLOAT_CACHE_MISS {cached.get('requests')} {cached.get('cached')}"]
    store = repo / ".git" / "codex-quality-gate" / "jev-answers.jsonl"  # one damaged line loses only its own answer
    store.write_text("not json\n" + "".join(store.read_text(encoding="utf-8").splitlines(keepends=True)[1:]), encoding="utf-8")
    partial = bloat_rule(bloat_run(repo, "--bloat-review", "--gitnexus-context-json", str(graph), TYPESAFE_API_KEY=key)[1])["evidence"]
    problems += [] if (partial.get("requests"), partial.get("cached")) == (1, cached["cached"] - 1) else [f"BLOAT_CACHE_MISS damaged line {partial.get('requests')} {partial.get('cached')}"]
    printed = {(item["evidence"]["question"], frozenset(re.sub(r":\d+ ", "::", owner) for owner in item["evidence"]["owners"])) for item in payload["findings"]
               if item["region"].get("category") == "duplicate" and isinstance(item["evidence"].get("score"), float)}
    missing = ({("same", frozenset(pair)) for pair in (("src/state.py::map_items", "src/maps.py::recorded_map"), ("scripts/b.sh::copy_tree", "scripts/a.sh::sync_dirs"),
                                                       ("tests/test_calc_more.py::run", "tests/support.py::run_tool"))} | {
        ("host", frozenset(("tests/test_calc_more.py::test_add_other_inputs", "tests/test_calc.py::test_add_cases")))}) - printed
    noise = {pair for pair in printed if any("settings" in owner for owner in pair[1])}
    missing |= {category for category, owner in (("deleted-guard", "tests/test_modes.py:4 test_legacy_mode_is_gone"), ("two-owners", "src/report.py:1 report"))
                if not any(item["region"].get("category") == category and item["evidence"]["owners"][0] == owner
                           and isinstance(item["evidence"].get("score"), float) for item in payload["findings"])}
    shape = sorted(evidence.get("questions", {}))
    refused = [g for g in bloat_rule(rejected)["completeness"]["gaps"] if "HTTP 401" in g]
    gaps = bloat_rule(payload)["completeness"]["gaps"]
    if missing or noise or gaps or not refused or res.returncode != 0 or shape != ["guard", "host", "owners", "same"]:
        problems.append(f"BLOAT_DUPLICATES_WRONG {missing} {noise} {gaps} {refused} {shape}")
    assert not problems, " ".join(problems)
    # Sized before sending, never under what TypeSafe counts: also for a change asking one short question.
    commit_change(repo, {"src/modes.py": "", "tests/test_modes.py": "import modes\n\n\ndef test_current_mode_is_gone():\n    assert not hasattr(modes, 'CURRENT_MODE')\n"})
    small = bloat_rule(bloat_run(repo, "--bloat-review", TYPESAFE_API_KEY=key)[1])["evidence"]
    sized = [(e["estimatedTokens"], e["inputTokens"], e["requests"]) for e in (evidence, small)]
    assert all(estimate >= counted for estimate, counted, _ in sized) and small["requests"] >= 1, f"BLOAT_BUDGET_UNDERSIZED {sized}"
    # Quoted text and comments are never assertions: only test_legacy_mode_is_gone is asked the deleted-code question.
    assert evidence["questions"]["guard"] == 1, f"BLOAT_DUPLICATES_WRONG text asked as an assertion: {evidence['questions']}"


def test_gate_implementation_budget() -> None:
    # Every raise requires a recorded operator approval against a measured total; a comment is never an approval.
    # 2825 (#54, 2026-08-12) -> 1900 when QG-BLOAT and the new-test-needed check replaced the QG54 duplicate and owner
    # rules (#115, 2026-09-27; measured 1878 after --bloat-paths over the package cut its own duplicate helpers).
    limits = {"wrapper_lines": 150, "module_lines": 700, "function_lines": 90, "total_lines": 1900}
    production_files = [SCRIPT, *sorted((SCRIPT_DIR / "_quality_gate").glob("*.py"))]
    line_counts = {str(path.relative_to(SCRIPT_DIR)): len(path.read_text(encoding="utf-8").splitlines()) for path in production_files}
    assert line_counts["code_quality_gate.py"] <= limits["wrapper_lines"]
    assert sum(line_counts.values()) <= limits["total_lines"], sum(line_counts.values())
    for rel_path, count in line_counts.items():
        assert count <= limits["module_lines"], rel_path
    for path in production_files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= limits["function_lines"], f"{path.name}:{node.name}"


def main() -> int:
    # Discovered in definition order: a hand-maintained registry silently
    # drops any test that is never added to it.
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    passed = skipped = 0
    for test in tests:
        try:
            test()
        except (_SourceRepositoryUnavailable, _NoTypeSafeKey) as reason:
            print(f"SKIP {test.__name__} ({reason})")
            skipped += 1
            continue
        # Counted only once the test has returned, so a failure escapes here
        # and aborts before it can be summarised as a pass.
        passed += 1
        print(f"PASS {test.__name__}")
    print(f"{passed} passed, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
