"""Decisive-context measurements in disposable Python source copies, not a gate.

The comparison runner owns execution and evidence. This module owns expression
analysis, instrumentation and measured coverage; it never judges a change's intent.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from difflib import SequenceMatcher
import itertools
import json
import operator
import os
from pathlib import Path
import symtable
import sys
import threading


def _logic(node, leaves):
    if isinstance(node, ast.BoolOp):
        return ["and" if isinstance(node.op, ast.And) else "or", *[_logic(n, leaves) for n in node.values]]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ["not", _logic(node.operand, leaves)]
    for i, leaf in enumerate(leaves):
        if ast.dump(leaf) == ast.dump(node):
            return i
    leaves.append(node)
    return len(leaves) - 1


def _evaluate(logic, values, visited):
    if isinstance(logic, int):
        visited.add(logic)
        return values[logic]
    kind, *children = logic
    if kind == "not":
        return not _evaluate(children[0], values, visited)
    for child in children:
        value = _evaluate(child, values, visited)
        if value == (kind == "or"):
            return value
    return value


class _Decisions(ast.NodeVisitor):
    def __init__(self, source):
        self.source, self.scope, self.entries = source, [], []
        self.roots = {}

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.roots[tuple(self.scope)] = node
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def decision(self, node, truth=True, owner=None):
        leaves = []
        logic = _logic(node, leaves)
        self.entries.append({"node": node, "truth": truth, "scope": tuple(self.scope),
                             "logic": logic, "leaves": leaves, "owner": owner or node})
        # A call's arguments, for example, can contain independent decisions.
        for leaf in leaves:
            if isinstance(leaf, ast.IfExp):
                self.visit(leaf)
            else:
                self.generic_visit(leaf)

    def visit_If(self, node):
        self.decision(node.test, owner=node)
        for statement in [*node.body, *node.orelse]:
            self.visit(statement)

    visit_While = visit_If

    def visit_IfExp(self, node):
        self.decision(node.test, owner=node)
        self.visit(node.body)
        self.visit(node.orelse)

    def visit_Assert(self, node):
        self.decision(node.test, owner=node)
        if node.msg:
            self.visit(node.msg)

    def visit_comprehension(self, node):
        self.visit(node.iter)
        for expression in node.ifs:
            self.decision(expression, owner=node)

    def visit_match_case(self, node):
        if node.guard:
            self.decision(node.guard, owner=node)
        for statement in node.body:
            self.visit(statement)

    def visit_BoolOp(self, node):
        self.decision(node, False)

    visit_Compare = visit_BoolOp

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.decision(node, False)
        else:
            self.generic_visit(node)


def _context_truth(node, values):
    if not values:
        return None
    leaves = []
    logic = _logic(node, leaves)
    try:
        return _evaluate(logic, [values[ast.dump(leaf)] for leaf in leaves], set())
    except KeyError:
        return None


def _effect_shape(value, values):
    if isinstance(value, ast.IfExp):
        truth = _context_truth(value.test, values)
        if truth is not None:
            return _effect_shape(value.body if truth else value.orelse, values)
    if isinstance(value, ast.AST):
        return (type(value).__name__, tuple((k, _effect_shape(v, values)) for k, v in ast.iter_fields(value)))
    return tuple(_effect_shape(v, values) for v in value) if isinstance(value, list) else value


def _effects(node, values):
    """Syntactic effects, not equivalence or authorization of a removal."""
    def visit(value):
        if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and value is not node:
            return
        if isinstance(value, ast.If):
            truth = _context_truth(value.test, values)
            for child in (value.body if truth else value.orelse) if truth is not None else [*value.body, *value.orelse]:
                yield from visit(child)
        elif isinstance(value, (ast.Return, ast.Raise, ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            yield value
        else:
            for child in ast.iter_child_nodes(value):
                yield from visit(child)
    return visit(node)


def analyse(before: str, after: str, path: str) -> tuple[list[dict], list[dict]]:
    """Identify edited original decisions by structure, within their owning scope."""
    if not path.endswith(".py"):
        return [], [{"path": path, "reason": "condition analysis unavailable for this file type"}]
    visitors = []
    try:
        for source in (before, after):
            visitor = _Decisions(source)
            visitor.roots[()] = ast.parse(source)
            visitor.visit(visitor.roots[()])
            visitors.append(visitor)
    except SyntaxError as error:
        return [], [{"path": path, "reason": f"Python parse failed: {error.msg}"}]
    groups = [defaultdict(list), defaultdict(list)]
    for group, visitor in zip(groups, visitors):
        for entry in visitor.entries:
            group[entry["scope"]].append(entry)
    plans, unavailable = [], []
    for scope, old in groups[0].items():
        new = groups[1][scope]
        key = lambda e: (type(e["owner"]).__name__, ast.dump(e["node"]))
        matcher = SequenceMatcher(None, list(map(key, old)), list(map(key, new)), autojunk=False)
        effects = defaultdict(list)
        if scope in visitors[1].roots:
            for effect in _effects(visitors[1].roots[scope], {}):
                effects[type(effect)].append(effect)
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag in {"equal", "insert"}:
                continue
            replacements = list(new[c:d])
            for entry in old[a:b]:
                node, leaves = entry["node"], entry["leaves"]
                replacement = next((e for e in replacements if type(e["owner"]) is type(entry["owner"])), None)
                if replacement:
                    replacements.remove(replacement)
                retained = {ast.dump(n) for n in replacement["leaves"]} if replacement else set()
                touched = [i for i, leaf in enumerate(leaves) if ast.dump(leaf) not in retained]
                # Rewritten boolean structure with the same leaves changes their roles.
                if not touched:
                    touched = list(range(len(leaves)))
                if len(leaves) > 12:
                    unavailable.append({"path": path, "line": node.lineno,
                                        "reason": "decision exceeds 12-condition enumeration limit"})
                    continue
                if any(isinstance(n, ast.Compare) and len(n.ops) > 1 for n in ast.walk(node)):
                    unavailable.append({"path": path, "line": node.lineno,
                                        "reason": "chained comparison left uninstrumented to preserve truth evaluation"})
                    continue
                if any(isinstance(n, (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)) for n in ast.walk(node)):
                    unavailable.append({"path": path, "line": node.lineno,
                                        "reason": "assignment or suspension within decision cannot be measured"})
                    continue
                texts = [ast.get_source_segment(before, leaf) or ast.unparse(leaf) for leaf in leaves]
                contexts = []
                for index in touched:
                    patterns = set()
                    for values in itertools.product((False, True), repeat=len(leaves)):
                        low, high = list(values), list(values)
                        low[index], high[index] = False, True
                        left, right = set(), set()
                        if _evaluate(entry["logic"], low, left) == _evaluate(entry["logic"], high, right):
                            continue
                        patterns.add(tuple((i, values[i]) for i in sorted(left | right) if i != index))
                    for pattern in sorted(patterns):
                        values = {ast.dump(leaves[i]): v for i, v in pattern}
                        retained_effect = any(
                            shape == _effect_shape(candidate, values)
                            for value in (False, True)
                            for inputs in [values | {ast.dump(leaves[index]): value}]
                            for effect in _effects(entry["owner"], inputs)
                            for shape in [_effect_shape(effect, inputs)]
                            for candidate in effects[type(effect)])
                        contexts.append({"index": index, "condition": texts[index],
                                         "when": {texts[i]: v for i, v in pattern},
                                         "values": {str(i): v for i, v in pattern}, "retainedEffect": retained_effect})
                plans.append({"id": f"{path}:{node.lineno}:{node.col_offset}", "path": path,
                              "line": node.lineno, "column": node.col_offset, "truth": entry["truth"],
                              "expression": ast.get_source_segment(before, node), "conditions": texts,
                              "touched": touched, "contexts": contexts, "coupled": not contexts})
    return plans, unavailable


def instrument(source: str, plans: list[dict]) -> str:
    """Instrument only measured decisions. Keep evaluation order and short circuiting."""
    if not plans:
        return source
    selected = {(p["line"], p["column"]): p for p in plans}
    tree = ast.parse(source)
    scopes = [symtable.symtable(source, "<measurement>", "exec")]
    for scope in scopes:
        if any(name.startswith("_workflow_mcdc_") for name in scope.get_identifiers()):
            raise ValueError("instrumentation name collides with source")
        scopes.extend(scope.get_children())

    def call(name, *args):
        return ast.Call(ast.Name("_workflow_mcdc_" + name, ast.Load()), list(args), [])

    class Instrument(ast.NodeTransformer):
        def visit(self, node):
            plan = selected.get((getattr(node, "lineno", None), getattr(node, "col_offset", None)))
            if plan is None:
                return super().visit(node)
            selected.pop((node.lineno, node.col_offset))
            leaves = []

            def wrap(expression):
                if isinstance(expression, ast.BoolOp):
                    return ast.BoolOp(expression.op, [wrap(v) for v in expression.values])
                if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
                    return ast.UnaryOp(expression.op, wrap(expression.operand))
                index = _logic(expression, leaves)
                return call("atom", ast.Constant(plan["id"]), ast.Constant(index),
                            self.generic_visit(expression), ast.Constant(plan["truth"]))

            expression = wrap(node)
            begin = call("begin", ast.Constant(plan["id"]), ast.Constant(tuple(plan["conditions"])))
            end = call("end", ast.Constant(plan["id"]), expression)
            return ast.copy_location(ast.Subscript(ast.Tuple([begin, end], ast.Load()), ast.Constant(1), ast.Load()), node)

    tree = Instrument().visit(tree)
    at = 0
    while at < len(tree.body) and (isinstance(tree.body[at], ast.ImportFrom) and tree.body[at].module == "__future__"
                                  or at == 0 and isinstance(tree.body[at], ast.Expr) and isinstance(tree.body[at].value, ast.Constant)):
        at += 1
    tree.body.insert(at, ast.ImportFrom("_workflow_mcdc_runtime", [ast.alias(n, "_workflow_mcdc_" + n)
                                                               for n in ("begin", "atom", "end")], 0))
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def coverage(plans: list[dict], records: list[dict]) -> list[dict]:
    result = []
    for plan in plans:
        vectors = [r for r in records if r["id"] == plan["id"]]
        contexts = []
        for context in plan["contexts"]:
            index, required = str(context["index"]), context["values"]
            status, pair = "missing", []
            for a in vectors:
                for b in vectors:
                    if (a["outcome"] is None or b["outcome"] is None or a["outcome"] == b["outcome"]
                            or a["values"].get(index) is not False or b["values"].get(index) is not True):
                        continue
                    if any(a["values"].get(k, v) != v or b["values"].get(k, v) != v for k, v in required.items()):
                        continue
                    if any(k not in a["values"] and k not in b["values"] for k in required):
                        continue
                    evaluated = all(k in a["values"] and k in b["values"] for k in required)
                    if evaluated or status == "missing":
                        status, pair = ("evaluated" if evaluated else "unverified"), [a, b]
                    if evaluated:
                        break
                if status == "evaluated":
                    break
            contexts.append({"condition": context["condition"], "when": context["when"], "status": status,
                             "retainedEffect": context["retainedEffect"],
                             **({"pair": pair, "inferred": status == "unverified",
                                 "inferredValues": [{k: v for k, v in required.items() if k not in arm["values"]}
                                                    for arm in pair]} if pair else {})})
        counts = {plan["conditions"][i]: sum(c["condition"] == plan["conditions"][i] for c in contexts)
                  for i in plan["touched"]}
        result.append({k: plan[k] for k in ("id", "path", "line", "expression", "conditions", "coupled")}
                      | {"contexts": contexts, "contextCounts": counts,
                         "large": [text for text, count in counts.items() if count > 16]})
    return result


def missing(report: dict) -> str:
    lines = []
    for decision in report.get("decisions", []):
        def label(text):
            if len(text) <= 80:
                return text
            node = ast.parse("(" + text + ")", mode="eval").body
            brief = ast.unparse(node.func) + "(...)" if isinstance(node, ast.Call) else " ".join(text.split())[:60] + "..."
            return f"{brief} (operand {decision['conditions'].index(text) + 1})"

        contexts = []
        for condition in decision.get("large", []):
            contexts.append(f"{label(condition)}: large context count ({decision['contextCounts'][condition]})")
        if decision.get("coupled"):
            contexts.append("coupled conditions; no independent decisive context")
        for context in decision["contexts"]:
            if context["status"] != "evaluated" and context.get("retainedEffect"):
                when = ", ".join(f"{label(k)}={str(v).lower()}" for k, v in context["when"].items()) or "unconditional"
                contexts.append(f"{label(context['condition'])} [{when}]: {context['status']}")
        if contexts:
            lines.append(f"{decision['path']}:{decision['line']}\n  " + "\n  ".join(contexts))
    lines.extend(f"{item['path']}: {item['reason']}" for item in report.get("unavailable", []))
    counts = {status: sum(c["status"] == status for d in report.get("decisions", []) for c in d["contexts"])
              for status in ("missing", "unverified", "evaluated")}
    advisory = sum(c["status"] != "evaluated" and not c.get("retainedEffect")
                   for d in report.get("decisions", []) for c in d["contexts"])
    if not lines and not advisory:
        return ""
    output = "MC/DC contexts: " + ", ".join(f"{count} {status}" for status, count in counts.items())
    if advisory:
        output += f"\n{advisory} advisory contexts in full evidence: no structurally retained effect established; this does not authorize removal."
    entries = [line for group in lines for line in group.splitlines()]
    for index, line in enumerate(entries):
        if len(output) + len(line) > 2200:
            output += f"\n{len(entries) - index} further context/location entries in full evidence."
            break
        output += "\n" + line
    return output


# Runtime instrumentation is copied with this file into the disposable execution.
# Extra evaluations admit exact builtins only: no overloaded truth, comparison,
# membership, indexing, attributes, descriptors or calls.
_runtime = threading.local()
_expressions = {}
_seen = set()
_comparisons = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
                ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not,
                ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def _plain(value, deep=False):
    if type(value) in (str, bytes, int, float, bool, type(None)):
        return True
    if type(value) in (list, tuple, set, frozenset, dict):
        if not deep:
            return True
        return all(_plain(v, True) for v in value) and (type(value) is not dict or all(_plain(v, True) for v in value.values()))
    return False


def _pure(node, frame):
    if isinstance(node, ast.Name):
        return frame.f_locals[node.id] if node.id in frame.f_locals else frame.f_globals[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Compare):
        left = _pure(node.left, frame)
        for op, expression in zip(node.ops, node.comparators):
            right = _pure(expression, frame)
            if not _plain(left, True) or not _plain(right, True):
                raise ValueError("comparison may run user code")
            if not _comparisons[type(op)](left, right):
                return False
            left = right
        return True
    raise ValueError("expression may run user code")


def begin(identifier, expressions):
    frame = sys._getframe(1)
    shadows = {}
    for i, expression in enumerate(expressions):
        try:
            if expression not in _expressions:
                _expressions[expression] = ast.parse(expression, mode="eval").body
            value = _pure(_expressions[expression], frame)
            if not _plain(value):
                raise ValueError("truth may run user code")
            shadows[str(i)] = bool(value)
        except (ValueError, KeyError, TypeError, RecursionError):
            # Later leaves could observe state changed by this unevaluated operation.
            # Earlier exact-builtin expressions can still be measured safely.
            break
    if not hasattr(_runtime, "frames"):
        _runtime.frames = []
    _runtime.frames.append({"id": identifier, "values": dict(shadows), "additional": shadows, "production": {}})


def atom(identifier, index, value, truth):
    record = next(r for r in reversed(_runtime.frames) if r["id"] == identifier)
    if truth or _plain(value):
        measured = bool(value)
        if str(index) in record["production"] and record["values"].get(str(index)) != measured:
            record["values"][str(index)] = None
        elif record["values"].get(str(index), measured) is not None:
            record["values"][str(index)] = measured
        record["production" if truth or type(value) is bool else "additional"][str(index)] = measured
        if truth:
            return measured
    return value


def end(identifier, value):
    position = next(i for i in range(len(_runtime.frames) - 1, -1, -1) if _runtime.frames[i]["id"] == identifier)
    record = _runtime.frames.pop(position)
    record["outcome"] = bool(value) if _plain(value) else None
    encoded = json.dumps(record, sort_keys=True)
    if encoded not in _seen:
        _seen.add(encoded)
        with Path(os.environ["WORKFLOW_MCDC_LOG"]).open("a") as stream:
            stream.write(encoded + "\n")
    return value
