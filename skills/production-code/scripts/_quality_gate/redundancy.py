from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from itertools import combinations

from .findings import (
    Finding, RULE_DUPLICATE_BASELINE, RULE_DUPLICATE_BLOCK, RULE_DUPLICATE_SYMBOL,
    RULE_OWNER_PRODUCTION, RULE_OWNER_TEST,
    anchor, finding_id, pass_condition,
)
from .snapshot import BASELINE_ROLES, EvaluationSnapshot
from .symbols import canonical_lines, extract_symbols

# Shortest canonical implementation any exact rule reports, for a complete
# symbol as much as for a contiguous block. Pinned by the checked-in corpus
# calibration in references/duplicate-calibration.md, which measured that a
# shorter bound reports two-line test lifecycle boilerplate whose ownership
# belongs to #77. It is a warning threshold, never an approved blocker.
MIN_REGION_LINES = 6

DUPLICATE_ACTION = "Keep one implementation and delete the copies, or call the surviving owner instead of repeating it."

DUPLICATE_PASS_CONDITION = pass_condition(
    "duplicate-absent",
    ("content anchor of every named region", "complete exact-duplicate indexing"),
    "at most one region carries each implementation, with exact indexing complete",
)


def find_exact_duplicates(snapshot: EvaluationSnapshot) -> tuple[list[Finding], list[Finding]]:
    """The exact-duplication rules, as (one state per rule, one per duplicate).

    Exactness compares canonical implementation bytes, so identifiers, literals,
    operators and control flow all discriminate. Regions never cross a hunk or
    symbol boundary, a scope the gate could not read is reported incomplete
    rather than passed, and each duplicate is its own finding while the per-rule
    state findings carry the projection they were evaluated under.
    """
    scans, gaps = _scan(snapshot)
    streams = snapshot.gap_streams()
    # Unattributed hunks, unmeasured paths, and capture failures each hide
    # regions these hunk-reading rules would have matched.
    gaps += list(streams["attribution"] + streams["measurement"] + streams["capture"])
    symbols = [region for scan in scans for region in scan["symbols"]]
    counts = Counter(region["fingerprint"] for region in symbols)
    # One occurrence, one defect: a symbol repeated among the additions leaves
    # block scope, so no copy is reported under two rule IDs.
    blocks = _blocks(scans, {
        (region["path"], number)
        for region in symbols for number in region["lines"]
        if counts[region["fingerprint"]] > 1
    })
    # Only the baseline rule reads owners, and an unread owner can hide one
    # only while there is a candidate to match against it.
    owner = streams["baseline"] + streams["baseline_roles"] + streams["baseline_scope"]
    owner_gaps = list(owner) if symbols or blocks else []
    # A body copied under a different name is still a copy of the owner's
    # implementation, so each added symbol also offers its body alone as a
    # baseline candidate; the symbol wins when both match.
    bodies = [region for scan in scans for region in scan["bodies"]]
    retained = _retained_baseline(snapshot, symbols + blocks + bodies, owner_gaps)
    # A copy whose owner is still retained belongs to the baseline rule, and a
    # block inside a retained symbol is that same copy seen twice.
    owned = {(region["path"], number) for region in symbols
             if region["fingerprint"] in retained for number in region["lines"]}
    blocks = [region for region in blocks if (region["path"], region["displayLine"]) not in owned]
    states: list[Finding] = []
    duplicates: list[Finding] = []
    for rule_id, regions, rule_gaps in (
        (RULE_DUPLICATE_SYMBOL, [r for r in symbols if r["fingerprint"] not in retained], gaps),
        (RULE_DUPLICATE_BLOCK, [r for r in blocks if r["fingerprint"] not in retained], gaps),
        (RULE_DUPLICATE_BASELINE,
         [r for r in symbols + blocks if r["fingerprint"] in retained]
         + [r for r in bodies if r["fingerprint"] in retained and r["owner"] not in retained]
         + list(retained.values()),
         gaps + owner_gaps),
    ):
        groups = _groups(regions)
        states.append(_finding(
            rule_id, snapshot, groups, rule_gaps,
            (snapshot.base_identity, snapshot.candidate_identity),
            {"scope": "evaluation", "fileCount": len(snapshot.entries)},
        ))
        duplicates.extend(_finding(
            rule_id, snapshot, [group], [], (str(group["contentAnchor"]),),
            {"scope": "duplicate", "contentAnchor": group["contentAnchor"]},
        ) for group in groups)
    return states, duplicates


def _retained_baseline(
    snapshot: EvaluationSnapshot,
    added: list[dict[str, object]],
    gaps: list[str],
) -> dict[str, dict[str, object]]:
    """Base-tree owners of added implementations the candidate still carries,
    keyed by the fingerprint the added copy shares.

    Retention follows the implementation, not the path: a rename is tracked to
    where it landed and a deleted or edited-past-the-anchor owner holds nothing,
    so copy-then-delete, move, and extraction all clear this rule while a
    surviving second copy keeps it.
    """
    wanted = {
        str(region["fingerprint"]): (str(region["role"]), str(region["language"]), str(region["body"]))
        for region in added
    }
    heads = {body.split("\n", 1)[0] for _, _, body in wanted.values()}
    owners: dict[str, dict[str, object]] = {}
    for base in snapshot.baseline:
        # Canonicalization only drops blank and comment lines, so a copied
        # body's first canonical line survives verbatim in its owner's text.
        if base.text is None or not any(head in base.text for head in heads):
            continue
        canonical = canonical_lines(base.text, base.language)
        if canonical is None:
            # This file carries a copied body's first line and could not be
            # read. Skipping it quietly would report the copy as unique.
            gaps.append(f"{base.path}: exact duplicate owner is unreadable {base.language}")
            continue
        path = snapshot.renamed_to.get(base.path, base.path)
        entry = snapshot.entry(path)
        # Retention is judged in the same canonical form as the match, or
        # adding one comment to the owner would erase a live duplicate. An
        # unchanged path is its own candidate text; a changed one is reread.
        current = canonical if entry is None else canonical_lines(entry.current_text or "", base.language)
        anchored = "\n" + "\n".join(canonical.values()) + "\n"
        held = "\n" + "\n".join((current or {}).values()) + "\n"
        for fingerprint, (role, language, body) in wanted.items():
            # The base must own the anchor and the candidate must still carry
            # it; newline-anchored so a body can only match whole lines.
            if fingerprint in owners or (role, language) != (base.role, base.language):
                continue
            if f"\n{body}\n" not in anchored or f"\n{body}\n" not in held:
                continue
            # held joins the canonical lines in order behind one leading
            # separator, so newlines before the match count the lines above it.
            found_at = held[: held.index(f"\n{body}\n")].count("\n")
            owners[fingerprint] = {
                "fingerprint": fingerprint, "path": path, "role": base.role,
                "language": base.language, "displayLine": list(current or {})[found_at],
                "evidenceRole": "retained-baseline", "lines": (), "body": body,
            }
    return owners


def _scan(snapshot: EvaluationSnapshot) -> tuple[list[dict[str, object]], list[str]]:
    """One canonicalized read per changed hunk, and the scopes it could not read."""
    scans, gaps = [], []
    for entry in snapshot.role_entries(*BASELINE_ROLES):
        language = entry.classification.language
        text = entry.current_text or ""
        canonical = canonical_lines(text, language)
        extents = _extents(text) if canonical is not None else None
        reason = "has no tokenizer for" if canonical is None else "could not parse" if extents is None else ""
        if reason:
            # Guessing a comment, a string interior, or a symbol edge would let
            # two different regions canonicalize alike, so the rule says instead
            # that it did not read this file.
            gaps.append(f"{entry.path}: exact duplicate analysis {reason} this {language}")
            continue
        symbols = extract_symbols(entry.path, text, language)
        # Every symbol edge, opening and closing: a block running out of one
        # symbol into the next would span a boundary the contract forbids.
        edges = {symbol.line for symbol in symbols} | {
            symbol.line + len(symbol.content.splitlines()) for symbol in symbols}
        role = entry.classification.role
        for hunk in entry.hunks:
            added = sorted({number for number, _ in hunk.added})
            scans.append({
                "path": entry.path, "role": role, "language": language, "added": added,
                "canonical": canonical, "starts": edges,
                **_symbol_regions(entry.path, role, language, symbols, set(added), canonical, extents),
            })
    return scans, gaps


def _extents(text: str) -> dict[int, tuple[int, int]] | None:
    """Each definition's real first line and the first line of its suite, or
    `None` when the file does not parse.

    A decorator belongs to the implementation it decorates, a signature can
    span lines, and a suite opening with a decorated definition starts at that
    decorator. Only the parser knows any of those boundaries.
    """
    try:
        tree = ast.parse(text)
    # None, not {}: a definition-free file is read and empty, an unparseable
    # one has no trustworthy boundary at all.
    except (SyntaxError, UnicodeError, ValueError):
        return None
    defined = [node for node in ast.walk(tree)
               if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)) and node.body]
    starts = {node.lineno: min([item.lineno for item in node.decorator_list] + [node.lineno]) for node in defined}
    return {node.lineno: (starts[node.lineno], starts.get(node.body[0].lineno, node.body[0].lineno))
            for node in defined}


def _symbol_regions(path: str, role: str, language: str, symbols: list, added: set[int], canonical: dict[int, str], extents: dict[int, tuple[int, int]]) -> dict[str, list[dict[str, object]]]:
    """Complete symbol bodies this one hunk added in full, and each body alone.

    A hunk that edited part of an existing helper did not add it, and joining
    hunks to complete a symbol is forbidden, so partial extents are not
    candidates. The body-only region is a baseline candidate only: the same
    implementation under a different name is still a copy of it.
    """
    regions: list[dict[str, object]] = []
    bodies: list[dict[str, object]] = []
    for symbol in symbols:
        start, suite = extents.get(symbol.line, (symbol.line, symbol.line + 1))
        extent = range(start, symbol.line + len(symbol.content.splitlines()))
        if not all(number in added for number in extent):
            continue
        body = "\n".join(canonical[number] for number in extent if number in canonical)
        if len(body.splitlines()) < MIN_REGION_LINES:
            continue
        fingerprint = anchor("symbol", role, language, body)
        regions.append({
            "fingerprint": fingerprint, "path": path, "role": role, "language": language,
            "displayLine": start, "lines": tuple(extent), "evidenceRole": "duplicate", "body": body,
        })
        # From the suite: hashing a wrapped signature into the body would hide
        # a copy whose only difference is how its parameters are wrapped.
        inner = "\n".join(canonical[number] for number in extent if number in canonical and number >= suite)
        if len(inner.splitlines()) >= MIN_REGION_LINES:
            bodies.append({
                "fingerprint": anchor("block", role, language, inner), "path": path, "role": role,
                "language": language, "displayLine": suite, "lines": (),
                "evidenceRole": "duplicate", "body": inner, "owner": fingerprint,
            })
    return {"symbols": regions, "bodies": bodies}


def _blocks(scans: list[dict[str, object]], consumed: set[tuple[str, int]]) -> list[dict[str, object]]:
    """Contiguous added blocks repeated at least twice, collapsed to their
    longest common extent. A scope is one uninterrupted added run inside one
    hunk, cut at every symbol boundary and at every line a symbol duplicate
    already owns, so no block spans two hunks, two symbols, or a named copy.
    """
    scopes = _scopes(scans, consumed)
    windows: dict[str, list[tuple[int, int]]] = {}
    for index, (scan, lines) in enumerate(scopes):
        for start in range(len(lines) - MIN_REGION_LINES + 1):
            windows.setdefault(_key(scan, lines, start, MIN_REGION_LINES), []).append((index, start))

    regions, covered = [], set()
    for index, (scan, lines) in enumerate(scopes):
        for start in range(len(lines) - MIN_REGION_LINES + 1):
            if (index, start) in covered:
                continue
            positions = _disjoint(windows[_key(scan, lines, start, MIN_REGION_LINES)], covered)
            if len(positions) < 2:
                continue
            length = MIN_REGION_LINES
            while _extends(scopes, positions, length):
                length += 1
            for position, offset in positions:
                found, found_lines = scopes[position]
                covered.update((position, offset + step) for step in range(length))
                regions.append({
                    "fingerprint": anchor("block", str(found["role"]), str(found["language"]), _body(found_lines, offset, length)),
                    "path": found["path"], "role": found["role"], "language": found["language"],
                    "displayLine": found_lines[offset][0], "evidenceRole": "duplicate",
                    "lines": tuple(number for number, _ in found_lines[offset : offset + length]),
                    "body": _body(found_lines, offset, length),
                })
    return regions


def _scopes(scans: list[dict[str, object]], consumed: set[tuple[str, int]]) -> list[tuple[dict[str, object], list[tuple[int, str]]]]:
    scopes = []
    for scan in scans:
        canonical, run, previous = scan["canonical"], [], None
        for number in scan["added"]:
            cut = previous is not None and (number != previous + 1 or number in scan["starts"])
            owned = (scan["path"], number) in consumed
            previous = number
            if (cut or owned) and run:
                scopes.append((scan, run))
                run = []
            # A blank or comment line carries no code, so it breaks no run.
            if not owned and number in canonical:
                run.append((number, canonical[number]))
        if run:
            scopes.append((scan, run))
    return scopes


def _key(scan: dict[str, object], lines: list[tuple[int, str]], start: int, length: int) -> str:
    # Role and language join the content: the same bytes in a production file
    # and in a test are not one debt, and each group must share one anchor.
    return "\x1f".join((str(scan["role"]), str(scan["language"]), _body(lines, start, length)))


def _body(lines: list[tuple[int, str]], start: int, length: int) -> str:
    return "\n".join(text for _, text in lines[start : start + length])


def _disjoint(positions: list[tuple[int, int]], covered: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Occurrences that are still free and do not overlap each other."""
    kept: list[tuple[int, int]] = []
    for index, start in sorted(positions):
        if (index, start) in covered or (kept and kept[-1][0] == index and start < kept[-1][1] + MIN_REGION_LINES):
            continue
        kept.append((index, start))
    return kept


def _extends(
    scopes: list[tuple[dict[str, object], list[tuple[int, str]]]],
    positions: list[tuple[int, int]],
    length: int,
) -> bool:
    """Whether every occurrence carries the same next line, still disjointly."""
    following = set()
    for index, start in positions:
        lines = scopes[index][1]
        if start + length >= len(lines):
            return False
        following.add(lines[start + length][1])
    overlaps = any(
        left[0] == right[0] and left[1] + length >= right[1]
        for left, right in zip(positions, positions[1:])
    )
    return len(following) == 1 and not overlaps


def _groups(regions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Regions sharing canonical bytes, one entry per duplicated implementation."""
    # Keyed by where it physically sits: a symbol's body and the block over it
    # are one occurrence, named once.
    collected: dict[str, dict[tuple[str, int], dict[str, object]]] = {}
    for region in regions:
        key = (str(region["path"]), int(region["displayLine"]))
        collected.setdefault(str(region["fingerprint"]), {}).setdefault(key, region)
    return [
        {
            "contentAnchor": fingerprint,
            "regions": [
                {
                    "path": member["path"], "role": member["role"], "language": member["language"],
                    "displayLine": member["displayLine"], "contentAnchor": fingerprint,
                    "evidenceRole": member["evidenceRole"],
                }
                for member in sorted(found.values(), key=lambda item: (item["role"], item["path"], item["displayLine"]))
            ],
        }
        for fingerprint, found in sorted(collected.items())
        if len(found) > 1
    ]


def _finding(
    rule_id: str,
    snapshot: EvaluationSnapshot,
    groups: list[dict[str, object]],
    gaps: list[str],
    identity: tuple[str, ...],
    region: dict[str, object],
) -> Finding:
    """One warning-only exact finding: a single duplicate, or a rule's verdict.

    A duplicate is identified by its fingerprint alone, so an insertion above a
    region, a rename, a rebase, or another duplicate elsewhere moves nothing.
    """
    return Finding(
        rule_id=rule_id,
        severity="warning",
        status="incomplete" if gaps else "finding" if groups else "passed",
        # Schema v2: an incomplete rule is unknown whatever it did find, and an
        # active warning-only rule keeps its intrinsic check passed.
        passed=None if gaps else True,
        identity=identity,
        region={"changedScope": snapshot.changed_scope, **region,
                "regions": [region for group in groups for region in group["regions"]]},
        evidence={"duplicates": [
            {"findingId": finding_id(rule_id, (str(group["contentAnchor"]),)), **group} for group in groups
        ]},
        action=DUPLICATE_ACTION,
        pass_condition=DUPLICATE_PASS_CONDITION,
        gaps=tuple(dict.fromkeys(gaps)),
    )


# Responsibility-owner competition (#77): candidates come only from
# mechanical snapshot evidence, and no state here blocks completion.

OWNER_ACTION = "Deepen, replace, or consolidate until one owner remains for the responsibility, and delete the competing surface."

OWNER_PASS_CONDITION = pass_condition(
    "one-owner",
    ("content anchors of every named owner region", "every evidence class evaluated"),
    "no responsibility has two mechanically evidenced owners, with every evidence class evaluated",
)

# The eight mechanical evidence classes: each evaluated on every run; an
# entry that could not look reports its gap and the rule reads incomplete.
OWNER_CLASSES = (
    "state-writers", "invariant-validators", "interface-overlap",
    "lifecycle-coordinators", "parallel-entry-points", "fixture-lifecycle",
    "forwarding-surfaces", "exact-retained",
)

_OWNER_ROLES = {RULE_OWNER_PRODUCTION: ("production",), RULE_OWNER_TEST: ("test", "test-support")}
# Signature-equality classes fire per role family: repeated lifecycles are
# coordinator debt in production and fixture debt in tests.
_SIGNATURE_CLASS = {RULE_OWNER_PRODUCTION: "lifecycle-coordinators", RULE_OWNER_TEST: "fixture-lifecycle"}

_SHELL_ENV_READ = re.compile(r"\$\{([A-Z][A-Z0-9_]{3,})[:}\-]")
_MUTATORS = frozenset({"mkdir", "rename", "rmdir", "rmtree", "unlink", "write_bytes", "write_text"})
# Path segments so role-generic that sharing one is directory vocabulary, not
# shared state.
_SEGMENT_STOP = frozenset({"docs", "hooks", "src", "lib", "test", "tests"})
# Framework-reserved entry and lifecycle names: sharing one is convention,
# not an overlapping Interface for a domain operation.
_GENERIC_PUBLIC = frozenset({"main", "setUp", "tearDown"})
_MIN_SIGNATURE_OPS = 4


# Operation discriminators: flag-shaped strings are CLI modes wherever they
# appear; a bare subcommand word discriminates only under a command-runner
# callee (`git('add')` is not the operation `git('commit')`), so ordinary
# payload words stay normalized value slots.
_FLAG_TOKEN = re.compile(r"^--?[A-Za-z][\w-]{0,23}$")
_BARE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{1,15}$")
_COMMAND_CALLEES = frozenset({"check_call", "check_output", "exec", "git", "popen", "run", "sh"})


def _own_nodes(root: ast.AST) -> list[ast.AST]:
    """The scope's behavior nodes: a nested definition joins only when referenced by name."""
    own, nested, stack = [], [], [root]
    while stack:
        node = stack.pop()
        own.append(node)
        for child in ast.iter_child_nodes(node):
            (nested if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else stack).append(child)
    names = {node.id for node in own if isinstance(node, ast.Name)}
    return own + [item for child in nested if getattr(child, "name", "") in names for item in _own_nodes(child)]


def _called_names(node: ast.AST) -> tuple[str, ...]:
    """Callee anchors with command-token discriminators, in scope order."""
    names = []
    for sub in _own_nodes(node):
        if isinstance(sub, ast.Call):
            target = sub.func
            name = target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""
            if name:
                names.append(name)
                names.extend(arg.value for arg in sub.args
                             if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                             and (_FLAG_TOKEN.match(arg.value)
                                  or name in _COMMAND_CALLEES and _BARE_TOKEN.match(arg.value)))
    return tuple(names)


def _operation_signature(body: list[ast.stmt]) -> tuple:
    """The ordered lifecycle-operation signature of one function body:
    callee anchors, command-token discriminators, operation order, and nested
    control-flow shape are preserved; only call-free local bindings and
    payload/value slots normalize, so varying payloads keep one signature
    while an extra operation, callee, subcommand, mode, or reshaped control
    flow breaks it."""
    ops: list[tuple] = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            used = any(isinstance(sub, ast.Name) and sub.id == stmt.name for other in body
                       if not isinstance(other, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for sub in _own_nodes(other))
            ops.append(("def", (), _operation_signature(stmt.body) if used else ()))
            continue
        blocks = [(name, part) for name in ("body", "orelse", "finalbody")
                  for part in [getattr(stmt, name, None)] if part]
        if blocks:
            handlers = getattr(stmt, "handlers", ())
            headers = [value for name in ("test", "iter") for value in [getattr(stmt, name, None)] if value]
            headers += [item.context_expr for item in getattr(stmt, "items", ())]
            headers += [handler.type for handler in handlers if handler.type]
            calls = tuple(name for node in headers for name in _called_names(node))
            inner = tuple((name, (), _operation_signature(block))
                          for name, block in [*blocks, *[("handler", handler.body) for handler in handlers]])
            ops.append((type(stmt).__name__, calls, inner))
            continue
        calls = _called_names(stmt)
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and not calls:
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        ops.append((type(stmt).__name__, calls, ()))
    return tuple(ops)


def _signature_weight(signature: tuple) -> int:
    return sum((0 if op[0] in ("body", "orelse", "finalbody", "handler") else 1) + _signature_weight(op[2]) for op in signature)


def _owner_functions(text: str) -> list[dict[str, object]] | None:
    """Per-function mechanical facts (module functions and one level of
    methods), or None when the file does not parse."""
    try:
        tree = ast.parse(text)
    except (IndentationError, SyntaxError, SystemError, UnicodeError, ValueError):
        return None
    scopes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes += [item for node in tree.body if isinstance(node, ast.ClassDef)
               for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
    functions = []
    for node in scopes:
        envs: set[str] = set()
        segments: set[str] = set()
        mutates = False
        decides, compare_keys = False, set()
        for sub in _own_nodes(node):
            if isinstance(sub, (ast.Return, ast.Raise)):
                decides = decides or isinstance(sub, ast.Raise) or isinstance(sub.value, (ast.Compare, ast.BoolOp))
            elif isinstance(sub, ast.Compare) and len(sub.comparators) == 1 \
                    and isinstance(sub.comparators[0], ast.Constant):
                subject, parts = sub.left, []
                while isinstance(subject, ast.Attribute):
                    parts.append(subject.attr)
                    subject = subject.value
                parts += [subject.id] if isinstance(subject, ast.Name) else []
                if parts:
                    compare_keys.add((".".join(reversed(parts)), repr(sub.comparators[0].value)))
            if isinstance(sub, ast.Call):
                target = sub.func
                name = target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""
                mutates = mutates or name in _MUTATORS
                # os.environ.get / os.getenv with a literal name resolves a boundary.
                if name in {"get", "getenv"} and sub.args and isinstance(sub.args[0], ast.Constant) \
                        and isinstance(sub.args[0].value, str) and sub.args[0].value.isupper():
                    root = target.value if isinstance(target, ast.Attribute) else None
                    if name == "getenv" or (isinstance(root, ast.Attribute) and root.attr == "environ"):
                        envs.add(sub.args[0].value)
            elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Attribute) \
                    and sub.value.attr == "environ" and isinstance(sub.slice, ast.Constant) \
                    and isinstance(sub.slice.value, str):
                envs.add(sub.slice.value)
            elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div) \
                    and isinstance(sub.right, ast.Constant) and isinstance(sub.right.value, str):
                segment = sub.right.value.strip("/")
                if len(segment) >= 4 and "/" not in segment and segment.lower() not in _SEGMENT_STOP:
                    segments.add(segment)
        signature = _operation_signature(node.body)
        body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) \
            and isinstance(node.body[0].value, ast.Constant) else node.body
        forwards = ""
        if len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call) \
                and isinstance(body[0].value.func, ast.Name) \
                and all(isinstance(arg, ast.Name) for arg in body[0].value.args):
            forwards = body[0].value.func.id
        line = min([item.lineno for item in node.decorator_list] + [node.lineno])
        lines = text.splitlines()
        functions.append({
            "name": node.name, "line": line,
            "anchorText": lines[line - 1].strip() if line <= len(lines) else node.name,
            "public": not node.name.startswith("_"), "calls": _called_names(node),
            "signature": signature, "weight": _signature_weight(signature),
            "envs": envs, "segments": segments, "mutates": mutates, "forwards": forwards,
            "compares": frozenset(compare_keys) if decides else frozenset(),
        })
    return functions


def _owner_units(snapshot: EvaluationSnapshot, roles: tuple[str, ...]) -> tuple[list[dict[str, object]], int, list[str]]:
    """Evidence units for one role family over changed entries and captured
    baseline, with the file count and named parse gaps: one unit per parsed
    function (facts closed over same-file callees one hop, where a resolver
    delegates its boundary reads) and one per shell env-resolving line, so a
    parserless language still contributes its boundary evidence."""
    files = [(entry.path, entry.classification.role, entry.classification.language,
              entry.current_text or "", True) for entry in snapshot.role_entries(*roles)]
    changed = {item[0] for item in files}
    files += [(path, base.role, base.language, base.text, False)
              for base in snapshot.baseline
              for path in [snapshot.renamed_to.get(base.path, base.path)]
              if base.role in roles and base.text is not None
              and path not in changed and snapshot.entry(path) is None]
    units: list[dict[str, object]] = []
    gaps: list[str] = []
    for path, role, language, text, is_changed in files:
        common = {"path": path, "role": role, "language": language, "changed": is_changed}
        if language != "python":
            units += [{**common, "name": line.strip(), "line": number, "public": True, "calls": (),
                       "signature": (), "weight": 0, "envs": envs, "segments": frozenset(),
                       "mutates": True, "forwards": "", "compares": frozenset()}
                      for number, line in enumerate(text.splitlines(), 1)
                      for envs in [frozenset(_SHELL_ENV_READ.findall(line))] if len(envs) >= 2]
            continue
        functions = _owner_functions(text)
        if functions is None:
            gaps.append(f"{path}: owner evidence could not parse this python")
            continue
        ambiguous = {name for name, count in Counter(fn["name"] for fn in functions).items() if count > 1}
        if referenced := {called for fn in functions for called in fn["calls"]} & ambiguous:
            gaps.append(f"{path}: ambiguous same-named definitions referenced in closure: " + ", ".join(sorted(referenced)[:3]))
        by_name = {fn["name"]: fn for fn in functions if fn["name"] not in ambiguous}
        for fn in functions:
            closed = [fn] + [by_name[called] for called in fn["calls"]
                             if called in by_name and by_name[called] is not fn]
            units.append({**fn, **common,
                          "envs": frozenset().union(*[member["envs"] for member in closed]),
                          "segments": frozenset().union(*[member["segments"] for member in closed]),
                          "mutates": any(member["mutates"] for member in closed)})
    return units, len(files), gaps


def _owner_groups(units: list[dict[str, object]]) -> dict[str, list[list[dict[str, object]]]]:
    """Candidate groups per evidence class: every group spans two files,
    touches the changed surface, and keys on the shared evidence itself."""
    def grouped(keyed: dict[object, list[dict[str, object]]], accept=lambda members: True) -> list[list[dict[str, object]]]:
        found, seen = [], set()
        for members in keyed.values():
            ordered = sorted(members, key=lambda member: (member["path"], member["line"]))
            identity = tuple((member["path"], member["line"]) for member in ordered)
            files = {member["path"] for member in members}
            if len(files) >= 2 and identity not in seen and any(member["changed"] for member in members) and accept(members):
                seen.add(identity)
                found.append(ordered)
        return found

    def collect(key_of) -> dict[object, list[dict[str, object]]]:
        keyed: dict[object, list[dict[str, object]]] = {}
        for unit in units:
            for key in key_of(unit):
                keyed.setdefault(key, []).append(unit)
        return keyed

    by_name = {}
    for unit in units:
        by_name.setdefault((unit["path"], unit["name"]), unit)
    callers: set[str] = set()
    for unit in units:
        callers.update(unit["calls"])
    defined = {unit["name"] for unit in units}

    return {
        # Resolvers sharing one env-anchor pair across files; pairwise keys so a superset still competes.
        "state-writers": grouped(collect(
            lambda unit: [frozenset(pair) for pair in combinations(sorted(unit["envs"]), 2)]))
        # One state segment in two files, at least one mutating it.
        + grouped(collect(lambda unit: sorted(unit["segments"])),
                  lambda members: any(member["mutates"] for member in members)),
        "invariant-validators": grouped(collect(
            lambda unit: [unit["compares"]] if unit["compares"] else [])),
        # Overlapping Interfaces need shared evidence beyond the name.
        "interface-overlap": grouped(
            collect(lambda unit: [unit["name"]] if unit["public"] and unit["name"] not in _GENERIC_PUBLIC and unit["signature"] else []),
            lambda members: bool(
                set.intersection(*[set(member["calls"]) for member in members])
                or frozenset.intersection(*[member["envs"] | member["segments"] for member in members])
            ),
        ),
        "parallel-entry-points": grouped(collect(
            lambda unit: [frozenset(set(unit["calls"]) & defined)]
            if unit["name"] not in callers and len(set(unit["calls"]) & defined) >= 3 and unit["signature"] else [])),
        "forwarding-surfaces": grouped(collect(
            lambda unit: [unit["forwards"]] if unit["forwards"] and unit["forwards"] in defined
            and by_name.get((unit["path"], unit["forwards"])) is None else [])),
    }


def _signature_groups(units: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Functions owning one ordered lifecycle signature at least twice."""
    keyed: dict[tuple, list[dict[str, object]]] = {}
    for unit in units:
        if unit["signature"] and unit["weight"] >= _MIN_SIGNATURE_OPS:
            keyed.setdefault(unit["signature"], []).append(unit)
    return [
        sorted(members, key=lambda member: (member["path"], member["line"]))
        for members in keyed.values()
        if len(members) >= 2 and any(member["changed"] for member in members)
    ]


def find_owner_competition(
    snapshot: EvaluationSnapshot,
    duplicates: list[Finding],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """The responsibility-owner rules: state findings, active candidates,
    and resolved telemetry, generated independently of duplicate detection.
    Exact evidence is one class among eight, never an entry requirement;
    dispositions bind only through structural validation against the exact
    evaluated snapshot."""
    streams = snapshot.gap_streams()
    records = list(snapshot.disposition_records)
    states: list[Finding] = []
    candidates: list[Finding] = []
    resolved: list[Finding] = []
    for rule_id, roles in _OWNER_ROLES.items():
        # Capture/attribution failures hide any candidate; measurement gaps
        # stay with the owning role family.
        hidden = list(streams["attribution"] + streams["capture"]) + sorted(
            {gap for entry in snapshot.role_entries(*roles) for gap in entry.gaps}
        )
        units, file_count, parse_gaps = _owner_units(snapshot, roles)
        groups = _owner_groups(units)
        retained = [
            [dict(region, line=region["displayLine"], name=str(region["contentAnchor"]))
             for region in finding.evidence["duplicates"][0]["regions"]]
            for finding in duplicates
            if str(finding.evidence["duplicates"][0]["regions"][0]["role"]) in roles
        ]
        per_class: dict[str, list] = {name: [] for name in OWNER_CLASSES}
        per_class.update(groups)
        per_class[_SIGNATURE_CLASS[rule_id]] = _signature_groups(units)
        per_class["exact-retained"] = retained
        rule_candidates = []
        for name in OWNER_CLASSES:
            for members in per_class[name]:
                rule_candidates.append(_owner_candidate(snapshot, rule_id, name, members))
        # Parse, graph, and capture failures hide candidates; unread owner
        # discovery matters once a changed-side unit could pair against it.
        # Decision 1b: bound graph evidence must also carry symbol results
        # for the changed owner surface; a bare declaration is not evidence.
        uncovered = sorted({entry.path for entry in snapshot.role_entries(*roles)}
                           - set(snapshot.graph_files)) if not snapshot.graph_gap else []
        coverage_gap = (
            f"external graph evidence carries no symbol results for {len(uncovered)} changed file(s): "
            + ", ".join(uncovered[:5]) if uncovered else ""
        )
        graph_gaps = [gap for gap in (snapshot.graph_gap, coverage_gap) if gap]
        owner_gaps = hidden + parse_gaps + list(snapshot.gitnexus_warnings) + graph_gaps
        if any(unit["changed"] for unit in units):
            discovery = streams["baseline"] if "production" in roles else streams["baseline_roles"]
            owner_gaps += list(discovery + streams["baseline_scope"])
        owner_gaps = tuple(dict.fromkeys(owner_gaps))
        applied, rule_resolved, consumed, notes = _apply_dispositions(
            snapshot, rule_id, records, rule_candidates, not owner_gaps
        )
        active = applied + [item for item in rule_candidates if item.finding_id() not in consumed]
        candidates.extend(active)
        resolved.extend(rule_resolved)
        # A record that could not bind is rule-specific incompleteness.
        owner_gaps = tuple(dict.fromkeys(list(owner_gaps) + [f"disposition record: {note}" for note in notes]))
        ledger = [
            {"class": name, "status": "incomplete" if parse_gaps else "evaluated", "files": file_count,
             "candidates": len(per_class[name]), "gaps": sorted(parse_gaps)}
            for name in OWNER_CLASSES
        ]
        states.append(Finding(
            rule_id=rule_id,
            severity="warning",
            status="incomplete" if owner_gaps else "finding" if active else "passed",
            passed=None if owner_gaps else True,
            identity=(snapshot.base_identity, snapshot.candidate_identity),
            region={"scope": "evaluation", "changedScope": snapshot.changed_scope,
                    "fileCount": file_count},
            evidence={"classes": ledger, "candidates": len(active), "records": notes},
            action=OWNER_ACTION,
            pass_condition=OWNER_PASS_CONDITION,
            gaps=owner_gaps,
        ))
    return states, candidates, resolved


def _owner_candidate(
    snapshot: EvaluationSnapshot,
    rule_id: str,
    evidence_class: str,
    members: list[dict[str, object]],
) -> Finding:
    regions = [
        {"path": member["path"], "role": member["role"], "language": member["language"],
         "displayLine": member.get("displayLine", member["line"]), "owner": str(member["name"]),
         # Anchored on the resolved implementation line's content, not the
         # name: a different implementation under one name is different debt.
         "contentAnchor": member.get("contentAnchor")
         or anchor("owner", str(member["role"]), str(member["language"]),
                   str(member.get("anchorText") or member["name"])),
         "evidenceRole": "owner"}
        for member in members
    ]
    identity = (evidence_class, *sorted(str(region["contentAnchor"]) for region in regions))
    return Finding(
        rule_id=rule_id,
        severity="warning",
        status="finding",
        passed=True,
        identity=identity,
        region={"scope": "candidate", "evidenceClass": evidence_class, "regions": regions},
        evidence={"evidenceClass": evidence_class,
                  "owners": [f"{region['path']}:{region['displayLine']}" for region in regions]},
        action=OWNER_ACTION,
        pass_condition=OWNER_PASS_CONDITION,
        gaps=(),
        state="candidate",
    )


_SEMANTIC_DISPOSITIONS = ("same-responsibility", "distinct-authority", "temporary-coexistence")
# The parent-pinned v1 field set (#54 issuecomment-5259793024): anything wider is v2 territory.
_V1_FIELDS = frozenset((
    "schemaVersion", "ruleId", "responsibilityKey", "disposition", "repair", "base", "candidate", "owners",
    "survivor", "parentRecord", "validationRoot", "resolvedBase", "resolvedCandidateTree"))
_REPAIRS = ("deepen", "replace", "consolidate")

# Parent #54 decision (2026-08-12): resolution silences a warning, so it
# requires a parent-pinned record — identifier verbatim from #54 comment
# 5251048442; digests computed over the v1 shape blessed at 5259793024, one
# per pinned case. Extending this table is parent-approved code, like promotion.
_PINNED_VALIDATION_IDENTIFIER = "future3OOO/claude-skills#54 comment 5251048442"
_PINNED_VALIDATION_DIGESTS = frozenset({
    "08f61bed0d5df8b9435a38b1fb1712530bebb063d7c9b457dbe85770f97a016e",
    "d7bda52e9bff988face173e92467cc2db78d159c1564f2817075b4cd1c195de8",
    "3e96fd97af71111fc5e724f457ca5b3f32ef79fdd4d0a7a25e635ce600a0b39c",
    "6c2fdd01db924618efc9df048884b2ef64082d5d254657e6fae4d47c92d15575",
})


def _parent_pinned(record: dict[str, object]) -> bool:
    root = record.get("validationRoot")
    return isinstance(root, dict) and root.get("identifier") == _PINNED_VALIDATION_IDENTIFIER \
        and root.get("digest") in _PINNED_VALIDATION_DIGESTS


def _captured_text(snapshot: EvaluationSnapshot, path: str, side: str) -> str | None:
    """The captured text of one path on the base or candidate side."""
    entry = snapshot.entry(path)
    if entry is not None:
        return entry.base_text if side == "base" else entry.current_text
    for base in snapshot.baseline:
        if (base.path if side == "base" else snapshot.renamed_to.get(base.path, base.path)) == path:
            return base.text
    return None


def _anchor_line(text: str | None, ref: dict[str, object]) -> int | None:
    """Where one reference resolves in captured text: an exact content
    line or a parsed symbol definition; wildcards resolve nowhere."""
    if text is None:
        return None
    content = ref.get("content")
    if isinstance(content, str) and content.strip():
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip() == content.strip():
                return number
        return None
    symbol = ref.get("symbol")
    if isinstance(symbol, str) and symbol:
        for fn in _owner_functions(text) or []:
            if fn["name"] == symbol:
                return int(fn["line"])
    return None


def _sides(snapshot: EvaluationSnapshot) -> list[tuple[str | None, str | None]]:
    """Every captured surface as (base text, candidate text)."""
    paths = {entry.path for entry in snapshot.entries}
    pairs = [(entry.base_text, entry.current_text) for entry in snapshot.entries]
    pairs += [(base.text, base.text) for base in snapshot.baseline
              if snapshot.renamed_to.get(base.path, base.path) not in paths]
    return pairs


def _call_pattern(ref: dict[str, object]) -> re.Pattern[str] | None:
    symbol = ref.get("symbol")
    if isinstance(symbol, str) and symbol:
        return re.compile(rf"(?<!def )\b{re.escape(symbol)}\s*\(")
    content = ref.get("content")
    return re.compile(re.escape(str(content).strip())) if isinstance(content, str) and content.strip() else None


def _still_referenced(snapshot: EvaluationSnapshot, ref: dict[str, object]) -> bool:
    """Whether any candidate-tree surface still resolves the superseded
    symbol: a surviving reference keeps the conflict alive."""
    symbol = ref.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return False
    pattern = re.compile(rf"(?<!def )\b{re.escape(symbol)}\s*\(")
    return any(current and pattern.search(current) for _, current in _sides(snapshot))


def _function_spans(text: str) -> dict[str, str] | None:
    try:
        tree = ast.parse(text)
    except (IndentationError, SyntaxError, SystemError, UnicodeError, ValueError):
        return None
    return {
        node.name: ast.get_source_segment(text, node) or ""
        for scope in [tree, *[item for item in tree.body if isinstance(item, ast.ClassDef)]]
        for node in scope.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _rewired_to_survivor(snapshot: EvaluationSnapshot, ref: dict[str, object], present) -> bool:
    """Every affected surface must reach the survivor, per affected python
    function (an unrelated neighbor proves nothing) and at file level where
    no parser exists: a repair that only deletes behavior never resolves."""
    gone_pattern = _call_pattern(ref)
    survivor_patterns = [pattern for _, _, kept in present for pattern in [_call_pattern(kept)] if pattern]
    if gone_pattern is None or not survivor_patterns:
        return True

    def reaches(text: str) -> bool:
        return any(pattern.search(text) for pattern in survivor_patterns)

    for base_text, current in _sides(snapshot):
        if not (base_text and gone_pattern.search(base_text) and current):
            continue
        base_spans, current_spans = _function_spans(base_text), _function_spans(current)
        affected = [name for name, segment in (base_spans or {}).items() if gone_pattern.search(segment)]
        if base_spans is None or current_spans is None or not affected:
            if not reaches(current):
                return False
            continue
        if not all(reaches(current_spans.get(name) or current) for name in affected):
            return False
    return True


def _anchored_ref(ref: object) -> bool:
    """An owner/survivor reference: a path plus a symbol or exact content anchor."""
    return isinstance(ref, dict) and isinstance(ref.get("path"), str) and bool(ref["path"]) and bool(
        isinstance(ref.get("symbol"), str) and ref["symbol"] or isinstance(ref.get("content"), str) and ref["content"].strip())


def _record_problem(snapshot: EvaluationSnapshot, record: dict[str, object]) -> str | None:
    """The reason this record cannot bind, or None. Every check is
    structural and snapshot-bound; nothing here trusts prose."""
    if "invalidDocument" in record:
        return str(record["invalidDocument"])
    key = record.get("responsibilityKey")
    disposition = record.get("disposition")
    owners = record.get("owners")
    if record.get("schemaVersion") != 1:
        return f"record rejected: unknown disposition schema version {record.get('schemaVersion')!r}"
    if not isinstance(key, str) or not key or disposition not in _SEMANTIC_DISPOSITIONS:
        return f"record rejected: responsibilityKey and a semantic disposition from {_SEMANTIC_DISPOSITIONS} are required"
    if not isinstance(owners, list) or len(owners) < 2 or not all(_anchored_ref(ref) for ref in owners):
        return f"{key}: record rejected: at least two owner references, each a path with a symbol or exact content anchor, are required"
    if (survivor := record.get("survivor")) is not None and not _anchored_ref(survivor):
        return f"{key}: record rejected: the survivor requires a path with a symbol or exact content anchor"
    if extra := sorted(set(record) - _V1_FIELDS):
        return f"{key}: record rejected: fields outside the pinned schema-v1 set: {', '.join(extra)}"
    # The record names its validation root and carries a digest over its
    # canonical content, so a candidate-side edit breaks the binding.
    root = record.get("validationRoot")
    if not isinstance(root, dict) or not isinstance(root.get("identifier"), str) or not root["identifier"] \
            or not isinstance(record.get("parentRecord"), str) or not record["parentRecord"]:
        return f"{key}: record rejected: a parent-bound validation root and record reference are required"
    canonical = json.dumps(
        {name: value for name, value in record.items()
         if name not in ("validationRoot", "resolvedBase", "resolvedCandidateTree")},
        sort_keys=True,
    )
    if root.get("digest") != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
        return f"{key}: record rejected: its content does not match the validation root digest"
    repair = record.get("repair")
    if disposition == "distinct-authority":
        if repair is not None:
            return f"{key}: record rejected: a repair strategy is meaningless for distinct-authority"
    elif repair not in _REPAIRS:
        return f"{key}: record rejected: repair must be one of {_REPAIRS}"
    if disposition == "temporary-coexistence":
        return f"{key}: record rejected: the tracked follow-up/expiry slice is not expressible in schema v1; the finding stays active"
    if record.get("resolvedBase") != snapshot.base_identity or record.get("resolvedCandidateTree") != snapshot.candidate_tree:
        return f"{key}: record is stale: its base/candidate do not name the evaluated snapshot"
    # Unread capture bound: unverifiable, not absent; an unrecorded path
    # rejects.
    unresolved = [
        ref for ref in owners
        if all(_anchor_line(_captured_text(snapshot, str(ref["path"]), side), ref) is None
               for side in ("base", "candidate"))
        and not _path_known_unread(snapshot, str(ref["path"]))
    ]
    if unresolved:
        return f"{key}: record rejected: {len(unresolved)} owner reference(s) resolve nowhere in the evaluated snapshot"
    return None


def _path_known_unread(snapshot: EvaluationSnapshot, path: str) -> bool:
    return any(base.text is None and snapshot.renamed_to.get(base.path, base.path) == path
               for base in snapshot.baseline)


def _apply_dispositions(
    snapshot: EvaluationSnapshot,
    rule_id: str,
    records: list[dict[str, object]],
    rule_candidates: list[Finding],
    scope_complete: bool,
) -> tuple[list[Finding], list[Finding], set[str], list[str]]:
    """Validated records drive the state machine; everything else is a
    note. same-responsibility confirms and resolves only via the one-owner
    predicate plus the parent-pinned table; distinct-authority resolves from
    candidate with complete scope and a pinned record; coexistence is v2
    territory; everything unresolved or unpinned stays active."""
    applied: list[Finding] = []
    resolved: list[Finding] = []
    consumed: set[str] = set()
    notes: list[str] = []
    seen_digests: set[str] = set()
    for record in records:
        rule_ref = record.get("ruleId")
        if isinstance(rule_ref, str) and rule_ref != rule_id and rule_ref in _OWNER_ROLES:
            continue
        # A duplicate reference cannot bind twice; the second copy is a note.
        root = record.get("validationRoot")
        digest = str(root.get("digest")) if isinstance(root, dict) else ""
        if digest and digest in seen_digests:
            notes.append(f"duplicate record reference rejected: digest {digest[:16]} already applied")
            continue
        seen_digests.add(digest)
        problem = _record_problem(snapshot, record)
        if problem is not None:
            notes.append(problem)
            continue
        if not isinstance(rule_ref, str) or rule_ref not in _OWNER_ROLES:
            notes.append(f"{record['responsibilityKey']}: record rejected: unknown ruleId {rule_ref!r}")
            continue
        owners = [dict(ref) for ref in record["owners"]]
        survivor = record.get("survivor")
        refs = owners + ([dict(survivor)] if isinstance(survivor, dict) else [])
        present = []
        for ref in refs:
            line = _anchor_line(_captured_text(snapshot, str(ref["path"]), "candidate"), ref)
            if line is not None:
                present.append((str(ref["path"]), line, ref))
        key = str(record["responsibilityKey"])
        disposition = record["disposition"]
        if disposition == "distinct-authority":
            if len({(path, line) for path, line, _ in present}) >= len(owners) and scope_complete and _parent_pinned(record):
                resolved.append(_record_finding(snapshot, rule_id, record, present, "resolved"))
                consumed |= _consumed(rule_candidates, present)
            elif not _parent_pinned(record):
                notes.append(f"{key}: resolution requires a parent-pinned validation record")
            else:
                notes.append(f"{key}: distinct-authority record left the candidate active: unresolved anchors or incomplete scope")
            continue
        gone = [ref for ref in owners if _anchor_line(_captured_text(snapshot, str(ref["path"]), "candidate"), ref) is None]
        deletion_proven = all(
            _anchor_line(_captured_text(snapshot, str(ref["path"]), "base"), ref) is not None
            and not _still_referenced(snapshot, ref)
            and _rewired_to_survivor(snapshot, ref, present)
            for ref in gone
        )
        record_consumed = _consumed(rule_candidates, present)
        # A renamed or facaded competitor deletes the anchor, not the
        # competition: a surviving mechanical candidate that still names the
        # remaining owner keeps the conflict confirmed.
        named = {(path, line) for path, line, _ in present}
        survivor_contested = any(
            item.finding_id() not in record_consumed
            and named & {(str(region["path"]), int(region["displayLine"])) for region in item.region["regions"]}
            for item in rule_candidates
        )
        one_owner = len(named) == 1
        one_owner_holds = scope_complete and one_owner and bool(gone) and deletion_proven and not survivor_contested
        if one_owner_holds and _parent_pinned(record):
            resolved.append(_record_finding(snapshot, rule_id, record, present, "resolved"))
        else:
            if one_owner_holds:
                notes.append(f"{key}: resolution requires a parent-pinned validation record")
            applied.append(_record_finding(snapshot, rule_id, record, present, "confirmed-unresolved",
                                           {"oneOwnerPredicate": one_owner_holds}))
        consumed |= record_consumed
    return applied, resolved, consumed, notes


def _consumed(rule_candidates: list[Finding], present: list[tuple[str, int, dict[str, object]]]) -> set[str]:
    """Candidates whose every region the record names: the record-backed
    finding owns that pair, so the bare candidate retires."""
    named = {(path, line) for path, line, _ in present}
    return {
        item.finding_id() for item in rule_candidates
        if {(str(region["path"]), int(region["displayLine"])) for region in item.region["regions"]} <= named
    }


def _record_finding(
    snapshot: EvaluationSnapshot,
    rule_id: str,
    record: dict[str, object],
    present: list[tuple[str, int, dict[str, object]]],
    state: str,
    extra: dict[str, object] | None = None,
) -> Finding:
    stored = {entry.path: entry.classification for entry in snapshot.entries}
    regions = []
    for path, line, ref in sorted(present, key=lambda item: (item[0], item[1])):
        classification = stored.get(path)
        role = classification.role if classification else next(
            (base.role for base in snapshot.baseline if snapshot.renamed_to.get(base.path, base.path) == path), "unknown")
        language = classification.language if classification else "other"
        resolved = (_captured_text(snapshot, path, "candidate") or "").splitlines()
        content = resolved[line - 1].strip() if line <= len(resolved) else str(ref.get("symbol") or ref.get("content") or path)
        regions.append({
            "path": path, "role": role, "language": language, "displayLine": line,
            "owner": str(ref.get("symbol") or ref.get("content") or path),
            "contentAnchor": anchor("owner", str(role), str(language), content),
            "evidenceRole": "owner",
        })
    key = str(record["responsibilityKey"])
    return Finding(
        rule_id=rule_id,
        severity="warning",
        status="finding",
        passed=True,
        identity=(key, *sorted(str(region["contentAnchor"]) for region in regions)),
        region={"scope": "candidate", "evidenceClass": "disposition", "regions": regions},
        evidence={"responsibilityKey": key, "disposition": record["disposition"],
                  "repair": record.get("repair"), "parentRecord": record["parentRecord"],
                  **(extra or {}),
                  "owners": [f"{region['path']}:{region['displayLine']}" for region in regions]},
        action=OWNER_ACTION,
        pass_condition=OWNER_PASS_CONDITION,
        gaps=(),
        state=state,
    )
