"""Parse Codex hook input at one boundary."""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .workflow_state import safe_slug

_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE
)
# Shell writes observed from the Codex Bash tool: > / >> redirects and tee.
# Deliberately narrow — cp/mv/sed -i are not claimed until seen from Codex.
_BASH_WRITE = re.compile(
    r'(?:>>?|tee\s+(?:-\S+\s+)*)\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s;|&]+))'
)


def read_hook_payload() -> dict[str, object]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve(value: str, cwd: str | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path.resolve(strict=False)


def edited_path(payload: dict[str, object]) -> Path | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("file_path") or tool_input.get("notebook_path")
    if isinstance(value, str) and value:
        return Path(value).expanduser().resolve(strict=False)
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else None
    tool_name = payload.get("tool_name")
    if tool_name == "apply_patch":
        match = _PATCH_PATH.search(command)
        return _resolve(match.group(1).strip(), cwd) if match else None
    if tool_name == "Bash":
        match = _BASH_WRITE.search(command)
        target = next((g for g in match.groups() if g), None) if match else None
        return _resolve(target, cwd) if target else None
    return None


# Read verbs and their argument rules are the ones the CX2 corpus used (350 shell
# commands, 239 read events): sed 163, rg 38, inline python 23, cat 14, wc 13, jq 12,
# nl 9, tail 6, awk 3, `<` 2, head 1. These are historical extraction counts,
# not recall measurements for this stricter, read-only matcher.
_READ_VERBS = {"cat", "head", "tail", "nl", "wc", "jq"}
_RG_VALUE_OPTIONS = {"-e", "--regexp", "-g", "--glob", "--iglob", "-t", "--type", "-m", "--max-count",
                     "-A", "-B", "-C", "--max-columns", "-f", "--file"}
# Group 3 is whatever follows the marker word. Dropping it would hide a redirect
# written there, and with it the reason to decline the whole invocation.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?\n)?\2\s*(?=\n|$)", re.S)
_PATH_SUFFIXES = (".py", ".md", ".json", ".jsonl", ".txt", ".toml", ".cfg", ".yml", ".yaml", ".rst",
                  ".sh", ".js", ".ts", ".ini", ".html", ".db", ".sql", ".csv", ".lock", ".sqlite3")


def _path_like(token: str) -> bool:
    if not token or "$" in token or token.startswith(("-", "http")) or ("=" in token and "/" not in token):
        return False
    return (token.startswith(("/", "./", "../", "~")) or token.endswith(_PATH_SUFFIXES)
            or ("/" in token and not re.search(r"[|&;<>*]", token)))


_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_STDERR_NULL = re.compile(r"'[^']*'|\"[^\"]*\"|(?<!\S)2>>?\s*/dev/null(?=[\s;|]|$)")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|\\.|\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)")


def _segments(command: str) -> list[list[str]]:
    """Only unconditional foreground segments; malformed shell text is not evidence."""
    if "`" in command or "$(" in command or "\\\n" in command:
        return []
    text = _HEREDOC.sub(lambda match: match[3] or "", command)
    # Keep descriptor adjacency: `2>/dev/null` differs from `2 >/dev/null`.
    text = _STDERR_NULL.sub(lambda match: match[0] if match[0].startswith(("'", '\"')) else "", text)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";|&<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"||", "&&", "&", "|&", ";;", ";&", ";;&"}:
            return []
        if token == "|" or (token and set(token) <= {";", "\n"}):
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _substitute(command: str, cwd: str | None = None) -> str:
    """Expand initial literal assignments only. Reassignment and late assignment
    decline capture rather than attributing a later read to an earlier value."""
    # Most commands need no expansion and should pay for only one shell parse.
    if not _VARIABLE.search(command):
        return command
    # A quoted heredoc is interpreted by Python, not expanded by the shell.
    if _HEREDOC.search(command):
        return command
    values = {"HOME": str(Path.home()), "PWD": cwd or os.getcwd()}
    assigned: set[str] = set()
    started = False
    for tokens in _segments(command):
        if len(tokens) == 1 and _ASSIGNMENT.match(tokens[0]):
            name, value = tokens[0].split("=", 1)
            if started or name in assigned or not value or re.search(r"[$`\\*?\[\]]", value):
                return ""
            values[name] = value
            assigned.add(name)
        else:
            started = True

    def expand(match: re.Match[str]) -> str:
        token = match.group()
        if token.startswith(("'", "\\")):
            return token
        if token.startswith('"'):
            if "$" not in token:
                return token
            if "\\" in token:
                return '"$UNRESOLVED"'
            value = _VARIABLE.sub(lambda item: values.get(item[1] or item[2], "$UNRESOLVED"), token[1:-1])
            return shlex.quote(value)
        name = _VARIABLE.fullmatch(token)
        value = values.get(name[1] or name[2], "$UNRESOLVED") if name else token
        # Unquoted expansion would split or glob these values; do not invent a path.
        return "$UNRESOLVED" if re.search(r"[\s*?\[\]]", value) else shlex.quote(value)

    return _QUOTED.sub(expand, command)


def _python_reads(command: str) -> list[str] | None:
    """The supported inline Python shape prints literal reads. A regex occurrence
    in a conditional, function, writer or sqlite connect is not an inspection."""
    match = re.fullmatch(r"\s*python3?\s+-\s+<<(['\"])(\w+)\1\s*\n(.*?)\n\2\s*", command, re.S)
    if match is None:
        return None
    try:
        tree = ast.parse(match[3])
    except SyntaxError:
        return None
    paths: list[str] = []
    path_imported = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib" and node.level == 0:
            if all(item.name == "Path" and item.asname is None for item in node.names):
                path_imported = True
                continue
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            return None
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "print" and not call.keywords and len(call.args) == 1):
            return None
        read = call.args[0]
        if not (isinstance(read, ast.Call) and isinstance(read.func, ast.Attribute) and not read.args and not read.keywords):
            return None
        owner = read.func.value
        if not (isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) and len(owner.args) == 1 and not owner.keywords
                and isinstance(owner.args[0], ast.Constant) and isinstance(owner.args[0].value, str)):
            return None
        if owner.func.id == "Path" and not path_imported:
            return None
        if (owner.func.id, read.func.attr) not in {("open", "read"), ("Path", "read_text"), ("Path", "read_bytes")}:
            return None
        paths.append(owner.args[0].value)
    return paths


def read_candidates(command: str, *, cwd: str | None = None) -> list[str]:
    """Candidate reads in supported read-only commands, never arbitrary shell text.

    The PostToolUse digest is taken after the whole invocation. Omit an entire
    mixed or opaque invocation so a writer cannot lend an unread replacement's
    digest to an earlier reader. Stderr suppression is not a source-file write.
    """
    command = _substitute(command, cwd)
    segments = _segments(command)
    python_reads = _python_reads(command)
    found: list[str] = []
    for tokens in segments:
        while tokens and (_ASSIGNMENT.match(tokens[0]) or tokens[0] in {"sudo", "timeout", "nice"}):
            tokens = tokens[2:] if tokens[0] == "timeout" else tokens[1:]
        args: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "<":
                if index + 1 < len(tokens) and _path_like(tokens[index + 1]):
                    found.append(tokens[index + 1])
                index += 2
            elif token and set(token) <= set("|&<>"):
                return []
            else:
                args.append(token)
                index += 1
        if not args:
            continue
        verb, rest = os.path.basename(args[0]), args[1:]
        if verb in {"python", "python3"}:
            if python_reads is None:
                return []
        elif verb not in _READ_VERBS | {"sed", "rg", "awk"}:
            return []
        if verb in _READ_VERBS:
            found.extend(arg for arg in rest if _path_like(arg))
        elif verb == "sed":
            positional = [arg for arg in rest if not arg.startswith("-")]
            if (any(arg.startswith(("-i", "--in-place")) or arg in {"-e", "--expression", "-f", "--file"} for arg in rest)
                    or not positional or not re.fullmatch(r"[0-9]+(?:,(?:[0-9]+|\$))?p", positional[0])):
                return []
            found.extend(arg for arg in positional[1:] if _path_like(arg))
        elif verb == "rg":
            positional: list[str] = []
            skip = False
            for arg in rest:
                if skip:
                    skip = False
                elif arg in _RG_VALUE_OPTIONS:
                    skip = True
                elif not arg.startswith("-"):
                    positional.append(arg)
            pattern_inline = not any(arg in {"-e", "--regexp"} for arg in rest)
            found.extend(arg for arg in (positional[1:] if pattern_inline else positional) if _path_like(arg))
        elif verb == "awk":
            positional = [arg for arg in rest if not arg.startswith("-")]
            if not positional or not re.fullmatch(r"\{\s*print(?:\s+\$[0-9]+)?\s*\}", positional[0]):
                return []
            found.extend(arg for arg in positional[1:] if _path_like(arg))
    if segments and python_reads is not None:
        found.extend(path for path in python_reads if _path_like(path))
    return list(dict.fromkeys(found))


def read_paths(payload: dict[str, object]) -> list[Path]:
    """Existing regular files a Bash payload reads, resolved against its cwd."""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if payload.get("tool_name") != "Bash" or not isinstance(command, str) or not command:
        return []
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else None
    paths: list[Path] = []
    for candidate in read_candidates(command, cwd=cwd):
        try:
            resolved = _resolve(candidate, cwd)
            if resolved.is_file() and resolved not in paths:
                paths.append(resolved)
        except (OSError, RuntimeError):
            continue
    return paths


def working_directory(payload: dict[str, object]) -> str:
    """Working directory across hook transports.

    Codex payloads carry `cwd`; `working_directory` is accepted for parity with
    other harnesses. Falls back to the hook's own process cwd.
    """
    for key in ("cwd", "working_directory"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    env = os.environ.get("CODEX_PROJECT_DIR")
    return env if env else os.getcwd()


def is_explorer_continuation(payload: dict[str, object]) -> bool:
    """Resolve the target in Codex's existing thread metadata, without recording it."""
    inputs = payload.get("tool_input")
    target = (inputs.get("target") or inputs.get("id")) if isinstance(inputs, dict) else None
    session = payload.get("session_id")
    if not isinstance(target, str) or not isinstance(session, str):
        return False
    database = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "state_5.sqlite"
    if not database.is_file():
        return False
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)) as connection:
        caller = connection.execute("SELECT agent_path FROM threads WHERE id = ?", (session,)).fetchone()
        if caller is None:
            return False
        path = target if target.startswith("/") else f"{caller[0] or '/root'}/{target}"
        roles = connection.execute(
            "SELECT agent_role FROM threads WHERE (id = ? OR agent_path = ?) AND "
            "CASE WHEN json_valid(source) THEN "
            "json_extract(source, '$.subagent.thread_spawn.parent_thread_id') END = ?",
            (target, path, session),
        ).fetchall()
    return roles == [("explorer",)]


def session_key(payload: dict[str, object]) -> str | None:
    """The session identifier as one state path segment, or None when absent.

    Derived here so the hook that records an association and the hook that reads
    it cannot drift, and so a hostile `session_id` is bounded to a single safe
    segment before it ever reaches the filesystem.

    Absence is returned rather than defaulted. A session key names a per-session
    set, so defaulting a missing id to any shared literal would file every
    anonymous payload under one identity and let one repository's pass reach
    another's Stop. Callers that want a display name for repository-scoped
    storage supply their own fallback.
    """
    value = payload.get("session_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return safe_slug(value)[:40]
