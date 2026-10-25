"""Code-repair experiment: build the three prompt conditions for each task (see repair/PREREGISTRATION.md).

  A closed book        the snippet, the old and the new version, nothing else
  B documentation      + an API reference page for the module(s) the snippet imports, generated with griffe (the
                       mkdocstrings documentation engine) from the NEW release's wheel: public members, signatures and
                       the first docstring line. Independent of this dataset.
  C PyPI API Drift     + the dataset's API history for every name and keyword in the snippet, the recorded change
                       between the two releases (e.g. "still available as X", "a -> b"), and the new signatures.

Usage: python build_repair_items.py DATA_DIR TASKS_CSV SELECTED_PARQUET OUT_DIR N_BATCHES"""
import ast, json, os, random, sys, urllib.request, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "release"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import griffe
from apidrift import check as C
from build_baseline_items import Collect, ranges

D, TASKS, SEL, OUT, NB = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
os.makedirs(f"{OUT}/wheels", exist_ok=True)
store = C.Store(D)
sel = pd.read_parquet(SEL, columns=["project", "version", "wheel_url"])
url = dict(zip(zip(sel.project, sel.version), sel.wheel_url))
tasks = pd.read_csv(TASKS)
changes = pd.read_parquet(f"{D}/changes.parquet", columns=["project", "to_version", "path", "change_type", "param",
                                                           "detail", "breaking"],
                          filters=[("project", "in", sorted(set(tasks.project)))])


def wheel_dir(project, version):
    d = f"{OUT}/wheels/{project}-{version}"
    if not os.path.isdir(d):
        f = d + ".whl"
        if not os.path.exists(f):
            urllib.request.urlretrieve(url[(project, version)], f)
        with zipfile.ZipFile(f) as z:
            z.extractall(d, [n for n in z.namelist() if n.endswith((".py", ".pyi"))])
        os.remove(f)
    return d


def modules_of(snippet):
    mods = []
    for n in ast.walk(ast.parse(snippet)):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
        elif isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
    return list(dict.fromkeys(mods))


def _module_file(root, dotted):
    base = os.path.join(root, *dotted.split("."))
    for f in (base + ".py", os.path.join(base, "__init__.py"), base + ".pyi", os.path.join(base, "__init__.pyi")):
        if os.path.exists(f):
            return f
    return None


def _render(mod, limit_items=400):
    out = []
    for name, m in sorted(mod.members.items())[:limit_items]:
        if name.startswith("_"):
            continue
        try:
            if m.is_alias:
                out.append(f"- `{name}` (imported from `{m.target_path}`)")
                continue
            kind = m.kind.value
            if kind == "function":
                sig = "(" + ", ".join(p.name + ("=..." if p.default is not None else "") for p in m.parameters) + ")"
            elif kind == "class" and "__init__" in m.members and not m.members["__init__"].is_alias:
                sig = "(" + ", ".join(p.name + ("=..." if p.default is not None else "")
                                      for p in m.members["__init__"].parameters if p.name != "self") + ")"
            else:
                sig = ""
            doc = m.docstring.value.strip().splitlines()[0] if m.docstring and m.docstring.value.strip() else ""
            out.append(f"- {kind} `{name}{sig}`" + (f": {doc[:120]}" if doc else ""))
            if kind == "class":
                for mn, mm in sorted(m.members.items()):
                    if not mn.startswith("_") and not mm.is_alias and mm.kind.value == "function":
                        out.append(f"  - method `{mn}(" + ", ".join(p.name for p in mm.parameters
                                                                   if p.name not in ("self", "cls")) + ")`")
        except Exception:
            out.append(f"- `{name}`")
    return out


def doc_page(project, version, snippet, limit=7000):
    """API reference for the module(s) the snippet imports, in the new release: griffe (the mkdocstrings
    documentation engine) visits just that module's source file from the release wheel. If the module no longer
    exists, the page shows its nearest existing parent package and that package's submodules."""
    import pathlib
    root = wheel_dir(project, version)
    out = []
    for mod in modules_of(snippet):
        parts = mod.split(".")
        k = len(parts)
        while k > 0 and _module_file(root, ".".join(parts[:k])) is None:
            k -= 1
        if k == 0:
            out.append(f"Module `{mod}` does not exist in {project}=={version}.")
            continue
        found = ".".join(parts[:k])
        f = _module_file(root, found)
        if found != mod:
            out.append(f"Module `{mod}` does not exist in {project}=={version}. Nearest existing module: `{found}`.")
        try:
            m = griffe.visit(found.rpartition(".")[2], filepath=pathlib.Path(f),
                             code=open(f, encoding="utf-8", errors="replace").read())
            out.append(f"Module `{found}` in {project}=={version}:")
            out += _render(m)
        except Exception as e:
            out.append(f"(could not document `{found}`: {type(e).__name__})")
        if f.endswith(("__init__.py", "__init__.pyi")):
            d = os.path.dirname(f)
            subs = sorted(x[:-3] if x.endswith(".py") else x for x in os.listdir(d)
                          if not x.startswith("_") and (x.endswith(".py") or os.path.isdir(os.path.join(d, x))))
            if subs:
                out.append(f"Submodules of `{found}`: " + ", ".join(subs[:80]))
    text = "\n".join(out)
    return text[:limit] + ("\n... (truncated)" if len(text) > limit else "")


def doc_page_limited(project, version, snippet):
    """doc_page in a child process capped at 2 GB, so a huge package (transformers) cannot exhaust memory; for
    such packages griffe is asked for the imported submodule only."""
    import multiprocessing as mp, resource
    wheel_dir(project, version)
    q = mp.Queue()

    def run():
        resource.setrlimit(resource.RLIMIT_AS, (2_500_000_000, 2_500_000_000))
        try:
            q.put(doc_page(project, version, snippet))
        except BaseException as e:
            q.put(f"(documentation unavailable: {type(e).__name__})")
    pr = mp.Process(target=run)
    pr.start(); pr.join(300)
    if pr.is_alive():
        pr.kill(); return "(documentation unavailable: timeout)"
    return q.get() if not q.empty() else "(documentation unavailable: the package is too large to document here)"


def drift_evidence(project, old, new, snippet):
    c = Collect(store, {project: old})
    c.visit(ast.parse(snippet))
    vs = store.versions[project]
    iv = store.intervals(project)
    lines = [f"Analysed releases of {project}: {vs[0]} … {vs[-1]} ({len(vs)} releases)."]
    ch = changes[(changes.project == project) & (changes.to_version == new) & changes.breaking]
    ni, _ = store.version_index(project, new)
    api_new = store.api_at(project, ni) if ni is not None else {}
    for p in c.paths:
        h = iv[iv.path == p]
        idx = [i for r in h.itertuples() for i in range(int(r.first_index), int(r.last_index) + 1)]
        lines.append(f"- `{p}` exists in: {ranges(idx, vs)}")
        for r in ch[ch.path == p].itertuples():
            lines.append(f"  - {old} -> {new}: {r.change_type}" + (f" `{r.param}`" if r.param else "") +
                         (f" ({r.detail})" if r.detail else ""))
        info = api_new.get(p)
        if info and info[1]:
            sig = store.sig(info[1]) or {}
            lines.append(f"  - signature in {new}: (" + ", ".join(
                q[0] + ("=..." if q[2] else "") if q[1] in ("PO", "PK", "KO") else ("*" if q[1] == "VA" else "**") + q[0]
                for q in sig.get("params", [])) + ")")
    for p, kw in c.kwargs:
        h = iv[(iv.path == p) & iv.sig_hash.notna()]
        acc = []
        for r in h.itertuples():
            sig = store.sig(r.sig_hash) or {}
            if kw in {q[0] for q in sig.get("params", []) if q[1] in ("PK", "KO")} or \
                    any(q[1] == "VK" for q in sig.get("params", [])):
                acc += list(range(int(r.first_index), int(r.last_index) + 1))
        lines.append(f"- `{p}(..., {kw}=...)` keyword accepted in: {ranges(acc, vs)}")
    return "\n".join(lines)


HEAD = ("You are migrating Python code to a newer library release.\n"
        "This snippet was written for {project}=={old}. Arguments written as `...` are placeholders; keep them.\n"
        "```python\n{snippet}\n```\n")
TAIL = ("Rewrite the snippet so that every library name, attribute and keyword argument it uses is valid in "
        "{project}=={new}, keeping its purpose (use the replacement API where one exists; do not just delete the "
        "library calls). Reply with only the code, in one ```python block.")
rows = []
for t in tasks.itertuples():
    base = HEAD.format(project=t.project, old=t.version_before, snippet=t.snippet)
    tail = TAIL.format(project=t.project, new=t.version_after)
    try:
        doc = doc_page(t.project, t.version_after, t.snippet)
    except Exception as e:
        doc = f"(documentation unavailable: {type(e).__name__})"
    ev = drift_evidence(t.project, t.version_before, t.version_after, t.snippet)
    rows.append({"task_id": t.task_id, "a": base + tail,
                 "b": base + f"\nDocumentation for {t.project}=={t.version_after} (generated from the release):\n{doc}\n\n" + tail,
                 "c": base + f"\nAPI history from the PyPI API Drift dataset:\n{ev}\n\n" + tail,
                 "doc_chars": len(doc), "ev_chars": len(ev)})
    print(t.task_id, t.project, len(doc), len(ev), flush=True)
df = pd.DataFrame(rows)
df.to_parquet(f"{OUT}/prompts.parquet", index=False)
order = df.sample(frac=1, random_state=11).reset_index(drop=True)
for cond in ("a", "b", "c"):
    for k in range(NB):
        with open(f"{OUT}/cond_{cond}_batch{k + 1}.txt", "w") as f:
            for q in order.iloc[k::NB].itertuples():
                f.write(f"### {q.task_id}\n{getattr(q, cond)}\n\n")
print(df[["doc_chars", "ev_chars"]].describe())
