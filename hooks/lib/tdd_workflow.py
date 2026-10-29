"""Execute the same retained probe against recorded production source trees."""
from __future__ import annotations

import argparse
import ast
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

from . import behavior_map, changed_code, tdd_surface
from .command_runner import emit_json as _emit_json, interruptible, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import _active_candidate_tree, _git, is_test_path, tree_manifest, utc_timestamp
from .workflow_state import (
    NO_INSTANCE_ID, WorkflowError, bound_state,
    commit_tdd, evidence_document, instance_id, operation_receipt,
)

JsonObject = dict[str, object]
PROBE_TIMEOUT = 10.0  # a probe is a direct call: anything slower is not one


def _evidence_pair(identity: RepoIdentity, state: JsonObject) -> tuple[JsonObject | None, JsonObject | None]:
    current = evidence_document(identity, state.get("tddEvidence"))
    preflight = None if current and "behaviorMap" in current else evidence_document(identity, state.get("preflightEvidence"))
    return current, preflight


def current_map(identity: RepoIdentity, state: JsonObject) -> tuple[list[JsonObject] | None, JsonObject | None]:
    current, preflight = _evidence_pair(identity, state)
    items = behavior_map.recorded_map(current, preflight)
    if items:
        refresh_proof(identity, items)
    return items, current


def refresh_proof(identity: RepoIdentity, items: list[JsonObject]) -> None:
    """Re-judge each recorded comparison's freshness against the current production tree. The proof is the
    recorded run: its probe tree and files, never the checkout's, so a probe removed after it ran still proves."""
    execution_keys: dict[str, str | None] = {}
    candidate: str | None = None
    for item in items:
        proof = item.get("comparison")
        if proof:
            command = shlex.split(proof["command"])
            try:
                candidate = candidate or _active_candidate_tree(identity)
                binding = json.dumps([command, proof["timeout"], proof.get("execution"), proof.get("candidateTree"),
                                      proof.get("probeFiles")], sort_keys=True)
                if binding not in execution_keys:
                    execution_keys[binding] = (_execution_key(identity, candidate, proof["candidateTree"], proof["probeFiles"],
                        command, proof["timeout"], proof["execution"]) if proof.get("execution") else None)
                proof["fresh"] = bool(proof.get("execution")) and proof.get("candidateKey") == execution_keys[binding]
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
    owned = {"SHLVL", "_", "PWD", "OLDPWD", "PYTHONHOME", "TMPDIR", "CODEX_WORKFLOW_STATE_ROOT", "PYTHONDONTWRITEBYTECODE",
             "PYTEST_ADDOPTS", "WORKFLOW_CONDITION_PROBES", "WORKFLOW_CONDITION_RESULTS"}
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
    # Installed packages of each runtime the command names: any path (from the root or a named directory), any Python on a PATH.
    roots = [identity.root, *(argument for argument in command if os.path.isabs(argument) and os.path.isdir(argument))]
    searches = [os.pathsep.join(os.get_exec_path()), *(argument[5:] for argument in command if argument.startswith("PATH="))]
    paths = [os.pathsep.join(os.path.join(identity.root, entry) for entry in search.split(os.pathsep)) for search in searches]
    runtimes = {execution["executable"], *(os.path.join(root, argument) for argument in command if "/" in argument for root in roots),
                *filter(None, (shutil.which(argument, path=path) for argument in command
                               if tdd_surface.INTERPRETER.fullmatch(argument) for path in paths))}
    packages = sorted({path for runtime in runtimes for path in Path(runtime).parent.parent.glob("lib/python*/*-packages")})
    config = json.dumps([command, timeout, execution, executable_state.st_size, executable_state.st_mtime_ns,
                         [os.stat(path).st_mtime_ns for path in packages]], sort_keys=True).encode()
    producer = b"".join((Path(__file__).parent / name).read_bytes()
                       for name in ("tdd_workflow.py", "tdd_surface.py", "command_runner.py", "changed_code.py",
                                    "condition_trace/sitecustomize.py"))
    return hashlib.sha256(b"\0".join(production) + support + config + producer).hexdigest()


def _execute_tree(identity: RepoIdentity, source_tree: str, candidate_tree: str, files: list[str],
                  command: list[str], surface: JsonObject, timeout: float, named: dict[str, str],
                  decisions: list[JsonObject]) -> JsonObject:
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
        traced = Path(temporary) / "conditions.jsonl"
        if decisions:  # the original arm records which removed conditions its cases decide
            (Path(temporary) / "conditions.json").write_text(json.dumps(decisions), encoding="utf-8")
            trace = shutil.copytree(Path(__file__).with_name("condition_trace"), Path(temporary) / "trace")
            env.update(PYTHONPATH=os.pathsep.join([str(trace), env["PYTHONPATH"]]),
                       WORKFLOW_CONDITION_PROBES=str(Path(temporary) / "conditions.json"),
                       WORKFLOW_CONDITION_RESULTS=str(traced))
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
        evaluations: dict[str, list[JsonObject]] | None = None
        if traced.is_file():
            evaluations = {}
            for line in traced.read_text(encoding="utf-8").splitlines():
                for index, found in json.loads(line).items():
                    evaluations.setdefault(index, []).extend(found)
        timing = {"totalSeconds": time.perf_counter() - started,
                  "executionSeconds": time.perf_counter() - process_started}
        cases = tdd_surface.case_results(surface, output)
        proof, error = None, "command timed out" if timed_out else ""
        if not timed_out:
            if code == 0:
                proof, error = _pass_proof(surface, output, named)
            else:
                proof, error = tdd_surface.evaluate_red(surface, output, cases)
        outcome = ("passed" if code == 0 else "failed") if proof else "incomplete"
        return _run_entry(raw, code, timed_out, sourceTree=source_tree, outcome=outcome, proof=proof, error=error,
                          output=output, loadedRoot=identity.root, cases=cases,
                          executedCommand=actual, timing=timing,
                          **({"conditions": changed_code.judge(decisions, evaluations)} if decisions else {}))


def _from_file(case: str, path: str) -> bool:
    """Whether a runner-reported case belongs to the test file at `path`."""
    return case.startswith(path + "::") or f"({path[:-3].replace('/', '.')}." in case


def _named_command(surface: JsonObject, command: list[str], named: dict[str, str], root: str) -> tuple[list[str], dict[str, str]]:
    """A pytest or unittest batch extended by each test, in a test file named for a changed function, whose own
    source names that function; another command, or one selecting by discovery or filters, runs as given."""
    if surface.get("runner") not in {"pytest", "unittest"}:
        return command, {}
    targets, discover, ambiguous, selections = tdd_surface.proof_targets(surface, root)
    if discover or ambiguous or selections or not targets:
        return command, {}
    whole = {os.path.normpath(target) for target in targets} | {target.replace(".", "/") + ".py" for target in targets}
    added = {}
    for path, function in ((path, function) for path, function in named.items() if path.endswith(".py") and path not in whole):
        try:
            tree = ast.parse(Path(root, path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise WorkflowError(f"`{path}`, named for changed `{function}`, does not parse: {exc}") from exc
        scopes = [(None, node) for node in tree.body] + [(cls.name, node) for cls in tree.body
                                                         if isinstance(cls, ast.ClassDef) for node in cls.body]
        for owner, node in scopes:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
                    and (owner or surface["runner"] == "pytest")
                    and any(getattr(part, "id", None) == function or getattr(part, "attr", None) == function
                            for part in ast.walk(node))):
                selector = ("::".join(filter(None, (path, owner, node.name))) if surface["runner"] == "pytest"
                            else ".".join((path[:-3].replace("/", "."), owner, node.name)))
                added[selector] = function
    return [*command, *(selector for selector in added if selector not in targets)], added


def _renamed_tests(identity: RepoIdentity, original: str, candidate: str, files: list[str], surface: JsonObject,
                   timeout: float) -> list[str] | None:
    """The original pytest ids of each modified existing test file that the candidate loses while their test
    function remains, both collected on the candidate's production code; None when a collection fails."""
    changed = changed_code.changed_tests(identity, original, candidate)
    if not changed:
        return []
    command = [*shlex.split(str(surface["invocation"])), "--collect-only", "-q", "--rootdir=.", "--override-ini=addopts=", *changed]
    kept = [os.fsdecode(path) for path in
            _git(identity, "--literal-pathspecs", "ls-tree", "-r", "--name-only", "-z", original, "--", *files).split(b"\0") if path]
    collected = []
    for tests, paths in ((original, kept), (candidate, files)):
        arm = _execute_tree(identity, candidate, tests, paths, command, {"runner": "exact"}, timeout, {}, [])
        if arm["exitCode"] not in (0, 5):
            return None
        collected.append([line for line in arm["output"].splitlines() if line.startswith(tuple(path + "::" for path in changed))])
    present = {case.split("[", 1)[0] for case in collected[1]}  # collected, so inherited tests count
    return [case for case in collected[0] if case not in collected[1] and (case.split("[", 1)[0] in present
            or case.split("::", 1)[1].split("[")[0].replace("::", ".") in changed[case.split("::", 1)[0]])]


def _source_delta(identity: RepoIdentity, original: str, candidate: str) -> JsonObject:
    changed = [os.fsdecode(name) for name in _git(identity, "diff", "--name-only", "-z", original, candidate).split(b"\0")
               if name and not is_test_path(os.fsdecode(name))]
    command = ["--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--unified=3", original, candidate, "--", *changed]
    patch = _git(identity, *command).decode("utf-8", errors="replace") if changed else ""
    return {"patch": patch[:8000], "truncated": len(patch) > 8000,
            "command": shlex.join(["git", "-C", identity.root, *command]) if changed else None}


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


def _blob(identity: RepoIdentity, tree: str, path: str) -> str | None:
    try:
        return _git(identity, "cat-file", "blob", f"{tree}:{path}").decode("utf-8", errors="replace")
    except RuntimeError:
        return None


def _probe_outcome(identity: RepoIdentity, source: str, tree: str, path: str, command: list[str]) -> str:
    """passed or failed only when the probe file's own cases ran; a failure is the runner's red proof; past the limit, timed-out."""
    surface = tdd_surface.identify(command)
    files = [name for raw in _git(identity, "ls-tree", "-r", "--name-only", "-z", tree).split(b"\0")
             if raw and is_test_path(name := os.fsdecode(raw))]
    arm = _execute_tree(identity, source, tree, files, command, surface, PROBE_TIMEOUT, {}, [])
    if arm["timedOut"]:
        return "timed-out"
    cases = [case for name, case in (arm.get("cases") or {}).items() if _from_file(name, path)]
    if arm["outcome"] == "passed" and cases:
        return "passed"
    failed = arm["outcome"] == "failed" and any(case["outcome"] == "failed" for case in cases)
    return "failed" if failed else "incomplete"


def _run_probe(identity: RepoIdentity, probe: object, candidate: str, original: str | None) -> JsonObject:
    """A standalone test file added to the candidate's tests, run on the candidate and, first time, the original.
    Producer text git or the runner cannot take is an error, a report line, never an aborted intake."""
    if not (isinstance(probe, dict) and all(isinstance(probe.get(key), str) and probe[key] for key in ("path", "content", "command"))):
        return {"kind": "probe", "error": "probe needs path, content and command"}
    try:
        path, command = os.path.normpath(probe["path"]), shlex.split(probe["command"])
        if os.path.isabs(path) or path.startswith("..") or not is_test_path(path) or _blob(identity, candidate, path) not in (None, probe["content"]):
            return {"kind": "probe", "error": "probe path must be a new test file"}
        if tdd_surface.identify(command).get("runner") not in {"pytest", "unittest"}:
            return {"kind": "probe", "error": "probe command must run pytest or unittest"}
        blob = _git(identity, "hash-object", "-w", "--stdin", stdin=probe["content"].encode()).decode().strip()
        tree = _indexed(identity, candidate, (("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"), None))
        result: JsonObject = {"kind": "probe", "candidate": _probe_outcome(identity, candidate, tree, path, command)}
        if original is not None:
            result["original"] = _probe_outcome(identity, original, tree, path, command)
        if "timed-out" in result.values():  # visible: binding only when the original passed within the limit
            result["timedOut"] = f"`{path}` ran past {PROBE_TIMEOUT:g} s: not a direct probe; rewrite it to call the changed code"
    except (RuntimeError, ValueError) as exc:
        return {"kind": "probe", "error": f"probe: {exc}"}
    return result


def _shape_refusal(identity: RepoIdentity, before: str, after: str) -> str | None:
    """Why the change from `before` to `after` is not bloat removal: it must cut lines and never touch tests."""
    counts = _git(identity, "diff", "--numstat", "--no-renames", "-z", before, after).decode("utf-8", "replace").split("\0")
    rows = [row.split("\t", 2) for row in counts if row.count("\t") == 2]
    if tested := next((path for *_, path in rows if is_test_path(path)), None):
        return f"a change may not touch test files: {tested}"
    if sum(int(added) for added, _, _ in rows if added != "-") >= sum(int(deleted) for _, deleted, _ in rows if deleted != "-"):
        return "a change must delete more lines than it adds"
    return None


def _replay(identity: RepoIdentity, batches: list[JsonObject], tree: str) -> tuple[str, str] | None:
    """The outcome and reason `tree` does not pass a recorded batch: each runs once, on `tree` only, with its
    recorded test files; only a failed outcome is a measured counterexample."""
    if not batches:
        return "error", "no recorded comparison batch to rerun"
    for run in batches:
        command = shlex.split(run["command"])
        arm = _execute_tree(identity, tree, run["candidateTree"], run["probeFiles"], command, tdd_surface.identify(command),
                            run["timeout"], run.get("named") or {}, [])
        if arm["timedOut"]:
            return "timed-out", f"recorded batch `{run['command']}` ran past {run['timeout']:g} s: replace it with direct probes of the changed code"
        if arm["outcome"] != "passed":
            return arm["outcome"], f"recorded batch `{run['command']}` {arm['outcome']} with the change applied"
    return None


def _run_change(identity: RepoIdentity, batches: list[JsonObject], change: object, candidate: str) -> JsonObject:
    """A line-cutting diff applied to a copy of the candidate, against the recorded batches; an applied one is measured in place."""
    if not isinstance(change, str) or not change.strip():
        return {"kind": "change", "error": "change must be a unified diff"}
    patch = change.encode("utf-8", "replace") + (b"" if change.endswith("\n") else b"\n")  # unencodable text cannot apply
    try:
        changed = _indexed(identity, candidate, (("apply", "--cached", "-"), patch))
        before, after = candidate, changed
    except RuntimeError:
        try:  # already applied by the lead: the candidate itself carries the removal
            before, after = _indexed(identity, candidate, (("apply", "--cached", "--reverse", "-"), patch)), candidate
        except RuntimeError:
            return {"kind": "change", "error": "change does not apply to the candidate"}
    if refusal := _shape_refusal(identity, before, after):
        return {"kind": "change", "error": refusal}
    failure = _replay(identity, batches, after)
    if failure and failure[0] not in {"failed", "timed-out"}:
        return {"kind": "change", "error": failure[1]}  # an incomplete run measures no counterexample
    return {"kind": "change", "applied": after == candidate, "candidate": failure[0] if failure else "passed",
            **({"timedOut" if failure[0] == "timed-out" else "failure": failure[1]} if failure else {})}


def binds(execution: JsonObject) -> bool:
    """The acceptance order: a regression (a probe the original passes that fails or runs past its limit on the
    candidate) binds and is repaired; a deletion every recorded batch still passes, or one running past its limit, binds;
    anything else, a probe the original also fails included, asks for unrecorded behaviour: a report line."""
    if "error" in execution:
        return False
    if execution["kind"] == "change":
        return execution.get("candidate") in {"passed", "timed-out"}
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
                retained.append((entry, {**finding, "ref": ref}))
    return retained


def execute_findings(identity: RepoIdentity, state: JsonObject, findings: list[JsonObject]) -> JsonObject:
    """Execute each new finding's evidence, and re-execute every case of each open binding finding, on the measured candidate."""
    from .workflow_state import _finding_unresolved
    candidate = _active_candidate_tree(identity)
    original = _git(identity, "rev-parse", f"{state['passStartOid']}^{{tree}}").decode().strip()
    current = _evidence_pair(identity, state)[0] or {}  # the batches the current map compares, read once
    used = {(item.get("comparison") or {}).get("runIndex") for item in current.get("behaviorMap") or []}
    batches = [run for run in current.get("runs") or [] if run.get("valid") and run["runIndex"] in used]
    run = lambda finding, first: (_run_probe(identity, finding["probe"], candidate, original if first else None) if "probe" in finding
                                  else _run_change(identity, batches, finding["change"], candidate) if "change" in finding
                                  else {"kind": "claim", "error": "no probe or change: a report line"})
    executed = []
    for finding in findings:
        execution = run(finding, True)
        executed.append({**finding, "execution": execution, "material": binds(execution)})
    again, retained = [], _retained(identity, state)
    for entry, finding in retained:
        if _finding_unresolved(entry):
            execution = run(finding, False)
            again.append({**finding["ref"], "execution": {**execution, **{key: finding["execution"][key]
                                                                         for key in ("original",) if key in finding["execution"]}}})
    again += [{"evidenceId": entry["intakeEvidenceId"], "id": entry["findingId"], "execution": {"kind": "claim"}}
              for entry in state.get("findingStates") or []  # an open finding with no executed evidence is a report line
              if entry.get("stage") in {"code-review", "final"} and _finding_unresolved(entry)
              and all(entry is not other for other, _ in retained)]
    return {"measuredTree": candidate, "measuredEvidence": state.get("tddEvidence"), "findings": executed, "reexecuted": again}


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
    named, decisions = changed_code.analyse(identity, original, candidate)
    command, added = _named_command(surface, command, named, identity.root)
    surface = tdd_surface.identify(command)
    previous = [item.get("comparison") for item in mapped]
    runs = (current or {}).get("runs", [])
    sources = [original, candidate]  # an executed finding already ran on its reviewer's tree when recorded
    execution = {"executable": _executable(identity, command[0]),
                 "support": args.support,
                 "environment": hashlib.sha256(json.dumps(sorted(_environment().items())).encode()).hexdigest()}
    keys = [_execution_key(identity, source, candidate, files, command, args.timeout, execution) for source in sources]
    if decisions:  # the traced original answers for the conditions this candidate removes
        keys[0] = hashlib.sha256((keys[0] + json.dumps(decisions, sort_keys=True)).encode()).hexdigest()
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
                held[key] = {**_execute_tree(identity, source, candidate, files, command, surface, args.timeout, named,
                                             decisions if source == original else []), "key": key}
            arm = held[key]
        arms.append({**arm, "requestedTree": source})
    outcomes = [arm["outcome"] for arm in arms]
    valid = outcomes[-1] == "passed" and all(outcome in {"passed", "failed"} for outcome in outcomes[:-1])
    preserved = all(outcome == "passed" for outcome in outcomes)
    if preserved and surface.get("runner") not in {"pytest", "unittest"}:
        preserved = len({arm["output"].replace(arm["loadedRoot"], "<source>") for arm in arms}) == 1
    comparison = "incomplete" if not valid else "preserved" if preserved else "changed"
    # The candidate's own tests run on every arm, so renamed ids are collected apart.
    renamed = {"renamed": _renamed_tests(identity, original, candidate, files, surface, args.timeout)} if surface.get("runner") == "pytest" else {}
    run = {"runIndex": len(runs), "command": shlex.join(command), "candidateTree": candidate, "originalTree": original,
           "probeFiles": files, "support": args.support, "timeout": args.timeout, "candidateKey": keys[-1],
           "named": named, "added": added,
           "sourceDelta": _source_delta(identity, original, candidate), **renamed,
           "comparison": comparison, "arms": arms, "valid": valid, "execution": execution,
           "exitCode": 0 if valid else 1, "timedOut": any(arm["timedOut"] for arm in arms)}
    for item in mapped:
        item["comparison"] = {**run, "obligation": behavior_map.obligation(item)}
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
    items = list({item["id"]: item for item in [*(previous or []), *updates]}.values())
    for entry in updates:
        prior = recorded.get(entry["id"])
        if prior is not None and prior.get("boundaryInputs") == [] and "boundaryInputs" not in entry:
            entry["boundaryInputs"] = []  # unmapped recorded inputs stay until executed case names replace them
        if prior and "comparison" in prior:  # a changed kind or obligation keeps its latest run and reads stale until rerun
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


def _pass_proof(surface: JsonObject, output: str, named: dict[str, str]) -> tuple[JsonObject | None, str]:
    """A successful exit must report an executed check or observable operation. Skips and expected
    failures void it, except each one the runner names inside a test file named for a changed function."""
    runner = surface.get("runner")
    output = tdd_surface.ANSI_ESCAPE.sub("", output)
    if runner not in {"unittest", "pytest"}:
        observed = tdd_surface._final_diagnostic([line for line in output.splitlines() if line.strip()])[:1000]
        if not observed:
            return None, "the operation emitted nothing to observe"
        return {"quality": "operation-succeeded", "reach": "unresolved", "runner": runner,
                "observation": [observed], "site": shlex.join(surface.get("arguments") or [])}, ""
    excused = sum(case["outcome"] == "skipped" and any(_from_file(name, path) for path in named)
                  for name, case in tdd_surface.case_results(surface, output).items())
    if runner == "unittest":
        runs = list(tdd_surface.UNITTEST_RAN.finditer(output))
        result = re.search(r"(?m)^OK(?: \((.*)\))?$", output[runs[-1].end():]) if runs else None
        if result and result.group(1) and result.group(1) != f"skipped={excused}":
            return None, "selected unittest probes include skipped or expected failures"
        executed = int(runs[-1].group(1)) - (excused if result and result.group(1) else 0) if result else 0
    else:
        summaries = tdd_surface.PYTEST_SUMMARY.findall(output)
        unexecuted = sum(int(count) for count in re.findall(r"(\d+) (?:skipped|xfailed|xpassed)\b", summaries[-1])) if summaries else 0
        if unexecuted and unexecuted != excused:
            return None, "selected pytest probes did not all execute passing assertions"
        passed = re.search(r"(?<!\d)(\d+) passed\b", summaries[-1]) if summaries else None
        executed = int(passed.group(1)) if passed else 0
    if executed < 1:
        return None, f"{runner} did not report an executed passing test"
    return {"quality": "tests-passed", "runner": runner, "testsExecuted": executed}, ""
