from __future__ import annotations

import ast
import io
import re
import tokenize

from .findings import SymbolDef


_SYMBOL_PATTERNS = {
    "python": [
        ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(")),
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w]*)\s*(?:\(|:)")),
    ],
    "javascript": [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
    ],
    "go": [("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\("))],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)\s*\(")),
        ("type", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][\w]*)\b")),
    ],
    "shell": [("function", re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\s*\)"))],
    "php": [
        ("function", re.compile(r"^\s*(?:public|private|protected|static|\s)*function\s+([A-Za-z_][\w]*)\s*\(")),
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w]*)\b")),
    ],
    "ruby": [
        ("function", re.compile(r"^\s*def\s+([A-Za-z_][\w!?=]*)\b")),
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w:]*)\b")),
    ],
}


def extract_symbols(path: str, text: str, language: str) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    lines = text.splitlines()
    try: python_ends = {node.lineno: node.end_lineno for node in ast.walk(ast.parse(text)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))} if language == "python" else {}
    except (SyntaxError, UnicodeError, ValueError): python_ends = {}
    for line_no, line in enumerate(lines, 1):
        for kind, pattern in _SYMBOL_PATTERNS.get(language, []):
            match = pattern.search(line)
            if match:
                symbols.append(SymbolDef(
                    match.group(1), path, line_no, kind, language,
                    _definition_content(lines, line_no, language, python_ends.get(line_no)),
                ))
                break
    return symbols


def _definition_content(lines: list[str], line_no: int, language: str, known_end: int | None) -> str:
    start, end = line_no - 1, known_end or len(lines)
    if known_end is None and language == "ruby":
        depth = 1
        for index in range(line_no, len(lines)):
            token = lines[index].strip()
            depth += bool(re.match(r"(?:def|class|module|if|unless|case|while|until|for|begin)\b", token) or re.search(r"\bdo\b", token)) - bool(re.match(r"end\b", token))
            if not depth: end = index + 1; break
    elif known_end is None and language in {"javascript", "go", "rust", "shell", "php"} and not (language == "javascript" and "=>" in lines[start] and "{" not in lines[start]) and any("{" in line for line in lines[start:]):
        depth = 0
        for index in range(next(index for index in range(start, len(lines)) if "{" in lines[index]), len(lines)):
            depth += lines[index].count("{") - lines[index].count("}")
            if depth <= 0: end = index + 1; break
    elif known_end is None:
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = next((index for index in range(line_no, len(lines)) if lines[index].strip() and len(lines[index]) - len(lines[index].lstrip()) <= indent), end)
    return "\n".join(lines[start:end]).rstrip()


def canonical_lines(text: str, language: str) -> dict[int, str] | None:
    """Line number to canonical content for one captured file, or `None` when
    the language has no real tokenizer to prove what a comment is.

    Exactness is the point, so this removes only what a tokenizer proves is
    removable: whole-line comments and blank lines. Identifiers, literals,
    operators, control flow, indentation, and trailing whitespace all survive,
    and nothing inside a multi-line string token is dropped — a blank line
    there is content, and deleting it would make two different strings
    canonicalize identically. A trailing comment keeps its whole line for the
    same reason: the code beside it is not a comment.
    """
    if language != "python":
        return None
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    # A captured tree is untrusted input: a .py path holding NUL bytes reaches
    # CPython's C tokenizer, which reports that as SystemError rather than a
    # syntax error. Every one of these is "this file was not read", never a
    # blanket except: the caller turns None into a named incomplete scope.
    except (IndentationError, SyntaxError, SystemError, UnicodeError, ValueError, tokenize.TokenError):
        return None
    dropped: set[int] = set()
    protected: set[int] = set()
    for token in tokens:
        if token.type == tokenize.COMMENT and not token.line[: token.start[1]].strip():
            dropped.add(token.start[0])
        # Only string content spans physical lines, and 3.12 tokenizes an
        # f-string as FSTRING_* rather than STRING; the span covers both.
        elif token.end[0] > token.start[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
    return {
        number: line
        for number, line in enumerate(text.splitlines(), 1)
        if number in protected or (line.strip() and number not in dropped)
    }


