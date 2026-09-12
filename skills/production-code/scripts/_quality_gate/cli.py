from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .git_scope import git_ok, git_text
from .runner import check, format_text


def read_optional_input(value: str) -> tuple[str, str | None]:
    if not value:
        return "", None
    if value == "-":
        return sys.stdin.read(), None
    try:
        return Path(value).read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return "", f"could not read optional input {value}: {exc}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the global production code quality gate.")
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--repo", default=os.getcwd())
    check_parser.add_argument("--base-ref", default="")
    check_parser.add_argument("--json", action="store_true")
    check_parser.add_argument("--fail-on-warnings", action="store_true")
    check_parser.add_argument("--repo-context-packet", default="")
    check_parser.add_argument("--gitnexus-context-json", default="")
    check_parser.add_argument(
        "--staged-only",
        action="store_true",
        help="evaluate the exact Git index tree against --base-ref; ignore worktree-only content",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not git_ok(repo, ["rev-parse", "--show-toplevel"]):
        print(f"ERROR: not a git repository: {repo}", file=sys.stderr)
        return 1
    root = Path(git_text(repo, ["rev-parse", "--show-toplevel"]).strip()).resolve()
    repo_context_packet, repo_context_error = read_optional_input(args.repo_context_packet)
    gitnexus_context_json, gitnexus_context_error = read_optional_input(args.gitnexus_context_json)
    if args.staged_only and not args.base_ref:
        parser.error("--staged-only requires --base-ref")
    result = check(
        root,
        args.base_ref or None,
        args.fail_on_warnings,
        repo_context_packet,
        gitnexus_context_json,
        args.staged_only,
    )
    optional_errors = [error for error in (repo_context_error, gitnexus_context_error) if error]
    if optional_errors:
        result["warnings"] = [*result.get("warnings", []), *optional_errors]
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_text(result))
        print("")
        print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2
