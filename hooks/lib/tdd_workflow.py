"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import ast
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from functools import lru_cache, partial
from itertools import chain
from collections import Counter
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import stat
from statistics import median
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, TypeVar

from . import behavior_map
from . import SESSION_LOG, state_root
from ._workflow_db import CHECK_ONLY, cached_arms, history, read_active
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import CanonicalRoot, RepoIdentity, resolve_repo_identity
from .state_store import _EXECUTION, _comparison_scope, _active_candidate_tree, _git, is_test_path, read_blobs, blob_reader, tree_manifest, utc_timestamp, repo_state_dir, _flock, atomic_write_bytes, atomic_write_json, read_json
from .workflow_state import (
    NO_INSTANCE_ID, WorkflowError, _bound_instance_state,
    commit_tdd, commit_findings, evidence_document, instance_id, operation_receipt, rejections, _readiness_lines,
)

JsonObject = dict[str, object]
_Input = TypeVar("_Input")
MAX_CASE_SECONDS = 30.0
SESSION_SECONDS = 300.0  # the limit of a case that runs a real model session
NAME = re.compile(r"(?:[^\W\d]|\$)[\w$]*")
@lru_cache(maxsize=128)
def _changed_entries(identity: RepoIdentity, before: str, after: str) -> dict[str, tuple[str, str]]:
    raw = _git(identity, "diff", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff", "-z", before, after).split(b"\0")
    return {os.fsdecode(raw[index + 1]): tuple(os.fsdecode(oid) for oid in raw[index].split()[2:4])
            for index in range(0, len(raw) - 1, 2)
            if b"160000" not in (raw[index].split()[0].lstrip(b":"), raw[index].split()[1])}


def current_map(identity: RepoIdentity, state: JsonObject, *, fresh: bool = True) -> tuple[list[JsonObject] | None, JsonObject | None]:
    fields = ("preflightLatestEvidence",) if state.get("preflight") == "pending" else ()
    for field in (*fields, "tddEvidence", "preflightEvidence"):
        document = evidence_document(identity, state.get(field))
        items = behavior_map.recorded_map(document)
        if items is not None:
            if fresh:
                refresh_proof(identity, items, runs=(document or {}).get("runs", []))
            return items, document if "behaviorMap" in document else None
    return None, None


@_comparison_scope()
def refresh_proof(identity: RepoIdentity, items: list[JsonObject], *, candidate: str | None = None, runs: list[JsonObject] = ()) -> None:
    """Judge current inputs with the recorded frozen probe files."""
    freshness, controls = {}, {run.get("candidateKey"): run for run in runs}
    def fresh(proof, target):
        key = (proof["candidateKey"], target)
        if key not in freshness:
            try:
                execution, command = proof["execution"], shlex.split(proof["command"])  # as recorded: the reader's own environment is no input
                while re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
                    del command[0]
                arms = proof["arms"]
                sizes = _growth(execution)
                commands = [[arg.replace("{size}", str(size)) for arg in command] for size in (sizes[1], sizes[0], sizes[0], sizes[1])] if sizes else [command, command]
                sources = (proof["originalTree"], proof["originalTree"], target, target) if sizes else (proof["originalTree"], target)
                checked = [_input_identity(identity, execution, arm, recorded=True) for arm in arms]
                contexts = [_execution_key(identity, source, proof.get("probeTree", proof["candidateTree"]), _comparison_files(identity, proof), value, proof["timeout"], execution) for source, value in zip(sources, commands)]
                freshness[key] = {"fresh": len(arms) == len(contexts) and not _stale(identity, proof) and all(inputs and arm.get("contextKey") == context and arm.get("inputs") == inputs
                                               and arm.get("key") == hashlib.sha256((context + inputs).encode()).hexdigest()
                                               for arm, inputs, context in zip(arms, checked, contexts)),
                                  "freshnessReason": "execution inputs changed or unrecorded"}
            except (WorkflowError, OSError, RuntimeError):
                freshness[key] = {"fresh": False, "freshnessReason": "recorded execution inputs unavailable"}
        return freshness[key]
    for item in items:
        if proof := item.get("comparison"):
            candidate = candidate or _active_candidate_tree(identity)
            proof.update(fresh(proof, candidate))
            control = controls.get(proof.get("controlKey"))
            good = proof["arms"][-1] if proof["arms"][-1]["outcome"] == "passed" else proof["arms"][0]
            proof["sensitive"] = (proof["arms"][0]["outcome"] == "failed" and proof["arms"][-1]["outcome"] == "passed") or bool(_growth(proof.get("execution") or {})) or bool(control and proof.get("breakOid") and fresh(control, control["candidateTree"])["fresh"]
                                      and control["arms"][0]["key"] == good["key"]
                                      and control["arms"][-1]["outcome"] == "failed" and not control["timedOut"]
                                      and (candidate == control["originalTree"] or
                                           _source_input(identity, candidate, control["probeTree"], tuple(_comparison_files(identity, control))) ==
                                           _source_input(identity, control["originalTree"], control["probeTree"], tuple(_comparison_files(identity, control)))))


def _scratch(identity: RepoIdentity, name: str) -> Path | None:
    """The regular file outside the checkout that `name` resolves to; an argument too long to name a file names none."""
    path = (Path(identity.root) / name).resolve()
    return path if os.path.isfile(path) and not path.is_relative_to(identity.root) else None


def _probe_files(command: list[str], identity: RepoIdentity, support: list[str], virtual: list[str] = ()) -> list[str]:
    root = Path(identity.root)
    names = _git(identity, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = {name for raw in names.split(b"\0") if raw
             and is_test_path(name := os.fsdecode(raw)) and (root / name).exists()}
    for argument in command[1:]:
        value = argument.split("::", 1)[0]
        if "=" in value:
            value = value.partition("=")[2]
        if value and os.path.lexists(root / value):
            name = os.path.relpath(root / value, root)
            if is_test_path(name) or not (root / name).resolve().is_relative_to(root):
                paths.add(name)
    paths.update(os.path.relpath(os.path.normpath(root / name), root) for name in support if not _scratch(identity, name))
    for name in paths:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise WorkflowError("probe support is not a regular file: " + name)
        if not is_test_path(name):
            raise WorkflowError("unsupported probe: production source cannot be overlaid as test support: " + name)
    return sorted(paths | set(virtual))


def _comparison_files(identity: RepoIdentity, probe: JsonObject) -> list[str]:
    """Derived once a comparison scope: each arm, context and freshness check of a case asks again."""
    def derive():
        declared = _command_files(identity, probe) | {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root)
                    for name in probe.get("support") or []}
        return [path for path in probe["probeFiles"] if path.startswith(PROBES) or is_test_path(path) or path in declared]  # a frozen scratch file lies below the tests
    return _sample_input(("files", probe["command"], *probe["probeFiles"], "", *(probe.get("support") or [])), derive)


def _environment() -> dict[str, str]:
    owned = {"SHLVL", "_", "PWD", "OLDPWD", "PYTHONHOME", "TMPDIR", "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE",
             "CODEX_TOOL_CALL_ID", "CODEX_WORKFLOW_EXEC_TRACE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_CI",
             "CODEX_VERSION", "CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS", "ADVISOR_ACTIVE",
             "GH_PAGER", "GIT_PAGER", "PAGER", "NO_COLOR", "LC_ALL", "LC_CTYPE"}
    return {**{key: value for key, value in os.environ.items() if key not in owned and not key.startswith("CODEX_ADVISOR_")},
            "SHLVL": "0", "PYTHONDONTWRITEBYTECODE": ""}


def _executable(identity: RepoIdentity, command: str, environment: dict[str, str] | None = None, case: JsonObject | None = None) -> str:
    if os.path.dirname(command):
        return os.path.normpath(os.path.join(identity.root, command))
    search = os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path(environment))
    executable = shutil.which(command, path=search)
    if executable is None:
        raise WorkflowError("probe executable is unavailable: " + (f"{case.get('behaviorId') or '-'}: {case['command']}" if case else command))
    return executable


@lru_cache(maxsize=128)
def _tree_entries(identity: RepoIdentity, tree: str) -> dict[str, tuple[str, str]]:
    entries = {}
    for record in _git(identity, "ls-tree", "-r", "-z", tree).split(b"\0"):
        if record:
            header, path = record.split(b"\t", 1)
            mode, kind, oid = header.split()
            if kind in {b"blob", b"commit"}:
                entries[os.fsdecode(path)] = (mode.decode(), oid.decode())
    return entries


def _reader(identity: RepoIdentity):
    """The comparison scope's one object reader."""
    scope = _EXECUTION.get()
    if scope["reader"] is None:
        scope["reader"] = scope["resources"].enter_context(blob_reader(Path(identity.root)))
    return scope["reader"]


@lru_cache(maxsize=128)
def _source_base(identity: RepoIdentity, source: str) -> bytes:
    """A source's non-test entries, classified once for every case that runs on it."""
    return hashlib.sha256(json.dumps({path: value for path, value in _tree_entries(identity, source).items()
                                      if not is_test_path(path)}, sort_keys=True).encode()).hexdigest().encode()


def _entry(identity: RepoIdentity, tree: str, name: str) -> tuple[str, str] | None:
    """The mode and object `tree` holds at `name`, read a directory at a time: a case's few frozen files list no whole tree."""
    if _EXECUTION.get() is None:  # no comparison scope holds an object reader
        return _tree_entries(identity, tree).get(name)
    def children(oid):
        def load():
            data, result, offset = _reader(identity)(oid, kind=b"tree"), {}, 0
            if data is None:
                raise WorkflowError("source tree unavailable: " + oid)
            width = len(oid) // 2
            while offset < len(data):
                end = data.index(b"\0", offset)
                mode, name = data[offset:end].split(b" ", 1)
                result[os.fsdecode(name)] = (mode.decode().zfill(6), data[end + 1:end + 1 + width].hex())
                offset = end + 1 + width
            return result
        return _sample_input(("tree", identity.root, oid), load)
    value = ("040000", tree)
    for part in (() if name == "." else name.split("/")):
        value = children(value[1]).get(part) if value and value[0] == "040000" else None
    return value


@lru_cache(maxsize=128)
def _source_input(identity: RepoIdentity, source: str, probe: str, files: tuple[str, ...]):
    frozen = _sample_input(("frozen", identity.root, probe, files), lambda: {name: value for name in files
                           if (value := (_tree_entries(identity, probe).get(name) if probe == source else _entry(identity, probe, name))) is not None})
    return _source_base(identity, source) + json.dumps(frozen, sort_keys=True).encode()


def _sample_input(key: tuple[object, ...], read: Callable[[], _Input]) -> _Input:
    scope = _EXECUTION.get()
    if scope is None:
        return read()
    if key not in scope["samples"]:
        scope["samples"][key] = read()
    return scope["samples"][key]


def _tool_input(name: str) -> tuple:
    try:
        info = os.stat(name)
        value = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except FileNotFoundError:
        value = None
    return name, os.path.realpath(name), value


def _node_inputs(root: Path) -> list[tuple]:
    metadata, pending, seen = [], [root / "node_modules"], set()
    while pending:
        modules = pending.pop()
        if modules.resolve() in seen:
            continue
        seen.add(modules.resolve())
        for manifest in sorted([*modules.glob("*/package.json"), *modules.glob("@*/*/package.json")]):
            info = manifest.stat()
            metadata.append((str(manifest), info.st_ino, info.st_mtime_ns, hashlib.sha256(manifest.read_bytes()).hexdigest()))
            nested = manifest.parent / "node_modules"
            if nested.is_dir():
                pending.append(nested)
    return metadata


def _input_identity(identity: RepoIdentity, execution: JsonObject, arm: JsonObject, recorded: bool = False) -> str:
    """The arm's tools and lookups as they stand now, under this process's environment and search path, or, for a
    reader judging `recorded` proof, under the ones `execution` recorded."""
    tools = arm.get("tools", [])
    scope = None if recorded else _EXECUTION.get()
    cache_key = json.dumps([identity.root, execution.get("lookup") or execution["executable"], execution.get("search"),
                            execution.get("assignments", {}), tools, arm.get("lookups", {})], sort_keys=True)
    if scope is not None and cache_key in scope["inputs"]:
        digest = scope["inputs"][cache_key]["digest"]
    else:
        current = execution if recorded else _sample_input(("environment", identity.root, tuple(sorted(execution.get("assignments", {}).items()))), lambda: _comparison_input(identity, {"command": shlex.join([
            *(f"{name}={value}" for name, value in execution.get("assignments", {}).items()), execution.get("lookup") or execution["executable"]])})[1])
        tool_inputs = [_sample_input(("tool", name), lambda name=name: _tool_input(name)) for name in tools]
        metadata = [("environment", current["environment"]), *tool_inputs]
        lookups = dict(arm.get("lookups", {}))
        origin = lookups.get("bwrap")
        if origin is not None:
            lookups = {name: current["search"] if search == origin or origin.startswith(search + os.pathsep) else search
                       for name, search in lookups.items()}
        if execution.get("lookup"):
            lookups[execution["lookup"]] = current["search"]
        metadata.extend(("lookup:" + name, _sample_input(("lookup", identity.root, name, search),
                        lambda name=name, search=search: _executable(identity, name, {"PATH": search}))) for name, search in sorted(lookups.items()))
        roots = {root.parent.parent for name, target, _ in tool_inputs for root in (Path(name), Path(target))}
        metadata.extend(sorted({entry for root in roots for entry in _sample_input(("python-packages", root),
                        lambda root=root: [(str(path), path.stat().st_mtime_ns) for path in root.glob("lib/python*/*-packages")])}))
        metadata.extend(_sample_input(("node-packages", identity.root), lambda: _node_inputs(Path(identity.root))))
        digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
        if scope is not None:
            scope["inputs"][cache_key] = {"execution": execution, "tools": tools, "lookups": arm.get("lookups", {}), "digest": digest}
    return digest


def _runner_digest() -> str:
    return _sample_input(("runner",), lambda: hashlib.sha256(b"".join((Path(__file__).parent / name).read_bytes()
                        for name in ("tdd_workflow.py", "command_runner.py", "behavior_map.py", "state_store.py"))).hexdigest())


def _execution_key(identity: RepoIdentity, source: str, probe: str, files: list[str], command: list[str], timeout: float, execution: JsonObject) -> str:
    config = json.dumps([command, timeout, {key: value for key, value in execution.items()
                                           if key not in {"search", "lookup", "assignments"}}], sort_keys=True).encode()
    return hashlib.sha256(config + _source_input(identity, source, probe, tuple(files)) + _runner_digest().encode()).hexdigest()


def _trace_path(value: str) -> str:
    value = os.fsdecode(ast.literal_eval('b"' + value + '"')) if "\\" in value else value
    return "/" + value.lstrip("/") if value.startswith("//") else value


def _trace_records(raw):
    captured = re.findall(r"(?m)^(\d+)\s+(\d+\.\d+)\s+(.*)", raw)
    if "<unfinished ...>" not in raw:
        return sorted(captured, key=lambda record: float(record[1]))
    records, pending = [], {}
    for pid, stamp, call in captured:
        if call.endswith("<unfinished ...>"):
            pending[pid] = (stamp, call[:-16])
        elif call.startswith("<... "):
            previous = pending.pop(pid, None)
            if previous and (end := call.find(" resumed>")) >= 0:
                records.append((pid, previous[0], previous[1] + call[end + 9:]))
        else:
            records.append((pid, stamp, call))
    return sorted(records, key=lambda record: float(record[1]))


def _observed_inputs(identity: RepoIdentity, root: Path, temporary: Path, trace: Path,
                     offset: int, actual: list[str], environment: dict[str, str],
                     binding: list[str], timeout: float, runtimes: list[Path], *, lower=None, work_root=None, frozen: tuple[str, ...] = ()) -> JsonObject:
    """`frozen`: the scratch folders and temporary files the case ran with where they are, each ending in a separator.
    What it executes of them is its frozen input, no tool."""
    end = trace.stat().st_size
    with trace.open("rb") as stream:
        stream.seek(offset)
        records = _trace_records(stream.read(max(0, end - offset)).decode("utf-8", errors="surrogateescape"))
    tools = {_executable(identity, name, environment) for name in ("bwrap", "strace", "git")}
    search = [os.path.join(identity.root, entry) for entry in os.get_exec_path(environment)]
    lookups = dict.fromkeys(("bwrap", "strace", "realpath"), os.pathsep.join(search))
    directories, observed, owners = {}, set(), set()
    work_root = str(work_root or (records[0][0] if records else ""))
    groups, work = set(), {"processes": 0}
    shared = bool(os.environ.get("CODEX_WORKFLOW_EXEC_TRACE"))
    for pid, _, call in records:
        operation = call.split("(", 1)[0]
        directory = directories.setdefault(pid, [identity.root])
        changed = re.match(r"^(chdir|fchdir)\((.*)\)\s+=\s+0$", call) if operation in {"chdir", "fchdir"} else None
        if operation in {"clone", "clone3", "fork", "vfork"} and (child := re.match(r"^(?:clone3?|fork|vfork)\(.*\)\s+=\s+(\d+)$", call)):
            directories[child[1]] = directory if "CLONE_FS" in call else directory.copy()
            if pid in owners:
                owners.add(child[1])
            if pid in groups:
                groups.add(child[1])
                work["processes"] += "CLONE_THREAD" not in call
        elif changed:
            target = _trace_path(changed[2][1:-1] if changed[1] == "chdir" else changed[2].split("<", 1)[1].rsplit(">", 1)[0])
            directory[0] = os.path.join(directory[0], target)  # the kernel resolves symlinks before "..": compare resolved paths only
            if shared and pid not in owners and os.path.realpath(directory[0]) == os.path.realpath(root):
                owners.add(pid)
        if shared and pid not in owners:
            continue
        if pid == work_root and pid not in groups:
            groups.add(pid)
            work["processes"] += 1
        match = re.match(r'execve\(("(?:[^"\\]|\\.)*"),.*\)\s+=\s+(.+)', call) if operation == "execve" else None
        if not match:
            continue
        quoted, result = match.groups()
        path = Path(os.path.join(directory[0], _trace_path(quoted[1:-1])))
        if path.is_relative_to(temporary):
            continue  # recreated by this isolated operation
        observed.add((path, result))
    if not observed:
        raise WorkflowError("execution input capture unavailable; no proof recorded")
    resolved = {str(path): os.path.realpath(path) for path, _ in observed}
    names = sorted(name for name, target in resolved.items()
                   if Path(name).is_relative_to(identity.root) or Path(target).is_relative_to(identity.root))
    if names:
        resolver = _executable(identity, "realpath", environment)
        raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", resolver, "-mz", "--", *names],
                                   identity, timeout, env=environment)
        if code or timed_out:
            raise WorkflowError("execution path resolution unavailable; no proof recorded")
        resolved.update(zip(names, map(os.fsdecode, raw.rstrip(b"\0").split(b"\0")), strict=True))
        tools.add(resolver)
    for path, result in observed:
        source = path.is_relative_to(identity.root) and not any(path.is_relative_to(runtime) for runtime in runtimes)
        if result.startswith("0") and str(path) != actual[0] and str(path.parent) in search:
            prefix = os.pathsep.join(search[:search.index(str(path.parent)) + 1])
            if shutil.which(path.name, path=prefix) == str(path):
                lookups[path.name] = prefix
        target = Path(resolved.get(str(path), str(path)))
        owned = target.is_relative_to(identity.root) and not any(target.is_relative_to(runtime) for runtime in runtimes)
        physical = root / target.relative_to(identity.root) if owned else target
        if owned and lower and not physical.is_file():
            physical = lower / target.relative_to(identity.root)
        if result.startswith("0") and physical.is_file():
            with physical.open("rb") as handle:
                if handle.read(2) == b"#!":
                    tools.add(shlex.split(handle.readline().decode())[0])
        if not source or not owned:
            tools.add(str(path))
        if not owned:
            tools.add(str(target))
    return {"tools": sorted(tool for tool in tools if not (tool + os.sep).startswith(frozen)), "lookups": lookups, "work": work}


def _stamp(path: Path | str, follow: bool = False):
    try:
        info = os.lstat(path)
        info = os.stat(path) if follow and stat.S_ISLNK(info.st_mode) and os.path.exists(path) else info  # a dangling or looping link is itself
        return (info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except (FileNotFoundError, NotADirectoryError):  # a link to nothing is as gone as its target
        return None


def _advance_timestamp(path: Path, previous: int) -> None:
    if not previous:
        return
    try:
        current = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return
    if stat.S_ISREG(current.st_mode):
        os.utime(path, ns=(current.st_atime_ns, max(current.st_mtime_ns, previous) + 1_000_000_000))


def _write_entry(root: Path, name: str, mode: str, data: bytes, previous: int = 0) -> None:
    path = root / name
    before = _stamp(path)
    if before and stat.S_ISREG(before[0]):
        previous = max(previous, before[3])
    for parent in reversed(path.parents):
        if parent.is_relative_to(root) and parent != root:
            if parent.is_symlink() or parent.exists() and not parent.is_dir():
                parent.unlink()
            parent.mkdir(exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.is_symlink():
        path.unlink()
    if mode == "120000":
        if path.exists():
            path.unlink()
        path.symlink_to(os.fsdecode(data))
    else:
        path.write_bytes(data)
        path.chmod(0o755 if mode == "100755" else 0o644)
        _advance_timestamp(path, previous)


def _prepared_tree(identity: RepoIdentity, scope, source: str, probe: str, files: list[str], *, isolated=False):
    key = (source, "growth") if isolated else source
    if key not in scope["sources"]:
        cache = None if isolated else scope.get("kept", {}).get(key)
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
        work = Path(scope["resources"].enter_context(tempfile.TemporaryDirectory(
            prefix="source-", dir=cache.parent if cache else scope["root"])))
        kept = bool(cache and (cache / "tree").is_dir())
        if kept:
            os.replace(cache / "tree", work / "tree")
        root, clone = work / "tree", work / ("metadata" if kept else "tree")
        measure = bool(cache and not (cache / "wait").is_file())
        wait = (cache / "wait").read_text() == "1" if cache and not measure else bool(cache)
        old_index = (root / ".git/index").read_bytes() if kept else None
        subprocess.run(["git", "clone", "--shared", "--quiet", "--no-checkout", identity.root, str(clone)], check=True, capture_output=True)
        if kept:
            shutil.rmtree(root / ".git")
            shutil.move(clone / ".git", root / ".git")
            (root / ".git/index").write_bytes(old_index)
        alternates = root / ".git/objects/info/alternates"
        paths = alternates.read_text().splitlines()
        for path in paths:
            if path not in scope["objects"]:
                mount = scope["root"] / ("objects-" + str(len(scope["objects"])))
                mount.mkdir()
                scope["objects"][path] = mount
        snapshot = RepoIdentity(CanonicalRoot(str(root)), identity.key)
        replaced = {}
        if kept:
            names = _git(snapshot, "diff", "--name-only", "--no-ext-diff", "--no-renames", "-z", source)
            replaced = {os.fsdecode(name): before[3] for name in names.split(b"\0") if name
                        and (before := _stamp(root / os.fsdecode(name))) and stat.S_ISREG(before[0])}
        _git(snapshot, "read-tree", "--reset", "-u", source)
        for name, previous in replaced.items():
            _advance_timestamp(root / name, previous)
        if kept:
            _git(snapshot, "clean", "-fd")
        entries = {name: value for name, value in _tree_entries(identity, source).items() if value[0] != "160000"}
        base = {name: value for name, value in entries.items() if not is_test_path(name)}
        alternates.write_text("".join(str(scope["objects"][path]) + "\n" for path in paths))
        metadata = {str(path.relative_to(root)): ("100755" if path.stat().st_mode & 0o111 else "100644", path.read_bytes())
                    for path in (root / ".git").rglob("*") if path.is_file()}
        scope["sources"][key] = {"root": root, "base": base, "managed": entries, "metadata": metadata,
                                     "directories": [str(path.relative_to(root)) for path in (root / ".git").rglob("*") if path.is_dir()],
                                     "ignore": None, "read": _reader(identity), "resources": scope["resources"],
                                     "cache": cache, "source": source, "wait": wait, "measure": measure, "times": [], "temporary": scope["root"]}
    return scope["sources"][key], {path: _entry(identity, probe, path) for path in files}


def _finish_kept_trees():
    scope = _EXECUTION.get()
    for state in scope["sources"].values():
        cache = state.get("cache")
        primer = state.get("primer", state) if state["wait"] else state
        if not cache or primer.get("result", {}).get("timedOut", False):
            continue
        try:
            identity = scope["identity"]
            indexed = _tree_entries(identity, state["source"])
            desired = primer["managed"]
            changes = b"".join(os.fsencode(f"{desired.get(name, ('0', '0' * len(state['source'])))[0]} {desired.get(name, ('0', '0' * len(state['source'])))[1]}\t{name}") + b"\0"
                               for name in indexed.keys() | desired.keys() if indexed.get(name) != desired.get(name))
            if changes:
                _git(RepoIdentity(CanonicalRoot(str(primer["root"])), identity.key), "update-index", "-z", "--index-info", stdin=changes,
                     env={"GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(scope["objects"])})
            if state["measure"] and state["times"]:
                first = next((seconds for initial, seconds in state["times"] if initial), None)
                others = [seconds for initial, seconds in state["times"] if not initial]
                if first is not None and others:
                    (cache / "wait").write_text("1" if first >= 2 * median(others) else "0")
            os.replace(primer["root"], cache / "tree")
        except (OSError, RuntimeError) as error:
            print("Prepared tree not retained: " + str(error), file=sys.stderr)


def _ignored(state, name: str) -> bool:
    if name == ".git" or name.startswith(".git/"):
        return False
    if state["ignore"] is None:
        state["ignore"] = state["resources"].enter_context(subprocess.Popen(
            ["git", "-C", str(state["root"]), "check-ignore", "--no-index", "--stdin", "-z", "--verbose", "--non-matching"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={**os.environ, "GIT_FLUSH": "1"}))
        state["resources"].callback(state["ignore"].stdin.close)
    process = state["ignore"]
    process.stdin.write(os.fsencode(name) + b"\0")
    process.stdin.flush()
    fields = []
    for _ in range(4):
        field = bytearray()
        while (byte := process.stdout.read(1)) != b"\0":
            if not byte:
                raise WorkflowError("Git ignore classification unavailable; comparison not recorded")
            field.extend(byte)
        fields.append(bytes(field))
    return bool(fields[2]) and not fields[2].startswith(b"!")


@lru_cache(maxsize=16)
def _require_overlay(executable: str) -> None:
    with tempfile.TemporaryDirectory(prefix="workflow-overlay-check-") as directory:
        root = Path(directory)
        for name in ("lower", "upper", "work", "view"):
            (root / name).mkdir()
        checked = subprocess.run([executable, "--dev-bind", "/", "/", "--overlay-src", str(root / "lower"),
                                  "--overlay", str(root / "upper"), str(root / "work"), str(root / "view"),
                                  "--", "/bin/true"], capture_output=True, timeout=10)
    if checked.returncode:
        raise WorkflowError("Proof comparisons require bubblewrap 0.11 or later with working overlays: "
                            + checked.stderr.decode("utf-8", "replace").strip())


def _overlay_probe(state, frozen, restore=True):
    root, desired = state["root"], state["base"] | frozen
    for name in state["managed"].keys() - desired.keys():
        path = root / name
        if state.get("lower"):
            path.parent.mkdir(parents=True, exist_ok=True)
            os.mknod(path, stat.S_IFCHR | 0o600, os.makedev(0, 0))
        else:
            path.unlink(missing_ok=True)
    for name, (mode, oid) in desired.items():
        if state["managed"].get(name) != (mode, oid):
            data = state["read"](oid)
            if data is None:
                raise WorkflowError("frozen source object unavailable: " + oid)
            lower = _stamp(state["lower"] / name) if state.get("lower") else None
            _write_entry(root, name, mode, data, lower[3] if lower and stat.S_ISREG(lower[0]) else 0)
    state["managed"] = desired
    return {name: _stamp(root / name) for name in [*desired, *state["metadata"]]} if restore else {}


def _restore_case(state, before):
    root = state["root"]
    for name, expected in before.items():
        if _stamp(root / name) != expected:
            if name in state["metadata"]:
                mode, data = state["metadata"][name]
            else:
                mode, oid = state["managed"][name]
                data = state["read"](oid)
                if data is None:
                    raise WorkflowError("source restoration object unavailable: " + oid)
            _write_entry(root, name, mode, data, expected[3] if expected and stat.S_ISREG(expected[0]) else 0)
    for name in state["directories"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    parents = {str(parent) for name in before for parent in Path(name).parents if str(parent) != "."} | set(state["directories"])
    def clean(directory):
        for entry in list(os.scandir(directory)):
            name = str(Path(entry.path).relative_to(root))
            if name in before:
                continue
            if name not in parents and _ignored(state, name):
                continue  # compiler outputs remain; no ignored source is promoted into proof
            if entry.is_dir(follow_symlinks=False):
                clean(entry.path)
                if name not in parents and not any(os.scandir(entry.path)):
                    Path(entry.path).rmdir()
            else:
                Path(entry.path).unlink()
    clean(root)


def _execute_tree(identity: RepoIdentity, source_tree: str, prepared,
                  command: list[str], timeout: float, environment: dict[str, str], cancel, memory: int, objects, *, restore=True, passing=None, apart=()) -> JsonObject:
    state, frozen = prepared
    if cancel.is_set():
        raise WorkflowError("comparison interrupted")
    before = _overlay_probe(state, frozen, restore)
    try:
        result = _execute_case(identity, source_tree, state["root"], command, timeout, environment, cancel, memory, objects,
                               overlay=state if state.get("lower") else None, passing=passing, apart=apart)
        if restore:
            state["result"] = result
        if state.get("measure") and not result["timedOut"]:
            state["times"].append((restore, result["timing"]["executionSeconds"]))
        return result
    finally:
        if before:
            _restore_case(state, before)


def _after(previous, identity, source, prepared, *arguments, derived=False, first=False, passing=None, apart=()):
    state, frozen = prepared
    owner = state.get("owner", state)
    if previous is not None:
        previous.result()
    origin = owner["primer"] if previous is not None else owner
    with ExitStack() as resources:
        private = {**state, "root": origin["root"], "ignore": None, "metadata": origin["metadata"], "managed": dict(origin["managed"])}
        if not first or not state["wait"]:
            directory = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="case-", dir=state["temporary"])))
            for name in ("upper", "work"):
                (directory / name).mkdir()
            private.update(root=directory / "upper", lower=origin["root"], work=directory / "work")
        if first and state["wait"]:
            owner["primer"] = private
        if derived:
            index = private["root"] / ".git/index"
            index.parent.mkdir(parents=True, exist_ok=True)
            _git(RepoIdentity(CanonicalRoot(str(origin["root"])), identity.key), "read-tree", "--index-output=" + str(index), source,
                 env={"GIT_INDEX_FILE": str(index), "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(original for original, _ in arguments[-1])})
        return _execute_tree(identity, source, (private, frozen), *arguments, restore=first and state["wait"], passing=passing, apart=apart)


def _outlived(passing, started: float) -> bool:
    """A break started at `started` has run past max(20 s, 10x) the passing arm it is compared with."""
    arm = passing if isinstance(passing, dict) else passing.result() if passing.done() else None
    return bool(arm) and arm["outcome"] == "passed" and time.monotonic() - started > max(20.0, 10 * arm["timing"]["executionSeconds"])


class _SessionWatch:
    """Whether a running case has started a real model session: a program it ran has held a Codex session log open for
    writing (a reader of a recorded log runs none). Read at most once a second from the trace the case writes as it runs
    (a session holds its log from its first second to its end); once seen, it stays seen."""

    def __init__(self, trace: Path, offset: int):
        self.trace, self.offset, self.partial, self.live, self.found, self.read = trace, offset, b"", set(), False, 0.0

    def seen(self) -> bool:
        if self.found or time.monotonic() - self.read < 1.0:
            return self.found
        self.read = time.monotonic()
        try:
            with self.trace.open("rb") as stream:
                stream.seek(self.offset)
                added = stream.read()
        except FileNotFoundError:  # the tracer has not started writing
            return False
        self.offset += len(added)
        *lines, self.partial = (self.partial + added).split(b"\n")
        self.live.update(int(line.split(None, 1)[0]) for line in lines if b"execve" in line and line.endswith(b"= 0"))
        for pid in list(self.live):
            try:
                descriptors = os.listdir(f"/proc/{pid}/fd")
            except (FileNotFoundError, PermissionError):  # ended, or not the runner's to inspect
                self.live.discard(pid)
                continue
            for descriptor in descriptors:
                try:
                    if SESSION_LOG.search(os.readlink(f"/proc/{pid}/fd/{descriptor}")):
                        flags = Path(f"/proc/{pid}/fdinfo/{descriptor}").read_text().split("flags:", 1)[1].split()[0]
                        if int(flags, 8) & os.O_ACCMODE != os.O_RDONLY:
                            self.found = True
                            return True
                except FileNotFoundError:  # closed since it was listed
                    continue
        return False


# A one-shot SIGALRM between wait4 retries charges the case's descendants, re-armed after max(100 ms, 50x its own cost):
# RssAnon + VmSwap bounds their anonymous memory cheaply; only above the share does proportional Pss_Anon + SwapPss
# decide, so fork-shared pages count once; a process it may not inspect keeps its RssAnon + VmSwap.
_CPU_SUPERVISOR = '''import ctypes,json,os,signal,sys,time
if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0):
    raise OSError(ctypes.get_errno(),"cannot supervise case descendants")
share=int(sys.argv[2]);over=0
pid=os.fork()
if pid==0: os.execv(sys.argv[3],sys.argv[3:])
def owned():
    found=[os.getpid()]
    for parent in found:
        try: tasks=os.listdir(f"/proc/{parent}/task")
        except FileNotFoundError: continue
        for task in tasks:
            try:
                with open(f"/proc/{parent}/task/{task}/children") as handle: children=[int(child) for child in handle.read().split()]
            except (FileNotFoundError,ProcessLookupError): continue
            found+=children
    return found[1:]
cheap=("status",("RssAnon:","VmSwap:"))
def charged(pids,*sources):
    total=0
    for child in pids:
        for name,keys in sources:
            try:
                with open(f"/proc/{child}/{name}") as handle: total+=sum(int(line.split()[1]) for line in handle if line.startswith(keys))
            except PermissionError: continue
            except (FileNotFoundError,ProcessLookupError): pass
            break
    return total*1024
def check(signum,frame):
    global over
    started=time.monotonic()
    pids=owned()
    if not over and charged(pids,cheap)>share and (used:=charged(pids,("smaps_rollup",("Pss_Anon:","SwapPss:")),cheap))>share: over=used
    for child in pids if over else ():
        try: os.kill(child,signal.SIGKILL)
        except ProcessLookupError: pass
    signal.setitimer(signal.ITIMER_REAL,max(.1,50*(time.monotonic()-started)))
signal.signal(signal.SIGALRM,check)
signal.setitimer(signal.ITIMER_REAL,.1)
cpu=0.;status=None
while True:
    try: child,result,usage=os.wait4(-1,0)
    except ChildProcessError: break
    cpu+=usage.ru_utime+usage.ru_stime
    if child==pid: status=os.waitstatus_to_exitcode(result)
signal.setitimer(signal.ITIMER_REAL,0)
with open(sys.argv[1],"w") as output: json.dump([pid,cpu,over],output)
if status<0:
    if status!=-signal.SIGKILL: signal.signal(-status,signal.SIG_DFL)
    os.kill(os.getpid(),-status)
sys.exit(status)
'''


def _execute_case(identity: RepoIdentity, source_tree: str, root: Path,
                  command: list[str], timeout: float, environment: dict[str, str], cancel, memory: int, objects, *, overlay=None, passing=None, apart=()) -> JsonObject:
    """`apart` (`_apart`): the folders the case's writes are kept apart from, and the scratch folders it runs in where they are."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="workflow-proof-") as temporary:
        snapshot = RepoIdentity(CanonicalRoot(str(root)), identity.key)
        runner_env = dict(_environment() if environment is None else environment)
        env = dict(runner_env)
        inherited = os.environ.get("CODEX_WORKFLOW_EXEC_TRACE")
        trace = Path(inherited) if inherited else Path(temporary) / "exec.trace"
        offset = trace.stat().st_size if inherited else 0
        env["CODEX_WORKFLOW_EXEC_TRACE"] = str(trace)
        env.update(PYTHONPATH=os.pathsep.join(dict.fromkeys(filter(None, (str(identity.root), *env.get("PYTHONPATH", "").split(os.pathsep))))), PWD=identity.root,
                   TMPDIR=temporary, CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        actual = [_executable(identity, command[0], env), *command[1:]]
        binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/",
                   *(arg for original, mount in objects for arg in ("--ro-bind", original, str(mount)))]
        if overlay:
            binding.extend(("--overlay-src", str(overlay["lower"]), "--overlay", str(root), str(overlay["work"]), identity.root))
        else:
            binding.extend(("--bind", str(root), identity.root))
        layers, own = apart or ((), ())
        binding.extend(arg for origin in layers for arg in ("--overlay-src", origin, "--tmp-overlay", origin))
        binding.extend(arg for key, value in env.items() if runner_env.get(key) != value for arg in ("--setenv", key, value))
        dependencies = Path(identity.root) / "node_modules"
        runtimes = [dependencies] if dependencies.is_dir() else []
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            runtimes.append(runtime)
        for index, runtime in enumerate(runtimes):
            runtime_upper = Path(temporary) / f"runtime-{index}"
            runtime_work = Path(temporary) / f"runtime-work-{index}"
            runtime_upper.mkdir()
            runtime_work.mkdir()
            binding.extend(("--overlay-src", str(runtime), "--overlay", str(runtime_upper), str(runtime_work), str(runtime)))
        process_started = time.perf_counter()
        observer = [] if inherited else ["strace", "-f", "--seccomp-bpf", "-qq", "-ttt", "-e", "trace=execve,chdir,fchdir,clone,clone3,fork,vfork",
                    "-e", "status=successful,failed", "-y", "-s", "0", "-o", str(trace)]
        cpu_path = Path(temporary) / "cpu"
        launched = time.monotonic()  # a break also stops once it outlives its passing arm; the runner polls this every 50 ms
        session = _SessionWatch(trace, offset)
        stop = SimpleNamespace(is_set=lambda: cancel.is_set() or passing is not None and _outlived(passing, launched)
                               or time.monotonic() - launched >= (SESSION_SECONDS if session.seen() else timeout))
        raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *observer, sys.executable, "-I", "-S", "-c", _CPU_SUPERVISOR, str(cpu_path), str(memory), *actual],
                                    snapshot, max(timeout, SESSION_SECONDS), env=runner_env, cancel=stop)
        cpu = json.loads(cpu_path.read_bytes()) if cpu_path.exists() else [None, None, 0]
        inputs = _observed_inputs(identity, root, Path(temporary), trace, offset, actual, env, binding, timeout, runtimes, lower=overlay["lower"] if overlay else None, work_root=cpu[0],
                                  frozen=own)
        inputs["work"]["cpuSeconds"] = cpu[1]
        from .command_runner import _tail
        output = _tail(raw)
        timing = {"totalSeconds": time.perf_counter() - started,
                  "executionSeconds": time.perf_counter() - process_started}
        outcome = "incomplete" if timed_out or cpu[2] else "passed" if code == 0 else "failed"
        memory_error = {"error": f"memory: case held {cpu[2] >> 20} MB over its {memory >> 20} MB share"} if cpu[2] else {}
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome,
                          output=output, loadedRoot=identity.root, executedCommand=actual, timing=timing, **inputs, **memory_error,
                          **({"modelSession": True} if session.found else {}))


# --- executable findings ------------------------------------------------------------------------


def _indexed(identity: RepoIdentity, tree: str, *steps: tuple[tuple[str, ...], bytes | None]) -> str:
    """`tree` after git `steps` on a private index; the checkout is never touched."""
    handle = tempfile.NamedTemporaryFile(prefix="workflow-finding-index-", delete=False)
    handle.close()
    env = {"GIT_INDEX_FILE": handle.name}
    try:
        _git(identity, "read-tree", tree, env=env)
        for args, stdin in steps:
            _git(identity, *args, stdin=stdin, env=env)
        return _git(identity, "write-tree", env=env).decode().strip()
    finally:
        Path(handle.name).unlink(missing_ok=True)


@lru_cache(maxsize=128)
def _kept_tests(identity: RepoIdentity, candidate: str, original: str, overrides: tuple[str, ...]):
    before, current = _tree_entries(identity, original), _tree_entries(identity, candidate)
    files = {name for name in before if is_test_path(name)} - set(overrides)
    tree = _indexed(identity, candidate, (("restore", "--staged", "--source=" + original, "--", *sorted(files)), None)) if any(current.get(name) != before[name] for name in files) else candidate
    return tree, files


def _empty_tree(tree: str) -> str:
    """The empty tree's name under the object format that names `tree`."""
    return hashlib.new("sha1" if len(tree) == 40 else "sha256", b"tree 0\0").hexdigest()


TEMPORARY = re.compile(rb"(?<![\w.+@%~/-])/(?:[\w.+@%~-]+/)*[\w.+@%~-]+")
PROBES = "tests/.workflow-probes/"


def _freeze_temporary(identity: RepoIdentity, tree: str, files: list[str], command: list[str], scan: list[str] | None = None) -> tuple[str, list[str], list[str], dict[str, set[str]], JsonObject]:
    """Freeze each existing path below the temporary directory that a command word or a frozen scratch file in `scan`
    (all of `files` unless given) names, a directory with every regular file under it, and name its frozen place instead:
    the case no longer reads what its owner will remove. The executable's own file is never rewritten, as #170 never
    froze it. A probe directory whose file changes is renamed to its new content. Returns the tree, files, command, temporary roots named per scanned file, and the newly captured temporary records."""
    bound = len(files)
    roots = tuple(root + os.sep for root in {"/tmp", tempfile.gettempdir()})
    scan = tuple(sorted(files if scan is None else scan))
    def existing(texts):
        return {path for text in texts for token in TEMPORARY.findall(text)
                if (path := os.path.normpath(os.fsdecode(token))).startswith(roots) and os.path.exists(path)}  # a link to nothing holds nothing to freeze
    command_paths = tuple(sorted(existing(map(os.fsencode, command[1:]))))
    def capture(tree=tree):
        # Capture roots a probe directory at its files' common parent, so one whose files all lie below a single subdirectory is
        # a temporary tree an earlier call froze: its files are not read again, as they were not when it was frozen.
        spread: dict[str, set[str]] = {}
        nested = any("/" in name.split("/", 3)[3] for name in scan if name.startswith(PROBES))
        for name in files if nested else ():
            if name.startswith(PROBES):
                directory, _, place = name[len(PROBES):].partition("/")
                spread.setdefault(directory, set()).add(place.rpartition("/")[0].partition("/")[0])
        entries = {name: _entry(identity, tree, name) for name in scan if name.startswith(PROBES)
                   and (not nested or len(first := spread[name[len(PROBES):].partition("/")[0]]) > 1 or "" in first)}
        with blob_reader(Path(identity.root)) if entries else nullcontext() as read:  # one reader for those files, each held only while it names such a path
            texts, roots_by_file = {}, {}
            for name, (_, oid) in entries.items():
                if roots_found := existing([text := read(oid) or b""]):
                    texts[name], roots_by_file[name] = text, roots_found
        named = sorted(set(command_paths).union(*roots_by_file.values()), key=len, reverse=True)
        if not named:
            return tree, files, lambda match, root=b"": match.group(), lambda word: word, {}
        def refuse(error):
            raise ValueError(f"probe names a temporary path that cannot be frozen ({error}): read pass-start source with `git show <passStartOid>:FILE`")
        members = list(dict.fromkeys(member for path in named for member in (
            [path] if not os.path.isdir(path) else (os.path.join(folder, name) for folder, _, names in os.walk(path, onerror=refuse) for name in names))))
        if irregular := next((member for member in members if not os.path.isfile(member)), None):
            refuse(irregular + " is not a regular file")
        named = [name for name in named if any(member == name or member.startswith(name + os.sep) for member in members)]
        if not named:  # an empty directory holds nothing to freeze: the case keeps writing where it wrote
            return tree, files, lambda match, root=b"": match.group(), lambda word: word, {}
        try:
            blobs = _git(identity, "hash-object", "-w", "--stdin-paths", stdin=os.fsencode("\n".join(members))).decode().split()
        except RuntimeError as exc:
            refuse(exc)
        kept = _indexed(identity, _empty_tree(tree), (("update-index", "--index-info"), "".join(
            f"{'100755' if os.access(member, os.X_OK) else '100644'} {blob}\t{member.lstrip('/')}\n" for member, blob in zip(members, blobs)).encode()))
        stored = set(_git(identity, "ls-tree", "-r", "--name-only", "-z", kept).split(b"\0"))  # git leaves out what a tree cannot hold, a `.git` entry say
        if dropped := next((member for member in members if os.fsencode(member.lstrip("/")) not in stored), None):
            refuse(dropped + " cannot be stored in a tree")
        def placed(match, root=os.fsencode(identity.root + os.sep)):  # an absolute place: the frozen file may be read from any directory
            path = os.path.normpath(os.fsdecode(match.group()))
            return root + os.fsencode(f"{PROBES}{kept}/" + path.lstrip("/")) if any(path == name or path.startswith(name + os.sep) for name in named) else match.group()
        steps, changed = [(("read-tree", f"--prefix={PROBES}{kept}/", kept), None)], set()
        for name, text in texts.items():
            if name != command[0] and (rewritten := TEMPORARY.sub(placed, text)) != text:
                changed.add(name[len(PROBES):].split("/", 1)[0])
                steps.append((("update-index", "--add", "--cacheinfo", f"{entries[name][0]},"
                               f"{_git(identity, 'hash-object', '-w', '--stdin', stdin=rewritten).decode().strip()},{name}"), None))
        tree = _indexed(identity, tree, *steps)
        renamed = {old: _git(identity, "rev-parse", f"{tree}:{PROBES}{old}").decode().strip() for old in changed}
        tree = _indexed(identity, tree, *(step for old, new in renamed.items() for step in (
            (("update-index", "-z", "--index-info"), os.fsencode("".join(f"0 {'0' * len(kept)}\t{name}\0" for name in files if name.startswith(PROBES + old + "/")))),
            (("read-tree", f"--prefix={PROBES}{new}/", new), None))))
        moved = lambda text: re.sub(PROBES + "([0-9a-f]{40,64})/", lambda match: PROBES + renamed.get(match[1], match[1]) + "/", text)
        return tree, [*map(moved, files), *(f"{PROBES}{kept}/" + member.lstrip("/") for member in members)], placed, moved, roots_by_file
    tree, files, placed, moved, roots_by_file = _sample_input(("temporary", tree, *files, "", *command_paths, "", *scan, "", command[0] if command[0] in scan else None), capture)
    temporary = {name: [origin, *(_input_stamp(origin) or ())] for name in files[bound:] for origin in ["/" + name.split("/", 3)[3]]}
    return tree, files, [moved(command[0]), *(moved(os.fsdecode(TEMPORARY.sub(lambda match: placed(match, b""), os.fsencode(word)))) for word in command[1:])], roots_by_file, temporary



def _folder(folder: str) -> tuple[list[str], str]:
    """Every regular file below a scratch program's folder that a tree can hold, and a digest of their lstat identities:
    one walk a call. A `.git` entry, the workflow's state, a link, a special file and whatever cannot be read are left out."""
    def walk():
        state = str(state_root())
        files, pending = [], [folder] * (folder != state)
        while pending:
            try:
                entries = [(entry, entry.stat(follow_symlinks=False)) for entry in os.scandir(pending.pop()) if entry.name != ".git"]
            except OSError:  # a directory that cannot be read, or one removed under the walk, holds nothing to freeze
                continue
            for entry, info in entries:
                if stat.S_ISDIR(info.st_mode):
                    pending += [entry.path] * (entry.path != state)
                elif stat.S_ISREG(info.st_mode) and os.access(entry.path, os.R_OK):
                    files.append((entry.path, info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        files.sort()
        return [name for name, *_ in files], hashlib.sha256(json.dumps(files).encode()).hexdigest()
    return _sample_input(("folder", folder), walk)


def _shared(identity: RepoIdentity, folder: str) -> bool:
    """Whether `folder` holds the checkout, the temporary directory or the home directory: it is no program's own, and writes there cannot be kept apart."""
    return any((holds + os.sep).startswith(folder.rstrip(os.sep) + os.sep) for holds in (identity.root, "/tmp", tempfile.gettempdir(), os.path.expanduser("~")))


def _places(identity: RepoIdentity, command: list[str], supplied: tuple[str, ...] | list[str] = ()) -> tuple[dict[Path, str], set[str]]:
    """Each regular file outside the checkout that a command word names (a program binary aside) and every file of
    its folder (`_folder`), with its place below those files' common directory: where a capture freezes it. Also
    those folders: one that holds the checkout, the temporary directory or the home directory, or that the case's
    executable search path names, as the runner sets it, is no program's own. There an argument is frozen alone and the executable runs
    in place, as on #170."""
    path, env = None, None
    end = next((i for i in reversed(range(len(command))) if command[i].startswith("PATH=")), -1)
    for word in command[:end + 1]:
        if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", word):
            path = word[5:] if word.startswith("PATH=") else path
        else:
            try:
                env = env or os.path.realpath(_executable(identity, "env"))
                tool = _executable(identity, word, {"PATH": path} if path is not None else None)
            except WorkflowError:
                break
            if os.path.realpath(tool) != env:
                break
    search = {os.path.normpath(os.path.join(identity.root, entry)) for entry in (os.get_exec_path() if path is None else path.split(os.pathsep)) if entry}
    def own(path, word):  # as resolved and as written: the search path may name /bin where a program resolves to /usr/bin
        folder = str(path.parent)
        return not {folder, os.path.dirname(os.path.normpath(os.path.join(identity.root, word)))} & search and not _shared(identity, folder)
    found = {path: own(path, word) for index, word in enumerate(arg.split("::", 1)[0] for arg in command) if (path := _scratch(identity, word))
             and not (os.access(path, os.X_OK) and path.open("rb").read(4) == b"\x7fELF") and (index or own(path, word))}
    folders = {str(path.parent) for path, alone in found.items() if alone}
    paths = dict.fromkeys(Path(name) for folder in sorted(folders) for name in _folder(folder)[0])
    paths.update(dict.fromkeys(sorted(found)))
    paths.update(dict.fromkeys(map(Path, supplied)))  # declared files keep their place, not their neighbours
    base = os.path.commonpath([path.parent for path in paths]) if paths else ""
    return {path: os.path.relpath(path, base) for path in paths}, folders


def _input_stamp(origin: str):
    return _sample_input(("stat", origin), lambda: _stamp(origin, True))


def _apart(identity: RepoIdentity, run: JsonObject) -> tuple[tuple[str, ...], tuple[str, ...]] | tuple[()]:
    """While each scratch folder the case froze is as it was frozen, the case runs where its programs are, as a direct
    run does: the folders its writes are kept apart from, those and the folder of each temporary file it froze that
    is still there, and what it froze there, those folders and files, each ending in a separator. Nothing, so that the
    frozen copy runs, once a scratch folder is gone or changed, where such a temporary file's folder is shared
    (`_shared`), or when a program there names a temporary path now gone: it reads the frozen one, as on #170."""
    folders = [origin for origin, _ in (run.get("folders") or {}).values()]
    if not folders or not all(map(os.path.isdir, folders)) or _stale(identity, run):
        return ()
    origins = [origin for origin, *_ in (run.get("temporary") or {}).values()]
    files = [origin for origin in origins if (now := _input_stamp(origin)) and stat.S_ISREG(now[0])]
    around = {os.path.dirname(origin) for origin in files}
    if any(_shared(identity, folder) for folder in around):
        return ()
    if len(files) < len(origins):
        required = set(run.get("fileTemporaryRoots", ()))
        if "fileTemporaryRoots" not in run or required and any(
                origin in required or any(str(parent) in required for parent in Path(origin).parents)
                for origin in set(origins).difference(files)):
            return ()  # a live program needs the absent path; an argument already names its frozen copy
    layers: list[str] = []  # a folder below one already kept apart needs no layer of its own
    for prefix in sorted({folder + os.sep for folder in (*folders, *around)}):
        if not layers or not prefix.startswith(layers[-1]):
            layers.append(prefix)
    return tuple(prefix[:-1] for prefix in layers), tuple(path + os.sep for path in (*folders, *files))


def _stale(identity: RepoIdentity, run: JsonObject) -> bool:
    """Whether a folder the case froze holds other files than it did, a scratch or temporary file frozen alone holds
    other bytes, or a case that froze none names a scratch file that is there now: one walk a folder and one lstat a
    file a call, bytes read only when a file's identity differs. What is gone changes nothing: the frozen copy stays.
    A record from before origins were kept is checked by the scratch files its command or its `scratch` names, each at its frozen place."""
    def changed():
        if any(list(_input_stamp(origin) or ()) != stamp and os.path.isdir(os.path.dirname(origin))
               for origin, stamp in (run.get("absentInputs") or {}).items()):
            return True
        temporary = run.get("temporary") or {}
        files = {**(run.get("origins") or {}), **temporary}
        if not (files or run.get("folders")):
            words = shlex.split(run.get("sourceCommand", run["command"]))
            if not any(name.startswith(PROBES) for name in run["probeFiles"]):
                return bool(_places(identity, [word for index, word in enumerate(words) if not index or word.startswith(("/", ".."))])[0])
            recorded, supplied = shlex.split(run["command"]), run.get("scratch", ())
            if not supplied and words == recorded:  # a pasted bundle without live source names
                return False
            named = {now.split("::", 1)[0]: os.path.realpath(os.path.join(identity.root, was.split("::", 1)[0]))
                     for was, now in zip(words, recorded) if now.startswith(PROBES) and was != now}
            suffixes = {origin[pos + 1:]: origin for origin in supplied for pos, ch in enumerate(origin) if ch == "/"}
            files = {name: [named.get(name) or suffixes.get(place, "/" + place)] for name in run["probeFiles"] if name.startswith(PROBES) for place in [name.split("/", 3)[3]]}
        if any(os.path.isdir(origin) and _folder(origin)[1] != digest for origin, digest in (run.get("folders") or {}).values()):
            return True
        if run.keys() & {"origins", "folders", "temporary"}:  # a scratch file the command names that its capture lacked, written back since; #170 never froze its executable
            own = tuple(origin + os.sep for origin, _ in (run.get("folders") or {}).values())
            frozen = {origin for origin, *_ in files.values()}
            words = shlex.split(run.get("sourceCommand", run["command"]))
            lacked = lambda paths: any(str(path) not in frozen and not str(path).startswith(own) for path in paths)
            # Each word first, and the capture's own selection only for a word naming a file outside them (an installed program, say).
            if lacked(path for word in words if (path := _scratch(identity, word.split("::", 1)[0]))) and lacked(_places(identity, words)[0]):
                return True
        placed = re.compile(re.escape(os.fsencode(identity.root + os.sep + PROBES)) + rb"[0-9a-f]{40,64}/")  # a frozen file names a frozen temporary path at its frozen place
        for name, (origin, *stamp) in files.items():
            now = _input_stamp(origin)
            if now is None or name in temporary and not stat.S_ISREG(now[0]):  # a temporary link that no longer resolves is gone
                if name in temporary and os.path.isdir(os.path.dirname(origin)):
                    return True
            elif stat.S_ISREG(now[0]) and list(now) != stamp:
                mode, oid = _entry(identity, run["probeTree"], name)
                if (mode == "100755") != os.access(origin, os.X_OK) or Path(origin).read_bytes() != placed.sub(b"/", _reader(identity)(oid) or b""):
                    return True
        return False
    return _sample_input(("stale", run["probeTree"], run["command"]), changed)


def shown(identity: RepoIdentity, state: JsonObject, run: JsonObject) -> str:
    """The case's command as a lead runs it from the checkout, with the assignments and PYTHONPATH the runner gives it:
    its own while every scratch file it froze is where it was, unchanged. Otherwise the files it froze are written once,
    with their modes, to a directory per capture kept with the pass's execution state; a path into one of its frozen
    directories, in the command or in those files, names that copy."""
    command, frozen = run["command"], [name for name in run.get("probeFiles", ()) if name.startswith(PROBES)]
    if frozen and not run.get("temporary") and _apart(identity, run) and all(os.path.isfile(origin) for origin, *_ in (run.get("origins") or {}).values()):
        command, frozen = run["sourceCommand"], []  # the lead runs its own: it names no temporary path a run of it could change
    if frozen:
        kept = repo_state_dir(identity) / "execution-trees" / str(state["workflowId"])
        directories = {name[len(PROBES):].partition("/")[0] for name in frozen}
        relocate = lambda prefix, data: re.sub(re.escape(prefix) + rb"([0-9a-f]{40,64})/", lambda match: os.fsencode(f"{kept}/probes/") + match[1] + b"/"
                                               if os.fsdecode(match[1]) in directories else match[0], data)
        copies = [name for name in frozen if not os.path.lexists(kept / "probes" / name[len(PROBES):])]
        if copies:
            entries = _tree_entries(identity, run["probeTree"])
            with blob_reader(Path(identity.root)) as read:
                for name in copies:  # a file or link where none is, each read and written alone
                    (mode, oid), path = entries[name], kept / "probes" / name[len(PROBES):]
                    data = relocate(os.fsencode(identity.root + os.sep + PROBES), read(oid))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if mode == "120000":
                        path.symlink_to(os.fsdecode(data))
                    else:
                        path.write_bytes(data)
                        path.chmod(0o755 if mode == "100755" else 0o644)
        command = os.fsdecode(relocate(os.fsencode(PROBES), os.fsencode(command)))
    words, assignments, environment = _case_environment(identity, command)
    def quote(value: str) -> str:
        # Quote each literal separately so even arguments containing spaces, quotes or '$' round-trip.
        return '"${PWD}"'.join(shlex.quote(part) if part else "" for part in value.split(identity.root)) or "''"
    return " ".join([*(f"{name}={quote(value)}" for name, value in {**assignments, "PYTHONPATH": environment["PYTHONPATH"]}.items()),
                     *(quote(word) for word in words)])


def _probe_capture(identity: RepoIdentity, probe: object, candidate: str, timeout: float,
                   support: list[str] | None = None, inventory: list[str] | None = None, preserved: str | None = None, recorded=()) -> JsonObject:
    """Freeze the command's sources. Every regular file outside the checkout that a command argument names (a program
    binary aside) lands in one probe directory with the files of its folder, each keeping its place relative to the
    others. `support`: the test files a kept-behaviour capture reads at their candidate bytes, and scratch files the
    command does not name, frozen and read for temporary paths as a named one is."""
    if not (isinstance(probe, dict) and set(probe) == {"command"} and isinstance(probe["command"], str) and probe["command"].strip()):
        raise ValueError("probe needs a command; source content is not an input")
    command = shlex.split(probe["command"])
    source_command = shlex.join(command)
    scratch, tests = [], []
    for name in support or ():
        if path := _scratch(identity, name):
            scratch.append(str(path))
        else:
            tests.append(name)
    support = tests
    places, folders = _places(identity, command, scratch)
    overrides = {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root) for name in support or []}
    tree, original_files = _kept_tests(identity, candidate, preserved, tuple(sorted(overrides))) if preserved else (candidate, set())
    virtual, known = [], None
    if places:
        def freeze(tree=tree):  # cases that share their scratch files freeze them once a call, in one Git call for them all
            plain = [str(path) for path in places if "\n" not in str(path)]  # a name holding a newline is hashed alone
            blobs = dict(zip(plain, _git(identity, "hash-object", "-w", "--stdin-paths", stdin=os.fsencode("\n".join(plain))).decode().split()))
            entries = os.fsencode("".join(f"{'100755' if os.access(path, os.X_OK) else '100644'} blob "
                                          f"{blobs.get(str(path)) or _git(identity, 'hash-object', '-w', '--', str(path)).decode().strip()}\t{place}\0" for path, place in places.items()))
            frozen = (_indexed(identity, _empty_tree(tree), (("update-index", "-z", "--index-info"), entries)) if any("/" in place for place in places.values())
                      else _git(identity, "mktree", "-z", stdin=entries).decode().strip())  # files of one directory make one tree
            return frozen, _indexed(identity, tree, (("read-tree", f"--prefix={PROBES}{frozen}/", frozen), None))
        frozen, tree = _sample_input(("bundle", tree, *places.items()), freeze)
        prefix = f"tests/.workflow-probes/{frozen}/"
        virtual = [prefix + place for place in places.values()]
        command = [prefix + places[path] + arg[len(arg.split("::", 1)[0]):] if (path := _scratch(identity, arg.split("::", 1)[0])) in places else arg
                   for arg in command]
    elif frozen := next((match for arg in command if (match := re.fullmatch(r"(tests/\.workflow-probes/([0-9a-f]{40,64})/).+", arg.split("::", 1)[0]))), None):
        known = next((run for run in reversed(recorded) if run["command"] == source_command), None)
        if known and (directories := (known.get("scratchScope") or {}).get("dirs")):
            prefixes = tuple(directory + "/" for directory in directories)
            virtual = [name for name in known["probeFiles"] if name.startswith(prefixes)]
            tree = _indexed(identity, tree, (("restore", "--staged", "--source=" + known["probeTree"], "--", *directories), None))
        elif _git(identity, "cat-file", "-t", frozen[2]).decode().strip() == "tree":  # a new command over a bundle discovers its inputs
            directories = [frozen[2]]
            for name in directories:  # and each frozen directory that directory's files or the command name
                tree = _indexed(identity, tree, (("read-tree", "--prefix=" + PROBES + name + "/", name), None))
                held = dict(reversed(entry.split(b"\t", 1)) for entry in _git(identity, "ls-tree", "-r", "-z", name).split(b"\0") if entry)
                virtual += [PROBES + name + "/" + os.fsdecode(entry) for entry in held]
                if name == frozen[2]:  # one reader for the directory's files, each held alone
                    with blob_reader(Path(identity.root)) as read:
                        named = dict.fromkeys(os.fsdecode(found) for text in chain((os.fsencode(probe["command"]),), (read(os.fsdecode(value.split()[2])) or b"" for value in held.values()))
                                              for found in re.findall(rb"tests/\.workflow-probes/([0-9a-f]{40,64})/", text))
                    directories += [other for other in named if other != name and _git(identity, "cat-file", "-t", other).decode().strip() == "tree"]
        else:  # or, from a capture before probe directories, the one blob
            virtual = [frozen[0]]
            tree = _indexed(identity, tree, (("update-index", "--add", "--cacheinfo", f"100644,{frozen[2]},{frozen[0]}"), None))
    own = tuple(folder + os.sep for folder in folders)
    # Keep a known capture's scratch selection; a new command over a frozen bundle discovers its inputs as on #170.
    original_names, executable = virtual, command[0]  # the executable's own file is never rewritten: what it names moves nothing
    prior_scope = (known or {}).get("scratchScope", {})
    extra = {f"{prefix}{places[Path(path)]}" for path in scratch} | set(prior_scope.get("extra", ()))
    narrow = bool(places or known and (not prior_scope or "extra" in prior_scope))
    named = {word.split("::", 1)[0] for word in command} | extra
    tree, virtual, command, roots_by_file, temporary = _freeze_temporary(identity, tree, virtual, command, named.intersection(virtual) if narrow else None)
    # The fresh scratch tree and its optional temporary tree each contribute one directory.
    members = (virtual[0], virtual[-1]) if places else virtual
    scope = {"dirs": sorted({PROBES + name[len(PROBES):].partition("/")[0] for name in members})}
    if narrow:
        scope["extra"] = [new for old, new in zip(original_names, virtual) if old in extra] if extra else []
    # A renamed probe directory keeps its files' order. A folder is recorded with its digest, a file frozen alone with its identity.
    held = {os.path.dirname(name): [str(path.parent), _folder(str(path.parent))[1]] for name, path in zip(virtual, places) if str(path.parent) in folders}
    supplied = set(scratch)
    origins = {}
    source_roots = {root for name, path in zip(original_names, places) if name in roots_by_file and name != executable and
                    (str(path.parent) in folders or str(path) in supplied) for root in roots_by_file[name]} if roots_by_file else set()
    for name, path in zip(virtual, places):
        origin = str(path)
        if not origin.startswith(own):
            if origin in supplied:
                temporary[name] = [origin, *(_input_stamp(origin) or ())]
                source_roots.add(origin)
            else:
                origins[name] = [origin, *_stamp(path)]
    selected = list(command)
    if selected and Path(selected[0]).name == "env":
        selected.pop(0)
    while selected and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", selected[0]):
        selected.pop(0)
    if not selected:
        raise ValueError("probe command must name an executable")
    files = (_probe_files(selected, identity, support or [], virtual) if inventory is None
             else sorted(set(inventory) | set(virtual)))
    if preserved is not None:
        files = sorted(original_files | (set(files) & (overrides | set(virtual))))
        support = sorted(overrides & _tree_entries(identity, preserved).keys())  # a file added since pass start has no pass-start bytes to protect
    return {"command": shlex.join(command), "candidateTree": candidate, "probeTree": tree,
            "probeFiles": files, "support": support or [], "timeout": timeout, "captureKey": os.urandom(16).hex(),
            "sourceCommand": source_command, **({"scratchScope": scope} if virtual else {}), **({"scratch": scratch} if scratch else {}), **({"origins": origins} if origins else {}), **({"folders": held} if held else {}),
            **({"temporary": temporary, "fileTemporaryRoots": sorted(source_roots)} if temporary else {})}


def _probe_outcome(arm: JsonObject) -> str:
    return "timed-out" if arm["timedOut"] else str(arm["outcome"])


def _revised(identity: RepoIdentity, run: JsonObject, candidate: str, preserved: str | None, accepted: bool = False) -> JsonObject | None:
    """The recorded case captured again from its command: when a scratch file or folder it froze changed where it is
    (`_stale`), or, without `accepted` proof, when the candidate changed a test file it froze and the capture then
    freezes bytes the recorded one lacks."""
    live, source = _stale(identity, run), run.get("sourceCommand", run["command"])
    moved = () if live or accepted or run["candidateTree"] == candidate else _sample_input(("moved", run["candidateTree"]), lambda: _changed_entries(identity, run["candidateTree"], candidate))
    if not live and not any(name in moved for name in (run["support"] if preserved else _comparison_files(identity, run))):
        return None
    # A program gone with its folder is captured from its frozen command: the frozen copy stays.
    kept = (run.get("origins") or run.get("folders")) and not any(os.path.isdir(origin) for origin, _ in (run.get("folders") or {}).values()) and not _places(identity, shlex.split(source))[0]
    support = [*run["support"], *filter(os.path.isfile, run.get("scratch", ()))]
    captured = _probe_capture(identity, {"command": run["command"] if kept else source}, candidate, run["timeout"], support,
                              _sample_input(("inventory", *run["support"]), lambda: _probe_files([], identity, run["support"])), preserved, (run,))
    if run.get("scratch"):
        captured["scratch"] = run["scratch"]  # a declaration survives a recapture while its input is absent
    watched = chain(run.get("absentInputs", ()), (row[0] for row in (run.get("temporary") or {}).values()))
    absent = {origin: list(now or ()) for origin in watched
              if not ((now := _input_stamp(origin)) and stat.S_ISREG(now[0])) and os.path.isdir(os.path.dirname(origin))}
    if absent:
        captured["absentInputs"] = absent
    def frozen(probe):
        entries = _tree_entries(identity, probe["probeTree"])
        return {(name, entries[name][1]) for name in _comparison_files(identity, probe)}
    return captured if live or frozen(captured) - frozen(run) else None


UNBROKEN = "probe did not fail under its behavior break; supply --change probe=FILE"


def _run_probe(identity: RepoIdentity, probe: object, candidate: str, original: str | None,
               timeout: float = MAX_CASE_SECONDS, cached: list[JsonObject] | None = None,
               frozen: JsonObject | None = None, report: bool = False) -> JsonObject:
    """`report`: permit a final author's nondeciding result."""
    try:
        witnessed = bool(frozen and frozen.get("arms")) and (
            (frozen["arms"][0]["outcome"], _probe_outcome(frozen["arms"][-1])) in {("failed", "passed"), ("passed", "failed"), ("passed", "timed-out")})
        frozen = frozen or _probe_capture(identity, probe, candidate, timeout)
        compared = _compare(identity, original or candidate, candidate, frozen, cached if cached is not None else [], passing_original=original is not None)
        result: JsonObject = {"kind": "probe", "comparison": compared,
                              "candidate": _probe_outcome(compared["arms"][-1])}
        if original is not None:
            result["original"] = _probe_outcome(compared["arms"][0])
        if all(arm["outcome"] == "passed" for arm in compared["arms"]) and not compared.get("sensitive") and not witnessed and not report:
            result["error"] = compared.get("breakError") or UNBROKEN
        if any(_probe_outcome(arm) == "incomplete" for arm in compared["arms"]):
            result["error"] = "; ".join(dict.fromkeys(str(arm.get("error") or "unproved execution") for arm in compared["arms"] if _probe_outcome(arm) == "incomplete"))
        return result
    except (RuntimeError, ValueError) as exc:
        return {"kind": "probe", "error": f"probe: {exc}"}


def _command_files(identity: RepoIdentity, run: JsonObject) -> set[str]:
    """Explicit file arguments, not an inference from frozen support membership."""
    frozen, selected = set(run["probeFiles"]), set()
    for argument in shlex.split(run["command"]):
        values = {argument.split("::", 1)[0]}
        if argument.startswith("-"):
            values.add(argument.split("=", 1)[-1].split("::", 1)[0])
        matches = frozen & {os.path.relpath(os.path.normpath(Path(identity.root) / value), identity.root)
                            for value in values}
        if len(matches) > 1:
            matches &= {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root)
                        for name in run.get("support") or []}
            if not matches:
                raise WorkflowError("ambiguous file argument: " + argument)
        selected.update(matches)
    return selected


def _replacement_cases(identity: RepoIdentity, batches: list[JsonObject], before: str, after: str,
                       cached: list[JsonObject], validation: JsonObject | None, changed: set[str]) -> JsonObject:
    """Judge all bound commands with frozen support and, for changed tests, their surviving checkout files."""
    def live(run, tree, scratch):
        present = _tree_entries(identity, tree)
        overlay = _indexed(identity, tree, (("restore", "--staged", "--source=" + run["probeTree"], "--", *scratch), None)) if scratch else tree
        return {**run, "probeTree": overlay, "probeFiles": [name for name in run["probeFiles"] if name in present or name in scratch]}
    probes = list(batches)
    checks = [(run, i, i, False) for i, run in enumerate(batches)]
    if changed:
        for index, run in enumerate([*batches, *([validation] if validation else [])]):
            paths = _command_files(identity, run) or (set(run["probeFiles"]) & changed)
            scratch = sorted(name for name in paths if name.startswith(PROBES))
            if not paths or not scratch and not paths.intersection(_tree_entries(identity, after)):
                continue  # a test file the replacement deletes leaves the frozen proof
            if run is not validation:  # served from the recorded arm when the case's files are the frozen ones
                index = len(probes)
                probes.append({**live(run, before, scratch), "sources": (before, before)})
            checks.append((run, len(probes), index, True))
            probes.append({**live(run, after, scratch), "sources": (after, after)})
    checked = _compare_many(identity, before, after, probes, cached, challenge=False)
    broken, preexisting = [], None
    for bound, result, control, checkout_files in checks:
        arm = checked[result]["arms"][-1]
        if arm["outcome"] == "passed":
            continue
        if bound is validation or checkout_files and arm["outcome"] != "failed":
            return {"error": "required surviving named case fails: " + bound["command"] + "\n" + str(arm.get("output") or "")[-800:]}
        if arm["timedOut"]:
            return {"applied": after == before, "candidate": "timed-out", "timedOut": f"recorded batch `{bound['command']}` ran past {bound['timeout']:g} s: replace it with direct probes of the changed code"}
        if arm["outcome"] != "failed":
            return {"error": f"recorded batch `{bound['command']}` {arm['outcome']} with the change applied"}
        if checked[control]["arms"][0]["outcome"] == "passed":
            broken.append(behavior_map.case_key(bound["behaviorId"], bound))
        else:
            preexisting = bound["command"]
    if broken:
        return {"applied": False, "candidate": "failed", "failure": "replacement breaks bound cases: " + ", ".join(dict.fromkeys(broken))}
    if preexisting:
        return {"error": "current candidate does not pass the retained batch: " + preexisting}
    return {}


def _shape_refusal(identity: RepoIdentity, before: str, after: str, cost_proved: bool = False) -> str | None:
    """Keep net reduction unless measured cost justifies growth; immutable probes remain the oracle."""
    counts = _git(identity, "diff", "--numstat", "--no-renames", "-z", before, after).decode("utf-8", "replace").split("\0")
    rows = [row.split("\t", 2) for row in counts if row.count("\t") == 2]
    if not cost_proved and sum(int(added) for added, _, _ in rows if added != "-") >= sum(int(deleted) for _, deleted, _ in rows if deleted != "-"):
        return "a change must delete more lines than it adds"
    return None


def _run_change(identity: RepoIdentity, batches: list[JsonObject], change: object, candidate: str,
                probe: object = None, timeout: float = MAX_CASE_SECONDS, cached: list[JsonObject] | None = None,
                frozen: JsonObject | None = None, cost_recheck: bool = False) -> JsonObject:
    """Compare a concrete replacement and retain its correctness and cost counterexamples."""
    if not isinstance(change, str) or not change.strip():
        return {"kind": "change", "error": "change must be a unified diff"}
    if not batches:
        return {"kind": "change", "error": "no recorded comparison batch to rerun"}
    cached = list(batches) if cached is None else cached
    measured = _run_probe(identity, probe, candidate, None, timeout, cached, frozen) if probe is not None else None
    if measured and measured.get("candidate") == "passed" and "error" in measured:
        return {"kind": "change", "error": "a replacement's probe must fail on the candidate and pass with the replacement"
                if measured["error"] == UNBROKEN else measured["error"]}
    if cost_recheck and measured is not None:
        return measured
    patch = change.encode("utf-8", "replace") + (b"" if change.endswith("\n") else b"\n")  # unencodable text cannot apply
    try:
        changed = _indexed(identity, candidate, (("apply", "--cached", "-"), patch))
        before, after = candidate, changed
    except RuntimeError:
        return {"kind": "change", "error": "change does not apply to the candidate"}
    cost = None
    maintenance = {path for path in _changed_entries(identity, before, after) if is_test_path(path)}
    if probe is not None:
        if not maintenance and (measured is None or measured.get("candidate") not in {"passed", "failed"}):
            return {"kind": "change", "error": "replacement probe did not establish a candidate outcome"}
        if measured and "comparison" in measured:
            cost = _compare(identity, candidate, after, measured["comparison"], cached)
        if cost and (not cost["valid"] or not cost.get("sensitive")) and not maintenance:
            return {"kind": "change", "error": "replacement did not pass the same cost probe", "comparison": cost}
    if refusal := _shape_refusal(identity, before, after, cost is not None and cost["valid"] and measured["candidate"] == "failed"):
        return {"kind": "change", "error": refusal}
    if failure := _replacement_cases(identity, batches, before, after, cached, measured.get("comparison") if measured else None, maintenance):
        return {"kind": "change", **failure}
    return {"kind": "change", "applied": after == candidate, "candidate": "passed", **({"comparison": cost} if cost else {}),
                                     **({"selection": "proposer-selected command; exit result checked, source-line reachability and relevance not traced"} if probe is not None else {})}


def binds(execution: JsonObject) -> bool:
    """Observed regressions and measured cost failures bind; implementation sketches remain review evidence."""
    if "error" in execution:
        return False
    if execution["kind"] == "cost":
        return execution.get("candidate") != "passed"
    if execution["kind"] == "change":
        comparison = execution.get("comparison") or {}
        return bool(comparison.get("valid") and comparison["arms"][0]["outcome"] == "failed"
                    and execution.get("candidate") == "passed")
    return execution.get("candidate") in {"failed", "timed-out"} and execution.get("original") == "passed"


def _retained(identity: RepoIdentity, state: JsonObject) -> list[tuple[JsonObject, JsonObject]]:
    """Each observation of every review or final finding that binds by the current rule: a finding settles only with
    all of them; one recorded under an earlier rule that no longer binds is re-executed as a report line. Once the final
    author's ready verdict judges the candidate, a probe of its finding that it captured again there, passing on both
    sources, replays with that capture's probe files: its verdict confirms the repair of a file the probe had frozen."""
    retained, rejected, intakes, recaptured, candidate = [], set(), {}, {}, _active_candidate_tree(identity)
    judged = (state.get("finalReview") or {}).get("status") == "commit-ready" and state.get("judgedTree") == candidate
    for entry in (entry for entry in state.get("findingStates") or [] if entry.get("status") != "rejected"):  # the lead's rejection stands without a rerun
        for ref in [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]}, *entry.get("observations", [])]:
            ref = {"evidenceId": ref["evidenceId"], "id": ref["id"]}
            if ref["evidenceId"] not in intakes:
                intakes[ref["evidenceId"]] = evidence_document(identity, ref["evidenceId"], restore_runs=False) or {}
            intake = intakes[ref["evidenceId"]]
            finding = next((item for item in intake.get("findings", []) if item["id"] == ref["id"]), None)
            if finding and finding.get("rejected") is True:
                rejected.add(tuple(finding["priorFinding"][key] for key in ("evidenceId", "id")))
            run = (finding or {}).get("recapture") or ((finding or {}).get("execution") or {}).get("comparison") or {}
            if judged and intake.get("stage") == "final" and intake.get("candidateTree") == candidate and ((finding or {}).get("recapture") or all(
                    arm["outcome"] == "passed" for arm in run.get("arms") or [{"outcome": None}])):
                recaptured[intake.get("producer"), run["command"]] = run
            if intake.get("stage") in {"code-review", "final"} and finding and "execution" in finding and binds(finding["execution"]):
                retained.append((entry, {**finding, "ref": ref, "sourceTree": intake["candidateTree"], "stage": intake["stage"]}))
    for _, finding in retained:
        intake, held = intakes[finding["ref"]["evidenceId"]], finding["execution"]
        if held["kind"] == "probe" and intake["stage"] == "final" and (again := recaptured.get((intake.get("producer"), held["comparison"]["command"]))):
            finding["execution"] = {**held, "comparison": {**held["comparison"], "probeTree": again["probeTree"], "probeFiles": again["probeFiles"]}}
    return [(entry, finding) for entry, finding in retained
            if tuple(finding["ref"][key] for key in ("evidenceId", "id")) not in rejected]


def execute_findings(identity: RepoIdentity, state: JsonObject, findings: list[JsonObject], *, captured: JsonObject | None = None,
                     retained: list[tuple[JsonObject, JsonObject]] | None = None, candidate: str | None = None,
                     current: JsonObject | None = None, report: bool = False) -> JsonObject:
    """Execute submitted observations; gates supply retained ones. `report` identifies the final author."""
    from .workflow_state import _finding_state
    candidate = candidate or _active_candidate_tree(identity)
    rejected = [finding["priorFinding"] for finding in findings if finding.get("rejected") is True]
    withdrawn = []
    if rejected:
        targets = [_finding_state(state, ref["evidenceId"], ref["id"]) for ref in rejected]
        selected = [entry for entry in state.get("findingStates", []) if any(entry is target for target in targets)]
        remaining = [entry for entry, finding in _retained(identity, {**state, "findingStates": selected})
                     if finding["ref"] not in rejected]
        withdrawn = [{**ref, "execution": {"kind": "claim"}} for ref, target in zip(rejected, targets)
                     if target is not None and all(target is not entry for entry in remaining)]
    if retained is None and not any("probe" in finding or "change" in finding for finding in findings):
        return {"measuredTree": candidate, "measuredEvidence": state.get("tddEvidence"),
                "findings": [{**finding, "execution": {"kind": "claim"}, "material": False} for finding in findings],
                "reexecuted": withdrawn, "runs": []}
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    current = current if current is not None else evidence_document(identity, state.get("tddEvidence")) or {}
    set_aside = rejections(state, "item")  # a rejected item's cases replay nowhere
    maintained = [entry for entry in [*behavior_map.current_cases(current.get("behaviorMap") or [], current.get("runs") or [], _retired(state)), *(
        {**entry, "comparison": current["runs"][entry["runIndex"]]} for entry in _evaluation(identity, state, current, True).values())]
        if entry["id"] not in set_aside]
    used = {(item.get("comparison") or {}).get("runIndex") for item in maintained}
    batches = [run for run in current.get("runs") or [] if run["runIndex"] in used]
    cached = list(current.get("runs") or [])
    retained = [(entry, finding) for entry, finding in retained or [] if finding["ref"] not in rejected]
    loaded_keys = {tuple(arm.get("key") for arm in run["arms"]) for run in cached}
    def recorded(finding: JsonObject) -> JsonObject | None:
        """A retained finding's frozen probe; a temporary path it names that exists again is frozen in, so the probe replays."""
        prior = finding["execution"].get("comparison")
        if prior and "replay" not in finding:
            try:
                words = shlex.split(prior["command"])
                scope = prior.get("scratchScope", {})
                if "extra" in scope:
                    scan = ({word.split("::", 1)[0] for word in words} | set(scope["extra"])).intersection(prior["probeFiles"])
                elif scope:
                    prefixes = tuple(directory + "/" for directory in scope["dirs"])
                    scan = {name for name in prior["probeFiles"] if name.startswith(prefixes)}
                else:
                    scan = None  # #170 retained captures used the full frozen files
                program = words[0]
                tree, files, words, found, temporary = _freeze_temporary(identity, prior["probeTree"], prior["probeFiles"], words, scan)
            except ValueError:  # it exists again and cannot be frozen: the probe replays as captured and reads it where it is
                tree = prior["probeTree"]
            if tree == prior["probeTree"]:
                finding["replay"] = prior
            else:
                own = tuple(place + "/" for place in prior.get("folders", ()))
                supplied = set(prior.get("scratch", ()))
                standalone = {name for name, row in (prior.get("temporary") or {}).items() if row[0] in supplied} if supplied else set()
                roots = set(prior.get("fileTemporaryRoots", ())).union(*(paths for name, paths in found.items()
                                                                         if name != program and (name.startswith(own) or name in standalone)))
                finding["replay"] = {**prior, "probeTree": tree, "probeFiles": sorted(files), "command": shlex.join(words),
                                     "temporary": {**prior.get("temporary", {}), **temporary}, "fileTemporaryRoots": sorted(roots)}
        return finding.get("replay", prior)
    def run(finding: JsonObject, first: bool) -> JsonObject:
        timeout = next((batch["timeout"] for batch in reversed(current.get("runs") or [])
                        if batch["command"] == (finding.get("probe") or {}).get("command")),
                       max((batch["timeout"] for batch in batches), default=MAX_CASE_SECONDS) if "change" in finding and "probe" in finding else MAX_CASE_SECONDS)
        if finding.get("execution", {}).get("kind") == "cost":
            prior = finding["execution"]["comparison"]
            compared = _compare_many(identity, original, candidate, [prior], cached, challenge=False)[0]
            return {"kind": "cost", "candidate": "failed" if not compared["valid"] or _work_errors(compared) else "passed"}
        frozen = (captured or {}).get((finding.get("probe") or {}).get("command")) if first else None
        if not first and "change" in finding and "probe" not in finding:
            supplement = next((other for other in [*executed, *(f for _, f in retained)]
                               if other.get("change") == finding["change"] and other.get("claim") == finding["claim"]
                               and "probe" in other and (other.get("execution") or {}).get("comparison")), None)
            if supplement:
                finding = {**finding, "probe": supplement["probe"]}
                frozen = supplement["execution"]["comparison"]
        if not first and "probe" in finding:
            frozen = frozen or recorded(finding) or _probe_capture(identity, finding["probe"], finding["sourceTree"], timeout)
        prior = (finding.get("execution") or {}).get("comparison") or {}
        cost_recheck = not first and bool(prior.get("arms")) and prior["arms"][0]["outcome"] == "failed"
        return (_run_change(identity, batches, finding["change"], candidate, finding.get("probe"), timeout, cached, frozen, cost_recheck) if "change" in finding
                else _run_probe(identity, finding["probe"], candidate, original, timeout, cached, frozen,
                                report and first and not (frozen or {}).get("breakOid")) if "probe" in finding
                else {"kind": "claim"})
    for replacement in (False, True):
        probes = [captured[finding["probe"]["command"]] for finding in findings
                  if captured and "probe" in finding and ("change" in finding) == replacement]
        probes.extend(recorded(finding) for _, finding in retained
                      if "probe" in finding and ("change" in finding) == replacement and finding["execution"].get("comparison"))
        if probes:
            _compare_many(identity, candidate if replacement else original, candidate, probes, cached, passing_original=not replacement)
    executed, observations, recaptures = [], [], None
    for finding in findings:
        execution = run(finding, True)
        result = {**finding, "execution": execution, "material": binds(execution)}
        comparison = execution.get("comparison") or {}
        arms = comparison.get("arms", [])
        if "probe" in finding and "change" not in finding and (
                not arms or _probe_outcome(arms[0]) == _probe_outcome(arms[-1])
                or all(arm["outcome"] == "incomplete" for arm in arms)):
            observations.append(result)
            if report and arms and all(arm["outcome"] == "passed" for arm in arms):
                if recaptures is None:
                    recaptures = {held["execution"]["comparison"]["command"]: held["ref"] for _, held in _retained(identity, state)
                                  if held["stage"] == "final" and held["execution"]["kind"] == "probe"}
                if prior := recaptures.get(comparison["command"]):
                    result.update(priorFinding=prior, recapture={key: comparison[key] for key in ("command", "probeTree", "probeFiles")})
            if finding.get("retires") or result.get("recapture"):
                executed.append({key: value for key, value in result.items() if key != "probe"}
                                | {"execution": {"kind": "claim"}})
        else:
            executed.append(result)
    again = list(withdrawn)
    for entry, finding in retained:
        execution = run(finding, False)
        again.append({**finding["ref"], "execution": {**execution, **{key: finding["execution"][key]
                                                                     for key in ("original",) if key in finding["execution"]}}})
    return {"measuredTree": candidate, "measuredEvidence": state.get("tddEvidence"), "findings": executed, "reexecuted": again, "observations": observations,
            "runs": list({tuple(arm.get("key") for arm in run["arms"]): run for run in cached
                          if tuple(arm.get("key") for arm in run["arms"]) not in loaded_keys
                          and not any(run is item["execution"].get("comparison") for item in observations)}.values())}


def _execution_cache(identity, state):
    scope = _EXECUTION.get()
    cache = repo_state_dir(identity) / "execution-trees" / state["workflowId"]
    if scope.get("cache_root") != cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        scope["resources"].enter_context(_flock(cache.with_suffix(".lock")))
        original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
        scope.update(identity=identity, cache_root=cache, finish=_finish_kept_trees,
                     kept={original: cache / "original", _active_candidate_tree(identity): cache / "candidate"})


@_comparison_scope()
def check_retained(identity: RepoIdentity, slug: str | None, workflow_id: str | None) -> JsonObject:
    from .workflow_state import commit_retained_checks, finding_references, _finding_unresolved
    state = _bound_instance_state(read_active(identity), slug, workflow_id)
    _execution_cache(identity, state)
    state = _bound_instance_state(read_active(identity), slug, workflow_id)
    tree = _active_candidate_tree(identity)
    references = finding_references(state)
    cached = evidence_document(identity, state.get("retainedChecks", {}).get(tree), restore_runs=False) or {}
    if cached.get("workflowId") != state["workflowId"] or cached.get("candidateTree") != tree:
        cached = {}
    retained = _retained(identity, state)
    if (cached.get("findingRefs") == references and cached.get("runnerKey") == _runner_digest()
            and isinstance(cached.get("inputIdentities"), list)
            and not any(_finding_unresolved(entry) for entry, _ in retained)):  # a pending finding replays: a missing input of its probe may be back
        try:
            reusable = all(record["digest"] == _input_identity(identity, record["execution"], record) for record in cached["inputIdentities"])
        except (WorkflowError, OSError):
            reusable = False
        if reusable:
            return state
    reports = [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"], "execution": {"kind": "claim"}}
               for entry in state.get("findingStates", []) if _finding_unresolved(entry)
               and all(entry is not other for other, _ in retained)]
    current = evidence_document(identity, state.get("tddEvidence")) or {}
    execution = execute_findings(identity, state, [], retained=retained, current=current)
    execution["reexecuted"].extend(reports)
    execution["runnerKey"] = _runner_digest()
    execution["inputIdentities"] = [value for value in _EXECUTION.get()["inputs"].values() if isinstance(value, dict)]
    return commit_retained_checks(identity, str(state["slug"]), str(state["workflowId"]), execution, references)


def _retired(state: JsonObject) -> set[str]:
    """Frozen expectations an indexed author observation retired (`tdd --as ... --retire`); withdrawal removes its records."""
    return {case["key"] for entry in state.get("findingStates", []) for case in entry.get("retires", []) if case["key"] is not None}


def _evaluation(identity: RepoIdentity, state: JsonObject, current: JsonObject | None, binding: bool = False) -> JsonObject:
    """Retained proof; storage keeps retired cases, so withdrawal restores them; `binding` readers skip them."""
    if not current or "evaluation" in current:
        retired = _retired(state) if binding else ()
        return {key: dict(entry) for key, entry in (current or {}).get("evaluation", {}).items()
                if not retired or behavior_map.case_key(entry["id"], current["runs"][entry["runIndex"]]) not in retired}
    required = {item["id"] for item in behavior_map.recorded_map(None, evidence_document(identity, state.get("preflightEvidence"))) or []}
    retained = {}
    references = (reference for event in history(identity, state["workflowId"])["events"] if event["kind"] == "tdd-comparison"
                  for reference in event["evidenceIds"])
    for reference in references:
        document = evidence_document(identity, reference) or {}
        behavior_map.retain_evaluation(behavior_map.recorded_map(document) or [], required, retained)
        if retained.keys() == required:
            break
    return retained


def _case_environment(identity: RepoIdentity, text: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """A case command's words after its leading assignments, those assignments, and the environment the runner gives it."""
    command, assignments = shlex.split(text), {}
    while command and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
        name, value = command.pop(0).split("=", 1)
        assignments[name] = value
    environment = _environment() | assignments
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(
        os.path.normpath(os.path.join(identity.root, entry)) for entry in (str(identity.root), *environment.get("PYTHONPATH", "").split(os.pathsep))))
    return command, assignments, environment


def _comparison_input(identity: RepoIdentity, probe: JsonObject) -> tuple[list[str], JsonObject, dict[str, str]]:
    command, assignments, environment = _case_environment(identity, probe["command"])
    execution = {"executable": _executable(identity, command[0], environment, probe),
                 "assignments": assignments,
                 "search": os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path(environment)),
                 "lookup": command[0] if not os.path.dirname(command[0]) else None,
                 "environment": hashlib.sha256(json.dumps(sorted(environment.items())).encode()).hexdigest(),
                 **({"source": probe["sourceCommand"]} if probe.get("folders") else {})}  # the command a folder's program runs as where it is
    return command, execution, environment


def _growth(execution):
    value = execution.get("assignments", {}).get("WORKFLOW_GROWTH")
    if value is None:
        return None
    try:
        small, large, power = value.split(",")
        result = int(small), int(large), float(power)
    except (ValueError, TypeError):
        raise WorkflowError("WORKFLOW_GROWTH requires SMALL,LARGE,MAX_POWER") from None
    if not 0 < result[0] < result[1] or not 0 <= result[2] <= 10:
        raise WorkflowError("growth requires 0 < SMALL < LARGE and 0 <= MAX_POWER <= 10")
    return result


def _work_errors(run):
    """The author-declared growth bound; other observed work is reported, never judged."""
    arms, errors = run.get("arms", []), []
    if not arms or arms[-1].get("outcome") != "passed" or not arms[-1].get("work") or not (sizes := _growth(run["execution"])):
        return errors
    def increased(first, last, ratio, metrics=("processes", "cpuSeconds")):
        for metric in metrics:
            x, y = first.get(metric), last.get(metric)
            if x is not None and y is not None and y > x * ratio and (metric != "cpuSeconds" or y - x * ratio > max(.02, x * .2)):
                errors.append(f"growth: {metric} {x:g} -> {y:g} (limit {x * ratio:g})")
    if len(arms) != 4:
        errors.append("growth case lacks both sizes")
    elif all(arm.get("outcome") == "passed" for arm in arms[2:]):
        ratio = (sizes[1] / sizes[0]) ** sizes[2]
        increased(arms[2].get("work", {}), arms[-1]["work"], ratio)
        try:
            measurements = []
            for arm in arms[2:]:
                _, marker, text = ("\n" + arm["output"]).rpartition("\nWORKFLOW_METRICS=")
                measurements.append(json.loads(text.partition("\n")[0] if marker else arm["output"]))
            small, large = measurements
            if not isinstance(small, dict) or not isinstance(large, dict) or small.keys() != large.keys() or any(
                    type(value) not in (int, float) or not math.isfinite(value) or value < 0 for metrics in (small, large) for value in metrics.values()):
                raise ValueError("growth metrics must be matching objects of finite nonnegative numbers")
            increased(small, large, ratio, small)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append("growth metrics unavailable: " + str(exc))
    return errors


@lru_cache(maxsize=128)
def _identifiers(path: str, text: str) -> Counter[str]:
    if Path(path).suffix != ".py":
        return Counter(NAME.findall(text))
    import io
    import keyword
    import tokenize
    try:
        return Counter(token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                       if token.type == tokenize.NAME and not keyword.iskeyword(token.string))
    except (tokenize.TokenError, IndentationError) as exc:
        raise ValueError("break refused: invalid Python tokens: " + str(exc)) from exc


def _memory_available() -> int:
    with open("/proc/meminfo") as meminfo:
        return next(int(line.split()[1]) for line in meminfo if line.startswith("MemAvailable:")) * 1024


@_comparison_scope()
def _compare_many(identity: RepoIdentity, original: str, candidate: str, probes: list[JsonObject],
                  runs: list[JsonObject], *, challenge: bool = True, passing_original: bool = False) -> list[JsonObject]:
    """One execution/cache owner: each probe compares the two sources."""
    probes = [{**probe, "timeout": min(MAX_CASE_SECONDS, probe["timeout"])} for probe in probes]
    scope = _EXECUTION.get()
    cache = {}
    for run in [*runs, *probes]:
        for arm in run.get("arms", []):
            if arm.get("contextKey") and arm.get("outcome") in {"passed", "failed"}:
                cache.setdefault(arm["contextKey"], {})[arm["key"]] = arm
    def finish(probe, tree, execution, selected, before, after):
        arms = []
        for source, context, found in selected:
            if not isinstance(found, dict):
                found = pending[context].result()
            if "key" not in found:
                inputs = _input_identity(identity, execution, found)
                found = {**found, "contextKey": context, "inputs": inputs,
                         "key": hashlib.sha256((context + inputs).encode()).hexdigest()}
                cache.setdefault(context, {})[found["key"]] = found
            arms.append({**found, "requestedTree": source})
        keys = [arm["key"] for arm in arms]
        metadata = {"command": probe["command"], "support": probe.get("support") or [], "timeout": probe["timeout"],
                    **{key: probe[key] for key in ("sourceCommand", "scratch", "origins", "folders", "temporary", "fileTemporaryRoots", "scratchScope", "absentInputs") if key in probe},
                    "behaviorId": probe.get("behaviorId"), "probeFiles": probe["probeFiles"],
                    "candidateTree": after, "probeTree": tree, "originalTree": before,
                    "captureKey": probe.get("captureKey", probe.get("candidateKey", keys[-1]))}
        retained = next((run for run in reversed(runs) if run["candidateTree"] == after
                         and run["arms"] == arms
                         and all(run.get(key) == value for key, value in metadata.items())), None)
        if retained is not None:
            return retained
        outcomes = [arm["outcome"] for arm in arms]
        valid = all(arm["outcome"] == "passed" for arm in arms[len(arms) // 2:]) and not any(arm["timedOut"] for arm in arms) and outcomes[0] in {"passed", "failed"}
        run = {**metadata, "runIndex": len(runs), "candidateKey": keys[-1],
               "comparison": "incomplete" if not valid else "preserved" if all(value == "passed" for value in outcomes) else "changed",
               "arms": arms, "valid": valid, "execution": execution, "exitCode": 0 if valid else 1,
               "timedOut": any(arm["timedOut"] for arm in arms)}
        runs.append(run)
        return run
    pending, alone = {}, {}
    def schedule(source, probe, prepared, parent=None, size=None, passing=None):
        files, timeout = _comparison_files(identity, probe), probe["timeout"]
        command, execution, environment = prepared
        if size is not None:
            command = [arg.replace("{size}", str(size)) for arg in command]
        tree = probe.get("probeTree", probe["candidateTree"])
        context = _execution_key(identity, source, tree, files, command, timeout, execution)
        # A lead's case names the sources its failure is accepted on; any other failure is run again, never served.
        found = next((arm for arm in reversed(list(cache.get(context, {}).values()))
                      if (arm["outcome"] == "passed" or source in probe.get("failing", (source,)))
                      and (inputs := _input_identity(identity, execution, arm)) and arm["key"] == hashlib.sha256((context + inputs).encode()).hexdigest()), None)
        if found is None:
            pending_key = context
            if pending_key not in pending:
                state, frozen = _prepared_tree(identity, scope, parent or source, tree, files, isolated=size is not None)
                # While a scratch program's folder is as it was frozen, each word naming a file of it stays as the lead wrote it: the
                # program runs where it is, beside what a tree cannot hold with it. Its frozen copy is written once it changed or is gone.
                apart = _apart(identity, probe)
                if apart:
                    command = [now if not os.path.realpath(os.path.join(identity.root, was.split("::", 1)[0])).startswith(apart[1]) else was if size is None else was.replace("{size}", str(size))
                               for was, now in zip(_case_environment(identity, probe["sourceCommand"])[0], command)]
                    frozen = {name: value for name, value in frozen.items() if not name.startswith(tuple(place + "/" for place in probe["folders"]))}
                if parent:  # a break is its parent's warm tree plus the break's changed files
                    entries = _tree_entries(identity, source)
                    changes = _changed_entries(identity, parent, source)
                    state = {**state, "owner": state, "base": {path: value for path, value in state["base"].items() if path not in changes}
                             | {path: entries[path] for path in changes if path in entries}}
                if scope["pool"] is None:  # each concurrent case owns one CPU, offset per runner: tools sizing pools to the machine start one worker
                    cpus = sorted(os.sched_getaffinity(0))
                    starts = iter(range(os.getpid(), os.getpid() + 8))  # advanced in C: concurrent initializers cannot collide
                    scope["pool"] = ThreadPoolExecutor(min(len(cpus), 8), initializer=lambda: os.sched_setaffinity(0, [cpus[next(starts) % len(cpus)]]))
                    scope["memory"] = _memory_available() // min(len(cpus), 8)  # and an equal share of the memory available when the pool starts
                root_key = parent or source
                previous = scope["first"].get(root_key) if state["wait"] and size is None else None
                _require_overlay(_executable(identity, "bwrap", environment))
                execute = partial(_after, previous, identity, source, (state, frozen), command, timeout, environment, scope["cancel"],
                                  derived=bool(parent), passing=passing, apart=apart,
                                  first=size is None and not parent and root_key not in scope["first"])
                pending[pending_key] = scope["pool"].submit(execute, scope["memory"], tuple(scope["objects"].items()))
                if not parent:
                    alone[pending_key] = execute, id(probe)
                if not parent and size is None:
                    scope["first"].setdefault(root_key, pending[pending_key])
            found = pending[pending_key]
        return (source, context, found)
    plans, contexts = [], {}
    for probe in probes:
        prepared = _comparison_input(identity, probe)
        command, execution, _ = prepared
        tree = probe.get("probeTree", probe["candidateTree"])
        sizes = _growth(execution)
        if sizes and not any("{size}" in arg for arg in command):
            raise WorkflowError("growth command must contain {size}")
        mounted_sets = []
        for size in sizes[:2] if sizes else (None,):
            value = [arg.replace("{size}", str(size)) for arg in command] if size is not None else command
            mounted_sets.append(value)
            for source in probe.get("sources", (original, candidate)):
                contexts[_execution_key(identity, source, tree, _comparison_files(identity, probe), value, probe["timeout"], execution)] = execution
        oid = probe.get("breakOid")
        control = None
        if challenge and oid and not sizes:  # a growth case's two-size comparison is its own check
            try:
                source = original if passing_original else candidate
                key = (source, oid)
                if key not in scope["breaks"]:
                    prior = next((run for run in runs if run.get("breakOid") == oid and run.get("brokenSource") == source), None)
                    if (prior and _git(identity, "diff", "--no-ext-diff", "--full-index", source, prior["candidateTree"])
                            == _git(identity, "cat-file", "blob", oid)):
                        scope["breaks"][key] = (oid, prior["candidateTree"])
                    else:
                        broken = _indexed(identity, source, (("apply", "--cached", "-C1", "--unidiff-zero", "-"), _git(identity, "cat-file", "blob", oid)))
                        pairs = _changed_entries(identity, source, broken)
                        blobs = read_blobs(Path(identity.root), [oid for pair in pairs.values() for oid in pair if oid.strip("0")])
                        if not pairs or any(is_test_path(path) or _identifiers(path, blobs.get(a) or "") != _identifiers(path, blobs.get(b) or "") for path, (a, b) in pairs.items()):
                            raise ValueError("break refused: change values or operators only; keep identifier counts and test support unchanged")
                        canonical = _git(identity, "diff", "--no-ext-diff", "--full-index", source, broken)
                        normalized = _git(identity, "hash-object", "-w", "--stdin", stdin=canonical).decode().strip()
                        scope["breaks"][key] = scope["breaks"][(source, normalized)] = (normalized, broken)
                probe["breakOid"], broken = scope["breaks"][key]
                for value in mounted_sets:
                    contexts[_execution_key(identity, broken, tree, _comparison_files(identity, probe), value, probe["timeout"], execution)] = execution
                control = (source, broken)
            except (RuntimeError, ValueError) as exc:
                control = str(exc)
        plans.append((probe, tree, prepared, control))
    def keys(arm):
        context = arm["contextKey"]
        return [hashlib.sha256((context + _input_identity(identity, contexts[context], arm)).encode()).hexdigest()]
    workflows = scope.setdefault("workflow_ids", {})
    if identity.key not in workflows:
        workflows[identity.key] = instance_id(read_active(identity) or {})
    for arm in cached_arms(identity, workflows[identity.key], contexts, keys):
        cache.setdefault(arm["contextKey"], {}).setdefault(arm["key"], arm)  # a result the caller recorded later stands
    jobs, controls = [], []
    def selections(first, last, probe, prepared, parent=None):
        sizes = _growth(prepared[1])
        return ([schedule(source, probe, prepared, size=size)
                 for source, size in ((first, sizes[1]), (first, sizes[0]), (last, sizes[0]), (last, sizes[1]))]
                if sizes else [(good := schedule(first, probe, prepared)), schedule(last, probe, prepared, parent, passing=good[2] if parent else None)])
    for probe, tree, prepared, control in plans:
        execution = prepared[1]
        first, last = probe.get("sources", (original, candidate))  # a probe read on one source runs on that source only
        jobs.append((probe, tree, execution, selections(first, last, probe, prepared), first, last))
    # A case out of time or memory beside other cases runs each incomplete arm alone with all available memory; one
    # that ran a model session, its own clock, does not. Only its own run is held to the limit. Every case has ended
    # before the first such run starts.
    cases, again = {case for _, case in alone.values()}, {}
    for key, (_, case) in alone.items():
        if (result := pending[key].result())["outcome"] == "incomplete" and not result.get("modelSession") and (len(cases) > 1 or not result["timedOut"]):
            again.setdefault(case, []).append(key)
    for keys in again.values():
        for key in keys:
            pending[key] = scope["pool"].submit(alone[key][0], _memory_available(), tuple(scope["objects"].items()))
            pending[key].result()
    compared = [finish(*job) for job in jobs]
    for (probe, tree, prepared, control), result in zip(plans, compared):
        result["sensitive"] = bool(_growth(result["execution"])) or (not passing_original
            and result["arms"][0]["outcome"] == "failed" and result["arms"][-1]["outcome"] == "passed")
        if isinstance(control, tuple) and not result["sensitive"]:
            source, broken = control
            target = result["arms"][0 if passing_original else -1]
            control = ((probe, tree, prepared[1], selections(source, broken, probe, prepared, source), source, broken)
                       if target["outcome"] == "passed" else None)
        controls.append(control)
    if challenge:
        for probe, result, control in zip(probes, compared, controls):
            result.pop("controlKey", None)
            result.pop("breakExit", None)
            if not probe.get("breakOid") or result["sensitive"]:
                continue
            result["breakOid"] = probe["breakOid"]
            if control is None:
                continue
            if isinstance(control, str):
                result["breakError"] = control
                continue
            checked = finish(*control)
            checked.update(breakOid=probe["breakOid"], brokenSource=checked["originalTree"])
            good = result["arms"][0] if passing_original else result["arms"][-1]
            result["controlKey"] = checked["candidateKey"]
            result["sensitive"] = good["outcome"] == "passed" and checked["arms"][0]["key"] == good["key"] and checked["arms"][-1]["outcome"] == "failed" and not checked["timedOut"]
            result["breakExit"] = checked["arms"][-1]["exitCode"]
            result.pop("breakError", None)
    return compared

def _compare(identity: RepoIdentity, original: str, candidate: str, probe: JsonObject, runs: list[JsonObject], *, passing_original: bool = False) -> JsonObject:
    return _compare_many(identity, original, candidate, [probe], runs, passing_original=passing_original)[0]


def _case_keys(items: list[JsonObject], runs: list[JsonObject], evaluation: JsonObject, selections: list[list[str]]) -> list[str]:
    """Each selection's case key: a key or item id as given; an item and a command, every recorded case of that item
    running the command, its leading assignments aside. A pair that names no case stays as written, unmatched."""
    def bare(text: str) -> list[str]:
        words = shlex.split(text)
        return words[next((index for index, word in enumerate(words) if not re.match(r"[A-Za-z_][A-Za-z_0-9]*=", word)), len(words)):]
    keys = []
    for selection in selections:
        if len(selection) == 1 or all(re.fullmatch(r"[A-Z][A-Z0-9_-]+:[^\s:]+", value) for value in selection):
            keys.extend(selection)
            continue
        if len(selection) != 2:
            raise WorkflowError("--retire takes case keys, or an item and its command")
        identifier, command = selection
        runs_of = [case["comparison"] for case in behavior_map.current_cases([entry for entry in items if entry["id"] == identifier], runs)]
        runs_of += [runs[frozen["runIndex"]] for frozen in evaluation.values() if frozen["id"] == identifier]
        keys += list(dict.fromkeys(behavior_map.case_key(identifier, run) for run in runs_of
                                   if bare(command) in (bare(run.get("sourceCommand", run["command"])), bare(run["command"])))) or [shlex.join(selection)]
    return keys


# Mask the elapsed field of a measurement or run summary. A duration in a diagnostic is data.
ELAPSED_LINE = re.compile(
    r"(?im)^((?:(?:candidate|original) [^:\n]+: )?[ \t]*(?:=+[ \t]*)?"
    r"(?:ran \d+ tests? in|\d+ (?:passed|failed)(?:, \d+ \w+)* in|(?:ok|pass|fail)\s+\S+|"
    r"elapsed(?: time)?[:=]|[\w.-]+ [-+]?\d+(?:\.\d+)? at)[ \t]+)"
    r"\d+\.\d+([ \t]*(?:ns|us|µs|ms|s|sec(?:ond)?s?|minutes?)[ \t]*=*[ \t]*)$"
)


def _deliver(identity: RepoIdentity, receipt: JsonObject, role: str = "lead", emit=_emit_json) -> None:
    """Publish each reader's delta. Only the evidence and the unfiltered receipt decide readiness."""
    if CHECK_ONLY.get():
        emit(receipt)
        return
    path = repo_state_dir(identity) / "execution-trees" / receipt["workflowId"] / ("delivered-" + role + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    def digest(value):
        return hashlib.blake2s(json.dumps(value, sort_keys=True).encode(), digest_size=12).hexdigest()
    # A verdict or a map-only call needs no execution lock. Concurrent replies to this reader still serialize.
    with _flock(path.with_suffix(".lock")):
        seen = read_json(path) or {}
        history = dict(seen)
        aliases, results, listed = {}, {}, []
        for row in receipt.get("cases", []):
            run, counted = row.pop("_run", None), row.pop("_counted", False)
            source = run.get("sourceCommand", run["command"]) if run else row.get("case")
            case_key = row.get("caseKey", "")
            key = digest([row.get("findingId") or case_key.partition(":")[0], source])
            if case_key:
                aliases[case_key] = key
            if counted and key not in seen:
                continue
            result = {name: value for name, value in row.items() if name not in ("case", "caseKey")}
            if isinstance(errors := result.get("unexpected"), list):
                result["unexpected"] = [ELAPSED_LINE.sub(r"\1<time>\2", error) for error in errors]
            signature = digest(result)
            group = results.setdefault(key, [])
            if signature in group:
                continue
            group.append(signature)
            if signature in seen.get(key, []):
                continue
            if run:
                row["case"] = shown(identity, receipt, run)
            if command := row.get("case"):
                ref = "cmd-" + digest(command)
                if ref in history:
                    del row["case"]
                    if history[ref] != case_key:
                        row["commandFrom"] = history[ref]
                else:
                    history[ref] = case_key or row.get("findingId") or key
            listed.append(row)
        history.update(results)
        if "cases" in receipt:
            receipt["cases"] = listed
        if "open" in receipt:
            lines = receipt["open"]
            hashes = [digest(re.sub(r"[A-Z][A-Z0-9_-]+:[0-9a-f]{32,64}", lambda m: aliases.get(m[0], m[0]), line)) for line in lines]
            old = seen.get("open", [])
            history["open"] = hashes
            receipt["open"] = [line for line, key in zip(lines, hashes) if key not in old]
            if lines and receipt.get("summaryId") and not seen.get("fullCall"):
                receipt["full"] = ["evidence", "--evidence-id", receipt["summaryId"]]
                history["fullCall"] = True
            if lines and not receipt["open"]:
                receipt["open"] = {"unchanged": len(lines), "evidenceId": receipt.get("summaryId")}
        for field in ("verdictCommand", "unmetVerdictCommand"):
            if field in receipt:
                history[field] = digest(receipt[field])
                if history[field] == seen.get(field):
                    del receipt[field]
                else:
                    # Both verdicts share an immutable, source-bound call that runs from any directory.
                    prefix, verdict = receipt[field].rsplit(" ", 1)
                    script = path.with_name("verdict-" + digest(prefix) + ".sh")
                    if not script.exists():
                        atomic_write_bytes(script, ('exec ' + prefix + ' "$@"\n').encode())
                    receipt[field] = shlex.join(["sh", str(script), verdict])
        emit(receipt)
        if history != seen:
            atomic_write_json(path, history)


@interruptible()
@_comparison_scope()
def _run_tdd(values: list[str], *, update: list[JsonObject] | None = None) -> int | JsonObject:
    """`update`, a map change recorded with this call's comparison: one commit publishes both."""
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", usage="%(prog)s [OPTIONS] [--case ITEM COMMAND | --case ITEM:COMMAND] "
        "[--correct ID [DECLARATION] COMMAND | --correct CASEKEY COMMAND] [--retire CASEKEY [CASEKEY ...] | --retire ITEM COMMAND] [-- COMMAND ...]", description="Compare and record probes or replacements; authors record verdicts from their checkpoint command", epilog=binds.__doc__)
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--workflow-id")
    parser.add_argument("--expected-candidate-tree")
    parser.add_argument("--tdd-evidence", default=argparse.SUPPRESS)
    parser.add_argument("--verdict", help="record review completion without running cases; use the source-bound checkpoint command")
    parser.add_argument("--report", action="append", default=[], help="finding's owning invariant, or non-executable report lines with a verdict")
    parser.add_argument("--reject", action="append", nargs="+", default=[], metavar="ID 'COMMAND'",
                        help="the lead closes a map item or final finding it rejects: the workflow runs the command once and, when it "
                             "exits 0, keeps its output with the rejection; with --as, EVIDENCE:ID=REASON withdraws this author's invalid observation")
    parser.add_argument("--retire", action="append", nargs="+", default=[], metavar="CASE",
                        help="one or more case keys, or one item and command; with --as and a finding probe, also an unproved map item id")
    parser.add_argument("--change", action="append", default=[], help="with --as: probe=FILE supplies a behavior-breaking diff for the authored probe; a bare FILE submits an authored replacement")
    parser.add_argument("--as", dest="author", choices=("review", "final"), help="execute and record this author's finding in the same call")
    parser.add_argument("--behavior-id", action="append", default=[], help="map item for a single trailing command, or items whose recorded cases replay")
    parser.add_argument("--case", action="append", nargs="+", default=[], metavar="CASE",
                        help="ITEM COMMAND or ITEM:COMMAND; '-' is unbound. Growth: WORKFLOW_GROWTH=SMALL,LARGE,MAX_POWER COMMAND {size}; counts may grow by at most (LARGE/SMALL)**MAX_POWER. Print WORKFLOW_METRICS=<JSON object> or JSON alone for numeric metrics. Growth checks scaling; assert saved work and runtime in the probe.")
    parser.add_argument("--correct", nargs="+", metavar="ARG", help="ID [DECLARATION] COMMAND or CASEKEY COMMAND: replace the selected case and run its correction")
    parser.add_argument("--support", action="append", default=[], help="a file outside the checkout the cases of this call read that no command word names: frozen with them")
    args = parser.parse_args(values[:dash])
    for selection in args.case:
        if len(selection) == 1:
            selection[:] = selection[0].split(":", 1)
        if len(selection) != 2 or not all(selection):
            raise WorkflowError("--case needs ITEM COMMAND or ITEM:COMMAND (use - for an unbound case)")
    if args.correct and len(args.correct) not in (2, 3):
        raise WorkflowError("--correct needs ID [DECLARATION] COMMAND or CASEKEY COMMAND")
    if len(args.behavior_id) > 1 and values[dash + 1:]:
        raise WorkflowError("one Behavior Map item per case command")
    command = values[dash + 1:]
    identity = resolve_repo_identity(args.repo)
    state = _bound_instance_state(read_active(identity), args.slug, args.workflow_id)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    if (workflow_id := instance_id(state)) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    slug = str(state["slug"])
    if args.reject and not args.author:
        if command or args.case or args.change or args.retire or args.report or args.verdict or args.behavior_id:
            raise WorkflowError("a rejection call carries only --reject ID 'COMMAND'")
        _deliver(identity, _reject(identity, state, args.reject))
        return 0
    if args.author and args.retire and not (command or args.case or args.change):
        raise WorkflowError("only the finding author can withdraw an observation, or retire a case beside its probe or replacement")
    if args.verdict and (args.author != "review" or args.verdict not in {"complete", "incomplete"}
                         or command or args.case or args.change or args.behavior_id):
        raise WorkflowError("select the review verdict without a probe, change or Behavior Map edit")
    breaks, paths, replacement = {}, {}, None
    for value in args.change:
        identifier, equal, name = value.partition("=")
        if not equal:
            if replacement is not None:
                raise WorkflowError("one replacement per call")
            replacement = value
            continue
        path = Path(name).resolve()
        if path.is_relative_to(identity.root) or not path.is_file():
            raise WorkflowError("keep the break diff in scratch outside the checkout")
        if path not in paths:
            paths[path] = _git(identity, "hash-object", "-w", "--", str(path)).decode().strip()
        breaks[identifier] = paths[path]
    if args.author:
        candidate = _active_candidate_tree(identity)
        captured, past = {}, None
        if args.verdict:
            if not args.workflow_id or not args.expected_candidate_tree or not hasattr(args, "tdd_evidence"):
                raise WorkflowError("use the source-bound verdict command from checkpoint")
            reviewed_evidence = None if args.tdd_evidence == "null" else args.tdd_evidence
            if args.expected_candidate_tree != candidate or reviewed_evidence != state.get("tddEvidence"):
                raise WorkflowError("reviewed source or comparison evidence changed; inspect the current checkpoint")
            findings = [{"id": "REPORT-" + hashlib.sha256(report.encode()).hexdigest()[:16], "claim": report}
                        for report in dict.fromkeys(args.report) if report.strip()]
        else:
            if args.case and (command or args.behavior_id) or args.behavior_id:
                raise WorkflowError("authored findings do not edit the Behavior Map")
            specs = args.case or ([("-", shlex.join(command))] if command else [])
            if any(identifier != "-" for identifier, _ in specs):
                raise WorkflowError("authored probes are unbound cases; use - as their item")
            if not (specs or replacement or args.reject):
                raise WorkflowError("an authored finding needs a case or change")
            change = None
            if replacement:
                path = Path(replacement).resolve()
                if path.is_relative_to(identity.root):
                    raise WorkflowError("keep the replacement diff in scratch outside the checkout")
                change = path.read_text()
            inventory = _probe_files([], identity, args.support) if specs else None
            probes = []
            if any(PROBES in value for _, value in specs):
                past = evidence_document(identity, state.get("tddEvidence")) or {}
            for _, value in specs:
                probe = _probe_capture(identity, {"command": value}, candidate, MAX_CASE_SECONDS, args.support, inventory, recorded=(past or {}).get("runs", ()))
                if "probe" in breaks:
                    probe["breakOid"] = breaks["probe"]
                captured[probe["command"]] = probe
                probes.append({"command": probe["command"]})
            claim = "\n".join(dict.fromkeys(report.strip() for report in args.report if report.strip()))
            items, current = current_map(identity, state, fresh=False) if args.retire else (None, None)
            current = current or {}
            available = {entry["id"]: {**entry, "key": None} for entry in items or []}
            retained = _evaluation(identity, state, current)
            args.retire = _case_keys(items or [], current.get("runs", []), retained, args.retire)
            for alias, entry in [*retained.items(), *((None, entry) for entry in behavior_map.current_cases(items or [], current.get("runs", [])))]:
                if "runIndex" in entry.get("comparison", entry):
                    key = behavior_map.case_key(entry["id"], current["runs"][(entry.get("comparison") or entry)["runIndex"]])
                    available[key] = {**entry, "key": key}
                    if alias is not None:
                        available[alias] = available[key]
            retires = [{"id": available[key]["id"], "key": available[key].get("key")} for key in dict.fromkeys(args.retire) if key in available]
            findings = []
            for probe in list({probe["command"]: probe for probe in probes}.values()) or ([None] if replacement else []):
                finding = {**({"probe": probe} if probe else {}), **({"change": change} if change is not None else {})}
                identifier = "FINDING-" + hashlib.sha256((claim + json.dumps(finding, sort_keys=True)).encode()).hexdigest()[:16]
                findings.append({"id": identifier, "claim": claim or "Recorded " + ("replacement" if change is not None else "probe")
                                 + ": " + (replacement or probe["command"]), **finding,
                                 **({"retires": retires} if retires else {})})
        for value in map(" ".join, args.reject):
            reference, equal, reason = value.partition("=")
            evidence, colon, identifier = reference.partition(":")
            if not equal or not colon or not evidence or not identifier or not reason.strip():
                raise WorkflowError("withdrawal needs EVIDENCE:ID=REASON")
            findings.append({"id": "REPORT-" + hashlib.sha256(value.encode()).hexdigest()[:16],
                             "claim": reason.strip(), "kind": "nonbehavioral", "rejected": True,
                             "priorFinding": {"evidenceId": evidence, "id": identifier}})
        if not args.verdict and any("probe" in finding or "change" in finding for finding in findings):
            _execution_cache(identity, state)
        execution = execute_findings(identity, state, findings, captured=captured, candidate=candidate, current=past, report=args.author == "final")
        document = {"schemaVersion": 1, "kind": "intake", "workflowId": workflow_id, "slug": slug,
                    "stage": "code-review" if args.author == "review" else "final",
                    "producer": "code-review" if args.author == "review" else "codex-advisor",
                    "candidateTree": args.expected_candidate_tree if args.verdict else execution["measuredTree"],
                    "tddEvidence": reviewed_evidence if args.verdict else execution["measuredEvidence"],
                    "findings": execution["findings"], **({"verdict": args.verdict} if args.verdict else {})}
        observations = execution.pop("observations", [])
        if document["findings"] or args.verdict:
            state, evidence = commit_findings(identity, slug, workflow_id, document, execution)
        else:
            evidence = None
        receipt = operation_receipt(state, identity, kind="tdd", obligations=False,
                                    **({"summaryId": evidence} if evidence else {}))
        if args.author == "review":
            from .workflow_state import checkpoint
            commands = checkpoint(identity, "code-review")
            receipt.update({key: commands[key] for key in ("verdictCommand", "unmetVerdictCommand")})
        if args.verdict:
            _deliver(identity, receipt, args.author)
            return 0
        rows = []
        for finding in {item["id"]: item for item in [*execution["findings"], *observations]}.values():
            result = finding["execution"]
            refused = "error" in result
            row = {"case": (finding.get("probe") or {}).get("command", replacement), "findingId": finding["id"],
                   "result": "withdrawn" if finding.get("rejected") else "refused" if refused else "binds" if finding["material"] else "doesn't bind"}
            if finding.get("retires"):  # recorded whatever the probe's own result
                row["retired"] = sorted({case["id"] for case in finding["retires"]})
            compared = result.get("comparison") or {}
            if finding.get("probe") and compared:
                row["_run"] = compared
            if result.get("failure"):
                row["failure"] = result["failure"]
            if compared:
                row["arms"] = behavior_map.comparison_view(compared, detail=True)["arms"]
            if refused:
                row["unexpected"] = str(result["error"])
            rows.append(row)
        receipt["cases"] = rows
        if missing := [key for key in dict.fromkeys(args.retire) if key not in available]:
            receipt["unmatched"] = missing  # a retained case key or unproved map item id that names nothing
        _deliver(identity, receipt, args.author)
        return 2 if any(row["result"] == "refused" and "retired" not in row for row in rows) else 0
    if replacement or breaks or args.report and not args.retire:
        raise WorkflowError("changes and reports require --as review or --as final")
    if args.case and (command or args.behavior_id):
        raise WorkflowError("use repeated --case entries or one trailing command")
    _execution_cache(identity, state)
    state = _bound_instance_state(read_active(identity), slug, workflow_id)
    recorded, current = current_map(identity, state, fresh=False)
    items = update if update is not None else recorded
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    signature = json.dumps(recorded, sort_keys=True)
    runs = list((current or {}).get("runs", []))
    evaluation = _evaluation(identity, state, current)
    args.retire = _case_keys(items, runs, evaluation, args.retire)
    correct_key = args.correct[0] if args.correct and ":" in args.correct[0] else None
    replacement_case = None
    if correct_key:
        if len(args.correct) != 2:
            raise WorkflowError("--correct CASEKEY takes just its replacement command")
        identifier, corrected = correct_key.partition(":")[0], args.correct[-1]
        replacement_case = (identifier, corrected)
        args.case.append(replacement_case)
    elif args.correct:
        identifier, corrected = args.correct[0], args.correct[-1]
        entry = behavior_map.item(items, identifier)
        if len(args.correct) == 2:
            choices = entry.get("cases") or list(dict.fromkeys(case["comparison"].get("sourceCommand", case["comparison"]["command"])
                for case in behavior_map.current_cases([entry], runs, _retired(state))))
            if corrected in choices:
                choices = [corrected]
            if len(choices) != 1:
                raise WorkflowError(f"{identifier} needs the declaration to replace: " + (" | ".join(choices) or "no case is declared or recorded"))
            args.correct.insert(1, choices[0])
        declaration = args.correct[1]
        if declaration in entry.get("cases", []):
            entry["cases"] = [corrected if value == declaration else value for value in entry["cases"]]
        else:  # it already ran: the corrected command runs as the item's case and the declaration's recorded case is retired
            ran = _case_keys(items, runs, evaluation, [[identifier, declaration]])
            if ran == [shlex.join([identifier, declaration])]:
                raise WorkflowError(f"{identifier} declares and recorded no case {declaration!r}")
            args.retire += ran
            args.case.append([identifier, corrected])
    retired = _retired(state)
    by_key = {}
    if correct_key or args.retire:
        by_key = {behavior_map.case_key(entry["id"], run): run for entry in evaluation.values()
                  for run in [runs[entry["runIndex"]]]}
        by_key.update((behavior_map.case_key(case["id"], case["comparison"]), case["comparison"])
                      for case in behavior_map.current_cases(items, runs))
        if correct_key:
            if correct_key in retired or any(row["caseKey"] == correct_key for row in (current or {}).get("retired", [])):
                raise WorkflowError("already retired case key " + correct_key)
            if correct_key not in by_key or not any(entry["id"] == identifier for entry in items):
                raise WorkflowError("no bound case has case key " + correct_key)
    frozen_signature = json.dumps(evaluation, sort_keys=True)
    candidate = _active_candidate_tree(identity)
    before = tree_manifest(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    specs = [(identifier if identifier != "-" else None, value) for identifier, value in args.case]
    if command:
        specs = [(next(iter(args.behavior_id), None), shlex.join(command))]
    # A call that selects no item also captures the commands map items still declare and judges the whole map: beside
    # named cases the rest is judged as recorded, never run. A captured command leaves its item's `cases`.
    rejected = rejections(state, "item")  # set aside: never replayed
    drafted = [] if args.behavior_id and update is None else list(dict.fromkeys((entry["id"], value) for entry in items
                                                             if entry["id"] not in rejected and (not args.behavior_id or entry["id"] in args.behavior_id)
                                                             for value in entry.get("cases", []) if not args.correct or (entry["id"], value) == (args.correct[0], args.correct[-1])))
    selected = []
    if not specs and not args.correct:
        for entry in items:
            if args.behavior_id and entry["id"] not in args.behavior_id or entry["id"] in rejected:
                continue
            for case in behavior_map.current_cases([entry], runs, retired):
                selected.append((entry, case["comparison"]))
        if set(args.behavior_id) - {entry["id"] for entry in items}:
            raise WorkflowError("unknown Behavior Map item")
    inventory = _probe_files([], identity, args.support) if specs or drafted else None
    refused, asked = [], []
    for identifier, value in dict.fromkeys([*specs, *drafted]):
        entry = captured = None
        key_correction = (identifier, value) == replacement_case
        try:  # a case that cannot be captured or prepared is refused alone; the rest of the call is recorded
            entry = behavior_map.item(items, identifier) if identifier else None
            captured = _probe_capture(identity, {"command": value}, candidate, MAX_CASE_SECONDS, args.support, inventory, original if entry and entry.get("kind") == "preservation" else None, runs)
            _comparison_input(identity, captured)
        except (ValueError, RuntimeError, OSError) as exc:
            reason = str(exc).removesuffix(": -: " + captured["command"]) if captured and not key_correction else str(exc)
            selector = [correct_key] if key_correction else [identifier, value]
            refused.append({"refused": reason, **({"correct": ["tdd", "--correct", *selector], "missing": "replacement command"}
                           if entry is not None else {"case": value})})
            if entry is not None and not key_correction and value not in entry.get("cases", []):
                entry.setdefault("cases", []).append(value)
            captured = None
        if (identifier, value) in drafted and captured:
            entry["cases"] = [other for other in entry["cases"] if other != value]
            if not entry["cases"]:
                del entry["cases"]
        if captured is None:
            continue
        if key_correction:
            args.retire.append(correct_key)
        if entry is not None:
            prior = entry.get("comparison") or {}
            references = prior.get("caseRuns")
            if not references:
                references = {case["comparison"]["command"]: case["comparison"]["runIndex"]
                              for case in behavior_map.current_cases([entry], runs)}
            if captured["sourceCommand"] in references:
                known = runs[references[captured["sourceCommand"]]]  # a recorded case's frozen command is that case: its command and the scratch files it froze
                captured["sourceCommand"] = known.get("sourceCommand", captured["sourceCommand"])
                captured.update({key: known[key] for key in ("scratch", "origins", "folders", "temporary", "fileTemporaryRoots", "absentInputs") if key in known})
            captured["behaviorId"] = entry["id"]
        selected.append((entry, captured))
        asked += [captured] if (identifier, value) in specs else []
    # On every call a recorded case whose scratch program or its folder changed is first captured again, as naming it
    # would; one without accepted proof is also run again where it failed and captured again once the candidate changes
    # a test file it froze, as is a case a finding retired. Accepted proof otherwise keeps its frozen copy.
    named = {(probe.get("behaviorId"), probe["sourceCommand"]) for _, probe in selected if "runIndex" not in probe}
    replayed = {probe["runIndex"]: position for position, (_, probe) in enumerate(selected) if "runIndex" in probe}
    again = set()
    for case in behavior_map.current_cases([entry for entry in items if not args.behavior_id or entry["id"] in args.behavior_id], runs):
        run, key = case["comparison"], behavior_map.case_key(case["id"], case["comparison"])
        source = (case["id"], run.get("sourceCommand", run["command"]))
        if source in named or key in args.retire or case["id"] in rejected:
            continue
        entry, captured, accepted = behavior_map.item(items, case["id"]), None, not (key in retired or behavior_map._case_question(case))
        try:
            captured = _revised(identity, run, candidate, original if case.get("kind") == "preservation" else None, accepted)
        except (ValueError, RuntimeError, OSError) as exc:  # refused alone: the case stays pending on its recorded copy, its command kept
            refused.append({"case": source[1], "refused": str(exc)})
        if captured:
            captured["behaviorId"] = entry["id"]
            again.add(source)
        position = replayed.get(run["runIndex"])
        if captured and position is not None:
            selected[position] = (entry, captured)
        elif captured or position is None and key not in retired and not accepted:
            selected.append((entry, captured or dict(run)))
    # Retained first proofs: all of them without named cases, beside named cases those without accepted proof; none beside
    # the capture this call makes of its command.
    selected_indexes = {probe.get("runIndex") for _, probe in selected}
    selected.extend((None, dict(runs[index]))
                    for frozen in evaluation.values() if behavior_map.case_key(frozen["id"], runs[frozen["runIndex"]]) not in retired and (not args.behavior_id or frozen["id"] in args.behavior_id)
                    and frozen["id"] not in rejected and (index := frozen["runIndex"]) not in selected_indexes and (frozen["id"], runs[index].get("sourceCommand", runs[index]["command"])) not in again
                    and (not specs or behavior_map._case_question({**frozen, "comparison": runs[index]})))
    retiring = {}
    for key in dict.fromkeys(args.retire):  # each named case is compared in this call, whatever else it selects
        run = by_key.get(key)
        if run is None:  # superseded by a later capture, released to another item, or its item removed: nothing is left to retire
            continue
        retiring[key] = run["runIndex"]
        if all(probe.get("runIndex") != run["runIndex"] for _, probe in selected):
            selected.append((None, dict(run)))
    kinds = {entry["id"]: entry.get("kind") for entry in (*evaluation.values(), *items)}
    for _, probe in selected:  # only the original of a changed result may fail: any other failure is run again, never served
        probe["failing"] = () if kinds.get(probe.get("behaviorId")) == "preservation" else (original,)
    compared = _compare_many(identity, original, candidate, [probe for _, probe in selected], runs, challenge=False)
    edited = {name for name, (_, oid) in _changed_entries(identity, original, candidate).items() if is_test_path(name) and oid.strip("0")}
    recapture = {}
    for pos, ((entry, probe), run) in enumerate(zip(selected, compared)):
        if not edited or not entry or entry.get("kind") != "preservation" or behavior_map.original_outcome(run) == "passed":
            continue
        # Select current test bytes only after observing the original failure. Other frozen inputs keep their bytes.
        files = edited.intersection(run["probeFiles"])
        if added := edited - files:  # a command naming a test file the candidate edited or added, by path, directory or module, is that test's case: it reads the added test files a direct run has
            words = [word.split("::", 1)[0].rstrip("/") for word in shlex.split(run["command"])]
            paths = {os.path.relpath(os.path.normpath(os.path.join(identity.root, word)), identity.root) for word in words if word}
            def named(name):
                module = name.rsplit(".", 1)[0].replace("/", ".")
                forms = (module, module.rpartition(".")[2])
                return (any(name == path or name.startswith(path + "/") for path in paths)
                        or any(word in forms or word.startswith(tuple(form + "." for form in forms)) for word in words))
            files.update(added if any(map(named, edited)) else ())
        entries = _tree_entries(identity, run["probeTree"])
        current_files = sorted(name for name in files if name not in run["probeFiles"] or entries.get(name) != _tree_entries(identity, candidate).get(name))
        if current_files:
            tree = _indexed(identity, run["probeTree"], (("restore", "--staged", "--source=" + candidate, "--", *current_files), None))
            recapture[pos] = {**probe, "probeTree": tree, "support": sorted(set(probe.get("support", ())) | set(current_files)),
                              "probeFiles": sorted(set(probe["probeFiles"]) | set(current_files))}
    if recapture:
        retries = _compare_many(identity, original, candidate, list(recapture.values()), runs, challenge=False)
        for pos, run in zip(recapture, retries):
            compared[pos] = run
    indexes = {probe["runIndex"]: run["runIndex"] for (_, probe), run in zip(selected, compared) if "runIndex" in probe}
    released = []
    for (entry, _), run in zip(selected, compared):
        if entry is not None:
            prior = entry.get("comparison") or {}
            references = prior.get("caseRuns")
            if not references:
                references = {case["comparison"]["command"]: case["comparison"]["runIndex"]
                              for case in behavior_map.current_cases([entry], runs)}
            references = {**references, run["command"]: run["runIndex"]}
            if prior.get("caseRuns") != references or any(prior.get(key) != run.get(key) for key in ("runIndex", "candidateKey", "breakOid", "controlKey", "sensitive", "breakExit")) or prior.get("obligation") != behavior_map.obligation(entry):
                entry["comparison"] = {**run, "obligation": behavior_map.obligation(entry), "caseRuns": references}
            if (specs or drafted) and not behavior_map._case_question(entry):  # a recording that has not proved its own item takes nothing
                behavior_map.release(items, entry, run, runs)
                released.append((entry, run))
            behavior_map.retain_evaluation(items, {entry["id"]}, evaluation, runs, retired)
    for entry, run in released:  # again on this call's results: a later rerun of another item can bind the command again
        behavior_map.release(items, entry, run, runs)
    for frozen in evaluation.values() if indexes else ():
        if behavior_map.case_key(frozen["id"], runs[frozen["runIndex"]]) not in retired:
            frozen["runIndex"] = indexes.get(frozen["runIndex"], frozen["runIndex"])
    for entry in items if indexes else ():
        proof = entry.get("comparison") or {}
        if references := proof.get("caseRuns"):
            updated = {command: indexes.get(index, index) for command, index in references.items()}
            if updated != references or indexes.get(proof["runIndex"], proof["runIndex"]) != proof["runIndex"]:
                entry["comparison"] = {**runs[indexes.get(proof["runIndex"], proof["runIndex"])], "obligation": proof["obligation"], "caseRuns": updated}
    latest = {probe.get("runIndex"): run for (_, probe), run in zip(selected, compared)} if retiring else {}
    held = {behavior_map.case_key(frozen["id"], runs[frozen["runIndex"]]): name for name, frozen in evaluation.items()} if retiring else {}
    present = {entry["id"]: entry for entry in items}  # an item's recorded cases that hold, current and retained, beside those this call retires
    proving = {case["id"] for case in [*behavior_map.current_cases(items, runs, retired), *(
        {**present[frozen["id"]], "comparison": {**runs[frozen["runIndex"]], "obligation": behavior_map.obligation(frozen)}}
        for frozen in evaluation.values() if frozen["id"] in present)]
        if (key := behavior_map.case_key(case["id"], case["comparison"])) not in retiring and key not in retired
        and not behavior_map._case_question(case)} if retiring else ()
    gone = []
    for key, index in retiring.items():
        # The lead's retirement leaves the item's cases and is listed for review. A kept-behaviour case stays the item's
        # retained proof, replayed and holding the item open while it fails on the candidate, unless another recorded case
        # proves the item and the case either fails on the original or no longer passes after the item changed.
        # An item left without a case that proves it reads open.
        identifier, run = key.rpartition(":")[0], latest[index]
        entry, first = next((entry for entry in items if entry["id"] == identifier), None), evaluation.get(held.get(key))
        withdrawn = (first and entry and behavior_map.obligation(first)[1:] != behavior_map.obligation(entry)[1:]
                     and run["arms"][-1]["outcome"] != "passed")
        kept_proof = (entry is not None and entry.get("kind") == "preservation"
                      and not (identifier in proving and (behavior_map.original_outcome(run) != "passed" or withdrawn)))
        if entry is not None and (proof := entry.get("comparison")):
            kept = {case["comparison"]["command"]: case["comparison"]["runIndex"] for case in behavior_map.current_cases([entry], runs)
                    if behavior_map.case_key(identifier, case["comparison"]) != key}
            entry.pop("comparison")  # left empty, current_cases would rebuild the binding from run history
            if kept:
                entry["comparison"] = {**runs[max(kept.values())], "obligation": proof["obligation"], "caseRuns": kept}
        if kept_proof:
            evaluation[held.get(key, key)] = {**{name: value for name, value in (first or entry).items() if name not in ("comparison", "cases")}, "runIndex": run["runIndex"]}
        else:
            evaluation.pop(held.get(key), None)
        gone.append({"caseKey": key, "command": run["command"], "exits": [arm["exitCode"] for arm in run["arms"]],
                     **({"reason": " ".join(args.report)} if args.report else {})})
    cases = behavior_map.current_cases(items, runs, retired)
    frozen_cases = [{**frozen, "comparison": runs[frozen["runIndex"]]} for frozen in evaluation.values()
                    if behavior_map.case_key(frozen["id"], runs[frozen["runIndex"]]) not in retired]
    selected_only = bool(args.behavior_id) and update is None  # a map update's batch judges the whole map
    reused = bool(current and len(runs) == len(current.get("runs", [])) and signature == json.dumps(items, sort_keys=True)
                  and frozen_signature == json.dumps(evaluation, sort_keys=True))
    if reused:
        readiness = None if selected_only else _readiness_lines(identity, state, items,
                    documents=(current, None), candidate=_active_candidate_tree(identity), case_refs=True)
        evidence = state["tddEvidence"]
    else:
        document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                    "evaluation": evaluation, "runs": runs, "comparedRuns": [run["runIndex"] for run in compared],
                    "candidateTree": candidate, "updatedAt": utc_timestamp(),
                    **({"retired": lapsed} if (lapsed := [*((current or {}).get("retired") or []), *gone]) else {})}
        state, evidence, readiness = commit_tdd(identity, slug, workflow_id, document,
                    expected_evidence_id=state.get("tddEvidence"), tree_before=before, selected_only=selected_only)
    rows, baseline = [], candidate == original  # before any edit the candidate is the original: a map item's case is expected to behave as on it
    bound = {case["comparison"]["runIndex"] for case in cases}  # a run its item no longer binds, a moved case's, gets no row
    reported = {run["runIndex"]: (entry, run) for (entry, probe), run in zip(selected, compared)
                if all(latest[retiring[done["caseKey"]]] is not run for done in gone)
                and (entry is None or run["runIndex"] in bound)}
    # Selection and failures decide the exit code and count before delivery filters the rows.
    called = {run["runIndex"] for (_, probe), run in zip(selected, compared) if args.behavior_id or any(probe is other for other in asked)}
    recorded = {case["comparison"]["runIndex"]: (None, case["comparison"]) for case in frozen_cases if case["id"] not in rejected}
    recorded.update((case["comparison"]["runIndex"], (case, case["comparison"])) for case in cases if case["id"] not in rejected)
    listed, actions = set(), {}
    owned = {(case["id"], case["comparison"].get("sourceCommand", case["comparison"]["command"])) for case in cases}
    failed = False
    for index, (entry, run) in {**{index: pair for index, pair in recorded.items() if pair[1]["candidateTree"] == candidate}, **reported}.items():
        row = {"exits": [arm["exitCode"] for arm in run["arms"]], **({"breakExit": run["breakExit"]} if run.get("breakExit") is not None else {}),
               **({"caseKey": behavior_map.case_key(run["behaviorId"], run)} if run.get("behaviorId") else {})}
        if _growth(run["execution"]):
            row["work"] = [arm.get("work") for arm in run["arms"]]
        original = behavior_map.original_outcome(run)
        expected_original = original if entry is None else "passed" if entry.get("kind") == "preservation" else "failed"
        errors = _work_errors(run)
        if errors:
            identifier = "COST-" + hashlib.sha256(run["command"].encode()).hexdigest()[:16]
            if {entry.get("status") for entry in state.get("findingStates", []) if entry["findingId"] == identifier} == {"resolved"}:
                errors = []
        for side, outcome, arm, expected in (("original", original, run["arms"][0], expected_original),
                                             ("candidate", run["arms"][-1]["outcome"], run["arms"][-1], expected_original if baseline and entry else "passed")):
            if outcome != expected:
                errors.append(side + " " + outcome + ": " + str(arm.get("output") or arm.get("bindingError") or arm["outcome"])[-1200:])
        if entry is not None and entry.get("kind") == "preservation" and original != "passed" and edited.intersection(run["probeFiles"]):
            targets = [other for other in items if other.get("kind") in {"conformance", "contract"} and other["id"] not in rejected]
            source = run.get("sourceCommand", run["command"])
            if any((other["id"], source) in owned for other in targets):
                row["why"] = "already bound to a change item; the comparison still fails"
            elif len(targets) == 1:
                target = targets[0]
                row["mapItem"] = target["id"]
                actions.setdefault(target["id"], list(target.get("cases", []))).append(run.get("sourceCommand", run["command"]))
            else:
                row["why"] = ("choose the change item: " + ", ".join(other["id"] for other in targets)
                              if targets else "no change item is recorded; its requirement is missing")
        if errors:
            row["unexpected"] = errors
            failed = True
        if errors or run.get("bindingError") or index in called:
            listed.add(index)
        rows.append({"_run": run, "_counted": index not in listed, **row})
    receipt = operation_receipt(state, identity, kind="tdd", summaryId=evidence, readiness=readiness, obligations=not selected_only,
                                cases=[*rows, *refused], otherCases=len(recorded.keys() - listed), **({"retired": [done["caseKey"] for done in gone]} if gone else {}),
                                **({"unmatched": missing} if (missing := [key for key in dict.fromkeys(args.retire) if key not in retiring]) else {}))
    corrections = any("correct" in row for row in refused)
    if corrections or actions:
        receipt["next"] = {key: value for key, value in receipt["next"].items() if key not in ("args", "tool", "input")}
    if corrections:
        receipt["next"]["input"] = "Append the replacement command to the correct arguments last shown for that refused case."
    if actions:
        payload = {"items": [{"id": key, "cases": list(dict.fromkeys(commands))} for key, commands in actions.items()]}
        # Reuse record's file input: every reply can give the complete call without echoing its case commands.
        name = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24] + ".json"
        path = repo_state_dir(identity) / "execution-trees" / workflow_id / "maps" / name
        if not path.exists():
            atomic_write_json(path, payload)
        receipt["next"].update(tool="mcp__workflow__run", args=["record", "tdd-map", "--input", os.path.relpath(path, identity.root)])
    if update is not None:
        del receipt["otherCases"]
        return receipt
    _deliver(identity, receipt)
    return 2 if refused or any(run.get("bindingError") for _, run in reported.values()) else int(failed)


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if not value or set(value) - {"items", "added", "removed"}:
        raise ValueError("tdd-map takes items, added or removed; statuses and dispositions are runner-owned")
    previous, current = current_map(identity, state)
    recorded = {entry["id"]: entry for entry in previous or []}
    added = _advisor_added(identity, state)  # an item the approving consult added leaves only through --reject
    updates, items = behavior_map.merge(previous or [], value, keep=added)
    kept = [name for name in value.get("removed", []) if name in added]
    notice = {"kept": [f"{name} stays: an item the approving consult added leaves the map only through tdd --reject {name} 'COMMAND'"
                       for name in kept]} if kept else {}
    for entry in updates:
        prior = recorded.get(entry["id"])
        if "runIndex" in (prior or {}).get("comparison", {}):
            comparison = {**prior["comparison"], "obligation": prior["comparison"].get("obligation", behavior_map.obligation(prior))}
            entry["comparison"] = comparison
    if items == previous:
        return operation_receipt(state, identity, summaryId=state.get("tddEvidence") or state["preflightEvidence"],
                                 kind="tdd", readiness=_readiness_lines(identity, state, items, case_refs=True), reused=True, **notice)
    try:
        tree_manifest(identity)
    except RuntimeError as exc:
        raise WorkflowError(str(exc)) from exc
    document = {"workflowId": state["workflowId"], "slug": state["slug"], "kind": "comparison",
                "behaviorMap": items, "runs": (current or {}).get("runs", []),
                "updatedAt": utc_timestamp(),
                **({"retired": current["retired"]} if (current or {}).get("retired") else {})}
    rerun = [entry["id"] for entry in items if entry["id"] in {update["id"] for update in updates} and (entry.get("comparison") or entry.get("cases"))]
    if rerun and not CHECK_ONLY.get():  # the changed items' cases run again in this call, one batch whose commit records the map
        receipt = _run_tdd(["--repo", str(identity.root), *(word for identifier in rerun for word in ("--behavior-id", identifier))], update=items)
        return {**receipt, **notice}
    state, evidence, readiness = commit_tdd(identity, str(state["slug"]), str(state["workflowId"]), document,
                                expected_evidence_id=state.get("tddEvidence"))
    return operation_receipt(state, identity, summaryId=evidence, kind="tdd", readiness=readiness, **notice)


def _advisor_added(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """The map items the approving preflight consult added."""
    return (evidence_document(identity, (state.get("advisorPreflight") or {}).get("intakeEvidence"), restore_runs=False) or {}).get("added", [])


def _reject(identity: RepoIdentity, state: JsonObject, requests: list[list[str]]) -> JsonObject:
    """The lead's rejection of an advisor-added map item or a final finding by the id it names: the workflow runs its one
    command once, in the checkout, and keeps the output with it. Without a command, or unless the command exits 0, the
    target stays open."""
    from .command_runner import _tail
    from .workflow_state import commit_rejections
    added = _advisor_added(identity, state)
    items = {entry["id"]: entry for entry in current_map(identity, state, fresh=False)[0] or [] if entry["id"] in added}
    findings = {ref["id"]: entry for entry in state.get("findingStates", []) if entry["stage"] == "final"
                for ref in ({"id": entry["findingId"]}, *entry.get("observations", []))}
    records, rows = [], []
    for target, *words in requests:
        command = shlex.join(words) if len(words) > 1 else next(iter(words), None)
        if target not in items and target not in findings:
            rows.append({"target": target, "open": "no advisor-added map item or final finding has this id"})
            continue
        target = findings[target]["findingId"] if target in findings else target
        if not (command or "").strip():
            rows.append({"target": target, "open": "a rejection closes its target only with one command, which the workflow runs and records"})
            continue
        try:
            words, _, environment = _case_environment(identity, command)
            raw, code, timed_out = _run(words, identity, MAX_CASE_SECONDS, env=environment)
        except (OSError, ValueError) as exc:
            raw, code, timed_out = str(exc).encode(), None, False
        if code is None or timed_out:
            rows.append({"target": target, "command": command, "open": f"the command did not finish: {_tail(raw)[-300:]}"})
            continue
        if code:
            rows.append({"target": target, "command": command, "open": f"the command exited {code}: {_tail(raw)[-300:]}"})
            continue
        claim = items[target]["behavior"] if target in items else next((finding["claim"] for finding in (evidence_document(
            identity, findings[target]["intakeEvidenceId"], restore_runs=False) or {}).get("findings", []) if finding["id"] == target), "")
        records.append({"target": target, "kind": "item" if target in items else "finding", "claim": claim,
                        "command": command, "exitCode": code, "output": _tail(raw)})
    if records:
        state = commit_rejections(identity, str(state["slug"]), str(state["workflowId"]), records)
    return operation_receipt(state, identity, kind="tdd", rejected=[*records, *rows])
