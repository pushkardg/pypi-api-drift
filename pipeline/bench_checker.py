"""Measure apidrift-check cost: wall time, peak resident memory, files per second.
Usage: python bench_checker.py DATA_DIR FREEZE_TXT OUT_JSON"""
import json, os, resource, subprocess, sys, time

D, FREEZE, OUT = sys.argv[1:4]
SITE = "/usr/local/lib/python3.13/dist-packages"
CLI = [sys.executable, "-m", "apidrift.check"]
ENV = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "release"))


def count_py(paths):
    n = loc = 0
    for p in paths:
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__",)]
            for f in files:
                if f.endswith(".py"):
                    n += 1
                    loc += sum(1 for _ in open(os.path.join(root, f), errors="replace"))
    return n, loc


def run(name, paths, extra):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t = time.time()
    r = subprocess.run(CLI + paths + extra + ["--data", D], env=ENV, capture_output=True, text=True)
    wall = time.time() - t
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    n, loc = count_py(paths) if all(os.path.isdir(p) for p in paths) else (len(paths), sum(
        sum(1 for _ in open(p)) for p in paths))
    res = {"scenario": name, "files": n, "lines": loc, "seconds": round(wall, 1),
           "cpu_seconds": round((after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime), 1),
           "peak_rss_mb": round(after.ru_maxrss / 1024), "findings": r.stdout.count("\n"),
           "files_per_second": round(n / wall, 1), "seconds_per_1k_files": round(1000 * wall / max(n, 1), 1)}
    print(res, flush=True)
    return res


one = "/tmp/apidrift_bench_one.py"
open(one, "w").write("import pandas as pd\ndf = pd.DataFrame()\ndf.append({'a': 1})\n")
results = [
    run("one file, one pin (cold start)", [one], ["--pin", "pandas==2.2.3"]),
    run("requests package (17 files), all installed pins", [f"{SITE}/requests"], ["-r", FREEZE]),
    run("matplotlib package, all installed pins", [f"{SITE}/matplotlib"], ["-r", FREEZE]),
    run("pandas package, all installed pins", [f"{SITE}/pandas"], ["-r", FREEZE]),
]
json.dump({"machine": "2 vCPU, 7 GB RAM container (shared with other jobs)", "results": results}, open(OUT, "w"),
          indent=1)
