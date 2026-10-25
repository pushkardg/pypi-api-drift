"""Score the human labels: agreement between the two labellers (Cohen's kappa), agreement of each with the AI
auditor, and human-adjudicated precision with a Wilson interval.

    python score_agreement.py                      # reads labels_A.csv, labels_B.csv, optional labels_adjudicated.csv
    python score_agreement.py --json results.json

Verdicts: correct | incorrect | unsure. Items where A and B disagree (or either is unsure) are listed for
adjudication; put the third person's verdicts in labels_adjudicated.csv (same columns) and re-run.
"""
import json, math, os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OK = {"correct", "incorrect", "unsure"}


def load(name):
    f = os.path.join(HERE, name)
    if not os.path.exists(f):
        return None
    d = pd.read_csv(f, dtype=str).fillna("")
    d["verdict"] = d.verdict.str.strip().str.lower()
    bad = d[(d.verdict != "") & ~d.verdict.isin(OK)]
    if len(bad):
        sys.exit(f"{name}: unknown verdicts {sorted(set(bad.verdict))}; use correct / incorrect / unsure")
    return d.set_index("item_id").verdict


def kappa(x, y):
    m = (x != "") & (y != "")
    x, y = x[m], y[m]
    n = len(x)
    if n == 0:
        return None, 0
    cats = sorted(set(x) | set(y))
    po = (x == y).mean()
    pe = sum((x == c).mean() * (y == c).mean() for c in cats)
    return (round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0), n


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


items = pd.read_csv(os.path.join(HERE, "items.csv")).set_index("item_id")
key = pd.read_csv(os.path.join(HERE, "ai_key_DO_NOT_OPEN_BEFORE_LABELLING.csv")).set_index("item_id")
A, B, C = load("labels_A.csv"), load("labels_B.csv"), load("labels_adjudicated.csv")
if A is None or B is None:
    sys.exit("labels_A.csv and labels_B.csv are required")
ai = key.ai_verdict.reindex(items.index)
res = {"items": len(items), "labelled_A": int((A != "").sum()), "labelled_B": int((B != "").sum())}
res["kappa_A_B"], res["n_A_B"] = kappa(A.reindex(items.index).fillna(""), B.reindex(items.index).fillna(""))
res["kappa_A_AI"], _ = kappa(A.reindex(items.index).fillna(""), ai)
res["kappa_B_AI"], _ = kappa(B.reindex(items.index).fillna(""), ai)

# human verdict: A and B agree on correct/incorrect, else the adjudicator's verdict
final = {}
need = []
for i in items.index:
    a, b = A.get(i, ""), B.get(i, "")
    if a == b and a in ("correct", "incorrect"):
        final[i] = a
    elif C is not None and C.get(i, "") in ("correct", "incorrect"):
        final[i] = C.get(i)
    elif a or b:
        need.append(i)
final = pd.Series(final, dtype=str)
res["human_decided"] = len(final)
res["needs_adjudication"] = need
for t, g in items.groupby("type"):
    f = final.reindex(g.index).dropna()
    k = int((f == "correct").sum()); n = len(f)
    agree = (f == ai.reindex(f.index)).mean() if n else None
    res[t] = {"decided": n, "human_precision": round(k / n, 4) if n else None, "ci": wilson(k, n),
              "ai_precision_same_items": round((ai.reindex(f.index) == "correct").mean(), 4) if n else None,
              "human_ai_agreement": round(agree, 4) if agree is not None else None,
              "ai_wrong_items": sorted(f.index[f != ai.reindex(f.index)])}
print(json.dumps(res, indent=1))
if "--json" in sys.argv:
    json.dump(res, open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1)
