"""Blind judging files for the code-repair experiment: each item shows the original snippet, the two versions and
one candidate; the condition is hidden and items are shuffled. Usage: python build_repair_judging.py REPAIR_DIR N"""
import glob, json, re, sys
import pandas as pd
R, NB = sys.argv[1], int(sys.argv[2])
tasks = pd.read_csv(f"{R}/tasks.csv").set_index("task_id")
def answers(cond):
    out = {}
    for f in sorted(glob.glob(f"{R}/answers_{cond}_batch*.md")):
        for m in re.finditer(r"^### (t\d+)\s*\n(.*?)(?=^### t\d+|\Z)", open(f).read(), re.S | re.M):
            code = re.search(r"```(?:python)?\n(.*?)```", m.group(2), re.S)
            out[m.group(1)] = code.group(1) if code else m.group(2).strip()
    return out
rows = []
for c in ("a", "b", "c"):
    for tid, code in answers(c).items():
        rows.append({"task_id": tid, "cond": c, "code": code})
df = pd.DataFrame(rows).sample(frac=1, random_state=5).reset_index(drop=True)
df["jid"] = [f"j{i:04d}" for i in range(len(df))]
df[["jid", "task_id", "cond"]].to_csv(f"{R}/judge_map.csv", index=False)
for k in range(NB):
    with open(f"{R}/judge_batch{k + 1}.txt", "w") as f:
        for r in df.iloc[k::NB].itertuples():
            t = tasks.loc[r.task_id]
            f.write(f"### {r.jid}\nLibrary: {t.project}, migrating from {t.version_before} to {t.version_after}.\n"
                    f"Original:\n```python\n{t.snippet}\n```\nCandidate:\n```python\n{r.code}\n```\n\n")
print(len(df))
