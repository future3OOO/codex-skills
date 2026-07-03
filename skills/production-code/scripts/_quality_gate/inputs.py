from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .path_policy import normalize_path


def read_optional_input(value: str) -> tuple[str, str | None]:
    if not value:
        return "", None
    if value == "-":
        return sys.stdin.read(), None
    try:
        return Path(value).read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return "", f"could not read optional input {value}: {exc}"


def parse_repo_context_packet(text: str) -> dict[str, object]:
    paths: set[str] = set()
    repo = ""
    for match in re.finditer(r'(?:path|file)=["\']([^"\']+)["\']', text):
        paths.add(normalize_path(match.group(1)))
    for line in text.splitlines():
        for match in re.finditer(r"[\w./-]+\.(?:cjs|cts|go|js|jsx|mjs|mts|php|py|rb|rs|sh|ts|tsx)", line):
            paths.add(normalize_path(match.group(0).strip("`'\"(),:;")))
        if not repo:
            repo_match = re.search(r"<gitnexus_status>.*?<repo>(.*?)</repo>", line)
            if repo_match:
                repo = repo_match.group(1).strip()
    return {"paths": paths, "repo": repo}


def parse_gitnexus_context_json(text: str) -> tuple[dict[str, int], list[str]]:
    if not text.strip():
        return {}, []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"gitnexus context JSON ignored: {exc}"]
    symbols = payload.get("symbols", []) if isinstance(payload, dict) else []
    if not isinstance(symbols, list):
        return {}, []
    boosts: dict[str, int] = {}
    for item in symbols:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "").strip()
        path = normalize_path(str(item.get("file") or item.get("path") or "").strip())
        if not name or not path:
            continue
        boost = (8 if item.get("callers") or item.get("calleeOf") or item.get("references") else 0) + (
            7 if item.get("processes") or item.get("flows") or item.get("workflows") else 0
        )
        if boost:
            boosts[f"{path}:{name}"] = min(15, boost)
    return boosts, []
