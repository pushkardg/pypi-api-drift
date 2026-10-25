"""Build the human-labelling kit: the 160 claims of the v1.2 precision audit (an unbiased sample, so human precision is
directly comparable with the AI audit) and 40 random VersionProbe pairs from the 400-pair benchmark audit.
AI verdicts go to a separate key file so labelling is blind.
Usage: python build_labeling_kit.py VALIDATION_DIR OUT_DIR"""
import json, os, random, sys
import pandas as pd

V, OUT = sys.argv[1:3]
os.makedirs(OUT, exist_ok=True)
a4 = pd.read_csv(f"{V}/audit4/results_with_strata.csv")
bs = pd.read_csv(f"{V}/benchmark_check2/sample.csv").merge(pd.read_csv(f"{V}/benchmark_check2/results.csv"),
                                                             on=["id", "project"])
dropped = json.load(open(f"{V}/placeholder_releases.json"))["affected"]
bs = bs[~bs.apply(lambda r: r.project in dropped and r.version_before == dropped[r.project]["placeholder"], axis=1)]
bs = bs.sample(40, random_state=20261021)


def claim_text(r):
    p, ct, prm = r.path, r.change_type, r.param if isinstance(r.param, str) and r.param else None
    old, new = r.from_version, r.to_version
    t = {
        "removed": f"`{p}` can be used in {old} but no longer exists in {new}.",
        "param_removed": f"`{p}` accepts the parameter `{prm}` in {old} but not in {new}.",
        "param_renamed": f"The parameter `{prm}` of `{p}` was renamed between {old} and {new}; the old name fails in {new}.",
        "required_param_added": f"`{p}` gained a new required parameter `{prm}` in {new}; calls valid in {old} fail.",
        "became_keyword_only": f"The parameter `{prm}` of `{p}` can be passed positionally in {old} but only by keyword in {new}.",
        "became_positional_only": f"The parameter `{prm}` of `{p}` can be passed by keyword in {old} but only positionally in {new}.",
        "default_removed": f"The parameter `{prm}` of `{p}` has a default in {old} but is required in {new}.",
        "vararg_removed": f"`{p}` accepts extra positional arguments (*{prm or 'args'}) in {old} but not in {new}.",
        "varkw_removed": f"`{p}` accepts arbitrary keyword arguments (**{prm or 'kwargs'}) in {old} but not in {new}.",
        "positional_reordered": f"The positional parameters of `{p}` were reordered between {old} and {new}.",
        "kind_changed": f"`{p}` changed kind between {old} and {new} (e.g. from a function to a property) in a way that breaks callers.",
    }.get(ct, f"`{p}`: {ct} between {old} and {new}.")
    return t + (f" (detail: {r.detail})" if isinstance(r.detail, str) and r.detail else "")


def recipe_claim(r):
    sig = "" if r.change_type == "removed" else " --signature"
    return (f"In two fresh virtual environments, `pip install '{r.project}=={r.from_version}'` and "
            f"`pip install '{r.project}=={r.to_version}'`; in each run `python check_item.py {r.path}{sig}`. "
            f"Release notes, changelogs and documentation are also valid evidence.")


items, key = [], []
for r in a4.itertuples():
    iid = f"C{r.id:03d}"
    items.append({"item_id": iid, "type": "breaking_change_claim", "project": r.project, "old_version": r.from_version,
                  "new_version": r.to_version, "claim": claim_text(r),
                  "question": "Is the claim correct (code written for the old release that relies on this fails on the new one)?",
                  "how_to_check": recipe_claim(r)})
    key.append({"item_id": iid, "ai_verdict": "correct" if r.verdict == "confirmed" else "incorrect",
                "ai_method": r.method, "source": "validation/audit4", "stratum": r.stratum})
for r in bs.itertuples():
    iid = f"P{int(r.id):03d}"
    items.append({"item_id": iid, "type": "benchmark_pair", "project": r.project, "old_version": r.version_before,
                  "new_version": r.version_after,
                  "claim": f"This snippet works with {r.project}=={r.version_before} and fails with "
                           f"{r.project}=={r.version_after} (because of `{r.symbol}`, {r.change_type}"
                           + (f" `{r.param}`" if isinstance(r.param, str) and r.param else "") + "):\n" + r.snippet,
                  "question": "Is the label pair correct (valid in the old release AND invalid in the new one)?",
                  "how_to_check": f"Run the snippet's imports and calls (with placeholder arguments where needed) in "
                                  f"fresh environments with each release; a failure must come from `{r.symbol}`, not "
                                  f"from the placeholder arguments."})
    key.append({"item_id": iid, "ai_verdict": "correct" if str(r.label_correct).lower() == "yes" else "incorrect",
                "ai_method": r.method, "source": "validation/benchmark_check2", "stratum": "versionprobe"})
items = pd.DataFrame(items)
order = list(items.item_id); random.Random(7).shuffle(order)  # interleave the two item types
items = items.set_index("item_id").loc[order].reset_index()
items.to_csv(f"{OUT}/items.csv", index=False)
blank = pd.DataFrame({"item_id": items.item_id, "verdict": "", "evidence": "", "minutes": ""})
for who in ("A", "B"):
    blank.to_csv(f"{OUT}/labels_{who}.csv", index=False)
pd.DataFrame(key).to_csv(f"{OUT}/ai_key_DO_NOT_OPEN_BEFORE_LABELLING.csv", index=False)
print(len(items), "items:", items.type.value_counts().to_dict(),
      "| AI says incorrect:", sum(k["ai_verdict"] == "incorrect" for k in key))
