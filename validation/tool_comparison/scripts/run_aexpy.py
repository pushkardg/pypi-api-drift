"""Run AexPy (extract -> diff) on every pair in pairs.csv.

Usage: python3 run_aexpy.py <workdir> <aexpy_version> [ids...]
Per release: fresh `uv venv --python 3.12` with aexpy+pip, then
  echo <project>@<version> | aexpy extract - <api>.json -r --no-temp
(AexPy downloads the wheel and pip-installs it with deps into that env, then imports it.)
The env is deleted right after. Retries once with Python 3.11 if extraction fails.
Then `aexpy diff old.json new.json diff.json` and `aexpy report` (in a separate tool env).
"""
import csv, json, os, shutil, statistics, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
CH_FIELDS = ["id", "project", "from_version", "to_version", "path", "change_type", "param", "tool_label", "breaking", "raw"]
RUN_FIELDS = ["tool", "tool_version", "id", "project", "status", "seconds", "notes"]
EXTRACT_TIMEOUT = 1800
ENV = dict(os.environ, PYTHONUTF8="1")

# AexPy BreakingRank: Unknown=-1, Compatible=0, Low=30, Medium=60, High=100
RANKS = {-1: "Unknown", 0: "Compatible", 30: "Low", 60: "Medium", 100: "High"}

KIND_MAP = {
    "RemoveModule": "removed", "RemoveClass": "removed", "RemoveFunction": "removed",
    "RemoveMethod": "removed", "RemoveAttribute": "removed", "RemoveInstanceAttribute": "removed",
    "RemoveAlias": "removed", "RemoveExternalAlias": "removed",
    "RemoveRequiredParameter": "param_removed", "RemoveOptionalParameter": "param_removed",
    "RemoveRequiredCandidate": "param_removed", "RemoveOptionalCandidate": "param_removed",
    "RemoveVarPositional": "vararg_removed", "RemoveVarKeyword": "varkw_removed",
    "AddRequiredParameter": "required_param_added", "AddRequiredCandidate": "required_param_added",
    "RemoveParameterDefault": "default_removed",
    "MoveParameter": "positional_reordered",
}


def append(path, fields, rows):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def sh(cmd, timeout=None, inp=None, cwd=None):
    return subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout, env=ENV, cwd=cwd)


# Packages pre-installed into the extraction env (undeclared runtime deps of the release)
EXTRA = {"requirements-parser": ["setuptools<81"]}  # 0.10.2 imports pkg_resources without declaring setuptools


def pypi_name(project, version):
    import urllib.request
    with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/{version}/json", timeout=60) as r:
        return json.load(r)["info"]["name"]  # AexPy asserts release name == dist-info Name (e.g. GitPython)


def make_env(path, py, aexpy_version, extra=()):
    shutil.rmtree(path, ignore_errors=True)
    r = sh(["uv", "venv", "-q", "--python", py, str(path)])
    if r.returncode:
        raise RuntimeError("uv venv failed: " + r.stderr[-300:])
    r = sh(["uv", "pip", "install", "-q", "--python", str(path / "bin" / "python"), f"aexpy=={aexpy_version}", "pip", *extra])
    if r.returncode:
        raise RuntimeError("uv pip install aexpy failed: " + r.stderr[-300:])


def extract(work, project, version, out, aexpy_version):
    """Returns (ok, seconds_extract_only, python_used, note)."""
    notes = []
    dt = 0.0
    name = pypi_name(project, version)
    if name != project:
        notes.append(f"release id {name}@{version}")
    if EXTRA.get(project):
        notes.append("pre-installed " + ",".join(EXTRA[project]))
    for py in ("3.12",):  # aexpy 0.4.3 requires Python>=3.12, so no 3.11 fallback
        env = work / "env"
        make_env(env, py, aexpy_version, EXTRA.get(project, ()))
        t0 = time.time()
        try:
            r = sh([str(env / "bin" / "aexpy"), "extract", "-", str(out), "-r", "--no-temp"],
                   timeout=EXTRACT_TIMEOUT, inp=f"{name}@{version}\n", cwd=str(work))
            dt = time.time() - t0
            ok = r.returncode == 0 and out.exists()
            if ok:
                try:
                    dj = json.load(open(out))
                    st = dj.get("state")
                    ok = st == 1
                    notes.append("api=" + "/".join(str(len(dj.get(k) or {})) for k in ("modules", "classes", "functions", "attributes")))
                except Exception as e:
                    ok = False
                    notes.append(f"py{py}: bad json {e}")
            if ok:
                shutil.rmtree(env, ignore_errors=True)
                return True, dt, py, "; ".join(notes)
            tail = " ".join((r.stdout[-400:] + " " + r.stderr[-400:]).split())
            notes.append(f"py{py}: rc={r.returncode} {tail[:400]}")
        except subprocess.TimeoutExpired:
            dt = time.time() - t0
            notes.append(f"py{py}: extract timeout {EXTRACT_TIMEOUT}s")
            shutil.rmtree(env, ignore_errors=True)
            return False, dt, py, "; ".join(notes)
        finally:
            shutil.rmtree(env, ignore_errors=True)
    return False, dt, py, "; ".join(notes)


def param_kind(fe, name):
    for p in (fe or {}).get("parameters", []) or []:
        if p.get("name") == name:
            return p.get("kind")
    return None


def map_entry(e):
    kind = e.get("kind", "")
    rank = e.get("rank", -1)
    old, new = e.get("old") or {}, e.get("new") or {}
    data = e.get("data") or {}
    path = old.get("id") or new.get("id") or ""
    if kind in ("RemoveAlias", "RemoveExternalAlias", "ChangeExternalAlias", "ChangeAlias") and data.get("name"):
        path = f"{path}.{data['name']}"  # alias entries are reported on the containing module/class
    ct = KIND_MAP.get(kind, "other")
    param = ""
    if "Parameter" in kind or "Candidate" in kind or kind.startswith(("RemoveVar", "AddVar")):
        param = data.get("old") or data.get("new") or data.get("name") or ""
    elif kind == "MoveParameter":
        param = data.get("name", "")
    if kind == "MoveParameter":
        param = data.get("name", "")
    # AexPy reports positional->keyword-only (and ->positional-only) as removal of the
    # positional parameter; refine using the new function's parameter list.
    if ct == "param_removed" and param:
        nk = param_kind(new, param)
        if nk == 3:
            ct = "became_keyword_only"
        elif nk == 0:
            ct = "became_positional_only"
    rname = RANKS.get(rank, str(rank))
    breaking = "yes" if rank >= 100 else "potential"
    return dict(path=path, change_type=ct, param=param, tool_label=f"{kind}|{rname}", breaking=breaking,
                raw=" ".join(str(e.get("message", "")).split())[:300])


def main():
    work_root = Path(sys.argv[1])
    aexpy_version = sys.argv[2]
    only = set(sys.argv[3:])
    tool_env = work_root / "aexpy_tool"
    if not (tool_env / "bin" / "aexpy").exists():
        make_env(tool_env, "3.12", aexpy_version)
    pairs = list(csv.DictReader(open(OUT / "pairs.csv")))
    for p in pairs:
        if only and p["id"] not in only:
            continue
        work = work_root / f"aexpy_{p['id']}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        T0 = time.time()
        rows, notes, ok = [], [], False
        try:
            oa, ta, pya, na = extract(work, p["project"], p["from_version"], work / "old.json", aexpy_version)
            notes.append(f"extract_old_s={ta:.1f} py={pya}" + (f" [{na}]" if na else ""))
            if oa:
                ob, tb, pyb, nb = extract(work, p["project"], p["to_version"], work / "new.json", aexpy_version)
                notes.append(f"extract_new_s={tb:.1f} py={pyb}" + (f" [{nb}]" if nb else ""))
            if oa and ob:
                t0 = time.time()
                r = sh([str(tool_env / "bin" / "aexpy"), "diff", str(work / "old.json"), str(work / "new.json"), str(work / "diff.json")], timeout=1200)
                notes.append(f"diff_s={time.time()-t0:.1f}")
                if r.returncode == 0:
                    d = json.load(open(work / "diff.json"))
                    ents = list(d.get("entries", {}).values())
                    counts = {}
                    for e in ents:
                        rn = RANKS.get(e.get("rank", -1), "?")
                        counts[rn] = counts.get(rn, 0) + 1
                        if e.get("rank", -1) >= 30:  # Low/Medium/High = AexPy "Breakings" section
                            rows.append(map_entry(e))
                    notes.append("all_entries_by_rank=" + ",".join(f"{k}:{v}" for k, v in sorted(counts.items())))
                    keep = OUT / "aexpy_raw"
                    keep.mkdir(exist_ok=True)
                    # keep a compact copy of the diff (messages only) for auditing
                    with open(keep / f"{p['id']}_{p['project']}_diff.jsonl", "w") as f:
                        for e in ents:
                            f.write(json.dumps({"kind": e.get("kind"), "rank": e.get("rank"), "message": e.get("message"),
                                                "old": (e.get("old") or {}).get("id"), "new": (e.get("new") or {}).get("id"),
                                                "data": e.get("data")}) + "\n")
                    ok = True
                else:
                    keep = OUT / "aexpy_raw"
                    keep.mkdir(exist_ok=True)
                    (keep / f"{p['id']}_{p['project']}_diff_error.txt").write_text(r.stdout[-20000:] + "\n---stderr---\n" + r.stderr[-20000:])
                    notes.append(f"diff failed rc={r.returncode}: " + " ".join((r.stdout[-300:] + r.stderr[-300:]).split()))
        except Exception as e:
            notes.append(f"{type(e).__name__}: {e}"[:400])
        secs = time.time() - T0
        base = {k: p[k] for k in ("id", "project", "from_version", "to_version")}
        append(OUT / "aexpy_changes.csv", CH_FIELDS, [{**base, **r} for r in rows])
        nb_ = sum(r["breaking"] == "yes" for r in rows)
        append(OUT / "runs.csv", RUN_FIELDS, [dict(tool="aexpy", tool_version=aexpy_version, id=p["id"], project=p["project"],
                                                  status="ok" if ok else "failed", seconds=f"{secs:.1f}",
                                                  notes=f"changes={len(rows)} (breaking={nb_}, potential={len(rows)-nb_}); " + "; ".join(notes))])
        print(p["id"], p["project"], "ok" if ok else "FAILED", len(rows), f"{secs:.1f}s", flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
