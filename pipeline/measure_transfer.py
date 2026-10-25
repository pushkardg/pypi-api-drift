"""Measure the bytes the extractor actually transfers, against the full wheel sizes, by replaying the extraction's
fetch logic (central directory by range read, then only .py/.pyi members whose CRC/size changed since the previous
release of the same project) over the complete release history of a random sample of projects. Nothing is parsed.
Usage: python measure_transfer.py SELECTED_PARQUET RELEASES_PARQUET OUT_JSON [N_PROJECTS] [SEED]"""
import asyncio, contextvars, json, random, sys, time
import aiohttp, pandas as pd
import remote_zip as rz
from api import module_of

SEL, REL, OUT = sys.argv[1:4]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 60
SEED = int(sys.argv[5]) if len(sys.argv) > 5 else 20261021
UA = "pypi-api-drift/0.1 (research dataset; transfer measurement, range reads only)"
CUR = contextvars.ContextVar("cur")  # per-task byte counter (projects run concurrently)
_orig = rz.get_range


async def counted(session, url, start, end, tries=6):
    data = await _orig(session, url, start, end, tries)
    c = CUR.get()
    c["bytes"] += len(data); c["requests"] += 1
    return data
rz.get_range = counted


async def project(session, sem, rows):
    cache, out = {}, []
    got = {"bytes": 0, "requests": 0}
    CUR.set(got)
    for r in rows:
        b0, q0 = got["bytes"], got["requests"]
        async with sem:
            cd = await rz.central_directory(session, r.wheel_url, int(r.wheel_size))
            cd_bytes = got["bytes"] - b0
            mods = [e for e in cd if (mk := module_of(e["name"])) and mk[1] in ("py", "pyi")]
            need = [e for e in mods if cache.get(e["name"]) != (e["crc"], e["usize"])]
            if need:
                await rz.read_members(session, r.wheel_url, need)
        for e in mods:
            cache[e["name"]] = (e["crc"], e["usize"])
        all_py = sum(e["csize"] for e in mods)
        out.append({"project": r.project, "version": r.version, "wheel_size": int(r.wheel_size),
                    "fetched": got["bytes"] - b0, "central_directory": cd_bytes, "requests": got["requests"] - q0,
                    "py_members": len(mods), "py_members_fetched": len(need), "py_compressed_all": all_py})
    return out


async def main():
    rel = pd.read_parquet(REL, columns=["project", "version", "version_index", "status"])
    rel = rel[rel.status == "ok"]
    sel = pd.read_parquet(SEL, columns=["project", "version", "wheel_url", "wheel_size"])
    df = rel.merge(sel, on=["project", "version"]).sort_values(["project", "version_index"])
    projects = sorted(df.project.unique())
    random.Random(SEED).shuffle(projects)
    pick = projects[:N]
    sem = asyncio.Semaphore(8)
    t = time.time()
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=600)) as s:
        res = await asyncio.gather(*[project(s, sem, list(df[df.project == p].itertuples())) for p in pick],
                                   return_exceptions=True)
    rows = [x for r in res if not isinstance(r, Exception) for x in r]
    errors = [repr(r)[:200] for r in res if isinstance(r, Exception)]
    m = pd.DataFrame(rows)
    m.to_csv(OUT.replace(".json", "_releases.csv"), index=False)
    per_rel = (m.fetched / m.wheel_size)
    summary = {
        "sample": {"projects": int(m.project.nunique()), "releases": len(m), "seed": SEED, "errors": errors,
                   "share_of_analysed_releases": round(len(m) / len(df), 4)},
        "wheel_bytes": int(m.wheel_size.sum()), "fetched_bytes": int(m.fetched.sum()),
        "fetched_share": round(m.fetched.sum() / m.wheel_size.sum(), 5),
        "central_directory_bytes": int(m.central_directory.sum()),
        "requests_per_release_median": float(m.requests.median()),
        "fetched_per_release_median_kb": round(m.fetched.median() / 1024, 1),
        "fetched_share_per_release_median": round(float(per_rel.median()), 4),
        "py_members_fetched_share": round(m.py_members_fetched.sum() / max(1, m.py_members.sum()), 4),
        "without_crc_reuse_bytes_lower_bound": int(m.py_compressed_all.sum() + m.central_directory.sum()),
        "all_analysed_wheel_bytes": int(df.wheel_size.sum()),
        "extrapolated_fetched_bytes_all": int(float(df.wheel_size.sum()) * float(m.fetched.sum()) / float(m.wheel_size.sum())),
        "seconds": round(time.time() - t, 1),
        "note": "byte-weighted extrapolation from a random project sample; pip-installing every wheel would download "
                "all_analysed_wheel_bytes",
    }
    json.dump(summary, open(OUT, "w"), indent=1)
    print(json.dumps(summary, indent=1))


asyncio.run(main())
