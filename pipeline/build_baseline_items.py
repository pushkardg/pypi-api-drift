"""Build the model-baseline item files for VersionProbe-verified.

Condition A (closed book): the benchmark prompt as is.
Condition B (API history): the same prompt plus evidence retrieved from the dataset for EVERY library name,
attribute and keyword argument the snippet uses (not only the one that changed): the analysed releases in which each
path existed, and for each keyword the releases whose signature accepted it. No label and no "changed" marker is
given; the model still has to compare the evidence with the stated version.

Usage: python build_baseline_items.py DATA_DIR VERIFIED_PARQUET OUT_DIR N_BATCHES"""
import ast, json, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "release"))
import pandas as pd
from apidrift import check as C

if __name__ == "__main__":
    D, VER, OUT, NB = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    os.makedirs(OUT, exist_ok=True)
    store = C.Store(D)


class Collect(C.Checker):
    """Records every resolved library path and keyword use without reporting anything."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.paths, self.kwargs = [], []

    def lookup(self, node, path):
        proj, iv, api = self.api(path)
        if proj and path not in self.paths:
            self.paths.append(path)
        return api.get(path) if api else None

    def check_args(self, node, path, sig):
        for k in node.keywords:
            if k.arg and (path, k.arg) not in self.kwargs:
                self.kwargs.append((path, k.arg))

    def visit_Call(self, node):
        path = self.resolve(node.func)
        if path:
            self.lookup(node.func, path)
            for k in node.keywords:
                if k.arg and (path, k.arg) not in self.kwargs:
                    self.kwargs.append((path, k.arg))
        self.generic_visit(node)


def ranges(idxs, vs):
    idxs = sorted(set(idxs))
    if not idxs:
        return "none of the analysed releases"
    out, a = [], idxs[0]
    for x, y in zip(idxs, idxs[1:] + [None]):
        if y != x + 1:
            out.append(vs[a] if a == x else f"{vs[a]}–{vs[x]}")
            a = y
    return ", ".join(out)


def evidence(snippet, project, version):
    c = Collect(store, {project: version})
    c.visit(ast.parse(snippet))
    vs = store.versions[project]
    iv = store.intervals(project)
    lines = [f"Analysed releases of {project}: {vs[0]} … {vs[-1]} ({len(vs)} releases)."]
    for p in c.paths:
        h = iv[iv.path == p]
        idx = [i for r in h.itertuples() for i in range(int(r.first_index), int(r.last_index) + 1)]
        lines.append(f"- `{p}` exists in: {ranges(idx, vs)}")
    for p, kw in c.kwargs:
        h = iv[(iv.path == p) & iv.sig_hash.notna()]
        acc = []
        for r in h.itertuples():
            sig = store.sig(r.sig_hash) or {}
            names = {q[0] for q in sig.get("params", []) if q[1] in ("PK", "KO")}
            varkw = any(q[1] == "VK" for q in sig.get("params", []))
            if kw in names or varkw:
                acc += list(range(int(r.first_index), int(r.last_index) + 1))
        lines.append(f"- `{p}(..., {kw}=...)` keyword accepted in: {ranges(acc, vs)}")
    return "\n".join(lines)


if __name__ == "__main__":
    items = pd.read_parquet(VER)
    rows = []
    for r in items.itertuples():
        ev = evidence(r.snippet, r.project, r.version)
        rows.append({"item_id": r.item_id, "pair_id": r.pair_id, "label": r.label, "project": r.project,
                     "version": r.version, "change_released": str(r.change_released)[:10], "change_type": r.change_type,
                     "prompt_a": r.prompt,
                     "prompt_b": r.prompt.replace("Ignoring the placeholder",
                                                  "API history from the PyPI API Drift dataset (analysed releases only):\n"
                                                  + ev + "\n\nIgnoring the placeholder")})
    df = pd.DataFrame(rows)
    df.to_parquet(f"{OUT}/items_with_labels.parquet", index=False)
    random.seed(7)
    order = df.sample(frac=1, random_state=7).reset_index(drop=True)
    order["qid"] = [f"q{i:04d}" for i in range(len(order))]
    order[["qid", "item_id"]].to_csv(f"{OUT}/qid_map.csv", index=False)
    for cond in ("a", "b"):
        for k in range(NB):
            part = order.iloc[k::NB]
            with open(f"{OUT}/cond_{cond}_batch{k + 1}.txt", "w") as f:
                for q in part.itertuples():
                    f.write(f"### {q.qid}\n{getattr(q, 'prompt_' + cond)}\n\n")
    print(len(df), "items;", NB, "batches per condition")
    print(df.prompt_b.iloc[3])
