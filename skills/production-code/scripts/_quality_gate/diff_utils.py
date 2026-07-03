from __future__ import annotations

import re

from .path_policy import normalize_path


def collect_added_lines(raw_diff: str) -> dict[str, list[tuple[int, str]]]:
    added: dict[str, list[tuple[int, str]]] = {}
    current = ""
    new_line = 0
    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            current = ""
            new_line = 0
            continue
        if line.startswith("+++ b/"):
            current = normalize_path(line[len("+++ b/") :])
            continue
        if line.startswith("@@ "):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            new_line = int(match.group(1)) if match else 0
            continue
        if not current or not new_line:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.setdefault(current, []).append((new_line, line[1:]))
            new_line += 1
        elif line.startswith(" ") and not line.startswith("+++"):
            new_line += 1
    return added
