"""Private SQLite implementation for the repository workflow Module.
This is not a selectable backend or public persistence Interface.  It is the
workflow Module's local-runtime implementation: one on-disk database per
repository slot and deterministic recovery of the
active-event pointer from the event ledger.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, NoReturn, Sequence
from .repo_identity import RepoIdentity
from .state_store import _active_candidate_tree, codex_home, repo_state_dir, utc_timestamp
DATABASE_NAME = "workflow.sqlite3"
DATABASE_FILES = frozenset({DATABASE_NAME, *(f"{DATABASE_NAME}{suffix}" for suffix in ("-journal", "-wal", "-shm"))})
AUTHORITY = "sqlite-event-ledger-v1"
STATE_SCHEMA_VERSION = 2
POLICY_VERSION = 1
BUSY_TIMEOUT_MS = 2500
JsonObject = dict[str, object]
class LedgerError(RuntimeError):
    """The authoritative workflow ledger could not be read or changed."""
class LedgerBusy(LedgerError):
    """A bounded SQLite wait expired."""
@dataclass(frozen=True)
class EvidenceWrite:
    evidence_id: str
    workflow_id: str
    kind: str
    schema_version: int
    recorded_at: str
    document: JsonObject
@dataclass(frozen=True)
class ManifestWrite:
    manifest_id: str
    workflow_id: str
    kind: str
    schema_version: int
    recorded_at: str
    document: dict[str, str]
@dataclass(frozen=True)
class WorkflowRetentionItem:
    workflow_id: str
    slug: str
    latest_event_id: int
    active: bool
@dataclass(frozen=True)
class RetentionApplyResult:
    status: str
    current: tuple[WorkflowRetentionItem, ...] = ()
    error: str | None = None
def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _encode_json(value: object) -> str | bytes:
    raw = _canonical(value)
    encoded = raw.encode("utf-8")
    packed = zlib.compress(encoded, 1)
    return packed if len(packed) < len(encoded) else raw


def _decode_json(value: str | bytes) -> object:
    try:
        return json.loads(zlib.decompress(value).decode("utf-8") if isinstance(value, bytes) else value)
    except (TypeError, ValueError, UnicodeError, zlib.error) as exc:
        raise LedgerError("stored evidence JSON is corrupt") from exc
def _logical_id(prefix: str, workflow_id: str, kind: str, document: object) -> str:
    payload = "\0".join((workflow_id, kind, _canonical(document))).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:32]}"
def evidence_write(
    workflow_id: str,
    kind: str,
    document: JsonObject,
    *,
    schema_version: int = 2,
    recorded_at: str | None = None,
) -> EvidenceWrite:
    return EvidenceWrite(
        _logical_id("evidence", workflow_id, kind, document),
        workflow_id,
        kind,
        schema_version,
        recorded_at or utc_timestamp(),
        document,
    )
def manifest_write(
    workflow_id: str,
    kind: str,
    document: dict[str, str],
    *,
    schema_version: int = 1,
    recorded_at: str | None = None,
) -> ManifestWrite:
    return ManifestWrite(
        _logical_id("manifest", workflow_id, kind, document),
        workflow_id,
        kind,
        schema_version,
        recorded_at or utc_timestamp(),
        document,
    )
def database_path(identity: RepoIdentity) -> Path:
    override = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
    root = Path(override).expanduser() if override else codex_home() / "state"
    return root / identity.key / DATABASE_NAME
def _store_exists(identity: RepoIdentity) -> bool:
    return database_path(identity).exists()
def _private_sidecars(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        try:
            if candidate.exists():
                candidate.chmod(0o600)
        except OSError:
            pass
def _locked(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text
def _raise_operational(exc: sqlite3.OperationalError) -> NoReturn:
    if _locked(exc):
        raise LedgerBusy("workflow database is busy; no transition was recorded") from exc
    raise LedgerError(f"workflow database failure: {exc}") from exc
def _open_connection(path: Path, *, read_only: bool) -> sqlite3.Connection:
    """Open one ledger path with the Module's complete SQLite contract."""
    mode = "ro" if read_only else "rw"
    target = path.resolve(strict=False).as_uri() + f"?mode={mode}"
    connection = sqlite3.connect(
        target, uri=True, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if not read_only:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
    return connection
@contextlib.contextmanager
def _path_connection(path: Path, *, read_only: bool) -> Iterator[sqlite3.Connection]:
    """Close one configured connection and preserve private write artifacts."""
    previous_umask = os.umask(0o077) if not read_only else None
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_connection(path, read_only=read_only)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if previous_umask is not None:
            os.umask(previous_umask)
            _private_sidecars(path)
@contextlib.contextmanager
def _connection(
    identity: RepoIdentity, *, prepare_authority: bool = True,
) -> Iterator[sqlite3.Connection]:
    try:
        path = repo_state_dir(identity) / DATABASE_NAME
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
    except OSError as exc:
        raise LedgerError(f"workflow database failure: {exc}") from exc
    try:
        with _path_connection(path, read_only=False) as connection:
            _schema(connection)
            if prepare_authority:
                _ensure_authority(connection, identity)
            yield connection
    except sqlite3.OperationalError as exc:
        _raise_operational(exc)
    except sqlite3.DatabaseError as exc:
        raise LedgerError(f"workflow database failure: {exc}") from exc
def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY,
            repo_key TEXT NOT NULL,
            slug TEXT NOT NULL,
            created_at TEXT NOT NULL,
            state_json TEXT
        );
        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            kind TEXT NOT NULL, schema_version INTEGER NOT NULL,
            recorded_at TEXT NOT NULL, document_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_manifests (
            manifest_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            kind TEXT NOT NULL, recorded_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL, manifest_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            state_schema_version INTEGER NOT NULL,
            policy_version INTEGER NOT NULL,
            activates_workflow INTEGER NOT NULL DEFAULT 0 CHECK (activates_workflow IN (0, 1)),
            UNIQUE(event_id, workflow_id)
        );
        CREATE TABLE IF NOT EXISTS event_evidence (
            event_id INTEGER NOT NULL REFERENCES workflow_events(event_id) ON DELETE CASCADE,
            evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
            PRIMARY KEY(event_id, evidence_id)
        );
        CREATE TABLE IF NOT EXISTS event_manifests (
            event_id INTEGER NOT NULL REFERENCES workflow_events(event_id) ON DELETE CASCADE,
            manifest_id TEXT NOT NULL REFERENCES review_manifests(manifest_id) ON DELETE RESTRICT,
            PRIMARY KEY(event_id, manifest_id)
        );
        CREATE TABLE IF NOT EXISTS active_projection (
            slot INTEGER PRIMARY KEY CHECK(slot = 1),
            workflow_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            FOREIGN KEY(event_id, workflow_id)
                REFERENCES workflow_events(event_id, workflow_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS evidence_parts (
            part_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_part_links (
            evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
            part_id TEXT NOT NULL REFERENCES evidence_parts(part_id) ON DELETE RESTRICT,
            PRIMARY KEY(evidence_id, part_id)
        );
        CREATE INDEX IF NOT EXISTS evidence_part_links_by_part ON evidence_part_links(part_id);
        CREATE INDEX IF NOT EXISTS workflow_events_by_workflow
            ON workflow_events(workflow_id, event_id);
        CREATE INDEX IF NOT EXISTS evidence_by_workflow
            ON evidence(workflow_id, recorded_at);
        """
    )
    event_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(workflow_events)")}
    workflow_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(workflows)")}
    if "state_json" in event_columns:
        _begin_write(connection)
        try:
            if "state_json" not in {str(row["name"]) for row in connection.execute("PRAGMA table_info(workflow_events)")}:
                connection.rollback()
                return
            workflow_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(workflows)")}
            if "state_json" not in workflow_columns:
                connection.execute("ALTER TABLE workflows ADD COLUMN state_json TEXT")
            connection.execute(
                """UPDATE workflows SET state_json = (
                    SELECT state_json FROM workflow_events
                    WHERE workflow_events.workflow_id = workflows.workflow_id
                    ORDER BY event_id DESC LIMIT 1)"""
            )
            if connection.execute("SELECT 1 FROM workflows WHERE state_json IS NULL LIMIT 1").fetchone():
                raise LedgerError("workflow history has no current state for migration")
            connection.execute("ALTER TABLE workflow_events DROP COLUMN state_json")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    elif "state_json" not in workflow_columns:
        raise LedgerError("workflow state schema has no current projection")
def _begin_write(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        _raise_operational(exc)
def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None
def _validate_repository_identity(connection: sqlite3.Connection, identity: RepoIdentity) -> None:
    expected = {"repo_key": identity.key, "repo_root": str(identity.root)}
    for key, value in expected.items():
        stored = _metadata(connection, key)
        if stored != value:
            raise LedgerError("workflow database repository identity does not match this checkout")
def _validate_state_identity(identity: RepoIdentity, state: JsonObject) -> None:
    if state.get("repo") != identity.as_dict():
        raise LedgerError("canonical workflow state repository identity does not match this checkout")
def _manifest_value(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(path, str) and isinstance(digest, str) for path, digest in value.items()):
        return None
    return value
def _manifest_from(connection: sqlite3.Connection, manifest_id: str | None) -> dict[str, str] | None:
    if not manifest_id:
        return None
    row = connection.execute(
        "SELECT workflow_id, kind, manifest_json FROM review_manifests WHERE manifest_id = ?",
        (manifest_id,),
    ).fetchone()
    if row is None:
        return None
    stored = _decode_json(row["manifest_json"])
    if isinstance(stored, list):
        if (len(stored) != 3 or not isinstance(stored[0], str)
                or not isinstance(stored[2], list)
                or not all(isinstance(path, str) for path in stored[2])):
            raise LedgerError("stored review manifest delta is corrupt")
        base = connection.execute(
            "SELECT workflow_id, manifest_json FROM review_manifests WHERE manifest_id = ?",
            (stored[0],),
        ).fetchone()
        if base is None or base["workflow_id"] != row["workflow_id"]:
            raise LedgerError("stored review manifest base is missing")
        document = _manifest_value(_decode_json(base["manifest_json"]))
        changed = _manifest_value(stored[1])
        if document is None or changed is None:
            raise LedgerError("stored review manifest base is corrupt")
        document = {**document, **changed}
        for path in stored[2]:
            document.pop(path, None)
    else:
        document = _manifest_value(stored)
    if document is None or _logical_id("manifest", row["workflow_id"], row["kind"], document) != manifest_id:
        raise LedgerError("stored review manifest digest mismatch")
    return document
def _insert_workflow(connection: sqlite3.Connection, identity: RepoIdentity, state: JsonObject) -> None:
    _validate_state_identity(identity, state)
    workflow_id = state.get("workflowId")
    slug = state.get("slug")
    created_at = state.get("createdAt")
    if not all(isinstance(value, str) and value for value in (workflow_id, slug, created_at)):
        raise LedgerError("canonical workflow state is missing workflow identity")
    connection.execute(
        "INSERT OR IGNORE INTO workflows(workflow_id, repo_key, slug, created_at, state_json) VALUES (?, ?, ?, ?, ?)",
        (workflow_id, identity.key, slug, created_at, _canonical(state)),
    )


PART_FIELDS = (
    ("behaviorMap", "behaviorMapRevision", "map-item", "map-revision"),
    ("runs", "runsRevision", "run", "run-revision"),
)
REVISION_FIELDS = frozenset(entry[1] for entry in PART_FIELDS)


def _part(connection: sqlite3.Connection, kind: str, value: object) -> str:
    identifier = _logical_id("part", "", kind, value)
    stored = _encode_json(value)
    connection.execute(
        "INSERT OR IGNORE INTO evidence_parts(part_id, kind, content_json) VALUES (?, ?, ?)",
        (identifier, kind, stored),
    )
    return identifier


def _revision(
    connection: sqlite3.Connection, workflow_id: str, kind: str,
    ids: list[str], links: set[str],
) -> str:
    identifier = _logical_id("part", "", kind, ids)
    previous = connection.execute(
        "SELECT p.part_id FROM evidence_parts p "
        "JOIN evidence_part_links l ON l.part_id = p.part_id "
        "JOIN evidence e ON e.evidence_id = l.evidence_id "
        "WHERE e.workflow_id = ? AND p.kind = ? ORDER BY e.rowid DESC LIMIT 1",
        (workflow_id, kind),
    ).fetchone()
    stored: object = ids
    if previous is not None and previous[0] != identifier:
        prior, depth = _read_revision(connection, kind, previous[0])
        changes = [[index, item] for index, item in enumerate(ids[:len(prior)])
                   if item != prior[index]]
        delta = [previous[0], changes, ids[len(prior):], len(ids), depth + 1]
        if depth < 15 and len(_encode_json(delta)) < len(_encode_json(ids)):
            stored = delta
    connection.execute(
        "INSERT OR IGNORE INTO evidence_parts(part_id, kind, content_json) VALUES (?, ?, ?)",
        (identifier, kind, _encode_json(stored)),
    )
    ancestor = identifier
    while True:
        row = connection.execute(
            "SELECT content_json FROM evidence_parts WHERE part_id = ?", (ancestor,)
        ).fetchone()
        value = _decode_json(row[0]) if row is not None else None
        if not isinstance(value, list) or len(value) != 5 or not isinstance(value[0], str):
            break
        ancestor = value[0]
        links.add(ancestor)
    return identifier


def _read_revision(
    connection: sqlite3.Connection, kind: str, identifier: str,
    links: set[str] | None = None,
) -> tuple[list[str], int]:
    row = connection.execute(
        "SELECT kind, content_json FROM evidence_parts WHERE part_id = ?", (identifier,)
    ).fetchone()
    if row is None or row["kind"] != kind:
        raise LedgerError("evidence revision is missing or foreign")
    stored = _decode_json(row["content_json"])
    if isinstance(stored, list) and all(isinstance(item, str) for item in stored):
        ids, depth = stored, 0
    elif (isinstance(stored, list) and len(stored) == 5
          and isinstance(stored[0], str) and isinstance(stored[1], list)
          and isinstance(stored[2], list) and type(stored[3]) is int
          and type(stored[4]) is int and 0 < stored[4] <= 15):
        if links is not None and stored[0] not in links:
            raise LedgerError("evidence revision base is unlinked")
        prior, prior_depth = _read_revision(connection, kind, stored[0], links)
        if stored[4] != prior_depth + 1 or not 0 <= stored[3] <= len(prior) + len(stored[2]):
            raise LedgerError("evidence revision delta is corrupt")
        ids = prior[:stored[3]]
        ids.extend(stored[2])
        if len(ids) != stored[3] or not all(isinstance(item, str) for item in ids):
            raise LedgerError("evidence revision delta is corrupt")
        for change in stored[1]:
            if (not isinstance(change, list) or len(change) != 2
                    or type(change[0]) is not int or not 0 <= change[0] < len(ids)
                    or not isinstance(change[1], str)):
                raise LedgerError("evidence revision delta is corrupt")
            ids[change[0]] = change[1]
        depth = stored[4]
    else:
        raise LedgerError("evidence revision is corrupt")
    if _logical_id("part", "", kind, ids) != identifier:
        raise LedgerError("evidence revision digest mismatch")
    return ids, depth


def _pack(
    connection: sqlite3.Connection, workflow_id: str, value: object,
    linked_parts: set[str] | None = None,
) -> object:
    if isinstance(value, dict) and REVISION_FIELDS & value.keys():
        raise LedgerError("evidence root contains a reserved revision field")
    links = linked_parts if linked_parts is not None else set()

    def pack(item: object, *, document: bool = False, inline: bool = False) -> object:
        if isinstance(item, dict):
            packed = {key: pack(child) for key, child in item.items()
                      if not document or key not in {"behaviorMap", "runs"}}
            if document:
                for field, revision, item_kind, revision_kind in PART_FIELDS:
                    if field not in item:
                        continue
                    values = item[field]
                    if not isinstance(values, list):
                        raise LedgerError(f"evidence {field} is not an array")
                    ids = [_part(connection, item_kind, pack(child, inline=True))
                           for child in values]
                    links.update(ids)
                    packed[revision] = _revision(connection, workflow_id, revision_kind, ids, links)
                    links.add(packed[revision])
            elif set(item) & {"$part", "$literal", *REVISION_FIELDS}:
                links.add(_part(connection, "literal-marker", {"schemaVersion": 2}))
                packed = {"$literal": packed}
        elif isinstance(item, list):
            packed = [pack(child) for child in item]
        else:
            packed = item
        if not (document or inline) and len(_canonical(packed).encode("utf-8")) > 200:
            identifier = _part(connection, "subtree", packed)
            links.add(identifier)
            return {"$part": identifier}
        return packed

    return pack(value, document=True)


def _expand(
    connection: sqlite3.Connection, workflow_id: str, value: object, evidence_id: str,
    schema_version: int,
) -> object:
    links = {str(row[0]) for row in connection.execute(
        "SELECT part_id FROM evidence_part_links WHERE evidence_id = ?", (evidence_id,)
    )}
    if schema_version == 1 and not links:
        return value  # Schema-1 documents stored raw JSON and had no part edges.
    literal_marker = _logical_id("part", "", "literal-marker", {"schemaVersion": 2})
    if literal_marker in links:
        _read_part(connection, "literal-marker", literal_marker)

    def expand(item: object, *, document: bool = False) -> object:
        if isinstance(item, list):
            return [expand(child) for child in item]
        if not isinstance(item, dict):
            return item
        if not document and "$part" in item:
            identifier = item["$part"]
            if set(item) != {"$part"} or not isinstance(identifier, str) or identifier not in links:
                raise LedgerError("evidence has an unlinked part marker")
            return expand(_read_part(connection, "subtree", identifier))
        if not document and "$literal" in item:
            if (literal_marker not in links or set(item) != {"$literal"}
                    or not isinstance(item["$literal"], dict)):
                raise LedgerError("evidence literal marker is corrupt")
            return {key: expand(child) for key, child in item["$literal"].items()}
        expanded = {key: expand(child) for key, child in item.items()
                    if not document or key not in REVISION_FIELDS}
        if document:
            for field, revision, item_kind, revision_kind in PART_FIELDS:
                if revision not in item:
                    continue
                if field in item:
                    raise LedgerError(f"evidence has both {field} and {revision}")
                if item[revision] not in links:
                    raise LedgerError(f"evidence {revision} is unlinked")
                ids, _ = _read_revision(connection, revision_kind, item[revision], links)
                if any(child not in links for child in ids):
                    raise LedgerError(f"evidence {revision} contains an unlinked item")
                expanded[field] = [expand(_read_part(connection, item_kind, child))
                                   for child in ids]
        return expanded

    return expand(value, document=True)


def _read_part(connection: sqlite3.Connection, kind: str, identifier: object) -> object:
    row = connection.execute(
        "SELECT kind, content_json FROM evidence_parts WHERE part_id = ?",
        (identifier,),
    ).fetchone()
    if row is None or str(row["kind"]) != kind:
        raise LedgerError(f"evidence part is missing or foreign: {identifier}")
    try:
        value = _decode_json(row["content_json"])
    except LedgerError as exc:
        raise LedgerError(f"evidence part is corrupt: {identifier}") from exc
    if _logical_id("part", "", kind, value) != identifier:
        raise LedgerError(f"evidence part digest mismatch: {identifier}")
    return value


def _insert_evidence(connection: sqlite3.Connection, writes: Sequence[EvidenceWrite]) -> None:
    for write in writes:
        if connection.execute("SELECT 1 FROM evidence WHERE evidence_id = ?", (write.evidence_id,)).fetchone():
            continue
        links: set[str] = set()
        document = _canonical(_pack(connection, write.workflow_id, write.document, links))
        connection.execute(
            """INSERT INTO evidence(
                   evidence_id, workflow_id, kind, schema_version, recorded_at, document_json
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(evidence_id) DO NOTHING""",
            (
                write.evidence_id,
                write.workflow_id,
                write.kind,
                write.schema_version,
                write.recorded_at,
                document,
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO evidence_part_links(evidence_id, part_id) VALUES (?, ?)",
            ((write.evidence_id, identifier) for identifier in links),
        )
def _insert_manifests(connection: sqlite3.Connection, writes: Sequence[ManifestWrite]) -> None:
    for write in writes:
        existing = _manifest_from(connection, write.manifest_id)
        if existing is not None:
            if existing != write.document:
                raise LedgerError("review manifest digest collision")
            continue
        full = _canonical(write.document)
        base = connection.execute(
            "SELECT manifest_id FROM review_manifests WHERE workflow_id = ? ORDER BY rowid LIMIT 1",
            (write.workflow_id,),
        ).fetchone()
        if base is not None:
            original = _manifest_from(connection, base["manifest_id"])
            if original is None:
                raise LedgerError("stored review manifest base is missing")
            delta = _encode_json([
                base["manifest_id"],
                {path: value for path, value in write.document.items() if original.get(path) != value},
                [path for path in original if path not in write.document],
            ])
            if len(delta) < len(full.encode("utf-8")):
                full = delta
        connection.execute(
            """INSERT INTO review_manifests(
                   manifest_id, workflow_id, kind, schema_version, recorded_at, manifest_json
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(manifest_id) DO NOTHING""",
            (
                write.manifest_id,
                write.workflow_id,
                write.kind,
                write.schema_version,
                write.recorded_at,
                full,
            ),
        )
def _append_event(
    connection: sqlite3.Connection,
    state: JsonObject,
    kind: str,
    *, evidence: Sequence[EvidenceWrite] = (),
    manifests: Sequence[ManifestWrite] = (), activate: bool = False,
) -> int:
    workflow_id = state.get("workflowId")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise LedgerError("event state has no workflowId")
    _insert_evidence(connection, evidence)
    _insert_manifests(connection, manifests)
    recorded_at = str(state.get("updatedAt") or utc_timestamp())
    cursor = connection.execute(
        """INSERT INTO workflow_events(
               workflow_id, kind, recorded_at, state_schema_version,
               policy_version, activates_workflow
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            workflow_id,
            kind,
            recorded_at,
            STATE_SCHEMA_VERSION,
            POLICY_VERSION,
            1 if activate else 0,
        ),
    )
    connection.execute(
        "UPDATE workflows SET state_json = ? WHERE workflow_id = ?",
        (_canonical(state), workflow_id),
    )
    event_id = int(cursor.lastrowid)
    connection.executemany(
        "INSERT INTO event_evidence(event_id, evidence_id) VALUES (?, ?)",
        ((event_id, write.evidence_id) for write in evidence),
    )
    connection.executemany(
        "INSERT INTO event_manifests(event_id, manifest_id) VALUES (?, ?)",
        ((event_id, write.manifest_id) for write in manifests),
    )
    return event_id
def _set_projection(connection: sqlite3.Connection, workflow_id: str, event_id: int) -> None:
    connection.execute(
        """INSERT INTO active_projection(slot, workflow_id, event_id)
           VALUES (1, ?, ?)
           ON CONFLICT(slot) DO UPDATE SET
               workflow_id = excluded.workflow_id,
               event_id = excluded.event_id""",
        (workflow_id, event_id),
    )
def _ensure_authority(connection: sqlite3.Connection, identity: RepoIdentity) -> None:
    if _metadata(connection, "authority") == AUTHORITY:
        _validate_repository_identity(connection, identity)
        return
    _begin_write(connection)
    try:
        _apply_authority(connection, identity)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

def _apply_authority(connection: sqlite3.Connection, identity: RepoIdentity) -> None:
    if _metadata(connection, "authority") == AUTHORITY:
        _validate_repository_identity(connection, identity)
        return
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (("repo_key", identity.key), ("repo_root", str(identity.root)),
            ("authority", AUTHORITY),),
    )
def _event_versions(row: sqlite3.Row) -> None:
    state_version, policy_version = row["state_schema_version"], row["policy_version"]
    if (type(state_version) is not int or state_version not in (1, STATE_SCHEMA_VERSION)
            or type(policy_version) is not int or not 1 <= policy_version <= POLICY_VERSION):
        raise LedgerError("authoritative event schema or policy is unsupported")


def _event_state(row: sqlite3.Row, workflow_id: str | None = None) -> JsonObject:
    _event_versions(row)
    try:
        state = json.loads(str(row["state_json"]))
    except (TypeError, ValueError) as exc:
        raise LedgerError("authoritative event contains invalid state JSON") from exc
    if (not isinstance(state, dict) or type(state.get("schemaVersion")) is not int
            or state.get("schemaVersion") != 1):
        raise LedgerError("authoritative event contains invalid state schema")
    if workflow_id is not None and state.get("workflowId") != workflow_id:
        raise LedgerError("authoritative event workflowId does not match ledger identity")
    return state
def _repair_projection(connection: sqlite3.Connection) -> JsonObject | None:
    activation = connection.execute(
        """SELECT workflow_id
           FROM workflow_events
           WHERE activates_workflow = 1
           ORDER BY event_id DESC
           LIMIT 1"""
    ).fetchone()
    if activation is None:
        connection.execute("DELETE FROM active_projection WHERE slot = 1")
        return None
    workflow_id = str(activation["workflow_id"])
    latest = connection.execute(
        """SELECT event.event_id, event.state_schema_version, event.policy_version,
                  workflow.state_json
           FROM workflow_events AS event JOIN workflows AS workflow
             ON workflow.workflow_id = event.workflow_id
           WHERE event.workflow_id = ?
           ORDER BY event.event_id DESC
           LIMIT 1""",
        (workflow_id,),
    ).fetchone()
    if latest is None:
        connection.execute("DELETE FROM active_projection WHERE slot = 1")
        return None
    event_id = int(latest["event_id"])
    pointer = connection.execute(
        "SELECT workflow_id, event_id FROM active_projection WHERE slot = 1"
    ).fetchone()
    if pointer is None or str(pointer["workflow_id"]) != workflow_id or int(pointer["event_id"]) != event_id:
        _set_projection(connection, workflow_id, event_id)
    return _event_state(latest, workflow_id)
class LedgerMutation:
    """One private transaction over the active workflow facts."""
    def __init__(self, connection: sqlite3.Connection, identity: RepoIdentity) -> None:
        self.connection = connection
        self.identity = identity
        self.state = _repair_projection(connection)
        if self.state is not None:
            _validate_state_identity(identity, self.state)
    def append(
        self,
        state: JsonObject,
        kind: str,
        *,
        evidence: Sequence[EvidenceWrite] = (),
        manifests: Sequence[ManifestWrite] = (),
        activate: bool = False,
    ) -> JsonObject:
        _insert_workflow(self.connection, self.identity, state)
        workflow_id = str(state["workflowId"])
        if not activate:
            active = _repair_projection(self.connection)
            if active is None or active.get("workflowId") != workflow_id:
                raise LedgerError("workflow instance is no longer active")
        event_id = _append_event(
            self.connection,
            state,
            kind,
            evidence=evidence,
            manifests=manifests,
            activate=activate,
        )
        _set_projection(self.connection, workflow_id, event_id)
        self.state = state
        return state
    def evidence(self, evidence_id: str | None) -> JsonObject | None:
        if not evidence_id:
            return None
        row = self.connection.execute(
            "SELECT document_json, schema_version FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            return None
        value = _expand(self.connection, str(self.state["workflowId"]),
                        _decode_json(row["document_json"]), evidence_id, int(row["schema_version"]))
        return value if isinstance(value, dict) else None
    def manifest(self, manifest_id: str | None) -> dict[str, str] | None:
        return _manifest_from(self.connection, manifest_id)
@contextlib.contextmanager
def mutation(
    identity: RepoIdentity, *, expected_candidate_tree: str | None = None,
) -> Iterator[LedgerMutation]:
    with _connection(identity, prepare_authority=False) as connection:
        _begin_write(connection)
        try:
            _apply_authority(connection, identity)
            transaction = LedgerMutation(connection, identity)
            yield transaction
            if (expected_candidate_tree is not None
                    and _active_candidate_tree(identity) != expected_candidate_tree):
                raise LedgerError("active candidate changed during workflow mutation")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
def read_active(identity: RepoIdentity) -> JsonObject | None:
    if not _store_exists(identity):
        return None
    with _connection(identity) as connection:
        _begin_write(connection)
        try:
            state = _repair_projection(connection)
            if state is not None:
                _validate_state_identity(identity, state)
            connection.commit()
            return state
        except Exception:
            connection.rollback()
            raise
def read_evidence(identity: RepoIdentity, evidence_id: str, *, full: bool = True) -> JsonObject | None:
    if _store_exists(identity):
        with _connection(identity) as connection:
            row = connection.execute(
                """SELECT evidence_id, workflow_id, kind, schema_version, recorded_at"""
                + (", document_json" if full else "") + " FROM evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if row is not None:
                result: JsonObject = {
                    "evidenceId": str(row["evidence_id"]),
                    "workflowId": str(row["workflow_id"]),
                    "kind": str(row["kind"]),
                    "schemaVersion": int(row["schema_version"]),
                    "recordedAt": str(row["recorded_at"]),
                }
                if full:
                    result["document"] = _expand(
                        connection, str(row["workflow_id"]), _decode_json(row["document_json"]), evidence_id,
                        int(row["schema_version"]),
                    )
                return result
    return None
def read_manifest(identity: RepoIdentity, manifest_id: str) -> dict[str, str] | None:
    if _store_exists(identity):
        with _connection(identity) as connection:
            return _manifest_from(connection, manifest_id)
    return None
def history(identity: RepoIdentity, workflow_id: str | None = None) -> JsonObject:
    if not _store_exists(identity):
        return {"events": []}
    with _connection(identity) as connection:
        _begin_write(connection)
        try:
            _repair_projection(connection)
            where = "WHERE event.workflow_id = ?" if workflow_id else ""
            params: tuple[object, ...] = (workflow_id,) if workflow_id else ()
            rows = connection.execute(
                f"""SELECT event.event_id, event.workflow_id, event.kind, event.recorded_at,
                           event.state_schema_version, event.policy_version,
                           event.activates_workflow
                    FROM workflow_events AS event
                    {where}
                    ORDER BY event.event_id""",
                params,
            ).fetchall()
            events = []
            for row in rows:
                # Validated, never published: history fails closed on a row the
                # projection could not rebuild from, and replays no state blob.
                _event_versions(row)
                event_id = int(row["event_id"])
                evidence_ids = [
                    str(item["evidence_id"])
                    for item in connection.execute(
                        "SELECT evidence_id FROM event_evidence WHERE event_id = ? ORDER BY evidence_id",
                        (event_id,),
                    )
                ]
                manifest_ids = [
                    str(item["manifest_id"])
                    for item in connection.execute(
                        "SELECT manifest_id FROM event_manifests WHERE event_id = ? ORDER BY manifest_id",
                        (event_id,),
                    )
                ]
                events.append({
                    "eventId": event_id,
                    "workflowId": str(row["workflow_id"]),
                    "kind": str(row["kind"]),
                    "recordedAt": str(row["recorded_at"]),
                    "stateSchemaVersion": int(row["state_schema_version"]),
                    "policyVersion": int(row["policy_version"]),
                    "activatesWorkflow": bool(row["activates_workflow"]),
                    "evidenceIds": evidence_ids,
                    "manifestIds": manifest_ids,
                })
            connection.commit()
            return {"events": events}
        except Exception:
            connection.rollback()
            raise
def _retention_inventory_connection(
    connection: sqlite3.Connection, expected_repo_key: str,
) -> tuple[WorkflowRetentionItem, ...] | None:
    if _metadata(connection, "authority") != AUTHORITY:
        return None
    if _metadata(connection, "repo_key") != expected_repo_key:
        raise LedgerError("database repository identity does not match its state slot")
    activation = connection.execute(
        "SELECT workflow_id FROM workflow_events WHERE activates_workflow = 1 "
        "ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    active = str(activation["workflow_id"]) if activation else None
    event_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(workflow_events)")}
    state_column = "event.state_json" if "state_json" in event_columns else "workflow.state_json"
    rows = connection.execute(
        f"""SELECT workflow.workflow_id, workflow.repo_key, workflow.slug,
                  event.event_id AS latest_event, event.state_schema_version,
                  event.policy_version, {state_column} AS state_json
           FROM workflows AS workflow JOIN workflow_events AS event
             ON event.event_id = (SELECT MAX(latest.event_id) FROM workflow_events AS latest
                                  WHERE latest.workflow_id = workflow.workflow_id)
           ORDER BY event.event_id DESC, workflow.workflow_id DESC"""
    ).fetchall()
    if len(rows) != int(connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]):
        raise LedgerError("authoritative workflow history is incomplete")
    items = []
    for row in rows:
        workflow_id = str(row["workflow_id"])
        if str(row["repo_key"]) != expected_repo_key:
            raise LedgerError("workflow row repository identity does not match its state slot")
        _event_state(row, workflow_id)
        items.append(WorkflowRetentionItem(
            workflow_id, str(row["slug"]), int(row["latest_event"]), workflow_id == active))
    known = {item.workflow_id for item in items}
    if (items and active is None) or (active is not None and active not in known):
        raise LedgerError("authoritative active workflow history is missing")
    return tuple(items)
def retention_inventory(
    database: Path, expected_repo_key: str,
) -> tuple[tuple[WorkflowRetentionItem, ...] | None, str | None]:
    try:
        with _path_connection(database, read_only=True) as connection:
            return _retention_inventory_connection(connection, expected_repo_key), None
    except (OSError, sqlite3.DatabaseError, LedgerError, ValueError) as exc:
        return None, str(exc)
def apply_retention(
    database: Path, expected_repo_key: str, expected: Sequence[WorkflowRetentionItem],
    remove_ids: set[str],
) -> RetentionApplyResult:
    try:
        with _path_connection(database, read_only=False) as connection:
            try:
                _begin_write(connection)
            except LedgerBusy as exc:
                return RetentionApplyResult("busy", error=str(exc))
            try:
                current = _retention_inventory_connection(connection, expected_repo_key)
                if current is None:
                    connection.rollback()
                    return RetentionApplyResult("not-authoritative")
                if tuple(expected) != current:
                    connection.rollback()
                    return RetentionApplyResult("changed", current=current)
                known = {item.workflow_id for item in current}
                active = {item.workflow_id for item in current if item.active}
                if not remove_ids <= known or remove_ids & active:
                    raise LedgerError("retention selection is stale or includes the active workflow")
                connection.executemany(
                    "DELETE FROM workflows WHERE workflow_id = ?",
                    ((workflow_id,) for workflow_id in sorted(remove_ids)),
                )
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_parts'").fetchone():
                    connection.execute(
                        "DELETE FROM evidence_parts WHERE NOT EXISTS "
                        "(SELECT 1 FROM evidence_part_links WHERE part_id = evidence_parts.part_id)"
                    )
                connection.commit()
                return RetentionApplyResult("applied", current=current)
            except (sqlite3.DatabaseError, LedgerError, ValueError) as exc:
                connection.rollback()
                return RetentionApplyResult("failed", error=str(exc))
    except (OSError, sqlite3.DatabaseError) as exc:
        return RetentionApplyResult("failed", error=str(exc))
