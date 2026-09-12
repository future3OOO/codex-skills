from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .git_scope import git_read, read_git_file
from .findings import BaselineFile, Hunk, Numstat, SnapshotEntry
from .path_policy import classify_path, normalize_path


def parse_repo_context_packet(text: str) -> set[str]:
    """Paths a Repo Context Forge packet names, for owner-discovery boosts."""
    paths: set[str] = set()
    for match in re.finditer(r'(?:path|file)=["\']([^"\']+)["\']', text):
        paths.add(normalize_path(match.group(1)))
    for line in text.splitlines():
        for match in re.finditer(r"[\w./-]+\.(?:cjs|cts|go|js|jsx|mjs|mts|php|py|rb|rs|sh|ts|tsx)", line):
            paths.add(normalize_path(match.group(0).strip("`'\"(),:;")))
    return {path for path in paths if path}


def _list_valued(item: dict) -> list[str]:
    """Relationship keys holding the provider's list-shaped result; null/scalar values are malformed."""
    return [key for key in ("callers", "calleeOf", "references") if isinstance(item.get(key), list)]


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
        boost = (8 if any(item[key] for key in _list_valued(item)) else 0) + (
            7 if item.get("processes") or item.get("flows") or item.get("workflows") else 0
        )
        if boost:
            boosts[f"{path}:{name}"] = min(15, boost)
    return boosts, []

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


def _graph_binding(repo: Path, gitnexus_context_json: str, base: str, tree: str) -> tuple[str, frozenset[str]]:
    """The graph evidence's snapshot binding, resolved during capture: the
    gap that leaves caller/callee scope unestablished (parent decision 1b —
    the snapshot index is not a substitute), and the files it covers."""
    absent = "no snapshot-bound external graph evidence: caller/callee scope is unestablished"
    if not gitnexus_context_json.strip():
        return absent, frozenset()
    try:
        payload = json.loads(gitnexus_context_json)
    except json.JSONDecodeError:
        return absent, frozenset()
    if not isinstance(payload, dict) or not payload.get("base") or not payload.get("candidate"):
        return absent, frozenset()
    symbols = payload.get("symbols")
    files = frozenset(
        normalize_path(str(item.get("file") or item.get("path") or ""))
        for item in (symbols if isinstance(symbols, list) else [])
        if isinstance(item, dict) and _list_valued(item)
    ) - {""}
    if not files:
        return "external graph evidence carries no caller/callee symbol results: a bare declaration is not evidence", frozenset()
    declared_base, _ = git_read(repo, ["rev-parse", "--verify", f"{payload['base']}^{{commit}}"])
    declared_tree, _ = git_read(repo, ["rev-parse", "--verify", f"{payload['candidate']}^{{tree}}"])
    if (declared_base.strip(), declared_tree.strip()) != (base, tree):
        return "external graph evidence is stale: it does not name the evaluated snapshot", files
    return "", files


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


def top_dir(path: str) -> str:
    # Root-level files share the repository root: their directory is "", never
    # the filename, or no two root files could ever be neighbors.
    return path.split("/", 1)[0] if "/" in path else ""


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
    packet_paths: frozenset[str]
    gitnexus_boosts: dict[str, int]
    gitnexus_warnings: tuple[str, ...]
    # The two external data Interfaces #77 versions, captured and frozen: the
    # graph evidence's snapshot binding (empty gap when it names this exact
    # snapshot and covers real symbols) and the disposition records read from
    # the fixed out-of-tree carrier with their commits resolved.
    graph_gap: str
    graph_files: frozenset[str]
    disposition_records: tuple[dict[str, object], ...]

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
        untracked = set(scope["untracked"])
        capture_gaps = tuple(str(error) for error in scope["errors"])
        entries = tuple(
            _entry(repo, path, renamed.get(path, path), base, tree, hunks, counts, path in untracked)
            for path in sorted(changed)
        )
        packet_paths = frozenset(parse_repo_context_packet(repo_context_packet))
        boosts, warnings = parse_gitnexus_context_json(gitnexus_context_json)
        baseline, baseline_gaps, baseline_role_gaps, baseline_scope_gaps = _baseline_index(
            repo, base, entries, packet_paths, {key.rsplit(":", 1)[0] for key in boosts}
        )
        graph_gap, graph_files = _graph_binding(repo, gitnexus_context_json, base, tree)
        return cls(
            packet_paths=packet_paths,
            gitnexus_boosts=boosts,
            gitnexus_warnings=tuple(warnings),
            graph_gap=graph_gap,
            graph_files=graph_files,
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


def _baseline_index(
    repo: Path,
    base: str,
    entries: tuple[SnapshotEntry, ...],
    packet_paths: frozenset[str],
    gitnexus_paths: set[str],
) -> tuple[tuple[BaselineFile, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Bounded owner capture: the base-tree source files a change could
    reimplement, read before the snapshot freezes so no detector reads Git.

    Candidate-independent eligibility (owner language among the changed
    languages of the SAME role, and a shared top directory or packet/GitNexus
    naming) and the file and size caps all apply before any blob read. Roles
    are kept apart so widening capture to tests cannot widen which production
    owners the production rules see. A file skipped by a cap, or whose read
    failed, is a recorded gap — unread scope never reads as absence — while a
    path the change adds simply has no base entry.
    """
    if not base:
        return (), (), (), ()
    listed, failure = git_read(repo, ["ls-tree", "-r", "-l", "-z", base])
    if failure:
        return (), (f"reuse baseline listing failed: {failure}",), (), ()
    scope: dict[str, tuple[set[str], set[str]]] = {}
    for entry in entries:
        if entry.classification.role in BASELINE_ROLES:
            languages, roots = scope.setdefault(entry.classification.role, (set(), set()))
            languages.add(entry.classification.language)
            roots.add(top_dir(entry.path))
    files: list[BaselineFile] = []
    gaps: list[tuple[str, str]] = []
    reads: dict[str, int] = {}
    skipped = 0
    for record in listed.split("\0"):
        # ls-tree -l: "<mode> <type> <oid> <size>\t<path>".
        meta, sep, rel_path = record.partition("\t")
        fields = meta.split()
        if not sep or not rel_path or len(fields) < 4 or fields[1] != "blob":
            continue
        classification = classify_path(rel_path)
        if not classification.source:
            continue
        languages, roots = scope.get(classification.role, (set(), set()))
        eligible = classification.language in languages and (
            top_dir(rel_path) in roots or rel_path in packet_paths or rel_path in gitnexus_paths
        )
        text = None
        # An owner excluded by the candidate-relevance bound is unread scope,
        # not proven absence: the exact rules must report that, and no cap or
        # bound may shrink their denominator into a clean verdict.
        skipped += classification.language in languages and not eligible
        if eligible and int(fields[3]) > MAX_INDEX_FILE_BYTES:
            gaps.append((classification.role, f"{rel_path}: reuse baseline exceeds {MAX_INDEX_FILE_BYTES} bytes"))
        elif eligible and reads.get(classification.role, 0) >= MAX_INDEX_FILES:
            gaps.append((classification.role, f"reuse baseline discovery stopped at {MAX_INDEX_FILES} files"))
        elif eligible:
            # Attempts consume the cap, not successes: confirmed read failures
            # must not buy unbounded extra Git reads.
            reads[classification.role] = reads.get(classification.role, 0) + 1
            text, read_failure = git_read(repo, ["show", f"{base}:{rel_path}"])
            if read_failure:
                text = None
                gaps.append((classification.role, f"{rel_path}: reuse baseline could not be read"))
        files.append(BaselineFile(rel_path, classification.role, classification.language, text))
    scope_gap = (f"reuse baseline scope read only the changed top directories: {skipped} "
                 f"same-role source file(s) elsewhere were never read",) if skipped else ()
    production = tuple(dict.fromkeys(text for role, text in gaps if role == "production"))
    other = tuple(dict.fromkeys(text for role, text in gaps if role != "production"))
    return tuple(files), production, other, scope_gap


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
    base_line = current_line = 0
    added: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    in_hunk = False

    def close() -> None:
        nonlocal in_hunk, added, deleted
        if in_hunk and key:
            collected.setdefault(key, []).append(Hunk(tuple(added), tuple(deleted)))
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
