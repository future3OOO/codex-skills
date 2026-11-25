"""Retire workflow state that no pass can still need.

Report-first and fail-closed: every artifact is classified as retained,
removable, or skipped with a reason, and only an explicit apply deletes. What
this cannot confidently identify and order, it keeps. Growth is the tolerable
failure; deleting a live pass's evidence is not.

Estate-wide, unlike every operation in `workflow_state`, which is scoped to one
repository. That difference is why this is its own module, and it owns the
retention policy so a later persistence layer can preserve the same rule.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ._workflow_db import (
    DATABASE_FILES,
    DATABASE_NAME,
    WorkflowRetentionItem,
    apply_retention,
    retention_inventory,
)
from .state_store import codex_home, read_json

# Measured review chains commonly span two to four passes, against a current
# worst-case accumulation of fourteen. One owner for the constant, by contract.
RETAINED_HISTORIES = 4
# Wrapper-written session files: {repository key}-{slug}-{phase}-{instance}.{provider}.sid and its .held
# sibling. Only the instance id is parsed; slugs may themselves contain "-".
ADVISOR_SESSIONS = "_advisor-sessions"
SID = re.compile(r"-([0-9a-f]{32})\.(?:codex|claude)\.(?:sid|held)\Z")
# A root that cannot be confirmed present or absent proves nothing about its repository.
INDETERMINATE = "indeterminate"


def _walkable(directory: Path) -> bool:
    """A real, non-symlinked directory this estate may traverse.

    No writer creates symlinked directories, and following one could carry a
    destructive walk outside the state root.
    """
    return directory.is_dir() and not directory.is_symlink()


def _walk(directory: Path):
    """The traversable directory's children, or nothing at all.

    A missing or symlinked directory yields no children rather than raising,
    so every estate walk shares one guard instead of repeating it.
    """
    if _walkable(directory):
        yield from sorted(directory.iterdir())


def _root_state(root: str) -> str:
    """One owner for repository-root liveness: live, dead, or INDETERMINATE.

    Confirmed absence is the only stat failure that means dead; an unreachable
    root (permissions, I/O, a stale mount) proves nothing, and ValueError
    covers roots that can never be paths, like embedded NULs.
    """
    try:
        Path(root).stat()
    except (FileNotFoundError, NotADirectoryError):
        return "dead"
    except (OSError, ValueError):
        return INDETERMINATE
    return "live"


def _workflow_entries(
    inventory: tuple[WorkflowRetentionItem, ...],
) -> list[dict[str, object]]:
    """Apply estate retention policy to ledger-owned history facts."""
    historical = [item.workflow_id for item in inventory if not item.active]
    keep = set(historical[:RETAINED_HISTORIES])
    entries: list[dict[str, object]] = []
    for item in inventory:
        if item.active:
            decision, reason = "retained", "active-workflow"
        elif item.workflow_id in keep:
            decision, reason = "retained", "recent-history"
        else:
            decision, reason = "removable", "beyond-retention"
        entries.append({
            "workflowId": item.workflow_id,
            "slug": item.slug,
            "latestEventId": item.latest_event_id,
            "decision": decision,
            "reason": reason,
        })
    return entries


def _apply_database(
    database: Path,
    inventory: tuple[WorkflowRetentionItem, ...],
    workflows: list[dict[str, object]],
) -> str:
    """Map the ledger transaction outcome onto estate-owned report reasons."""
    remove_ids = {
        str(entry["workflowId"])
        for entry in workflows
        if entry["decision"] == "removable"
    }
    result = apply_retention(database, database.parent.name, inventory, remove_ids)
    if result.status == "applied":
        for entry in workflows:
            if entry["decision"] == "removable":
                entry["decision"] = "removed"
        return "applied"
    if result.status == "changed":
        workflows[:] = _workflow_entries(result.current)
    reason = {
        "busy": "busy-database",
        "changed": "reclassified-under-lock",
        "not-authoritative": "database-not-authoritative",
    }.get(result.status, f"database-apply-failed: {result.error or 'unknown failure'}")
    for entry in workflows:
        if entry["decision"] == "removable":
            entry["decision"], entry["reason"] = "skipped", reason
    return "skipped"


def _sqlite_entries(slot: Path) -> list[dict[str, str]]:
    return [{"path": child.name, "decision": "retained", "reason": "authoritative-database" if child.name in DATABASE_FILES else "pass-cache"}
            for child in sorted(slot.iterdir())]


def _retire_sessions(root: Path, retired: dict[str, str], pinned: set[str], apply: bool) -> list[dict[str, str]]:
    """Classifiable advisor pointers follow their workflow's decision.

    Classifiable means the filename carries both the owning slot's key prefix
    and the trailing instance id of a history this run actually removed; a
    foreign prefix, missing suffix, or unknown instance retains fail-closed.
    Pointers are decided from this invocation's real outcomes, so there is no
    plan window to re-verify: a retired instance cannot be consulted again.
    """
    entries: list[dict[str, str]] = []
    for path in _walk(root / ADVISOR_SESSIONS):
        match = SID.search(path.name)
        if path.is_symlink() or not path.is_file() or match is None:
            entries.append({"path": path.name, "decision": "retained", "reason": "unowned-pointer"})
            continue
        instance = match.group(1)
        if instance in pinned:
            entries.append({"path": path.name, "decision": "retained", "reason": "follows-retained-workflow"})
        elif instance not in retired:
            entries.append({"path": path.name, "decision": "retained", "reason": "unknown-workflow"})
        elif not path.name.startswith(f"{retired[instance]}-"):
            entries.append({"path": path.name, "decision": "retained", "reason": "foreign-repository"})
        elif not apply:
            entries.append({"path": path.name, "decision": "removable", "reason": "follows-removed-workflow"})
        else:
            try:
                path.unlink()
                entries.append({"path": path.name, "decision": "removed", "reason": "follows-removed-workflow"})
            except OSError as exc:
                entries.append({"path": path.name, "decision": "skipped", "reason": f"unlink-failed: {exc}"})
    return entries


def _retire_associations(root: Path, apply: bool) -> list[dict[str, str]]:
    """Session-association markers follow their repository's liveness.

    Each sessions/<session>/<key>.json embeds the repository identity its
    writer recorded; a confirmed-absent root retires the marker, anything
    else - a live root, a mismatched filename, malformed or symlinked data -
    is preserved and reported.
    """
    entries: list[dict[str, str]] = []
    for session in _walk(root / "sessions"):
        for marker in _walk(session):
            name = f"{session.name}/{marker.name}"
            document = read_json(marker) if marker.is_file() and not marker.is_symlink() else None
            repo = document.get("repo") if isinstance(document, dict) else None
            root_value = repo.get("root") if isinstance(repo, dict) else None
            if (not isinstance(root_value, str)
                    or marker.name != f"{repo.get('key')}.json"
                    or _root_state(root_value) != "dead"):
                entries.append({"path": name, "decision": "retained", "reason": "unowned-or-live"})
            elif not apply:
                entries.append({"path": name, "decision": "removable", "reason": "dead-repository"})
            else:
                try:
                    marker.unlink()
                    entries.append({"path": name, "decision": "removed", "reason": "dead-repository"})
                except OSError as exc:
                    entries.append({"path": name, "decision": "skipped", "reason": f"unlink-failed: {exc}"})
    return entries


def prune(root: Path | None = None, *, apply: bool = False) -> dict[str, object]:
    """Classify every artifact under the state root, deleting only when applying.

    Reporting never creates the root or a slot, so a dry run cannot bring into
    existence the state it is describing. Apply delegates transaction ordering and
    under-lock inventory revalidation to the ledger owner; a busy store is skipped
    whole and reported. A slot without a ledger is no store of this workflow's.
    """
    if root is None:
        # Resolved the way state_root() does, minus its secure_dir call: that
        # helper creates what it returns, and reporting must never do that.
        override = os.environ.get("CODEX_WORKFLOW_STATE_ROOT")
        root = Path(override).expanduser() if override else codex_home() / "state"
    slots: list[dict[str, object]] = []
    if not root.is_dir():
        return {"root": str(root), "applied": apply, "slots": slots}

    for slot in sorted(root.iterdir()):
        # Shared association stores are handled after repository slots.
        if not _walkable(slot) or slot.name.startswith("_") or slot.name == "sessions":
            continue
        try:
            database = slot / DATABASE_NAME
            if database.is_file() and not database.is_symlink():
                inventory, error = retention_inventory(database, slot.name)
                if inventory is not None:
                    workflows = _workflow_entries(inventory)
                    status = _apply_database(database, inventory, workflows) if apply else "reported"
                    slots.append({
                        "slot": slot.name,
                        "store": "sqlite",
                        "status": status,
                        "workflows": workflows,
                        "entries": _sqlite_entries(slot),
                    })
                    continue
                if error is not None:
                    slots.append({
                        "slot": slot.name,
                        "store": "unknown",
                        "status": "skipped",
                        "reason": f"database-unreadable: {error}",
                        "workflows": [],
                        "entries": [
                            {"path": child.name, "decision": "retained", "reason": "database-unreadable"}
                            for child in sorted(slot.iterdir())
                        ],
                    })
                    continue
            slots.append({"slot": slot.name, "store": "none", "status": "skipped", "reason": "no-ledger", "entries": []})
        except OSError as exc:
            slots.append({"slot": slot.name, "store": "unknown", "status": "skipped",
                          "reason": f"classification-failed: {exc}", "entries": []})

    # A pointer may follow its workflow out only when this run really removed
    # that history; any other outcome for the instance pins the pointer.
    retired: dict[str, str] = {}
    pinned: set[str] = set()
    removed_like = "removed" if apply else "removable"
    for report_slot in slots:
        for entry in [*report_slot.get("workflows", []), *report_slot.get("entries", [])]:
            instance = entry.get("workflowId")
            if not isinstance(instance, str):
                continue
            if entry["decision"] == removed_like:
                retired.setdefault(instance, report_slot["slot"])
            else:
                pinned.add(instance)
    return {"root": str(root), "applied": apply, "slots": slots,
            "advisorSessions": _retire_sessions(root, retired, pinned, apply),
            "sessions": _retire_associations(root, apply)}
