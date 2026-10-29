"""Probe selection and observed terminal failures for source comparisons."""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

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
    r"^(?:bwrap: |\[Errno \d+\] |\S+: (?:No module named |can't open file )"
    r"|Error(?: \[\w+\])?: Cannot find (?:module|package) "
    r"|\S+: (?:line )?\d+: \S+: (?:command )?not found$"
    r"|(?:\w+\.)*(?:ModuleNotFoundError|ImportError|SyntaxError|IndentationError)\b)"
)
UNITTEST_FIXTURES = frozenset({
    "setUp", "asyncSetUp", "tearDown", "asyncTearDown",
    "setUpClass", "tearDownClass", "setUpModule", "tearDownModule",
})
PYTEST_FAILURE_HEADER = re.compile(r"^_+ (.+?) _+$")
PYTEST_CAPTURED_HEADER = re.compile(r"^-+ Captured .+ -+$")
# pytest closes each traceback frame with `path:line:` (the exception class only
# on the last); unittest frames are `File "..."` lines. Object reprs carry the
# process's addresses, which say nothing about what was observed.
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
PRINTED_CASE = re.compile(r"(?m)^([^\s:][^:\n]*): (.*)$")
NATIVE_RUNNERS = frozenset({"unittest", "pytest"})
# unittest's own report lines: a verbose status line, the equals rule opening a FAIL/ERROR
# section, or the dash rule before its `Ran N tests` footer; any one survives a cut report.
UNITTEST_REPORT = re.compile(r"(?m)^\S+ \([^\n]+?\) \.\.\. (?:ok|FAIL|ERROR|skipped[^\n]*)$|^={70}\n(?:FAIL|ERROR): |^-{70}\nRan \d+ tests? in ")
# pytest's session header (printed first), short-summary header or terminal summary line identifies its report.
PYTEST_REPORT = re.compile(r"(?m)^=+ test session starts =+$"
                           r"|^=+ short test summary info =+$"
                           r"|^(?:=+ )?\d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed)\b.* in \d+(?:\.\d+)?s\b")
# The interpreter's own diagnostic: header, indented frames, then the exception with its message
# continuation lines: indented text and an assertion's diff lines (`- `, `+ `, `? `), which a blank
# line may separate.
PYTHON_TRACEBACK = re.compile(r"(?m)^Traceback \(most recent call last\):\n(?:[ \t].*\n)*.*"
                              r"(?:\n(?:[-+?] .*|[ \t]+\S.*)|\n(?=\n[-+?] ))*$")
SELECTION_OPTIONS = frozenset({"-k", "-m", "--deselect"})
# Where a runner's report starts (a unittest status line, pytest's progress line, session header, node
# id or failure banner) and ends (unittest's `Ran N tests` footer and result line, or pytest's final
# summary); outside it is the command's own. A bare progress line, a 70-character rule or a bare
# `name (dotted.id)` line, which printed output can also be, starts a report only when the line after
# its run is report structure: a start, a status line, a FAIL/ERROR header or a footer.
REPORT_START = re.compile(r"^\S*[^\s:] \([^\n]+?\) \.\.\. |^[.FEsxXu]+ +\[ *\d+%\]$"
                          r"|^=+ (?:test session starts|FAILURES|ERRORS) =+$|^\S+::\S*[^\s:] ")
AMBIGUOUS_START = re.compile(r"^\w+ \(\w+(?:\.\w+)+\)$|^[.FEsxXu]+$|^={70}$|^-{70}$")
REPORT_STATUS = re.compile(r"(?:FAIL|ERROR): |.* \.\.\. (?:ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b")
UNITTEST_RESULT = re.compile(r"^(?:OK|FAILED)(?: \([^)]*\))?$")
PYTEST_FINAL = re.compile(r"^(?:=+ )?\d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed)\b.* in \d+(?:\.\d+)?s\b")


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
) -> tuple[list[str], bool, list[str], list[str]]:
    """The test targets a unittest or pytest surface names, whether it is a
    discover run, the ambiguous path tokens, and the -k, -m and --deselect
    expressions that narrow the selection further.

    Option values are skipped by each runner's value-taking option table; a
    pytest bare word or number that names nothing under ``root`` is an unknown
    option's value, not a target. Every path-shaped pytest token after the first
    unknown option (a plugin's) may be one of its values, so they are returned as
    ambiguous: resolved fail-closed by callers, never a named target. A discover
    run with no start directory targets ``.``.
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
    selections: list[str] = []
    pending_start = False
    pending_value: str | None = None
    after_unknown_option = False
    for token in tokens[1 if discover else 0:]:
        if pending_value:
            if pending_start:
                targets.append(token)
            elif pending_value in SELECTION_OPTIONS:
                selections.append(f"{pending_value} {token}")
            pending_start, pending_value = False, None
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            name, separator, inline = token.partition("=")
            # The separator, not the value's truthiness, says a value was given:
            # -k= carries an empty value and does not take the next token.
            has_value = bool(separator)
            known_cluster = False
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
            # After cluster normalization, so `-kfast` and `-qk fast` report as `-k fast`.
            if name in value_options:
                if has_value:
                    if discover and name in UNITTEST_START_OPTIONS:
                        targets.append(inline)
                    elif name in SELECTION_OPTIONS:
                        selections.append(f"{name} {inline}")
                else:
                    pending_value = name
                    pending_start = discover and name in UNITTEST_START_OPTIONS
            continue
        if runner == "pytest" and not (
            "/" in token or "\\" in token or "::" in token
            or token.endswith(".py") or (top / token).exists()
        ):
            continue
        (ambiguous if after_unknown_option else targets).append(token)
    if not targets and discover:
        targets.append(".")
    return targets, discover, ambiguous, selections


def narrowing(surface: Mapping[str, object], root: object) -> str | None:
    """How a test-runner command narrows below whole modules: -k, -m and --deselect
    expressions, test ids and unittest classes. Tests outside that selection are not compared."""
    if surface.get("runner") not in NATIVE_RUNNERS:
        return None
    targets, discover, _, selections = proof_targets(surface, root)
    top = Path(str(root))
    below = [target for target in targets if "::" in target or (
        surface["runner"] == "unittest" and not discover and "/" not in target and not target.endswith(".py")
        and _below_module(target.split("."), top))]
    return ", ".join([*selections, *below]) or None


def _below_module(parts: list[str], top: Path) -> bool:
    """Whether a dotted unittest name continues past the module or package it resolves to."""
    return next((size < len(parts) for size in range(len(parts), 0, -1)
                 if top.joinpath(*parts[:size]).with_suffix(".py").is_file() or top.joinpath(*parts[:size]).is_dir()), False)


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


def evaluate_red(surface: Mapping[str, object], output: str, cases: Mapping[str, Mapping[str, str]]) -> tuple[dict[str, object] | None, str]:
    runner = surface.get("runner")
    output = ANSI_ESCAPE.sub("", output)
    lines = [line for line in output.splitlines() if line.strip()]
    diagnostic = _final_diagnostic(lines)
    if runner not in {"unittest", "pytest"} and PRE_INTERFACE_FAILURE.match(diagnostic):
        return None, "the operation failed before reaching the production Interface: " + diagnostic
    if runner == "unittest":
        result = _with_observation(_unittest_red(output))
    elif runner == "pytest":
        arguments = surface.get("arguments")
        result = _with_observation(_pytest_red(output, arguments if isinstance(arguments, list) else ()))
    else:
        result = {"quality": "failure-observed", "reach": "unresolved", "runner": str(runner),
                  "observedFailure": diagnostic, "observation": [diagnostic]}, ""
    unreached = [name for name, case in cases.items() if case["outcome"] == "error"]
    if result[0] and unreached and not any(case["outcome"] == "failed" for case in cases.values()):
        return None, "every failing case stopped before the production Interface: " + ", ".join(unreached[:3])
    return result


def _with_observation(
    result: tuple[dict[str, object] | None, str]
) -> tuple[dict[str, object] | None, str]:
    proof, _ = result
    if proof is None:
        return result
    rendering = proof.pop("rendering")
    return {**proof, "observation": [OBJECT_ADDRESS.sub("", line) for line in rendering if line]}, ""


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


def _unittest_red(output: str) -> tuple[dict[str, object] | None, str]:
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
    report_counts = tuple(sum(header.startswith(kind) for header, _, _ in failures)
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
    rendering = [line for _, _, block in failures for line in block]
    if not rendering:
        return None, "unittest reported no terminal failure"
    return {
        "quality": "failure-observed",
        "runner": "unittest",
        "testsExecuted": int(ran.group(1)),
        "observedFailure": rendering[0],
        "rendering": rendering,
    }, ""


def _unittest_unreached(header: str, frames: list[str], exception: list[str]) -> bool:
    """Whether the block's case stopped before the production code: the loader stand-in, a fixture named in the
    header or framed before the test's own frame, no exception, or an import or syntax exception. The test's name
    is only ever positive evidence it ran."""
    name = header.split()[1]
    entered = frames.index(name) if name in frames else len(frames)
    return ("unittest.loader._FailedTest" in header or not UNITTEST_FIXTURES.isdisjoint([name, *frames[:entered]])
            or not exception or bool(PRE_INTERFACE_FAILURE.match(exception[0])))


def _unittest_terminal_failures(output: str) -> list[tuple[str, list[str], list[str]]]:
    """Terminal traceback headers, frame functions and exceptions, excluding captured output."""
    failures: list[tuple[str, list[str], list[str]]] = []
    reading = False
    previous = ""
    for raw_line in output.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if _rule(previous, "=") and line.startswith(("FAIL: ", "ERROR: ")):
            failures.append((line, [], []))
            reading = True
        elif _rule(previous, "-") and line.startswith("Ran "):
            break  # the report's footer: anything after it is the process's own output
        elif not reading or _rule(line, "=") or _rule(line, "-"):
            pass
        elif stripped in {"Stdout:", "Stderr:"}:
            reading = False
        elif stripped == "Traceback (most recent call last):":
            failures[-1] = (failures[-1][0], [], [])
        elif line.startswith('  File "'):
            failures[-1][1].append(line.rsplit(", in ", 1)[-1])
        elif failures[-1][1] and (failures[-1][2] or (line and not line[0].isspace())):
            failures[-1][2].append(stripped)
        previous = line
    return failures


def case_results(surface: Mapping[str, object], output: str) -> dict[str, dict[str, str]]:
    """Attribute native terminal results; missing names are never inferred as passes. A command
    the runner does not recognise is attributed by the native report its output carries, else by
    its printed `name: result` lines outside interpreter tracebacks. A failing case reads `error` when a failure
    printed for it (a unittest method's subtests included) stopped before the production code; a pytest failure
    the report does not attribute to its test keeps pytest's own result."""
    cases = {}
    clean = ANSI_ESCAPE.sub("", output)
    runner = native_runner(surface, clean)
    if runner == "unittest":
        for name, status in re.findall(r"(?m)^(\S+ \([^\n]+?\)) \.\.\. (ok|FAIL|ERROR|skipped[^\n]*)$", clean):
            cases[name] = {"outcome": "passed" if status == "ok" else "failed" if status == "FAIL" else "error" if status == "ERROR" else "skipped"}
        failures = _unittest_terminal_failures(clean)
        # A method's own name ends at its first `)`; its subtests' names extend it.
        unreached = {header.split(": ", 1)[1].split(")")[0] for header, frames, assertion in failures
                     if _unittest_unreached(header, frames, assertion)}
        for header, frames, assertion in failures:
            name = header.split(": ", 1)[1]
            cases[name] = {"outcome": "error" if name.split(")")[0] in unreached else "failed", "assertion": "\n".join(assertion)}
            if re.fullmatch(r"\S+ \([^()\n]+\)", name) and name.split()[0] in frames:
                cases[name]["execution"] = "stopped"  # failed in its own body: later statements did not run
    elif runner == "pytest":
        # A test collected from another file's definition is reported `id <- file`; an ERROR is never overwritten.
        progress = re.findall(r"(?m)^(\S+::\S+)(?: <- \S+)? (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", clean)
        for name, status in progress:
            if cases.get(name, {}).get("outcome") != "error":
                cases[name] = {"outcome": {"PASSED": "passed", "FAILED": "failed", "ERROR": "error"}.get(status, "skipped")}
        summary = clean.rsplit("short test summary info", 1)[-1] if "short test summary info" in clean else ""
        # A failing subtest leaves its test PASSED in the progress lines; its SUBFAILED record fails it.
        # A subtest label is an optional `[message]` then optional ` (parameters)`; either may contain
        # `)`, `] ` or ` - `, so the node id (a `::` id or a collected file) anchors the match.
        for kind, name, assertion in re.findall(
                r"(?m)^(?:SUB)?(FAILED|ERROR)(?:\[.*?\])?(?: ?\(.*?\))? (\S+::\S+|\S+\.py)(?: - (.*))?$", summary):
            known = cases.get(name, {}).get("assertion", "") if cases.get(name, {}).get("outcome") == kind.lower() else ""
            joined = "\n".join(dict.fromkeys(filter(None, [*known.split("\n"), assertion])))
            # A setup or teardown ERROR beside the test's own FAILED leaves it unreached in either summary order.
            outcome = "error" if "error" in (kind.lower(), cases.get(name, {}).get("outcome")) else kind.lower()
            cases[name] = {"outcome": outcome, **({"assertion": joined} if joined else {})}
        # pytest prints one FAILURES block per failing report, in the order of its summary records and of its progress
        # lines; a block is attributed through such a list naming each block's test in turn, and a case reads error when
        # one of its blocks ends in an import or syntax error. A report no such list attributes keeps the original's
        # decision for every failed case: unreached when pytest ran it natively and its first E line is.
        def heading(name: str) -> str:  # `tests/a.py::Class::test[x::y]` heads its block `Class.test[x::y]`
            test, bracket, parameters = name.partition("::")[2].partition("[")
            return (test.replace("::", ".") + bracket + parameters).partition(" ")[0]
        blocks = _pytest_failures(clean.rsplit("short test summary info", 1)[0].splitlines())
        lists = (re.findall(r"(?m)^(?:SUB)?FAILED(?:\[.*?\])?(?: ?\(.*?\))? (\S+::\S+|\S+\.py)", summary),
                 re.findall(r"(?m)^(\S+::\S+)(?: <- \S+)? (?:SUB)?FAILED\b", re.split(r"(?m)^=+ (?:ERRORS|FAILURES) =+$", clean)[0]))
        names = next((names for names in lists if len(names) == len(blocks)
                      and all(header.partition(" ")[0] == heading(name) for name, (header, _, _) in zip(names, blocks))), [])
        first = next((lines[0].strip() for _, _, lines in blocks if lines), "")
        unattributed = not names and surface.get("runner") == "pytest" and PRE_INTERFACE_FAILURE.match(first)
        for name, case in cases.items():
            if case["outcome"] == "failed" and (unattributed or any(
                    PRE_INTERFACE_FAILURE.match(exception) for listed, (_, exception, _) in zip(names, blocks) if listed == name)):
                cases[name]["outcome"] = "error"
    else:
        # An operation probe names its cases itself: one `name: result` line each.
        for name, result in PRINTED_CASE.findall(PYTHON_TRACEBACK.sub("", clean)):
            # A repeated name cannot carry two results; neither may stand as proof.
            repeated = name.strip() in cases
            cases[name.strip()] = {"outcome": "printed", "result": "unverified - repeated printed case name" if repeated else result.strip()}
    return cases


def native_runner(surface: Mapping[str, object], output: str) -> str | None:
    """The test runner whose report `output` is: the recognised command's, else the unittest or
    pytest report a wrapping command (a shell, env or timeout) printed."""
    if surface.get("runner") in NATIVE_RUNNERS:
        return str(surface["runner"])
    clean = ANSI_ESCAPE.sub("", output)
    return "unittest" if UNITTEST_REPORT.search(clean) else "pytest" if PYTEST_REPORT.search(clean) else None


def outside_report(output: str) -> list[str]:
    """The lines of `output` outside every test runner report it carries. Each report runs from its
    first line to its footer, and every line after the last footer is the command's own; only output
    with no footer at all lets its first report, cut before its footer, run to the end."""
    lines = ANSI_ESCAPE.sub("", output).splitlines()
    footers = [index for index, line in enumerate(lines) if PYTEST_FINAL.match(line) or UNITTEST_RAN.match(line)]
    last = footers[-1] if footers else len(lines)
    outside: list[str] = []
    index = 0
    while index < len(lines):
        if index > last or not _starts_report(lines, index):
            outside.append(lines[index])
            index += 1
            continue
        while index < len(lines) and not (PYTEST_FINAL.match(lines[index]) or UNITTEST_RAN.match(lines[index])):
            index += 1
        if index < len(lines) and UNITTEST_RAN.match(lines[index]):
            following = next((position for position in range(index + 1, len(lines)) if lines[position].strip()), None)
            index = following if following is not None and UNITTEST_RESULT.match(lines[following]) else index
        index += 1
    return outside


def _starts_report(lines: list[str], index: int) -> bool:
    if not AMBIGUOUS_START.match(lines[index]):
        return bool(REPORT_START.match(lines[index]))
    following = next((line for line in lines[index + 1:] if not AMBIGUOUS_START.match(line)), "")
    return any(pattern.match(following) for pattern in (REPORT_START, REPORT_STATUS, UNITTEST_RAN, PYTEST_FINAL))


def _pytest_red(
    output: str, arguments: Sequence[object] = ()
) -> tuple[dict[str, object] | None, str]:
    if any(argument in PYTEST_TB_SUPPRESSED for argument in arguments):
        return None, (
            "the recorded command suppresses tracebacks; rerun without "
            "--tb=no/--tb=line so the assertion can be observed"
        )
    counts = _pytest_summary(output.splitlines())
    if counts is None:
        return None, (
            "pytest did not print its short failure summary; rerun without "
            "summary suppression so the failure is observable"
        )
    if counts["no_tests"] or counts["failed"] < 1:
        return None, "pytest failed during collection/setup or executed no tests"
    # pytest prints one header per failed test; any extra header-shaped line
    # is printed text, and the marker can no longer be attributed to a test.
    blocks = _pytest_failures(output.rsplit("short test summary info", 1)[0].splitlines())
    if len(blocks) != counts["failed"]:
        return None, (
            f"pytest reported {counts['failed']} failed but its FAILURES section "
            f"holds {len(blocks)} header-shaped lines; printed header-shaped text "
            "cannot be attributed to a test - remove it or narrow the command"
        )
    rendering = [line.strip() for _, _, block in blocks for line in block]
    if not rendering:
        return None, "pytest reported no terminal failure"
    if all(not exception or PRE_INTERFACE_FAILURE.match(exception) for _, exception, _ in blocks):
        return None, "no failure reached the production Interface: " + rendering[0]
    return {
        "quality": "failure-observed",
        "runner": "pytest",
        "testsExecuted": counts["failed"] + counts["passed"],
        "observedFailure": rendering[0],
        "rendering": rendering,
    }, ""


def _pytest_summary(lines: list[str]) -> dict[str, int | bool] | None:
    start = None
    for index, line in enumerate(lines):
        if "short test summary info" in line:
            start = index
    if start is None:
        return None
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
            "no_tests": "no tests ran" in lowered,
        }
    return None


def _pytest_failures(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """Each block of the last FAILURES section: its header, its exception (the least-indented E line, below a
    SyntaxError's location and source) and the E lines of its last chained exception, excluding captured output."""
    rules = [index for index, line in enumerate(lines) if line.startswith("=") and " FAILURES " in line]
    blocks: list[tuple[str, list[str]]] = []
    captured = False
    for line in lines[rules[-1] + 1:] if rules else []:
        if header := PYTEST_FAILURE_HEADER.match(line):
            blocks.append((header.group(1), []))
            captured = False
        elif not blocks:
            continue
        elif PYTEST_CAPTURED_HEADER.match(line):
            captured = True
        elif captured:
            continue
        elif line.startswith(("During handling of the above exception", "The above exception was")):
            blocks[-1][1].clear()
        elif line.startswith("E "):
            blocks[-1][1].append(line[1:])
    return [(header, min(lines, key=lambda line: len(line) - len(line.lstrip()), default="").strip(), lines) for header, lines in blocks]


def _rule(line: str, character: str) -> bool:
    return len(line) >= 20 and set(line) == {character}


def python_entry(command: Sequence[str]) -> tuple[str, str, int] | None:
    """Locate Python's script, module or inline entry after interpreter options."""
    if not command or not INTERPRETER.fullmatch(PurePosixPath(command[0]).name):
        return None
    index = 1
    while index < len(command):
        token = command[index]
        index += 1
        if token == "--":
            return ("script", command[index], index + 1) if index < len(command) else None
        if token == "-":
            return None
        if not token.startswith("-"):
            return "script", token, index
        if token.startswith("--"):
            index += token == "--check-hash-based-pycs"
            continue
        for offset, option in enumerate(token[1:], 2):
            if option in "cmWX":
                value = token[offset:]
                if not value and index < len(command):
                    value = command[index]
                    index += 1
                if option in "cm":
                    return option, value, index
                break
    return None


def _recognise(command: Sequence[str]) -> tuple[str | None, Sequence[str]]:
    if not command:
        return None, ()
    executable = PurePosixPath(command[0]).name
    entry = python_entry(command)
    if entry and entry[0] == "m" and entry[1] in IGNORED_BY_RUNNER:
        return entry[1], command[:entry[2]]
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
