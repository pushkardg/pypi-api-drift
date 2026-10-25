"""Score the VersionProbe-verified model baseline. Usage: python score_baseline.py BASELINE_DIR CUTOFF OUT_JSON
Item accuracy, pair accuracy (both halves right), McNemar test between conditions, by change type, and before /
after the model's training cutoff (by the release date of the breaking change)."""
import glob, json, math, sys
import pandas as pd

B, CUT, OUT = sys.argv[1:4]
items = pd.read_parquet(f"{B}/items_with_labels.parquet").merge(pd.read_csv(f"{B}/qid_map.csv"), on="item_id")


def load(cond):
    a = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{B}/answers_{cond}_batch*.csv"))])
    a["answer"] = a.answer.astype(str).str.strip().str.lower()
    return a.drop_duplicates("qid").set_index("qid").answer


def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


res = {"items": len(items), "pairs": int(items.pair_id.nunique()), "libraries": int(items.project.nunique()),
       "cutoff": CUT}
for cond in ("a", "b"):
    items[f"ans_{cond}"] = items.qid.map(load(cond))
    items[f"ok_{cond}"] = items[f"ans_{cond}"] == items.label
items["after_cutoff"] = pd.to_datetime(items.change_released) >= pd.Timestamp(CUT)
for cond, name in (("a", "closed_book"), ("b", "with_api_history")):
    ok = items[f"ok_{cond}"]
    pairs = items.groupby("pair_id")[f"ok_{cond}"].all()
    r = {"answered": int(items[f"ans_{cond}"].isin(["valid", "invalid"]).sum()),
         "item_acc": round(ok.mean(), 4), "item_ci": wilson(int(ok.sum()), len(ok)),
         "pair_acc": round(pairs.mean(), 4), "pair_ci": wilson(int(pairs.sum()), len(pairs)),
         "acc_on_valid": round(ok[items.label == "valid"].mean(), 4),
         "acc_on_invalid": round(ok[items.label == "invalid"].mean(), 4),
         "by_type": {t: round(g[f"ok_{cond}"].mean(), 4) for t, g in items.groupby("change_type")},
         "before_cutoff": {"n": int((~items.after_cutoff).sum()), "item_acc": round(ok[~items.after_cutoff].mean(), 4)},
         "after_cutoff": {"n": int(items.after_cutoff.sum()), "item_acc": round(ok[items.after_cutoff].mean(), 4)
                          if items.after_cutoff.any() else None},
         "by_year": {int(y): round(g[f"ok_{cond}"].mean(), 4)
                     for y, g in items.groupby(pd.to_datetime(items.change_released).dt.year)}}
    res[name] = r
b01 = int((~items.ok_a & items.ok_b).sum()); b10 = int((items.ok_a & ~items.ok_b).sum())
chi = (abs(b01 - b10) - 1) ** 2 / (b01 + b10) if b01 + b10 else 0.0
res["mcnemar"] = {"fixed_by_history": b01, "broken_by_history": b10, "chi2": round(chi, 2),
                  "p_lt_0.001": chi > 10.83}
items.drop(columns=["prompt_a", "prompt_b"]).to_csv(f"{B}/scored_items.csv", index=False)
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1))
