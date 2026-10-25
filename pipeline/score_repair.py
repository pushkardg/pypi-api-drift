"""Score the code-repair experiment (see repair/PREREGISTRATION.md).
Usage: python score_repair.py REPAIR_DIR OUT_JSON"""
import glob, json, math, sys
import pandas as pd

R, OUT = sys.argv[1:3]
tasks = pd.read_csv(f"{R}/tasks.csv")
ver = [json.loads(l) for l in open(f"{R}/verify.jsonl")]
jm = pd.read_csv(f"{R}/judge_map.csv")
jo = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{R}/judge_out*.csv"))])
jo["faithful"] = jo.faithful.astype(str).str.strip().str.lower()
judge = jm.merge(jo, on="jid", how="left")
faith = {(r.task_id, r.cond): r.faithful == "yes" for r in judge.itertuples()}


def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def mcnemar_exact(b, c):
    """two-sided exact binomial p for discordant pairs"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return round(min(1.0, 2 * p), 4)


rows, excluded = [], []
for v in ver:
    t = v["task_id"]
    if "env_error" in v:
        excluded.append({"task_id": t, "reason": "environment: " + v["env_error"][:120]}); continue
    if v.get("orig", {}).get("verdict") != "invalid":
        excluded.append({"task_id": t, "reason": f"original snippet is {v.get('orig', {}).get('verdict')} in the new release"})
        continue
    row = {"task_id": t}
    for c in ("a", "b", "c"):
        valid = v.get(c, {}).get("verdict") == "valid"
        f = faith.get((t, c), False)
        row.update({f"valid_{c}": valid, f"faith_{c}": f, f"succ_{c}": valid and f,
                    f"reason_{c}": v.get(c, {}).get("reason", "")})
    rows.append(row)
df = pd.DataFrame(rows).merge(tasks[["task_id", "project", "change_type"]], on="task_id")
n = len(df)
res = {"tasks": len(tasks), "tasks_scored": n, "tasks_excluded": len(excluded), "excluded": excluded,
       "libraries": int(df.project.nunique()), "conditions": {}, "by_stratum": {}, "mcnemar": {}}
names = {"a": "closed book", "b": "documentation", "c": "PyPI API Drift"}
for c in ("a", "b", "c"):
    k = int(df[f"succ_{c}"].sum())
    res["conditions"][c] = {"name": names[c], "valid_rate": round(df[f"valid_{c}"].mean(), 4),
                            "faithful_rate": round(df[f"faith_{c}"].mean(), 4), "success": k,
                            "success_rate": round(k / n, 4), "success_ci": wilson(k, n)}
for st, g in df.groupby("change_type"):
    res["by_stratum"][st] = {"n": len(g), **{c: round(g[f"succ_{c}"].mean(), 4) for c in ("a", "b", "c")}}
for other in ("a", "b"):
    co = int((df.succ_c & ~df[f"succ_{other}"]).sum()); oo = int((~df.succ_c & df[f"succ_{other}"]).sum())
    res["mcnemar"][f"c_vs_{other}"] = {"c_only": co, "other_only": oo, "p": mcnemar_exact(co, oo)}
df.to_csv(f"{R}/scored_tasks.csv", index=False)
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "excluded"}, indent=1))
