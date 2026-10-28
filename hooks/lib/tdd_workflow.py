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

from . import behavior_map, mcdc, tdd_surface
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _active_candidate_tree, _git, is_test_path, tree_manifest, utc_timestamp
from .workflow_state import (
    NO_INSTANCE_ID, TDD_CLOSED, WorkflowError, bound_state,
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
                       for name in ("tdd_workflow.py", "tdd_surface.py", "command_runner.py", "mcdc.py"))
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
                  command: list[str], surface: JsonObject, timeout: float, measurement=None) -> JsonObject:
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
        log = Path(temporary) / "mcdc.jsonl"
        unavailable = []
        instrument_started = time.perf_counter()
        if measurement:
            log.touch()
            runtime = Path(temporary) / "instrumentation"
            runtime.mkdir()
            shutil.copyfile(Path(mcdc.__file__), runtime / "_workflow_mcdc_runtime.py")
            for path in dict.fromkeys(plan["path"] for plan in measurement):
                source = root / path
                if any(p.is_symlink() for p in (source, *source.parents) if p.is_relative_to(root)):
                    unavailable.append({"path": path, "reason": "instrumentation cannot rewrite a symbolic link"})
                    continue
                try:
                    source.write_text(mcdc.instrument(source.read_text(), [p for p in measurement if p["path"] == path]))
                except (ValueError, SyntaxError) as error:
                    unavailable.append({"path": path, "reason": str(error)})
        instrumentation_seconds = time.perf_counter() - instrument_started if measurement else 0.0
        env = _environment()
        if measurement:
            env["WORKFLOW_MCDC_LOG"] = str(log)
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (identity.root, env.get("PYTHONPATH")))), PWD=identity.root,
                   TMPDIR=temporary, CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        if measurement:
            env["PYTHONPATH"] = str(Path(temporary) / "instrumentation") + os.pathsep + env["PYTHONPATH"]
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
                  "executionSeconds": time.perf_counter() - process_started,
                  "instrumentationSeconds": instrumentation_seconds}
        if measurement:
            return {"records": [json.loads(line) for line in log.read_text().splitlines()],
                    "unavailable": unavailable, "timing": timing, "exitCode": code, "timedOut": timed_out, "output": output[:8000]}
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
    plans, unavailable = [], []
    old_files = {os.fsdecode(p) for p in _git(identity, "ls-tree", "-r", "--name-only", "-z", original).split(b"\0")}
    new_files = {os.fsdecode(p) for p in _git(identity, "ls-tree", "-r", "--name-only", "-z", candidate).split(b"\0")}
    for path in changed:
        before = _git(identity, "show", original + ":" + path).decode("utf-8", errors="replace") if path in old_files else ""
        after = _git(identity, "show", candidate + ":" + path).decode("utf-8", errors="replace") if path in new_files else ""
        touched, unsupported = mcdc.analyse(before, after, path)
        plans.extend(touched)
        unavailable.extend(unsupported)
    return {"patch": patch[:8000], "truncated": len(patch) > 8000,
            "command": shlex.join(["git", "-C", identity.root, *command]) if changed else None,
            "coverage": {"plans": plans, "unavailable": unavailable}}



@interruptible()
def _run_tdd(values: list[str]) -> int:
    started = time.perf_counter()
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
    analysis_started = time.perf_counter()
    source_delta = _source_delta(identity, original, candidate)
    coverage_sources = {key: tree for item in mapped for key, tree in (item.get("comparison") or {}).get("coverageSources", {}).items()}
    plans = {plan["id"]: plan for plan in source_delta["coverage"].pop("plans")}
    for plan in plans.values():
        for context in plan["contexts"]:
            coverage_sources.setdefault(f"{plan['id']}/{context['index']}", candidate)
    for tree in dict.fromkeys(coverage_sources.values()):
        if tree == candidate:
            continue
        for plan in _source_delta(identity, original, tree)["coverage"]["plans"]:
            if plan["id"] in plans:
                known = {(c["index"], tuple(c["values"].items())) for c in plans[plan["id"]]["contexts"]}
                plans[plan["id"]]["contexts"].extend(c for c in plan["contexts"] if (c["index"], tuple(c["values"].items())) not in known)
            else:
                plans[plan["id"]] = plan
    previous = [item.get("comparison") for item in mapped]
    sources = [original, *dict.fromkeys(tree for tree in [*reviewed.values(), *coverage_sources.values(),
                                                        *(arm["requestedTree"] for proof in previous if proof for arm in proof["arms"])]
                                       if tree not in {original, candidate}), candidate]
    analysis_seconds = time.perf_counter() - analysis_started
    execution = {"executable": _executable(identity, command[0]),
                 "support": args.support,
                 "environment": hashlib.sha256(json.dumps(sorted(_environment().items())).encode()).hexdigest()}
    keys = [_execution_key(identity, source, candidate, files, command, args.timeout, execution) for source in sources]
    if (all(proof and proof.get("valid") and proof.get("fresh") and proof.get("candidateKey") == keys[-1] for proof in previous)
            and len({proof["runIndex"] for proof in previous}) == 1
            and not _readiness_stale(state, items)):
        receipt = operation_receipt(state, identity, kind="tdd", **selection,
                   summaryId=state["tddEvidence"], runIndex=previous[0]["runIndex"], valid=True, reused=True,
                   comparison=previous[0]["comparison"], sourceDelta=previous[0].get("sourceDelta"),
                   **{k: v for k, v in behavior_map.comparison_view(previous[0]).items()
                      if k in {"arms", "cases", "caseAttribution"}})
        _emit_json(receipt, sort_keys=False)
        return 0
    cache = {(arm.get("key"), arm.get("sourceTree")): arm for run in (current or {}).get("runs", [])
             for arm in run.get("arms", []) if arm.get("outcome") in {"passed", "failed"}}
    held = {key: arm for (key, _), arm in cache.items()}
    arms: list[JsonObject] = []
    executed_seconds = 0.0
    # Arms share host resources, so distinct production sources run one at a time.
    for source, key in zip(sources, keys):
        arm = cache.get((key, source))
        if arm is None:
            if key not in held:
                held[key] = {**_execute_tree(identity, source, candidate, files, command, surface, args.timeout),
                             "key": key}
                executed_seconds += held[key]["timing"]["totalSeconds"]
            arm = held[key]
        arms.append({**arm, "requestedTree": source})
    measured = None
    if plans:
        measured = _execute_tree(identity, original, candidate, files, command, surface, args.timeout, list(plans.values()))
        source_delta["coverage"]["unavailable"].extend(measured["unavailable"])
        source_delta["coverage"].update(measurement={k: measured[k] for k in ("exitCode", "timedOut", "output", "timing")},
                                        decisions=mcdc.coverage(list(plans.values()), measured["records"]))
    else:
        source_delta["coverage"]["decisions"] = []
    source_delta["coverage"]["editedTrees"] = list(dict.fromkeys(coverage_sources.values()))
    source_delta["coverage"]["timing"] = {
        "analysisSeconds": analysis_seconds,
        "sourceArmsSeconds": executed_seconds,
        "measurementSeconds": measured["timing"]["totalSeconds"] if measured else 0.0,
        "comparisonOverheadSeconds": time.perf_counter() - started - analysis_seconds - executed_seconds
                                     - (measured["timing"]["totalSeconds"] if measured else 0.0)}
    outcomes = [arm["outcome"] for arm in arms]
    valid = outcomes[-1] == "passed" and all(outcome in {"passed", "failed"} for outcome in outcomes[:-1])
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    comparison = "incomplete" if not valid else "preserved" if preserved else "changed"
    run = {"runIndex": len((current or {}).get("runs", [])), "command": shlex.join(command), "candidateTree": candidate, "originalTree": original,
           "probeFiles": files, "support": args.support, "timeout": args.timeout, "candidateKey": keys[-1], "reviewSources": reviewed,
           "sourceDelta": source_delta, "coverageSources": coverage_sources,
           "comparison": comparison, "arms": arms, "valid": valid, "execution": execution,
           "exitCode": 0 if valid else 1, "timedOut": any(arm["timedOut"] for arm in arms)}
    for item in mapped:
        item["comparison"] = {**run, "reviewSources": owner_sources[item["id"]]}
    document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                "runs": [*(current or {}).get("runs", []), run], "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, slug, workflow_id, document,
                                expected_evidence_id=state.get("tddEvidence"), tree_before=before)
    receipt = operation_receipt(state, identity, kind="tdd", **selection,
               summaryId=evidence, runIndex=len(document["runs"])-1, valid=run["valid"],
               comparison=comparison, sourceDelta=source_delta,
               **{k: v for k, v in behavior_map.comparison_view(run).items() if k in {"arms", "cases", "caseAttribution"}})
    _emit_json(receipt, sort_keys=False)
    return 0 if run["valid"] else 2


def refresh_comparisons(identity: RepoIdentity, state: JsonObject) -> bool:
    """Refresh recorded operations after quality succeeds, without caller reassembly.
    Stale recorded readiness republishes through one owning operation's cached arms."""
    items, _ = current_map(identity, state)
    refreshed = set()
    stale = _readiness_stale(state, items or [])
    for item in items or []:
        proof = item.get("comparison")
        if not proof or (proof.get("fresh") and not stale) or proof["runIndex"] in refreshed:
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
        stale = False
    return True


def _readiness_stale(state: JsonObject, items: list[JsonObject]) -> bool:
    return state.get("tdd") != ("in-progress" if behavior_map.unresolved(items) else "passed")


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if set(value) not in ({"items"}, {"added"}):
        raise ValueError("tdd-map takes items to update by id; statuses and dispositions are runner-owned")
    updates = behavior_map.initial_items(value.get("items", value.get("added")))
    previous, current = current_map(identity, state)
    items = list({item["id"]: item for item in [*(previous or []), *updates]}.values())
    for entry in items:
        prior = next((item for item in previous or [] if item["id"] == entry["id"]), None)
        if prior and "comparison" in prior and all(prior.get(k) == entry.get(k) for k in {*prior, *entry} - {"comparison", "sourceRefs"}):
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
