from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .diff_utils import collect_added_lines
from .git_scope import read_file
from .models import Numstat
from .path_policy import is_production_source_path, is_source_path


@dataclass(frozen=True)
class GateContext:
    repo: Path
    scope: dict[str, object]
    changed_files: set[str]
    untracked: set[str]
    raw_diff: str
    base_for_file: str
    numstats: list[Numstat]
    added_lines: dict[str, list[tuple[int, str]]]

    @classmethod
    def from_scope(cls, repo: Path, scope: dict[str, object]) -> "GateContext":
        raw_diff = str(scope["raw_diff"])
        return cls(
            repo=repo,
            scope=scope,
            changed_files=set(scope["changed_files"]),
            untracked=set(scope["untracked"]),
            raw_diff=raw_diff,
            base_for_file=str(scope["base_for_file"]),
            numstats=list(scope["numstats"]),
            added_lines=collect_added_lines(raw_diff),
        )

    def added_lines_with_untracked(self, *, production_only: bool) -> dict[str, list[tuple[int, str]]]:
        out = {path: list(lines) for path, lines in self.added_lines.items()}
        predicate = is_production_source_path if production_only else is_source_path
        for rel_path in sorted(self.untracked):
            if predicate(rel_path) and (text := read_file(self.repo / rel_path)) is not None:
                out.setdefault(rel_path, []).extend((idx, line) for idx, line in enumerate(text.splitlines(), 1))
        return out
