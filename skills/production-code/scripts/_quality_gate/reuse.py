from __future__ import annotations

import re
from pathlib import Path

from .context import GateContext
from .git_scope import git_text, read_file, read_git_file
from .models import ReuseFinding, SymbolDef
from .path_policy import is_production_source_path, language_for_path, normalize_path
from .symbols import RISKY_BLOCK_RULE, REUSE_ACTION_TOKENS, extract_symbols, same_behavior_name, split_name_tokens, subtree_score, token_overlap


MAX_INDEX_FILES = 4000
MAX_INDEX_FILE_BYTES = 500_000
MAX_INDEX_SYMBOLS = 25_000

GENERIC_MATCH_TOKENS = {
    "and", "code", "content", "data", "for", "get", "handle", "has", "is", "load", "parse", "read",
    "request", "response", "result", "results", "signal", "state", "text", "url", "value", "wait", "with",
}


def detect_reuse_issues(
    ctx: GateContext,
    repo_context: dict[str, object],
    gitnexus_boosts: dict[str, int],
) -> tuple[list[ReuseFinding], list[str]]:
    packet_paths = {normalize_path(str(path)) for path in repo_context.get("paths", set()) if str(path)}
    candidates = _new_symbols(ctx) + _risky_added_blocks(ctx)
    if not candidates:
        return [], []
    existing = _existing_symbol_index(ctx, candidates, packet_paths, gitnexus_boosts)
    if not existing:
        return [], []
    findings = _score_reuse_candidates(candidates, existing, ctx.added_lines, _deleted_definition_names(ctx.raw_diff))
    queries = [
        f'gitnexus_context(name="{finding.existing_symbol}") and gitnexus_impact(target="{finding.existing_symbol}", direction="upstream")'
        for finding in findings
        if finding.score < 90
    ]
    return findings[:30], sorted(set(queries))[:10]


def _existing_symbol_index(
    ctx: GateContext,
    candidates: list[SymbolDef],
    packet_paths: set[str],
    gitnexus_boosts: dict[str, int],
) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    indexed = 0
    tracked = [normalize_path(line) for line in git_text(ctx.repo, ["ls-files"]).splitlines() if normalize_path(line)]
    candidate_languages = {item.language for item in candidates}
    candidate_roots = {_top_dir(item.path) for item in candidates}
    gitnexus_paths = {key.rsplit(":", 1)[0] for key in gitnexus_boosts}
    for rel_path in tracked:
        if indexed >= MAX_INDEX_FILES or len(symbols) >= MAX_INDEX_SYMBOLS:
            break
        if rel_path in ctx.untracked or not is_production_source_path(rel_path):
            continue
        if not _should_index_existing(rel_path, candidate_languages, candidate_roots, packet_paths, gitnexus_paths):
            continue
        text = read_git_file(ctx.repo, ctx.base_for_file, rel_path) if rel_path in ctx.changed_files else read_file(ctx.repo / rel_path)
        if text is None or len(text.encode("utf-8", errors="ignore")) > MAX_INDEX_FILE_BYTES:
            continue
        indexed += 1
        for symbol in extract_symbols(rel_path, text, "baseline", 12 if rel_path in packet_paths else 0):
            boost = min(20, symbol.context_boost + gitnexus_boosts.get(f"{rel_path}:{symbol.name}", 0))
            symbols.append(SymbolDef(symbol.name, symbol.path, symbol.line, symbol.kind, symbol.language, symbol.tokens, symbol.source, boost))
            if len(symbols) >= MAX_INDEX_SYMBOLS:
                break
    return symbols


def _new_symbols(ctx: GateContext) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    for rel_path, lines in ctx.added_lines.items():
        if not is_production_source_path(rel_path):
            continue
        for line_no, text in lines:
            for symbol in extract_symbols(rel_path, text, "added"):
                symbols.append(SymbolDef(symbol.name, rel_path, line_no, symbol.kind, symbol.language, symbol.tokens, symbol.source))
    for rel_path in sorted(ctx.untracked):
        if is_production_source_path(rel_path) and (text := read_file(ctx.repo / rel_path)) is not None:
            symbols.extend(extract_symbols(rel_path, text, "untracked"))
    return symbols


def _risky_added_blocks(ctx: GateContext) -> list[SymbolDef]:
    blocks: list[SymbolDef] = []
    for rel_path, lines in ctx.added_lines_with_untracked(production_only=True).items():
        if not is_production_source_path(rel_path):
            continue
        for line_no, text in lines:
            dedupe_shape = bool(re.search(r"\bseen\s*=\s*set\s*\(|\bnot\s+in\s+seen\b", text))
            if not RISKY_BLOCK_RULE.search(text) and not dedupe_shape:
                continue
            tokens = [token for token in split_name_tokens(text) if token in REUSE_ACTION_TOKENS]
            if dedupe_shape:
                tokens.append("dedupe")
            if dedupe_shape or len(set(tokens)) >= 2:
                blocks.append(SymbolDef("+".join(tokens[:3]), rel_path, line_no, "block", language_for_path(rel_path), tuple(tokens[:3]), "added"))
    return blocks


def _deleted_definition_names(raw_diff: str) -> set[str]:
    deleted: set[str] = set()
    current = ""
    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            current = ""
        elif line.startswith("--- a/"):
            current = normalize_path(line[len("--- a/") :])
        elif current and is_production_source_path(current) and line.startswith("-") and not line.startswith("---"):
            deleted.update(symbol.name for symbol in extract_symbols(current, line[1:], "deleted"))
    return deleted


def _score_reuse_candidates(
    candidates: list[SymbolDef],
    existing: list[SymbolDef],
    added_by_file: dict[str, list[tuple[int, str]]],
    moved_or_deleted: set[str],
) -> list[ReuseFinding]:
    findings: list[ReuseFinding] = []
    for new_item in candidates:
        if new_item.name in moved_or_deleted or new_item.name.lower() in moved_or_deleted:
            continue
        best = _best_existing_match(new_item, existing, added_by_file)
        if best is None:
            continue
        score, reason, existing_item = best
        severity = "error" if score >= 70 else "warning" if score >= 45 else ""
        if severity == "warning" and not _warning_is_actionable(new_item, existing_item):
            continue
        if severity:
            findings.append(ReuseFinding(severity, score, new_item.name, new_item.path, new_item.line, existing_item.name, existing_item.path, existing_item.line, reason))
    findings.sort(key=lambda item: (item.severity != "error", -item.score, item.new_file, item.new_line))
    return findings


def _best_existing_match(
    new_item: SymbolDef,
    existing: list[SymbolDef],
    added_by_file: dict[str, list[tuple[int, str]]],
) -> tuple[int, str, SymbolDef] | None:
    best: tuple[int, str, SymbolDef] | None = None
    for existing_item in existing:
        if existing_item.path == new_item.path and existing_item.name == new_item.name:
            continue
        if existing_item.language != new_item.language and new_item.kind != "block":
            continue
        if _symbol_is_called_nearby(existing_item.name, added_by_file.get(new_item.path, []), new_item.line):
            continue
        base_score, reason = same_behavior_name(new_item, existing_item)
        if new_item.kind == "block":
            overlap = token_overlap(new_item.tokens, existing_item.tokens)
            if overlap < 0.5:
                continue
            base_score = 55 + int(overlap * 20)
            reason = f"new loop/helper block overlaps existing behavior tokens: {', '.join(set(new_item.tokens) & set(existing_item.tokens))}"
        if base_score <= 0:
            continue
        if new_item.kind == "block" and not _same_reuse_neighborhood(new_item.path, existing_item.path, existing_item.context_boost):
            continue
        score = min(100, base_score + subtree_score(new_item.path, existing_item.path) + existing_item.context_boost)
        if existing_item.context_boost and base_score >= 52:
            score = min(100, score + 10)
        if best is None or score > best[0]:
            best = (score, reason, existing_item)
    return best


def _symbol_is_called_nearby(symbol: str, lines: list[tuple[int, str]], new_line: int) -> bool:
    pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    return any(max(0, new_line - 8) <= line_no <= new_line + 20 and pattern.search(text) for line_no, text in lines)


def _should_index_existing(
    rel_path: str,
    candidate_languages: set[str],
    candidate_roots: set[str],
    packet_paths: set[str],
    gitnexus_paths: set[str],
) -> bool:
    return (
        language_for_path(rel_path) in candidate_languages
        and (
            _top_dir(rel_path) in candidate_roots
            or rel_path in packet_paths
            or rel_path in gitnexus_paths
        )
    )


def _warning_is_actionable(new_item: SymbolDef, existing_item: SymbolDef) -> bool:
    shared = set(new_item.tokens) & set(existing_item.tokens)
    discriminating = shared - GENERIC_MATCH_TOKENS
    has_shared_action = bool(discriminating & REUSE_ACTION_TOKENS)
    return has_shared_action and len(discriminating) >= 2 and _same_reuse_neighborhood(new_item.path, existing_item.path, existing_item.context_boost)


def _same_reuse_neighborhood(path_a: str, path_b: str, context_boost: int) -> bool:
    return context_boost > 0 or _top_dir(path_a) == _top_dir(path_b)


def _top_dir(path: str) -> str:
    return normalize_path(path).split("/", 1)[0]
