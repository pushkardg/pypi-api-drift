"""Find the projects whose extraction changes under the container-shim rule for module __getattr__: for every
dynamic module in module_coverage, parse its source at the first and last release of each recorded range with the
previous (v1.2) and current extractor and compare the names __getattr__ serves, rejects and marks deprecated.
Usage: python scan_getattr_shims.py DATA_DIR SELECTED_PARQUET OLD_MODPARSE_DIR OUT_JSON"""
import asyncio, importlib.util, json, sys, zlib
import aiohttp, pandas as pd
import remote_zip as rz
import modparse as new
from api import module_of

D, SEL, OLD, OUT = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("modparse_old", f"{OLD}/modparse_v12.py")
old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
UA = "pypi-api-drift/0.1 (research dataset; range reads of .py/.pyi members only)"

mc = pd.read_parquet(f"{D}/module_coverage.parquet")
mc = mc[mc.dynamic == True]  # noqa: E712
rel = pd.read_parquet(f"{D}/releases.parquet", columns=["project", "version", "version_index", "status"])
sel = pd.read_parquet(SEL, columns=["project", "version", "wheel_url", "wheel_size"])
rel = rel.merge(sel, on=["project", "version"])
want = {}
for r in mc.itertuples():
    for idx in {int(r.first_index), int(r.last_index)}:
        want.setdefault((r.project, idx), set()).add(r.module)
jobs = []
for r in rel.itertuples():
    mods = want.get((r.project, int(r.version_index)))
    if mods:
        jobs.append((r.project, r.version, r.wheel_url, int(r.wheel_size), mods))
print(len(jobs), "releases to scan,", mc.project.nunique(), "projects", flush=True)
seen, changed, errors = {}, {}, []


def compare(name, data):
    try:
        a = old.parse_module(data, name.endswith(".pyi")); b = new.parse_module(data, name.endswith(".pyi"))
    except Exception as e:
        return None
    diff = []
    for k in ("getattr_names", "getattr_raises", "getattr_deprecated_names"):
        x, y = set(a.get(k, [])), set(b.get(k, []))
        diff += [f"{k}:+{n}" for n in sorted(y - x)] + [f"{k}:-{n}" for n in sorted(x - y)]
    return diff


async def one(s, sem, job):
    p, v, url, size, mods = job
    async with sem:
        try:
            cd = await rz.central_directory(s, url, size)
            ents = [e for e in cd if (mk := module_of(e["name"])) and mk[1] in ("py", "pyi") and mk[0] in mods]
            need = [e for e in ents if (e["crc"], e["usize"]) not in seen]
            data = await rz.read_members(s, url, need) if need else {}
        except Exception as e:
            errors.append(f"{p}=={v}: {e!r}"[:200]); return
    for e in ents:
        k = (e["crc"], e["usize"])
        if k not in seen:
            seen[k] = compare(e["name"], data[e["name"]]) if e["name"] in data else None
        extra = seen[k]
        if extra:
            changed.setdefault(p, {}).setdefault(e["name"], sorted(set(changed.get(p, {}).get(e["name"], [])) | set(extra)))


async def main():
    sem = asyncio.Semaphore(16)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=900)) as s:
        done = 0
        for k in range(0, len(jobs), 400):
            await asyncio.gather(*[one(s, sem, j) for j in jobs[k:k + 400]])
            done += len(jobs[k:k + 400])
            print(done, "scanned;", len(changed), "projects changed", flush=True)
    json.dump({"releases_scanned": len(jobs), "unique_files": len(seen), "errors": errors,
               "projects_changed": changed}, open(OUT, "w"), indent=1)
    print(json.dumps({p: {f: x[:8] for f, x in v.items()} for p, v in changed.items()}, indent=1)[:4000])


asyncio.run(main())
