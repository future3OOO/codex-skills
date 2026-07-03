from __future__ import annotations

import re

from .models import SymbolDef
from .path_policy import language_for_path, normalize_path


GENERIC_SYMBOLS = {
    "app", "config", "create", "delete", "get", "handler", "index", "init", "load", "main",
    "post", "put", "render", "run", "save", "setup", "start", "stop", "update",
}

REUSE_ACTION_TOKENS = {
    "build", "dedupe", "deduplicate", "fetch", "filter", "format", "load", "map", "normalize",
    "parse", "read", "resolve", "retry", "sanitize", "sync", "validate", "walk", "write",
}

RISKY_BLOCK_RULE = re.compile(
    r"\b(?:for|while|map|filter|reduce|retry|fetch|read|write|parse|normalize|validate|format|resolve|dedupe|deduplicate)\b",
    re.I,
)

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


def split_name_tokens(name: str) -> tuple[str, ...]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    expanded = re.sub(r"[^A-Za-z0-9]+", " ", expanded)
    tokens = tuple(token.lower() for token in expanded.split() if len(token) > 1)
    return tokens or (name.lower(),)


def extract_symbols(path: str, text: str, source: str, context_boost: int = 0) -> list[SymbolDef]:
    language = language_for_path(path)
    symbols: list[SymbolDef] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for kind, pattern in _SYMBOL_PATTERNS.get(language, []):
            match = pattern.search(line)
            if match:
                name = match.group(1)
                symbols.append(SymbolDef(name, path, line_no, kind, language, split_name_tokens(name), source, context_boost))
                break
    return symbols


def subtree_score(path_a: str, path_b: str) -> int:
    parts_a = normalize_path(path_a).split("/")[:-1]
    parts_b = normalize_path(path_b).split("/")[:-1]
    if not parts_a or not parts_b:
        return 0
    if parts_a == parts_b:
        return 10
    shared = 0
    for left, right in zip(parts_a, parts_b):
        if left != right:
            break
        shared += 1
    return 10 if shared >= 2 else 5 if shared == 1 else 0


def token_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(len(left_set), len(right_set)) if left_set and right_set else 0.0


def same_behavior_name(left: SymbolDef, right: SymbolDef) -> tuple[int, str]:
    if left.name == right.name:
        return (35, "generic same symbol name") if left.name.lower() in GENERIC_SYMBOLS else (100, "same symbol name")
    if left.tokens == right.tokens and left.tokens:
        return (35, "generic matching name tokens") if set(left.tokens) <= GENERIC_SYMBOLS else (90, "same name tokens")
    overlap = token_overlap(left.tokens, right.tokens)
    if overlap < 0.66:
        return 0, ""
    shared = sorted(set(left.tokens) & set(right.tokens))
    if set(shared) & REUSE_ACTION_TOKENS:
        return 52, f"matching behavior tokens: {', '.join(shared)}"
    return 45, f"matching name tokens: {', '.join(shared)}"
