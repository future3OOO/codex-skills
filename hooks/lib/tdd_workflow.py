"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import ast
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from functools import lru_cache, partial
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
from typing import Callable, TypeVar

from . import behavior_map
from ._workflow_db import cached_arms, history, read_active
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import CanonicalRoot, RepoIdentity, resolve_repo_identity
from .state_store import _EXECUTION, _comparison_scope, _active_candidate_tree, _git, is_test_path, read_blobs, blob_reader, tree_manifest, utc_timestamp, repo_state_dir, _flock
from .workflow_state import (
    NO_INSTANCE_ID, WorkflowError, _bound_instance_state,
    commit_tdd, commit_findings, evidence_document, instance_id, operation_receipt, _readiness_lines,
)

JsonObject = dict[str, object]
_Input = TypeVar("_Input")
PROBE_TIMEOUT = 10.0  # default for new finding probes; recorded cases keep their execution budget
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
                command, execution, _ = _comparison_input(identity, proof)
                arms = proof["arms"]
                sizes = _growth(execution)
                commands = [[arg.replace("{size}", str(size)) for arg in command] for size in (sizes[1], sizes[0], sizes[0], sizes[1])] if sizes else [command, command]
                sources = (proof["originalTree"], proof["originalTree"], target, target) if sizes else (proof["originalTree"], target)
                checked = [_input_identity(identity, execution, arm) for arm in arms]
                contexts = [_execution_key(identity, source, proof.get("probeTree", proof["candidateTree"]), _comparison_files(identity, proof), value, proof["timeout"], execution) for source, value in zip(sources, commands)]
                freshness[key] = {"fresh": len(arms) == len(contexts) and all(inputs and arm.get("contextKey") == context and arm.get("inputs") == inputs
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
            proof["sensitive"] = bool(_growth(proof.get("execution") or {})) or bool(control and proof.get("breakOid") and fresh(control, control["candidateTree"])["fresh"]
                                      and control["arms"][0]["key"] == good["key"]
                                      and control["arms"][-1]["outcome"] == "failed" and not control["timedOut"]
                                      and (candidate == control["originalTree"] or
                                           _source_input(identity, candidate, control["probeTree"], tuple(_comparison_files(identity, control))) ==
                                           _source_input(identity, control["originalTree"], control["probeTree"], tuple(_comparison_files(identity, control)))))


def _probe_files(command: list[str], identity: RepoIdentity, support: list[str], virtual: str | None = None) -> list[str]:
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
    paths.update(os.path.relpath(os.path.normpath(root / name), root) for name in support)
    if virtual:
        paths.add(virtual)
    for name in paths - {virtual}:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise WorkflowError("probe support must be an in-repository regular file: " + name)
        if not is_test_path(name):
            raise WorkflowError("unsupported probe: production source cannot be overlaid as test support: " + name)
    return sorted(paths)


def _comparison_files(identity: RepoIdentity, probe: JsonObject) -> list[str]:
    declared = _command_files(identity, probe) | {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root)
                for name in probe.get("support") or []}
    return [path for path in probe["probeFiles"] if is_test_path(path) or path in declared]


def _environment() -> dict[str, str]:
    owned = {"SHLVL", "_", "PWD", "OLDPWD", "PYTHONHOME", "TMPDIR", "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE",
             "CODEX_TOOL_CALL_ID", "CODEX_WORKFLOW_EXEC_TRACE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_CI",
             "CODEX_VERSION", "CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS", "ADVISOR_ACTIVE",
             "GH_PAGER", "GIT_PAGER", "PAGER", "NO_COLOR", "LC_ALL", "LC_CTYPE"}
    return {**{key: value for key, value in os.environ.items() if key not in owned and not key.startswith("CODEX_ADVISOR_")},
            "SHLVL": "0", "PYTHONDONTWRITEBYTECODE": ""}


def _executable(identity: RepoIdentity, command: str, environment: dict[str, str] | None = None) -> str:
    if os.path.dirname(command):
        return os.path.normpath(os.path.join(identity.root, command))
    search = os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path(environment))
    executable = shutil.which(command, path=search)
    if executable is None:
        raise WorkflowError("probe executable is unavailable: " + command)
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


@lru_cache(maxsize=128)
def _source_input(identity: RepoIdentity, source: str, probe: str, files: tuple[str, ...]):
    scope = _EXECUTION.get()
    def children(oid):
        def load():
            if scope["reader"] is None:
                scope["reader"] = scope["resources"].enter_context(blob_reader(Path(identity.root)))
            data, result, offset = scope["reader"](oid, kind=b"tree"), {}, 0
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
    def native(tree, name):
        value = ("040000", tree)
        for part in (() if name == "." else name.split("/")):
            value = children(value[1]).get(part) if value and value[0] == "040000" else None
        return value
    entries = {path: value for path, value in _tree_entries(identity, source).items() if not is_test_path(path)}
    frozen = _sample_input(("frozen", identity.root, probe, files), lambda: {name: value for name in files
                           if (value := (_tree_entries(identity, probe).get(name) if probe == source else native(probe, name))) is not None})
    entries.update(frozen)
    return json.dumps(entries, sort_keys=True).encode()


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


def _input_identity(identity: RepoIdentity, execution: JsonObject, arm: JsonObject) -> str:
    tools = arm.get("tools", [])
    scope = _EXECUTION.get()
    cache_key = json.dumps([identity.root, execution.get("lookup") or execution["executable"], execution.get("search"),
                            execution.get("assignments", {}), tools, arm.get("lookups", {})], sort_keys=True)
    if scope is not None and cache_key in scope["inputs"]:
        digest = scope["inputs"][cache_key]["digest"]
    else:
        current = _sample_input(("environment", identity.root, tuple(sorted(execution.get("assignments", {}).items()))), lambda: _comparison_input(identity, {"command": shlex.join([
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
                     binding: list[str], timeout: float, runtimes: list[Path], *, lower=None, work_root=None) -> JsonObject:
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
        if result.startswith("0") and (not owned or physical.is_file()):
            with physical.open("rb") as handle:
                if handle.read(2) == b"#!":
                    tools.add(shlex.split(handle.readline().decode())[0])
        if not source or not owned:
            tools.add(str(path))
        if not owned:
            tools.add(str(target))
    return {"tools": sorted(tools), "lookups": lookups, "work": work}


def _stamp(path: Path):
    try:
        info = path.lstat()
        return (info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except (FileNotFoundError, NotADirectoryError):
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
    if scope["reader"] is None:
        scope["reader"] = scope["resources"].enter_context(blob_reader(Path(identity.root)))
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
                                     "ignore": None, "read": scope["reader"], "resources": scope["resources"],
                                     "cache": cache, "source": source, "wait": wait, "measure": measure, "times": [], "temporary": scope["root"]}
    frozen = _tree_entries(identity, probe)
    return scope["sources"][key], {path: frozen[path] for path in files}


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
                  command: list[str], timeout: float, environment: dict[str, str], cancel, objects, *, restore=True) -> JsonObject:
    state, frozen = prepared
    if cancel.is_set():
        raise WorkflowError("comparison interrupted")
    before = _overlay_probe(state, frozen, restore)
    try:
        result = _execute_case(identity, source_tree, state["root"], command, timeout, environment, cancel, objects,
                               overlay=state if state.get("lower") else None)
        if restore:
            state["result"] = result
        if state.get("measure") and not result["timedOut"]:
            state["times"].append((restore, result["timing"]["executionSeconds"]))
        return result
    finally:
        if before:
            _restore_case(state, before)


def _after(previous, identity, source, prepared, *arguments, derived=False, first=False):
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
        return _execute_tree(identity, source, (private, frozen), *arguments, restore=first and state["wait"])


_CPU_SUPERVISOR = '''import ctypes,json,os,signal,sys
if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0):
    raise OSError(ctypes.get_errno(),"cannot supervise case descendants")
pid=os.fork()
if pid==0: os.execv(sys.argv[2],sys.argv[2:])
cpu=0.;status=None
while True:
    try: child,result,usage=os.wait4(-1,0)
    except ChildProcessError: break
    cpu+=usage.ru_utime+usage.ru_stime
    if child==pid: status=os.waitstatus_to_exitcode(result)
with open(sys.argv[1],"w") as output: json.dump([pid,cpu],output)
if status<0:
    if status!=-signal.SIGKILL: signal.signal(-status,signal.SIG_DFL)
    os.kill(os.getpid(),-status)
sys.exit(status)
'''


def _execute_case(identity: RepoIdentity, source_tree: str, root: Path,
                  command: list[str], timeout: float, environment: dict[str, str], cancel, objects, *, overlay=None) -> JsonObject:
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
        binding.extend(arg for key, value in env.items() if runner_env.get(key) != value for arg in ("--setenv", key, value))
        dependencies = Path(identity.root) / "node_modules"
        runtimes = [dependencies] if dependencies.is_dir() else []
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            runtimes.append(runtime)
        binding.extend(arg for runtime in runtimes for arg in ("--ro-bind", str(runtime), str(runtime)))
        process_started = time.perf_counter()
        observer = [] if inherited else ["strace", "-f", "--seccomp-bpf", "-qq", "-ttt", "-e", "trace=execve,chdir,fchdir,clone,clone3,fork,vfork",
                    "-e", "status=successful,failed", "-y", "-s", "0", "-o", str(trace)]
        cpu_path = Path(temporary) / "cpu"
        raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *observer, sys.executable, "-I", "-S", "-c", _CPU_SUPERVISOR, str(cpu_path), *actual], snapshot, timeout, env=runner_env, cancel=cancel)
        cpu = json.loads(cpu_path.read_bytes()) if cpu_path.exists() else [None, None]
        inputs = _observed_inputs(identity, root, Path(temporary), trace, offset, actual, env, binding, timeout, runtimes, lower=overlay["lower"] if overlay else None, work_root=cpu[0])
        inputs["work"]["cpuSeconds"] = cpu[1]
        from .command_runner import _tail
        output = _tail(raw)
        timing = {"totalSeconds": time.perf_counter() - started,
                  "executionSeconds": time.perf_counter() - process_started}
        outcome = "incomplete" if timed_out else "passed" if code == 0 else "failed"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome,
                          output=output, loadedRoot=identity.root, executedCommand=actual, timing=timing, **inputs)


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


def _probe_capture(identity: RepoIdentity, probe: object, candidate: str, timeout: float,
                   support: list[str] | None = None, inventory: list[str] | None = None, preserved: str | None = None) -> JsonObject:
    """Freeze the command's existing sources, optionally reading one scratch probe file."""
    if not (isinstance(probe, dict) and set(probe) <= {"path", "command"}
            and isinstance(probe.get("command"), str) and probe["command"].strip()):
        raise ValueError("probe needs command and, for a new scratch file, path; source content is not an input")
    command = shlex.split(probe["command"])
    overrides = {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root) for name in support or []}
    tree, original_files = _kept_tests(identity, candidate, preserved, tuple(sorted(overrides))) if preserved else (candidate, set())
    virtual = None
    if "path" not in probe:
        external = [arg for arg in command[1:] if (Path(identity.root) / arg).is_file()
                    and not (Path(identity.root) / arg).resolve().is_relative_to(identity.root)]
        if len(external) == 1:
            probe = {**probe, "path": external[0]}
    if "path" in probe:
        if not isinstance(probe["path"], str) or not probe["path"]:
            raise ValueError("probe path must name a readable scratch file")
        source = (Path(identity.root) / probe["path"]).resolve()
        if source.is_relative_to(identity.root) or not source.is_file():
            raise ValueError("use command for an existing case; a new probe path must be a scratch file outside the checkout")
        positions = [index for index, arg in enumerate(command)
                     if (Path(identity.root) / arg.split("::", 1)[0]).resolve() == source]
        if not positions:
            raise ValueError("the direct probe command must name its scratch file")
        blob = _git(identity, "hash-object", "-w", "--", str(source)).decode().strip()
        virtual = f"tests/.workflow-probes/{blob}/{source.name}"
        mode = "100755" if source.stat().st_mode & 0o111 else "100644"
        tree = _indexed(identity, tree, (("update-index", "--add", "--cacheinfo", f"{mode},{blob},{virtual}"), None))
        for index in positions:
            command[index] = virtual + command[index][len(command[index].split("::", 1)[0]):]
    selected = list(command)
    if selected and Path(selected[0]).name == "env":
        selected.pop(0)
    while selected and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", selected[0]):
        selected.pop(0)
    if not selected:
        raise ValueError("probe command must name an executable")
    files = (_probe_files(selected, identity, support or [], virtual) if inventory is None
             else sorted(set(inventory) | ({virtual} if virtual else set())))
    if preserved is not None:
        files = sorted(original_files | (set(files) & (overrides | ({virtual} if virtual else set()))))
    return {"command": shlex.join(command), "candidateTree": candidate, "probeTree": tree,
            "probeFiles": files, "support": support or [], "timeout": timeout}


def _probe_outcome(arm: JsonObject) -> str:
    return "timed-out" if arm["timedOut"] else str(arm["outcome"])


def _run_probe(identity: RepoIdentity, probe: object, candidate: str, original: str | None,
               timeout: float = PROBE_TIMEOUT, cached: list[JsonObject] | None = None,
               frozen: JsonObject | None = None) -> JsonObject:
    try:
        regression = original is not None and bool(frozen and frozen.get("arms")) and _probe_outcome(frozen["arms"][0]) == "passed" and _probe_outcome(frozen["arms"][-1]) in {"failed", "timed-out"}
        frozen = frozen or _probe_capture(identity, probe, candidate, timeout)
        compared = _compare(identity, original or candidate, candidate, frozen, cached if cached is not None else [], passing_original=original is not None)
        result: JsonObject = {"kind": "probe", "comparison": compared,
                              "candidate": _probe_outcome(compared["arms"][-1])}
        if original is not None:
            result["original"] = _probe_outcome(compared["arms"][0])
        if all(arm["outcome"] == "passed" for arm in compared["arms"]) and not compared.get("sensitive") and not regression:  # a candidate failure the original passes is its own witness
            result["error"] = compared.get("breakError") or "probe did not fail under its behavior break; supply --change probe=FILE"
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
                raise WorkflowError("ambiguous file argument: " + argument + "; replay with --support naming the captured input")
        selected.update(matches)
    return selected


def _maintained_tests(identity: RepoIdentity, batches: list[JsonObject], maintained: list[JsonObject] | None,
                      before: str, after: str, cached: list[JsonObject], validation: JsonObject | None) -> str | None:
    changed = set(_changed_entries(identity, before, after))
    if not any(is_test_path(path) for path in changed):
        return None
    present = set(_tree_entries(identity, after))
    for run in [*batches, *([validation] if validation else [])]:
        selected = _command_files(identity, run) or (set(run["probeFiles"]) & changed)
        scratch = sorted(path for path in selected if path.startswith("tests/.workflow-probes/"))
        available = present | set(scratch)
        if not selected or not selected & available:
            continue
        live_tree = _indexed(identity, after, (("restore", "--staged", "--source=" + run["probeTree"], "--", *scratch), None)) if scratch else after
        live = {**run, "probeTree": live_tree, "probeFiles": [path for path in run["probeFiles"] if path in available]}
        delivery = _compare(identity, after, after, live, cached)["arms"][-1]
        if delivery["outcome"] != "passed":
            return "required surviving named case fails: " + run["command"] + "\n" + str(delivery.get("output") or "")[-800:]
    return None


def _shape_refusal(identity: RepoIdentity, before: str, after: str, cost_proved: bool = False) -> str | None:
    """Keep net reduction unless measured cost justifies growth; immutable probes remain the oracle."""
    counts = _git(identity, "diff", "--numstat", "--no-renames", "-z", before, after).decode("utf-8", "replace").split("\0")
    rows = [row.split("\t", 2) for row in counts if row.count("\t") == 2]
    if not cost_proved and sum(int(added) for added, _, _ in rows if added != "-") >= sum(int(deleted) for _, deleted, _ in rows if deleted != "-"):
        return "a change must delete more lines than it adds"
    return None


def _run_change(identity: RepoIdentity, batches: list[JsonObject], change: object, candidate: str,
                probe: object = None, timeout: float = PROBE_TIMEOUT, cached: list[JsonObject] | None = None, maintained: list[JsonObject] | None = None,
                frozen: JsonObject | None = None, cost_recheck: bool = False) -> JsonObject:
    """Compare a concrete replacement and retain its correctness and cost counterexamples."""
    if not isinstance(change, str) or not change.strip():
        return {"kind": "change", "error": "change must be a unified diff"}
    if not batches:
        return {"kind": "change", "error": "no recorded comparison batch to rerun"}
    cached = list(batches) if cached is None else cached
    measured = _run_probe(identity, probe, candidate, None, timeout, cached, frozen) if probe is not None else None
    if measured and measured.get("candidate") == "passed" and "error" in measured:
        return {"kind": "change", "error": measured["error"]}
    repaired = cost_recheck and measured is not None and "error" not in measured and measured.get("candidate") == "passed"
    if repaired:
        before, after = frozen["originalTree"], candidate
    else:
        patch = change.encode("utf-8", "replace") + (b"" if change.endswith("\n") else b"\n")  # unencodable text cannot apply
        try:
            changed = _indexed(identity, candidate, (("apply", "--cached", "-"), patch))
            before, after = candidate, changed
        except RuntimeError:
            try:  # already applied by the lead: the candidate itself carries the removal
                before, after = _indexed(identity, candidate, (("apply", "--cached", "--reverse", "-"), patch)), candidate
            except RuntimeError:
                return {"kind": "change", "error": "change does not apply to the candidate"}
    cost = None
    maintenance = any(is_test_path(path) for path in _changed_entries(identity, before, after))
    if probe is not None and not repaired:
        if not maintenance and (measured is None or measured.get("candidate") not in {"passed", "failed"}):
            return {"kind": "change", "error": "replacement probe did not establish a candidate outcome"}
        if measured and "comparison" in measured:
            cost = _compare(identity, candidate, after, measured["comparison"], cached)
        if cost and (not cost["valid"] or not cost.get("sensitive")) and not maintenance:
            return {"kind": "change", "error": "replacement did not pass the same cost probe", "comparison": cost}
    if not repaired and (refusal := _shape_refusal(identity, before, after, cost is not None and cost["valid"] and measured["candidate"] == "failed")):
        return {"kind": "change", "error": refusal}
    if after == candidate:
        return measured if repaired else {"kind": "change", "applied": True, "candidate": "passed", **({"comparison": cost} if cost else {})}
    if error := _maintained_tests(identity, batches, maintained, before, after, cached,
                                  measured.get("comparison") if measured else None):
        return {"kind": "change", "error": error}
    for run in batches:
        replay = _compare(identity, after, after, dict(run), cached)
        if replay.get("breakError") and frozen and frozen.get("breakOid"):
            replay = _compare(identity, after, after, {**run, "breakOid": frozen["breakOid"]}, cached)
        arm = replay["arms"][-1]
        if arm["outcome"] == "passed" and not replay.get("sensitive"):
            return {"kind": "change", "error": "retained detector lacks a valid behavior break: " + run["command"]}
        outcome = "timed-out" if arm["timedOut"] else arm["outcome"]
        if outcome != "passed":
            if outcome == "failed" and after != candidate:
                control = _compare(identity, candidate, candidate, dict(run), cached)["arms"][-1]
                if control["outcome"] != "passed":
                    return {"kind": "change", "error": "current candidate does not pass the retained batch: " + run["command"]}
            reason = (f"recorded batch `{run['command']}` ran past {run['timeout']:g} s: replace it with direct probes of the changed code"
                      if arm["timedOut"] else f"recorded batch `{run['command']}` {outcome} with the change applied")
            return ({"kind": "change", "applied": after == candidate, "candidate": outcome,
                     "timedOut" if arm["timedOut"] else "failure": reason} if outcome in {"failed", "timed-out"}
                    else {"kind": "change", "error": reason})
    return measured if repaired else {"kind": "change", "applied": after == candidate, "candidate": "passed", **({"comparison": cost} if cost else {}),
                                     **({"selection": "proposer-selected command; exit result checked, source-line reachability and relevance not traced"} if probe is not None else {})}


def binds(execution: JsonObject) -> bool:
    """The acceptance order: a regression (a probe the original passes that fails or runs past its limit on the
    candidate) binds and is repaired; a net reduction or cost-proved replacement every retained batch passes binds;
    anything else, a probe the original also fails included, asks for unrecorded behaviour: a report line."""
    if "error" in execution:
        return False
    if execution["kind"] == "cost":
        return execution.get("candidate") != "passed"
    if execution["kind"] == "change":
        return execution.get("candidate") == "passed"
    return execution.get("candidate") in {"failed", "timed-out"} and execution.get("original") == "passed"


def _retained(identity: RepoIdentity, state: JsonObject) -> list[tuple[JsonObject, JsonObject]]:
    """Each observation of every review or final finding that binds by the current rule: a finding settles only with
    all of them; one recorded under an earlier rule that no longer binds is re-executed as a report line."""
    retained, rejected, intakes = [], set(), {}
    for entry in state.get("findingStates") or []:
        for ref in [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]}, *entry.get("observations", [])]:
            ref = {"evidenceId": ref["evidenceId"], "id": ref["id"]}
            if ref["evidenceId"] not in intakes:
                intakes[ref["evidenceId"]] = evidence_document(identity, ref["evidenceId"], restore_runs=False) or {}
            intake = intakes[ref["evidenceId"]]
            finding = next((item for item in intake.get("findings", []) if item["id"] == ref["id"]), None)
            if finding and finding.get("rejected") is True:
                rejected.add(tuple(finding["priorFinding"][key] for key in ("evidenceId", "id")))
            if intake.get("stage") in {"code-review", "final"} and finding and "execution" in finding and binds(finding["execution"]):
                retained.append((entry, {**finding, "ref": ref, "sourceTree": intake["candidateTree"]}))
    return [(entry, finding) for entry, finding in retained
            if tuple(finding["ref"][key] for key in ("evidenceId", "id")) not in rejected]


def execute_findings(identity: RepoIdentity, state: JsonObject, findings: list[JsonObject], *, captured: JsonObject | None = None,
                     retained: list[tuple[JsonObject, JsonObject]] | None = None, candidate: str | None = None,
                     current: JsonObject | None = None) -> JsonObject:
    """Execute only submitted observations; gates supply uncached retained observations explicitly."""
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
    evaluation = _evaluation(identity, state, current)
    maintained = [*behavior_map.current_cases(current.get("behaviorMap") or [], current.get("runs") or []),
                  *({**entry, "comparison": current["runs"][entry["runIndex"]]} for entry in evaluation.values())]
    used = {(item.get("comparison") or {}).get("runIndex") for item in maintained}
    batches = [run for run in current.get("runs") or [] if run["runIndex"] in used]
    cached = list(current.get("runs") or [])
    retained = [(entry, finding) for entry, finding in retained or [] if finding["ref"] not in rejected]
    loaded_keys = {tuple(arm.get("key") for arm in run["arms"]) for run in cached}
    def run(finding: JsonObject, first: bool) -> JsonObject:
        timeout = next((batch["timeout"] for batch in reversed(current.get("runs") or [])
                        if batch["command"] == (finding.get("probe") or {}).get("command")),
                       max((batch["timeout"] for batch in batches), default=PROBE_TIMEOUT) if "change" in finding and "probe" in finding else PROBE_TIMEOUT)
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
            frozen = frozen or finding["execution"].get("comparison") or _probe_capture(identity, finding["probe"], finding["sourceTree"], timeout)
        prior = (finding.get("execution") or {}).get("comparison") or {}
        cost_recheck = not first and bool(prior.get("arms")) and prior["arms"][0]["outcome"] == "failed"
        return (_run_change(identity, batches, finding["change"], candidate, finding.get("probe"), timeout, cached, maintained, frozen, cost_recheck) if "change" in finding
                else _run_probe(identity, finding["probe"], candidate, original, timeout, cached, frozen) if "probe" in finding
                else {"kind": "claim"})
    for replacement in (False, True):
        probes = [captured[finding["probe"]["command"]] for finding in findings
                  if captured and "probe" in finding and ("change" in finding) == replacement]
        probes.extend(finding["execution"]["comparison"] for _, finding in retained
                      if "probe" in finding and ("change" in finding) == replacement and finding["execution"].get("comparison"))
        if probes:
            _compare_many(identity, candidate if replacement else original, candidate, probes, cached, passing_original=not replacement)
    executed = []
    for finding in findings:
        execution = run(finding, True)
        executed.append({**finding, "execution": execution, "material": binds(execution)})
    again = list(withdrawn)
    for entry, finding in retained:
        execution = run(finding, False)
        again.append({**finding["ref"], "execution": {**execution, **{key: finding["execution"][key]
                                                                     for key in ("original",) if key in finding["execution"]}}})
    return {"measuredTree": candidate, "measuredEvidence": state.get("tddEvidence"), "findings": executed, "reexecuted": again,
            "runs": list({tuple(arm.get("key") for arm in run["arms"]): run for run in cached
                          if tuple(arm.get("key") for arm in run["arms"]) not in loaded_keys}.values())}


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
    if (cached.get("findingRefs") == references and cached.get("runnerKey") == _runner_digest()
            and isinstance(cached.get("inputIdentities"), list)):
        try:
            reusable = all(record["digest"] == _input_identity(identity, record["execution"], record) for record in cached["inputIdentities"])
        except (WorkflowError, OSError):
            reusable = False
        if reusable:
            return state
    retained = _retained(identity, state)
    reports = [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"], "execution": {"kind": "claim"}}
               for entry in state.get("findingStates", []) if _finding_unresolved(entry)
               and all(entry is not other for other, _ in retained)]
    current = evidence_document(identity, state.get("tddEvidence")) or {}
    execution = execute_findings(identity, state, [], retained=retained, current=current)
    execution["reexecuted"].extend(reports)
    execution["runnerKey"] = _runner_digest()
    execution["inputIdentities"] = [value for value in _EXECUTION.get()["inputs"].values() if isinstance(value, dict)]
    return commit_retained_checks(identity, str(state["slug"]), str(state["workflowId"]), execution, references)


def _evaluation(identity: RepoIdentity, state: JsonObject, current: JsonObject | None) -> JsonObject:
    if not current or "evaluation" in current:
        return {key: dict(entry) for key, entry in (current or {}).get("evaluation", {}).items()}
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


def _comparison_input(identity: RepoIdentity, probe: JsonObject) -> tuple[list[str], JsonObject, dict[str, str]]:
    command = shlex.split(probe["command"])
    environment = {}
    while command and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
        name, value = command.pop(0).split("=", 1)
        environment[name] = value
    assignments = environment.copy()
    environment = _environment() | environment
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(
        os.path.normpath(os.path.join(identity.root, entry)) for entry in (str(identity.root), *environment.get("PYTHONPATH", "").split(os.pathsep))))
    execution = {"executable": _executable(identity, command[0], environment),
                 "assignments": assignments,
                 "search": os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path(environment)),
                 "lookup": command[0] if not os.path.dirname(command[0]) else None,
                 "environment": hashlib.sha256(json.dumps(sorted((key, value) for key, value in environment.items() if key != "PATH")).encode()).hexdigest()}
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
            small, large = [json.loads(arm["output"]) for arm in arms[2:]]
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


@_comparison_scope()
def _compare_many(identity: RepoIdentity, original: str, candidate: str, probes: list[JsonObject],
                  runs: list[JsonObject], *, challenge: bool = True, passing_original: bool = False) -> list[JsonObject]:
    """One execution/cache owner. Each command has its own two exit results."""
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
                found = found.result()
            if "key" not in found:
                inputs = _input_identity(identity, execution, found)
                found = {**found, "contextKey": context, "inputs": inputs,
                         "key": hashlib.sha256((context + inputs).encode()).hexdigest()}
                cache.setdefault(context, {})[found["key"]] = found
            arms.append({**found, "requestedTree": source})
        keys = [arm["key"] for arm in arms]
        metadata = {"command": probe["command"], "support": probe.get("support") or [], "timeout": probe["timeout"],
                    "behaviorId": probe.get("behaviorId"), "probeFiles": probe["probeFiles"],
                    "candidateTree": after, "probeTree": tree, "originalTree": before}
        retained = next((run for run in reversed(runs) if run["candidateTree"] == after
                         and [arm.get("key") for arm in run["arms"]] == keys
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
    pending = {}
    def schedule(source, probe, parent=None, size=None):
        files, timeout = _comparison_files(identity, probe), probe["timeout"]
        command, execution, environment = _comparison_input(identity, probe)
        if size is not None:
            command = [arg.replace("{size}", str(size)) for arg in command]
        tree = probe.get("probeTree", probe["candidateTree"])
        context = _execution_key(identity, source, tree, files, command, timeout, execution)
        found = next((arm for arm in reversed(list(cache.get(context, {}).values()))
                      if (inputs := _input_identity(identity, execution, arm)) and arm["key"] == hashlib.sha256((context + inputs).encode()).hexdigest()), None)
        if found is None:
            pending_key = context
            if pending_key not in pending:
                state, frozen = _prepared_tree(identity, scope, parent or source, tree, files, isolated=size is not None)
                if parent:  # a break is its parent's warm tree plus the break's changed files
                    entries = _tree_entries(identity, source)
                    changes = _changed_entries(identity, parent, source)
                    state = {**state, "owner": state, "base": {path: value for path, value in state["base"].items() if path not in changes}
                             | {path: entries[path] for path in changes if path in entries}}
                if scope["pool"] is None:
                    scope["pool"] = ThreadPoolExecutor(max_workers=min(os.cpu_count() or 1, 8))  # measured: more workers inflate CPU time
                root_key = parent or source
                previous = scope["first"].get(root_key) if state["wait"] and size is None else None
                _require_overlay(_executable(identity, "bwrap", environment))
                execute = partial(_after, previous, identity, source, (state, frozen), command, timeout, environment, scope["cancel"],
                                  tuple(scope["objects"].items()), derived=bool(parent),
                                  first=size is None and not parent and root_key not in scope["first"])
                pending[pending_key] = scope["pool"].submit(execute)
                if not parent and size is None:
                    scope["first"].setdefault(root_key, pending[pending_key])
            found = pending[pending_key]
        return (source, context, found)
    plans, contexts = [], {}
    for probe in probes:
        command, execution, _ = _comparison_input(identity, probe)
        tree = probe.get("probeTree", probe["candidateTree"])
        sizes = _growth(execution)
        if sizes and not any("{size}" in arg for arg in command):
            raise WorkflowError("growth command must contain {size}")
        mounted_sets = []
        for size in sizes[:2] if sizes else (None,):
            value = [arg.replace("{size}", str(size)) for arg in command] if size is not None else command
            mounted_sets.append(value)
            for source in (original, candidate):
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
        plans.append((probe, tree, execution, control))
    def keys(arm):
        context = arm["contextKey"]
        return [hashlib.sha256((context + _input_identity(identity, contexts[context], arm)).encode()).hexdigest()]
    workflows = scope.setdefault("workflow_ids", {})
    if identity.key not in workflows:
        workflows[identity.key] = instance_id(read_active(identity) or {})
    for arm in cached_arms(identity, workflows[identity.key], contexts, keys):
        cache.setdefault(arm["contextKey"], {})[arm["key"]] = arm
    jobs, controls = [], []
    def selections(first, last, probe, parent=None):
        sizes = _growth(_comparison_input(identity, probe)[1])
        return ([schedule(source, probe, size=size)
                 for source, size in ((first, sizes[1]), (first, sizes[0]), (last, sizes[0]), (last, sizes[1]))]
                if sizes else [schedule(first, probe), schedule(last, probe, parent)])
    for probe, tree, execution, control in plans:
        selected = selections(original, candidate, probe)
        jobs.append((probe, tree, execution, selected, original, candidate))
        if isinstance(control, tuple):
            source, broken = control
            target = selected[0 if passing_original else -1][2]
            control = ((probe, tree, execution, selections(source, broken, probe, source), source, broken)
                       if not isinstance(target, dict) or target["outcome"] == "passed" else None)
        controls.append(control)
    compared = [finish(*job) for job in jobs]
    if challenge:
        for probe, result, control in zip(probes, compared, controls):
            result["sensitive"] = bool(_growth(result["execution"]))
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


@interruptible()
@_comparison_scope()
def _run_tdd(values: list[str]) -> int:
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", description="Compare and record probes or replacements; authors record verdicts from their checkpoint command", epilog=binds.__doc__)
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--workflow-id")
    parser.add_argument("--expected-candidate-tree")
    parser.add_argument("--tdd-evidence", default=argparse.SUPPRESS)
    parser.add_argument("--verdict", help="record review completion without running cases; use the source-bound checkpoint command")
    parser.add_argument("--report", action="append", default=[], help="finding's owning invariant, or non-executable report lines with a verdict")
    parser.add_argument("--reject", action="append", default=[], metavar="EVIDENCE:ID=REASON",
                        help="withdraw this author's invalid observation without running cases; retain its raw evidence")
    parser.add_argument("--change", action="append", default=[], help="ITEM=FILE supplies a behavior-breaking diff for its commands (probe for authored probes); a bare FILE submits an authored replacement")
    parser.add_argument("--as", dest="author", choices=("review", "final"), help="execute and record this author's finding in the same call")
    parser.add_argument("--path", help="new probe file outside the checkout; existing cases need only their command")
    parser.add_argument("--behavior-id", help="map item for a single trailing command, or select recorded cases to replay")
    parser.add_argument("--case", action="append", nargs=2, default=[], metavar=("ITEM_OR_DASH", "COMMAND"),
                        help="one command per case; '-' is unbound. Growth: WORKFLOW_GROWTH=SMALL,LARGE,MAX_POWER COMMAND {size}; counts may grow by at most (LARGE/SMALL)**MAX_POWER")
    parser.add_argument("--support", action="append", help="name support; recorded replay uses its captured immutable bytes")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(values[:dash])
    if sum(value.partition("=")[0] == "--behavior-id" for value in values[:dash]) > 1:
        raise WorkflowError("one Behavior Map item per case command")
    command = values[dash + 1:]
    identity = resolve_repo_identity(args.repo)
    state = _bound_instance_state(read_active(identity), args.slug, args.workflow_id)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    if (workflow_id := instance_id(state)) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    scope = _EXECUTION.get()
    slug = str(state["slug"])
    if args.path and not (command or args.case) or args.timeout is not None and args.timeout <= 0:
        raise WorkflowError("a scratch path needs its command; timeout must be positive")
    if args.reject and not args.author:
        raise WorkflowError("only the finding author can withdraw an observation")
    if args.verdict and (args.author != "review" or args.verdict not in {"complete", "incomplete"}
                         or command or args.case or args.change or args.path or args.behavior_id):
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
        captured = {}
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
            inventory = _probe_files([], identity, args.support or []) if specs else None
            probes = []
            for _, value in specs:
                probe = _probe_capture(identity, {"command": value, **({"path": args.path} if args.path else {})},
                                       candidate, args.timeout or PROBE_TIMEOUT, args.support, inventory)
                if "probe" in breaks:
                    probe["breakOid"] = breaks["probe"]
                captured[probe["command"]] = probe
                probes.append({"command": probe["command"]})
            claim = "\n".join(dict.fromkeys(report.strip() for report in args.report if report.strip()))
            findings = []
            for probe in list({probe["command"]: probe for probe in probes}.values()) or ([None] if replacement else []):
                finding = {**({"probe": probe} if probe else {}), **({"change": change} if change is not None else {})}
                identifier = "FINDING-" + hashlib.sha256((claim + json.dumps(finding, sort_keys=True)).encode()).hexdigest()[:16]
                findings.append({"id": identifier, "claim": claim or "Recorded " + ("replacement" if change is not None else "probe")
                                 + ": " + (replacement or probe["command"]), **finding})
        for value in args.reject:
            reference, equal, reason = value.partition("=")
            evidence, colon, identifier = reference.partition(":")
            if not equal or not colon or not evidence or not identifier or not reason.strip():
                raise WorkflowError("withdrawal needs EVIDENCE:ID=REASON")
            findings.append({"id": "REPORT-" + hashlib.sha256(value.encode()).hexdigest()[:16],
                             "claim": reason.strip(), "kind": "nonbehavioral", "rejected": True,
                             "priorFinding": {"evidenceId": evidence, "id": identifier}})
        if not args.verdict and any("probe" in finding or "change" in finding for finding in findings):
            _execution_cache(identity, state)
        execution = execute_findings(identity, state, findings, captured=captured, candidate=candidate)
        document = {"schemaVersion": 1, "kind": "intake", "workflowId": workflow_id, "slug": slug,
                    "stage": "code-review" if args.author == "review" else "final",
                    "producer": "code-review" if args.author == "review" else "codex-advisor",
                    "candidateTree": args.expected_candidate_tree if args.verdict else execution["measuredTree"],
                    "tddEvidence": reviewed_evidence if args.verdict else execution["measuredEvidence"],
                    "findings": execution["findings"], **({"verdict": args.verdict} if args.verdict else {})}
        state, evidence = commit_findings(identity, slug, workflow_id, document, execution)
        receipt = operation_receipt(state, identity, kind="tdd", summaryId=evidence, obligations=False)
        if args.verdict:
            _emit_json(receipt, sort_keys=False)
            return 0
        rows = []
        for finding in execution["findings"]:
            result = finding["execution"]
            refused = "error" in result
            row = {"case": (finding.get("probe") or {}).get("command", replacement), "findingId": finding["id"],
                   "result": "withdrawn" if finding.get("rejected") else "refused" if refused else "binds" if finding["material"] else "doesn't bind"}
            compared = result.get("comparison") or {}
            if result.get("failure"):
                row["failure"] = result["failure"]
            if compared:
                row["exits"] = [arm["exitCode"] for arm in compared["arms"]]
            if refused:
                row["unexpected"] = str(result["error"])
            elif result.get("original") == "passed" and result.get("candidate") in {"failed", "timed-out"}:
                row["unexpected"] = str((compared.get("arms") or [{}])[-1].get("output") or result["candidate"])[-1200:]
            elif compared and not finding["material"] and any(arm["outcome"] != "passed" for arm in compared["arms"]):
                row["unexpected"] = [side + ": " + str(arm.get("output") or arm["outcome"])[-800:]
                                     for side, arm in zip(("original", "candidate"), compared["arms"]) if arm["outcome"] != "passed"]
            rows.append(row)
        receipt["cases"] = rows
        _emit_json(receipt)
        return 2 if any(row["result"] == "refused" for row in rows) else 0
    if replacement or args.report:
        raise WorkflowError("replacements and reports require --as review or --as final; a lead break is ITEM=FILE")
    if args.case and (command or args.behavior_id):
        raise WorkflowError("use repeated --case entries or one trailing command")
    _execution_cache(identity, state)
    state = _bound_instance_state(read_active(identity), slug, workflow_id)
    items, current = current_map(identity, state, fresh=False)
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    signature = json.dumps(items, sort_keys=True)
    runs = list((current or {}).get("runs", []))
    evaluation = _evaluation(identity, state, current)
    frozen_signature = json.dumps(evaluation, sort_keys=True)
    candidate = _active_candidate_tree(identity)
    before = tree_manifest(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    specs = [(identifier if identifier != "-" else None, value) for identifier, value in args.case]
    if command:
        specs = [(args.behavior_id, shlex.join(command))]
    selected = []
    inventory = _probe_files([], identity, args.support or []) if specs else None
    for identifier, value in specs:
        entry = behavior_map.item(items, identifier) if identifier else None
        if identifier and entry is None:
            raise WorkflowError("unknown Behavior Map item: " + identifier)
        captured = _probe_capture(identity, {"command": value, **({"path": args.path} if args.path else {})},
                                  candidate, args.timeout or 900., args.support, inventory, original if entry and entry.get("kind") == "preservation" else None)
        if entry is not None:
            inputs = entry.get("boundaryInputs") or []
            entry["boundaryInputs"] = list(dict.fromkeys(captured["command"] if item == value else item for item in inputs))
            if captured["command"] not in entry["boundaryInputs"]:
                entry["boundaryInputs"].append(captured["command"])
            captured["behaviorId"] = entry["id"]
        selected.append((entry, captured))
    if not specs:
        for entry in items:
            if args.behavior_id and entry["id"] != args.behavior_id:
                continue
            observations = {(case.get("comparison") or {}).get("command"): case.get("comparison")
                            for case in behavior_map.current_cases([entry], runs)}
            for value in entry.get("boundaryInputs") or []:
                recorded = observations.get(value)
                if not recorded or "runIndex" not in recorded:
                    raise WorkflowError("capture the pending command with --case " + entry["id"] + " " + shlex.quote(value))
                selected.append((entry, recorded))
        if args.behavior_id and not any(entry["id"] == args.behavior_id for entry in items):
            raise WorkflowError("unknown Behavior Map item")
    support_overrides = {}
    if args.support and not specs:
        names = {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root) for name in args.support}
        owners = {entry["id"] for entry, _ in selected}
        targets = {probe["runIndex"] for _, probe in selected} | {
            entry["runIndex"] for entry in evaluation.values() if entry["id"] in owners}
        eligible = set()
        for index in targets:
            probe = runs[index]
            support = probe.get("support") or []
            inputs = {name for name in probe["probeFiles"] if is_test_path(name)} | _command_files(
                identity, {**probe, "support": [*support, *names]})
            eligible.update(inputs)
            declared = {os.path.relpath(os.path.normpath(Path(identity.root) / name), identity.root) for name in support}
            support_overrides[index] = {**probe, "support": [*support, *sorted((names & inputs) - declared)]}
        if names - eligible:
            raise WorkflowError("recorded replay support must name a captured test or command input; supply a command to capture new support")
        selected = [(entry, support_overrides[probe["runIndex"]]) for entry, probe in selected]
    for entry, probe in selected:
        identifier = entry["id"] if entry else "-"
        if identifier in breaks:
            probe["breakOid"] = breaks[identifier]
        elif entry and not probe.get("breakOid"):
            known = (entry.get("comparison") or {}).get("breakOid")
            if known:
                probe["breakOid"] = known
    unknown = set(breaks) - {entry["id"] if entry else "-" for entry, _ in selected}
    if unknown:
        raise WorkflowError("break has no selected item: " + ", ".join(sorted(unknown)))
    if not specs:
        selected_indexes = {probe.get("runIndex") for _, probe in selected}
        selected.extend((None, {**support_overrides.get(index, runs[index]),
                                "breakOid": breaks.get(frozen["id"], runs[index].get("breakOid"))})
                        for frozen in evaluation.values() if (not args.behavior_id or frozen["id"] == args.behavior_id)
                        and (index := frozen["runIndex"]) not in selected_indexes)
    compared = _compare_many(identity, original, candidate, [probe for _, probe in selected], runs)
    indexes = {probe["runIndex"]: run["runIndex"] for (_, probe), run in zip(selected, compared) if "runIndex" in probe}
    for (entry, _), run in zip(selected, compared):
        if entry is not None:
            prior = entry.get("comparison") or {}
            references = prior.get("caseRuns", {prior["command"]: prior["runIndex"]} if prior.get("command") else {}) if prior.get("obligation") == behavior_map.obligation(entry) else {}
            references = {**references, run["command"]: run["runIndex"]}
            if prior.get("caseRuns") != references or any(prior.get(key) != run.get(key) for key in ("runIndex", "candidateKey", "breakOid", "controlKey", "sensitive", "breakExit")) or prior.get("obligation") != behavior_map.obligation(entry):
                entry["comparison"] = {**run, "obligation": behavior_map.obligation(entry), "caseRuns": references}
            behavior_map.retain_evaluation([entry], {entry["id"]}, evaluation, runs)
    for frozen in evaluation.values() if indexes else ():
        frozen["runIndex"] = indexes.get(frozen["runIndex"], frozen["runIndex"])
    for entry in items if indexes else ():
        proof = entry.get("comparison") or {}
        if references := proof.get("caseRuns"):
            updated = {command: indexes.get(index, index) for command, index in references.items()}
            if updated != references or indexes.get(proof["runIndex"], proof["runIndex"]) != proof["runIndex"]:
                entry["comparison"] = {**runs[indexes.get(proof["runIndex"], proof["runIndex"])], "obligation": proof["obligation"], "caseRuns": updated}
    selected_only = bool(specs or args.behavior_id)
    reused = bool(current and len(runs) == len(current.get("runs", [])) and signature == json.dumps(items, sort_keys=True)
                  and frozen_signature == json.dumps(evaluation, sort_keys=True))
    if reused:
        readiness = None if selected_only else _readiness_lines(identity, state, items,
                    documents=(current, evidence_document(identity, state.get("preflightEvidence"))), candidate=_active_candidate_tree(identity))
        evidence = state["tddEvidence"]
    else:
        document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                    "evaluation": evaluation, "runs": runs, "comparedRuns": [run["runIndex"] for run in compared],
                    "candidateTree": candidate, "updatedAt": utc_timestamp()}
        state, evidence, readiness = commit_tdd(identity, slug, workflow_id, document,
                    expected_evidence_id=state.get("tddEvidence"), tree_before=before, selected_only=selected_only)
    rows = []
    reported = {run["runIndex"]: (entry, run) for (entry, _), run in zip(selected, compared)}
    for entry, run in reported.values():
        row = {"case": run["command"], "exits": [arm["exitCode"] for arm in run["arms"]], "breakExit": run.get("breakExit"),
               "work": [arm.get("work") for arm in run["arms"]]}
        original = behavior_map.original_outcome(run)
        expected_original = original if entry is None else "passed" if entry.get("kind") == "preservation" else "failed"
        errors = _work_errors(run)
        if errors:
            identifier = "COST-" + hashlib.sha256(run["command"].encode()).hexdigest()[:16]
            if {entry.get("status") for entry in state.get("findingStates", []) if entry["findingId"] == identifier} == {"resolved"}:
                errors = []
        for side, outcome, arm, expected in (("original", original, run["arms"][0], expected_original), ("candidate", run["arms"][-1]["outcome"], run["arms"][-1], "passed")):
            if outcome != expected:
                errors.append(side + " " + outcome + ": " + str(arm.get("output") or arm.get("bindingError") or arm["outcome"])[-1200:])
        if not run.get("sensitive"):
            errors.append(run.get("breakError") or "supply --change ITEM=FILE: every command must fail when this behavior is broken")
        if errors:
            row["unexpected"] = errors
        rows.append(row)
    receipt = operation_receipt(state, identity, kind="tdd", summaryId=evidence, readiness=readiness, obligations=not selected_only,
                                cases=rows, preparation=[{"tree": state["source"], "wait": state["wait"]} for state in scope["sources"].values()])
    _emit_json(receipt)
    return 0 if all(run["valid"] and not run.get("bindingError") for _, run in reported.values()) and not any(row.get("unexpected") for row in rows) else 2


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if set(value) not in ({"items"}, {"added"}, {"removed"}):
        raise ValueError("tdd-map takes items, added or removed; statuses and dispositions are runner-owned")
    previous, current = current_map(identity, state)
    recorded = {entry["id"]: entry for entry in previous or []}
    removed = value.get("removed", [])
    if not isinstance(removed, list) or any(not isinstance(name, str) or name not in recorded for name in removed):
        raise ValueError("removed must contain existing probe ids")
    merged = value.get("items", value.get("added", []))
    if isinstance(merged, list):  # an item names only what changes: a given field replaces, null removes, an omitted field stays
        fields = behavior_map.REQUIRED_FIELDS | behavior_map.JUDGEMENT_FIELDS
        for position, entry in enumerate(merged):
            if isinstance(entry, dict):
                prior = recorded.get(str(entry.get("id")).strip(), {})
                kept = {key: prior[key] for key in fields if prior.get(key) not in (None, [])
                        and not ("interpretations" in entry and key in ("interpretation", "authority"))}
                merged[position] = {key: field for key, field in {**kept, **entry}.items() if field is not None}
    updates = behavior_map.initial_items(merged)
    if state.get("preflightEvidence"):
        for entry in updates:
            prior = recorded.get(entry["id"])
            if entry["kind"] in {"contract", "conformance"} or (prior or {}).get("kind") in {"contract", "conformance"}:
                if prior is None or any(entry.get(key) != prior.get(key) for key in ("kind", "behavior", "expected")):
                    raise WorkflowError("recorded contract and conformance obligations are fixed; user scope changes require a new pass")
    items = [item for item in {item["id"]: item for item in [*(previous or []), *updates]}.values()
             if item["id"] not in removed]
    for entry in updates:
        prior = recorded.get(entry["id"])
        if prior is not None and "boundaryInputs" in prior and "boundaryInputs" not in entry:
            entry["boundaryInputs"] = []  # removing every command cannot fall back to unrelated evidence
        if prior and "comparison" in prior:  # a changed kind or obligation keeps its latest run and reads stale until rerun
            comparison = {**prior["comparison"], "obligation": prior["comparison"].get("obligation", behavior_map.obligation(prior))}
            if references := comparison.get("caseRuns"):
                active = {command: index for command, index in references.items() if command in entry.get("boundaryInputs", [])}
                if active != references:
                    comparison = ({**current["runs"][next(reversed(active.values()))], "obligation": comparison["obligation"], "caseRuns": active}
                                  if active else {})
            entry["comparison"] = comparison
    if items == previous:
        return operation_receipt(state, identity, summaryId=state.get("tddEvidence") or state["preflightEvidence"],
                                 pending=behavior_map.unresolved(items, _readiness_lines(identity, state, items)["open"]), reused=True)
    try:
        tree_manifest(identity)
    except RuntimeError as exc:
        raise WorkflowError(str(exc)) from exc
    document = {"workflowId": state["workflowId"], "slug": state["slug"], "kind": "comparison",
                "behaviorMap": items, "runs": (current or {}).get("runs", []), "updatedAt": utc_timestamp()}
    state, evidence, readiness = commit_tdd(identity, str(state["slug"]), str(state["workflowId"]), document,
                                expected_evidence_id=state.get("tddEvidence"))
    return operation_receipt(state, identity, summaryId=evidence, pending=behavior_map.unresolved(items, readiness["open"]))
