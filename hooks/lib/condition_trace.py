"""Copied as sitecustomize.py, beside the probed decisions, onto a traced original run's PYTHONPATH: for each
evaluation of a probed decision, the conditional jumps it took and the truth of its bare names holding builtin
values. It evaluates no expression and calls no code of the traced program."""
import json
import os
import sys

SAMPLED = (bool, int, float, str, bytes, list, tuple, dict, set, frozenset, type(None))
JUMPS = {"POP_JUMP_IF_TRUE": "true", "POP_JUMP_IF_FALSE": "false", "POP_JUMP_IF_NONE": "none", "POP_JUMP_IF_NOT_NONE": "notnone"}


def _install(decisions: list, out_path: str) -> None:
    import atexit
    import dis

    owners: dict = {}
    for decision in decisions:
        owners.setdefault((decision["file"], decision["qualname"]), []).append(decision)
    instructions: dict = {}
    evaluating: dict = {}
    seen: dict = {decision["id"]: set() for decision in decisions}

    def close(key) -> None:
        names, jumps = evaluating.pop(key)
        seen[key[0]].add((tuple(sorted(names.items())), tuple(sorted(jumps.items()))))

    def branch(code, offset, destination):
        owned = next((value for (path, name), value in owners.items()
                      if code.co_qualname == name and code.co_filename.endswith("/" + path)), None)
        if owned is None:
            return sys.monitoring.DISABLE
        if code not in instructions:
            instructions[code] = {item.offset: item for item in dis.get_instructions(code)}
        item = instructions[code].get(offset)
        where = item and (item.positions.lineno, item.positions.end_lineno, item.positions.col_offset, item.positions.end_col_offset)
        inside = [decision for decision in owned if item and item.opname in JUMPS and where[0] is not None
                  and (decision["span"][0], decision["span"][2]) <= (where[0], where[2])
                  and (where[1], where[3]) <= (decision["span"][1], decision["span"][3])]
        if not inside:  # every evaluation starts at its decision's first jump, so jumps elsewhere are never needed
            return sys.monitoring.DISABLE
        frame = sys._getframe(1)
        for decision in inside:
            key = (decision["id"], id(frame))
            if key in evaluating and where in evaluating[key][1]:
                close(key)
            if key not in evaluating:
                scope = {**frame.f_globals, **frame.f_locals}
                evaluating[key] = ({name: bool(scope[name]) for name in decision["names"]
                                    if name in scope and type(scope[name]) in SAMPLED}, {})
            evaluating[key][1][where] = (JUMPS[item.opname], destination == item.argval)
        return None

    def dump() -> None:
        for key in list(evaluating):
            close(key)
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({name: [{"names": dict(names), "jumps": [[*span, *jump] for span, jump in jumps]}
                                            for names, jumps in evaluations] for name, evaluations in seen.items()}) + "\n")

    sys.monitoring.use_tool_id(4, "workflow-conditions")
    sys.monitoring.register_callback(4, sys.monitoring.events.BRANCH, branch)
    sys.monitoring.set_events(4, sys.monitoring.events.BRANCH)
    atexit.register(dump)


if hasattr(sys, "monitoring") and __name__ == "sitecustomize":
    with open(os.path.join(os.path.dirname(__file__), "conditions.json"), encoding="utf-8") as handle:
        _install(json.load(handle), os.path.join(os.environ["TMPDIR"], "workflow-conditions.jsonl"))
