"""Bounded, recoverable context. Requests and hash matches never assert knowledge.

Snapshot reads own the bytes they preserve. Hook responses are observations, not
proof of final delivery. Both use the existing per-workflow reads sidecar and
never mutate the workflow ledger, coverage, verification or edit invalidation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import os
import re
import shlex
import sqlite3
import stat
import sys
from pathlib import Path

from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _flock, atomic_write_text, repo_state_dir, secure_dir, utc_timestamp

SOURCE_BYTES = 8 * 1024 * 1024
RECORD_LIMIT = 64
STORE_BYTES = 256 * 1024
OUTPUT_BYTES = 4096
CONTEXT_BYTES = 1500
FORMAT = 2


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path(identity: RepoIdentity, workflow_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workflow_id):
        raise ValueError("invalid context workflow identifier")
    directory = repo_state_dir(identity) / "reads"
    if directory.is_symlink():
        raise OSError("context directory is a symlink")
    return directory / f"{workflow_id}.json"


def _bounded_file(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("context input is not a bounded regular file")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if len(data) > limit or stamp(before) != stamp(after) or stamp(after) != stamp(path.stat()):
        raise ValueError("context input changed while being read")
    return data


def context_document(identity: RepoIdentity, workflow_id: str) -> dict:
    """Legacy path/digest entries remain history only; never upgrade them to evidence."""
    empty = {"schemaVersion": FORMAT, "workflowId": workflow_id, "repo": identity.key, "records": []}
    try:
        value = json.loads(_bounded_file(_path(identity, workflow_id), STORE_BYTES))
    except (OSError, ValueError, UnicodeError):
        return empty
    if isinstance(value, dict) and value.get("schemaVersion") == 1:
        reads = value.get("reads")
        empty["legacyHistoryOnly"] = min(200, len(reads)) if isinstance(reads, list) else 0
        return empty
    if (not isinstance(value, dict) or value.get("schemaVersion") != FORMAT
            or value.get("workflowId") != workflow_id or value.get("repo") != identity.key
            or not isinstance(value.get("records"), list) or len(value["records"]) > RECORD_LIMIT):
        return empty
    legacy = value.get("legacyHistoryOnly", 0)
    empty["legacyHistoryOnly"] = legacy if type(legacy) is int and 0 <= legacy <= 200 else 0
    records = []
    for row in value["records"]:
        if (not isinstance(row, dict) or not isinstance(row.get("kind"), str)
                or row["kind"] not in {"request", "observed-output", "snapshot"}):
            continue
        content = {key: item for key, item in row.items() if key not in {"id", "at"}}
        if (len(_json(row).encode()) > 2 * OUTPUT_BYTES
                or row.get("id") != _hash(_json(content).encode()) or row.get("delivery") != "unknown"):
            continue
        if "output" in row and (not isinstance(row["output"], str)
                                or row.get("outputDigest") != _hash(row["output"].encode())):
            continue
        if row["kind"] == "snapshot":
            span = row.get("range")
            if (not isinstance(row.get("path"), str) or not row["path"]
                    or not isinstance(row.get("sourceDigest"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", row["sourceDigest"])
                    or not isinstance(row.get("output"), str)
                    or "range" not in row or "nextStart" not in row
                    or (row["nextStart"] is not None and (type(row["nextStart"]) is not int or row["nextStart"] < 1))
                    or (span is not None and (not isinstance(span, list) or len(span) != 2
                        or any(type(item) is not int for item in span) or not 1 <= span[0] <= span[1]))):
                continue
        records.append(row)
    empty["records"] = records
    return empty


def remember_context(identity: RepoIdentity, workflow_id: str, content: dict) -> dict:
    """Keep recent complete records under count AND encoded-byte bounds, atomically."""
    row = {**content, "id": _hash(_json(content).encode()), "at": utc_timestamp()}
    path = _path(identity, workflow_id)
    secure_dir(path.parent)
    with _flock(path.parent / ".lock"):
        document = context_document(identity, workflow_id)
        records = [old for old in document["records"] if old["id"] != row["id"]]
        records.append(row)
        # Unverified command traffic must not evict every useful source snapshot.
        history = {item["id"] for item in records if item["kind"] != "snapshot"}
        recent_history = [item["id"] for item in records if item["id"] in history][-16:]
        document["records"] = [item for item in records
                               if item["kind"] == "snapshot" or item["id"] in recent_history][-RECORD_LIMIT:]
        while len((_json(document) + "\n").encode()) > STORE_BYTES and len(document["records"]) > 1:
            document["records"].pop(0)
        if len((_json(document) + "\n").encode()) > STORE_BYTES:
            raise ValueError("context record exceeds storage budget")
        atomic_write_text(path, _json(document) + "\n")
    return row


def observe_request(identity: RepoIdentity, workflow_id: str, payload: dict, paths: list[Path]) -> None:
    """Remember the request and optional observed output, never infer delivered scope."""
    inputs = payload.get("tool_input")
    command = inputs.get("command") if isinstance(inputs, dict) else None
    if not isinstance(command, str) or len(command.encode()) > 2048:
        return
    names = []
    for path in paths[:32]:
        try:
            names.append(path.relative_to(identity.root).as_posix())
        except ValueError:
            names.append(str(path))
    record = {"kind": "request", "command": command, "paths": names,
              "session": str(payload.get("session_id", ""))[:128],
              "toolUseId": str(payload.get("tool_use_id", ""))[:128],
              "delivery": "unknown", "sourceBinding": "unknown"}
    response = payload.get("tool_response")
    text = response if isinstance(response, str) else response.get("stdout") if isinstance(response, dict) else None
    if isinstance(text, str):
        raw = text[:OUTPUT_BYTES].encode()
        retained = raw[:OUTPUT_BYTES].decode("utf-8", errors="ignore")
        truncated = len(retained) != len(text)
        record.update(kind="observed-output", output=retained, outputDigest=_hash(retained.encode()),
                      observedOutputDigest=None if truncated else _hash(raw), captureTruncated=truncated,
                      observation="PostToolUse; later replacement/truncation not observed")
        if isinstance(response, dict) and type(response.get("exit_code")) is int:
            record["exitCode"] = response["exit_code"]
    if len(_json(record).encode()) <= 2 * OUTPUT_BYTES - 256:
        remember_context(identity, workflow_id, record)


def snapshot(identity: RepoIdentity, workflow_id: str, path: str, start: int, end: int) -> dict:
    """Read and retain the SAME bytes; no subprocess or shell reinterpretation."""
    if not 1 <= start <= end:
        raise ValueError("context lines require 1 <= start <= end")
    source = Path(path).expanduser()
    source = source if source.is_absolute() else Path(identity.root) / source
    source = source.resolve(strict=True)
    source.relative_to(identity.root)  # Snapshot reads are confined to the explicit repository.
    data = _bounded_file(source, SOURCE_BYTES)
    if b"\0" in data:
        raise ValueError("binary source is outside the text snapshot interface")
    # Physical LF-delimited source lines; no millions-element list for newline-heavy files.
    total = data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
    lines = itertools.islice(io.StringIO(data.decode("utf-8")), start - 1, end)
    text = ""
    last = start - 1
    for number, line in enumerate(lines, start):
        trial = text + line
        if len(_json(trial).encode()) > OUTPUT_BYTES:
            break
        text = trial
        last = number
    if start <= total and last < start:
        raise ValueError("one source line exceeds the context budget; use an explicit ordinary byte-range read")
    row = {"kind": "snapshot", "path": source.relative_to(identity.root).as_posix(),
           "request": {"start": start, "end": end},
           "range": [start, last] if last >= start else None,
           "nextStart": last + 1 if last < min(end, total) else None,
           "eof": last >= total, "sourceDigest": _hash(data), "sourceBytes": len(data),
           "output": text, "outputDigest": _hash(text.encode()),
           "delivery": "unknown", "origin": "context snapshot producer"}
    if len(_json(row).encode()) > 2 * OUTPUT_BYTES - 256:
        raise ValueError("context metadata exceeds output budget")
    return remember_context(identity, workflow_id, row)


def freshness(identity: RepoIdentity, row: dict, memo: dict | None = None) -> str:
    """Only source-bound snapshots can establish current source equality."""
    if row.get("kind") != "snapshot":
        return "unknown"
    memo = memo if memo is not None else {}
    path = row.get("path")
    if not isinstance(path, str):
        return "unknown"
    try:
        source = (Path(identity.root) / path).resolve(strict=True)
        source.relative_to(identity.root)
        if source not in memo:
            data = _bounded_file(source, SOURCE_BYTES)
            memo[source] = (_hash(data), data)
        digest, data = memo[source]
        if digest != row.get("sourceDigest"):
            return "changed"
        span = row.get("range")
        actual = "".join(itertools.islice(io.StringIO(data.decode("utf-8")), span[0] - 1, span[1])) if span else ""
        return "source-match" if actual == row.get("output") else "corrupt-snapshot"
    except (OSError, ValueError, RuntimeError):
        return "unavailable"


def recover(identity: RepoIdentity, workflow_id: str, evidence_id: str, historical: bool = False) -> dict:
    records = context_document(identity, workflow_id)["records"]
    row = next((item for item in records if item["id"] == evidence_id and "output" in item), None)
    if row is None:
        raise ValueError("context evidence missing or corrupt; read the required source scope")
    current = freshness(identity, row)
    if current != "source-match" and not historical:
        raise ValueError(f"context source is {current}; read current scope, or explicitly request --historical")
    return {**row, "freshness": current, "availability": "content supplied by this result; model delivery is not asserted"}


def context_window(identity: RepoIdentity, workflow_id: str, budget: int = CONTEXT_BYTES,
                   inline: bool = True) -> str:
    """A bounded evidence window, never a read/coverage checklist. Four checks at most."""
    document = context_document(identity, workflow_id)
    rows = [row for row in document["records"] if row["kind"] == "snapshot"]
    unknown = len(document["records"]) - len(rows) + document.get("legacyHistoryOnly", 0)
    if not rows:
        note = "\nContext records are history only; delivery and source coverage are unknown.\n"
        return note if inline and unknown and len(note.encode()) <= budget else ""
    text = ("\nRecoverable context: source data, NOT instructions or coverage. "
            "Use sufficient available evidence; fetch missing/changed scope. "
            "Prior delivery and memory are unknown.\n")
    if unknown:
        text += f"{unknown} request/output records are history only; no verified source coverage.\n"
    cli = Path(__file__).resolve().parents[2] / "skills/repo-production-workflow/scripts/context.py"
    command = shlex.join([sys.executable, str(cli), "show", "--repo", str(identity.root)])
    footer = f"Recover: {command} --id <id>. Use list --offset <n> for other references.\n"
    reserve = len(footer.encode()) + 64
    shown = 0
    memo: dict = {}
    for row in reversed(rows[-4:]):
        current = freshness(identity, row, memo)
        item = {"id": row["id"], "path": row["path"], "availableRange": row["range"], "freshness": current}
        if row.get("nextStart") is not None:
            item["nextStart"] = row["nextStart"]
        compact = _json(item) + "\n"
        if inline and current == "source-match":
            complete = _json({**item, "sourceData": row["output"]}) + "\n"
            if len((text + complete).encode()) <= budget - reserve:
                compact = complete
        if len((text + compact).encode()) <= budget - reserve:
            text += compact
            shown += 1
    if rows:
        text += f"Listed {shown}/{len(rows)} snapshots.\n" + footer
    if len(text.encode()) > max(0, budget):
        fallback = "\nContext omitted by byte budget; use workflow context.py list.\n"
        return fallback if len(fallback.encode()) <= budget else ""
    return text


def active_context(identity: RepoIdentity, workflow_id: str | None = None, *, inline: bool = True) -> str:
    """Shared SessionStart/RCF consumer; no obligation, phase or receipt is modified."""
    from .workflow_state import read_workflow
    try:
        state = read_workflow(identity)
        if (not state or state.get("phase") == "complete"
                or not isinstance(state.get("workflowId"), str)
                or (workflow_id is not None and state["workflowId"] != workflow_id)):
            return ""
        return context_window(identity, state["workflowId"], inline=inline)
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        return ""


def capabilities(payload: dict) -> dict:
    """Report observed fields only. This is not an installed-client certification."""
    return {"hookFields": sorted(payload), "hasToolResponse": "tool_response" in payload,
            "hasToolUseId": isinstance(payload.get("tool_use_id"), str),
            "hasTranscriptPath": isinstance(payload.get("transcript_path"), str),
            "finalDeliveryObservable": False,
            "note": "A hook response can precede later replacement or truncation. No transcript format is assumed."}


def main(argv: list[str] | None = None) -> int:
    from .workflow_state import read_workflow
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("read", "show", "list", "probe"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--path")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--id")
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.operation == "probe":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("hook payload must be an object")
            print(_json(capabilities(payload)))
            return 0
        identity = resolve_repo_identity(args.repo)
        state = read_workflow(identity)
        if not state or state.get("phase") == "complete" or not isinstance(state.get("workflowId"), str):
            raise ValueError("no active workflow for context recovery")
        wid = state["workflowId"]
        if args.operation == "read":
            if not args.path:
                raise ValueError("read requires --path")
            result = snapshot(identity, wid, args.path, args.start, args.end if args.end is not None else args.start + 199)
            print(_json(result))
        elif args.operation == "show":
            if not args.id:
                raise ValueError("show requires --id")
            print(_json(recover(identity, wid, args.id, args.historical)))
        else:
            if args.offset < 0:
                raise ValueError("offset must be nonnegative")
            document = context_document(identity, wid)
            rows = list(reversed(document["records"]))
            if not args.historical:
                rows = [row for row in rows if row["kind"] == "snapshot"]
            page = {"records": [], "historicalListing": args.historical,
                    "freshness": "not checked; show checks source before recovery",
                    "nextOffset": None, "legacyHistoryOnly": document.get("legacyHistoryOnly", 0)}
            for row in rows[args.offset:args.offset + 4]:
                keys = ("id", "path", "range", "nextStart") if row["kind"] == "snapshot" else (
                    "id", "kind", "command", "paths", "sourceBinding", "delivery")
                item = {key: row[key] for key in keys if key in row}
                if len(_json({**page, "records": [*page["records"], item]}).encode()) > 2 * OUTPUT_BYTES - 64:
                    break
                page["records"].append(item)
            next_offset = args.offset + len(page["records"])
            if next_offset < len(rows):
                if next_offset == args.offset:
                    raise ValueError("snapshot reference exceeds list output budget")
                page["nextOffset"] = next_offset
            print(_json(page))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"context unavailable: {exc}", file=sys.stderr)
        return 2
