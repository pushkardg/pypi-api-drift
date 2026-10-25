"""Compare a fresh extraction against the independent runtime audit.
Usage: python regress_validation.py OUT_DIR validation/sample.csv validation/results.csv"""
import glob, sys
import pandas as pd

out, sample, results = sys.argv[1:4]
s = pd.read_csv(sample).merge(pd.read_csv(results)[["id", "verdict", "evidence"]], on="id")
rows = []
for r in s.itertuples():
    fs = glob.glob(f"{out}/proj/{r.project}/events_*.parquet")
    ev = pd.concat([pd.read_parquet(f) for f in fs]) if fs else pd.DataFrame(columns=["path","to_version","change_type","param","breaking","confidence"])
    e = ev[(ev.path == r.path) & (ev.to_version == r.to_version) & (ev.change_type == r.change_type)]
    if isinstance(r.param, str):
        e = e[e.param == r.param]
    still = bool(len(e) and e.breaking.any())
    conf = ",".join(sorted(set(e[e.breaking].confidence))) if len(e) and "confidence" in e else ""
    other = ev[(ev.path == r.path) & (ev.to_version == r.to_version)].change_type.unique().tolist() if len(ev) else []
    rows.append({"id": r.id, "project": r.project, "path": r.path, "change_type": r.change_type,
                 "audit": r.verdict, "still_claimed_breaking": still, "confidence": conf, "events_now": ",".join(other)})
d = pd.DataFrame(rows)
d["outcome"] = d.apply(lambda x: ("kept_true" if x.still_claimed_breaking else "LOST_true") if x.audit == "confirmed"
                       else ("fixed" if not x.still_claimed_breaking else "STILL_WRONG") if x.audit == "false_positive"
                       else ("partially:kept" if x.still_claimed_breaking else "partially:dropped"), axis=1)
print(d.outcome.value_counts().to_string())
kept = d[d.still_claimed_breaking]
print("claims still made:", len(kept), " precision:", round((kept.audit == "confirmed").mean(), 3))
for c in ["high", "medium"]:
    k = kept[kept.confidence.str.contains(c)]
    print(f"  {c}: n={len(k)} precision={round((k.audit == 'confirmed').mean(), 3) if len(k) else 'n/a'}")
print(d[d.outcome.isin(["LOST_true", "STILL_WRONG"])].to_string(index=False))
d.to_csv(f"{out}/regression.csv", index=False)
