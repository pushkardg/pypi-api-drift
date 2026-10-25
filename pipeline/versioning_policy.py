"""Classify each project's stated versioning policy from its PyPI project description (the README shown on PyPI).

  semver        a sentence in the description says the project follows / uses / adheres to Semantic Versioning
  not_semver    such a sentence is negated ("does not strictly follow semantic versioning")
  calver        most analysed releases look like calendar versions (YYYY.x, YY.MM)
  unknown       nothing stated in the description
Only the PyPI description is read, so projects that document their policy elsewhere count as unknown.
Usage: python versioning_policy.py RELEASES_PARQUET OUT_CSV"""
import asyncio, re, sys
import aiohttp, pandas as pd

SV = re.compile(r"(?<![\w=&])(?:semver|semantic[\s\-_]+versioning)(?![\w=&])", re.I)
VERB = re.compile(r"\b(?:follow|use|adhere|adopt|conform|abide|respect|stick|comply|obey|versioned)\w*\b", re.I)
NEG = re.compile(r"\b(?:not|never|no\s+longer)\b|n't\b", re.I)


def classify_text(d):
    """-> (policy, evidence) from the first sentence that states the project's own policy."""
    for m in SV.finditer(d):
        before = d[max(0, m.start() - 70): m.start()]
        before = re.split(r"[.!?]\s|\n\s*\n", before)[-1]  # same sentence
        if VERB.search(before):
            ctx = re.sub(r"\s+", " ", d[max(0, m.start() - 80): m.end() + 60])
            return ("not_semver" if NEG.search(before) else "semver"), ctx
    return None, ""


async def fetch(s, sem, p):
    async with sem:
        for _ in range(3):
            try:
                async with s.get(f"https://pypi.org/pypi/{p}/json") as r:
                    if r.status == 200:
                        j = await r.json()
                        return p, (j["info"].get("description") or "")
                    if r.status == 404:
                        return p, None
            except Exception:
                await asyncio.sleep(2)
        return p, None


async def main(rel_path, out):
    rel = pd.read_parquet(rel_path, columns=["project", "version", "likely_calver", "status"])
    projects = sorted(rel.project.unique())
    sem = asyncio.Semaphore(16)
    async with aiohttp.ClientSession(headers={"User-Agent": "pypi-api-drift/0.1 (research dataset)"}) as s:
        res = dict(await asyncio.gather(*(fetch(s, sem, p) for p in projects)))
    calver = rel[rel.status == "ok"].groupby("project").likely_calver.apply(lambda x: x.fillna(False).mean() > 0.5)
    rows = []
    for p in projects:
        d = res.get(p)
        pol, ctx = classify_text(d or "")
        if pol is None or (pol == "semver" and calver.get(p, False)):
            pol = "calver" if calver.get(p, False) else "unknown"
        rows.append({"project": p, "policy": pol, "description_found": d is not None, "evidence": ctx})
    df = pd.DataFrame(rows)
    df.to_csv(out, index=False)
    print(df.policy.value_counts())


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:3]))
