"""First on a traced comparison arm's PYTHONPATH: for each evaluation of a probed decision, the
conditional jumps it took, the truth of its bare names holding builtin values and the running test as
its runner reports it. It evaluates no expression and calls no code of the traced program."""
import json
import os
import sys

SAMPLED = (bool, int, float, str, bytes, list, tuple, dict, set, frozenset, type(None))
JUMPS = {"POP_JUMP_IF_TRUE": "true", "POP_JUMP_IF_FALSE": "false", "POP_JUMP_IF_NONE": "none", "POP_JUMP_IF_NOT_NONE": "notnone"}


def _install(probe_path: str, out_path: str) -> None:
    import atexit
    import dis

    with open(probe_path, encoding="utf-8") as handle:
        decisions = json.load(handle)
    owners: dict = {}
    for index, decision in enumerate(decisions):
        owners.setdefault((decision["file"], decision["qualname"]), []).append((index, decision))
    instructions: dict = {}
    evaluating: dict = {}
    seen: dict = {index: set() for index in range(len(decisions))}

    def close(key) -> None:
        names, jumps, test = evaluating.pop(key)
        seen[key[0]].add((tuple(sorted(names.items())), tuple(sorted(jumps.items())), test))

    def running_test(frame) -> str | None:
        """The outermost pytest item's node id, else the outermost unittest TestCase.run's test id."""
        item = case = None
        while frame is not None:
            code = frame.f_code
            if code.co_name == "runtest" and f"{os.sep}_pytest{os.sep}" in code.co_filename:
                item = frame.f_locals.get("self")
            elif code.co_name == "run" and code.co_filename.endswith(os.path.join("unittest", "case.py")):
                case = frame.f_locals.get("self")
            frame = frame.f_back
        if item is not None:
            return getattr(item, "nodeid", None)
        name = getattr(case, "_testMethodName", None)
        return name and f"{type(case).__module__}.{type(case).__qualname__}.{name}"

    def branch(code, offset, destination):
        owned = next((value for (path, name), value in owners.items()
                      if code.co_qualname == name and code.co_filename.endswith("/" + path)), None)
        if owned is None:
            return sys.monitoring.DISABLE
        if code not in instructions:
            instructions[code] = {item.offset: item for item in dis.get_instructions(code)}
        item = instructions[code].get(offset)
        if item is None or item.opname not in JUMPS or item.positions.lineno is None:
            return None
        where = (item.positions.lineno, item.positions.end_lineno, item.positions.col_offset, item.positions.end_col_offset)
        frame = sys._getframe(1)
        for index, decision in owned:
            line, end_line, column, end_column = decision["span"]
            key = (index, id(frame))
            inside = (line, column) <= (where[0], where[2]) and (where[1], where[3]) <= (end_line, end_column)
            if key in evaluating and (not inside or where in evaluating[key][1]):
                close(key)
            if inside:
                if key not in evaluating:
                    scope = {**frame.f_globals, **frame.f_locals}
                    values = {name: scope[name] for name in decision["names"] if name in scope}
                    evaluating[key] = ({name: bool(value) for name, value in values.items() if type(value) in SAMPLED}, {},
                                       running_test(frame))
                evaluating[key][1][where] = (JUMPS[item.opname], destination == item.argval)
        return None

    def dump() -> None:
        for key in list(evaluating):
            close(key)
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({index: [{"names": dict(names), "jumps": [[*span, *jump] for span, jump in jumps], "test": test}
                                             for names, jumps, test in evaluations] for index, evaluations in seen.items()}) + "\n")

    sys.monitoring.use_tool_id(4, "workflow-conditions")
    sys.monitoring.register_callback(4, sys.monitoring.events.BRANCH, branch)
    sys.monitoring.set_events(4, sys.monitoring.events.BRANCH)
    atexit.register(dump)


if hasattr(sys, "monitoring") and os.environ.get("WORKFLOW_CONDITION_PROBES"):
    _install(os.environ["WORKFLOW_CONDITION_PROBES"], os.environ["WORKFLOW_CONDITION_RESULTS"])
