"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import argparse
import hashlib
from functools import lru_cache
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
from ._workflow_db import history
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _active_candidate_tree, _git, is_test_path, tree_manifest, utc_timestamp
from .workflow_state import (
    NO_INSTANCE_ID, WorkflowError, bound_state,
    commit_tdd, evidence_document, instance_id, operation_receipt, _readiness_lines,
)

JsonObject = dict[str, object]
PROBE_TIMEOUT = 10.0  # default for new finding probes; recorded cases keep their execution budget


def current_map(identity: RepoIdentity, state: JsonObject, *, fresh: bool = True) -> tuple[list[JsonObject] | None, JsonObject | None]:
    fields = ("preflightLatestEvidence",) if state.get("preflight") == "pending" else ()
    for field in (*fields, "tddEvidence", "preflightEvidence"):
        document = evidence_document(identity, state.get(field))
        items = behavior_map.recorded_map(document)
        if items is not None:
            if fresh:
                refresh_proof(identity, items)
            return items, document if "behaviorMap" in document else None
    return None, None


def refresh_proof(identity: RepoIdentity, items: list[JsonObject], *, candidate: str | None = None) -> None:
    """Re-judge each recorded comparison's freshness against the current production tree. The proof is the
    recorded run: its probe tree and files, never the checkout's, so a probe removed after it ran still proves."""
    freshness: dict[object, bool] = {}
    for item in items:
        proof = item.get("comparison")
        if proof:
            index = proof.get("runIndex")
            if index not in freshness:
                try:
                    candidate = candidate or _active_candidate_tree(identity)
                    command, execution, _ = _comparison_input(identity, proof)
                    freshness[index] = bool(proof.get("execution")) and proof.get("candidateKey") == _execution_key(
                        identity, candidate, proof.get("probeTree", proof["candidateTree"]), proof["probeFiles"],
                        command, proof["timeout"], execution)
                except (WorkflowError, OSError, RuntimeError):
                    freshness[index] = False
            proof["fresh"] = freshness[index]


def edit_blockers(identity: RepoIdentity, state: JsonObject, *, reminders=None) -> list[str]:
    items, _ = current_map(identity, state)
    if items and reminders is not None:
        reminders.append("Probe obligations: " + "; ".join(f"{entry['id']}: {entry['behavior']} => {entry['expected']}" for entry in items)[:1800])
    return []


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
    owned = {"SHLVL", "_", "PWD", "OLDPWD", "PYTHONHOME", "TMPDIR", "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE",
             "PYTEST_ADDOPTS", "CODEX_TOOL_CALL_ID"}
    return {**{key: value for key, value in os.environ.items() if key not in owned},
            "SHLVL": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""}


def _executable(identity: RepoIdentity, command: str, environment: dict[str, str] | None = None) -> str:
    if os.path.dirname(command):
        return os.path.normpath(os.path.join(identity.root, command))
    search = os.pathsep.join(os.path.join(identity.root, entry) for entry in os.get_exec_path(environment))
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
    # Installed packages of each runtime the command names: any path (from the root or a named directory), any Python on a PATH.
    roots = [identity.root, *(argument for argument in command if os.path.isabs(argument) and os.path.isdir(argument))]
    searches = [os.pathsep.join(os.get_exec_path()), *(argument[5:] for argument in command if argument.startswith("PATH="))]
    paths = [os.pathsep.join(os.path.join(identity.root, entry) for entry in search.split(os.pathsep)) for search in searches]
    runtimes = {execution["executable"], *(os.path.join(root, argument) for argument in command if "/" in argument for root in roots),
                *filter(None, (shutil.which(argument, path=path) for argument in command
                               if tdd_surface.INTERPRETER.fullmatch(argument) for path in paths))}
    packages = sorted({path for runtime in runtimes for path in Path(runtime).parent.parent.glob("lib/python*/*-packages")})
    dependencies = Path(identity.root) / "node_modules"
    if dependencies.is_dir():
        packages.append(dependencies)
    config = json.dumps([command, timeout, execution, executable_state.st_size, executable_state.st_mtime_ns,
                         [os.stat(path).st_mtime_ns for path in packages]], sort_keys=True).encode()
    producer = b"".join((Path(__file__).parent / name).read_bytes()
                       for name in ("tdd_workflow.py", "tdd_surface.py", "command_runner.py", "behavior_map.py"))
    return hashlib.sha256(b"\0".join(production) + support + config + producer).hexdigest()


def _execute_tree(identity: RepoIdentity, source_tree: str, candidate_tree: str, files: list[str],
                  command: list[str], surface: JsonObject, timeout: float, environment: dict[str, str] | None = None) -> JsonObject:
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
        runner_env = _environment()
        env = runner_env | (environment or {})
        env.update(PYTHONPATH=os.pathsep.join(filter(None, (identity.root, env.get("PYTHONPATH")))), PWD=identity.root,
                   TMPDIR=temporary, CODEX_WORKFLOW_STATE_ROOT=str(Path(temporary) / "state"))
        if surface.get("runner") in {"pytest", "unittest"}:
            env["PYTHONPATH"] += os.pathsep + str(Path(__file__).parent)
        actual = [_executable(identity, command[0], env), *command[1:]]
        binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/", "--bind", str(root), identity.root]
        binding.extend(arg for key, value in env.items() if runner_env.get(key) != value for arg in ("--setenv", key, value))
        dependencies = Path(identity.root) / "node_modules"
        if dependencies.is_dir():
            binding.extend(["--ro-bind", str(dependencies), str(dependencies)])
        runtime = Path(actual[0]).parent.parent
        if runtime.is_relative_to(identity.root) and (runtime / "pyvenv.cfg").is_file():
            binding.extend(["--ro-bind", str(runtime), str(runtime)])
        if surface.get("runner") == "pytest":
            position = actual.index("--") if "--" in actual else len(actual)
            actual[position:position] = ["--override-ini=addopts=", "-p", "tdd_surface"]
        if surface.get("runner") in {"pytest", "unittest"}:
            position = actual.index("--") if "--" in actual else len(actual)
            actual.insert(position, "-vv" if surface["runner"] == "pytest" else "-v")
        entry = tdd_surface.python_entry(actual)
        if surface.get("runner") == "unittest" and entry and entry[:2] == ("m", "unittest"):
            # Keep native lifecycle frames: filtered setup and cleanup reports can be identical.
            option = entry[2] - (2 if actual[entry[2] - 1] == "unittest" else 1)
            actual[option:entry[2]] = [actual[option].split("m", 1)[0] + "c", "import runpy,unittest.case,unittest.async_case,tdd_surface;tdd_surface.unittest_scope();unittest.case.__dict__.pop('__unittest',None);unittest.async_case.__dict__.pop('__unittest',None);runpy.run_module('unittest',run_name='__main__',alter_sys=True)"]
        process_started = time.perf_counter()
        try:
            raw, code, timed_out = _run([*binding, "--chdir", identity.root, "--", *actual], snapshot, timeout, env=runner_env)
        except OSError as exc:
            raw, code, timed_out = str(exc).encode(), 127, False
        output = raw.decode("utf-8", errors="replace")
        timing = {"totalSeconds": time.perf_counter() - started,
                  "executionSeconds": time.perf_counter() - process_started}
        cases = tdd_surface.case_results(surface, output, code)
        proof, error = None, "command timed out" if timed_out else ""
        if not timed_out:
            if code == 0:
                proof, error = _pass_proof(surface, output)
            else:
                proof, error = tdd_surface.evaluate_red(surface, output, cases)
        outcome = ("passed" if code == 0 else "failed") if proof else "incomplete"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome, proof=proof, error=error,
                          output=output, loadedRoot=identity.root, cases=cases,
                          executedCommand=actual, timing=timing)


def _from_file(case: str, path: str) -> bool:
    """Whether a runner-reported case belongs to the test file at `path`."""
    return case.startswith(path + "::") or f"({path[:-3].replace('/', '.')}." in case


# --- executable findings ------------------------------------------------------------------------

@lru_cache(maxsize=8)
def source_changes(identity: RepoIdentity, original: str, candidate: str) -> list[JsonObject]:
    """Raw zero-context changes; no language-specific claims about their meaning."""
    paths = _git(identity, "diff", "--no-renames", "--name-only", "-z", original, candidate).split(b"\0")
    changes = []
    for raw in paths:
        if not raw or is_test_path(path := os.fsdecode(raw)):
            continue
        patch = _git(identity, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--unified=0", original, candidate, "--", path).decode()
        parts = re.split(r"(?m)(?=^@@ )", patch)
        for body in parts[1:] or [""]:
            full = parts[0] + body
            identifier = "DIFF-" + hashlib.sha256(full.encode()).hexdigest()[:16]
            excerpt = " / ".join(line[:160] for line in body.splitlines() if line.startswith(("-", "+")))[:350]
            changes.append({"id": identifier, "path": path, "patch": full,
                            "label": f"{path} {body.splitlines()[0] if body else 'file metadata/content'}: {excerpt}"})
    return changes


def change_questions(identity: RepoIdentity, state: JsonObject, document: JsonObject | None, candidate: str) -> list[JsonObject]:
    """Derive source questions from ordinary comparison runs; never store question status."""
    changes = source_changes(identity, str(state["passStartOid"]), candidate)
    runs = (document or {}).get("runs", [])
    source_only = (document or {}).get("sourceDelta") or {}
    controls = [run for run in runs if run.get("change")]
    proofs = {run["candidateKey"]: {"comparison": run} for run in controls}
    refresh_proof(identity, list(proofs.values()), candidate=candidate)
    questions = []
    for change in changes:
        answer = None
        for run in reversed(controls):
            if run.get("change") != change["id"] or not proofs[run["candidateKey"]]["comparison"]["fresh"]:
                continue
            selected = run.get("selectedCases", [])
            before, after = run["arms"][0], run["arms"][-1]
            passed = [name for name in selected if behavior_map._passed((after.get("cases") or {}).get(name), after["outcome"])
                      and not behavior_map.unverified((after.get("cases") or {}).get(name))]
            sensitive = [name for name in passed if not run.get("controlError") and behavior_map._state((before.get("cases") or {}).get(name)) == "failed"]
            if sensitive or passed and run.get("judgment"):
                answer = {"cases": sensitive or passed, "runIndex": run["runIndex"], "sensitive": bool(sensitive),
                          "resolution": "measured sensitivity; lead coverage judgment" if sensitive else "lead judgment; no sensitivity proof",
                          "control": before["outcome"], "judgment": run.get("judgment") or "lead nominated this case for the affected behavior; sensitivity is measured, coverage and requiredness remain judgment"}
                break
        if answer is None and source_only.get("candidateTree") == candidate and change["id"] in source_only.get("changes", []):
            answer = {"cases": [], "sensitive": False, "resolution": "lead judgment; source-only", "judgment": source_only["judgment"]}
        questions.append({"id": change["id"], "source": change["label"], **(answer or {})})
    return questions


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


def _blob(identity: RepoIdentity, tree: str, path: str) -> str | None:
    try:
        return _git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
    except RuntimeError:
        return None


def _finding_probe(identity: RepoIdentity, probe: object, candidate: str, timeout: float) -> JsonObject:
    """Validate and freeze the existing finding payload for the shared comparison runner."""
    if not (isinstance(probe, dict) and all(isinstance(probe.get(key), str) and probe[key] for key in ("path", "content", "command"))):
        raise ValueError("probe needs path, content and command")
    path, command = os.path.normpath(probe["path"]), shlex.split(probe["command"])
    while command and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
        command.pop(0)
    existing = _blob(identity, candidate, path)
    if os.path.isabs(path) or path.startswith("..") or existing not in (None, probe["content"]) or (not is_test_path(path) and existing is not None):
        raise ValueError("probe source must be an in-repository test or focused script matching its recorded content")
    if not command or (tdd_surface.identify(command).get("runner") not in {"pytest", "unittest"} and Path(identity.root, path).resolve() not in (Path(identity.root, arg).resolve() for arg in command)):
        raise ValueError("probe command must name its probe file" if command else "probe command must name an executable")
    blob = _git(identity, "hash-object", "-w", "--stdin", stdin=probe["content"].encode()).decode().strip()
    tree = _indexed(identity, candidate, (("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"), None))
    files = [name for raw in _git(identity, "ls-tree", "-r", "--name-only", "-z", tree).split(b"\0")
             if raw and is_test_path(name := os.fsdecode(raw))]
    files = sorted(set([*files, path]))
    return {"command": probe["command"], "candidateTree": tree, "probeFiles": files, "support": [], "timeout": timeout}


def _finding_outcome(arm: JsonObject, path: str, command: str) -> str:
    if arm["timedOut"]:
        return "timed-out"
    if tdd_surface.native_runner(tdd_surface.identify(shlex.split(command)), arm["output"]) not in {"pytest", "unittest"}:
        return str(arm["outcome"])
    cases = [case for name, case in (arm.get("cases") or {}).items() if _from_file(name, os.path.normpath(path))]
    if arm["outcome"] == "passed" and cases:
        return "passed"
    return "failed" if arm["outcome"] == "failed" and any(case["outcome"] == "failed" for case in cases) else "incomplete"


def _run_probe(identity: RepoIdentity, probe: object, candidate: str, original: str | None,
               timeout: float = PROBE_TIMEOUT, cached: list[JsonObject] | None = None,
               frozen: JsonObject | None = None) -> JsonObject:
    try:
        frozen = frozen or _finding_probe(identity, probe, candidate, timeout)
        compared = _compare(identity, original or candidate, candidate, frozen, cached if cached is not None else [], False)
        result: JsonObject = {"kind": "probe", "comparison": compared,
                              "candidate": _finding_outcome(compared["arms"][-1], probe["path"], probe["command"])}
        if original is not None:
            result["original"] = _finding_outcome(compared["arms"][0], probe["path"], probe["command"])
        return result
    except (RuntimeError, ValueError) as exc:
        return {"kind": "probe", "error": f"probe: {exc}"}


def _selected_sources(identity: RepoIdentity, tree: str, probe: JsonObject) -> list[tuple[str, str, str | None]]:
    """Exact native case selectors, or explicitly named files of a focused direct command."""
    command = shlex.split(probe["command"])
    if command and Path(command[0]).name == "env":
        command.pop(0)
    while command and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
        command.pop(0)
    surface, files = tdd_surface.identify(command), probe["probeFiles"]
    if surface["runner"] not in tdd_surface.NATIVE_RUNNERS:
        if command and Path(command[0]).name in {"node", "nodejs"} and any(arg == "--test" or arg.startswith("--test=") for arg in command[1:]):
            raise WorkflowError("unparsed Node test collection is refused; use a focused direct operation with named checked results")
        return [(argument, path, None) for argument in command for path in files
                if os.path.normpath(argument) in {path, os.path.join(identity.root, path)}]
    targets, discover, ambiguous, selections = tdd_surface.proof_targets(surface, identity.root)
    if discover or ambiguous or selections or not targets:
        raise WorkflowError("Never collect a whole test file, class, directory or suite; a focused direct script is allowed.")
    selected = []
    for target in targets:
        if surface["runner"] == "pytest":
            path, *parts = target.split("::")
            matches = [(path, ".".join(part.split("[")[0] for part in parts))] if path in files else []
        else:
            matches = [(path, target[len(path[:-3].replace("/", ".")) + 1:]) for path in files if path.endswith(".py")
                       and target.startswith(path[:-3].replace("/", ".") + ".")]
            if not matches:
                matches = [(path, target[len(Path(path).stem) + 1:]) for path in files if path.endswith(".py")
                           and target.startswith(Path(path).stem + ".")]
        if len(matches) != 1:
            raise WorkflowError("Name an exact test case; its source selection is ambiguous or outside the recorded probes")
        path, qualified = matches[0]
        selected.append((target, path, qualified))
    return selected


def _maintained_tests(identity: RepoIdentity, batches: list[JsonObject], maintained: list[JsonObject] | None,
                      before: str | None, after: str, cached: list[JsonObject]) -> str | None:
    """Check delivered named cases and retained failure detectors; frozen replay stays separate.

    Current selections name the surviving maintained cases. Required future coverage and
    assertion equivalence beyond the recorded source witnesses remain review obligations.
    """
    baselines = [before] if before else list(dict.fromkeys(run.get("probeTree", run["candidateTree"]) for run in batches))
    changed = {os.fsdecode(path) for baseline in baselines for path in _git(identity, "diff", "--no-renames", "--name-only", "-z", baseline, after).split(b"\0")
               if path and is_test_path(os.fsdecode(path))}
    if not changed:
        return None
    present = {os.fsdecode(path) for path in _git(identity, "ls-tree", "-r", "--name-only", "-z", after).split(b"\0") if path}
    owners = {}
    for item in maintained or []:
        owners.setdefault((item.get("comparison") or {}).get("runIndex"), []).append(item)
    active = [run for run in batches if maintained is None or run["runIndex"] in owners]
    if any(path in present and not any(path in run["probeFiles"] for run in active) for path in changed):
        return "record the affected surviving named cases before reducing their maintained support"
    for run in active:
        selected = _selected_sources(identity, run.get("probeTree", run["candidateTree"]), run)
        surviving = [(target, path, name) for target, path, name in selected if path in present]
        if selected and not surviving:
            continue  # the whole one-off entry was removed; immutable cases still replay below
        if surviving and len(surviving) != len(selected) and any(name is None for _, _, name in selected):
            return "record a focused command for surviving cases after removing a direct-command entry/support"
        dropped = {target for target, path, name in selected if path not in present}
        live = {**run, "command": shlex.join([arg for arg in shlex.split(run["command"]) if arg not in dropped]),
                "probeTree": after, "probeFiles": [path for path in run["probeFiles"] if path in present]}
        baseline = before or run.get("probeTree", run["candidateTree"])
        expected = _compare(identity, baseline, baseline, {**run, "command": live["command"]}, cached, False)
        required = {name for item in owners.get(run["runIndex"], []) for name in behavior_map.selected_cases(item, expected)}
        required = required or set(expected["arms"][-1].get("cases") or {})
        delivery = _compare(identity, after, after, live, cached, False)["arms"][-1]
        if not required or any(not behavior_map._passed((delivery.get("cases") or {}).get(name), delivery["outcome"])
                               or behavior_map.unverified((delivery.get("cases") or {}).get(name)) for name in required):
            return "affected surviving maintained named cases are missing or unproved: " + live["command"]
        if delivery["outcome"] != "passed":
            return "affected surviving maintained tests fail with current support: " + live["command"]
        # A weakened test must not turn an already observed detector failure into green.
        witnesses = {arm["sourceTree"] for prior in list(cached) if prior["command"] == run["command"]
                     and prior.get("probeTree", prior["candidateTree"]) == run.get("probeTree", run["candidateTree"])
                     for arm in prior["arms"] if arm["outcome"] == "failed" or any(name in (prior["arms"][-1].get("cases") or {}) and behavior_map._state(case) != behavior_map._state(prior["arms"][-1]["cases"][name]) for name, case in (arm.get("cases") or {}).items())}
        for source in witnesses:
            reference = _compare(identity, source, source, run, cached, False)["arms"][-1]
            reduced = _compare(identity, source, source, live, cached, False)["arms"][-1]
            for name in required:
                if behavior_map._state((reference.get("cases") or {}).get(name)) != behavior_map._state((reduced.get("cases") or {}).get(name)):
                    return "maintained cleanup removed or changed a recorded named detector result: " + name
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
                frozen: JsonObject | None = None) -> JsonObject:
    """Compare a concrete replacement and retain its correctness and cost counterexamples."""
    if not isinstance(change, str) or not change.strip():
        return {"kind": "change", "error": "change must be a unified diff"}
    if not batches:
        return {"kind": "change", "error": "no recorded comparison batch to rerun"}
    cached = list(batches) if cached is None else cached
    measured = _run_probe(identity, probe, candidate, None, timeout, cached, frozen) if probe is not None else None
    repaired = measured is not None and measured.get("candidate") == "passed"
    if repaired:
        before, after = None, candidate
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
    if probe is not None and not repaired:
        if measured is None or measured.get("candidate") != "failed":
            return {"kind": "change", "error": "cost probe did not establish a candidate failure"}
        try:
            cost = _compare(identity, candidate, after, measured["comparison"], cached, False)
        except (RuntimeError, ValueError) as exc:
            return {"kind": "change", "error": f"cost probe: {exc}"}
        if not cost["valid"] or _finding_outcome(cost["arms"][-1], probe["path"], probe["command"]) != "passed":
            return {"kind": "change", "error": "replacement did not pass the same cost probe", "comparison": cost}
    if not repaired and (refusal := _shape_refusal(identity, before, after, cost is not None)):
        return {"kind": "change", "error": refusal}
    if error := _maintained_tests(identity, batches, maintained, before, after, cached):
        return {"kind": "change", "error": error}
    for run in batches:
        arm = _compare(identity, after, after, run, cached, False)["arms"][-1]
        outcome = "timed-out" if arm["timedOut"] else arm["outcome"]
        if outcome != "passed":
            if outcome == "failed" and after != candidate:
                control = _compare(identity, candidate, candidate, run, cached, False)["arms"][-1]
                if control["outcome"] != "passed":
                    return {"kind": "change", "error": "current candidate does not pass the retained batch: " + run["command"]}
            reason = (f"recorded batch `{run['command']}` ran past {run['timeout']:g} s: replace it with direct probes of the changed code"
                      if arm["timedOut"] else f"recorded batch `{run['command']}` {outcome} with the change applied")
            return ({"kind": "change", "applied": after == candidate, "candidate": outcome,
                     "timedOut" if arm["timedOut"] else "failure": reason} if outcome in {"failed", "timed-out"}
                    else {"kind": "change", "error": reason})
    return measured if repaired else {"kind": "change", "applied": after == candidate, "candidate": "passed", **({"comparison": cost} if cost else {})}


def binds(execution: JsonObject) -> bool:
    """The acceptance order: a regression (a probe the original passes that fails or runs past its limit on the
    candidate) binds and is repaired; a net reduction or cost-proved replacement every retained batch passes binds;
    anything else, a probe the original also fails included, asks for unrecorded behaviour: a report line."""
    if "error" in execution:
        return False
    if execution["kind"] == "change":
        return execution.get("candidate") == "passed"
    return execution.get("candidate") in {"failed", "timed-out"} and execution.get("original") == "passed"


def _retained(identity: RepoIdentity, state: JsonObject) -> list[tuple[JsonObject, JsonObject]]:
    """Each observation of every review or final finding that binds by the current rule: a finding settles only with
    all of them; one recorded under an earlier rule that no longer binds is re-executed as a report line."""
    retained = []
    for entry in state.get("findingStates") or []:
        for ref in [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"]}, *entry.get("observations", [])]:
            ref = {"evidenceId": ref["evidenceId"], "id": ref["id"]}
            intake = evidence_document(identity, ref["evidenceId"]) or {}
            finding = next((item for item in intake.get("findings", []) if item["id"] == ref["id"]), None)
            if intake.get("stage") in {"code-review", "final"} and finding and "execution" in finding and binds(finding["execution"]):
                retained.append((entry, {**finding, "ref": ref, "sourceTree": intake["candidateTree"]}))
    return retained


def execute_findings(identity: RepoIdentity, state: JsonObject, findings: list[JsonObject]) -> JsonObject:
    """Execute new findings and reevaluate all retained bindings, including resolved findings, on the measured candidate."""
    from .workflow_state import _finding_unresolved
    candidate = _active_candidate_tree(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    current = evidence_document(identity, state.get("tddEvidence")) or {}
    used = {(item.get("comparison") or {}).get("runIndex") for item in current.get("behaviorMap") or []}
    maintained = current.get("behaviorMap") or []
    used.update(entry["runIndex"] for entry in _evaluation(identity, state, current).values())
    batches = [run for run in current.get("runs") or [] if run["runIndex"] in used]
    cached = list(current.get("runs") or [])
    retained = _retained(identity, state)
    references = {state.get("codeReviewEvidence"), (state.get("finalReview") or {}).get("intakeEvidence"),
                  *(entry.get("dispositionEvidenceId") for entry, _ in retained),
                  *(finding["ref"]["evidenceId"] for _, finding in retained)}
    keys = {tuple(arm.get("key") for arm in run["arms"]) for run in cached}
    tdd_keys = set(keys)
    for reference in references - {None}:
        for prior in (evidence_document(identity, reference) or {}).get("runs", []):
            key = tuple(arm.get("key") for arm in prior["arms"])
            if key not in keys:
                cached.append(prior)
                keys.add(key)
    def run(finding: JsonObject, first: bool) -> JsonObject:
        timeout = next((batch["timeout"] for batch in reversed(current.get("runs") or [])
                        if batch["command"] == (finding.get("probe") or {}).get("command")),
                       max((batch["timeout"] for batch in batches), default=PROBE_TIMEOUT) if "change" in finding and "probe" in finding else PROBE_TIMEOUT)
        frozen = None
        if not first and "probe" in finding:
            frozen = finding["execution"].get("comparison") or _finding_probe(identity, finding["probe"], finding["sourceTree"], timeout)
        return (_run_change(identity, batches, finding["change"], candidate, finding.get("probe"), timeout, cached, maintained, frozen) if "change" in finding
                else _run_probe(identity, finding["probe"], candidate, original if first else None, timeout, cached, frozen) if "probe" in finding
                else {"kind": "claim", "error": "no probe or change: a report line"})
    executed = []
    for finding in findings:
        execution = run(finding, True)
        executed.append({**finding, "execution": execution, "material": binds(execution)})
    again = []
    for entry, finding in retained:
        execution = run(finding, False)
        again.append({**finding["ref"], "execution": {**execution, **{key: finding["execution"][key]
                                                                     for key in ("original",) if key in finding["execution"]}}})
    again += [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"], "execution": {"kind": "claim"}}
              for entry in state.get("findingStates") or []  # an open finding with no executed evidence is a report line
              if entry.get("stage") in {"code-review", "final"} and _finding_unresolved(entry)
              and all(entry is not other for other, _ in retained)]
    return {"measuredTree": candidate, "measuredEvidence": state.get("tddEvidence"), "findings": executed, "reexecuted": again,
            "runs": list({tuple(arm.get("key") for arm in run["arms"]): run for run in cached
                          if tuple(arm.get("key") for arm in run["arms"]) not in tdd_keys}.values())}


def _evaluation(identity: RepoIdentity, state: JsonObject, current: JsonObject | None) -> JsonObject:
    if not current or "evaluation" in current:
        return {key: dict(entry) for key, entry in (current or {}).get("evaluation", {}).items()}
    required = {item["id"] for item in behavior_map.recorded_map(None, evidence_document(identity, state.get("preflightEvidence"))) or []}
    retained = {}
    references = (reference for event in history(identity, state["workflowId"])["events"] if event["kind"] == "tdd-comparison"
                  for reference in event["evidenceIds"])
    for reference in references:
        document = evidence_document(identity, reference) or {}
        behavior_map.retain_evaluation(document.get("behaviorMap", []), required, retained)
        if retained.keys() == required:
            break
    return retained


def _comparison_input(identity: RepoIdentity, probe: JsonObject) -> tuple[list[str], JsonObject, dict[str, str]]:
    command = shlex.split(probe["command"])
    environment = {}
    while command and re.match(r"[A-Za-z_][A-Za-z_0-9]*=", command[0]):
        name, value = command.pop(0).split("=", 1)
        environment[name] = value
    execution = {"executable": _executable(identity, command[0], _environment() | environment),
                 "support": probe["support"],
                 "environment": hashlib.sha256(json.dumps(sorted((_environment() | environment).items())).encode()).hexdigest()}
    return command, execution, environment


def _compare(identity: RepoIdentity, original: str, candidate: str, probe: JsonObject,
             runs: list[JsonObject], conformance: bool) -> JsonObject:
    """Compare immutable probes on both sources; selected and retained bindings share the same cache."""
    files, timeout = probe["probeFiles"], probe["timeout"]
    command, execution, environment = _comparison_input(identity, probe)
    tree = probe.get("probeTree", probe["candidateTree"])
    _selected_sources(identity, tree, probe)
    surface = tdd_surface.identify(command)
    sources = [original, candidate]
    keys = [_execution_key(identity, source, tree, files, command, timeout, execution) for source in sources]
    for prior in reversed(runs):
        if (prior["valid"] and prior["candidateTree"] == candidate and all(prior.get(key) == probe.get(key) for key in ("change", "judgment", "controlError", "selectedCases")) and [arm.get("key") for arm in prior["arms"]] == keys
                and (conformance or prior["arms"][0]["outcome"] in {"passed", "failed"})):
            return prior
    cache = {arm.get("key"): arm for run in runs for arm in run.get("arms", [])
             if arm.get("outcome") in {"passed", "failed"}}
    arms: list[JsonObject] = []
    for source, key in zip(sources, keys):
        if key not in cache:
            cache[key] = {**_execute_tree(identity, source, tree, files, command, surface, timeout, environment), "key": key}
        arms.append({**cache[key], "requestedTree": source})
    outcomes = [arm["outcome"] for arm in arms]
    valid = outcomes[-1] == "passed" and not arms[0]["timedOut"] and (conformance or outcomes[0] in {"passed", "failed"})
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    run = {**{key: probe[key] for key in ("command", "probeFiles", "support", "timeout", "behaviorId", "behaviorIds", "change", "judgment", "controlError", "selectedCases") if key in probe},
           "runIndex": len(runs), "candidateTree": candidate, "probeTree": tree, "originalTree": original, "candidateKey": keys[-1],
           "comparison": "incomplete" if not valid else "preserved" if preserved else "changed", "arms": arms, "valid": valid, "execution": execution,
           "exitCode": 0 if valid else 1, "timedOut": any(arm["timedOut"] for arm in arms)}
    runs.append(run)
    return run


@interruptible()
def _run_tdd(values: list[str]) -> int:
    dash = values.index("--") if "--" in values else len(values)
    parser = argparse.ArgumentParser(prog="workflow tdd", description="Compare a probe on recorded sources; omit the command to reuse its recorded batch")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--behavior-id", action="append", default=[])
    parser.add_argument("--change", action="append", default=[], help="current DIFF id to check with the same named batch")
    parser.add_argument("--judgment", help="source/observed basis when sensitivity cannot establish relevance; explicitly trusted, reviewed judgment")
    parser.add_argument("--support", action="append")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(values[:dash])
    command = values[dash + 1:]
    if args.judgment is not None:
        args.judgment = args.judgment.strip()
        if not args.judgment:
            raise ValueError("judgment needs the source and observed basis; it is not proof")
    identity = resolve_repo_identity(args.repo)
    state = bound_state(identity, args.slug)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    if (workflow_id := instance_id(state)) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    slug = str(state["slug"])
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("tdd requires a recorded probe list")
    mapped = [behavior_map.item(items, identifier) for identifier in dict.fromkeys(args.behavior_id)]
    previous = [item.get("comparison") or {} for item in mapped]
    recorded = previous[0] if previous else {}
    reuse = not command and bool(recorded.get("command")) and all(
            all(run.get(key) == recorded.get(key) for key in ("command", "support", "timeout")) for run in previous)
    if reuse:
        command = shlex.split(recorded["command"])
        args.support = recorded["support"] if args.support is None else args.support
        args.timeout = recorded["timeout"] if args.timeout is None else args.timeout
    args.support = args.support or []
    reuse = reuse and args.support == recorded["support"]
    args.timeout = 900.0 if args.timeout is None else args.timeout
    candidate = _active_candidate_tree(identity)
    changes = {entry["id"]: entry for entry in source_changes(identity, str(state["passStartOid"]), candidate)}
    missing = set(args.change) - changes.keys()
    if missing:
        old_tree = ((current or {}).get("sourceDelta") or {}).get("candidateTree") or next((prior["candidateTree"] for prior in reversed((current or {}).get("runs", []))), candidate)
        obsolete = {entry["id"] for entry in source_changes(identity, str(state["passStartOid"]), old_tree)}
        if missing - obsolete:
            raise WorkflowError("change id is not in the current diff; read current open questions")
        args.change = [identifier for identifier in args.change if identifier in changes]
    if not items and (not changes or args.judgment and set(args.change) == changes.keys()):
        note = {"candidateTree": candidate, "changes": args.change, "judgment": args.judgment} if changes else None
        if (not changes and state.get("tdd") == "passed") or note is not None and (current or {}).get("sourceDelta") == note:
            _emit_json(operation_receipt(state, identity, kind="tdd", summaryId=state.get("tddEvidence"), reused=True), sort_keys=False)
            return 0
        document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                    "runs": list((current or {}).get("runs", [])), **({"sourceDelta": note} if note else {})}
        state, evidence = commit_tdd(identity, slug, workflow_id, document, expected_evidence_id=state.get("tddEvidence"))
        _emit_json(operation_receipt(state, identity, kind="tdd", summaryId=evidence), sort_keys=False)
        return 0
    if not mapped:
        raise WorkflowError("select the responsible behavior id; an empty map may record an explicit judgment naming all current changes")
    if not command or args.timeout <= 0:
        raise ValueError("supply a probe command after -- or select one recorded batch; timeout must be positive")
    selection = {"behaviorId": mapped[0]["id"]} if len(mapped) == 1 else {"behaviorIds": [item["id"] for item in mapped]}
    surface = tdd_surface.identify(command)
    if not reuse and (refusal := tdd_surface.repository_resolution(surface, identity.root)):
        raise WorkflowError(refusal)
    files = recorded["probeFiles"] if reuse else _probe_files(surface, Path(identity.root), args.support)
    before = tree_manifest(identity)
    candidate = _active_candidate_tree(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    runs = list((current or {}).get("runs", []))
    evaluation = _evaluation(identity, state, current)
    probe = {"candidateTree": candidate, "probeFiles": files, "command": shlex.join(command), "support": args.support,
             "probeTree": recorded.get("probeTree", recorded["candidateTree"]) if reuse else candidate,
             "timeout": args.timeout, **selection}
    run = _compare(identity, original, candidate, probe,
                   runs, any(item.get("kind") == "conformance" for item in mapped))
    probe["selectedCases"] = sorted({name for item in mapped for name in behavior_map.selected_cases(item, run)})
    reused_changes = all(any(prior.get("change") == identifier and (prior["valid"] or args.judgment and prior["arms"][-1]["outcome"] == "passed")
                             and prior["candidateKey"] == run["candidateKey"] and prior.get("judgment") == args.judgment
                             and prior.get("selectedCases") == probe["selectedCases"]
                             for prior in runs) for identifier in args.change)
    if (reused_changes and current and "evaluation" in current and all(behavior_map.producer_proved(item) and item["comparison"]["candidateKey"] == run["candidateKey"] for item in mapped)
            and {proof["runIndex"] for proof in previous if proof} == {run["runIndex"]}):
        receipt = operation_receipt(state, identity, kind="tdd", **selection, **behavior_map.comparison_view(previous[0]),
                                    summaryId=state["tddEvidence"], reused=True)
        _emit_json(receipt, sort_keys=False)
        return 0
    original_valid = all(arm["outcome"] in {"passed", "failed"} for arm in run["arms"][:-1])
    for item in mapped:
        item["comparison"] = {**run, "obligation": behavior_map.obligation(item),
                              "valid": run["valid"] and (original_valid or item.get("kind") == "conformance")}
    replayed = {index: _compare(identity, original, candidate, runs[index], runs,
                               any(entry["kind"] == "conformance" and entry["runIndex"] == index for entry in evaluation.values()))["runIndex"]
                for index in dict.fromkeys(entry["runIndex"] for entry in evaluation.values())}
    for entry in evaluation.values():
        entry["runIndex"] = replayed[entry["runIndex"]]
    for item in items:
        fixed = evaluation.get(item["id"])
        if item not in mapped and fixed and behavior_map.obligation(item) == behavior_map.obligation(fixed) and item.get("boundaryInputs") == fixed.get("boundaryInputs"):
            item["comparison"] = {**runs[fixed["runIndex"]], "obligation": behavior_map.obligation(item)}
    if all(behavior_map.producer_proved(item) for item in mapped):
        for identifier in dict.fromkeys(args.change):
            try:
                reverted = _indexed(identity, candidate, (("apply", "--cached", "--reverse", "--unidiff-zero", "-"), changes[identifier]["patch"].encode()))
                _compare(identity, reverted, candidate, {**probe, "change": identifier, **({"judgment": args.judgment} if args.judgment else {})}, runs, False)
            except RuntimeError as exc:
                if not args.judgment:
                    raise WorkflowError("cannot isolate this change; inspect its interacting edits and record an explicit judgment: " + str(exc)) from exc
                _compare(identity, original, candidate, {**probe, "change": identifier, "judgment": args.judgment, "controlError": str(exc)}, runs, False)
    document = {"workflowId": workflow_id, "slug": slug, "kind": "comparison", "behaviorMap": items,
                "evaluation": evaluation, "runs": runs, "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, slug, workflow_id, document,
                                expected_evidence_id=state.get("tddEvidence"), tree_before=before)
    receipt = operation_receipt(state, identity, kind="tdd", **selection, **behavior_map.comparison_view(run),
                                summaryId=evidence)
    _emit_json(receipt, sort_keys=False)
    return 0 if run["valid"] else 2


def map_update(identity: RepoIdentity, state: JsonObject, value: JsonObject) -> JsonObject:
    if set(value) not in ({"items"}, {"added"}):
        raise ValueError("tdd-map takes items to update by id; statuses and dispositions are runner-owned")
    previous, current = current_map(identity, state)
    recorded = {entry["id"]: entry for entry in previous or []}
    merged = value.get("items", value.get("added"))
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
    items = list({item["id"]: item for item in [*(previous or []), *updates]}.values())
    for entry in updates:
        prior = recorded.get(entry["id"])
        if prior is not None and "boundaryInputs" in prior and "boundaryInputs" not in entry:
            entry["boundaryInputs"] = []  # removing every witness cannot fall back to unrelated batch cases
        if prior and "comparison" in prior:  # a changed kind or obligation keeps its latest run and reads stale until rerun
            entry["comparison"] = {**prior["comparison"], "obligation": prior["comparison"].get("obligation", behavior_map.obligation(prior))}
    if items == previous:
        return operation_receipt(state, identity, summaryId=state.get("tddEvidence") or state["preflightEvidence"],
                                 pending=behavior_map.unresolved(items, _readiness_lines(identity, state, items)["open"]), reused=True)
    try:
        tree_manifest(identity)
    except RuntimeError as exc:
        raise WorkflowError(str(exc)) from exc
    document = {"workflowId": state["workflowId"], "slug": state["slug"], "kind": "comparison",
                "behaviorMap": items, "runs": (current or {}).get("runs", []), "updatedAt": utc_timestamp()}
    state, evidence = commit_tdd(identity, str(state["slug"]), str(state["workflowId"]), document,
                                expected_evidence_id=state.get("tddEvidence"), review_changed=True)
    return operation_receipt(state, identity, summaryId=evidence, pending=behavior_map.unresolved(items, _readiness_lines(identity, state, items)["open"]))


def _pass_proof(surface: JsonObject, output: str) -> tuple[JsonObject | None, str]:
    """A successful exit must report an operation; selected native tests must all execute."""
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
        unexecuted = sum(int(count) for count in re.findall(r"(\d+) (?:skipped|xfailed|xpassed)\b", summaries[-1])) if summaries else 0
        if unexecuted:
            return None, "selected pytest probes did not all execute passing assertions"
        passed = re.search(r"(?<!\d)(\d+) passed\b", summaries[-1]) if summaries else None
        executed = int(passed.group(1)) if passed else 0
    if executed < 1:
        return None, f"{runner} did not report an executed passing test"
    return {"quality": "tests-passed", "runner": runner, "testsExecuted": executed}, ""
