#!/usr/bin/env python3
"""Tests for the generic production code quality gate."""

from __future__ import annotations

import json
import os
import ast
import shutil
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("code_quality_gate.py")
SCRIPT_DIR = Path(__file__).parent


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


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
    res = run(["python3", str(SCRIPT), "check", "--repo", str(repo), "--json", *args], repo)
    payload = json.loads(res.stdout)
    return res.returncode, payload, res.stderr


def snapshot_paths(repo: Path) -> set[str]:
    return {
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts
    }


def with_repo(fn):
    repo = create_repo()
    try:
        fn(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_clean_pass() -> None:
    def body(repo: Path) -> None:
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert set(payload["hardRules"]) == {
            "codeVolume",
            "noDuplication",
            "shortestPath",
            "cleanup",
            "anticipateConsequences",
            "simplicity",
        }

    with_repo(body)


def test_temp_artifact_fails_cleanup() -> None:
    def body(repo: Path) -> None:
        write(repo / "tmp" / "debug.txt", "scratch\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False
        assert "no-temp-artifacts" in payload["hardRules"]["cleanup"]["checks"]

    with_repo(body)


def test_gate_creates_no_repo_artifacts() -> None:
    def body(repo: Path) -> None:
        before = snapshot_paths(repo)
        code, payload, _ = run_gate(repo)
        after = snapshot_paths(repo)
        assert code == 0
        assert payload["ok"] is True
        assert after == before

    with_repo(body)


def test_duplicate_added_block_fails() -> None:
    block = "\n".join(
        [
            "    const payload = normalizeInput(value) + normalizeInput(other);",
            "    const result = payload.trim().toLowerCase().replaceAll('x', 'y');",
            "    return result.includes('ready') ? result : `${result}:ready`;",
        ]
    )

    def body(repo: Path) -> None:
        write(repo / "src" / "dup.js", f"export function a(value, other) {{\n{block}\n}}\nexport function b(value, other) {{\n{block}\n}}\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["noDuplication"]["passed"] is False

    with_repo(body)


def test_js_ts_escapes_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "bad.ts", "export function bad(value: any) {\n  // eslint-disable-next-line\n  return value as any;\n}\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_python_escapes_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "bad.py", "from typing import Any\n\ndef bad(value: Any):\n    try:\n        return value\n    except Exception:\n        pass\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_test_any_annotations_do_not_fail_cleanup() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "test_fake.py", "from typing import Any\n\nclass Fake:\n    value: Any\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_test_fake_green_escapes_still_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "test_bad.py", "def test_bad():\n    try:\n        assert False\n    except Exception:\n        pass\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_new_huge_source_file_fails() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["codeVolume"]["passed"] is False

    with_repo(body)


def test_large_existing_file_must_not_grow() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "large.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1201)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "large baseline")
        with (repo / "src" / "large.py").open("a", encoding="utf-8") as handle:
            handle.write("EXTRA = 1\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["codeVolume"]["passed"] is False

    with_repo(body)


def test_fixtures_excluded_from_bloat() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "fixtures" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1200)) + "\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_reimplemented_existing_helper_fails() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["noDuplication"]["passed"] is False
        assert payload["reuseFindings"][0]["existingFile"] == "src/ids.py"

    with_repo(body)


def test_reimplemented_dedupe_loop_fails() -> None:
    def body(repo: Path) -> None:
        write(
            repo / "src" / "collections.py",
            "def dedupe_items(items: list[str]) -> list[str]:\n"
            "    seen = set()\n"
            "    out = []\n"
            "    for item in items:\n"
            "        if item not in seen:\n"
            "            seen.add(item)\n"
            "            out.append(item)\n"
            "    return out\n",
        )
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "dedupe helper")
        write(
            repo / "src" / "importer.py",
            "def import_items(items: list[str]) -> list[str]:\n"
            "    seen = set()\n"
            "    result = []\n"
            "    for item in items:\n"
            "        if item not in seen:\n"
            "            seen.add(item)\n"
            "            result.append(item)\n"
            "    return result\n",
        )
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["shortestPath"]["passed"] is False
        assert any(item["existingSymbol"] == "dedupe_items" for item in payload["reuseFindings"])

    with_repo(body)


def test_single_token_cross_domain_reuse_warning_is_suppressed() -> None:
    def body(repo: Path) -> None:
        write(repo / "api" / "contracts.py", "def _parse_limit(value: str) -> int:\n    return int(value)\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "parse helper")
        write(repo / "workers" / "cli.py", "def run(value: str) -> str:\n    parsed = value.split(':')\n    return parsed[0]\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_action_only_wait_helper_overlap_is_suppressed() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "waits.py", "def wait_for_tapi_authenticated_signal(page):\n    return page.url\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "wait helper")
        write(repo / "src" / "property_tree.py", "def wait_for_property_tree_authenticated_signal(page):\n    return page.url\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_calling_existing_helper_passes() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(
            repo / "src" / "users.py",
            "from src.ids import normalize_user_id\n\n"
            "def import_user(value: str) -> str:\n"
            "    return normalize_user_id(value)\n",
        )
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_helper_move_refactor_passes() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        (repo / "src" / "ids.py").unlink()
        write(repo / "src" / "identity.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_generic_name_does_not_fail_by_name_alone() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "cli.py", "def handler(event: str) -> str:\n    return event\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "handler")
        write(repo / "src" / "web.py", "def handler(request: str) -> str:\n    return request\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_same_file_related_helper_warns_but_function_edit_passes() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        with (repo / "src" / "users.py").open("a", encoding="utf-8") as handle:
            handle.write("\ndef normalize_user_record(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["reuseFindings"][0]["severity"] == "warning"
        assert payload["reuseFindings"][0]["existingFile"] == "src/users.py"
        write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().casefold()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_test_helper_is_not_reuse_evidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "helpers.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "test helper")
        write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_repo_context_packet_boosts_reuse_confidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "lib" / "users.py", "def normalize_account(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "account helper")
        write(repo / "app" / "users.py", "def normalize_account_record(value: str) -> str:\n    return value.strip().lower()\n")
        packet = repo / "packet.txt"
        write(packet, "<top_targets>\n<file path=\"lib/users.py\" />\n</top_targets>\n")
        code, payload, _ = run_gate(repo, "--repo-context-packet", str(packet))
        assert code == 2
        assert payload["reuseFindings"][0]["existingFile"] == "lib/users.py"

    with_repo(body)


def test_gitnexus_context_json_boosts_reuse_confidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "lib" / "orders.py", "def resolve_order(value: str) -> str:\n    return value.strip()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "order helper")
        write(repo / "app" / "orders.py", "def resolve_order_key(value: str) -> str:\n    return value.strip()\n")
        context = repo / "gitnexus.json"
        write(
            context,
            json.dumps(
                {
                    "symbols": [
                        {
                            "name": "resolve_order",
                            "file": "lib/orders.py",
                            "callers": ["checkout"],
                            "processes": ["order-import"],
                        }
                    ]
                }
            ),
        )
        code, payload, _ = run_gate(repo, "--gitnexus-context-json", str(context))
        assert code == 2
        assert payload["reuseFindings"][0]["existingSymbol"] == "resolve_order"

    with_repo(body)


def test_ambiguous_reuse_warns_with_gitnexus_query() -> None:
    def body(repo: Path) -> None:
        write(repo / "lib" / "orders.py", "def resolve_order(value: str) -> str:\n    return value.strip()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "order helper")
        write(repo / "lib" / "legacy.py", "def resolve_order_record(value: str) -> str:\n    return value.strip()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"][0]["severity"] == "warning"
        assert payload["gitnexusQueries"]

    with_repo(body)


def test_duplicate_polling_loop_is_grouped() -> None:
    def body(repo: Path) -> None:
        block = "\n".join(
            [
                "    deadline = time.monotonic() + timeout_seconds",
                "    while True:",
                "        current_url = page.url",
                "        body_text = page.locator('body').inner_text()",
                "        if check(current_url, body_text):",
                "            return current_url, body_text",
                "        if time.monotonic() >= deadline:",
                "            return current_url, body_text",
                "        wait_for_timeout = getattr(page, 'wait_for_timeout', None)",
                "        if callable(wait_for_timeout):",
                "            wait_for_timeout(poll_interval_ms)",
            ]
        )
        write(repo / "src" / "polls.py", f"def a(page, timeout_seconds, poll_interval_ms):\n{block}\n\ndef b(page, timeout_seconds, poll_interval_ms):\n{block}\n")
        code, payload, _ = run_gate(repo)
        duplicate_check = next(item for item in payload["checks"] if item["name"] == "no-duplicate-added-blocks")
        assert code == 2
        assert len(duplicate_check["sample"]) <= 3

    with_repo(body)


def test_bloat_reports_one_error_per_file() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "large.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1201)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "large baseline")
        with (repo / "src" / "large.py").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(f"EXTRA_{i} = {i}" for i in range(300)) + "\n")
        code, payload, _ = run_gate(repo)
        bloat_errors = [error for error in payload["errors"] if "large.py" in error]
        assert code == 2
        assert len(bloat_errors) == 1

    with_repo(body)


def test_gate_implementation_budget() -> None:
    limits = {
        "wrapper_lines": 150,
        "module_lines": 1200,
        "function_lines": 180,
        "total_lines": 1800,
    }
    review_triggers = {
        "module_lines": 700,
        "function_lines": 90,
        "total_lines": 1200,
    }
    justified: dict[str, str] = {}
    production_files = [SCRIPT, *sorted((SCRIPT_DIR / "_quality_gate").glob("*.py"))]
    line_counts = {str(path.relative_to(SCRIPT_DIR)): len(path.read_text(encoding="utf-8").splitlines()) for path in production_files}

    assert line_counts["code_quality_gate.py"] <= limits["wrapper_lines"]
    assert sum(line_counts.values()) <= limits["total_lines"]
    if sum(line_counts.values()) > review_triggers["total_lines"]:
        assert "TOTAL" in justified

    for rel_path, count in line_counts.items():
        if rel_path == "code_quality_gate.py":
            continue
        assert count <= limits["module_lines"], rel_path
        if count > review_triggers["module_lines"]:
            assert rel_path in justified

    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not hasattr(node, "end_lineno"):
                continue
            size = node.end_lineno - node.lineno + 1
            key = f"{path.relative_to(SCRIPT_DIR)}:{node.name}"
            assert size <= limits["function_lines"], key
            if size > review_triggers["function_lines"]:
                assert key in justified


def main() -> int:
    tests = [
        test_clean_pass,
        test_temp_artifact_fails_cleanup,
        test_gate_creates_no_repo_artifacts,
        test_duplicate_added_block_fails,
        test_js_ts_escapes_fail,
        test_python_escapes_fail,
        test_test_any_annotations_do_not_fail_cleanup,
        test_test_fake_green_escapes_still_fail,
        test_new_huge_source_file_fails,
        test_large_existing_file_must_not_grow,
        test_fixtures_excluded_from_bloat,
        test_reimplemented_existing_helper_fails,
        test_reimplemented_dedupe_loop_fails,
        test_single_token_cross_domain_reuse_warning_is_suppressed,
        test_action_only_wait_helper_overlap_is_suppressed,
        test_calling_existing_helper_passes,
        test_helper_move_refactor_passes,
        test_generic_name_does_not_fail_by_name_alone,
        test_same_file_related_helper_warns_but_function_edit_passes,
        test_test_helper_is_not_reuse_evidence,
        test_repo_context_packet_boosts_reuse_confidence,
        test_gitnexus_context_json_boosts_reuse_confidence,
        test_ambiguous_reuse_warns_with_gitnexus_query,
        test_duplicate_polling_loop_is_grouped,
        test_bloat_reports_one_error_per_file,
        test_gate_implementation_budget,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
