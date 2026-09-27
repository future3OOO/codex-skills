from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .git_scope import git_read, read_git_file, read_tree_blobs
from .findings import Hunk, Numstat, SnapshotEntry
from .path_policy import classify_path, is_data_path, normalize_path


def _graph_symbols(repo: Path, text: str, base: str, tree: str) -> tuple[dict[tuple[str, str], tuple[str, ...]], tuple[str, ...]]:
    """Each graph symbol's related symbols (callers, callees, references), keyed by (file, name), from
    caller-supplied graph evidence; evidence that is unreadable or names another snapshot is a named gap."""
    if not text.strip():
        return {}, ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, (f"graph evidence ignored: {exc}",)
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list) or not payload.get("base") or not payload.get("candidate"):
        return {}, ("graph evidence ignored: it names no snapshot or symbols",)
    declared_base, _ = git_read(repo, ["rev-parse", "--verify", f"{payload['base']}^{{commit}}"])
    declared_tree, _ = git_read(repo, ["rev-parse", "--verify", f"{payload['candidate']}^{{tree}}"])
    if (declared_base.strip(), declared_tree.strip()) != (base, tree):
        return {}, ("graph evidence is stale: it does not name the evaluated snapshot",)
    return {
        (normalize_path(str(item.get("file") or item.get("path"))), str(item.get("name") or item.get("symbol"))):
            tuple(str(ref) for key in ("callers", "calleeOf", "references") if isinstance(item.get(key), list) for ref in item[key])
        for item in symbols if isinstance(item, dict)
    }, ()


def _tree_sources(repo: Path, tree: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """The candidate tree's code files (any language, data and docs excepted); oversized ones are a named gap."""
    listed, failure = git_read(repo, ["ls-tree", "-r", "-l", "-z", tree])
    if failure:
        return {}, (f"source index listing failed: {failure}",)
    wanted, oversized = [], 0
    for record in listed.split("\0"):
        meta, _, rel_path = record.partition("\t")
        fields = meta.split()
        kind = classify_path(rel_path)
        if len(fields) < 4 or fields[1] != "blob" or not (kind.role in BASELINE_ROLES or (
                kind.exclusion_reason == "non-source extension" and kind.role != "docs" and not is_data_path(rel_path))):
            continue
        oversized += int(fields[3]) > MAX_INDEX_FILE_BYTES
        wanted += [rel_path] if int(fields[3]) <= MAX_INDEX_FILE_BYTES else []
    unreadable = sum("\n" in path for path in wanted)
    return read_tree_blobs(repo, tree, wanted), tuple(gap for count, gap in (
        (oversized, f"source index skipped {oversized} file(s) over {MAX_INDEX_FILE_BYTES} bytes"),
        (unreadable, f"source index skipped {unreadable} file(s) whose path contains a newline")) if count)


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_QUOTED_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "\\": "\\"}

# The source index bloat review reads: the roles whose code can own a behaviour, and the largest file read.
MAX_INDEX_FILE_BYTES = 500_000
BASELINE_ROLES = ("production", "test", "test-support")


@dataclass(frozen=True)
class EvaluationSnapshot:
    """The one immutable base-to-candidate evaluation every detector reads.

    Classification, base and candidate text, hunk boundaries, growth, and
    completeness are resolved once here so no detector re-derives them. Every
    byte comes from the captured base commit and candidate tree objects, so a
    worktree that keeps moving cannot produce a mixed snapshot.
    """

    base_identity: str
    base_source: str
    candidate_source: str
    candidate_tree: str
    changed_scope: str
    entries: tuple[SnapshotEntry, ...]
    unattributed: tuple[str, ...]
    capture_gaps: tuple[str, ...]
    # Graph relationships per (file, symbol), and the candidate tree's own source files for bloat review
    # (read only when bloat review asks for them, so the default gate reads no more than before).
    graph_symbols: dict[tuple[str, str], tuple[str, ...]]
    sources: dict[str, str]
    source_gaps: tuple[str, ...]

    @classmethod
    def from_scope(
        cls,
        repo: Path,
        scope: dict[str, object],
        gitnexus_context_json: str = "",
        with_sources: bool = False,
    ) -> "EvaluationSnapshot":
        base = str(scope["base_commit"])
        tree = str(scope["candidate_tree"])
        hunks = _collect_hunks(str(scope["raw_diff"]))
        changed = set(scope["changed_files"])
        renamed = dict(scope["renamed"])
        counts = _merge_numstats(list(scope["numstats"]))
        untracked = set(scope["untracked"])
        capture_gaps = tuple(str(error) for error in scope["errors"])
        entries = tuple(
            _entry(repo, path, renamed.get(path, path), base, tree, hunks, counts, path in untracked)
            for path in sorted(changed)
        )
        sources, source_gaps = _tree_sources(repo, tree) if with_sources else ({}, ())
        graph, graph_gaps = _graph_symbols(repo, gitnexus_context_json, base, tree) if with_sources else ({}, ())
        return cls(
            graph_symbols=graph,
            sources=sources,
            source_gaps=source_gaps + graph_gaps,
            base_identity=base,
            base_source=str(scope["base_source"]),
            candidate_source=str(scope["candidate_source"]),
            candidate_tree=tree,
            changed_scope=str(scope["changed_scope"]),
            entries=entries,
            unattributed=tuple(sorted(set(hunks) - changed)),
            capture_gaps=capture_gaps,
        )

    @property
    def candidate_identity(self) -> str:
        if not self.candidate_tree:
            return self.candidate_source
        kind = "git-tree" if self.candidate_source == "index" else "worktree-snapshot"
        return f"{kind}:{self.candidate_tree}"

    def role_entries(self, *roles: str) -> list[SnapshotEntry]:
        return [entry for entry in self.entries if entry.classification.role in roles]

    def growth(self) -> dict[str, dict[str, int]]:
        buckets = {
            "production": _totals(self.role_entries("production")),
            "test": _totals(self.role_entries("test")),
            "testSupport": _totals(self.role_entries("test-support")),
            "generated": _totals(self.role_entries("generated")),
            "humanAuthored": _totals([entry for entry in self.entries if entry.classification.human_authored]),
        }
        return buckets

    def gap_streams(self) -> dict[str, tuple[str, ...]]:
        """The one completeness source every rule draws from: capture-level
        failures, unattributed diff hunks, and the
        per-entry measurement gaps (all entries, and the source subset the
        hunk-reading rules depend on)."""
        return {
            "capture": self.capture_gaps,
            "attribution": tuple(f"{path}: diff hunks matched no changed file" for path in self.unattributed),
            "measurement": tuple(sorted({gap for entry in self.entries if entry.classification.source for gap in entry.gaps})),
            "measurement_all": tuple(sorted({gap for entry in self.entries for gap in entry.gaps})),
        }


def _entry(
    repo: Path,
    rel_path: str,
    base_path: str,
    base: str,
    tree: str,
    hunks: dict[str, tuple[Hunk, ...]],
    counts: dict[str, Numstat],
    untracked: bool,
) -> SnapshotEntry:
    classification = classify_path(rel_path)
    # Candidate text is read for every entry: a temp artifact is detected by its
    # presence, whatever its suffix. Base text only serves source-role rules and
    # lives at the pre-rename path for a renamed entry, so a pure rename never
    # reads as new content.
    current_text = read_git_file(repo, tree, rel_path)
    base_text = read_git_file(repo, base, base_path) if classification.source else None
    added, deleted, gaps = _counts_for(rel_path, counts.get(rel_path))
    return SnapshotEntry(
        path=rel_path,
        classification=classification,
        base_text=base_text,
        current_text=current_text,
        untracked=untracked,
        added=added,
        deleted=deleted,
        hunks=hunks.get(rel_path, ()),
        gaps=gaps,
    )


def _counts_for(rel_path: str, record: Numstat | None) -> tuple[int, int, tuple[str, ...]]:
    if record is None:
        return 0, 0, ()
    if record.added is None or record.deleted is None:
        # Git reports "-" counts for a file it treats as binary. Inventing a
        # number here would let an unmeasured file report as measured.
        return 0, 0, (f"{rel_path}: Git reported no line counts (binary)",)
    return record.added, record.deleted, ()


def _collect_hunks(raw_diff: str) -> dict[str, tuple[Hunk, ...]]:
    """One hunk-preserving walk of the captured diff, keyed by literal path."""
    collected: dict[str, list[Hunk]] = {}
    base_path = key = ""
    base_line = current_line = start = 0
    added: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    in_hunk = False

    def close() -> None:
        nonlocal in_hunk, added, deleted
        if in_hunk and key:
            collected.setdefault(key, []).append(Hunk(tuple(added), tuple(deleted), start))
        in_hunk, added, deleted = False, [], []

    # Split on Git's actual record delimiter only: splitlines() would also
    # break on vertical tab, form feed, and Unicode separators inside a
    # changed payload line, dropping the remainder without its +/- prefix.
    for line in raw_diff.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line.startswith("diff --git "):
            close()
            base_path = key = ""
            continue
        # File headers only precede the first hunk. Inside a hunk, "--- x" is a
        # deleted line whose own text began with "-- ", not a header.
        if not in_hunk and line.startswith("--- "):
            base_path = _diff_path(line[len("--- ") :])
            continue
        if not in_hunk and line.startswith("+++ "):
            # A deleted file has no "+++ b/" path; its hunks belong to the path
            # that was removed, which is the path the change set records.
            key = _diff_path(line[len("+++ ") :]) or base_path
            continue
        match = _HUNK_HEADER.match(line)
        if match:
            close()
            base_line, current_line = int(match.group(1)), int(match.group(3))
            start = current_line
            in_hunk = True
            continue
        if not in_hunk or not key:
            continue
        if line.startswith("+"):
            added.append((current_line, line[1:]))
            current_line += 1
        elif line.startswith("-"):
            deleted.append((base_line, line[1:]))
            base_line += 1
        elif line.startswith(" "):
            base_line += 1
            current_line += 1
    close()
    return {path: tuple(items) for path, items in collected.items()}


def _diff_path(value: str) -> str:
    # Git terminates a header path holding spaces with one tab; nothing else
    # may be trimmed, or a literal-whitespace filename loses its identity.
    if value.endswith("\t"):
        value = value[:-1]
    if value == "/dev/null":
        return ""
    value = _unquote_git_path(value)
    return value[2:] if value[:2] in {"a/", "b/"} else value


def _unquote_git_path(value: str) -> str:
    """Decode Git's C-style quoted path back to the literal filename.

    Git only quotes in the textual diff header; the -z name and numstat
    transports carry literal names, so decoding here reunites the hunks with
    their entry. A literal name that merely begins with a quote character is
    not quoted output and passes through untouched.
    """
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    inner = value[1:-1]
    out = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            out.extend(char.encode("utf-8", errors="surrogateescape"))
            index += 1
            continue
        index += 1
        if index >= len(inner):
            return value
        escape = inner[index]
        if escape in _QUOTED_ESCAPES:
            out.extend(_QUOTED_ESCAPES[escape].encode("latin-1"))
            index += 1
        elif escape.isdigit():
            octal = inner[index : index + 3]
            out.append(int(octal, 8))
            index += 3
        else:
            return value
    # Same lossless decode as the -z transports, or the header path and the
    # entry path would key on different strings and the hunks would not attach.
    return out.decode("utf-8", errors="surrogateescape")


def _merge_numstats(records: list[Numstat]) -> dict[str, Numstat]:
    merged: dict[str, Numstat] = {}
    for record in records:
        previous = merged.get(record.path)
        merged[record.path] = record if previous is None else Numstat(
            _sum(previous.added, record.added), _sum(previous.deleted, record.deleted), record.path
        )
    return merged


def _sum(left: int | None, right: int | None) -> int | None:
    return None if left is None or right is None else left + right


def _totals(entries: list[SnapshotEntry]) -> dict[str, int]:
    added = sum(entry.added for entry in entries)
    deleted = sum(entry.deleted for entry in entries)
    return {"added": added, "deleted": deleted, "net": added - deleted}
