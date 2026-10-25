"""Post-hoc sensitivity check for the code-repair experiment: re-score the SAME answers and AI-judge verdicts with full
argument binding (inspect.signature().bind, so missing required arguments fail) instead of the preregistered
bind_partial. Task set = the preregistered scored tasks (scored_tasks.csv). Not preregistered; reported as such.
Usage: python score_repair_strict.py REPAIR_DIR OUT_JSON"""
import json, sys
import pandas as pd

R, OUT = sys.argv[1:3]
from score_repair_lib import wilson, mcnemar_exact  # noqa: E402

scored = pd.read_csv(f"{R}/scored_tasks.csv")
ver = {}
for line in open(f"{R}/verify_strict.jsonl"):
    v = json.loads(line)
    ver[v["task_id"]] = v
rows, missing_env = [], []
for t in scored.itertuples():
    v = ver.get(t.task_id)
    if v is None or "env_error" in v:
        missing_env.append(t.task_id); continue
    row = {"task_id": t.task_id, "change_type": t.change_type}
    for c in ("a", "b", "c"):
        rerun_partial = v.get(c, {}).get("verdict") == "valid"
        strict = v.get("strict_" + c, {}).get("verdict") == "valid"
        f = bool(getattr(t, f"faith_{c}"))
        row.update({f"orig_valid_{c}": bool(getattr(t, f"valid_{c}")), f"rerun_valid_{c}": rerun_partial,
                    f"strict_valid_{c}": strict, f"faith_{c}": f, f"succ_{c}": bool(getattr(t, f"succ_{c}")),
                    f"strict_succ_{c}": strict and f,
                    f"strict_reason_{c}": "" if strict else v.get("strict_" + c, {}).get("reason", "")})
    rows.append(row)
df = pd.DataFrame(rows)
n = len(df)
names = {"a": "closed book", "b": "documentation", "c": "PyPI API Drift"}
res = {"note": "post-hoc sensitivity analysis, not preregistered; same answers and judge verdicts, full binding",
       "tasks_scored_preregistered": len(scored), "tasks_rechecked": n, "env_failures_on_recheck": missing_env,
       "conditions": {}, "mcnemar_strict": {}, "rerun_agreement": {}}
for c in ("a", "b", "c"):
    k = int(df[f"strict_succ_{c}"].sum())
    res["conditions"][c] = {
        "name": names[c],
        "success_preregistered": round(df[f"succ_{c}"].mean(), 4),
        "valid_strict": round(df[f"strict_valid_{c}"].mean(), 4),
        "success_strict": k, "success_strict_rate": round(k / n, 4), "success_strict_ci": wilson(k, n),
        "lost_to_missing_required_args": int((df[f"succ_{c}"] & ~df[f"strict_succ_{c}"]).sum()),
    }
    res["rerun_agreement"][c] = round((df[f"orig_valid_{c}"] == df[f"rerun_valid_{c}"]).mean(), 4)
for other in ("a", "b"):
    co = int((df.strict_succ_c & ~df[f"strict_succ_{other}"]).sum())
    oo = int((~df.strict_succ_c & df[f"strict_succ_{other}"]).sum())
    res["mcnemar_strict"][f"c_vs_{other}"] = {"c_only": co, "other_only": oo, "p": mcnemar_exact(co, oo)}
df.to_csv(f"{R}/scored_tasks_strict.csv", index=False)
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1))
