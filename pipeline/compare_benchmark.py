"""Summarise the independent runtime check of VersionProbe labels.
Usage: python compare_benchmark.py validation/benchmark_check OUT_JSON"""
import json, math, sys
import pandas as pd

V, OUT = sys.argv[1:3]
s = pd.read_csv(f"{V}/sample.csv").merge(pd.read_csv(f"{V}/results.csv")[
    ["id", "before_ok", "after_broken", "label_correct", "method"]], on="id")


def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


known = s[s.label_correct.isin(["yes", "no"])]
k = int((known.label_correct == "yes").sum())
res = {"sample": len(s), "decided": len(known), "unknown": int(len(s) - len(known)),
       "pairs_correct": k, "pair_label_precision": round(k / len(known), 4), "ci": wilson(k, len(known)),
       "before_valid_rate": round(float((known.before_ok == "yes").mean()), 4),
       "after_invalid_rate": round(float((known.after_broken == "yes").mean()), 4),
       "by_type": {t: {"n": int(len(g)), "correct": int((g.label_correct == "yes").sum()),
                       "precision": round(float((g.label_correct == "yes").mean()), 4),
                       "ci": wilson(int((g.label_correct == "yes").sum()), len(g))}
                   for t, g in known.groupby("change_type")},
       "libraries": int(known.project.nunique()),
       "libraries_all_correct": int(known.groupby("project").label_correct.apply(lambda x: (x == "yes").all()).sum()),
       "wrong_before_only": int(((known.before_ok == "no") & (known.after_broken == "yes")).sum()),
       "wrong_after_only": int(((known.before_ok == "yes") & (known.after_broken == "no")).sum()),
       "wrong_both": int(((known.before_ok == "no") & (known.after_broken == "no")).sum()),
       "methods": s.method.value_counts().to_dict()}
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1))
