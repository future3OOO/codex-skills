"""Observer loaded by a proof's own interpreter (PYTHONPATH) while proof_gaps reruns it.

Records into PROOF_GAPS_OUT/<pid>.json the lines of PROOF_GAPS_FILES executed, and every value returned,
yielded or raised into test code (PROOF_GAPS_TESTS) or by changed code into anything outside it (both
newline-separated paths). Child Python processes inherit the environment and report too. Uses
sys.monitoring on Python 3.12+ and sys.settrace on older interpreters, so the proof keeps its own interpreter."""
import atexit
import json
import os
import re
import sys

_OUT = os.environ.get("PROOF_GAPS_OUT")
if _OUT:
    _COPY = os.environ["PROOF_GAPS_ROOT"]  # masked in every record: nothing may depend on which copy ran
    _ROOT = os.path.join(_COPY, "")
    _FILES = {_ROOT + f for f in os.environ["PROOF_GAPS_FILES"].split("\n") if f}
    _TESTS = {_ROOT + f for f in os.environ["PROOF_GAPS_TESTS"].split("\n") if f}
    _NOISE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{32}\b|\b[0-9a-f-]{36}\b|/tmp/[\w./-]+")
    _lines, _events = set(), []

    def _observed(code, caller):
        # A value crosses into what the proof observes when test code receives it, or when code under change
        # hands it to anything outside itself (a script, a child process's -c code, a library).
        callee_changed, caller_changed = code.co_filename in _FILES, caller.f_code.co_filename in _FILES
        return (caller.f_code.co_filename in _TESTS and code.co_filename not in _TESTS) or (callee_changed and not caller_changed)

    def _seen(code, caller, value, kind):
        if caller is None or len(_events) >= 2000 or not _observed(code, caller):
            return
        try:
            text = f"{type(value).__name__}: {value}" if kind == "exc" else repr(value)
        except Exception as error:  # an object whose repr raises is itself an observation
            text = f"<repr raised {type(error).__name__}>"
        name = getattr(code, "co_qualname", code.co_name)
        _events.append([name, kind, _NOISE.sub("#", text.replace(_COPY, "<root>")[:300])])

    if hasattr(sys, "monitoring"):  # Python 3.12+: low-overhead events; a line is recorded once, then disabled
        _M = sys.monitoring

        def _line(code, line):
            if code.co_filename in _FILES:
                _lines.add(f"{code.co_filename[len(_ROOT):]}:{line}")
            return _M.DISABLE

        # From each callback lambda, frame 1 is the returning function and its f_back is the caller.
        _M.use_tool_id(4, "proof-gaps")
        _M.register_callback(4, _M.events.LINE, _line)
        _M.register_callback(4, _M.events.PY_RETURN, lambda code, o, v: _seen(code, sys._getframe(1).f_back, v, "ret"))
        _M.register_callback(4, _M.events.PY_YIELD, lambda code, o, v: _seen(code, sys._getframe(1).f_back, v, "yield"))
        _M.register_callback(4, _M.events.PY_UNWIND, lambda code, o, e: _seen(code, sys._getframe(1).f_back, e, "exc"))
        _M.set_events(4, _M.events.LINE | _M.events.PY_RETURN | _M.events.PY_YIELD | _M.events.PY_UNWIND)
        _stop = lambda: _M.set_events(4, 0)
    else:  # older Pythons: the same records through sys.settrace, traced only in frames that can produce one
        import dis
        import threading
        _raised, _normal = {}, {dis.opmap[name] for name in ("RETURN_VALUE", "YIELD_VALUE") if name in dis.opmap}

        def _local(frame, event, arg):
            if event == "line" and frame.f_code.co_filename in _FILES:
                _lines.add(f"{frame.f_code.co_filename[len(_ROOT):]}:{frame.f_lineno}")
            elif event == "exception":
                _raised[frame] = arg[1]
            elif event == "return":
                # A frame stopped anywhere but a return or yield is unwinding with its latest exception.
                error = _raised.pop(frame, None)
                unwinding = error is not None and frame.f_code.co_code[frame.f_lasti] not in _normal
                _seen(frame.f_code, frame.f_back, error if unwinding else arg, "exc" if unwinding else "ret")
            return _local

        def _call(frame, event, arg):
            code, caller = frame.f_code, frame.f_back
            if code.co_filename in _FILES or (caller is not None and _observed(code, caller)):
                return _local
            return None

        sys.settrace(_call)
        threading.settrace(_call)
        _stop = lambda: (sys.settrace(None), threading.settrace(None))

    @atexit.register
    def _write():
        _stop()
        with open(os.path.join(_OUT, f"{os.getpid()}.json"), "w", encoding="utf-8") as handle:
            json.dump({"lines": sorted(_lines), "events": _events}, handle)
