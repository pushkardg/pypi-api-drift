"""Precision of the released data on each independent audit sample.
For every audited claim, look up whether the released changes table still makes it (same project, path,
target version, change type and parameter, breaking) and with which confidence; precision = confirmed / claims
still made. Usage: python audit_summary.py DATA_DIR VALIDATION_DIR OUT_JSON"""
import json, sys
import duckdb, pandas as pd

D, V, OUT = sys.argv[1:4]
con = duckdb.connect()
res = {}
for k, (s, r) in {"audit1": ("sample.csv", "results.csv"), "audit2": ("sample2.csv", "results2.csv"),
                  "audit3": ("sample3.csv", "results3.csv")}.items():
    smp = pd.read_csv(f"{V}/{s}").merge(pd.read_csv(f"{V}/{r}")[["id", "verdict"]], on="id")
    smp["param"] = smp.param.fillna("")
    con.register("smp", smp)
    m = con.execute(f"""select smp.id, any_value(c.confidence) conf
        from smp join '{D}/changes.parquet' c
          on c.project = smp.project and c.path = smp.path and c.to_version = smp.to_version
         and c.change_type = smp.change_type and coalesce(c.param, '') = smp.param and c.breaking
        group by 1""").df()
    smp = smp.merge(m, on="id", how="left")
    kept = smp[smp.conf.notna()]
    out = {"sample": len(smp), "libraries": int(smp.project.nunique()),
           "as_audited": smp.verdict.value_counts().to_dict(), "claims_still_made": len(kept)}
    for tier in ["high", "medium"]:
        t = kept[kept.conf == tier]
        out[tier] = {"n": len(t), "confirmed": int((t.verdict == "confirmed").sum()),
                     "partially": int((t.verdict == "partially").sum()),
                     "false_positive": int((t.verdict == "false_positive").sum()),
                     "precision": round(float((t.verdict == "confirmed").mean()), 4) if len(t) else None}
    if "confidence" in smp:  # tier as audited (audit 3 was drawn per tier)
        for tier in ["high", "medium"]:
            t = smp[smp.confidence == tier]
            out[f"as_audited_{tier}"] = {"n": len(t), "precision": round(float((t.verdict == "confirmed").mean()), 4),
                                         "precision_incl_partial": round(float(t.verdict.isin(["confirmed", "partially"]).mean()), 4)}
    res[k] = out
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1))
