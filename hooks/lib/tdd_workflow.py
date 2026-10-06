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
import time
from pathlib import Path

from . import behavior_map, tdd_surface
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _active_candidate_tree, _git, is_test_path, tree_manifest, utc_timestamp
from .workflow_state import (
    NO_INSTANCE_ID, WorkflowError, bound_state,
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
    """Re-judge each recorded comparison's freshness against the current tree; a map with
    no comparison captures nothing."""
    execution_keys: dict[str, str | None] = {}
    candidate: str | None = None
    for item in items:
        proof = item.get("comparison")
        if proof:
            command = shlex.split(proof["command"])
            try:
                candidate = candidate or _active_candidate_tree(identity)
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


def _probe_key(identity: RepoIdentity, probe: str, files: list[str]) -> str:
    """The probe files' content on the candidate tree; a changed key is a revised probe."""
    support = _git(identity, "ls-tree", "-r", "-z", probe, "--", *files) if files else b""
    return hashlib.sha256(support).hexdigest()


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
                  command: list[str], surface: JsonObject, timeout: float) -> JsonObject:
    started = time.perf_counter()
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
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (identity.root, env.get("PYTHONPATH")))), PWD=identity.root,
                   TMPDIR=temporary, CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        actual = [_executable(identity, command[0]), *command[1:]]
        binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/", "--bind", str(root), identity.root]
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            binding.extend(["--ro-bind", str(runtime), str(runtime)])
        if surface.get("runner") == "pytest":
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "--override-ini=addopts=")
        if surface.get("runner") in {"pytest", "unittest"}:
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "-vv" if surface["runner"] == "pytest" else "-v")
        process_started = time.perf_counter()
        try:
            raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *actual], snapshot, timeout, env=env)
        except OSError as exc:
            raw, code, timed_out = str(exc).encode(), 127, False
        output = raw.decode("utf-8", errors="replace")
        timing = {"totalSeconds": time.perf_counter() - started,
                  "executionSeconds": time.perf_counter() - process_started}
        proof, error = None, "command timed out" if timed_out else ""
        if not timed_out:
            if code == 0:
                proof, error = _pass_proof(surface, output)
            else:
                proof, error = tdd_surface.evaluate_red(surface, output)
        outcome = ("passed" if code == 0 else "failed") if proof else "incomplete"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome, proof=proof, error=error,
                          output=output, loadedRoot=identity.root, cases=tdd_surface.case_results(surface, output),
                          executedCommand=actual, timing=timing)


def _source_delta(identity: RepoIdentity, original: str, candidate: str) -> JsonObject:
    changed = [os.fsdecode(name) for name in _git(identity, "diff", "--name-only", "-z", original, candidate).split(b"\0")
               if name and not is_test_path(os.fsdecode(name))]
    command = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--unified=3", original, candidate, "--", *changed]
    patch = _git(identity, *command).decode("utf-8", errors="replace") if changed else ""
    return {"patch": patch[:8000], "truncated": len(patch) > 8000,
            "command": shlex.join(["git", "-C", identity.root, *command]) if changed else None}


@interruptible()
def _run_tdd(values: list[str]) -> int:
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", description="Compare a probe on recorded sources; omit the command to reuse its recorded batch")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--behavior-id", required=True, action="append")
    parser.add_argument("--support", action="append")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(values[:dash])
    command = values[dash + 1:]
    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    mapped = [behavior_map.item(items, identifier) for identifier in dict.fromkeys(args.behavior_id)]
    if not command:
        recorded = [item.get("comparison") or {} for item in mapped]
        batches = {(run.get("command"), tuple(run.get("support", [])), run.get("timeout")) for run in recorded}
        if len(batches) == 1 and recorded[0].get("command"):
            command = shlex.split(recorded[0]["command"])
            if args.support is None:
                args.support = recorded[0]["support"]
            if args.timeout is None:
                args.timeout = recorded[0]["timeout"]
    args.support = args.support or []
    args.timeout = 900.0 if args.timeout is None else args.timeout
    if not command or args.timeout <= 0:
        raise ValueError("supply a probe command after -- or select one recorded batch; timeout must be positive")
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
    previous = [item.get("comparison") for item in mapped]
    runs = (current or {}).get("runs", [])
    # An earlier edited tree runs again only to show a revised probe's sensitivity: a run of this
    # command at a probe key these items last compared failed there (the broken edit).
    probe_key = _probe_key(identity, candidate, files)
    revised = {proof["probeKey"] for proof in previous if proof and proof.get("probeKey") not in (None, probe_key)}
    earlier = [run["candidateTree"] for run in runs if run.get("probeKey") in revised
               and run.get("command") == shlex.join(command) and run["arms"][-1]["outcome"] == "failed"]
    sources = [original, *dict.fromkeys(tree for tree in [*reviewed.values(), *earlier]
                                       if tree not in {original, candidate}), candidate]
    execution = {"executable": _executable(identity, command[0]),
                 "support": args.support,
                 "environment": hashlib.sha256(json.dumps(sorted(_environment().items())).encode()).hexdigest()}
    keys = [_execution_key(identity, source, candidate, files, command, args.timeout, execution) for source in sources]
    # An earlier tree with the original's or the current production adds no column.
    sources, keys = map(list, zip(*[(source, key) for index, (source, key) in enumerate(zip(sources, keys))
                                     if index in (0, len(sources) - 1) or key not in (keys[0], keys[-1])]))
    if (all(proof and proof.get("valid") and proof.get("fresh") and proof.get("candidateKey") == keys[-1] for proof in previous)
            and all(behavior_map.producer_proved(item) for item in mapped)
            and len({proof["runIndex"] for proof in previous}) == 1
            and state.get("tdd") == ("in-progress" if behavior_map.unresolved(items) else "passed")):
        receipt = operation_receipt(state, identity, kind="tdd", **selection, **behavior_map.comparison_view(previous[0]),
                                    summaryId=state["tddEvidence"], reused=True)
        _emit_json(receipt, sort_keys=False)
        return 0
    cache = {(arm.get("key"), arm.get("sourceTree")): arm for run in runs
             for arm in run.get("arms", []) if arm.get("outcome") in {"passed", "failed"}}
    held = {key: arm for (key, _), arm in cache.items()}
    arms: list[JsonObject] = []
    # Arms share host resources, so distinct production sources run one at a time.
    for source, key in zip(sources, keys):
        arm = cache.get((key, source))
        if arm is None:
            if key not in held:
                held[key] = {**_execute_tree(identity, source, candidate, files, command, surface, args.timeout), "key": key}
            arm = held[key]
        arms.append({**arm, "requestedTree": source})
    outcomes = [arm["outcome"] for arm in arms]
    valid = outcomes[-1] == "passed" and all(outcome in {"passed", "failed"} for outcome in outcomes[:-1])
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    comparison = "incomplete" if not valid else "preserved" if preserved else "changed"
    run = {"runIndex": len(runs), "command": shlex.join(command), "candidateTree": candidate, "originalTree": original,
           "probeFiles": files, "support": args.support, "timeout": args.timeout, "candidateKey": keys[-1],
           "probeKey": probe_key, "reviewSources": reviewed,
           "sourceDelta": _source_delta(identity, original, candidate),
           "comparison": comparison, "arms": arms, "valid": valid, "execution": execution,
           "exitCode": 0 if valid else 1, "timedOut": any(arm["timedOut"] for arm in arms)}
    # Each comparison carries the regressions earlier ones exposed until executed evidence resolves them,
    # and whether its item is the preflight contract with an unchanged obligation.
    approved = behavior_map.approved_contracts(items, behavior_map.recorded_map(
        None, evidence_document(identity, state.get("preflightEvidence"))))
    priors = [behavior_map.judgement(item, items) or {"owed": {}, "exposed": False} for item in mapped]
    for item, prior in zip(mapped, priors):
        item["comparison"] = {**run, "reviewSources": owner_sources[item["id"]], "approved": item["id"] in approved,
                              "obligation": behavior_map.obligation(item),
                              "regressions": prior["owed"], "exposed": prior["exposed"]}
    for item in mapped:
        if judged := behavior_map.judgement(item, items):
            item["comparison"].update(regressions=judged["owed"], exposed=judged["exposed"], deferred=sorted(judged["deferred"]))
    document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                "runs": [*runs, run], "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, slug, workflow_id, document,
                                expected_evidence_id=state.get("tddEvidence"), tree_before=before)
    receipt = operation_receipt(state, identity, kind="tdd", **selection, **behavior_map.comparison_view(run),
                                summaryId=evidence)
    _emit_json(receipt, sort_keys=False)
    return 0 if run["valid"] else 2


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if set(value) not in ({"items"}, {"added"}):
        raise ValueError("tdd-map takes items to update by id; statuses and dispositions are runner-owned")
    updates = behavior_map.initial_items(value.get("items", value.get("added")))
    previous, current = current_map(identity, state)
    recorded = {entry["id"]: entry for entry in previous or []}
    items = list({item["id"]: item for item in [*(previous or []), *updates]}.values())
    for entry in updates:
        prior = recorded.get(entry["id"])
        if prior is not None and prior.get("boundaryInputs") == [] and "boundaryInputs" not in entry:
            entry["boundaryInputs"] = []  # unmapped recorded inputs stay until executed case names replace them
        if prior and "comparison" in prior:  # a changed kind or obligation keeps its debts and reads stale until rerun
            entry["comparison"] = {**prior["comparison"],
                                   "obligation": prior["comparison"].get("obligation", behavior_map.obligation(prior))}
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
