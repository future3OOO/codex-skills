"""Copied as sitecustomize.py, beside the probed decisions, onto either traced run's PYTHONPATH: for each
evaluation of a probed decision, the conditional jumps it took and the truth of its bare names holding builtin
values. It evaluates no expression and calls no code of the traced program."""
import hashlib
import json
import marshal
import os
import sys
from collections import Counter

SAMPLED = {id(kind) for kind in (bool, int, float, str, bytes, list, tuple, dict, set, frozenset, type(None))}
JUMPS = {"POP_JUMP_IF_TRUE": "true", "POP_JUMP_IF_FALSE": "false", "POP_JUMP_IF_NONE": "none", "POP_JUMP_IF_NOT_NONE": "notnone"}


def _install(decisions: list, out_path: str) -> None:
    import atexit
    import dis

    owners: dict = {}
    for decision in decisions:
        owners.setdefault((decision["file"], decision["qualname"]), []).append(decision)
    instructions: dict = {}
    branches: dict = {}
    evaluating: dict = {}
    seen: dict = {decision["id"]: Counter() for decision in decisions}

    def close(key) -> None:
        names, jumps, inputs = evaluating.pop(key)
        seen[key[0]][tuple(sorted(names.items())), tuple(sorted(jumps.items())), inputs] += 1

    def branch(code, offset, destination):
        if (code, offset) not in branches:
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
            branches[code, offset] = item, where, inside
        item, where, inside = branches[code, offset]
        if not inside:
            return sys.monitoring.DISABLE
        frame = sys._getframe(1)
        sampled = {}
        for decision in inside:
            key = (decision["id"], id(frame))
            if key in evaluating and where in evaluating[key][1]:
                close(key)
            if key not in evaluating:
                names = tuple(decision["names"])
                if names not in sampled:
                    local, global_ = frame.f_locals, frame.f_globals
                    values = {name: local.get(name, global_.get(name)) for name in decision["names"] if name in local or name in global_}
                    inputs = []
                    for name, value in values.items():
                        encoded = None
                        pending, visited = [value], set()
                        while pending:
                            child = pending.pop()
                            kind = type(child)
                            if id(kind) not in SAMPLED:
                                break
                            if kind in (list, tuple, dict, set, frozenset) and id(child) not in visited:
                                visited.add(id(child))
                                pending.extend(child)
                                if kind is dict:
                                    pending.extend(child.values())
                        else:
                            try:
                                encoded = marshal.dumps(marshal.loads(marshal.dumps(value)))
                            except ValueError:  # safe builtin graphs can exceed marshal's depth limit
                                encoded = None
                        if encoded is None:
                            # Descriptor calls bypass metaclass overrides; serialization never sees the opaque value.
                            kind = type(value)
                            module = type.__dict__["__module__"].__get__(kind)
                            encoded = str.encode(module if type(module) is str else "?") + b"." + str.encode(type.__dict__["__qualname__"].__get__(kind))
                        inputs.append((name, encoded))
                    sampled[names] = ({name: bool(value) for name, value in values.items() if id(type(value)) in SAMPLED},
                                       hashlib.sha256(marshal.dumps(inputs, 2)).hexdigest())
                values, inputs = sampled[names]
                evaluating[key] = (values, {}, inputs)
            evaluating[key][1][where] = (JUMPS[item.opname], destination == item.argval)
        return None

    def dump() -> None:
        for key in list(evaluating):
            close(key)
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({name: [[{"names": dict(names), "jumps": [[*span, *jump] for span, jump in jumps],
                                             "input": inputs}, count]
                                            for (names, jumps, inputs), count in observations.items()]
                                     for name, observations in seen.items()}) + "\n")

    sys.monitoring.use_tool_id(4, "workflow-conditions")
    sys.monitoring.register_callback(4, sys.monitoring.events.BRANCH, branch)
    sys.monitoring.set_events(4, sys.monitoring.events.BRANCH)
    atexit.register(dump)


if hasattr(sys, "monitoring") and __name__ == "sitecustomize":
    with open(os.path.join(os.path.dirname(__file__), "conditions.json"), encoding="utf-8") as handle:
        _install(json.load(handle), os.path.join(os.environ["TMPDIR"], "workflow-conditions.jsonl"))
