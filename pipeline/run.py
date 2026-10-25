"""Extract public-API history for every selected project.

Usage: python run.py SELECTED_PARQUET OUT_DIR [--projects a,b] [--concurrency 10]
Per project writes OUT_DIR/proj/<project>/{intervals,events,releases,sigs}.parquet (resumable).
"""
import argparse, asyncio, json, os, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor
import aiohttp, pandas as pd
from packaging.version import Version

sys.path.insert(0, os.path.dirname(__file__))
import remote_zip as rz
from modparse import parse_module, merge_stub
from api import Snapshot, diff, module_of, BREAKING

UA = "pypi-api-drift/0.1 (research dataset; range reads of .py/.pyi members only)"
MAX_MODULES = 8000


def parse_batch(items):
    return [(name, parse_module(data, is_stub)) for name, data, is_stub in items]


def bump(a, b):
    ra, rb = list(Version(a).release) + [0, 0, 0], list(Version(b).release) + [0, 0, 0]
    if rb[0] != ra[0]:
        return "major"
    if rb[1] != ra[1]:
        return "minor"
    if rb[2] != ra[2]:
        return "patch"
    return "other"


def likely_calver(v):
    r = Version(v).release
    return r[0] >= 2000 or (17 <= r[0] <= 40 and len(r) > 1 and 1 <= r[1] <= 12 and r[0] >= 17 and len(str(r[0])) == 2)


async def process_project(session, pool, sem, proj, rows, outdir):
    loop = asyncio.get_running_loop()
    cache = {}          # member name -> (crc, parsed)
    sigs = {}
    prev_syms, prev_ver, prev_idx = None, None, None  # last analysed release (skipped ones are not covered)
    open_iv = {}        # path -> [kind, sig, dep, first_ver, first_idx]
    intervals, events, rels = [], [], []
    depr_seen = {}      # path -> first version a deprecation marker was seen
    hidden = {}         # path -> reason, for symbols carried while no longer public
    d = f"{outdir}/proj/{proj}"
    os.makedirs(d + ".tmp", exist_ok=True)
    nparts = [0]

    def flush(final=False):
        nonlocal events, intervals
        if events and (final or len(events) > 150_000):
            pd.DataFrame(events).to_parquet(f"{d}.tmp/events_{nparts[0]:03d}.parquet", index=False)
            events = []; nparts[0] += 1
        if intervals and (final or len(intervals) > 300_000):
            iv = pd.DataFrame(intervals, columns=["path", "kind", "sig_hash", "deprecated", "first_version",
                                                  "first_index", "last_version", "last_index"])
            iv.insert(0, "project", proj)
            iv.to_parquet(f"{d}.tmp/intervals_{nparts[0]:03d}.parquet", index=False)
            intervals = []; nparts[0] += 1
    for i, r in enumerate(rows):
        rec = {k: r[k] for k in ("project", "version", "version_index", "upload_time", "wheel_filename",
                                 "wheel_size", "pure_python_wheel", "popularity_rank")}
        try:
            async with sem:
                cd = await rz.central_directory(session, r["wheel_url"], int(r["wheel_size"]))
            mods, ext, is_pkg = {}, set(), {}
            for e in cd:
                mk = module_of(e["name"])
                if not mk:
                    continue
                dotted, kind, pkg = mk
                if kind == "ext":
                    ext.add(dotted); continue
                cur = mods.get(dotted)
                if cur is None or (cur["kind"] == "pyi" and kind == "py"):
                    mods[dotted] = {"kind": kind, "entry": e, "alt": cur}
                elif kind == "pyi":
                    cur["alt"] = {"kind": kind, "entry": e, "alt": None}
                is_pkg[dotted] = is_pkg.get(dotted, False) or pkg
            if len(mods) > MAX_MODULES:
                rec["status"] = "skipped_too_many_modules"; rec["n_modules"] = len(mods)
                rels.append(rec); continue
            need = [m["entry"] for m in mods.values()
                    if cache.get(m["entry"]["name"], (None,))[0] != (m["entry"]["crc"], m["entry"]["usize"])]
            data = {}
            if need:
                async with sem:
                    data = await rz.read_members(session, r["wheel_url"], need)
            crcmap = {e["name"]: (e["crc"], e["usize"]) for e in cd}
            todo = [(n, b, n.endswith(".pyi")) for n, b in data.items()]
            for k in range(0, len(todo), 200):
                for n, parsed in await loop.run_in_executor(pool, parse_batch, todo[k:k + 200]):
                    cache[n] = (crcmap[n], parsed)
            parsed_mods, failed = {}, set()
            alt_need = [m["alt"]["entry"] for m in mods.values() if m["alt"] is not None
                        and cache.get(m["alt"]["entry"]["name"], (None,))[0] != crcmap[m["alt"]["entry"]["name"]]]
            if alt_need:
                async with sem:
                    extra = await rz.read_members(session, r["wheel_url"], alt_need)
                for n, parsed in await loop.run_in_executor(pool, parse_batch, [(n, b, True) for n, b in extra.items()]):
                    cache[n] = (crcmap[n], parsed)
            for dotted, m in mods.items():
                p = cache[m["entry"]["name"]][1]
                if m["alt"] is not None:  # .py + .pyi pair
                    p = merge_stub(p, cache[m["alt"]["entry"]["name"]][1])
                if p["error"]:
                    failed.add(dotted)
                else:
                    parsed_mods[dotted] = p
            opaque_mods = failed | (ext - set(parsed_mods))
            snap = Snapshot(parsed_mods, is_pkg, ext - set(parsed_mods), opaque=failed)
            syms = snap.symbols(sigs)
            # Symbols that left the public API but are still reachable at runtime (dropped from __all__,
            # now a re-export of an external object, lazy __getattr__, compiled/unparseable module, open class)
            # keep their previous state; one non-breaking event records the change of status.
            hidden_now = []
            if prev_syms is not None:
                for path, st in prev_syms.items():
                    if path in syms:
                        hidden.pop(path, None)
                        continue
                    acc = snap.accessible(path)
                    # a class-level __getattr__ (e.g. DataFrame column access) does not stand in for a removed
                    # method/property the class used to define
                    if acc is None and snap.last_reason == "class_getattr" and \
                            st[0] in ("method", "classmethod", "staticmethod", "property"):
                        acc = False
                    if acc is not False:
                        syms[path] = st
                        if path not in hidden:
                            hidden[path] = "accessible" if acc else "undecidable"
                            hidden_now.append(path)
            n_sym = sum(1 for v in syms.values() if v[0] != "module")
            rec.update({"status": "ok", "n_modules": len(mods), "n_modules_parse_failed": len(failed),
                        "n_extension_modules": len(ext), "n_public_symbols": n_sym,
                        "n_members_fetched": len(need)})
            for path, st in syms.items():
                if st[2] and path not in depr_seen:
                    depr_seen[path] = r["version"]
            if prev_syms is not None:
                ev = diff(prev_syms, syms, sigs)
                for path in hidden_now:
                    st = prev_syms[path]
                    ev.append({"path": path, "symbol_kind": st[0], "change_type": "left_public_api",
                               "param": "", "breaking": False, "parent_removed": False,
                               "detail": "still reachable at runtime" if hidden[path] == "accessible"
                               else "no longer visible statically (lazy/compiled/external); may still work",
                               "old_sig": st[1], "new_sig": st[1], "confidence": "high"})
                b = bump(prev_ver, r["version"])
                for e in ev:
                    if "unresolved" in (e["symbol_kind"],) or (e["change_type"] == "removed" and prev_syms[e["path"]][0] == "unresolved"):
                        e["breaking"] = False
                    e.update({"project": proj, "from_version": prev_ver, "to_version": r["version"],
                              "to_version_index": i, "bump": b,
                              "deprecated_before": e["path"] in depr_seen and e["change_type"] in BREAKING
                              and depr_seen[e["path"]] != r["version"]})
                events.extend(ev)
                nb = [e for e in ev if e["breaking"]]
                rec.update({"prev_version": prev_ver, "bump": b, "likely_calver": likely_calver(r["version"]),
                            "n_added": sum(e["change_type"] == "added" for e in ev),
                            "n_removed": sum(e["change_type"] == "removed" for e in ev),
                            "n_breaking_events": len(nb),
                            "n_breaking_symbols": len({e["path"] for e in nb})})
            # interval bookkeeping
            for path, st in syms.items():
                key = (st[0], st[1], st[2])
                cur = open_iv.get(path)
                if cur is None or cur[0] != key:
                    if cur is not None:
                        intervals.append((path, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
                    open_iv[path] = [key, r["version"], i]
            for path in list(open_iv):
                if path not in syms:
                    cur = open_iv.pop(path)
                    intervals.append((path, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
            prev_syms, prev_ver, prev_idx = syms, r["version"], i
            flush()
        except Exception as ex:
            rec["status"] = "error"; rec["error"] = f"{type(ex).__name__}: {str(ex)[:150]}"
        rels.append(rec)
    for path, cur in open_iv.items():
        intervals.append((path, *cur[0], cur[1], cur[2], prev_ver, prev_idx))
    flush(final=True)
    pd.DataFrame(rels).to_parquet(f"{d}.tmp/releases.parquet", index=False)
    pd.DataFrame({"sig_hash": list(sigs), "sig": [json.dumps(s, separators=(",", ":")) for s in sigs.values()]}) \
        .to_parquet(f"{d}.tmp/sigs.parquet", index=False)
    os.replace(d + ".tmp", d)
    return proj, len(rows), sum(1 for x in rels if x.get("status") == "ok"), len(events)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("selected"); ap.add_argument("out")
    ap.add_argument("--projects", default="")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--shard", default="0/1", help="i/n: only projects whose index mod n == i")
    ap.add_argument("--reverse", action="store_true", help="process the shard from its end (helper worker)")
    a = ap.parse_args()
    si, sn = map(int, a.shard.split("/"))
    sel = pd.read_parquet(a.selected).sort_values(["popularity_rank", "version_index"])
    if a.projects:
        sel = sel[sel.project.isin(a.projects.split(","))]
    groups = [(p, g.to_dict("records")) for p, g in sel.groupby("project", sort=False)]
    groups = [g for k, g in enumerate(groups) if k % sn == si]
    groups = [g for g in groups if not os.path.exists(f"{a.out}/proj/{g[0]}")]
    if a.reverse:
        groups = groups[::-1]
    print(f"{len(groups)} projects to do", flush=True)
    pool = ProcessPoolExecutor(1)
    sem = asyncio.Semaphore(48)
    psem = asyncio.Semaphore(a.concurrency)
    t0 = time.time(); done = [0]
    conn = aiohttp.TCPConnector(limit=64)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=300),
                                     headers={"User-Agent": UA}) as s:
        async def one(p, rows):
            async with psem:
                if os.path.exists(f"{a.out}/proj/{p}") or os.path.exists(f"{a.out}/proj/{p}.tmp"):
                    return  # finished or in progress in another worker
                try:
                    res = await process_project(s, pool, sem, p, rows, a.out)
                except Exception:
                    print(f"FAILED {p}\n{traceback.format_exc()[-800:]}", flush=True); return
                done[0] += 1
                print(f"[{done[0]}/{len(groups)}] {res[0]} versions={res[1]} ok={res[2]} events={res[3]} "
                      f"elapsed={(time.time() - t0) / 60:.1f}m", flush=True)
        await asyncio.gather(*(one(p, r) for p, r in groups))


if __name__ == "__main__":
    asyncio.run(main())
