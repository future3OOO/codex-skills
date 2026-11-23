"""python3 operation.py --edits FILE[,FILE ...] 'COMMAND' ['COMMAND' ...]: run one input of an operation, its commands in
order in one tree, and measure each. A Python program is counted in calls, of the whole and of the edited files, with
Python's allocation peak; any program gets its peak memory from the operating system, and one that is no Python program
its seconds and CPU seconds. A tree with uncommitted changes is measured as the commit it will be. The last line is
WORKFLOW_METRICS; a failing command ends the run."""
import json, os, re, shlex, subprocess, sys, tempfile, time

COUNT = """import cProfile, json, pstats, runpy, sys, tracemalloc
out, edits, sys.argv = sys.argv[1], set(sys.argv[2].split(chr(0))), sys.argv[3:]
run = (lambda: runpy.run_module(sys.argv.pop(1), run_name="__main__", alter_sys=True)) if sys.argv[0] == "-m" else (lambda: runpy.run_path(sys.argv[0], run_name="__main__"))
sys.path.insert(0, "" if sys.argv[0] == "-m" else __import__("os").path.dirname(sys.argv[0])); tracemalloc.start(); profile = cProfile.Profile(); status = 0
try: profile.runcall(run)
except SystemExit as exit: status = exit.code if isinstance(exit.code, int) else int(bool(exit.code))
rows, peak = pstats.Stats(profile).stats, tracemalloc.get_traced_memory()[1]
real = __import__("os").path.realpath; inside = {name for name in rows if real(name[0]) in edits}
edited = sum(rows[name][1] for name in inside) + sum((made[0] if isinstance(made, tuple) else made) for name, row in rows.items() if name not in inside for caller, made in row[4].items() if caller in inside)
json.dump({"calls": sum(row[1] for row in rows.values()), "editedCalls": edited, "pythonPeakKb": peak // 1024, "top": [[rows[name][1], name[0], name[1], name[2]] for name in sorted(inside, key=lambda name: -rows[name][1])[:6]]}, open(out, "w"))
sys.exit(status)"""
if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
    work = tempfile.mkdtemp(prefix="operation-")
    copied = subprocess.run(f"git clone -q --shared . {work} && git -C {work} rm -rqf . && git ls-files -z -c -o --exclude-standard | xargs -0 -r cp --parents -t {work} 2>/dev/null; "
                            f"git -C {work} add -A && git -C {work} -c user.name=operation -c user.email=operation@localhost commit -q --allow-empty -m candidate", shell=True, capture_output=True, text=True)
    if copied.returncode:
        sys.exit("the tree could not be copied as a commit: " + copied.stderr[-600:])
    os.chdir(work)
edits, measured = [os.path.realpath(name) for name in sys.argv[2].split(",")], {}
for number, step in enumerate(sys.argv[3:], 1):
    words, counted = [os.path.expandvars(word) for word in shlex.split(step)], tempfile.mkstemp(suffix=".json")[1]
    python = next((place for place, word in enumerate(words[:-1]) if re.fullmatch(r"python[\d.]*", os.path.basename(word)) and words[place + 1] != "-c"), None)
    if python is not None:
        words[python + 1:python + 1] = ["-c", COUNT, counted, "\0".join(edits)]
    begun, child = time.perf_counter(), subprocess.Popen(words, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    errors = child.stderr.read()
    _, status, used = os.wait4(child.pid, 0)
    if os.waitstatus_to_exitcode(status):
        sys.exit(f"step {number} exited {os.waitstatus_to_exitcode(status)}: {step}\n{errors[-1500:]}")
    print(f"step {number}: {step}")
    if python is None:
        result = {"seconds": round(time.perf_counter() - begun, 3), "cpuSeconds": round(used.ru_utime + used.ru_stime, 3)}
        print(f"  no Python program, so no call count: {result['seconds']} s, {result['cpuSeconds']} CPU s; memory peak {used.ru_maxrss} KB")
    else:
        result = json.load(open(counted))
        print(f"  calls: {result['calls']} in all, {result['editedCalls']} by the edited code; memory peak {used.ru_maxrss} KB, Python allocations {result['pythonPeakKb']} KB; the edited code's functions by calls:")
        for count, file, line, name in result.pop("top"):
            print(f"  {count} {os.path.relpath(file)}:{line} {name}")
    measured.update({f"step{number}.{key}": value for key, value in {**result, "peakKb": used.ru_maxrss}.items()})
print("WORKFLOW_METRICS=" + json.dumps(measured))
