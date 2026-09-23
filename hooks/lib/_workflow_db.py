"""Private SQLite implementation for the repository workflow Module.
This is not a selectable backend or public persistence Interface.  It is the
workflow Module's local-runtime implementation: one on-disk database per
repository slot. Events are receipts; each workflow's current state lives on its
row, and the active workflow is the one the latest activating event names.
Evidence stores Behavior Map items and run rows once each, content-addressed.
"""
from __future__ import annotations
import contextlib
import contextvars
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, NoReturn, Sequence
from .repo_identity import RepoIdentity
from .state_store import _active_candidate_tree, codex_home, repo_state_dir, utc_timestamp
DATABASE_NAME = "workflow.sqlite3"
DATABASE_FILES = frozenset({DATABASE_NAME, *(f"{DATABASE_NAME}{suffix}" for suffix in ("-journal", "-wal", "-shm"))})
AUTHORITY = "sqlite-event-ledger-v1"
STATE_SCHEMA_VERSION = 1
# Format 2 keeps state on workflow rows and evidence lists as parts. The tables an
# older estate reads first become views over a function no SQLite build defines,
# so its first read fails naming the format and its schema script creates nothing.
LEDGER_FORMAT = "2"
FORMAT_REFUSAL = f"workflow ledger format v{LEDGER_FORMAT} needs the upgraded workflow estate"
POLICY_VERSION = 1
BUSY_TIMEOUT_MS = 2500
# Lists stored as content-addressed parts: the same item or run is written once
# however many evidence documents carry it.
PART_PATHS = (("behaviorMap",), ("runs",), ("document", "behaviorMap"))
# `record --check`: every recorder runs its whole transaction, then rolls back.
CHECK_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("check_only", default=False)
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
def _logical_id(prefix: str, workflow_id: str, kind: str, document: object) -> str:
    payload = "\0".join((workflow_id, kind, _canonical(document))).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:32]}"
def evidence_write(
    workflow_id: str,
    kind: str,
    document: JsonObject,
    *,
    schema_version: int = 1,
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
def _connection(identity: RepoIdentity) -> Iterator[sqlite3.Connection]:
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
            _ensure_authority(connection, identity)
            yield connection
    except sqlite3.OperationalError as exc:
        _raise_operational(exc)
    except sqlite3.DatabaseError as exc:
        raise LedgerError(f"workflow database failure: {exc}") from exc
def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ledger_metadata (
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
        CREATE TABLE IF NOT EXISTS evidence_parts (
            part_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
            part_json TEXT NOT NULL
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
        CREATE INDEX IF NOT EXISTS workflow_events_by_workflow
            ON workflow_events(workflow_id, event_id);
        CREATE INDEX IF NOT EXISTS evidence_by_workflow
            ON evidence(workflow_id, recorded_at);
        """
    )
def _begin_write(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        _raise_operational(exc)
def _object(connection: sqlite3.Connection, name: str) -> str | None:
    row = connection.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return None if row is None else str(row["type"])
def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    """Format 2 keeps metadata in ledger_metadata; an older ledger still has its
    metadata table until its migration renames it."""
    table = "metadata" if _object(connection, "metadata") == "table" else "ledger_metadata"
    if _object(connection, table) is None:
        return None
    row = connection.execute(f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()
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
def _snapshot_era(connection: sqlite3.Connection) -> bool:
    return any(row["name"] == "state_json" for row in connection.execute("PRAGMA table_info(workflow_events)"))
def _ensure_authority(connection: sqlite3.Connection, identity: RepoIdentity) -> None:
    """Stamp a new ledger, or bring an older one to format v2; either commits whole
    or leaves the ledger untouched. Each check reads one snapshot, and the locked one
    repeats it: a racing opener may migrate the ledger between reads."""
    connection.execute("BEGIN")
    current = _metadata(connection, "format") == LEDGER_FORMAT
    connection.rollback()
    if not current:
        _begin_write(connection)
        if current := _metadata(connection, "format") == LEDGER_FORMAT:
            connection.rollback()
    if current:
        _validate_repository_identity(connection, identity)
        return
    try:
        if _object(connection, "metadata") == "table":
            connection.execute("INSERT INTO ledger_metadata SELECT key, value FROM metadata")
        if _metadata(connection, "authority") == AUTHORITY:
            _validate_repository_identity(connection, identity)
        else:
            connection.executemany(
                "INSERT INTO ledger_metadata(key, value) VALUES (?, ?)",
                (("repo_key", identity.key), ("repo_root", str(identity.root)), ("authority", AUTHORITY)),
            )
        if _snapshot_era(connection):
            if not any(row["name"] == "state_json" for row in connection.execute("PRAGMA table_info(workflows)")):
                connection.execute("ALTER TABLE workflows ADD COLUMN state_json TEXT")
            connection.execute(
                """UPDATE workflows SET state_json = (
                       SELECT event.state_json FROM workflow_events AS event
                       WHERE event.workflow_id = workflows.workflow_id
                       ORDER BY event.event_id DESC LIMIT 1)"""
            )
            connection.execute("ALTER TABLE workflow_events DROP COLUMN state_json")
        connection.execute("INSERT OR REPLACE INTO ledger_metadata(key, value) VALUES ('format', ?)", (LEDGER_FORMAT,))
        for name in ("metadata", "active_projection", "migration_records"):
            connection.execute(f"DROP TABLE IF EXISTS {name}")
            connection.execute(f'CREATE VIEW {name} AS SELECT "{FORMAT_REFUSAL}"()')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
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
        "SELECT manifest_json FROM review_manifests WHERE manifest_id = ?",
        (manifest_id,),
    ).fetchone()
    return _manifest_value(json.loads(str(row["manifest_json"]))) if row is not None else None
def _insert_workflow(connection: sqlite3.Connection, identity: RepoIdentity, state: JsonObject) -> None:
    _validate_state_identity(identity, state)
    workflow_id = state.get("workflowId")
    slug = state.get("slug")
    created_at = state.get("createdAt")
    if not all(isinstance(value, str) and value for value in (workflow_id, slug, created_at)):
        raise LedgerError("canonical workflow state is missing workflow identity")
    connection.execute(
        "INSERT OR IGNORE INTO workflows(workflow_id, repo_key, slug, created_at) VALUES (?, ?, ?, ?)",
        (workflow_id, identity.key, slug, created_at),
    )
def _part_holders(document: JsonObject) -> Iterator[tuple[JsonObject, str]]:
    for path in PART_PATHS:
        holder: object = document
        for key in path[:-1]:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if isinstance(holder, dict) and path[-1] in holder:
            yield holder, path[-1]
def _insert_evidence(connection: sqlite3.Connection, writes: Sequence[EvidenceWrite]) -> None:
    for write in writes:
        stored = json.loads(_canonical(write.document))
        for holder, key in _part_holders(stored):
            if not isinstance(holder[key], list):
                continue
            parts = [(_logical_id("part", write.workflow_id, "part", value), _canonical(value)) for value in holder[key]]
            connection.executemany(
                """INSERT INTO evidence_parts(part_id, workflow_id, part_json) VALUES (?, ?, ?)
                   ON CONFLICT(part_id) DO NOTHING""",
                ((part_id, write.workflow_id, text) for part_id, text in parts),
            )
            holder[key] = {"parts": [part_id for part_id, _ in parts]}
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
                _canonical(stored),
            ),
        )
def _document(connection: sqlite3.Connection, text: str) -> JsonObject:
    """A stored evidence document with its content-addressed parts restored."""
    value = json.loads(text)
    for holder, key in _part_holders(value) if isinstance(value, dict) else ():
        reference = holder[key]
        if isinstance(reference, dict) and set(reference) == {"parts"}:
            ids = list(reference["parts"])
            rows = dict(connection.execute(
                f"SELECT part_id, part_json FROM evidence_parts WHERE part_id IN ({','.join('?' * len(ids))})", ids,
            ).fetchall()) if ids else {}
            if not set(ids) <= set(rows):
                raise LedgerError("stored evidence references a missing part")
            holder[key] = [json.loads(rows[part_id]) for part_id in ids]
    return value
def _insert_manifests(connection: sqlite3.Connection, writes: Sequence[ManifestWrite]) -> None:
    for write in writes:
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
                _canonical(write.document),
            ),
        )
def _append_event(
    connection: sqlite3.Connection,
    state: JsonObject,
    kind: str,
    *, evidence: Sequence[EvidenceWrite] = (),
    manifests: Sequence[ManifestWrite] = (), activate: bool = False,
) -> None:
    workflow_id = state.get("workflowId")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise LedgerError("event state has no workflowId")
    _insert_evidence(connection, evidence)
    _insert_manifests(connection, manifests)
    cursor = connection.execute(
        """INSERT INTO workflow_events(
               workflow_id, kind, recorded_at, state_schema_version, policy_version, activates_workflow
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (workflow_id, kind, str(state.get("updatedAt") or utc_timestamp()),
         STATE_SCHEMA_VERSION, POLICY_VERSION, 1 if activate else 0),
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
    connection.execute("UPDATE workflows SET state_json = ? WHERE workflow_id = ?", (_canonical(state), workflow_id))
def _state(row: sqlite3.Row) -> JsonObject:
    try:
        state = json.loads(str(row["state_json"]))
    except (TypeError, ValueError) as exc:
        raise LedgerError("authoritative workflow contains invalid state JSON") from exc
    if (not isinstance(state, dict) or type(state.get("schemaVersion")) is not int
            or state.get("schemaVersion") != STATE_SCHEMA_VERSION
            or state.get("workflowId") != row["workflow_id"]):
        raise LedgerError("authoritative workflow contains invalid state")
    return state
def _active(connection: sqlite3.Connection) -> JsonObject | None:
    row = connection.execute(
        """SELECT workflow.workflow_id, workflow.state_json
           FROM workflow_events AS event JOIN workflows AS workflow USING (workflow_id)
           WHERE event.activates_workflow = 1 ORDER BY event.event_id DESC LIMIT 1"""
    ).fetchone()
    return None if row is None else _state(row)
class LedgerMutation:
    """One private transaction over the active workflow facts."""
    def __init__(self, connection: sqlite3.Connection, identity: RepoIdentity) -> None:
        self.connection = connection
        self.identity = identity
        self.state = _active(connection)
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
        if not activate:
            active = _active(self.connection)
            if active is None or active.get("workflowId") != state["workflowId"]:
                raise LedgerError("workflow instance is no longer active")
        _append_event(self.connection, state, kind, evidence=evidence, manifests=manifests, activate=activate)
        self.state = state
        return state
    def write(self, evidence: Sequence[EvidenceWrite]) -> None:
        """Make evidence readable inside this transaction before its event."""
        _insert_evidence(self.connection, evidence)
    def evidence(self, evidence_id: str | None) -> JsonObject | None:
        if not evidence_id:
            return None
        row = self.connection.execute(
            "SELECT document_json FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            return None
        value = _document(self.connection, str(row["document_json"]))
        return value if isinstance(value, dict) else None
    def manifest(self, manifest_id: str | None) -> dict[str, str] | None:
        return _manifest_from(self.connection, manifest_id)
@contextlib.contextmanager
def mutation(
    identity: RepoIdentity, *, expected_candidate_tree: str | None = None,
) -> Iterator[LedgerMutation]:
    with _connection(identity) as connection:
        _begin_write(connection)
        try:
            transaction = LedgerMutation(connection, identity)
            yield transaction
            if (expected_candidate_tree is not None
                    and _active_candidate_tree(identity) != expected_candidate_tree):
                raise LedgerError("active candidate changed during workflow mutation")
            if CHECK_ONLY.get():
                connection.rollback()
            else:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
def read_active(identity: RepoIdentity) -> JsonObject | None:
    if not _store_exists(identity):
        return None
    with _connection(identity) as connection:
        state = _active(connection)
        if state is not None:
            _validate_state_identity(identity, state)
        return state
def read_evidence(identity: RepoIdentity, evidence_id: str) -> JsonObject | None:
    if _store_exists(identity):
        with _connection(identity) as connection:
            row = connection.execute(
                """SELECT evidence_id, workflow_id, kind, schema_version, recorded_at, document_json
                   FROM evidence WHERE evidence_id = ?""",
                (evidence_id,),
            ).fetchone()
            if row is not None:
                return {
                    "evidenceId": str(row["evidence_id"]),
                    "workflowId": str(row["workflow_id"]),
                    "kind": str(row["kind"]),
                    "schemaVersion": int(row["schema_version"]),
                    "recordedAt": str(row["recorded_at"]),
                    "document": _document(connection, str(row["document_json"])),
                }
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
        where = "WHERE workflow_id = ?" if workflow_id else ""
        params: tuple[object, ...] = (workflow_id,) if workflow_id else ()
        events = []
        for row in connection.execute(
            f"""SELECT event_id, workflow_id, kind, recorded_at, state_schema_version,
                       policy_version, activates_workflow
                FROM workflow_events {where} ORDER BY event_id""",
            params,
        ).fetchall():
            event_id = int(row["event_id"])
            events.append({
                "eventId": event_id,
                "workflowId": str(row["workflow_id"]),
                "kind": str(row["kind"]),
                "recordedAt": str(row["recorded_at"]),
                "stateSchemaVersion": int(row["state_schema_version"]),
                "policyVersion": int(row["policy_version"]),
                "activatesWorkflow": bool(row["activates_workflow"]),
                "evidenceIds": [str(item[0]) for item in connection.execute(
                    "SELECT evidence_id FROM event_evidence WHERE event_id = ? ORDER BY evidence_id", (event_id,))],
                "manifestIds": [str(item[0]) for item in connection.execute(
                    "SELECT manifest_id FROM event_manifests WHERE event_id = ? ORDER BY manifest_id", (event_id,))],
            })
        return {"events": events}
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
    # A snapshot-era ledger is read where it keeps state, never migrated by a report.
    state = ("(SELECT latest.state_json FROM workflow_events AS latest WHERE latest.workflow_id = "
             "workflow.workflow_id ORDER BY latest.event_id DESC LIMIT 1)" if _snapshot_era(connection)
             else "workflow.state_json")
    rows = connection.execute(
        f"""SELECT workflow.workflow_id, workflow.repo_key, workflow.slug, {state} AS state_json,
                  MAX(event.event_id) AS latest_event
           FROM workflows AS workflow JOIN workflow_events AS event USING (workflow_id)
           GROUP BY workflow.workflow_id
           ORDER BY latest_event DESC, workflow.workflow_id DESC"""
    ).fetchall()
    if len(rows) != int(connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]):
        raise LedgerError("authoritative workflow history is incomplete")
    items = []
    for row in rows:
        workflow_id = str(row["workflow_id"])
        if str(row["repo_key"]) != expected_repo_key:
            raise LedgerError("workflow row repository identity does not match its state slot")
        _state(row)
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
                connection.commit()
                return RetentionApplyResult("applied", current=current)
            except (sqlite3.DatabaseError, LedgerError, ValueError) as exc:
                connection.rollback()
                return RetentionApplyResult("failed", error=str(exc))
    except (OSError, sqlite3.DatabaseError) as exc:
        return RetentionApplyResult("failed", error=str(exc))
