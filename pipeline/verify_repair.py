"""Verify code-repair answers at runtime. For each task: a fresh environment with only project==version_after,
then repair_resolver.py judges the original snippet and every condition's answer (nothing is executed).
Usage: python verify_repair.py REPAIR_DIR DATA_DIR [WORKERS] [--strict]
--strict (post-hoc sensitivity check) judges every item twice, with bind_partial (as preregistered) and with full
binding, and writes verify_strict.jsonl; keys orig/a/b/c and strict_orig/strict_a/...."""
import glob, json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

R, D = sys.argv[1], sys.argv[2]
STRICT = "--strict" in sys.argv
argv = [a for a in sys.argv if a != "--strict"]
W = int(argv[3]) if len(argv) > 3 else 2
HERE = os.path.dirname(os.path.abspath(__file__))
tasks = pd.read_csv(f"{R}/tasks.csv")
tops = pd.read_parquet(f"{D}/top_level_modules.parquet")
tops = tops[~tops.module.str.contains(r"\.")].groupby("project").module.apply(set).to_dict()


def answers(cond):
    out = {}
    for f in sorted(glob.glob(f"{R}/answers_{cond}_batch*.md")):
        text = open(f).read()
        for m in re.finditer(r"^### (t\d+)\s*\n(.*?)(?=^### t\d+|\Z)", text, re.S | re.M):
            code = re.search(r"```(?:python)?\n(.*?)```", m.group(2), re.S)
            out[m.group(1)] = code.group(1) if code else m.group(2).strip()
    return out


A = {c: answers(c) for c in ("a", "b", "c")}
done = set()
OUT = f"{R}/verify_strict.jsonl" if STRICT else f"{R}/verify.jsonl"
if os.path.exists(OUT):
    done = {json.loads(l)["task_id"] for l in open(OUT)}


def run(t):
    if t.task_id in done:
        return None
    roots = {m.split(".")[0] for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", t.snippet, re.M)}
    top = sorted(tops.get(t.project, set()) | roots)
    env = tempfile.mkdtemp(prefix="rv_", dir="/tmp")
    res = {"task_id": t.task_id, "project": t.project, "version": t.version_after}
    try:
        ok = False
        for py in ("3.12", "3.11", "3.10"):
            subprocess.run(["uv", "venv", "-q", "--clear", env, "--python", py], capture_output=True)
            p = subprocess.run(["uv", "pip", "install", "-q", "--python", f"{env}/bin/python",
                                f"{t.project}=={t.version_after}"], capture_output=True, text=True, timeout=900)
            if p.returncode == 0:
                ok = True; res["python"] = py; break
        if not ok:
            res["env_error"] = "install failed: " + p.stderr[-300:]
            return res
        items = [{"key": "orig", "code": t.snippet, "tops": top}] + \
                [{"key": c, "code": A[c].get(t.task_id, ""), "tops": top} for c in ("a", "b", "c")]
        if STRICT:
            items += [dict(i, key="strict_" + i["key"], strict=True) for i in items]
        p = subprocess.run([f"{env}/bin/python", os.path.join(HERE, "repair_resolver.py")],
                           input="\n".join(json.dumps(i) for i in items) + "\n", capture_output=True, text=True,
                           timeout=600)
        for line in p.stdout.splitlines():
            r = json.loads(line)
            res[r["key"]] = r
        if p.returncode != 0 and not p.stdout:
            res["env_error"] = "resolver crashed: " + p.stderr[-300:]
        return res
    except Exception as e:
        res["env_error"] = f"{type(e).__name__}: {e}"[:300]
        return res
    finally:
        shutil.rmtree(env, ignore_errors=True)
        if shutil.disk_usage("/").free < 8e9:  # large wheels (torch, ...) fill the disk; clean after every task
            subprocess.run(["uv", "cache", "clean"], capture_output=True)


with ThreadPoolExecutor(W) as ex, open(OUT, "a") as out:
    from concurrent.futures import as_completed
    for i, r in enumerate(f.result() for f in as_completed([ex.submit(run, t) for t in tasks.itertuples()])):
        if r is None:
            continue
        out.write(json.dumps(r) + "\n"); out.flush()
        print(i, r["task_id"], r["project"], r.get("env_error", "")[:60] or
              {k: r.get(k, {}).get("verdict") for k in ("orig", "a", "b", "c")}, flush=True)
        if i % 20 == 0:  # keep the shared disk from filling with cached wheels
            du = shutil.disk_usage("/")
            if du.free < 6e9:
                subprocess.run(["uv", "cache", "clean"], capture_output=True)
