from __future__ import annotations

import ast
import re

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
        ("function", re.compile(r"^\s*(?:it|test)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]")),  # a callback test, named by its title
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
            # Parsed Python names its real definitions; a "def" inside a string literal is text.
            if match and (not python_ends or line_no in python_ends):
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
