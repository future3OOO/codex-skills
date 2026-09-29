"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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
    for item in items:
        proof = item.get("comparison")
        if proof:
            command = shlex.split(proof["command"])
            try:
                files = _probe_files(tdd_surface.identify(command), Path(identity.root), proof.get("support", []))
                reviewed = _reviewed_sources(identity, state, item)
                if set(reviewed.values()) <= {arm["requestedTree"] for arm in proof["arms"]}:
                    proof["reviewSources"] = reviewed
                proof["fresh"] = (bool(proof.get("execution")) and files == proof["probeFiles"]
                    and proof.get("candidateKey") == _execution_key(identity, candidate, candidate, files,
                        command, proof["timeout"], proof["execution"])
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
    targets, discover, ambiguous, unresolved = (tdd_surface.proof_targets(surface, root)
        if surface.get("runner") in {"pytest", "unittest"} else ([], False, False, []))
    if ambiguous or (unresolved and not targets):
        if not support:
            raise WorkflowError("name the probe's local test target or --support: " + str(unresolved or ambiguous))
        targets = []
    paths = set()
    arguments = surface.get("arguments") or []
    if (surface.get("runner") == "exact" and len(arguments) > 1
            and tdd_surface.INTERPRETER.fullmatch(Path(arguments[0]).name)
            and arguments[1].endswith(".py") and is_test_path(arguments[1])):
        paths.add(os.path.relpath(root / arguments[1], root))
    for target in targets:
        scope = _target_scope(target, root, discover or surface.get("runner") == "pytest")
        if scope is None:
            raise WorkflowError("probe target cannot be resolved: " + target)
        if scope[2]:
            names = _git(resolve_repo_identity(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z")
            paths.update(name for raw in names.split(b"\0") if raw
                         and is_test_path(name := os.fsdecode(raw))
                         and (root / name).exists()
                         and (root / name).is_relative_to(root / scope[0]))
        else:
            paths.add(scope[0])
    paths.update(support)
    for name in list(paths):
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise WorkflowError("probe support must be an in-repository regular file: " + name)
        if not is_test_path(name):
            raise WorkflowError("production source cannot be overlaid as test support: " + name)
        for parent in path.relative_to(root).parents:
            init = root / parent / "__init__.py"
            if init.is_file() and is_test_path(str(init.relative_to(root))):
                paths.add(str(init.relative_to(root)))
        for parent in [path.parent, *path.parent.parents]:
            if not parent.is_relative_to(root):
                break
            conf = parent / "conftest.py"
            if conf.is_file():
                paths.add(str(conf.relative_to(root)))
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
    return hashlib.sha256(b"\0".join(production) + support + config).hexdigest()


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
                aliases = [key for key in keys if key]
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
                  command: list[str], surface: JsonObject, timeout: float) -> JsonObject:
    with tempfile.TemporaryDirectory(prefix="workflow-proof-") as temporary:
        root = Path(temporary) / "source"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", identity.root, str(root)],
                       check=True, capture_output=True)
        snapshot = resolve_repo_identity(root)
        _git(snapshot, "read-tree", source_tree)
        _git(snapshot, "checkout-index", "--all")
        for raw in _git(snapshot, "ls-files", "-z").split(b"\0"):
            if raw and is_test_path(name := os.fsdecode(raw)):
                (root / name).unlink(missing_ok=True)
        for name in files:
            path = root / name
            if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
                raise WorkflowError("snapshot probe parent is a symlink: " + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_git(identity, "show", f"{candidate_tree}:{name}"))
            mode = int(_git(identity, "ls-tree", candidate_tree, "--", name).split()[0], 8)
            path.chmod(mode & 0o777)
        env = _environment()
        search = env.get("PYTHONPATH", "").replace(str(identity.root), str(root))
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (str(root), search))), PWD=str(root), TMPDIR=temporary,
                   CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        actual = [token.replace(str(identity.root) + "/", str(root) + "/") for token in command]
        if tdd_surface.INTERPRETER.fullmatch(Path(command[0]).name) or surface.get("runner") in {"pytest", "unittest"}:
            actual[0] = _executable(identity, command[0])
        if surface.get("runner") == "pytest":
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "--override-ini=addopts=")
        try:
            raw, code, timed_out = _run(actual, snapshot, timeout, env=env)
        except OSError as exc:
            raw, code, timed_out = str(exc).encode(), 127, False
        output = raw.decode("utf-8", errors="replace")
        proof, error = None, "command timed out" if timed_out else ""
        if not timed_out:
            if code == 0:
                proof, error = _pass_proof(surface, output)
            else:
                proof, error = tdd_surface.evaluate_red(surface, output, "", root)
        outcome = ("passed" if code == 0 else "failed") if proof else "incomplete"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome,
                          proof=proof, error=error, output=output, loadedRoot=str(root))


def _run_tdd(values: list[str]) -> int:
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", description="Compare one probe on recorded original and candidate sources")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--behavior-id", required=True)
    parser.add_argument("--support", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(values[:dash])
    command = values[dash + 1:]
    if not command or args.timeout <= 0:
        raise ValueError("a probe command after -- and a positive timeout are required")
    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    mapped = behavior_map.item(items, args.behavior_id)
    surface = tdd_surface.identify(command)
    if refusal := tdd_surface.repository_resolution(surface, identity.root):
        raise WorkflowError(refusal)
    files = _probe_files(surface, Path(identity.root), args.support)
    before = tree_manifest(identity)
    candidate = _active_candidate_tree(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    reviewed = _reviewed_sources(identity, state, mapped)
    sources = [original, *dict.fromkeys(reviewed.values()), candidate]
    execution = {"executable": _executable(identity, command[0]),
                 "environment": hashlib.sha256(json.dumps(sorted(_environment().items())).encode()).hexdigest()}
    keys = [_execution_key(identity, source, candidate, files, command, args.timeout, execution) for source in sources]
    previous = mapped.get("comparison")
    if previous and previous.get("valid") and previous.get("fresh") and previous.get("candidateKey") == keys[-1]:
        _emit_json(operation_receipt(state, identity, kind="tdd", behaviorId=args.behavior_id,
                   summaryId=state["tddEvidence"], runIndex=previous["runIndex"], valid=True, reused=True,
                   comparison=previous["comparison"], arms=behavior_map.comparison_view(previous)["arms"]))
        return 0
    cache = {(arm.get("key"), arm.get("sourceTree")): arm for run in (current or {}).get("runs", [])
             for arm in run.get("arms", []) if arm.get("outcome") in {"passed", "failed"}}
    arms = []
    for source, key in zip(sources, keys):
        arm = cache.get((key, source)) or next((arm for (held, _), arm in cache.items() if held == key), None)
        if arm is None:
            arm = _execute_tree(identity, source, candidate, files, command, surface, args.timeout)
            arm["key"] = key
        arms.append({**arm, "requestedTree": source})
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
    mapped["comparison"] = run
    document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                "runs": [*(current or {}).get("runs", []), run], "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, slug, workflow_id, document,
                                expected_evidence_id=state.get("tddEvidence"), tree_before=before)
    _emit_json(operation_receipt(state, identity, kind="tdd", behaviorId=args.behavior_id,
               summaryId=evidence, runIndex=len(document["runs"])-1, valid=run["valid"],
               comparison=comparison, arms=behavior_map.comparison_view(run)["arms"]))
    return 0 if run["valid"] else 2


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
