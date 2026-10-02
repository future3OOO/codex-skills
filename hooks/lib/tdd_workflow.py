"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import behavior_map, tdd_surface
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
             "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS",
             "WORKFLOW_REACH", "WORKFLOW_REACH_ROOT", "WORKFLOW_REACH_CHILD"}
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


# Imported first by the probe's first Python process only (it removes the variable its
# children would inherit): which code objects under the source root that process ran,
# each with the test function that ran it, and every test function started.
# Every Python process the probe starts records the source-tree code it ran. In the first,
# each call is credited when probe code (a test file or inline -c/stdin code) is on its
# stack, and attributed to the enclosing test function; later processes are the probe's children.
_REACH_SITE = """import os, sys
_site, _root = os.environ.get("WORKFLOW_REACH"), os.environ.get("WORKFLOW_REACH_ROOT", "")
if _site:
    import atexit, json, threading
    _child = bool(os.environ.get("WORKFLOW_REACH_CHILD"))
    os.environ["WORKFLOW_REACH_CHILD"] = "1"
    _out = os.path.join(_site, "child-%d.json" % os.getpid() if _child else "reach.json")
    with open(os.path.join(_site, "probes.json")) as _stream:
        _probes = set(json.load(_stream))
    _codes, _tests, _monitor = set(), set(), hasattr(sys, "monitoring")
    def _relative(code):
        name = code.co_filename if os.path.isabs(code.co_filename) else os.path.abspath(code.co_filename)
        return name[len(_root):] if name.startswith(_root) else None
    def _seen(code, frame):
        relative = _relative(code)
        if relative is None:
            return
        span = (relative, code.co_firstlineno, max((l for _, _, l in code.co_lines() if l), default=code.co_firstlineno),
                code.co_name)
        if _child:
            _codes.add(span)
            return
        if relative in _probes and code.co_name.startswith("test"):
            _tests.add(relative + "::" + code.co_qualname)
            _monitor and sys.monitoring.restart_events()
            return
        test, probe, caller = "", False, frame.f_back
        while caller is not None and not test:
            owner = _relative(caller.f_code)
            if owner in _probes:
                probe = True
                test = owner + "::" + caller.f_code.co_qualname if caller.f_code.co_name.startswith("test") else ""
            caller = caller.f_back
        _codes.add((*span, test, probe))
    if _monitor:
        sys.monitoring.use_tool_id(3, "workflow-reach")
        sys.monitoring.register_callback(3, sys.monitoring.events.PY_START,
                                         lambda code, _: _seen(code, sys._getframe(1)) or sys.monitoring.DISABLE)
        sys.monitoring.set_events(3, sys.monitoring.events.PY_START)
    else:
        _profile = lambda frame, event, _: event == "call" and _seen(frame.f_code, frame)
        sys.setprofile(_profile); threading.setprofile(_profile)
    def _write():
        with open(_out + ".part", "w") as stream:
            stream.write(json.dumps({"reach": sorted(_codes), "tests": sorted(_tests)}))
        os.replace(_out + ".part", _out)
    atexit.register(_write)
"""


def _changed_code(identity: RepoIdentity, original: str, candidate: str) -> tuple[set, set]:
    """Per side, the innermost code objects each changed production Python hunk touches. Import-time
    code (module or class body) is owned through the functions of its file, when it has any."""
    hunks: tuple[dict, dict] = ({}, {})
    names: list[str | None] = [None, None]
    diff = _git(identity, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", original, candidate, "--", "*.py")
    for line in diff.decode("utf-8", errors="replace").splitlines():
        if line.startswith(("--- ", "+++ ")):
            names[line.startswith("+++")] = None if line.endswith("/dev/null") else line[6:]
        elif match := re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line):
            for side, name, start, count in ((0, names[0], match[1], match[2]), (1, names[1], match[3], match[4])):
                if name and not is_test_path(name):
                    start, count = int(start), 1 if count is None else int(count)
                    # A pure insertion falls between two lines; only code spanning both contains it.
                    hunks[side].setdefault(name, []).append((start, start + count - 1) if count else (start + 0.5,) * 2)
    owners: tuple[set, set] = (set(), set())
    for side, tree in ((0, original), (1, candidate)):
        for name, ranges in hunks[side].items():
            try:
                pending, spans = [compile(_git(identity, "show", f"{tree}:{name}"), name, "exec")], []
            except (SyntaxError, ValueError):
                continue
            while pending:
                code = pending.pop()
                pending.extend(const for const in code.co_consts if hasattr(const, "co_lines"))
                spans.append((code.co_firstlineno, max((l for _, _, l in code.co_lines() if l), default=code.co_firstlineno),
                              code.co_name, bool(code.co_flags & inspect.CO_OPTIMIZED)))
            functions = [span for span in spans if span[3]]
            for low, high in ranges:
                hit = [span for span in spans if span[0] <= high and span[1] >= low]
                for span in hit:
                    if not any(other != span and span[0] <= other[0] and other[1] <= span[1] for other in hit):
                        owners[side].update((name, *owner[:3]) for owner in ([span] if span[3] else functions or [span]))
    return owners


def _execute_tree(identity: RepoIdentity, source_tree: str, candidate_tree: str, files: list[str],
                  command: list[str], surface: JsonObject, timeout: float) -> JsonObject:
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
        site = Path(temporary) / "reach"
        site.mkdir()
        (site / "sitecustomize.py").write_text(_REACH_SITE, encoding="utf-8")
        (site / "probes.json").write_text(json.dumps([name for name in files if os.fsencode(name) not in set(production)] + ["<string>", "<stdin>"]))
        env = _environment()
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (str(site), identity.root, env.get("PYTHONPATH")))), PWD=identity.root,
                   TMPDIR=temporary, CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"),
                   WORKFLOW_REACH=str(site), WORKFLOW_REACH_ROOT=identity.root + os.sep)
        actual = [_executable(identity, command[0]), *command[1:]]
        binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/", "--bind", str(root), identity.root]
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            binding.extend(["--ro-bind", str(runtime), str(runtime)])
        if surface.get("runner") == "pytest":
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "--override-ini=addopts=")
        try:
            raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *actual], snapshot, timeout, env=env)
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
        reach = json.loads((site / "reach.json").read_text()) if (site / "reach.json").is_file() else {}
        children = sorted({tuple(span) for path in site.glob("child-*.json")
                           for span in json.loads(path.read_text())["reach"] if not is_test_path(span[0])})
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome, proof=proof, error=error,
                          output=output, loadedRoot=identity.root, childReach=children,
                          reach=[span for span in reach.get("reach", []) if not is_test_path(span[0])],
                          tests=[test for test in reach.get("tests", []) if is_test_path(test.split("::")[0])])


@interruptible()
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
            and len({proof["runIndex"] for proof in previous}) == 1
            and not _readiness_stale(state, items)):
        _emit_json(operation_receipt(state, identity, kind="tdd", **selection,
                   summaryId=state["tddEvidence"], runIndex=previous[0]["runIndex"], valid=True, reused=True,
                   comparison=previous[0]["comparison"], arms=behavior_map.comparison_view(previous[0])["arms"]))
        return 0
    cache = {(arm.get("key"), arm.get("sourceTree")): arm for run in (current or {}).get("runs", [])
             for arm in run.get("arms", []) if arm.get("outcome") in {"passed", "failed"}}
    held = {key: arm for (key, _), arm in cache.items()}
    sides = dict(zip((len(sources) - 1, 0), reversed(_changed_code(identity, original, candidate))))
    arms: list[JsonObject] = [{"requestedTree": source, "sourceTree": source, "key": key, "outcome": "incomplete",
                               "error": "not run: an earlier arm never ran the changed code", "output": "",
                               "timedOut": False, "unreached": ""} for source, key in zip(sources, keys)]
    # Arms share HOME, network and host paths, so they run one at a time, the candidate first.
    # Proof calls the code it judges: on both sides probe code must call the changed code in its
    # own process, from every selected test, and no process it starts may run it; a miss stops the run.
    for index in (len(sources) - 1, *range(len(sources) - 1)):
        source, key, owned = sources[index], keys[index], sides.get(index)
        if key not in held:
            held[key] = {**_execute_tree(identity, source, candidate, files, command, surface, args.timeout), "key": key}
        arm = arms[index] = {**(cache.get((key, source)) or held[key]), "requestedTree": source, "unreached": ""}
        reached = [span for span in arm.get("reach", []) if tuple(span[:4]) in (owned or ()) and span[5:6] == [True]]
        hit = {span[4] for span in reached if span[4]}
        missed = sorted(set(arm.get("tests", [])) - hit) if hit else []
        child = any(tuple(span[:4]) in (owned or ()) for span in arm.get("childReach", []))
        if owned and (not reached or missed or child):
            arm["unreached"] = (("a process the probe started ran" if child else
                                 f"tests {', '.join(missed)} never ran" if missed else "the probe's own code never ran")[:300]
                                + " the changed code (" + ", ".join(sorted(f"{name}:{low}" for name, low, *_ in owned))[:200]
                                + "); call its owning Interface in-process from the probe instead")
            break
    outcomes = [arm["outcome"] for arm in arms]
    valid = (outcomes[-1] == "passed" and all(outcome in {"passed", "failed"} for outcome in outcomes[:-1])
             and not any(arm["unreached"] for arm in arms))
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    comparison = "incomplete" if not valid else "preserved" if preserved else "changed"
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


COVERED = ("the probe list is recorded: behavior runs through tdd comparisons, which the quality gate refreshes, "
           "and the suite belongs to CI. Add uncovered affected behavior to the list and compare it with tdd")


def covered(identity: RepoIdentity, state: JsonObject) -> bool:
    """A recorded probe list owns behavior execution, so a test-runner run duplicates or bypasses it."""
    items, _ = current_map(identity, state)
    return bool(items)


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
    if set(value) != {"items"}:
        raise ValueError("tdd-map takes the complete items list; statuses and dispositions are runner-owned")
    items = behavior_map.initial_items(value["items"])
    previous, current = current_map(identity, state)
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
