"""Multi-model replication: VersionProbe-verified (closed book) and the code-repair experiment for every model run,
scored exactly as the preregistered single-model study (same tasks, same runtime check, same blind AI judge).
Usage: python score_models.py BASELINE_DIR REPAIR_DIR MODELS_DIR OUT_JSON
  BASELINE_DIR / REPAIR_DIR: the original (claude-opus-5-5) runs; MODELS_DIR/<model>/{baseline,repair}: the others."""
import glob, json, os, re, subprocess, sys
import pandas as pd
from score_repair_lib import wilson, mcnemar_exact

B, R, M, OUT = sys.argv[1:5]
HERE = os.path.dirname(os.path.abspath(__file__))
items = pd.read_parquet(f"{B}/items_with_labels.parquet").merge(pd.read_csv(f"{B}/qid_map.csv"), on="item_id")
runs = {"claude-opus-5-5": (B, R)}
for d in sorted(glob.glob(f"{M}/*/")):
    name = os.path.basename(d.rstrip("/"))
    runs[{"sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"}.get(name, name)] = \
        (f"{d}baseline", f"{d}repair")


def probe(bdir):
    a = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{bdir}/answers_a_batch*.csv"))])
    a["answer"] = a.answer.astype(str).str.strip().str.lower()
    ans = a.drop_duplicates("qid").set_index("qid").answer
    ok = items.qid.map(ans) == items.label
    pairs = ok.groupby(items.pair_id).all()
    return {"answered": int(items.qid.map(ans).isin(["valid", "invalid"]).sum()), "items": len(items),
            "item_acc": round(ok.mean(), 4), "item_ci": wilson(int(ok.sum()), len(ok)),
            "pair_acc": round(pairs.mean(), 4), "pair_ci": wilson(int(pairs.sum()), len(pairs)),
            "acc_on_valid": round(ok[items.label == "valid"].mean(), 4),
            "acc_on_invalid": round(ok[items.label == "invalid"].mean(), 4)}


def repair(rdir, tag):
    """Re-use the preregistered scorer; strict-binding numbers from verify_strict.jsonl when present."""
    if not os.path.exists(f"{rdir}/verify.jsonl") and os.path.exists(f"{rdir}/verify_strict.jsonl"):
        with open(f"{rdir}/verify.jsonl", "w") as f:  # same verdict keys (orig/a/b/c) as the preregistered run
            for line in open(f"{rdir}/verify_strict.jsonl"):
                v = json.loads(line)
                f.write(json.dumps({k: x for k, x in v.items() if not k.startswith("strict_")}) + "\n")
    out = f"{rdir}/results.json"
    subprocess.run([sys.executable, f"{HERE}/score_repair.py", rdir, out], check=True, capture_output=True)
    res = json.load(open(out))
    if os.path.exists(f"{rdir}/verify_strict.jsonl"):
        so = f"{rdir}/results_strict_binding.json"
        subprocess.run([sys.executable, f"{HERE}/score_repair_strict.py", rdir, so], check=True, capture_output=True)
        res["strict_binding"] = json.load(open(so))
    return res


def unchanged(rdir):
    """Share of answers per condition that return the original snippet unchanged (whitespace ignored)."""
    t = pd.read_csv(f"{rdir}/tasks.csv").set_index("task_id")
    norm = lambda x: re.sub(r"\s+", "", x or "")
    out = {}
    for c in ("a", "b", "c"):
        ans = {}
        for f in sorted(glob.glob(f"{rdir}/answers_{c}_batch*.md")):
            for m in re.finditer(r"^### (t\d+)\s*\n(.*?)(?=^### t\d+|\Z)", open(f).read(), re.S | re.M):
                code = re.search(r"```(?:python)?\n(.*?)```", m.group(2), re.S)
                ans[m.group(1)] = code.group(1) if code else m.group(2)
        out[c] = round(sum(norm(ans.get(i)) == norm(t.loc[i, "snippet"]) for i in t.index) / len(t), 4)
    return out


summary = {"note": "same 150 tasks, same runtime check and blind AI judge (claude-opus-5-5) for every model; "
                   "claude-opus-5-5 is the preregistered run, the others are replications run after it", "models": {}}
for name, (bdir, rdir) in runs.items():
    p = probe(bdir)
    r = repair(rdir, name)
    c = r["conditions"]
    summary["models"][name] = {
        "versionprobe_verified_closed_book": p,
        "repair": {"tasks_scored": r["tasks_scored"], "tasks_excluded": r["tasks_excluded"],
                   **{k: {"valid": c[k]["valid_rate"], "faithful": c[k]["faithful_rate"], "success": c[k]["success_rate"],
                          "success_ci": c[k]["success_ci"]} for k in ("a", "b", "c")},
                   "unchanged_answers": unchanged(rdir),
                   "mcnemar_c_vs_a": r["mcnemar"]["c_vs_a"], "mcnemar_c_vs_b": r["mcnemar"]["c_vs_b"],
                   "strict_binding_success": {k: r["strict_binding"]["conditions"][k]["success_strict_rate"]
                                              for k in ("a", "b", "c")} if "strict_binding" in r else None}}
# pooled across models: sign of the effect per model and a pooled exact test on all discordant pairs
for other in ("a", "b"):
    co = sum(m["repair"][f"mcnemar_c_vs_{other}"]["c_only"] for m in summary["models"].values())
    oo = sum(m["repair"][f"mcnemar_c_vs_{other}"]["other_only"] for m in summary["models"].values())
    summary[f"pooled_c_vs_{other}"] = {"c_only": co, "other_only": oo, "p": mcnemar_exact(co, oo),
                                       "models_with_gain": sum(m["repair"][f"mcnemar_c_vs_{other}"]["c_only"] >
                                                               m["repair"][f"mcnemar_c_vs_{other}"]["other_only"]
                                                               for m in summary["models"].values()),
                                       "note": "pooling treats model-task pairs as independent; indicative only"}
json.dump(summary, open(OUT, "w"), indent=1)
print(json.dumps(summary, indent=1))
