"""Compare PyPI API Drift with griffe and AexPy on the 30 release pairs of the recall study, scoring every method
with the same rules against the same runtime ground truth (validation/recall/ground_truth.csv).

Usage: python compare_tools.py DATA_DIR VALIDATION_DIR OUT_JSON

Detection rule (same for every method): a ground-truth breaking change is detected when the method makes a
breaking claim on the same path, or on a parent path that it reports as removed (e.g. the whole class), in the
same release pair. Exact = same change type (and parameter, for signature changes).
Agreement: the share of a method's breaking claims on public paths (no "_" segment) that the runtime ground truth
also lists, at the same path or a parent/child path. Claims outside the ground truth are not all false: the methods
define the public API differently (see the paper)."""
import json, math, sys
import duckdb, pandas as pd

D, V, OUT = sys.argv[1:4]
pairs = pd.read_csv(f"{V}/recall/pairs.csv")
st = pd.read_csv(f"{V}/recall/pairs_status.csv")
done = set(st[st.status.isin(["done", "partial"])].id)
gt = pd.read_csv(f"{V}/recall/ground_truth.csv")
gt = gt[gt.id.isin(done)].copy()
gt["param"] = gt.param.fillna("").astype(str)
ev = gt.evidence.fillna("")
gt["within_definition"] = ~ev.str.contains("alias path removed") & ~ev.str.contains("pass-through") & (gt.project != "trio")


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


# ---- claims of each method on the evaluated pairs
con = duckdb.connect()
con.register("pairs", pairs[pairs.id.isin(done)])
ours = con.execute(f"""select p.id, c.path, c.change_type, coalesce(c.param, '') param, c.confidence
  from '{D}/changes.parquet' c join pairs p on c.project = p.project and c.to_version = p.to_version
  where c.breaking""").df()
methods = {"PyPI API Drift (all confidence)": ours,
           "PyPI API Drift (high confidence)": ours[ours.confidence == "high"]}
runs = pd.read_csv(f"{V}/tool_comparison/runs.csv")
for tool, f in (("griffe", "griffe_changes.csv"), ("AexPy", "aexpy_changes.csv")):
    t = pd.read_csv(f"{V}/tool_comparison/{f}")
    t["param"] = t.param.fillna("").astype(str)
    ok = set(runs[(runs.tool.str.lower() == tool.lower()) & (runs.status == "ok")].id)
    t = t[t.id.isin(done & ok)]
    methods[f"{tool} (breaking)"] = t[t.breaking == "yes"]
    if (t.breaking == "potential").any():
        methods[f"{tool} (breaking + potential)"] = t[t.breaking.isin(["yes", "potential"])]
    methods[f"{tool} (breaking)"].attrs["pairs_ok"] = sorted(ok & done)


def parents(path):
    s = path.split(".")
    return {".".join(s[:k]) for k in range(1, len(s))}


res = {}
for name, cl in methods.items():
    tool_pairs = None
    for key in ("griffe", "AexPy"):
        if name.startswith(key):
            tool_pairs = set(runs[(runs.tool.str.lower() == key.lower()) & (runs.status == "ok")].id) & done
    g = gt if tool_pairs is None else gt[gt.id.isin(tool_pairs)]
    by_pair = {i: c for i, c in cl.groupby("id")}
    det, exact = [], []
    for r in g.itertuples():
        c = by_pair.get(r.id)
        if c is None:
            det.append(False); exact.append(False); continue
        same = c[c.path == r.path]
        removed_parent = c[(c.change_type == "removed") & c.path.isin(parents(r.path))]
        det.append(len(same) > 0 or len(removed_parent) > 0)
        exact.append(bool(((same.change_type == r.change_type) & ((r.param == "") | (same.param == r.param))).any())
                     or (r.change_type == "removed" and len(removed_parent) > 0))
    g = g.assign(det=det, exact=exact)
    pub = cl[~cl.path.str.split(".").apply(lambda s: any(x.startswith("_") for x in s))]
    gt_keys = {}
    for r in g.itertuples():
        gt_keys.setdefault(r.id, set()).add(r.path)
    agree = [any(p == r.path or p in parents(r.path) or r.path in parents(p) for p in gt_keys.get(r.id, ()))
             for r in pub.itertuples()]
    secs = None
    for key in ("griffe", "AexPy"):
        if name.startswith(key):
            rr = runs[(runs.tool.str.lower() == key.lower()) & (runs.status == "ok")]
            secs = {"median_seconds_per_pair": round(float(rr.seconds.median()), 1),
                    "total_seconds": round(float(rr.seconds.sum()), 1)}
    view = lambda m: {"n": int(m.sum()), "detected": int(g.det[m].sum()),
                      "recall": round(float(g.det[m].mean()), 4) if m.sum() else None,
                      "ci": wilson(int(g.det[m].sum()), int(m.sum()))}
    res[name] = {"pairs": len(done if tool_pairs is None else tool_pairs),
                 "recall_all": view(pd.Series(True, index=g.index)),
                 "recall_without_trio": view(g.project != "trio"),
                 "recall_within_api_drift_definition": view(g.within_definition),
                 "exact_change_recall_all": round(float(g.exact.mean()), 4) if len(g) else None,
                 "breaking_claims": int(len(cl)), "public_claims": int(len(pub)),
                 "public_claims_in_ground_truth": int(sum(agree)),
                 "agreement": round(sum(agree) / len(pub), 4) if len(pub) else None,
                 **({"time": secs} if secs else {})}
json.dump(res, open(OUT, "w"), indent=1)
for k, v in res.items():
    print(f"{k:38s} pairs {v['pairs']:2d} | recall all {v['recall_all']['detected']}/{v['recall_all']['n']} "
          f"no-trio {v['recall_without_trio']['detected']}/{v['recall_without_trio']['n']} "
          f"within-def {v['recall_within_api_drift_definition']['detected']}/{v['recall_within_api_drift_definition']['n']} "
          f"| claims {v['breaking_claims']} public {v['public_claims']} in GT {v['public_claims_in_ground_truth']}")
