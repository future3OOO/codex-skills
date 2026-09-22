"""TDD surface identity and structured RED proof.

RED and GREEN must select the same tests, not use byte-identical command text.
Direct pytest and unittest commands establish reach: the runner reports an
executed test whose own failure carries the mapped marker. Any other command is
an operation at the production Interface: its failure is recorded with reach
unresolved and review establishes that the observed failure is the mapped
promise. The workflow ledger is continuity, not an attestation system.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from .state_store import is_test_path

SURFACE_SCHEMA_VERSION = 1
INTERPRETER = re.compile(r"^python(3(\.\d+)?)?$")
REPEATED_VERBOSITY = re.compile(r"^-(v+|q+)$")
DIRECT_RUNNERS = {"pytest": "pytest", "py.test": "pytest"}
IGNORED_BY_RUNNER = {
    "unittest": {
        "-f": "fail-fast",
        "--failfast": "fail-fast",
        "--verbose": "verbosity",
        "--quiet": "verbosity",
    },
    "pytest": {
        "-x": "fail-fast",
        "--exitfirst": "fail-fast",
        "--maxfail=1": "fail-fast",
        "--verbose": "verbosity",
        "--quiet": "verbosity",
    },
}
EXACT_BOUND = "unrecognised runner; identity stays bound to the exact command"
EVIDENCE_ONLY = frozenset({"ignored"})
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UNITTEST_RAN = re.compile(r"(?m)^Ran (\d+) tests? in ")
UNITTEST_FAILED = re.compile(r"(?m)^FAILED \(([^)]*)\)")
# pytest's terminal summary line, framed with = at normal verbosity and bare
# under -q; the only place a pass count describes the run.
PYTEST_SUMMARY = re.compile(r"(?m)^(?:=+ )?(.+?) in \d+\.\d+s(?: \([^)]*\))?(?: =+)?$")
# Failures identifiable as happening before the production Interface: a command
# that could not start, the Python, Node and shell loaders' missing-target
# reports, and the import and syntax exception classes. Nothing else is classified.
PRE_INTERFACE_FAILURE = re.compile(
    r"^(?:\[Errno \d+\] |\S+: (?:No module named |can't open file )"
    r"|Error(?: \[\w+\])?: Cannot find (?:module|package) "
    r"|\S+: (?:line )?\d+: \S+: (?:command )?not found$"
    r"|(?:\w+\.)*(?:ModuleNotFoundError|ImportError|SyntaxError|IndentationError)\b)"
)
UNITTEST_FIXTURES = frozenset({
    "setUp", "asyncSetUp", "tearDown", "asyncTearDown",
    "setUpClass", "tearDownClass", "setUpModule", "tearDownModule",
})
PYTEST_FAILURE_HEADER = re.compile(r"^_{3,}.+_{3,}$")
PYTEST_CAPTURED_HEADER = re.compile(r"^-+ Captured .+ -+$")
# pytest closes each traceback frame with `path:line:` (the exception class only
# on the last); unittest frames are `File "..."` lines. Object reprs carry the
# process's addresses, which say nothing about what was observed.
PYTEST_LOCATION = re.compile(r"^(\S+):(\d+): ?\w*$")
UNITTEST_FRAME = re.compile(r'^  File "([^"]+)", line (\d+), in ')
OBJECT_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")
PYTEST_SUMMARY_RECORDS = (
    "FAILED ",
    "ERROR ",
    "SKIPPED ",
    "XFAIL ",
    "XPASS ",
    "PASSED ",
    "RERUN ",
)
PYTEST_TB_SUPPRESSED = ("--tb=no", "--tb=line")


def identify(command: Sequence[str]) -> dict[str, object]:
    """Return the comparable test surface selected by ``command``."""
    runner, prefix = _recognise(command)
    if runner is None:
        return _surface("exact", "", list(command), (), EXACT_BOUND)
    arguments: list[str] = []
    ignored: set[str] = set()
    literal = False
    for token in command[len(prefix) :]:
        literal = literal or token == "--"
        dropped = None if literal else _ignored_class(runner, token)
        if dropped is None:
            arguments.append(token)
        else:
            ignored.add(dropped)
    return _surface(runner, shlex.join(prefix), arguments, sorted(ignored), None)


UNITTEST_VALUE_OPTIONS = frozenset({"-k"})
UNITTEST_DISCOVER_VALUE_OPTIONS = frozenset({
    "-s", "--start-directory", "-p", "--pattern", "-t", "--top-level-directory", "-k",
})
UNITTEST_START_OPTIONS = frozenset({"-s", "--start-directory"})
# pytest core options that take no value, measured from pytest 9.1.1's own
# argparse actions (nargs == 0); a path after one of these is a target, a
# path after an option in neither table may be a plugin option's value.
PYTEST_FLAG_OPTIONS = frozenset({
    "--cache-clear", "--co", "--collect-in-virtualenv", "--collect-only", "--collectonly",
    "--continue-on-collection-errors", "--disable-plugin-autoload", "--disable-pytest-warnings",
    "--disable-warnings", "--doctest-continue-on-failure", "--doctest-ignore-import-errors",
    "--doctest-modules", "--exitfirst", "--failed-first", "--ff", "--fixtures",
    "--fixtures-per-test", "--force-short-summary", "--full-trace", "--fulltrace", "--funcargs",
    "--help", "--keep-duplicates", "--keepduplicates", "--last-failed", "--lf", "--markers",
    "--new-first", "--nf", "--no-fold-skipped", "--no-header", "--no-showlocals",
    "--no-summary", "--noconftest", "--pdb", "--pyargs", "--quiet", "--runxfail",
    "--setup-only", "--setup-plan", "--setup-show", "--setuponly", "--setupplan", "--setupshow",
    "--showlocals", "--stepwise", "--stepwise-reset", "--stepwise-skip", "--strict",
    "--strict-config", "--strict-markers", "--sw", "--sw-reset", "--sw-skip", "--trace",
    "--trace-config", "--traceconfig", "--verbose", "--version", "--xfail-tb", "-V", "-h", "-l",
    "-q", "-s", "-v", "-x",
})
# The complete value-taking core option domain measured from pytest's own
# parser (every registered option whose action stores a value); plugin options
# stay under the fail-closed target-shape rule.
PYTEST_VALUE_OPTIONS = frozenset({
    "-W", "-c", "-k", "-m", "-o", "-p", "-r",
    "--assert", "--basetemp", "--cache-show", "--capture", "--code-highlight",
    "--color", "--confcutdir", "--config-file", "--debug", "--deselect",
    "--doctest-glob", "--doctest-report", "--durations", "--durations-min",
    "--ignore", "--ignore-glob", "--import-mode", "--junit-prefix",
    "--junit-xml", "--junitprefix", "--junitxml", "--last-failed-no-failures",
    "--lfnf", "--log-auto-indent", "--log-cli-date-format", "--log-cli-format",
    "--log-cli-level", "--log-date-format", "--log-disable", "--log-file",
    "--log-file-date-format", "--log-file-format", "--log-file-level",
    "--log-file-mode", "--log-format", "--log-level", "--max-warnings",
    "--maxfail", "--override-ini", "--pastebin", "--pdbcls",
    "--pythonwarnings", "--report-chars", "--rootdir", "--show-capture",
    "--tb", "--verbosity",
})


def proof_targets(
    surface: Mapping[str, object], root: object
) -> tuple[list[str], bool, list[str], str | None]:
    """The test targets a unittest or pytest surface names, whether it is a
    discover run, the ambiguous path tokens, and what this parse cannot resolve.

    Option values are skipped by each runner's value-taking option table; a
    pytest bare word or number that names nothing under ``root`` is an unknown
    option's value, not a target. Every path-shaped pytest token after the first
    unknown option (a plugin's) may be one of its values, so they are returned as
    ambiguous: resolved fail-closed by callers, never a named target. A discover
    run with no start directory targets ``.``.

    The fourth value describes what this parse cannot resolve into named scope,
    or None: an option beyond the discovery routing and the verbosity and
    fail-fast ones `identify` removes, a discovery pattern after the start
    directory, or a command naming no target, which selects implicitly from the
    runner's own working directory or configuration. Callers publish named
    ownership only when it is None.
    """
    runner = surface.get("runner")
    top = Path(str(root)).resolve()
    raw = surface.get("arguments")
    tokens = [token for token in raw if isinstance(token, str)] if isinstance(raw, list) else []
    discover = runner == "unittest" and bool(tokens) and tokens[0] == "discover"
    value_options = (
        UNITTEST_DISCOVER_VALUE_OPTIONS if discover
        else UNITTEST_VALUE_OPTIONS if runner == "unittest" else PYTEST_VALUE_OPTIONS
    )
    targets: list[str] = []
    ambiguous: list[str] = []
    unresolved: str | None = None
    pending_start = False
    pending_value = False
    after_unknown_option = False
    for token in tokens[1 if discover else 0:]:
        if pending_value:
            if pending_start:
                targets.append(token)
            pending_start = pending_value = False
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            name, separator, inline = token.partition("=")
            # The separator, not the value's truthiness, says a value was given:
            # -k= carries an empty value and does not take the next token.
            has_value = bool(separator)
            known_cluster = False
            all_ignored = False
            if runner == "pytest" and name[1:2] != "-" and len(name) > 2:
                # A short cluster reads left to right: no-value flags, then at
                # most one value option whose value is the rest of the token or
                # the next token (-xktest_a is -x -k test_a; -qk test is -q -k test).
                letters = name[1:]
                head = 0
                while head < len(letters) and f"-{letters[head]}" in PYTEST_FLAG_OPTIONS:
                    head += 1
                if head == len(letters) and not separator:
                    known_cluster = True
                    # Only when every letter is one identify already treats as
                    # irrelevant: `-xq` changes nothing about which tests run,
                    # while `-xqh` is the same arity and runs none of them.
                    all_ignored = all(_ignored_class(runner, f"-{letter}") for letter in letters)
                elif head < len(letters) and f"-{letters[head]}" in value_options:
                    rest = token[2 + head:]
                    name, has_value = f"-{letters[head]}", bool(rest)
                    inline = rest[1:] if rest.startswith("=") else rest
            # Once an unknown pytest option appears, nothing after it is a named
            # target: an option declared with REMAINDER swallows later flags and
            # the sentinel too, so ambiguity never clears. Targets go first.
            after_unknown_option = after_unknown_option or (
                runner == "pytest" and not has_value and name not in value_options
                and name not in PYTEST_FLAG_OPTIONS and not known_cluster
                and not REPEATED_VERBOSITY.match(name)
            )
            # After cluster normalization, so `-kfast` reports as `-k`. Discovery
            # routing is the only option this parse turns into a target.
            if not (discover and name in UNITTEST_START_OPTIONS) and not REPEATED_VERBOSITY.match(name) and not all_ignored:
                unresolved = unresolved or f"the option {name}"
            if name in value_options:
                if has_value:
                    if discover and name in UNITTEST_START_OPTIONS:
                        targets.append(inline)
                else:
                    pending_value = True
                    pending_start = discover and name in UNITTEST_START_OPTIONS
            continue
        if runner == "pytest" and not (
            "/" in token or "\\" in token or "::" in token
            or token.endswith(".py") or (top / token).exists()
        ):
            continue
        if discover and targets:
            # `discover <start> <pattern>`: only the start is routing.
            unresolved = unresolved or f"the discovery pattern {token}"
        (ambiguous if after_unknown_option else targets).append(token)
    if not targets:
        # Nothing named: both runners then select implicitly, discovery from the
        # working directory and pytest from its own rootdir and configuration.
        unresolved = unresolved or "an implicit whole-suite selection"
        if discover:
            targets.append(".")
    return targets, discover, ambiguous, unresolved


def repository_resolution(surface: Mapping[str, object], root: object) -> str | None:
    """Why the mapped proof targets do not resolve inside ``root``, or None.

    The narrowed promise is target-name resolution: unittest selectors,
    discover start directories, and pytest targets must resolve under the
    repository root. Deliberately routing executed test source from outside
    the repository through an in-repo re-export, load_tests, or conftest
    delegation remains the audited fabrication class, not a mechanical
    refusal - the ledger is continuity, not an attestation system.
    """
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest"}:
        return None
    top = Path(str(root)).resolve()
    raw = surface.get("arguments")
    tokens = [token for token in raw if isinstance(token, str)] if isinstance(raw, list) else []
    if runner == "pytest" and "--pyargs" in tokens:
        return "--pyargs selects import targets whose location the repository root cannot establish; use repository path targets"
    targets, discover, ambiguous, _ = proof_targets(surface, root)
    unresolved: list[str] = []
    for target in targets + ambiguous:
        selector = target.split("::", 1)[0] if runner == "pytest" else target
        if (
            runner == "unittest"
            and not discover
            and "/" not in selector
            and "\\" not in selector
            and not selector.endswith(".py")
        ):
            head = selector.split(".", 1)[0]
            if not ((top / f"{head}.py").exists() or (top / head).is_dir()):
                unresolved.append(target)
            continue
        candidate = Path(selector)
        try:
            resolved = (candidate if candidate.is_absolute() else top / candidate).resolve()
            in_repo = resolved.is_relative_to(top) and resolved.exists()
        except OSError:
            in_repo = False
        if not in_repo:
            unresolved.append(target)
    if unresolved:
        return "proof target(s) do not resolve under the repository root: " + ", ".join(sorted(set(unresolved)))
    return None


def input_evidence(surface: Mapping[str, object], root: Path, inputs: list[object],
                   test_id: str | None = None) -> dict[str, object]:
    """Represent selected inputs by typed JSON equality, never infer reach.
    Python runners bound source selection; opaque selections report limits, not absence.
    """
    values: list[object] = []
    sources: dict[str, str] = {}
    limits: list[str] = []
    runner = surface.get("runner")
    targets, discover, ambiguous, unresolved = proof_targets(surface, root)
    if runner == "exact":
        args = list(surface.get("arguments", []))
        if args and INTERPRETER.fullmatch(Path(str(args[0])).name):
            if len(args) > 1 and str(args[1]).endswith(".py"):
                targets = [str(args[1])]
                values.extend(args[2:])
                unresolved = None
            else:
                targets, unresolved = [], "inline/module execution has no selected source binding"
        else:
            # argv is the concrete process Interface, independent of its language.
            values.extend(args[1:])
            targets, unresolved = [], None
    if test_id:
        targets, discover, ambiguous, unresolved = [test_id], False, [], None
    if discover or ambiguous or unresolved or len(targets) > 32:
        limits.append(unresolved or "selection is too coarse or exceeds 32 files")
        targets = []
    for target in targets:
        if runner == "unittest":
            parts = target.split(".")
            path = root / target
            names: list[str] = []
            if not path.is_file():
                while parts:
                    path = root.joinpath(*parts).with_suffix(".py")
                    if path.is_file():
                        break
                    names.insert(0, parts.pop())
        else:
            file, *names = target.split("::")
            path = root / file
        try:
            if not Path(os.path.abspath(path)).is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                limits.append(f"unresolved selected source: {target}")
                continue
            with path.open("rb") as handle:
                data = handle.read(262145)
            if len(data) > 262144:
                limits.append(f"selected source exceeds 256 KiB: {target}")
                continue
            tree = ast.parse(data, filename=str(path))
            if sum(1 for _ in ast.walk(tree)) > 10000:
                limits.append(f"selected source exceeds 10000 nodes: {target}")
                continue
        except (OSError, SyntaxError, UnicodeError) as exc:
            limits.append(f"selected source unavailable: {target}: {exc}")
            continue
        sources[str(path.resolve().relative_to(root.resolve()))] = hashlib.sha256(data).hexdigest()
        selected: list[ast.AST] = [tree]
        case_id = None
        for name in names:
            if "[" in name:
                name, _, case = name.partition("[")
                case_id = case.removesuffix("]")
            selected = [child for parent in selected for child in getattr(parent, "body", [])
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name]
        classes = [node for node in selected if isinstance(node, ast.ClassDef)]
        if any(ast.unparse(base) not in {"unittest.TestCase", "object"} for node in classes for base in node.bases):
            limits.append("inherited test bodies were not inspected")
        selected = [child for node in selected for child in (
            [member for member in node.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
             and (member.name.startswith("test") or member.name in {"setUp", "tearDown", "setUpClass", "tearDownClass", "setup_method", "teardown_method", "setup_class", "teardown_class"})]
            if isinstance(node, ast.ClassDef) else [node])]
        if not selected:
            limits.append(f"selected definition unavailable: {target}")
            continue
        bindings: dict[str, list[object]] = {}
        fixtures: set[str] = set()
        for node in tree.body if runner != "exact" else []:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for expression in [node.args, *([node.returns] if node.returns else [])]:
                    _source_inputs(expression, bindings, [], [])
                bindings.pop(node.name, None)
                decorator = node.decorator_list[0] if len(node.decorator_list) == 1 else None
                if isinstance(decorator, ast.Call) and not decorator.args and not decorator.keywords:
                    decorator = decorator.func
                if (len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                        and isinstance(decorator, ast.Attribute) and decorator.attr == "fixture"):
                    fixtures.add(node.name)
                    bindings[node.name] = _input_literals(node.body[0].value, {})
                elif node.decorator_list:
                    for decorator in node.decorator_list:
                        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                                and decorator.func.attr == "parametrize"):
                            bindings.clear()
                        else:
                            for expression in [*decorator.args, *(kw.value for kw in decorator.keywords)]:
                                _source_inputs(expression, bindings, [], [])
            else:
                _source_inputs(node, bindings, [], [])
        for node in selected:
            if isinstance(node, ast.Module) and runner != "exact":
                limits.append(f"whole-file runner selection lacks case attribution: {target}")
                continue
            local = {} if classes else dict(bindings)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = {arg.arg for arg in node.args.args[-len(node.args.defaults):]} if node.args.defaults else set()
                defaults.update(arg.arg for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults) if default is not None)
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg):
                    if arg is not None and (arg.arg not in fixtures or arg.arg in defaults):
                        local.pop(arg.arg, None)
            _source_inputs(node, local, values, limits, case_id=case_id, selected=True)
    keys = {json.dumps(value, sort_keys=True, allow_nan=False) for value in values}
    represented = [value for value in inputs if json.dumps(value, sort_keys=True, allow_nan=False) in keys]
    absent = [value for value in inputs if json.dumps(value, sort_keys=True, allow_nan=False) not in keys]
    return {"represented": represented, "missing": [] if limits else absent,
            "unresolved": absent if limits else [], "limits": sorted(set(limits)), "sources": sources}


def _input_literals(node: ast.AST, bindings: dict[str, list[object]]) -> list[object]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, [])
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_input_literals(part, bindings) for part in node.elts]
        if all(len(part) == 1 for part in parts):
            return [[part[0] for part in parts]]
        varying = {part.id for part in ast.walk(node) if isinstance(part, ast.Name)
                   and len(bindings.get(part.id, [])) > 1}
        if len(varying) == 1:
            name = varying.pop()
            return [value for candidate in bindings[name]
                    for value in _input_literals(node, {**bindings, name: [candidate]})]
        return []
    if isinstance(node, ast.Subscript):
        try:
            key = ast.literal_eval(node.slice)
            return [value[key] for value in _input_literals(node.value, bindings)
                    if isinstance(value, (dict, list, tuple))]
        except (ValueError, TypeError, KeyError, IndexError):
            return []
    try:
        value = ast.literal_eval(node)
        json.dumps(value, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, SyntaxError):
        return []
    return [value]


def _source_inputs(node: ast.AST, bindings: dict[str, list[object]],
                   values: list[object], limits: list[str], *, case_id: str | None = None,
                   selected: bool = False) -> None:
    """Inspect literal call inputs and local literal tables, not arbitrary dataflow."""
    if isinstance(node, ast.ClassDef):
        bindings.pop(node.name, None)
        if node.decorator_list or node.keywords or any(isinstance(part, ast.Call) for base in node.bases for part in ast.walk(base)):
            bindings.clear()
        for statement in node.body:
            _source_inputs(statement.value if isinstance(statement, ast.Assign) else statement, bindings, [], [])
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not selected:
        bindings.pop(node.name, None)
        for expression in [node.args, *([node.returns] if node.returns else [])]:
            _source_inputs(expression, bindings, [], limits)
        if node.decorator_list:
            bindings.clear()
        limits.append("unselected callable bodies were not inspected")
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bindings = dict(bindings)
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "parametrize" and len(decorator.args) >= 2):
                # Decorators ran at definition time, not with the function's
                # current globals. Only literal case data is known here.
                names = _input_literals(decorator.args[0], {})
                rows = _input_literals(decorator.args[1], {})
                if len(names) == len(rows) == 1 and isinstance(names[0], str) and isinstance(rows[0], (list, tuple)):
                    cases = rows[0]
                    if case_id is not None:
                        ids = next((kw.value for kw in decorator.keywords if kw.arg == "ids"), None)
                        labels = _input_literals(ids, {}) if ids is not None else []
                        if len(labels) != 1 or not isinstance(labels[0], (list, tuple)) or labels[0].count(case_id) != 1:
                            limits.append("parameter subset lacks concrete attributed case data")
                            continue
                        cases = [cases[labels[0].index(case_id)]]
                    fields = [name.strip() for name in names[0].split(",")]
                    if any(kw.arg == "indirect" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                           for kw in decorator.keywords) or any(
                           len(fields) > 1 and (not isinstance(row, (list, tuple)) or len(row) != len(fields)) for row in cases):
                        limits.append("indirect parameter data")
                        continue
                    bindings.update({name: [row[index] for row in cases] if len(fields) > 1 else list(cases) for index, name in enumerate(fields)})
                else:
                    limits.append("indirect parameter data")
            else:
                limits.append("opaque decorator")
        for statement in node.body:
            _source_inputs(statement, bindings, values, limits)
        return
    if isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) for target in node.targets):
        _source_inputs(node.value, bindings, values, limits)
        for name in node.targets:
            bindings[name.id] = _input_literals(node.value, bindings)
        return
    if isinstance(node, ast.For):
        if node.orelse:
            bindings.clear()
            limits.append("loop else input flow was not inspected")
            return
        rows = _input_literals(node.iter, bindings)
        if len(rows) == 1 and isinstance(rows[0], (list, tuple)):
            local = dict(bindings)
            if isinstance(node.target, ast.Name):
                local[node.target.id] = list(rows[0])
            elif isinstance(node.target, (ast.Tuple, ast.List)) and all(isinstance(n, ast.Name) for n in node.target.elts):
                try:
                    for index, name in enumerate(node.target.elts):
                        local[name.id] = [row[index] for row in rows[0]]
                except (IndexError, TypeError):
                    limits.append("indirect loop data")
            else:
                limits.append("indirect loop target")
            for statement in node.body:
                _source_inputs(statement, local, values, limits)
        else:
            limits.append("indirect loop data")
        bindings.clear()
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = {alias.asname or alias.name.split(".")[0] for alias in node.names}
        for name in list(bindings) if "*" in names else names:
            bindings.pop(name, None)
        return
    if isinstance(node, (ast.Assert, ast.Compare)):
        _source_inputs(node.test if isinstance(node, ast.Assert) else node.left, bindings, values, limits)
        if isinstance(node, ast.Compare) and any(isinstance(child, ast.Call) for operand in node.comparators for child in ast.walk(operand)):
            limits.append("comparison has an ambiguous input/expected operand")
        bindings.clear()
        return
    if isinstance(node, ast.Call):
        arguments = [*node.args, *(kw.value for kw in node.keywords)]
        assertion = (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                     and node.func.value.id == "self" and node.func.attr.startswith("assert"))
        output = (isinstance(node.func, ast.Name) and node.func.id in {"print", "repr"}) or (
            isinstance(node.func, ast.Attribute) and node.func.attr == "write" and ast.unparse(node.func.value) in {"sys.stdout", "sys.stderr"})
        if not (assertion or output):
            _source_inputs(node.func, bindings, values, limits)
        # Later arguments can mutate aliases evaluated earlier. Resolve cached
        # values only after all argument effects, then expire call-owned state.
        for argument in node.args[:1] if assertion else arguments:
            _source_inputs(argument, bindings, values, limits)
        ambiguous = (assertion and getattr(node.func, "attr", "").startswith(("assertRaises", "assertWarns"))) or any(kw.arg is None for kw in node.keywords) or any(
            isinstance(part, ast.Call) for argument in (node.args[1:] if assertion else arguments) for part in ast.walk(argument))
        if not output and (ambiguous or (not arguments and not (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call)))):
            limits.append("call argument roles are ambiguous")
        for argument in [] if assertion or output or ambiguous else arguments:
            concrete = _input_literals(argument, bindings)
            if not concrete:
                limits.append("dynamic call input")
            values.extend(part for value in concrete for part in ([value, *value] if isinstance(value, (list, tuple)) else [value]))
        bindings.clear()
        return
    # These wrappers have ordered children, not alternative execution paths.
    # Unhandled syntax must not restore knowledge by walking competing branches.
    if _input_literals(node, {}):
        return
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        concrete = _input_literals(node.value if isinstance(node, ast.Attribute) else node, bindings)
        for child in ast.iter_child_nodes(node):
            _source_inputs(child, bindings, values, limits)
        if not concrete:
            bindings.clear()
        return
    if isinstance(node, ast.Dict) and any(key is None or not _input_literals(key, bindings) for key in node.keys):
        bindings.clear()
        limits.append("opaque mapping construction")
        return
    if not isinstance(node, (ast.Module, ast.Expr, ast.Return, ast.Pass, ast.arguments, ast.arg,
                             ast.List, ast.Tuple, ast.Dict,
                             ast.Name, ast.Load)):
        bindings.clear()
        limits.append("conditional or indirect input flow")
        return
    for child in ast.iter_child_nodes(node):
        _source_inputs(child, bindings, values, limits)


def differences(
    recorded: Mapping[str, object], requested: Mapping[str, object]
) -> list[dict[str, object]]:
    """Return named surface differences; empty means the same selected tests."""
    fields = (set(recorded) | set(requested)) - EVIDENCE_ONLY
    return [
        {
            "field": f"surface.{name}",
            "recorded": recorded.get(name),
            "requested": requested.get(name),
        }
        for name in sorted(fields)
        if recorded.get(name) != requested.get(name)
    ]


def evaluate_red(
    surface: Mapping[str, object], output: str, marker: str, root: Path | None = None
) -> tuple[dict[str, object] | None, str]:
    """Evidence that RED reached the mapped failure, or why it did not: a runner's
    report decides for runner surfaces; a non-runner operation is classified by
    its final diagnostic and keeps its marker line with reach unresolved.

    A proof also records what the failure observed apart from the authored marker
    (issue #54): `observation` is the terminal rendering with the marker elided and
    object addresses dropped; `site` is the last test-side frame of the terminal
    traceback with that source line when ``root`` resolves it, or the command for
    a non-runner operation. The same observation cannot open RED for two items."""
    runner = surface.get("runner")
    output = ANSI_ESCAPE.sub("", output)
    lines = [line for line in output.splitlines() if line.strip()]
    diagnostic = _final_diagnostic(lines)
    if runner not in {"unittest", "pytest"} and (refusal := _pre_interface_refusal(diagnostic)):
        return None, refusal
    if marker not in output:
        return None, f"output did not contain the mapped redFailure marker {marker!r}"
    if runner == "unittest":
        return _with_observation(_unittest_red(output, marker), marker, root)
    if runner == "pytest":
        arguments = surface.get("arguments")
        return _with_observation(
            _pytest_red(output, marker, arguments if isinstance(arguments, list) else ()), marker, root,
        )
    observed = diagnostic if marker in diagnostic else next(line.strip() for line in lines if marker in line)
    return {"quality": "failure-observed", "reach": "unresolved", "runner": str(runner),
            "observedFailure": observed, "observation": [observed.replace(marker, "")],
            "site": shlex.join(str(token) for token in surface.get("arguments") or [])}, ""


def _with_observation(
    result: tuple[dict[str, object] | None, str], marker: str, root: Path | None
) -> tuple[dict[str, object] | None, str]:
    """Replace the runner proof's raw rendering with the recorded observation and site."""
    proof, _ = result
    if proof is None:
        return result
    rendering = proof.pop("rendering")
    location = proof.pop("location")
    observation = [OBJECT_ADDRESS.sub("", line.replace(marker, "")) for line in rendering if line]
    site = location
    if root is not None and location:
        path, _, line_number = location.rpartition(":")
        try:
            source = (Path(root) / path).read_text(encoding="utf-8", errors="replace").splitlines()
            site = f"{location} {source[int(line_number) - 1].strip()}"
        except (OSError, IndexError, ValueError):
            site = location
    return {**proof, "observation": observation, "site": site}, ""


def _final_diagnostic(lines: list[str]) -> str:
    """The exception line of the last Python traceback, the last ``Error:`` line of
    a Node uncaught-error report, else the last line: buffered stdout flushes after
    an uncaught traceback, so the last line alone does not name the failure."""
    last = lines[-1].strip() if lines else ""
    if last.startswith("Node.js v"):
        return next((line.strip() for line in reversed(lines) if re.match(r"\w*Error(?: \[\w+\])?: ", line.strip())), last)
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() == "Traceback (most recent call last):":
            return next((line.strip() for line in lines[index + 1:] if not line[0].isspace()), last)
    return last


def _not_terminal(runner: str, terminal: list[str]) -> str:
    return f"mapped marker was not carried by the failure that ended an executed {runner} test" + (
        ": " + terminal[-1] if terminal else ""
    )


def _pre_interface_refusal(diagnostic: str) -> str | None:
    prefix = "the operation failed before reaching the production Interface: "
    return prefix + diagnostic if PRE_INTERFACE_FAILURE.match(diagnostic) else None


def _unittest_red(
    output: str, marker: str, *, test_id: str | None = None
) -> tuple[dict[str, object] | None, str]:
    runs = list(UNITTEST_RAN.finditer(output))
    ran = runs[-1] if runs else None
    if ran is None or int(ran.group(1)) < 1:
        return None, "unittest did not report an executed test"
    summaries = [
        match for match in UNITTEST_FAILED.finditer(output) if match.start() > ran.start()
    ]
    summary = summaries[-1] if summaries else None
    if summary is None:
        return None, "unittest did not report a failed test"
    summary_counts: dict[str, int] = {}
    for field in summary.group(1).split(","):
        count = re.fullmatch(r"(failures|errors)=(\d+)", field.strip())
        if count:
            summary_counts[count.group(1)] = int(count.group(2))
    failures = _unittest_terminal_failures(output)
    report_counts = tuple(sum(header.startswith(kind) for _, header, _, _, _ in failures)
                          for kind in ("FAIL: ", "ERROR: "))
    expected_counts = (
        summary_counts.get("failures", 0),
        summary_counts.get("errors", 0),
    )
    if report_counts != expected_counts:
        return None, (
            f"unittest report blocks failures={report_counts[0]}, errors={report_counts[1]} "
            f"did not match summary failures={expected_counts[0]}, errors={expected_counts[1]}"
        )
    if summary_counts.get("failures", 0) + summary_counts.get("errors", 0) < 1:
        return None, "unittest did not report a failed test"
    if test_id is not None:
        selected = []
        for block in failures:
            match = re.fullmatch(r"(?:FAIL|ERROR): (\S+) \(([^)]+)\)", block[1])
            if match is not None:
                name, parent = match.groups()
                identifier = parent if parent.endswith('.' + name) else parent + '.' + name
                if identifier == test_id:
                    selected.append(block)
        if len(selected) != 1:
            return None, "the selected test has no unique terminal failure"
        failures = selected
    unreached = next((reason for reason in (_unittest_unreached(*block[1:3]) for block in failures) if reason), None)
    if unreached is not None:
        return None, "the operation failed before reaching the production Interface: " + unreached
    found = next((block for block in failures for line in block[3] if marker in line), None)
    if found is None:
        return None, _not_terminal("unittest", [rendering[0] for _, _, _, rendering, _ in failures if rendering])
    rendering = found[3]
    head, observed = rendering[0], next(line for line in rendering if marker in line)
    refusal = _pre_interface_refusal(head)
    if refusal is not None:
        return None, refusal
    return {
        "quality": "assertion-reached",
        "runner": "unittest",
        "testsExecuted": int(ran.group(1)),
        "observedFailure": observed,
        "rendering": rendering,
        "location": found[4],
    }, ""


def attributed_result(surface: dict[str, object], receipt: dict[str, object], test_id: str,
                      marker: str, root: Path | None = None) -> tuple[str | None, dict[str, object] | None, str]:
    """Attribute a retained verbose unittest report; ambiguity stays single-item."""
    if surface.get("runner") != "unittest" or receipt.get("outputBytes", 16001) > 16000:
        return None, None, "reuse needs a complete verbose unittest execution report"
    if test_id.rsplit(".", 1)[-1] in UNITTEST_FIXTURES:
        return None, None, "a fixture cannot supply test-body proof"
    command = shlex.split(str(receipt.get("command", "")))
    runner, prefix = _recognise(command)
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("-v", "--verbose", dest="verbosity", action="store_const", const=2)
    parser.add_argument("-q", "--quiet", dest="verbosity", action="store_const", const=0)
    for option in ("-f", "-c", "-b"):
        parser.add_argument(option, action="store_true")
    for option in UNITTEST_DISCOVER_VALUE_OPTIONS | {"--durations"}:
        parser.add_argument(option)
    try:
        options, _ = parser.parse_known_args(command[len(prefix):])
    except argparse.ArgumentError:
        return None, None, "reuse could not establish unittest verbosity"
    if runner != "unittest" or options.verbosity != 2:
        return None, None, "reuse requires an effectively verbose unittest command"
    output = ANSI_ESCAPE.sub("", str(receipt.get("outputTail", "")))
    ran = list(UNITTEST_RAN.finditer(output))
    report = output[:ran[-1].start()] if ran else ""
    # Output has no producer labels. A row search is insufficient: every byte
    # of progress must belong to a result or a separator, otherwise execute the
    # item directly. Terminal tracebacks remain the existing validator's job.
    terminal = _unittest_terminal_failures(output)
    progress = report[:terminal[0][0]] if terminal else report
    record_pattern = re.compile(
        r"(?m)^(\S+) \(([^)]+)\)(?:\n([^\n]*))? \.\.\. "
        r"(ok|FAIL|ERROR|skipped .*|expected failure|unexpected success)$"
    )
    records = record_pattern.findall(progress)
    remainder = record_pattern.sub("", progress)
    if any(line and not _rule(line, "=") and not _rule(line, "-") for line in remainder.splitlines()):
        return None, None, "interleaved output prevents test attribution; use direct execution"
    # A native identity inside a description admits another interpretation of
    # the merged text. Ordinary prose, including parentheses, remains data.
    if any(parent.endswith("." + name) for _, _, description, _ in records
           for name, parent in re.findall(r"(\S+) \(([^)]+)\)", description)):
        return None, None, "description and test progress cannot be distinguished; use direct execution"
    failures = [f"{outcome}: {name} ({parent})" for name, parent, _, outcome in records
                if outcome in {"FAIL", "ERROR"}]
    if sorted(failures) != sorted(header for _, header, _, _, _ in terminal):
        return None, None, "terminal failures disagree with test results"
    summary = UNITTEST_FAILED.search(output[ran[-1].end():]) if ran else None
    counts = dict(re.findall(r"(failures|errors)=(\d+)", summary[1])) if summary else {}
    if any(sum(outcome == status for _, _, _, outcome in records) != int(counts.get(key, 0))
           for key, status in (("failures", "FAIL"), ("errors", "ERROR"))):
        return None, None, "summary disagrees with test results"
    results = [(parent if parent.endswith('.' + name) else parent + '.' + name, outcome)
               for name, parent, _, outcome in records]
    selected = [outcome for identifier, outcome in results if identifier == test_id]
    if (len(ran) != 1 or int(ran[-1].group(1)) != sum(name not in UNITTEST_FIXTURES for name, _, _, _ in records)
            or len(selected) != 1 or len({identifier for identifier, _ in results}) != len(results)):
        return None, None, "report does not unambiguously identify each executed test"
    outcome = selected[0]
    proof: dict[str, object] = {"runner": "unittest", "testId": test_id, "testsExecuted": 1}
    if outcome == "ok":
        footer = output[ran[-1].end():]
        if not re.search(r"(?m)^(OK(?: \(.*\))?|FAILED \(.*\))$", footer):
            return None, None, "runner did not complete its report"
        return "passed", {**proof, "quality": "baseline-passed"}, ""
    if outcome.startswith("skipped") or outcome == "expected failure":
        return "skipped", None, "selected test did not execute a passing assertion"
    checked, error = _with_observation(_unittest_red(output, marker, test_id=test_id), marker, root)
    if checked is None:
        return None, None, error
    return "failed", {**checked, **proof}, ""


def _unittest_unreached(header: str, frames: list[str]) -> str | None:
    """Why the block's test body never ran, when the report shows it: loader stand-in,
    a class/module fixture named in the header, or a fixture-named frame before any
    frame named after the test. The test's name is only ever positive evidence it ran."""
    name = header.split()[1]
    if "unittest.loader._FailedTest" in header:
        return f"unittest could not load {name}"
    entered = frames.index(name) if name in frames else len(frames)
    fixture = name if name in UNITTEST_FIXTURES else next(
        (frame for frame in frames[:entered] if frame in UNITTEST_FIXTURES), None
    )
    return f"unittest failed in {fixture} before the test body" if fixture else None


def _unittest_terminal_failures(output: str) -> list[tuple[int, str, list[str], list[str], str]]:
    """Per framed FAIL or ERROR block: its offset, header, frame functions of the terminal
    traceback unittest itself reported, that traceback's rendering (exception line
    first) and its site: the last test-side frame as ``path:line``, else the
    innermost. Earlier chained segments and captured output after Stdout:/Stderr:
    never count: the failure that ended the test governs."""
    failures: list[tuple[int, str, list[str], list[str], str]] = []
    reading = False
    previous = ""
    offset = 0
    for raw_line in output.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if _rule(previous, "=") and line.startswith(("FAIL: ", "ERROR: ")):
            failures.append((offset, line, [], [], ""))
            reading = True
        elif _rule(previous, "-") and line.startswith("Ran "):
            break  # the report's footer: anything after it is the process's own output
        elif not reading or _rule(line, "=") or _rule(line, "-"):
            pass
        elif stripped in {"Stdout:", "Stderr:"}:
            reading = False
        elif stripped == "Traceback (most recent call last):":
            failures[-1] = (*failures[-1][:2], [], [], "")
        elif line.startswith('  File "'):
            failures[-1][2].append(line.rsplit(", in ", 1)[-1])
            frame = UNITTEST_FRAME.match(line)
            if frame and (is_test_path(frame.group(1)) or not failures[-1][4]):
                failures[-1] = (*failures[-1][:4], f"{frame.group(1)}:{frame.group(2)}")
        elif failures[-1][2] and (failures[-1][3] or (line and not line[0].isspace())):
            failures[-1][3].append(stripped)
        previous = line
        offset += len(raw_line)
    return failures


def _pytest_red(
    output: str, marker: str, arguments: Sequence[object] = ()
) -> tuple[dict[str, object] | None, str]:
    if any(argument in PYTEST_TB_SUPPRESSED for argument in arguments):
        return None, (
            "the recorded command suppresses tracebacks; rerun without "
            "--tb=no/--tb=line so the assertion can be observed"
        )
    lines = output.splitlines()
    counts, summary_start = _pytest_summary(lines)
    if counts is None:
        return None, (
            "pytest did not print its short failure summary; rerun without "
            "summary suppression so the RED is observable"
        )
    if counts["no_tests"] or counts["errors"] or counts["failed"] < 1:
        return None, "pytest failed during collection/setup or executed no tests"
    failure_rules = [
        index
        for index, line in enumerate(lines[:summary_start])
        if line.startswith("=") and " FAILURES " in line
    ]
    if not failure_rules:
        return None, "pytest printed no FAILURES section containing the mapped assertion"
    failures = lines[failure_rules[-1] + 1 : summary_start]
    # pytest prints one header per failed test; any extra header-shaped line
    # is printed text, and the marker can no longer be attributed to a test.
    headers = sum(1 for line in failures if PYTEST_FAILURE_HEADER.match(line))
    if headers != counts["failed"]:
        return None, (
            f"pytest reported {counts['failed']} failed but its FAILURES section "
            f"holds {headers} header-shaped lines; printed header-shaped text "
            "cannot be attributed to a test - remove it or narrow the command"
        )
    renderings, locations = _pytest_terminal_renderings(failures)
    found = next((index for index, block in enumerate(renderings) for line in block if marker in line), None)
    if found is None:
        return None, _not_terminal("pytest", [block[0] for block in renderings if block])
    rendering = renderings[found]
    head, observed = rendering[0], next(line for line in rendering if marker in line)
    refusal = _pre_interface_refusal(head)
    if refusal is not None:
        return None, refusal
    return {
        "quality": "assertion-reached",
        "runner": "pytest",
        "testsExecuted": counts["failed"] + counts["passed"],
        "observedFailure": observed,
        "rendering": rendering,
        "location": locations[found],
    }, ""


def _pytest_summary(lines: list[str]) -> tuple[dict[str, int | bool] | None, int]:
    start = None
    for index, line in enumerate(lines):
        if "short test summary info" in line:
            start = index
    if start is None:
        return None, len(lines)
    for line in lines[start + 1 :]:
        if line.startswith(PYTEST_SUMMARY_RECORDS):
            continue
        text = line.strip().strip("=").strip()
        if not text or not re.search(r" in \d+(?:\.\d+)?s$", text):
            continue
        lowered = text.lower()
        return {
            "failed": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) failed\b", lowered)
            ),
            "passed": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) passed\b", lowered)
            ),
            "errors": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) errors?\b", lowered)
            ),
            "no_tests": "no tests ran" in lowered,
        }, start
    return None, start


def _pytest_terminal_renderings(lines: list[str]) -> tuple[list[list[str]], list[str]]:
    """Per failed test's block, the E-prefixed rendering of its terminal chain
    segment, first line first, and that segment's site: the last test-side frame
    as ``path:line``, else the innermost; earlier segments and captured output
    never count."""
    blocks: list[list[str]] = []
    locations: list[str] = []
    innermost: list[str] = []
    captured = False
    for line in lines:
        # Every header is genuine here: _pytest_red matched the header count already.
        if PYTEST_FAILURE_HEADER.match(line):
            blocks.append([])
            locations.append("")
            innermost.append("")
            captured = False
        elif not blocks:
            continue
        elif PYTEST_CAPTURED_HEADER.match(line):
            captured = True
        elif captured:
            continue
        elif line.startswith(("During handling of the above exception", "The above exception was")):
            blocks[-1] = []
            locations[-1] = innermost[-1] = ""
        elif line.startswith("E "):
            blocks[-1].append(line[1:].strip())
        elif location := PYTEST_LOCATION.match(line):
            innermost[-1] = f"{location.group(1)}:{location.group(2)}"
            if is_test_path(location.group(1)):
                locations[-1] = innermost[-1]
    return blocks, [site or inner for site, inner in zip(locations, innermost)]


def _rule(line: str, character: str) -> bool:
    return len(line) >= 20 and set(line) == {character}


def _recognise(command: Sequence[str]) -> tuple[str | None, Sequence[str]]:
    if not command:
        return None, ()
    executable = PurePosixPath(command[0]).name
    if (
        len(command) >= 3
        and INTERPRETER.match(executable)
        and command[1] == "-m"
        and command[2] in IGNORED_BY_RUNNER
    ):
        return command[2], command[:3]
    if executable in DIRECT_RUNNERS:
        return DIRECT_RUNNERS[executable], command[:1]
    return None, ()


def _ignored_class(runner: str, token: str) -> str | None:
    named = IGNORED_BY_RUNNER.get(runner, {}).get(token)
    if named is not None:
        return named
    return "verbosity" if REPEATED_VERBOSITY.match(token) else None


def _surface(
    runner: str,
    invocation: str,
    arguments: list[str],
    ignored: Sequence[str],
    fallback: str | None,
) -> dict[str, object]:
    return {
        "surfaceSchemaVersion": SURFACE_SCHEMA_VERSION,
        "runner": runner,
        "invocation": invocation,
        "arguments": arguments,
        "ignored": list(ignored),
        "fallbackReason": fallback,
    }
