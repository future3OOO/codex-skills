"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import behavior_map, tdd_surface
from .command_runner import emit_json as _emit_json, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _active_candidate_tree, _git, is_test_path, tree_manifest, utc_timestamp
from .workflow_state import (
    NO_INSTANCE_ID, TDD_CLOSED, WorkflowError, _executed_selections, bound_state,
    commit_tdd, evidence_document, instance_id, operation_receipt,
)

JsonObject = dict[str, object]


def _evidence_pair(identity: RepoIdentity, state: JsonObject) -> tuple[JsonObject | None, JsonObject | None]:
    current = evidence_document(identity, state.get("tddEvidence"))
    preflight = None if current and "behaviorMap" in current else evidence_document(identity, state.get("preflightEvidence"))
    return current, preflight


def current_map(identity: RepoIdentity, state: JsonObject) -> tuple[list[JsonObject] | None, JsonObject | None]:
    current, preflight = _evidence_pair(identity, state)
    items = behavior_map.recorded_map(current, preflight)
    if items:
        refresh_proof(identity, items, state)
    return items, current


def refresh_proof(identity: RepoIdentity, items: list[JsonObject], state: JsonObject) -> None:
    candidate = _active_candidate_tree(identity)
    execution_keys: dict[str, str | None] = {}
    for item in items:
        proof = item.get("comparison")
        if proof:
            command = shlex.split(proof["command"])
            try:
                binding = json.dumps([command, proof["timeout"], proof.get("support", []), proof.get("execution")], sort_keys=True)
                if binding not in execution_keys:
                    files = _probe_files(tdd_surface.identify(command), Path(identity.root), proof.get("support", []))
                    execution_keys[binding] = (_execution_key(identity, candidate, candidate, files,
                        command, proof["timeout"], proof["execution"]) if proof.get("execution") else None)
                reviewed = _reviewed_sources(identity, state, item)
                if set(reviewed.values()) <= {arm["requestedTree"] for arm in proof["arms"]}:
                    proof["reviewSources"] = reviewed
                proof["fresh"] = (bool(proof.get("execution"))
                    and proof.get("candidateKey") == execution_keys[binding]
                    and proof.get("reviewSources", {}) == reviewed)
            except (WorkflowError, OSError, RuntimeError):
                proof["fresh"] = False


def edit_blockers(identity: RepoIdentity, state: JsonObject, *, reminders=None) -> list[str]:
    items, _ = current_map(identity, state)
    if items and reminders is not None:
        reminders.append(behavior_map.obligation_digest(items))
    return []


def completion_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    items, _ = current_map(identity, state)
    pending = behavior_map.unresolved(items or [])
    return ["unresolved probes: " + ", ".join(pending)] if pending else []


def _active_candidate(identity: RepoIdentity, value: str | None) -> tuple[JsonObject, str, str]:
    state = bound_state(identity, value)
    if state.get("revalidation"):
        raise WorkflowError(TDD_CLOSED)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    if (workflow_id := instance_id(state)) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    return state, str(state["slug"]), workflow_id


def _probe_files(surface: JsonObject, root: Path, support: list[str]) -> list[str]:
    identity = resolve_repo_identity(root)
    names = _git(identity, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = {name for raw in names.split(b"\0") if raw
             and is_test_path(name := os.fsdecode(raw)) and (root / name).exists()}
    arguments = surface.get("arguments") or []
    if surface.get("runner") == "exact" and arguments:
        shell = Path(arguments[0]).name in {"bash", "sh", "dash"}
        entry = tdd_surface.python_entry(arguments)
        if entry and entry[0] == "script":
            paths.add(os.path.relpath(root / entry[1], root))
        executable = Path(_executable(identity, arguments[0]))
        with executable.open("rb") as source:
            direct = source.read(2) == b"#!"
        if direct:
            paths.add(os.path.relpath(executable, root))
        for index, argument in enumerate(arguments[1:], 1):
            option = argument.startswith("-") and arguments[index - 1] != "--"
            inline = re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", argument)
            if (shell and option and ((inline and len(arguments) > index + 2)
                    or (not inline and not re.fullmatch(r"--(?:norc|noprofile)?|-[efuvx]+", argument)))):
                raise WorkflowError("unsupported shell probe option: " + argument)
            value = argument.partition("=")[2] if option and "=" in argument else argument
            if value.lower().startswith("file:"):
                raise WorkflowError("unsupported probe file URI: " + value)
            if value and (not shell or not option) and os.path.lexists(root / value):
                name = os.path.relpath(root / value, root)
                if not shell and not is_test_path(name) and (root / name).resolve().is_relative_to(root):
                    continue
                paths.add(name)
            if not option:
                shell = False
    paths.update(os.path.relpath(os.path.normpath(root / name), root) for name in support)
    for name in paths:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise WorkflowError("probe support must be an in-repository regular file: " + name)
        if not is_test_path(name):
            raise WorkflowError("unsupported probe: production source cannot be overlaid as test support: " + name)
    return sorted(paths)


def _environment() -> dict[str, str]:
    owned = {"SHLVL", "_", "PWD", "OLDPWD", "PYTHONHOME", "TMPDIR",
             "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS"}
    return {**{key: value for key, value in os.environ.items() if key not in owned},
            "SHLVL": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""}


def _executable(identity: RepoIdentity, command: str) -> str:
    if os.path.dirname(command):
        return os.path.normpath(os.path.join(identity.root, command))
    search = os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path())
    executable = shutil.which(command, path=search)
    if executable is None:
        raise WorkflowError("probe executable is unavailable: " + command)
    return executable


def _execution_key(identity: RepoIdentity, source: str, probe: str, files: list[str],
                   command: list[str], timeout: float, execution: JsonObject) -> str:
    entries = _git(identity, "ls-tree", "-r", "-z", source).split(b"\0")
    production = [entry for entry in entries if entry and not is_test_path(os.fsdecode(entry.split(b"\t", 1)[1]))]
    support = _git(identity, "ls-tree", "-r", "-z", probe, "--", *files) if files else b""
    executable_state = os.stat(execution["executable"])
    config = json.dumps([command, timeout, execution,
                         executable_state.st_size, executable_state.st_mtime_ns], sort_keys=True).encode()
    producer = b"".join(Path(__file__).with_name(name).read_bytes()
                       for name in ("tdd_workflow.py", "tdd_surface.py", "command_runner.py"))
    return hashlib.sha256(b"\0".join(production) + support + config + producer).hexdigest()


def _reviewed_sources(identity: RepoIdentity, state: JsonObject, mapped: JsonObject) -> dict[str, str]:
    trees = {}
    for reference in mapped.get("sourceRefs", []):
        if reference["type"] != "finding":
            continue
        aliases = [reference]
        for finding in state.get("findingStates", []):
            keys = [finding.get("canonicalFinding"), {"evidenceId": finding["intakeEvidenceId"], "id": finding["findingId"]},
                    *finding.get("observations", [])]
            if any(key and (key["evidenceId"], key["id"]) == (reference["evidenceId"], reference["id"]) for key in keys):
                aliases = [] if finding.get("status") == "fixed" else [key for key in keys if key]
        if not aliases:
            continue
        latest = aliases[-1]
        intake = evidence_document(identity, latest["evidenceId"])
        if not intake or intake.get("workflowId") != state["workflowId"]:
            raise WorkflowError("finding probe requires its recorded review intake")
        tree = intake.get("candidateTree")
        if not isinstance(tree, str):
            raise WorkflowError("finding intake has no recorded reviewed source tree")
        for alias in aliases:
            trees[f"{alias['evidenceId']}:{alias['id']}"] = tree
    return trees


def _execute_tree(identity: RepoIdentity, source_tree: str, candidate_tree: str, files: list[str],
                  command: list[str], surface: JsonObject, timeout: float,
                  cancel: threading.Event | None = None) -> JsonObject:
    with tempfile.TemporaryDirectory(prefix="workflow-proof-") as temporary:
        root = Path(temporary) / "source"
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", identity.root, str(root)],
                       check=True, capture_output=True)
        snapshot = resolve_repo_identity(root)
        _git(snapshot, "read-tree", source_tree)
        production = [raw for raw in _git(snapshot, "ls-files", "-z").split(b"\0")
                      if raw and not is_test_path(os.fsdecode(raw))]
        _git(snapshot, "checkout-index", "-z", "--stdin", stdin=b"".join(name + b"\0" for name in production))
        for name in files:
            path = root / name
            if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
                raise WorkflowError("snapshot probe parent is a symlink: " + name)
        if files:
            _git(snapshot, "--literal-pathspecs", "restore", "--worktree", "--source=" + candidate_tree,
                 "--pathspec-from-file=-", "--pathspec-file-nul",
                 stdin=b"".join(os.fsencode(name) + b"\0" for name in files))
        env = _environment()
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (identity.root, env.get("PYTHONPATH")))), PWD=identity.root, TMPDIR=temporary,
                   CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        actual = [_executable(identity, command[0]), *command[1:]]
        binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/", "--bind", str(root), identity.root]
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            binding.extend(["--ro-bind", str(runtime), str(runtime)])
        if surface.get("runner") == "pytest":
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "--override-ini=addopts=")
        try:
            raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *actual], snapshot, timeout,
                                        env=env, cancel=cancel)
        except OSError as exc:
            raw, code, timed_out = str(exc).encode(), 127, False
        output = raw.decode("utf-8", errors="replace")
        proof, error = None, "command timed out" if timed_out else ""
        if not timed_out:
            if code == 0:
                proof, error = _pass_proof(surface, output)
            else:
                proof, error = tdd_surface.evaluate_red(surface, output)
        outcome = ("passed" if code == 0 else "failed") if proof else "incomplete"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome,
                          proof=proof, error=error, output=output, loadedRoot=identity.root)


def _run_tdd(values: list[str]) -> int:
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", description="Compare one probe on recorded original and candidate sources")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--behavior-id", required=True, action="append")
    parser.add_argument("--support", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(values[:dash])
    command = values[dash + 1:]
    if not command or args.timeout <= 0:
        raise ValueError("a probe command after -- and a positive timeout are required")
    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    mapped = [behavior_map.item(items, identifier) for identifier in dict.fromkeys(args.behavior_id)]
    selection = {"behaviorId": mapped[0]["id"]} if len(mapped) == 1 else {"behaviorIds": [item["id"] for item in mapped]}
    surface = tdd_surface.identify(command)
    if refusal := tdd_surface.repository_resolution(surface, identity.root):
        raise WorkflowError(refusal)
    files = _probe_files(surface, Path(identity.root), args.support)
    before = tree_manifest(identity)
    candidate = _active_candidate_tree(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    owner_sources = {item["id"]: _reviewed_sources(identity, state, item) for item in mapped}
    reviewed = {reference: tree for sources in owner_sources.values() for reference, tree in sources.items()}
    sources = [original, *dict.fromkeys(reviewed.values()), candidate]
    execution = {"executable": _executable(identity, command[0]),
                 "support": args.support,
                 "environment": hashlib.sha256(json.dumps(sorted(_environment().items())).encode()).hexdigest()}
    keys = [_execution_key(identity, source, candidate, files, command, args.timeout, execution) for source in sources]
    previous = [item.get("comparison") for item in mapped]
    if (all(proof and proof.get("valid") and proof.get("fresh") and proof.get("candidateKey") == keys[-1] for proof in previous)
            and len({proof["runIndex"] for proof in previous}) == 1):
        _emit_json(operation_receipt(state, identity, kind="tdd", **selection,
                   summaryId=state["tddEvidence"], runIndex=previous[0]["runIndex"], valid=True, reused=True,
                   comparison=previous[0]["comparison"], arms=behavior_map.comparison_view(previous[0])["arms"]))
        return 0
    cache = {(arm.get("key"), arm.get("sourceTree")): arm for run in (current or {}).get("runs", [])
             for arm in run.get("arms", []) if arm.get("outcome") in {"passed", "failed"}}
    held = {key: arm for (key, _), arm in cache.items()}
    # Each arm owns its clone, TMPDIR and state root, so distinct production runs at once.
    pending = {key: source for source, key in reversed(list(zip(sources, keys))) if key not in held}
    cancel = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(pending))) as pool:
        futures = {key: pool.submit(_execute_tree, identity, source, candidate, files, command, surface,
                                    args.timeout, cancel) for key, source in pending.items()}
        try:
            concurrent.futures.wait(futures.values())
        except BaseException:
            cancel.set()
            raise
        for key, future in futures.items():
            arm = future.result()
            arm["key"] = key
            held[key] = arm
    arms = []
    for source, key in zip(sources, keys):
        arms.append({**(cache.get((key, source)) or held[key]), "requestedTree": source})
    outcomes = [arm["outcome"] for arm in arms]
    valid = outcomes[-1] == "passed" and all(outcome in {"passed", "failed"} for outcome in outcomes[:-1])
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    comparison = "preserved" if preserved else "changed" if valid else "incomplete"
    run = {"runIndex": len((current or {}).get("runs", [])), "command": shlex.join(command), "candidateTree": candidate, "originalTree": original,
           "probeFiles": files, "support": args.support, "timeout": args.timeout, "candidateKey": keys[-1], "reviewSources": reviewed,
           "comparison": comparison, "arms": arms, "valid": valid, "execution": execution,
           "exitCode": 0 if valid else 1, "timedOut": any(arm["timedOut"] for arm in arms)}
    for item in mapped:
        item["comparison"] = {**run, "reviewSources": owner_sources[item["id"]]}
    document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                "runs": [*(current or {}).get("runs", []), run], "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, slug, workflow_id, document,
                                expected_evidence_id=state.get("tddEvidence"), tree_before=before)
    _emit_json(operation_receipt(state, identity, kind="tdd", **selection,
               summaryId=evidence, runIndex=len(document["runs"])-1, valid=run["valid"],
               comparison=comparison, arms=behavior_map.comparison_view(run)["arms"]))
    return 0 if run["valid"] else 2


def refresh_comparisons(identity: RepoIdentity, state: JsonObject) -> bool:
    """Refresh recorded operations after quality succeeds, without caller reassembly."""
    items, _ = current_map(identity, state)
    refreshed = set()
    for item in items or []:
        proof = item.get("comparison")
        if not proof or proof.get("fresh") or proof["runIndex"] in refreshed:
            continue
        arguments = ["--repo", str(identity.root), "--slug", state["slug"], "--timeout", str(proof["timeout"])]
        for owner in items:
            if owner.get("comparison", {}).get("runIndex") == proof["runIndex"]:
                arguments.extend(["--behavior-id", owner["id"]])
        for name in proof["support"]:
            arguments.extend(["--support", name])
        if _run_tdd([*arguments, "--", *shlex.split(proof["command"])]):
            return False
        refreshed.add(proof["runIndex"])
    return True


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if set(value) != {"items"}:
        raise ValueError("tdd-map takes the complete items list; statuses and dispositions are runner-owned")
    items = behavior_map.initial_items(value["items"])
    previous, current = current_map(identity, state)
    for entry in items:
        prior = next((item for item in previous or [] if item["id"] == entry["id"]), None)
        if prior and {k: v for k, v in prior.items() if k != "comparison"} == entry and "comparison" in prior:
            entry["comparison"] = prior["comparison"]
    if items == previous:
        return operation_receipt(state, identity, summaryId=state.get("tddEvidence") or state["preflightEvidence"],
                                 pending=behavior_map.unresolved(items), reused=True)
    try:
        tree_manifest(identity)
    except RuntimeError as exc:
        raise WorkflowError(str(exc)) from exc
    document = {"workflowId": state["workflowId"], "slug": state["slug"], "kind": "comparison",
                "behaviorMap": items, "runs": (current or {}).get("runs", []), "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, str(state["slug"]), str(state["workflowId"]), document,
                                expected_evidence_id=state.get("tddEvidence"), review_changed=True)
    return operation_receipt(state, identity, summaryId=evidence, pending=behavior_map.unresolved(items))


def _pass_proof(surface: JsonObject, output: str) -> tuple[JsonObject | None, str]:
    """A successful exit must report an executed check or observable operation."""
    runner = surface.get("runner")
    output = tdd_surface.ANSI_ESCAPE.sub("", output)
    if runner not in {"unittest", "pytest"}:
        observed = tdd_surface._final_diagnostic([line for line in output.splitlines() if line.strip()])[:1000]
        if not observed:
            return None, "the operation emitted nothing to observe"
        return {"quality": "operation-succeeded", "reach": "unresolved", "runner": runner,
                "observation": [observed], "site": shlex.join(surface.get("arguments") or [])}, ""
    if runner == "unittest":
        runs = list(tdd_surface.UNITTEST_RAN.finditer(output))
        result = re.search(r"(?m)^OK(?: \((.*)\))?$", output[runs[-1].end():]) if runs else None
        if result and result.group(1):
            return None, "selected unittest probes include skipped or expected failures"
        executed = int(runs[-1].group(1)) if result else 0
    else:
        summaries = tdd_surface.PYTEST_SUMMARY.findall(output)
        if summaries and re.search(r"\b(?:skipped|xfailed|xpassed)\b", summaries[-1]):
            return None, "selected pytest probes did not all execute passing assertions"
        passed = re.search(r"(?<!\d)(\d+) passed\b", summaries[-1]) if summaries else None
        executed = int(passed.group(1)) if passed else 0
    if executed < 1:
        return None, f"{runner} did not report an executed passing test"
    return {"quality": "tests-passed", "runner": runner, "testsExecuted": executed}, ""


_ADVISORY_TIMEOUT = 10
# Git permits control bytes in a path and the graph can surface one verbatim, so
# escape them before the path reaches the one-line notice.
_ADVISORY_CONTROL_ESCAPES = {c: f"\\x{c:02x}" for c in range(0x20)} | {0x7f: "\\x7f"}


def map_advisory(identity: RepoIdentity, state: JsonObject) -> str | None:
    """After a successful production edit, name the impacted tests the map does
    not own, or a short gap when that cannot be decided against this pass's
    index. Advisory only: it returns at most one notice line for the caller to
    deliver and never raises into the edit it follows."""
    try:
        snapshot = state.get("passStartSnapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            return _advisory_publish("the pass-start index identity was not recorded", {})
        root = Path(identity.root)
        impacted, gap = _impacted_tests(snapshot, root)
        owned = _owned_scopes(identity, state, root)
        unowned: dict[str, int] = {}
        for entry in impacted:
            path = str(entry.get("filePath") or "").replace("\\", "/")
            node = (str(entry.get("id") or "").split(":", 2)[2:] or [""])[0]
            if path and not _is_owned(path, node, owned):
                unowned[path] = unowned.get(path, 0) + 1
        return _advisory_publish(gap, unowned)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError, subprocess.SubprocessError):
        return _advisory_publish("the advisory could not complete", {})


def _impacted_tests(snapshot: JsonObject, root: Path) -> tuple[list[JsonObject], str | None]:
    """The impacted tests the pass-start index attributes to the current candidate,
    with a gap reason when the analysis is not a complete diff against that index."""
    binary = shutil.which("gitnexus")
    if binary is None:
        return [], "the graph tool is unavailable"
    try:
        proc = subprocess.run(
            [binary, "detect-changes", "--repo", str(snapshot["indexRepo"]), "--worktree", str(root)],
            capture_output=True, text=True, check=False, timeout=_ADVISORY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [], "the graph diff did not finish in time"
    if proc.returncode != 0:
        return [], "the pass-start index could not be diffed"
    data = json.loads(proc.stdout)
    impacted = data.get("impacted_tests") or []
    analysis = data.get("analysis") or {}
    baseline = analysis.get("baseline") or {}
    if baseline.get("tree") != snapshot.get("indexedTree") or baseline.get("source_commit") != snapshot.get("sourceCommit"):
        return impacted, "the graph baseline is not this pass's index"
    status = analysis.get("status")
    return impacted, None if status == "complete" else f"the graph analysis is {status}"


def _owned_scopes(identity: RepoIdentity, state: JsonObject, root: Path) -> list[tuple[str, str, bool]]:
    """Every (path, node-prefix, is-directory) the current map's recorded proofs
    selected. An unresolved selection contributes nothing, so it owns nothing."""
    selections = _executed_selections(identity, state) or {}
    scopes: list[tuple[str, str, bool]] = []
    for phases in selections.values():
        if not isinstance(phases, dict):
            continue
        for selection in phases.values():
            if not isinstance(selection, dict):
                continue
            targets = selection.get("targets")
            if not isinstance(targets, list):
                continue
            # A directory owns its subtree only for a recursive selection: a
            # pytest path or a unittest `discover`. A plain unittest package
            # load is non-recursive, so it owns only the tests it names.
            surface = tdd_surface.identify(shlex.split(str(selection.get("command") or "")))
            recursive = surface.get("runner") == "pytest" or bool(selection.get("discover"))
            for target in targets:
                scope = _target_scope(str(target), root, recursive)
                if scope is not None:
                    scopes.append(scope)
    return scopes


def _target_scope(target: str, root: Path, recursive: bool) -> tuple[str, str, bool] | None:
    """Resolve a recorded selection target to (path, node-prefix, is-directory)
    under root, or None when it names nothing there or is a non-recursive
    directory. A pytest target is a path with an optional ``::`` node; a unittest
    target is a dotted path whose file prefix is found on disk and its remainder
    the node."""
    if "::" in target or "/" in target or target.endswith(".py") or target in (".", ".."):
        head, _, node = target.partition("::")
        resolved = root / head.rstrip("/")
        is_dir = resolved.is_dir()
        if is_dir and not recursive:
            return None
        # Normalise to the producer's root-relative filePath shape, so a
        # recorded "./tests/x.py" or "." matches "tests/x.py"; "" owns the tree.
        relative = _relative(resolved, root)
        path = "" if relative == "." else relative
        return path, node.replace("::", "."), is_dir
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        base = root.joinpath(*parts[:i])
        file = base.with_suffix(".py")
        if file.is_file():
            return _relative(file, root), ".".join(parts[i:]), False
        if base.is_dir():
            return (_relative(base, root), "", True) if recursive else None
    return None


def _relative(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _is_owned(path: str, node: str, scopes: list[tuple[str, str, bool]]) -> bool:
    """Whether one impacted test (path, node) falls inside any selected scope: a
    directory owns its subtree, a file with an empty node-prefix owns the file,
    and a node-prefix owns itself and its descendants."""
    for scope_path, node_prefix, is_dir in scopes:
        if is_dir:
            if scope_path == "" or path == scope_path or path.startswith(scope_path.rstrip("/") + "/"):
                return True
        elif path == scope_path and (
            node_prefix == "" or node == node_prefix or node.startswith(node_prefix + ".")
        ):
            return True
    return False


def _advisory_publish(gap: str | None, unowned: dict[str, int]) -> str | None:
    """The one notice line, or None when there is nothing to report; the hook
    delivers it only when it changed for this session."""
    paths = {name: unowned[name] for name in sorted(unowned)}
    if not gap and not paths:
        return None
    sort = sorted(paths)
    shown = ", ".join(p.translate(_ADVISORY_CONTROL_ESCAPES) for p in sort[:10])
    remaining = len(sort) - 10
    if paths:
        total = sum(paths.values())
        report = f"{total} impacted tests not owned by the map: {shown}"
        if remaining > 0:
            report += f" (and {remaining} more)"
    else:
        report = ""
    if gap:
        report = f"gap, {gap}" + (f"; {report}" if report else "")
    return f"map advisory: {report}"
