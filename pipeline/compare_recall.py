"""Recall of the released data against an independent runtime ground truth.
Usage: python compare_recall.py DATA_DIR validation/recall OUT_JSON"""
import json, math, sys
import duckdb, pandas as pd

D, V, OUT = sys.argv[1:4]
gt = pd.read_csv(f"{V}/ground_truth.csv")
st = pd.read_csv(f"{V}/pairs_status.csv")
pairs = pd.read_csv(f"{V}/pairs.csv")
done = st[st.status.isin(["done", "partial"])].id
gt = gt[gt.id.isin(done)].copy()
gt["param"] = gt.param.fillna("").astype(str)
con = duckdb.connect()
con.register("pairs", pairs[pairs.id.isin(done)])
ours = con.execute(f"""select c.project, c.to_version, c.path, c.change_type, coalesce(c.param, '') param, c.breaking,
       c.confidence, c.representative
  from '{D}/changes.parquet' c join pairs p on c.project = p.project and c.to_version = p.to_version""").df()
SIG = {"param_removed", "param_renamed", "required_param_added", "became_keyword_only", "became_positional_only",
       "default_removed", "vararg_removed", "varkw_removed", "positional_reordered"}


def match(r):
    o = ours[(ours.project == r.project) & (ours.to_version == r.to_version) & (ours.path == r.path)]
    if o.empty:
        return "not_found", None
    b = o[o.breaking]
    exact = b[(b.change_type == r.change_type) & ((r.param == "") | (b.param == r.param))]
    if len(exact):
        return "same_change", ",".join(sorted(set(exact.confidence)))
    if len(b):
        return "other_breaking_change", ",".join(sorted(set(b.confidence)))
    return "recorded_non_breaking:" + ",".join(sorted(set(o.change_type))), None


m = gt.apply(lambda r: pd.Series(match(r), index=["match", "conf"]), axis=1)
gt = pd.concat([gt, m], axis=1)
gt.to_csv(f"{V}/recall_matched.csv", index=False)


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


det = gt.match.isin(["same_change", "other_breaking_change"])
hi = gt.conf.fillna("").str.contains("high")
res = {"pairs_evaluated": int(len(done)), "ground_truth_breaking": int(len(gt)),
       "recall_any_breaking": round(float(det.mean()), 4) if len(gt) else None, "ci_any": wilson(int(det.sum()), len(gt)),
       "recall_exact_change": round(float((gt.match == "same_change").mean()), 4) if len(gt) else None,
       "recall_high_confidence": round(float((det & hi).mean()), 4) if len(gt) else None,
       "ci_high": wilson(int((det & hi).sum()), len(gt)),
       "by_type": {k: {"n": int(len(g)), "detected": int(g.match.isin(["same_change", "other_breaking_change"]).sum())}
                   for k, g in gt.assign(t=gt.change_type.where(gt.change_type == "removed", "signature")).groupby("t")},
       "miss_reasons": gt[~det].match.str.split(":").str[0].value_counts().to_dict(),
       "per_pair": gt.groupby("project").apply(lambda g: f"{int(g.match.isin(['same_change','other_breaking_change']).sum())}/{len(g)}").to_dict()}
# views: (1) all rows; (2) without trio, whose 72 rows are *args/**kwargs pass-through wrappers generated at
# runtime in the old release; (3) rows inside this dataset's public-API definition: drop alias paths created by
# incidental imports in non-package modules and *args/**kwargs pass-through rows
ev = gt.evidence.fillna("")
incidental = ev.str.contains("alias path removed")
passthru = ev.str.contains("pass-through")
for name, mask in {"without_trio": gt.project != "trio",
                   "within_definition": ~incidental & ~passthru & (gt.project != "trio")}.items():
    g = gt[mask]; dg = g.match.isin(["same_change", "other_breaking_change"])
    res[f"recall_{name}"] = {"n": int(len(g)), "detected": int(dg.sum()),
                             "recall": round(float(dg.mean()), 4) if len(g) else None, "ci": wilson(int(dg.sum()), len(g))}
# what we claim on these pairs that the runtime diff does not list (descriptive; definitions differ)
claims = ours[ours.breaking & ours.representative & (ours.confidence == "high")]
key = set(zip(gt.project, gt.to_version, gt.path))
res["our_high_claims_on_pairs"] = int(len(claims))
res["our_high_claims_also_in_ground_truth"] = int(sum((p, v, x) in key for p, v, x in zip(claims.project, claims.to_version, claims.path)))
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1))
