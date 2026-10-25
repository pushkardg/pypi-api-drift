"""Run griffe.find_breaking_changes on every pair in pairs.csv.

Usage: griffe-venv/bin/python -I run_griffe.py <workdir> [ids...]
Per pair: download+unpack both releases (fetch.py) into separate dirs, griffe.load each
top-level package with search_paths=[that dir] only, diff, append rows to griffe_changes.csv
and runs.csv. Each pair runs in a child process with a timeout.
"""
import csv, json, multiprocessing as mp, shutil, sys, time, traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
sys.path.insert(0, str(HERE))
import fetch  # noqa: E402

CH_FIELDS = ["id", "project", "from_version", "to_version", "path", "change_type", "param", "tool_label", "breaking", "raw"]
RUN_FIELDS = ["tool", "tool_version", "id", "project", "status", "seconds", "notes"]
TIMEOUT = 900


def append(path, fields, rows):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def map_breakage(b, griffe):
    K = griffe.BreakageKind
    k = b.kind
    param = ""
    ct = "other"
    if k == K.OBJECT_REMOVED:
        ct = "removed"
    elif k == K.PARAMETER_REMOVED:
        param = b.old_value.name
        kind = str(b.old_value.kind.value)
        ct = {"variadic positional": "vararg_removed", "variadic keyword": "varkw_removed"}.get(kind, "param_removed")
    elif k == K.PARAMETER_ADDED_REQUIRED:
        param = b.new_value.name
        ct = "required_param_added"
    elif k == K.PARAMETER_CHANGED_REQUIRED:
        param = b.old_value.name
        ct = "default_removed"
    elif k == K.PARAMETER_MOVED:
        param = b.old_value.name
        ct = "positional_reordered"
    elif k == K.PARAMETER_CHANGED_KIND:
        param = b.old_value.name
        nk = str(b.new_value.kind.value)
        ct = {"keyword-only": "became_keyword_only", "positional-only": "became_positional_only"}.get(nk, "other")
    elif k == K.PARAMETER_CHANGED_DEFAULT:
        param = b.old_value.name
    path = b.obj.path
    # griffe reports parameter breakages on the function object; path is the function
    def short(v):
        if v is None:
            return "None"
        if hasattr(v, "name") and hasattr(v, "kind") and hasattr(v, "default"):
            return f"{v.name}:{v.kind.value}" + (f"={v.default}" if v.default is not None else "")
        if hasattr(v, "path"):
            return f"{getattr(v, 'kind', '')}:{v.path}"
        return str(v)
    raw = f"{k.value}: {path}" + (f" [{param}]" if param else "") + f" old={short(b.old_value)!s:.80} new={short(b.new_value)!s:.80}"
    raw = " ".join(str(raw).split())[:300]
    return dict(path=path, change_type=ct, param=param, tool_label=k.name, breaking="yes", raw=raw)


def child(pair, work, q):
    import griffe
    try:
        t0 = time.time()
        a, fa, ta = fetch.fetch(pair["project"], pair["from_version"], work / "old")
        b, fb, tb = fetch.fetch(pair["project"], pair["to_version"], work / "new")
        t1 = time.time()
        pkgs = sorted(n for n in set(ta) & set(tb) if not n.startswith("_")) or sorted(set(ta) & set(tb))
        gone = sorted(set(ta) - set(tb))
        rows, errs = [], []
        # load every top-level (incl. private ones like _griffe) into one loader per release so
        # public re-exports (aliases) resolve, then diff only the public top-levels
        loaders = {}
        for side, root, names in (("old", a, ta), ("new", b, tb)):
            ld = griffe.GriffeLoader(search_paths=[str(root)], allow_inspection=False)
            for name in names:
                try:
                    ld.load(name)
                except Exception as e:
                    errs.append(f"{side}:{name}: {type(e).__name__}: {e}"[:200])
            try:
                ld.resolve_aliases(implicit=False, external=False)
            except Exception as e:
                errs.append(f"{side}: resolve_aliases {type(e).__name__}: {e}"[:200])
            loaders[side] = ld
        for name in pkgs:
            try:
                old = loaders["old"].modules_collection[name]
                new = loaders["new"].modules_collection[name]
                for br in griffe.find_breaking_changes(old, new):
                    rows.append(map_breakage(br, griffe))
            except Exception as e:  # per-package failure
                errs.append(f"{name}: {type(e).__name__}: {e}"[:200])
        t2 = time.time()
        q.put(dict(ok=not (errs and not rows and len(errs) == len(pkgs)), rows=rows,
                   notes=f"artifacts={fa} | {fb}; packages={','.join(pkgs)}; fetch_s={t1-t0:.1f}; analysis_s={t2-t1:.1f}"
                   + (f"; top-level only in old (not reported by griffe): {','.join(gone)}" if gone else "")
                   + (f"; errors: {' / '.join(errs)}" if errs else "")))
    except Exception as e:
        q.put(dict(ok=False, rows=[], notes=f"{type(e).__name__}: {e}"[:300] + " " + traceback.format_exc(limit=2).replace("\n", " ")[:300]))


def main():
    work_root = Path(sys.argv[1])
    only = set(sys.argv[2:])
    import importlib.metadata as md
    ver = md.version("griffe")
    pairs = list(csv.DictReader(open(OUT / "pairs.csv")))
    for p in pairs:
        if only and p["id"] not in only:
            continue
        work = work_root / f"griffe_{p['id']}"
        work.mkdir(parents=True, exist_ok=True)
        q = mp.Queue()
        t0 = time.time()
        proc = mp.Process(target=child, args=(p, work, q))
        proc.start()
        res = None
        try:
            res = q.get(timeout=TIMEOUT)
        except Exception:
            pass
        proc.join(10)
        if proc.is_alive():
            proc.kill()
        secs = time.time() - t0
        if res is None:
            res = dict(ok=False, rows=[], notes=f"timeout after {TIMEOUT}s")
        base = {k: p[k] for k in ("id", "project", "from_version", "to_version")}
        append(OUT / "griffe_changes.csv", CH_FIELDS, [{**base, **r} for r in res["rows"]])
        append(OUT / "runs.csv", RUN_FIELDS, [dict(tool="griffe", tool_version=ver, id=p["id"], project=p["project"],
                                                  status="ok" if res["ok"] else "failed", seconds=f"{secs:.1f}",
                                                  notes=f"changes={len(res['rows'])}; " + res["notes"])])
        print(p["id"], p["project"], "ok" if res["ok"] else "FAILED", len(res["rows"]), f"{secs:.1f}s", flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
