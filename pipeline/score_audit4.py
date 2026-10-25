"""Score the fresh v1.2 precision audit, overall and by stratum (claims new in v1.2 vs unchanged).
Usage: python score_audit4.py SAMPLE_WITH_STRATA RESULTS_DIR OUT_JSON"""
import glob, json, math, sys
import pandas as pd
S, R, OUT = sys.argv[1:4]
s = pd.read_csv(S)
r = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{R}/results_part*.csv"))]).drop_duplicates("id", keep="last")
m = s.merge(r[["id", "verdict", "method"]], on="id", how="left")
def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]
def summ(g):
    d = g[g.verdict.isin(["confirmed", "false_positive", "partially"])]
    k = int((d.verdict == "confirmed").sum())
    return {"n": len(g), "decided": len(d), "confirmed": k, "false_positive": int((d.verdict == "false_positive").sum()),
            "partially": int((d.verdict == "partially").sum()), "precision": round(k / len(d), 4) if len(d) else None,
            "ci": wilson(k, len(d)) if len(d) else None, "runtime": int((g.method == "runtime").sum())}
res = {"all": summ(m), "by_stratum": {k: summ(g) for k, g in m.groupby("stratum")},
       "by_type": {k: summ(g) for k, g in m.assign(t=m.change_type.where(m.change_type == "removed", "signature")).groupby("t")},
       "libraries": int(m.project.nunique())}
m.to_csv(f"{R}/results_with_strata.csv", index=False)
json.dump(res, open(OUT, "w"), indent=1); print(json.dumps(res, indent=1))
