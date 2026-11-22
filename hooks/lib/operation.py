"""python3 operation.py --edits FILE[,FILE ...] 'COMMAND' ['COMMAND' ...]: run the operation a design touches and count
its work in calls, of the whole and of the edited files, per step. A step that is no Python program has no call
count: its seconds and CPU seconds stand for it. The last line is WORKFLOW_METRICS; a failing step ends the run."""
import json, os, pstats, re, resource, shlex, subprocess, sys, tempfile, time

edits = {os.path.realpath(name) for name in sys.argv[2].split(",")} if sys.argv[1:2] == ["--edits"] else set()
totals: dict[str, float] = {}
for number, step in enumerate(sys.argv[3 if edits else 1:], 1):
    words, profile = [os.path.expandvars(word) for word in shlex.split(step)], None
    python = next((place for place, word in enumerate(words[:-1]) if re.fullmatch(r"python[\d.]*", os.path.basename(word)) and words[place + 1] != "-c"), None)
    if python is not None:
        profile = tempfile.mkstemp(suffix=".profile")[1]
        words[python + 1:python + 1] = ["-m", "cProfile", "-o", profile]
    spent, begun = resource.getrusage(resource.RUSAGE_CHILDREN), time.perf_counter()
    done = subprocess.run(words, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    seconds, after = time.perf_counter() - begun, resource.getrusage(resource.RUSAGE_CHILDREN)
    if done.returncode:
        sys.exit(f"step {number} exited {done.returncode}: {step}\n{done.stderr[-1500:]}")
    print(f"step {number}: {step}")
    if profile is None:
        measured = {"seconds": round(seconds, 3), "cpuSeconds": round(after.ru_utime + after.ru_stime - spent.ru_utime - spent.ru_stime, 3)}
        print(f"  no Python program, so no call count: {measured['seconds']} s, {measured['cpuSeconds']} CPU s")
    else:
        rows = pstats.Stats(profile).stats
        inside = {function for function in rows if os.path.realpath(function[0]) in edits}
        # the edited code's work: every call of its functions, and every call they make into other code
        edited = sum(row[1] for function, row in rows.items() if function in inside) + sum(
            (made[0] if isinstance(made, tuple) else made) for function, row in rows.items() if function not in inside for caller, made in row[4].items() if caller in inside)
        measured = {"calls": sum(row[1] for row in rows.values()), "editedCalls": edited}
        print(f"  calls: {measured['calls']} in all, {edited} by the code to be edited; its functions by calls:")
        for function in sorted(inside, key=lambda function: -rows[function][1])[:8]:
            print(f"  {rows[function][1]} {os.path.relpath(function[0])}:{function[1]} {function[2]}")
    for key, value in measured.items():
        totals[key] = round(totals.get(key, 0) + value, 3)
        totals[f"step{number}.{key}"] = value
print("WORKFLOW_METRICS=" + json.dumps(totals))
