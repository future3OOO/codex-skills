from __future__ import annotations

from .findings import SymbolDef


def extract_symbols(path: str, text: str, language: str, graph: tuple[dict[str, object], ...]) -> list[SymbolDef]:
    """GitNexus owns syntax and ranges. Slice exact source without re-parsing it."""
    lines = text.splitlines()
    return [SymbolDef(str(node["name"]), path, int(node["startLine"]) + 1,
                      str(node["id"]).partition(":")[0], language,
                      "\n".join(lines[int(node["startLine"]):int(node["endLine"]) + 1]))
            for node in graph if 0 <= int(node["startLine"]) <= int(node["endLine"]) < len(lines)]


def canonical_lines(text: str, language: str) -> dict[int, str]:
    """Exact lines in every language; literal contents and whitespace stay significant."""
    return dict(enumerate(text.splitlines(), 1))
