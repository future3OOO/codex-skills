from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .git_scope import git_read
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


def _reds(path: str) -> list | str | None:
    """The recorded failing runs, None when not supplied, or the reason they could not be read."""
    text, error = read_optional_input(path)
    try:
        reds = json.loads(text)["reds"] if path and not error else None
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return None if not path else error or (reds if isinstance(reds, list) and all(isinstance(red, dict) for red in reds) else "not a list of runs")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the global production code quality gate.")
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--repo", default=os.getcwd())
    check_parser.add_argument("--base-ref", default="")
    check_parser.add_argument("--json", action="store_true")
    check_parser.add_argument("--fail-on-warnings", action="store_true")
    check_parser.add_argument("--gitnexus-context-json", default="")
    check_parser.add_argument("--bloat-review", action="store_true", help="judge every changed unit with TypeSafe Jev (sends that code to TypeSafe)")
    check_parser.add_argument("--bloat-paths", nargs="*", default=[], help="also judge every function in these files or folders, changed or not")
    check_parser.add_argument("--bloat-touched", action="store_true", help="also judge every function in each changed file")
    check_parser.add_argument("--bloat-budget", type=int, default=400_000, help="send nothing when the uncached requests are estimated over this many input tokens")
    check_parser.add_argument("--tdd-evidence-json", default="", help="the pass's recorded failing runs, {reds:[{command,site}]}")
    check_parser.add_argument(
        "--staged-only",
        action="store_true",
        help="evaluate the exact Git index tree against --base-ref; ignore worktree-only content",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    top, failure = git_read(repo, ["rev-parse", "--show-toplevel"])
    if failure:
        print(f"ERROR: not a git repository: {repo}", file=sys.stderr)
        return 1
    root = Path(top.strip()).resolve()
    gitnexus_context_json, graph_error = read_optional_input(args.gitnexus_context_json)
    gitnexus_context_json = "{" if graph_error else gitnexus_context_json  # unreadable: the gate names its graph gap
    if args.staged_only and not args.base_ref:
        parser.error("--staged-only requires --base-ref")
    result = check(
        root,
        args.base_ref or None,
        args.fail_on_warnings,
        gitnexus_context_json,
        args.staged_only,
        (_reds(args.tdd_evidence_json), tuple(args.bloat_paths), args.bloat_touched, args.bloat_budget) if args.bloat_review else None,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_text(result))
        print("")
        print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2
