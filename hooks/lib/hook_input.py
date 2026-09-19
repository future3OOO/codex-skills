"""Parse Codex hook input at one boundary."""
from __future__ import annotations

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
# Deliberately narrow: `read_candidates` declines any invocation that writes at all,
# so it needs no write set of its own.
_BASH_WRITE = re.compile(
    r'(?:>>?|>\||tee\s+(?:-\S+\s+)*)\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s;|&]+))'
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
                     "-A", "-B", "-C", "--max-columns", "-f", "--file", "--pre-glob"}
# Only option forms whose operand boundaries are known are accepted. Auxiliary
# --rawfile/--slurpfile values are not claims that their contents reached stdout.
_JQ_VALUE_OPTIONS = {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2, "--indent": 1}
_JQ_READ_ONLY = set("cjrRaSMCseb")
_JQ_READ_ONLY_LONG = {"--compact-output", "--join-output", "--raw-output", "--raw-output0",
                      "--raw-input", "--ascii-output", "--sort-keys", "--monochrome-output",
                      "--color-output", "--slurp", "--exit-status", "--binary", "--tab",
                      "--seq", "--unbuffered", "--stream", "--stream-errors"}
_CAT_READ_ONLY = set("AbeEnstTuv")
_CAT_READ_ONLY_LONG = {"--show-all", "--number-nonblank", "--show-ends", "--number",
                       "--squeeze-blank", "--show-tabs", "--show-nonprinting"}
# sed options that only read. Every other letter, in any bundle, declines the command:
# `i` edits in place, `e` and `f` supply the script from elsewhere.
_SED_READ_ONLY = set("nsuzEr")
_SED_READ_ONLY_LONG = {"--quiet", "--silent", "--separate", "--unbuffered", "--null-data",
                       "--regexp-extended", "--posix", "--debug", "--sandbox"}
_PATH_SUFFIXES = (".py", ".md", ".json", ".jsonl", ".txt", ".toml", ".cfg", ".yml", ".yaml", ".rst",
                  ".sh", ".js", ".ts", ".ini", ".html", ".db", ".sql", ".csv", ".lock", ".sqlite3")


def _path_like(token: str) -> bool:
    if not token or "$" in token or token.startswith(("-", "http")) or ("=" in token and "/" not in token):
        return False
    return (token.startswith(("/", "./", "../", "~")) or token.endswith(_PATH_SUFFIXES)
            or ("/" in token and not re.search(r"[|&;<>*]", token)))


_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_STDERR_NULL = re.compile(r"'[^']*'|\"[^\"]*\"|(?<!\S)2>>?\s*/dev/null(?=[\s;|]|$)")


def _tokens(command: str) -> list[str]:
    """Shell words of an unconditional foreground command, or nothing when the text is
    conditional, backgrounded, substituted or malformed. Such text is not evidence."""
    if "`" in command or "$(" in command or "\\\n" in command or "<<" in command:
        return []
    # Do not erase heredocs: they can replace an earlier stdin redirection.
    text = command
    # Keep descriptor adjacency: `2>/dev/null` differs from `2 >/dev/null`.
    text = _STDERR_NULL.sub(lambda match: match[0] if match[0].startswith(("'", '\"')) else "", text)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";|&<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    return [] if any(token in {"||", "&&", "&", "|&", ";;", ";&", ";;&"} for token in tokens) else tokens


def _split(command: str, separators: set[str]) -> list[list[str]]:
    parts: list[list[str]] = [[]]
    for token in _tokens(command):
        if token in separators or (token and set(token) <= {";", "\n"}):
            parts.append([])
        else:
            parts[-1].append(token)
    return [part for part in parts if part]


def _segments(command: str) -> list[list[str]]:
    """One command each: pipeline members are separate commands."""
    return _split(command, {"|"})


def read_candidates(command: str) -> list[str]:
    """Candidate reads in supported read-only commands, never arbitrary shell text.

    These are requested paths, not proof of execution, delivery or coverage.
    Mixed or opaque invocations are omitted. Stderr suppression is not a
    source-file write.
    A token carrying `$` is never a path, so nothing is resolved on the shell's
    behalf: an unexpanded reference simply declines.
    """
    found: list[str] = []
    for tokens in _segments(command):
        while tokens and (_ASSIGNMENT.match(tokens[0]) or tokens[0] in {"sudo", "timeout", "nice"}):
            tokens = tokens[2:] if tokens[0] == "timeout" else tokens[1:]
        args: list[str] = []
        stdin: str | None = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "<":
                # Multiple redirects and descriptor-specific input are outside the
                # supported form. Opening a descriptor does not mean it was read.
                if stdin is not None or index + 1 == len(tokens) or (index and tokens[index - 1].isdigit()):
                    return []
                stdin = tokens[index + 1]
                index += 2
            elif token and set(token) <= set("|&<>"):
                return []
            else:
                args.append(token)
                index += 1
        if not args:
            continue
        verb, rest = os.path.basename(args[0]), args[1:]
        if verb not in _READ_VERBS | {"sed", "rg", "awk"}:
            return []
        if stdin is not None and verb != "cat":
            return []
        if verb == "jq":
            positional: list[str] = []
            index = 0
            while index < len(rest):
                arg = rest[index]
                if arg == "--":
                    positional.extend(rest[index + 1:])
                    break
                if arg in _JQ_VALUE_OPTIONS:
                    # --arg name value consumes TWO words, even when a value looks
                    # like an option or filename. --indent consumes only one.
                    index += _JQ_VALUE_OPTIONS[arg] + 1
                    if index > len(rest):
                        return []
                    continue
                if arg.startswith("--"):
                    if arg not in _JQ_READ_ONLY_LONG:
                        return []
                elif arg.startswith("-") and arg != "-":
                    # Null-input (-n) also declines when bundled, e.g. -cn.
                    if set(arg[1:]) - _JQ_READ_ONLY:
                        return []
                else:
                    positional.append(arg)
                index += 1
            found.extend(arg for arg in positional[1:] if _path_like(arg))
        elif verb == "cat":
            operands: list[str] = []
            for index, arg in enumerate(rest):
                if arg == "--":
                    operands.extend(rest[index + 1:])
                    break
                if arg.startswith("--"):
                    if arg not in _CAT_READ_ONLY_LONG:
                        return []
                elif arg.startswith("-") and arg != "-":
                    if set(arg[1:]) - _CAT_READ_ONLY:
                        return []
                else:
                    operands.append(arg)
            if stdin is not None and (not operands or "-" in operands) and _path_like(stdin):
                found.append(stdin)
            found.extend(arg for arg in operands if _path_like(arg))
        elif verb in _READ_VERBS:
            found.extend(arg for arg in rest if _path_like(arg))
        elif verb == "sed":
            positional = [arg for arg in rest if not arg.startswith("-")]
            # Only the read-only short options, letter by letter: `-ni` edits in place
            # just as `-i` does, and `-e`/`-f` mean the first positional is not the script.
            if (any(set(arg[1:]) - _SED_READ_ONLY for arg in rest if arg.startswith("-") and not arg.startswith("--"))
                    or any(arg.startswith("--") and arg not in _SED_READ_ONLY_LONG for arg in rest)
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
                elif arg == "--pre" or arg.startswith("--pre="):
                    return []
                elif not arg.startswith("-"):
                    positional.append(arg)
            pattern_inline = not any(arg in {"-e", "--regexp"} for arg in rest)
            found.extend(arg for arg in (positional[1:] if pattern_inline else positional) if _path_like(arg))
        elif verb == "awk":
            positional = [arg for arg in rest if not arg.startswith("-")]
            if not positional or not re.fullmatch(r"\{\s*print(?:\s+\$[0-9]+)?\s*\}", positional[0]):
                return []
            found.extend(arg for arg in positional[1:] if _path_like(arg))
    return list(dict.fromkeys(found))


def read_paths(payload: dict[str, object]) -> list[Path]:
    """Existing regular files a Bash payload reads, resolved against its cwd."""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if payload.get("tool_name") != "Bash" or not isinstance(command, str) or not command:
        return []
    cwd = working_directory(payload)
    paths: list[Path] = []
    for candidate in read_candidates(command):
        try:
            resolved = _resolve(candidate, cwd)
            if resolved.is_file() and resolved not in paths:
                paths.append(resolved)
        except (OSError, RuntimeError, ValueError):
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
