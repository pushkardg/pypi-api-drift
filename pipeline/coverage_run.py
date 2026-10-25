"""Per-module analysis coverage for every analysed release (companion to run.py).

For each release and each importable module in its wheel, record how much the static extraction can see:
  source            py | py+pyi | pyi (stub only) | compiled (extension module, no Python source) | unparseable
  dynamic           module-level __getattr__ that is not closed, names injected through globals()/sys.modules,
                    or an __all__ built at runtime: names may exist that the dataset does not list
  imports_compiled  the module binds names from a compiled extension module of the same wheel: their
                    signatures and members are not visible
  generated_init_classes  classes whose constructor is generated or replaced by a class decorator (dataclass,
                    attrs, factories), so the static constructor signature is not reliable
Rows are compressed into version intervals like api_history.

Usage: python coverage_run.py SELECTED_PARQUET OUT_DIR [--shard i/n] [--reverse]
Writes OUT_DIR/proj/<project>.parquet (resumable).
"""
import argparse, asyncio, os, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor
import aiohttp, pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import remote_zip as rz
from modparse import parse_module
from api import module_of

UA = "pypi-api-drift/0.1 (research dataset; range reads of .py/.pyi members only)"
MAX_MODULES = 8000


def summarize(items):
    """items: [(name, bytes, is_stub)] -> [(name, flags)] with only what coverage needs (keeps IPC small)."""
    out = []
    for name, data, is_stub in items:
        p = parse_module(data, is_stub)
        if p["error"]:
            out.append((name, {"error": True}))
            continue
        imps = [(m, lvl) for (m, lvl, _a) in p["imports"].values()] + list(p["stars"])
        dyn = bool((p["has_getattr"] and not p["getattr_closed"]) or p["dynamic_globals"] or p.get("all_dynamic"))
        gen = sorted(n for n, d in p["defs"].items() if d.get("kind") == "class" and d.get("init_generated"))
        out.append((name, {"error": False, "dynamic": dyn, "imports": imps, "generated_init": gen}))
    return out


def absmod(mod, cur, level, is_pkg):
    if level == 0:
        return mod
    base = cur if is_pkg else cur.rpartition(".")[0]
    for _ in range(level - 1):
        base = base.rpartition(".")[0]
    return f"{base}.{mod}" if mod else base


async def process_project(session, pool, sem, proj, rows, outdir):
    loop = asyncio.get_running_loop()
    cache = {}          # member -> ((crc, size), flags)
    open_iv, out = {}, []
    prev_ver, prev_idx = None, None  # last release actually read (skipped ones are not covered)
    for i, r in enumerate(rows):
        try:
            async with sem:
                cd = await rz.central_directory(session, r["wheel_url"], int(r["wheel_size"]))
            mods, ext, is_pkg = {}, set(), {}
            for e in cd:
                mk = module_of(e["name"])
                if not mk:
                    continue
                dotted, kind, pkg = mk
                is_pkg[dotted] = is_pkg.get(dotted, False) or pkg
                if kind == "ext":
                    ext.add(dotted); continue
                m = mods.setdefault(dotted, {})
                m[kind] = e
            if len(mods) > MAX_MODULES:
                continue
            crc = {e["name"]: (e["crc"], e["usize"]) for e in cd}
            # the runtime source decides dynamic behaviour; read the stub only for stub-only modules
            want = [m.get("py") or m["pyi"] for m in mods.values()]
            need = [e for e in want if cache.get(e["name"], (None,))[0] != crc[e["name"]]]
            if need:
                async with sem:
                    data = await rz.read_members(session, r["wheel_url"], need)
                todo = [(n, b, n.endswith(".pyi")) for n, b in data.items()]
                for k in range(0, len(todo), 200):
                    for n, fl in await loop.run_in_executor(pool, summarize, todo[k:k + 200]):
                        cache[n] = (crc[n], fl)
            state = {}
            for dotted, m in mods.items():
                e = m.get("py") or m["pyi"]
                fl = cache.get(e["name"], (None, {"error": True}))[1]
                if fl["error"]:
                    state[dotted] = ("unparseable", False, False, "")
                    continue
                src = "py+pyi" if ("py" in m and "pyi" in m) else ("py" if "py" in m else "pyi")
                ic = any(absmod(mm, dotted, lvl, is_pkg.get(dotted, False)) in ext for mm, lvl in fl["imports"])
                state[dotted] = (src, fl["dynamic"], ic, ",".join(fl.get("generated_init", [])))
            for dotted in ext - set(mods):
                state[dotted] = ("compiled", False, False, "")
            for dotted, st in state.items():
                cur = open_iv.get(dotted)
                if cur is None or cur[0] != st:
                    if cur is not None:
                        out.append((dotted, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
                    open_iv[dotted] = [st, r["version"], i]
            for dotted in list(open_iv):
                if dotted not in state:
                    cur = open_iv.pop(dotted)
                    out.append((dotted, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
            prev_ver, prev_idx = r["version"], i
        except Exception as ex:
            print(f"  {proj} {r['version']}: {type(ex).__name__} {str(ex)[:100]}", flush=True)
    for dotted, cur in open_iv.items():
        out.append((dotted, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
    df = pd.DataFrame(out, columns=["module", "source", "dynamic", "imports_compiled", "generated_init_classes",
                                    "first_version",
                                    "first_index", "last_version", "last_index"])
    df.insert(0, "project", proj)
    df.to_parquet(f"{outdir}/proj/{proj}.parquet.tmp", index=False)
    os.replace(f"{outdir}/proj/{proj}.parquet.tmp", f"{outdir}/proj/{proj}.parquet")
    return proj, len(rows), len(df)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("selected"); ap.add_argument("out")
    ap.add_argument("--projects", default="")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--reverse", action="store_true")
    a = ap.parse_args()
    os.makedirs(f"{a.out}/proj", exist_ok=True)
    si, sn = map(int, a.shard.split("/"))
    sel = pd.read_parquet(a.selected).sort_values(["popularity_rank", "version_index"])
    if a.projects:
        sel = sel[sel.project.isin(a.projects.split(","))]
    groups = [(p, g.to_dict("records")) for p, g in sel.groupby("project", sort=False)]
    groups = [g for k, g in enumerate(groups) if k % sn == si]
    if a.reverse:
        groups = groups[::-1]
    print(f"{len(groups)} projects", flush=True)
    pool = ProcessPoolExecutor(1)
    sem = asyncio.Semaphore(32)
    psem = asyncio.Semaphore(a.concurrency)
    t0 = time.time(); done = [0]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=48),
                                     timeout=aiohttp.ClientTimeout(total=300), headers={"User-Agent": UA}) as s:
        async def one(p, rows):
            async with psem:
                f = f"{a.out}/proj/{p}.parquet"
                if os.path.exists(f) or os.path.exists(f + ".lock"):
                    return
                open(f + ".lock", "w").close()
                try:
                    res = await process_project(s, pool, sem, p, rows, a.out)
                except Exception:
                    print(f"FAILED {p}\n{traceback.format_exc()[-600:]}", flush=True); return
                finally:
                    os.remove(f + ".lock")
                done[0] += 1
                print(f"[{done[0]}/{len(groups)}] {res[0]} versions={res[1]} rows={res[2]} "
                      f"elapsed={(time.time() - t0) / 60:.1f}m", flush=True)
        await asyncio.gather(*(one(p, r) for p, r in groups))


if __name__ == "__main__":
    asyncio.run(main())
