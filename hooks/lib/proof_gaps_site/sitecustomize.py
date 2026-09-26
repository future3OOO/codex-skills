"""Observer loaded by a proof's own interpreter (PYTHONPATH) while proof_gaps reruns it.

Records into PROOF_GAPS_OUT/<pid>.json the lines of PROOF_GAPS_FILES executed, and every value returned,
yielded or raised into test code (PROOF_GAPS_TESTS) or by changed code into anything outside it (both
newline-separated paths). Child Python processes inherit the environment and report too."""
import atexit
import json
import os
import re
import sys

_OUT = os.environ.get("PROOF_GAPS_OUT")
if _OUT and hasattr(sys, "monitoring"):
    _COPY = os.environ["PROOF_GAPS_ROOT"]  # masked in every record: nothing may depend on which copy ran
    _ROOT = os.path.join(_COPY, "")
    _FILES = {_ROOT + f for f in os.environ["PROOF_GAPS_FILES"].split("\n") if f}
    _TESTS = {_ROOT + f for f in os.environ["PROOF_GAPS_TESTS"].split("\n") if f}
    _NOISE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{32}\b|\b[0-9a-f-]{36}\b|/tmp/[\w./-]+")
    _M = sys.monitoring
    _lines, _events = set(), []

    def _line(code, line):
        if code.co_filename in _FILES:
            _lines.add(f"{code.co_filename[len(_ROOT):]}:{line}")
        return _M.DISABLE

    def _seen(code, value, kind):
        # Frames: _seen, the callback lambda, the returning function, then its caller (absent at the stack's top).
        caller = sys._getframe(2).f_back
        if caller is None or len(_events) >= 2000:
            return
        # A value crosses into what the proof observes when test code receives it, or when code under change
        # hands it to anything outside itself (a script, a child process's -c code, a library).
        callee_changed, caller_changed = code.co_filename in _FILES, caller.f_code.co_filename in _FILES
        if (caller.f_code.co_filename in _TESTS and code.co_filename not in _TESTS) or (callee_changed and not caller_changed):
            try:
                text = repr(value)
            except Exception as error:  # an object whose repr raises is itself an observation
                text = f"<repr raised {type(error).__name__}>"
            _events.append([code.co_qualname, kind, _NOISE.sub("#", text.replace(_COPY, "<root>")[:300])])

    _M.use_tool_id(4, "proof-gaps")
    _M.register_callback(4, _M.events.LINE, _line)
    _M.register_callback(4, _M.events.PY_RETURN, lambda code, offset, value: _seen(code, value, "ret"))
    _M.register_callback(4, _M.events.PY_YIELD, lambda code, offset, value: _seen(code, value, "yield"))
    _M.register_callback(4, _M.events.PY_UNWIND, lambda code, offset, error: _seen(code, f"{type(error).__name__}: {error}", "exc"))
    _M.set_events(4, _M.events.LINE | _M.events.PY_RETURN | _M.events.PY_YIELD | _M.events.PY_UNWIND)

    @atexit.register
    def _write():
        _M.set_events(4, 0)
        with open(os.path.join(_OUT, f"{os.getpid()}.json"), "w", encoding="utf-8") as handle:
            json.dump({"lines": sorted(_lines), "events": _events}, handle)
