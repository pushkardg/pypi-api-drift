"""Build the VersionProbe benchmark: paired valid/invalid API-usage snippets from real breaking changes.

Each breaking change (A -> B) yields two items with the same snippet:
  * pinned to version A -> label "valid"
  * pinned to version B -> label "invalid"
Every item is verified with apidrift_check before it is kept.
Usage: python make_probes.py DATA_DIR OUT_DIR [--max-rank 1000]
"""
import argparse, json, os, random, sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from apidrift.check import Store, check_source  # single canonical implementation (the packaged CLI)

KINDS_TOP = {"function", "class", "class_open", "attribute", "dynamic_attribute"}
KINDS_MEMBER = {"method", "classmethod", "staticmethod", "property"}  # data attributes may be instance-only


def placeholder_call(name, sig, drop=None, add=None):
    """Call `name(...)` satisfying every required parameter with `...`, plus `add=...` keyword."""
    args, kws = [], []
    for p in sig["params"]:
        n, kind, has_def = p[0], p[1], p[2]
        if n == drop or kind in ("VA", "VK") or has_def:
            continue
        (args if kind == "PO" else kws).append(n)
    parts = ["..."] * len(args) + [f"{k}=..." for k in kws]
    if add:
        parts.append(f"{add}=...")
    return f"{name}({', '.join(parts)})"


def build(ev, kinds, store):
    path, ct = ev.path, ev.change_type
    mod, _, leaf = path.rpartition(".")
    if ct == "removed":
        pkind = kinds.get((ev.project, mod))
        if ev.symbol_kind in KINDS_TOP and pkind in ("module", None):
            return f"from {mod} import {leaf}"
        if ev.symbol_kind in KINDS_MEMBER and pkind in ("class", "class_open"):
            cmod, _, cls = mod.rpartition(".")
            return f"from {cmod} import {cls}\n{cls}.{leaf}"
        return None
    if ct in ("param_removed", "param_renamed") and ev.symbol_kind in ("function", "class", "class_open"):
        sig = store.sig(ev.old_sig)
        if not sig:
            return None
        return f"from {mod} import {leaf}\n{placeholder_call(leaf, sig, drop=ev.param, add=ev.param)}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("out")
    ap.add_argument("--max-rank", type=int, default=1000)
    ap.add_argument("--per-release", type=int, default=6)
    ap.add_argument("--shard", default="0/1", help="i/n: only projects whose sorted index mod n == i")
    a = ap.parse_args()
    si, sn = map(int, a.shard.split("/"))
    os.environ["APIDRIFT_DATA"] = a.data
    store = Store(a.data)
    rel = pd.read_parquet(f"{a.data}/releases.parquet", columns=["project", "version", "popularity_rank", "upload_time"])
    rank = rel.groupby("project").popularity_rank.first()
    up = dict(zip(zip(rel.project, rel.version), rel.upload_time))
    ch = pd.read_parquet(f"{a.data}/changes.parquet",
                         filters=[("breaking", "=", True), ("parent_removed", "=", False), ("representative", "=", True), ("confidence", "=", "high"),
                                  ("change_type", "in", ["removed", "param_removed", "param_renamed"])])
    ch = ch[ch.project.map(rank) <= a.max_rank]
    projs = sorted(ch.project.unique())
    ch = ch[ch.project.isin([p for k, p in enumerate(projs) if k % sn == si])]
    ch = ch[~ch.path.str.split(".").apply(lambda s: any(x.startswith("_") for x in s))]
    ch = ch[~((ch.change_type == "param_removed") & ch.detail.fillna("").str.startswith("absorbed"))]
    import duckdb
    con = duckdb.connect()
    con.register("need", pd.DataFrame({"project": ch.project, "path": ch.path.str.rpartition(".")[0]}).drop_duplicates())
    kv = con.execute(f"select h.project, h.path, any_value(h.kind) from '{a.data}/api_history.parquet' h "
                     "join need using (project, path) group by 1, 2").fetchall()
    kinds = {(p, q): k for p, q, k in kv}
    ch = ch.sample(frac=1, random_state=7).groupby(["project", "to_version"]).head(a.per_release * 3)
    ch = ch.sort_values(["project", "to_version_index"], kind="stable")  # keeps the checker's caches warm
    items, kept_per = [], {}
    last = None
    print(len(ch), "candidate events", flush=True)
    for n_ev, ev in enumerate(ch.itertuples()):
        if ev.project != last:  # rows are sorted by project: free the previous project's API snapshots
            Store.api_at.cache_clear(); Store.intervals.cache_clear(); last = ev.project
        if n_ev % 5000 == 0:
            print(n_ev, len(items) // 2, "pairs", flush=True)
        key = (ev.project, ev.to_version)
        if kept_per.get(key, 0) >= a.per_release:
            continue
        code = build(ev, kinds, store)
        if not code:
            continue
        ok_before = [i for i in check_source(code, {ev.project: ev.from_version}, store) if i["kind"] != "deprecated"]
        bad_after = [i for i in check_source(code, {ev.project: ev.to_version}, store) if i["kind"] != "deprecated"]
        if ok_before or not bad_after:
            continue
        kept_per[key] = kept_per.get(key, 0) + 1
        pid = f"{ev.project}:{ev.to_version}:{ev.path}:{ev.change_type}:{ev.param}"
        base = {"pair_id": pid, "project": ev.project, "popularity_rank": int(rank[ev.project]),
                "snippet": code, "symbol": ev.path, "change_type": ev.change_type, "param": ev.param or None,
                "change_detail": ev.detail or None, "version_before": ev.from_version, "version_after": ev.to_version,
                "change_released": str(up.get((ev.project, ev.to_version))),
                "checker_message": bad_after[0]["message"]}
        items.append(dict(base, item_id=pid + ":before", version=ev.from_version, label="valid"))
        items.append(dict(base, item_id=pid + ":after", version=ev.to_version, label="invalid"))
    df = pd.DataFrame(items)
    df["prompt"] = df.apply(lambda r: (
        f"You are given a Python snippet and an installed library version.\n"
        f"Library: {r.project}=={r.version}\n```python\n{r.snippet}\n```\n"
        "Ignoring the placeholder `...` argument values, is every library name, attribute and keyword argument "
        "used here valid in exactly this version? Answer with one word: valid or invalid."), axis=1)
    os.makedirs(a.out, exist_ok=True)
    if sn > 1:
        df.to_parquet(f"{a.out}/part_{si:02d}.parquet", index=False)
        print(len(df), "items in shard", si); return
    df.to_parquet(f"{a.out}/versionprobe.parquet", index=False)
    with open(f"{a.out}/versionprobe.jsonl", "w") as f:
        for r in df.to_dict("records"):
            f.write(json.dumps(r, default=str) + "\n")
    print(len(df), "items", df.pair_id.nunique(), "pairs", df.project.nunique(), "projects")
    print(df.change_type.value_counts().to_dict())


if __name__ == "__main__":
    main()
