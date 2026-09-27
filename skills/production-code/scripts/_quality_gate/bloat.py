"""QG-BLOAT: code retrieves each pair, TypeSafe Jev judges it, code decides. A changed function against the functions
most like it; a new committed test against its folder's most similar tests; an added assertion against the removed production lines; a changed production function against
other lines writing its keys. One pair per request, all sized before any is sent; kept answers are never re-sent."""
from __future__ import annotations

import json
import keyword
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import jev
from .findings import RULE_BLOAT, Finding, pass_condition
from .new_tests import fresh, unproven
from .path_policy import classify_path, is_data_path, language_for_path
from .snapshot import EvaluationSnapshot
from .symbols import extract_symbols

# SAME's bars come from the labelled pairs, HOST's from PR #106's eight folds, GUARD's and OWNERS' from PR #111 (fitted).
# Shortlist, labels held-out: the top three by character runs plus the top one by normalised body reach 35 of 43
# counterparts, as many as the Jev catalogue search did, with no request; FLOOR keeps all 35.
SAME_BAR, HOST_BAR, GUARD_BAR, OWNERS_BAR = {"production": 1.0, "test": 1.5}, 0.5, 0.4, 0.7
BY_CHARS, BY_BODY, FLOOR, CODE_CAP = 3, 1, 0.25, 6000
_TOP_ASSIGN = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)")
_ASSERTION = re.compile(r"\bassert|\bexpect\(|\bshould\b|\[\[ .* \]\]")
_TRIVIAL = re.compile(r"^\s*((from\s+\S+\s+)?import\b|#|//|$)")
_WRITES = re.compile(r"\[[\"'](\w+)[\"']\]\s*=(?!=)|(?:^|[{,])\s*[\"'](\w+)[\"']\s*:|\.([A-Za-z_]\w*)\s*=(?!=)")
_TEST_FILE = re.compile(r"(?:_test\.go|_spec\.rb)$")
_COMMENT = ("#", '"""', "'" * 3, "//")
_CALL = re.compile(r"(?<![\w.])(?:self\.)?([A-Za-z_]\w*)\s*\(")
_NOT_CALLS = {"assert", "print", "len", "str", "int", "set", "dict", "list", "tuple", "sorted", "isinstance", "open", "range", "any", "all"}
_NORMAL = ((re.compile(r'""".*?"""|\'\'\'.*?\'\'\'', re.DOTALL), "S"), (re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\''), "S"),
           (re.compile(r"\b\d+\b"), "N"), (re.compile(r"def \w+"), "def F"), (re.compile(r"\s+"), " "))

_noul = lambda question, yes, no: {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}
SAME = ("How much of the logic in `unit` repeats logic that `candidates.c0` already contains, judged by what the steps compute, not by how they are written?",
        ["Different jobs: they share only shape, names or library calls, or compute different results.",
         "Overlapping: part of one repeats steps the other already has, but each keeps real logic of its own.",
         "The same logic: one repeats what the other computes, so it should call the other or the two should merge."])
HOST = _noul("Could the check `unit` makes be written as one or two more cases or lines in `host` (a new input with its expected outcome in a list `host` already runs, or one more assertion on a result `host` already produces), so `unit` need not exist as a separate test?",
        "Yes: a case or line in `host` would carry it", "No: it needs its own test")
GUARD = _noul("This change removed `removed` from the production code and added `added`. Does `assertion` only check that something those removed lines produced is no longer there, rather than checking a result the added or remaining code produces?",
         "It only checks that removed output is gone", "It checks a result the code still produces")
OWNERS = _noul("Does `unit` set a field, key or list that a line in `other_writers` also sets, so that two places decide that one value?",
          "Two places decide the same value", "Only `unit` decides this value, or the writers serve different purposes")


def find_bloat(snapshot: EvaluationSnapshot, reds: list | str | None = None, paths: tuple[str, ...] = (), touched: bool = False,
               budget: int = 400_000, store=None) -> list[Finding]:
    """`reds` are the pass's recorded failing runs ({command, site}); given, each new test none of them names is flagged.
    `paths` judges every function of those files or folders, changed or not, `touched` of every changed file: bloat counts
    whoever wrote it. `store` is the file keeping Jev's answers."""
    index = {path: extract_symbols(path, text, language_for_path(path)) for path, text in snapshot.sources.items()}
    units, edits = _units(snapshot, index, paths + (tuple(e.path for e in snapshot.entries) if touched else ())), _production_edits(snapshot)
    streams = snapshot.gap_streams()
    gaps = [*snapshot.source_gaps, *streams["capture"], *streams["attribution"], *streams["measurement"]]
    cost, published, key = {"requests": 0, "cached": 0, "inputTokens": 0, "estimatedTokens": 0, "questions": {}}, [], jev.api_key()
    if not key:
        gaps.append("no TypeSafe key: set TYPESAFE_API_KEY or ~/.config/typesafe/key")
    elif units:
        published, failed = _judge(jev.Session(key, store), _pairs(snapshot, index, units, edits), budget, cost)
        gaps += failed
    if isinstance(reds, list):
        published += [_line(unit, "test-unproven") for unit in unproven(units, snapshot, reds)]
    elif reds is not None:
        gaps.append(f"tdd evidence ignored: {reds}")
    gaps = sorted(set(gaps))
    rule = Finding(
        rule_id=RULE_BLOAT, severity="warning", status="incomplete" if gaps else "finding" if published else "passed",
        passed=None if gaps else True, identity=(snapshot.base_identity, snapshot.candidate_identity),
        region={"scope": "evaluation", "fileCount": len(snapshot.entries)},
        evidence={"units": [{k: unit[k] for k in ("path", "line", "symbol", "kind")} for unit in units],
                  "removedNames": len(edits[2]), "model": jev.MODEL, **cost},
        action="Merge each duplicate into its counterpart: call it, or make the test a case of it.",
        pass_condition=pass_condition("bloat-absent", ("every unit judged",), "no judged pair reaches its bar"),
        gaps=tuple(gaps),
    )
    return [rule, *published]


def _pairs(snapshot: EvaluationSnapshot, index: dict[str, list], units: list[dict[str, object]], edits) -> list[tuple]:
    """Every (unit, category, counterpart, question, bar, state, questions) the change asks."""
    entries = _catalogue(snapshot.sources, index, snapshot.graph_symbols)
    pairs = [same(unit, entry) for unit in units if unit["kind"] == "function" and unit["shape"] != "class"
                          for entry in shortlist(unit, [e for e in entries if e["kind"] == "function" and e["role"] == unit["role"]])]
    # A new committed test against its folder's most similar tests (PR #106: tracing which tests run the changed lines
    # took 475 s and ranked the table host top-3 for 1 of 7 folds, so it is not done; folds into a table stay unseen).
    for unit in (u for u in fresh(units, snapshot) if u["symbol"] != "<region>"):
        folder = [e for e in entries if e["kind"] == "test" and e["path"].rpartition("/")[0] == str(unit["path"]).rpartition("/")[0]]
        pairs += [(unit, "duplicate", f"{e['path']}:{e['line']} {e['name']}", "host", HOST_BAR, {"unit": str(unit["code"])[:CODE_CAP], "host": str(e["code"])[:CODE_CAP]}, {"host": HOST})
                  for e in shortlist(unit, folder)]
    # An assertion is asked only when it names something the change took out of production code (a name or literal
    # its removed lines use more often than its added ones), given the removed and added lines naming the same.
    near = lambda lines, names: sorted((r for r in lines if _words(r) & names), key=lambda r: -len(_words(r) & names))[:6]
    for unit in (u for u in units if u["kind"] == "test"):
        for text in _assertions(unit["changed"]):
            if gone := _words(text) & edits[2]:
                pairs.append((unit, "deleted-guard", text, "guard", GUARD_BAR, {"assertion": text, "removed": near(edits[0], gone),
                              "added": near(edits[1], _words(text))}, {"guard": GUARD}))
    for unit in (u for u in units if u["kind"] == "function" and u["role"] == "production"):
        if writers := _writers(unit, snapshot, [e for e in entries if e["kind"] == "function" and unit["symbol"] in e["calls"]]):
            pairs.append((unit, "two-owners", writers[0], "owners", OWNERS_BAR, {"unit": str(unit["code"])[:CODE_CAP], "other_writers": writers},
                          {"owners": OWNERS}))
    return pairs


def _judge(session, pairs: list[tuple], budget: int, cost: dict[str, object]) -> tuple[list[Finding], list[str]]:
    """Answers every pair (the stored answer first), after sizing what must be sent: over `budget` nothing is sent."""
    fresh = {ident: int(len(json.dumps({"state": state, "questions": questions})) / jev.CHARS_PER_TOKEN) + jev.REQUEST_TOKENS
             for *_, state, questions in pairs if (ident := session.id(state, questions)) not in session.kept}
    cost["estimatedTokens"], cost["questions"] = sum(fresh.values()), dict(Counter(pair[3] for pair in pairs))
    if cost["estimatedTokens"] > budget:
        return [], [f"bloat review sent nothing: an estimated {cost['estimatedTokens']} input tokens are over the {budget}-token budget (--bloat-budget)"]

    requests = {session.id(state, questions): (state, questions) for *_, state, questions in pairs}  # identical pairs ask once

    def ask(ident: str) -> tuple[str, dict | str]:
        try:
            answers, tokens, sent = session.ask(*requests[ident])
        except jev.Unjudged as error:
            return ident, str(error)
        cost["requests" if sent else "cached"] += 1
        cost["inputTokens"] += tokens
        return ident, next(iter(answers.values()))

    with ThreadPoolExecutor(8) as pool:
        got = dict(pool.map(ask, requests))
    answered = [(pair, got[session.id(*pair[5:])]) for pair in pairs]
    failed = Counter(answer for _, answer in answered if isinstance(answer, str))
    lines = [_line(unit, category, counterpart, judged=(question, answer)) for (unit, category, counterpart, question, bar, _, _), answer in answered
             if not isinstance(answer, str) and float(answer.get("noul", answer.get("score", 0))) >= bar]
    seen: set[frozenset[str]] = set()  # a pair found from both sides prints once
    return [line for line in lines if (pair := frozenset(line.evidence["owners"])) not in seen and not seen.add(pair)], \
        [f"{count} request(s) unjudged: {reason}" for reason, count in sorted(failed.items())]


def same(unit: dict[str, object], entry: dict[str, object]) -> tuple:  # both whole, shaped as the labelled pairs were asked
    return (unit, "duplicate", f"{entry['path']}:{entry['line']} {entry['name']}", "same", SAME_BAR[str(unit["role"])], {"unit": str(unit["code"])[:CODE_CAP], "candidates": {"c0": str(entry["code"])[:CODE_CAP]}},
            {"c0": {"type": "score", "instructions": SAME[0], "criteria": SAME[1]}})


def shortlist(unit: dict[str, object], options: list[dict[str, object]]) -> list[dict[str, object]]:
    """The options most like the unit, never itself or code it uses: the top BY_CHARS by shared five-character runs and
    the top BY_BODY by normalised body (strings, numbers and names of definitions blanked), each at FLOOR or above."""
    options = [e for e in options if not _related(unit, e)]
    runs, body = _runs(str(unit["code"])), _runs(_normal(str(unit["code"])))
    ranked = lambda mine, field: [e for score, e in sorted(((_overlap(mine, e[field]), e) for e in options), key=lambda row: -row[0]) if score >= FLOOR]
    chosen = ranked(runs, "runs")[:BY_CHARS]
    return chosen + [e for e in ranked(body, "body") if e not in chosen][:BY_BODY]


def _catalogue(sources: dict[str, str], index: dict[str, list], graph: dict[tuple[str, str], tuple[str, ...]]) -> list[dict[str, object]]:
    """Every function and test once, named by path, line and name; a file in a language with no symbol pattern is one
    entry. The recorded code graph adds the calls it resolved."""
    found = []
    for path, symbols in index.items():
        lines = sources[path].splitlines()
        entries = [(s.name, s.line, s.content) for s in symbols if s.kind == "function"] or ([("<region>", 1, sources[path])] if not symbols and sources[path].strip() else [])
        found += [{"path": path, "line": line, "name": name, "code": code, "role": _role(path), "kind": _kind(path, name, code, lines, line),
                   "calls": set(_calls(code)), "runs": _runs(code), "body": _runs(_normal(code))} for name, line, code in entries]
    by_name = {(e["path"], e["name"]): e for e in found}
    for caller, name in ((by_name.get(tuple(str(ref).split(":")[-2:])), name) for (_, name), refs in graph.items() for ref in refs):
        if caller is not None:
            caller["calls"].add(name)
    return found


def _runs(text: str) -> frozenset[str]:
    text = " ".join(text.split())
    return frozenset(text[i:i + 5] for i in range(max(len(text) - 4, 1)))


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _normal(code: str) -> str:
    for pattern, blank in _NORMAL:
        code = pattern.sub(blank, code)
    return code


def _role(path: str) -> str:
    kind = classify_path(path)
    return "test" if kind.role in ("test", "test-support") or _TEST_FILE.search(path) or (kind.role != "production" and kind.test_like_compat) else "production"


def _kind(path: str, name: str, code: str, lines: list[str], line: int) -> str:
    """A test entry point (test-named, a JS it()/test() title, a test file's loose region) is a test; fixtures and helpers are functions."""
    if _role(path) != "test" or (line > 1 and re.match(r"\s*@[\w.]*fixture\b", lines[line - 2])):
        return "function"
    entry = re.match(r"(?:test|Test)", name) or re.match(r"\s*(?:it|test)\s*\(", code) or name == "<region>"
    return "test" if entry else "function"


def _calls(code: object) -> list[str]:
    """The names a unit calls, in order, without assertion and builtin helpers."""
    return list(dict.fromkeys(c for c in _CALL.findall("\n".join(str(code).splitlines()[1:])) if c not in _NOT_CALLS and not keyword.iskeyword(c) and not c.startswith("assert")))


def _related(unit: dict[str, object], entry: dict[str, object]) -> bool:
    """The unit itself, code nesting with it, and code using it or used by it: reuse is not duplication."""
    if entry["path"] == unit["path"] and (int(entry["line"]) in _lines(int(unit["line"]), str(unit["code"]))
                                          or int(unit["line"]) in _lines(int(entry["line"]), str(entry["code"]))):
        return True
    # What other code names to reuse a unit: a function by calling it, a loose region (a table, a constant) by reading a name it assigns.
    names = lambda symbol, code: [rf"{re.escape(str(symbol))}\s*\("] if symbol != "<region>" else [rf"{re.escape(m.group(1))}\b" for line in str(code).splitlines() if (m := _TOP_ASSIGN.match(line))]
    uses = lambda code, found: any(re.search(rf"(?:(?<![\w\"'.])|(?<=self\.)){name}", re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", "", "\n".join(code.splitlines()[1:]))) for name in found)
    return uses(str(unit["code"]), names(entry["name"], entry["code"])) or uses(str(entry["code"]), names(unit["symbol"], unit["code"]))


def _writers(unit: dict[str, object], snapshot: EvaluationSnapshot, callers: list[dict[str, object]]) -> list[str]:
    """Other production lines writing a key or attribute this unit's added lines write: any such line inside a caller
    (it rewrites what the unit returns), else only for keys at most three other lines write, since a name written
    everywhere ("name", "id") is a convention, not one value with two owners. Callers' lines come first."""
    written = lambda line: {name for match in _WRITES.finditer(line) for name in match.groups() if name}  # x["k"] =, "k": ..., x.k =
    names = set().union(*(written(text) for text in unit["changed"] if not text.startswith("- ")))
    span, near = _lines(int(unit["line"]), str(unit["code"])), {(e["path"], n) for e in callers for n in _lines(int(e["line"]), str(e["code"]))}
    found = [(names & written(line), (p, n) in near, f"{p}:{n}: {line.strip()}"[:200]) for p in snapshot.sources if names and _role(p) == "production"
             for n, line in enumerate(snapshot.sources[p].splitlines(), 1) if not (p == unit["path"] and n in span)]
    counts = Counter(name for shared, _, _ in found for name in shared)
    return [line for shared, caller, line in sorted(found, key=lambda row: not row[1]) if shared and (caller or any(counts[name] <= 3 for name in shared))][:6]


def _words(text: str) -> set[str]:
    """The names a line uses: each quoted literal whole and each identifier outside quotes, four or more characters."""
    quoted = re.findall(r"\"([^\"\n]{4,})\"|'([^'\n]{4,})'", text)
    bare = re.findall(r"(?<![\w-])[A-Za-z_][\w-]{3,}", re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", " ", text))
    return {w for pair in quoted for w in pair if w} | set(bare) - {"assert", "self", "item", "payload", "True", "False", "None"}


def _assertions(changed: list[str]) -> list[str]:
    """Each added assertion with the lines that close the brackets it opens; comments and quoted-text lines never start one."""
    found: list[str] = []
    depth = 0  # brackets the current assertion has opened and not closed
    for text in (t.strip() for t in changed if not t.startswith("- ")):
        if depth > 0 and not text.startswith(_COMMENT):
            found[-1] += " " + text
        elif _ASSERTION.search(text) and not text.startswith(("\"", "'", *_COMMENT)):
            found.append(text)
            depth = 0
        else:
            continue
        code = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'|#.*", "", text)
        depth = max(depth + sum(map(code.count, "([{")) - sum(map(code.count, ")]}")), 0)
    return found


def _lines(start: int, code: str) -> range:
    return range(start, start + max(len(code.splitlines()), 1))


def _enclosing(symbols: list, line: int):
    return min((s for s in symbols if line in _lines(s.line, s.content)), key=lambda s: len(_lines(s.line, s.content)), default=None)


def _production_edits(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str], set[str]]:
    """The production code lines the change removed and added, and the names and literals it removed more of than it added."""
    code = lambda entry: entry.path in snapshot.sources or entry.classification.source or entry.current_text is None and entry.classification.role != "docs" \
        and not is_data_path(entry.path)  # a deleted file is no longer in the sources
    lines = lambda pick: [line.strip()[:160] for entry in snapshot.entries if code(entry) and _role(entry.path) == "production"
                          for _, line in pick(entry) if _words(line) and not _TRIVIAL.match(line) and not line.strip().startswith(_COMMENT)]
    removed, added = lines(lambda e: e.deleted_lines()), lines(lambda e: e.added_lines())
    counts = Counter(w for line in removed for w in _words(line)) - Counter(w for line in added for w in _words(line))
    return sorted(set(removed)), sorted(set(added)), set(counts)


def _units(snapshot: EvaluationSnapshot, index: dict[str, list], paths: tuple[str, ...] = ()) -> list[dict[str, object]]:
    """Changed units (smallest enclosing symbol, else the changed region; a pure deletion's symbol) and every function under `paths`."""
    texts, units = snapshot.sources, {}

    def unit_at(path: str, number: int, text: str) -> dict[str, object]:
        symbol = _enclosing(index[path], number)
        # A changed region inside a top-level statement (a table's rows) carries that statement's first line, so the name it
        # binds is visible to _related: the test reading the table is its user, not a duplicate.
        head = next((t for t in reversed(texts[path].splitlines()[:number]) if t[:1].strip()), "") if not symbol else ""
        line, name, code = (symbol.line, symbol.name, symbol.content) if symbol else (number, "<region>", (head + "\n" if _TOP_ASSIGN.match(head) and head not in text else "") + text)
        return units.setdefault((path, line if symbol else -number), {
            "path": path, "line": line, "symbol": name, "shape": symbol.kind if symbol else "region", "role": _role(path), "kind": _kind(path, name, code, texts[path].splitlines(), line),
            "code": code, "changed": []})

    for entry in snapshot.entries:
        if entry.path not in texts:
            continue
        for hunk in entry.hunks:
            # A pure deletion changes its symbol; in a file with no symbol index (no pattern for its language), the file.
            if not hunk.added and hunk.deleted and (_enclosing(index[entry.path], max(hunk.at, 1)) or not index[entry.path]):
                unit_at(entry.path, max(hunk.at, 1), "" if index[entry.path] else texts[entry.path])["changed"] += [f"- {text}" for _, text in hunk.deleted]
            loose = [(number, text) for number, text in hunk.added if not _enclosing(index[entry.path], number)]
            region = "\n".join(text for _, text in loose)
            for number, text in hunk.added:
                if not text.strip():  # a blank line belongs to no unit; it would otherwise make a whole class one
                    continue
                if _enclosing(index[entry.path], number):
                    unit_at(entry.path, number, region)["changed"].append(text)
                elif not all(_TRIVIAL.match(line) for _, line in loose):
                    unit_at(entry.path, loose[0][0], region)["changed"].append(text)
    for path in (p for p in texts if any(p == want or p.startswith(want.rstrip("/") + "/") for want in paths)):
        for symbol in index[path]:
            if symbol.kind == "function" and not any(o is not symbol and o.kind == "function" and symbol.line in _lines(o.line, o.content) for o in index[path]):
                unit_at(path, symbol.line, symbol.content)
    return list(units.values())


def _line(unit: dict[str, object], category: str, counterpart: str = "", judged: tuple = ()) -> Finding:
    """One line: the unit, then its counterpart."""
    owners = [f"{unit['path']}:{unit['line']} {unit['symbol']}", *([counterpart] if counterpart else [])]
    return Finding(
        rule_id=RULE_BLOAT, severity="warning", status="finding", passed=True,
        identity=(category, str(unit["path"]), str(unit["symbol"]), str(unit["line"]), counterpart),
        region={"scope": "unit", "category": category, "path": unit["path"], "line": unit["line"]},
        evidence={"owners": owners, **({"question": judged[0], "score": round(float(judged[1].get("noul", judged[1].get("score", 0))), 3)} if judged else {}),
                  **({"confidence": round(float(judged[1]["confidence"]), 3)} if judged and "confidence" in judged[1] else {})},  # Score/Choice only; low flaps
        action={"duplicate": "Merge the duplicate into its counterpart: call it, or make the test a case of it",
                "deleted-guard": "Delete the assertion: it guards code that no longer exists",
                "two-owners": "Keep one owner of the value", "test-unproven": "Prove the new test with a failing run, or delete it"}[category],
        pass_condition=pass_condition("bloat-absent", (category,), f"{category} does not fire"), gaps=())
