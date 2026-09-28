"""Advisory check after a valid mapped GREEN: would the item's proof notice its own changed code breaking?

The proof is rerun on scratch copies of the candidate with small breaks on the changed lines it runs (negate a
condition, flip a comparison, drop a statement, return None). A break the proof still passes on is a surviving
mutation: a lead to a missing assertion, not a confirmed gap. The lead's worktree is never written."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import random
import re
import shlex
import shutil
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .command_runner import run
from .repo_identity import CanonicalRoot, RepoIdentity
from .state_store import _git, is_test_path

SITE = Path(__file__).with_name("proof_gaps_site")
MAX_BREAKS, WORKERS, BUDGET_SECONDS, SHARE = 12, 4, 60.0, 0.25
PREFIX = "proof-gap check:"
SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE,
        ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
CHANGE = {"negate": "condition negated", "swap": "comparison flipped", "drop": "statement removed",
          "none": "returns None"}
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def report(identity: RepoIdentity, command: list[str], env: dict[str, str] | None, item: dict[str, object],
           green_tree: str, pass_start: str, others: list[list[str]]) -> tuple[list[str], list[str], list[str]]:
    """(lead, evidence, ran): the lines the lead is shown (the summary and each survivor whose run recorded a
    difference; none when every break was caught or the check did not run), every line for the GREEN run entry
    (adding each survivor with no difference detected), and the changed lines the proof ran."""
    try:
        with tempfile.TemporaryDirectory(prefix="proof-gaps-") as scratch:
            return _report(identity, command, env, item, green_tree, pass_start, others, Path(scratch))
    except (OSError, RuntimeError, ValueError, SyntaxError, KeyError, TypeError) as error:
        return [], [f"{PREFIX} not run ({type(error).__name__}: {error})"], []


def _report(identity, command, env, item, green_tree, pass_start, others, scratch: Path) -> tuple[list[str], list[str], list[str]]:
    deadline = time.monotonic() + BUDGET_SECONDS
    root = Path(identity.root)
    # The proof's own files (a script it runs) are the proof, not the code under test.
    scripts = {str((root / t).resolve().relative_to(root.resolve())) for t in command
               if t.endswith(".py") and (root / t).resolve().is_file() and (root / t).resolve().is_relative_to(root.resolve())}
    since_start = {p: lines for p, lines in _changed(identity, pass_start, green_tree).items() if p not in scripts}
    if not since_start:
        return [], [f"{PREFIX} not run - no production .py line changed since the pass start"], []
    listed = [os.fsdecode(p) for p in _git(identity, "ls-files", "-z", "-c", "-o", "--exclude-standard").split(b"\0") if p]
    files = [p for p in listed if (Path(identity.root) / p).is_file()]
    tests = [p for p in files if is_test_path(p) or p in scripts]

    def observe(name: str, timeout: float = BUDGET_SECONDS, mutation: tuple[str, str] | None = None) -> dict[str, object]:
        """One rerun on its own fresh copy, so no run sees state an earlier run left behind; copying counts against the deadline."""
        copy = _copy(identity, files, scratch / name / "copy")
        if mutation:
            (copy / mutation[0]).write_text(mutation[1], encoding="utf-8")
        return _observe(identity, command, env, copy, scratch / name, since_start, tests,
                        min(timeout, deadline - time.monotonic()))

    base = observe("b1")
    if base["exit"] != 0:
        cause = "hit the time budget" if base["exit"] is None else f"exited {base['exit']}"
        return [], [f"{PREFIX} not run (the proof {cause} on a copy of the candidate)"], []
    ran = base["lines"] & {f"{p}:{n}" for p, lines in since_start.items() for n in lines}
    # Break what this proof owns: changed lines it runs that few other items' GREEN proofs run.
    share = Counter(line for lines in others for line in lines)
    owned = {line for line in ran if share[line] < max(2, SHARE * len(others))}
    targets = {p: {n for n in lines if f"{p}:{n}" in owned} for p, lines in since_start.items()}
    if ran and not owned:
        return [], [f"{PREFIX} not run - other items' GREEN proofs already run every changed line this proof runs"], sorted(ran)
    if not any(targets.values()):
        return [], [f"{PREFIX} not run - the proof ran none of the changed lines in its copy (its interpreter may ignore "
                "PYTHONPATH, or import the checkout through an absolute path or an installed package)"], sorted(ran)
    second = observe("b2")
    if second["exit"] != 0:
        cause = "hit the time budget" if second["exit"] is None else f"exited {second['exit']}; its outcome is not repeatable"
        return [], [f"{PREFIX} not run (a second unchanged run of the proof {cause})"], sorted(ran)
    noise = {(e[0], e[1]) for e in set(base["events"]) ^ set(second["events"])}
    sites = sorted((path, site) for path, lines in targets.items() for site in _sites(identity, path) if site[1] in lines)
    sites = random.Random(str(item.get("id"))).sample(sites, min(MAX_BREAKS, len(sites)))
    timeout = max(5.0, 5 * base["seconds"])  # per break, and never past the budget
    def attempt(site: tuple[str, tuple[str, int, int]]) -> tuple[tuple[str, tuple[str, int, int]], str]:
        if deadline - time.monotonic() < 1:
            return site, "skipped"
        path, where = site
        broken = _mutate((Path(identity.root) / path).read_text(encoding="utf-8"), where)
        seen = observe(f"m{sites.index(site)}", timeout, (path, broken))
        if seen["exit"] is None:  # stopped by its own time limit or the shared budget: proves nothing either way
            return site, "inconclusive"
        if seen["exit"] != 0:
            return site, "caught"
        return site, "survived" if _differs(base, seen, noise) else "quiet"

    with ThreadPoolExecutor(WORKERS) as pool:
        outcomes = list(pool.map(attempt, sites))
    counts = Counter(outcome for _, outcome in outcomes)
    summary = (f"{PREFIX} {shlex.join(command)[-120:]}: {len(sites)} breaks on {len(owned)} changed lines this proof owns: "
               f"{counts['caught']} caught, {counts['survived']} survived")
    extras = [(counts["quiet"], "survived; no difference detected (in the run evidence)"),
              (counts["inconclusive"], "inconclusive (timed out)"), (counts["skipped"], "skipped (time budget)")]
    summary += "".join(f", {n} {text}" for n, text in extras if n)
    lines = {"survived": [], "quiet": []}
    for (path, (kind, line, _)), outcome in outcomes:
        if outcome in lines:
            code = (Path(identity.root) / path).read_text(encoding="utf-8").splitlines()[line - 1].strip()[:80]
            lines[outcome].append(f"{path}:{line} ({CHANGE[kind]}: `{code}`) survived"
                                  + ("" if outcome == "survived" else "; no difference detected"))
    lead = [summary, *lines["survived"]] if counts["caught"] < len(sites) else []
    return lead, [summary, *lines["survived"], *lines["quiet"]], sorted(ran)


def _copy(identity: RepoIdentity, files: list[str], target: Path) -> Path:
    for path in files:
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(identity.root) / path, target / path)  # a link is copied as its target: no write escapes
    return target


def _changed(identity: RepoIdentity, base: str, tree: str) -> dict[str, set[int]]:
    """Production .py lines added or changed from base to tree, by path."""
    out: dict[str, set[int]] = {}
    path = None
    for line in _git(identity, "diff", "-U0", "--no-color", "--no-renames", base, tree, "--", "*.py").decode("utf-8", "replace").splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            path = name[2:] if name.startswith("b/") and not is_test_path(name[2:]) else None
        elif path and (hunk := HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            out.setdefault(path, set()).update(range(start, start + count))
    return out


def _observe(identity, command, env, copy: Path, out: Path, changed: dict[str, set[int]], tests: list[str],
             timeout: float) -> dict[str, object]:
    (out / "tmp").mkdir(parents=True)
    root = str(identity.root)

    def moved(value: str) -> str:
        """A checkout path moved to the copy, unless the copy lacks it (an ignored venv stays where it is)."""
        target = value.replace(root, str(copy))
        return target if os.path.exists(target.split("=")[-1]) else value

    argv = [moved(token) for token in command]
    inherited = env or os.environ
    pythonpath = os.pathsep.join([str(SITE), *(moved(p) for p in inherited.get("PYTHONPATH", "").split(os.pathsep) if p)])
    # A fixed hash seed keeps set and dict iteration order equal across reruns.
    runtime = {**inherited, "PYTHONHASHSEED": "0", "PYTHONPATH": pythonpath, "PROOF_GAPS_OUT": str(out), "PROOF_GAPS_ROOT": str(copy),
               "PROOF_GAPS_FILES": "\n".join(changed), "PROOF_GAPS_TESTS": "\n".join(tests),
               "TMPDIR": str(out / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"}
    begun = time.monotonic()
    _, code, timed_out = run(argv, dataclasses.replace(identity, root=CanonicalRoot(str(copy))), timeout, runtime)
    seen = {"exit": None if timed_out else code, "seconds": time.monotonic() - begun, "lines": set(), "events": []}
    for report_file in sorted(out.glob("*.json")):
        data = json.loads(report_file.read_text(encoding="utf-8"))
        seen["lines"] |= set(data["lines"])
        seen["events"] += [tuple(e) for e in data["events"]]
    return seen


def _sites(identity: RepoIdentity, path: str) -> list[tuple[str, int, int]]:
    found = []
    for node in ast.walk(ast.parse((Path(identity.root) / path).read_text(encoding="utf-8"))):
        where = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
        if isinstance(node, (ast.If, ast.While)):
            found.append(("negate", *where))
        if isinstance(node, ast.Compare) and type(node.ops[0]) in SWAP:
            found.append(("swap", *where))
        if isinstance(node, ast.Return) and node.value is not None and not (isinstance(node.value, ast.Constant) and node.value.value is None):
            found.append(("none", *where))
        if isinstance(node, (ast.Assign, ast.AugAssign)) or (isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant)):
            found.append(("drop", *where))
    return found


def _mutate(source: str, site: tuple[str, int, int]) -> str:
    kind, line, column = site

    class Break(ast.NodeTransformer):
        def generic_visit(self, node: ast.AST) -> ast.AST:
            node = super().generic_visit(node)
            if (getattr(node, "lineno", None), getattr(node, "col_offset", None)) != (line, column):
                return node
            if kind == "negate" and isinstance(node, (ast.If, ast.While)):
                node.test = ast.UnaryOp(ast.Not(), node.test)
            elif kind == "swap" and isinstance(node, ast.Compare):
                node.ops = [SWAP[type(node.ops[0])](), *node.ops[1:]]
            elif kind == "none" and isinstance(node, ast.Return):
                node.value = ast.Constant(None)
            elif kind == "drop" and isinstance(node, (ast.Assign, ast.AugAssign, ast.Expr)):
                return ast.Pass()
            return node

    return ast.unparse(ast.fix_missing_locations(Break().visit(ast.parse(source))))


def _differs(base: dict, seen: dict, noise: set) -> bool:
    """Whether the values and errors the proof received differ from the unchanged run, ignoring run-to-run noise."""
    return [e for e in base["events"] if (e[0], e[1]) not in noise] != [e for e in seen["events"] if (e[0], e[1]) not in noise]

