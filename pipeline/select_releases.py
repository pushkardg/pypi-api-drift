"""Pick, for each of the top-N PyPI projects, its final releases since START and one wheel per release.
Usage: python select_releases.py TOP_JSON OUT_PARQUET [--top 2000] [--start 2021-01-01] [--cap 80]"""
import argparse, asyncio, json
import aiohttp, pandas as pd
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version, InvalidVersion

ACCEPT = "application/vnd.pypi.simple.v1+json"
UA = "pypi-api-drift/0.1 (research dataset)"


def wheel_rank(fn):
    """Lower is better: pure-Python, then CPython manylinux x86_64 (newest CPython), then anything."""
    try:
        _, _, _, tags = parse_wheel_filename(fn)
    except Exception:
        return None
    best = None
    for t in tags:
        if t.platform == "any":
            r = (0, 0)
        elif "manylinux" in t.platform and t.platform.endswith("x86_64") and t.interpreter.startswith("cp"):
            try:
                r = (1, -int(t.interpreter[2:]))
            except ValueError:
                r = (2, 0)
        elif t.interpreter.startswith("cp") or t.interpreter.startswith("py"):
            r = (2, 0)
        else:
            r = (3, 0)
        best = r if best is None or r < best else best
    return best


async def fetch(s, sem, name):
    async with sem:
        for _ in range(5):
            try:
                async with s.get(f"https://pypi.org/simple/{name}/", headers={"Accept": ACCEPT}) as r:
                    if r.status == 200:
                        return name, await r.json(content_type=None)
                    if r.status == 404:
                        return name, None
            except Exception:
                pass
            await asyncio.sleep(2)
    return name, None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("top"); ap.add_argument("out")
    ap.add_argument("--top-n", type=int, default=2000)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--cap", type=int, default=80)
    a = ap.parse_args()
    rows = json.load(open(a.top))["rows"][: a.top_n]
    rank = {canonicalize_name(r["project"]): (i + 1, r["download_count"]) for i, r in enumerate(rows)}
    sem = asyncio.Semaphore(32)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}) as s:
        res = await asyncio.gather(*(fetch(s, sem, n) for n in rank))
    out = []
    for name, d in res:
        if not d:
            continue
        by_ver = {}
        for f in d["files"]:
            fn = f["filename"]
            if not fn.endswith(".whl") or f.get("yanked") or (f.get("upload-time") or "") < a.start:
                continue
            try:
                _, v, _, _ = parse_wheel_filename(fn)
            except Exception:
                continue
            if v.is_prerelease or v.is_devrelease:
                continue
            wr = wheel_rank(fn)
            if wr is None:
                continue
            key = (wr, f.get("size") or 0)
            cur = by_ver.get(v)
            if cur is None or key < cur[0]:
                by_ver[v] = (key, f)
        # placeholder versions (0.0.0, 0, 0.0) uploaded after a real release would sort first and be diffed against
        # an unrelated build: drop them (see drop_placeholders.py)
        real_times = [by_ver[v][1].get("upload-time") or "" for v in by_ver if any(v.release)]
        for v in [v for v in by_ver if not any(v.release)]:
            if real_times and (by_ver[v][1].get("upload-time") or "") > min(real_times):
                del by_ver[v]
        vers = sorted(by_ver)[-a.cap:]
        for i, v in enumerate(vers):
            f = by_ver[v][1]
            out.append({"project": name, "popularity_rank": rank[name][0], "downloads_30d": rank[name][1],
                        "version": str(v), "version_index": i, "n_selected_versions": len(vers),
                        "wheel_filename": f["filename"], "wheel_url": f["url"], "wheel_size": f.get("size"),
                        "wheel_sha256": f["hashes"].get("sha256"), "upload_time": f.get("upload-time"),
                        "pure_python_wheel": by_ver[v][0][0][0] == 0})
    df = pd.DataFrame(out)
    df.to_parquet(a.out, index=False)
    print(len(df), "releases", df.project.nunique(), "projects; pure-python share",
          round(df.pure_python_wheel.mean(), 3), "capped projects", int((df.n_selected_versions == a.cap).sum() and
          df[df.n_selected_versions == a.cap].project.nunique()))


asyncio.run(main())
