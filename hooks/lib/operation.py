"""python3 operation.py 'COMMAND' ['COMMAND' ...]: run the commands a person really runs, in that order and number, and
measure the whole of it: per step its seconds, the CPU seconds of everything it started and, for a Python step, its
function calls and where they go. The last line is WORKFLOW_METRICS; a failing step ends the run with its status."""
import os, pstats, re, resource, shlex, subprocess, sys, tempfile, time


def used() -> float:
    spent = resource.getrusage(resource.RUSAGE_CHILDREN)
    return spent.ru_utime + spent.ru_stime


totals = {"calls": 0, "cpuSeconds": 0.0, "seconds": 0.0}
for number, step in enumerate(sys.argv[1:], 1):
    words, profile = [os.path.expandvars(word) for word in shlex.split(step)], None
    python = next((place for place, word in enumerate(words[:-1]) if re.fullmatch(r"python[\d.]*", os.path.basename(word)) and words[place + 1] != "-c"), None)
    if python is not None:  # the step's own interpreter counts its calls; what it starts shows in the CPU seconds
        profile = tempfile.mkstemp(suffix=".profile")[1]
        words[python + 1:python + 1] = ["-m", "cProfile", "-o", profile]
    cpu, begun = used(), time.perf_counter()
    done = subprocess.run(words, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    seconds, cpu = time.perf_counter() - begun, used() - cpu
    if done.returncode:
        sys.exit(f"step {number} exited {done.returncode}: {step}\n{done.stderr[-1500:]}")
    rows = list(pstats.Stats(profile).stats.items()) if profile else []
    calls = sum(row[1][1] for row in rows)
    print(f"step {number}: {step[:110]}\n  {seconds:.3f} s, {cpu:.3f} CPU s" + (f", {calls} calls; the most called and the slowest by their own seconds (calls, own seconds, function):" if profile else ""))
    for (file, line, name), (_, count, own, _, _) in dict(row for order in (1, 2) for row in sorted(rows, key=lambda row: -row[1][order])[:6]).items():
        print(f"  {count} {own:.3f} {os.path.relpath(file) if file.startswith(os.getcwd()) else os.path.basename(file)}:{line} {name}")
    for key, value in (("calls", calls), ("cpuSeconds", cpu), ("seconds", seconds)):
        totals[key] += value
        totals[f"step{number}.{key}"] = round(value, 3)
print("WORKFLOW_METRICS=" + __import__("json").dumps({key: round(value, 3) for key, value in totals.items()}))
