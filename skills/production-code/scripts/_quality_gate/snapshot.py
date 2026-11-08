from __future__ import annotations

import json
import re
import subprocess
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hooks.lib.state_store import read_blobs

from .git_scope import git_read, read_git_file
from .findings import BaselineFile, Hunk, Numstat, SnapshotEntry
from .path_policy import classify_path


# The fixed disposition carrier: a git-dir path is readable during capture
# and never part of any candidate tree, so records there are out-of-tree.
DISPOSITIONS_CARRIER = "qg54-dispositions.json"


def _disposition_records(repo: Path) -> tuple[dict[str, object], ...]:
    """Disposition records from the fixed out-of-tree carrier, decoded and
    shape-checked, with their commits resolved during capture. Trust,
    finding state, and severity stay with the owner rules."""
    located, failure = git_read(repo, ["rev-parse", "--git-path", DISPOSITIONS_CARRIER])
    if failure:
        return ()
    carrier = Path(located.strip())
    carrier = carrier if carrier.is_absolute() else repo / carrier
    try:
        text = carrier.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeDecodeError) as exc:
        return ({"invalidDocument": f"dispositions carrier ignored: {exc}"},)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ({"invalidDocument": f"dispositions carrier ignored: {exc}"},)
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return ({"invalidDocument": "dispositions carrier has no records array"},)
    resolved = []
    for record in records:
        if not isinstance(record, dict):
            resolved.append({"invalidDocument": "record is not an object"})
            continue
        base, _ = git_read(repo, ["rev-parse", "--verify", f"{record.get('base', '')}^{{commit}}"])
        tree, _ = git_read(repo, ["rev-parse", "--verify", f"{record.get('candidate', '')}^{{tree}}"])
        resolved.append({**record, "resolvedBase": base.strip(), "resolvedCandidateTree": tree.strip()})
    return tuple(resolved)


def _graph_facts(payload: object, base: str, tree: str, paths: list[str]) -> tuple[str, tuple[dict, ...], dict]:
    if not isinstance(payload, dict) or (payload.get("base"), payload.get("candidate")) != (base, tree) or not payload.get("repo"):
        return "no current graph identity; structural checks are incomplete", (), {}
    kinds = ["File", "Function", "Method", "Class", "Interface", "Struct", "Enum", "Property"]
    # Encode literal word starts uniformly: upstream's read-only scanner also
    # scans string contents (e.g. a file named copy.py).
    literals = ["\"" + "".join("\\u%04x" % ord(char) if char.isascii() and (char.isalpha() or char == "_")
                         and (index == 0 or not (path[index - 1].isalnum() or path[index - 1] == "_"))
                         else json.dumps(char)[1:-1] for index, char in enumerate(path)) + "\"" for path in paths]
    query = ("MATCH (n) WHERE n.filePath IN " + "[" + ",".join(literals) + "]" + " AND (" +
             " OR ".join("n.id STARTS WITH '" + kind + ":'" for kind in kinds) + ") "
             "OPTIONAL MATCH (n)-[r:CodeRelation]->(m) WHERE r.type = 'CALLS' "
             "WITH n, collect(m.id) AS calls RETURN collect({id:n.id, name:n.name, file:n.filePath, "
             "startLine:n.startLine, endLine:n.endLine, calls:calls}) AS symbols")
    started = time.perf_counter()
    measured = {"files": len(paths), "queryCount": 1}
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
            result = subprocess.run(["gitnexus", "cypher", "-r", str(payload["repo"]), query],
                                    stdout=output, stderr=subprocess.PIPE, text=True, timeout=30)
            output.seek(0)
            text = output.read()
        measured["bytes"] = len(text.encode())
        if result.returncode:
            raise ValueError((result.stderr or text)[-300:])
        data = json.loads(text)
        if isinstance(data, dict) and "complete_result" in data:
            path = Path(data["complete_result"])
            try:
                measured["bytes"] = path.stat().st_size
                data = json.loads(path.read_text())
            finally:
                path.unlink(missing_ok=True)
        if not isinstance(data, dict) or data.get("row_count") != 1:
            raise ValueError("query returned no symbol collection")
        nodes = json.loads(data["markdown"].splitlines()[2][1:-1].strip())
        if not isinstance(nodes, list):
            raise ValueError("query returned no symbol list")
        return "", tuple({**node, "calls": node.get("calls") or []} for node in nodes), measured
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        return "graph query failed; structural checks are incomplete: " + str(exc), (), measured
    finally:
        measured["seconds"] = time.perf_counter() - started


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_QUOTED_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "\\": "\\"}

# Baseline capture bounds: how many owner files may be read PER ROLE, so one
# role cannot spend another's budget, and how large one may be. They live with
# the capture because the cap must prevent the read.
MAX_INDEX_FILES = 4000
MAX_INDEX_FILE_BYTES = 500_000

# Roles whose implementation the exact rules read and whose base-tree files are
# captured as owner evidence: an exact copy of an existing helper is the same
# defect wherever it lives; generated and vendored code is never an owner.
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
    baseline: tuple[BaselineFile, ...]
    baseline_gaps: tuple[str, ...]
    # Owner-discovery gaps outside the production role: the reuse advisory
    # scores production owners only, so a test owner it never reads cannot
    # make its verdict unknown.
    baseline_role_gaps: tuple[str, ...]
    # Same-role, same-language owners the candidate-relevance bound never read.
    baseline_scope_gaps: tuple[str, ...]
    # Where each renamed base path lives in the candidate. A renamed path has
    # no entry of its own, so without this its content could neither be found
    # nor be proven gone.
    renamed_to: dict[str, str]
    unattributed: tuple[str, ...]
    capture_gaps: tuple[str, ...]
    # Caller-supplied evidence, parsed once and frozen here with everything
    # else a detector reads.
    gitnexus_warnings: tuple[str, ...]
    # The two external data Interfaces #77 versions, captured and frozen: the
    # graph evidence's snapshot binding (empty gap when it names this exact
    # snapshot and covers real symbols) and the disposition records read from
    # the fixed out-of-tree carrier with their commits resolved.
    graph_gap: str
    graph_files: frozenset[str]
    disposition_records: tuple[dict[str, object], ...]
    graph_symbols: tuple[dict[str, object], ...] = ()
    graph_query: dict[str, object] | None = None

    def symbols(self, path: str) -> tuple[dict[str, object], ...]:
        return tuple(value for value in self.graph_symbols if value.get("file") == path
                     and type(value.get("startLine")) is int and type(value.get("endLine")) is int)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_path", {entry.path: entry for entry in self.entries})

    @classmethod
    def from_scope(
        cls,
        repo: Path,
        scope: dict[str, object],
        repo_context_packet: str = "",
        gitnexus_context_json: str = "",
    ) -> "EvaluationSnapshot":
        base = str(scope["base_commit"])
        tree = str(scope["candidate_tree"])
        hunks = _collect_hunks(str(scope["raw_diff"]))
        changed = set(scope["changed_files"])
        renamed = dict(scope["renamed"])
        counts = _merge_numstats(list(scope["numstats"]))
        capture_gaps = tuple(str(error) for error in scope["errors"])
        warnings = []
        try:
            graph_data = json.loads(gitnexus_context_json) if gitnexus_context_json.strip() else {}
        except json.JSONDecodeError as exc:
            graph_data = {}
            warnings.append("gitnexus context JSON ignored: " + str(exc))
        entries = tuple(
            _entry(repo, path, renamed.get(path, path), base, tree, hunks, counts)
            for path in sorted(changed)
        )
        baseline, baseline_gaps, baseline_role_gaps, baseline_scope_gaps = _baseline_index(repo, base, entries)
        paths = sorted({entry.path for entry in entries if entry.current_text is not None} |
                       {file.path for file in baseline if file.text is not None})
        graph_gap, graph_symbols, graph_query = _graph_facts(graph_data, base, tree, paths)
        graph_files = frozenset(node["file"] for node in graph_symbols)
        return cls(
            gitnexus_warnings=tuple(warnings),
            graph_gap=graph_gap,
            graph_files=graph_files,
            graph_symbols=graph_symbols,
            graph_query=graph_query,
            disposition_records=_disposition_records(repo),
            base_identity=base,
            base_source=str(scope["base_source"]),
            candidate_source=str(scope["candidate_source"]),
            candidate_tree=tree,
            changed_scope=str(scope["changed_scope"]),
            entries=entries,
            baseline=baseline,
            baseline_gaps=baseline_gaps,
            baseline_role_gaps=baseline_role_gaps,
            baseline_scope_gaps=baseline_scope_gaps,
            renamed_to={old: new for new, old in renamed.items()},
            unattributed=tuple(sorted(set(hunks) - changed)),
            capture_gaps=capture_gaps,
        )

    @property
    def candidate_identity(self) -> str:
        if not self.candidate_tree:
            return self.candidate_source
        kind = "git-tree" if self.candidate_source == "index" else "worktree-snapshot"
        return f"{kind}:{self.candidate_tree}"

    def entry(self, rel_path: str) -> SnapshotEntry | None:
        return self._by_path.get(rel_path)

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
        failures, baseline discovery gaps, unattributed diff hunks, and the
        per-entry measurement gaps (all entries, and the source subset the
        hunk-reading rules depend on)."""
        return {
            "capture": self.capture_gaps,
            "baseline": self.baseline_gaps,
            "baseline_roles": self.baseline_role_gaps,
            "baseline_scope": self.baseline_scope_gaps,
            "attribution": tuple(f"{path}: diff hunks matched no changed file" for path in self.unattributed),
            "measurement": tuple(sorted({gap for entry in self.entries if entry.classification.source for gap in entry.gaps})),
            "measurement_production": tuple(sorted({gap for entry in self.role_entries("production") for gap in entry.gaps})),
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
        added=added,
        deleted=deleted,
        hunks=hunks.get(rel_path, ()),
        gaps=gaps,
    )


def _baseline_index(
    repo: Path, base: str, entries: tuple[SnapshotEntry, ...],
) -> tuple[tuple[BaselineFile, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Capture existing owners across every directory and language of each changed role.

    Existing per-role file and per-file size caps apply before blob reads.
    Unread objects remain gaps; the candidate cannot select its own competitors.
    """
    if not base:
        return (), (), (), ()
    listed, failure = git_read(repo, ["ls-tree", "-r", "-l", "-z", base])
    if failure:
        return (), (f"reuse baseline listing failed: {failure}",), (), ()
    roles = {entry.classification.role for entry in entries} & set(BASELINE_ROLES)
    files, selected, gaps, reads = [], {}, [], {}
    for record in listed.split("\0"):
        meta, sep, path = record.partition("\t")
        fields = meta.split()
        if not sep or not path or len(fields) < 4 or fields[1] != "blob":
            continue
        classification = classify_path(path)
        if not classification.source:
            continue
        role = classification.role
        if role in roles:
            if int(fields[3]) > MAX_INDEX_FILE_BYTES:
                gaps.append((role, f"{path}: reuse baseline exceeds {MAX_INDEX_FILE_BYTES} bytes"))
            elif reads.get(role, 0) >= MAX_INDEX_FILES:
                gaps.append((role, f"reuse baseline discovery stopped at {MAX_INDEX_FILES} files"))
            else:
                reads[role] = reads.get(role, 0) + 1
                selected[path] = fields[2]
        files.append(BaselineFile(path, role, classification.language, None))
    captured = read_blobs(repo, list(selected.values()))
    files = [BaselineFile(file.path, file.role, file.language, captured.get(selected.get(file.path))) for file in files]
    gaps += [(file.role, f"{file.path}: reuse baseline could not be read")
             for file in files if file.path in selected and file.text is None]
    production = tuple(dict.fromkeys(text for role, text in gaps if role == "production"))
    other = tuple(dict.fromkeys(text for role, text in gaps if role != "production"))
    return tuple(files), production, other, ()


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
    current_line = 0
    added: list[tuple[int, str]] = []
    in_hunk = False

    def close() -> None:
        nonlocal in_hunk, added
        if in_hunk and key:
            collected.setdefault(key, []).append(Hunk(tuple(added)))
        in_hunk, added = False, []

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
            current_line = int(match.group(3))
            in_hunk = True
            continue
        if not in_hunk or not key:
            continue
        if line.startswith("+"):
            added.append((current_line, line[1:]))
            current_line += 1
        elif line.startswith(" "):
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
