from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from .findings import (
    Finding, RULE_DUPLICATE_BASELINE, RULE_DUPLICATE_BLOCK, RULE_DUPLICATE_SYMBOL,
    RULE_OWNER_PRODUCTION,
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
                "kind": next((str(node["id"]).partition(":")[0] for node in snapshot.symbols(path)
                              if node["startLine"] + 1 == list(current or {})[found_at]), "block"),
            }
    return owners


def _scan(snapshot: EvaluationSnapshot) -> tuple[list[dict[str, object]], list[str]]:
    """One canonicalized read per changed hunk, and the scopes it could not read."""
    scans, gaps = [], []
    for entry in snapshot.role_entries(*BASELINE_ROLES):
        language = entry.classification.language
        text = entry.current_text or ""
        canonical = canonical_lines(text, language)
        symbols = extract_symbols(entry.path, text, language, snapshot.symbols(entry.path))
        if entry.path not in snapshot.graph_files:
            gaps.append(f"{entry.path}: no snapshot-bound graph ranges")
            continue
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
                **_symbol_regions(entry.path, role, language, symbols, set(added), canonical),
            })
    return scans, gaps


def _symbol_regions(path: str, role: str, language: str, symbols: list, added: set[int], canonical: dict[int, str]) -> dict[str, list[dict[str, object]]]:
    """Complete symbol bodies this one hunk added in full, and each body alone.

    A hunk that edited part of an existing helper did not add it, and joining
    hunks to complete a symbol is forbidden, so partial extents are not
    candidates. The body-only region is a baseline candidate only: the same
    implementation under a different name is still a copy of it.
    """
    regions: list[dict[str, object]] = []
    bodies: list[dict[str, object]] = []
    for symbol in symbols:
        start, suite = symbol.line, symbol.line + 1
        extent = range(start, symbol.line + len(symbol.content.splitlines()))
        if not all(number in added for number in extent):
            continue
        body = "\n".join(canonical[number] for number in extent if number in canonical)
        if len(body.splitlines()) < MIN_REGION_LINES:
            continue
        fingerprint = anchor("symbol", role, language, body)
        regions.append({
            "fingerprint": fingerprint, "path": path, "role": role, "language": language,
            "displayLine": start, "lines": tuple(extent), "evidenceRole": "duplicate", "body": body, "kind": symbol.kind,
        })
        # A graph-bounded exact suffix, not a guessed language-specific body.
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
                    "evidenceRole": member["evidenceRole"], "kind": member.get("kind", "block"),
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
    "no unresolved structural owner-overlap candidate remains in the captured graph and source",
)

# These are structural candidates. Existing review judges responsibility and cost.
OWNER_CLASSES = ("implementation-overlap", "shared-callees", "interface-overlap", "exact-retained")
_OWNER_ROLES = {RULE_OWNER_PRODUCTION: ("production",)}


def _owner_units(snapshot: EvaluationSnapshot, roles: tuple[str, ...]) -> tuple[list[dict[str, object]], int, list[str]]:
    paths = {entry.path for entry in snapshot.role_entries(*roles)} | {base.path for base in snapshot.baseline if base.role in roles and base.text is not None}
    units, gaps = [], []
    for path in sorted(paths):
        text = _captured_text(snapshot, path, "candidate")
        if text is None:
            continue
        if path not in snapshot.graph_files:
            gaps.append(path + ": no snapshot-bound graph symbols")
            continue
        for node in snapshot.symbols(path):
            lines = text.splitlines()[node["startLine"]:node["endLine"] + 1]
            if not lines:
                continue
            # Remove only the reported declaration name from the first line.
            # Equality is a source-overlap hint, not semantic equivalence.
            lines[0] = lines[0].replace(str(node["name"]), "<declared-name>", 1)
            units.append({"id": node["id"], "name": node["name"], "path": path, "line": node["startLine"] + 1,
                          "role": "production", "language": (snapshot.entry(path).classification.language if snapshot.entry(path) else next(base.language for base in snapshot.baseline if base.path == path)),
                          "changed": snapshot.entry(path) is not None, "kind": str(node["id"]).partition(":")[0], "calls": tuple(node.get("calls") or []),
                          "implementation": "\n".join(lines), "weight": len(lines)})
    return units, len(paths), gaps


def _owner_groups(units: list[dict[str, object]]) -> dict[str, list[list[dict[str, object]]]]:
    def grouped(key):
        groups = {}
        for unit in units:
            value = key(unit)
            if value:
                groups.setdefault(value, []).append(unit)
        return [members for members in groups.values() if len({member["path"] for member in members}) > 1
                and any(member["changed"] for member in members)]
    return {"implementation-overlap": grouped(lambda unit: (unit["language"], unit["implementation"]) if unit["weight"] >= 3 else None),
            "shared-callees": grouped(lambda unit: frozenset(unit["calls"]) if len(set(unit["calls"])) >= 2 else None),
            "interface-overlap": grouped(lambda unit: (unit["name"], frozenset(unit["calls"])) if unit["calls"] else None)}


def find_owner_competition(
    snapshot: EvaluationSnapshot,
    duplicates: list[Finding],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """The responsibility-owner rules: state findings, active candidates,
    and resolved telemetry, generated independently of duplicate detection.
    Exact evidence is one class among seven, never an entry requirement;
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
            owner_gaps += list(streams["baseline"] + streams["baseline_scope"])
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
        region={"scope": "candidate", "evidenceClass": evidence_class, "regions": regions, "kind": members[0].get("kind", "block")},
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
    "d7bda52e9bff988face173e92467cc2db78d159c1564f2817075b4cd1c195de8",
    "3e96fd97af71111fc5e724f457ca5b3f32ef79fdd4d0a7a25e635ce600a0b39c",
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


def _anchor_line(snapshot: EvaluationSnapshot, path: str, side: str, ref: dict[str, object]) -> int | None:
    text = _captured_text(snapshot, path, side)
    if text is None:
        return None
    if isinstance(content := ref.get("content"), str) and content.strip():
        return next((number for number, line in enumerate(text.splitlines(), 1) if line.strip() == content.strip()), None)
    if side == "base":
        symbol = ref.get("symbol")
        matches = [number for number, line in enumerate(text.splitlines(), 1)
                   if isinstance(symbol, str) and re.search(r"(?<!\w)" + re.escape(symbol) + r"(?!\w)", line)]
    else:
        matches = [node["startLine"] + 1 for node in snapshot.symbols(path) if node["name"] == ref.get("symbol")]
    return matches[0] if len(matches) == 1 else None


def _still_referenced(snapshot: EvaluationSnapshot, ref: dict[str, object]) -> bool:
    targets = {node["id"] for node in snapshot.graph_symbols
               if node.get("file") == ref["path"] and node.get("name") == ref.get("symbol")}
    if any(targets & set(node.get("calls") or []) for node in snapshot.graph_symbols):
        return True
    # A missing graph edge cannot prove absence. Keep textual uncertainty visible.
    if not isinstance(symbol := ref.get("symbol"), str) or not symbol:
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(symbol) + r"(?!\w)")
    paths = {entry.path for entry in snapshot.entries} | {base.path for base in snapshot.baseline}
    return any(pattern.search(_captured_text(snapshot, path, "candidate") or "") for path in paths)


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
        if all(_anchor_line(snapshot, str(ref["path"]), side, ref) is None
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
            line = _anchor_line(snapshot, str(ref["path"]), "candidate", ref)
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
        gone = [ref for ref in owners if _anchor_line(snapshot, str(ref["path"]), "candidate", ref) is None]
        anchors_removed = all(
            _anchor_line(snapshot, str(ref["path"]), "base", ref) is not None
            and not _still_referenced(snapshot, ref)
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
        one_owner_holds = scope_complete and one_owner and bool(gone) and anchors_removed and not survivor_contested
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
