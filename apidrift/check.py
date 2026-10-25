"""apidrift-check: flag Python code that uses library APIs which don't exist, or are called wrongly,
in the library versions you pin. Works offline from the PyPI API Drift dataset; never imports the
libraries.

  python apidrift_check.py my_script.py --pin pandas==2.2.0 --pin numpy==2.0.0
  python apidrift_check.py my_script.py -r requirements.txt --json
  python apidrift_check.py src/ --lock uv.lock --sarif apidrift.sarif   # whole project, GitHub code scanning
Add `# apidrift: ignore` to a line to suppress findings on it.

Checks
  * `import` / `from ... import` of names that don't exist in the pinned version
  * module attribute access and calls (pd.read_csv, np.float_, sklearn.utils.foo)
  * method calls on objects created from a known class in the same scope (df = pd.DataFrame(); df.append(...))
  * keyword arguments that don't exist, too many positional arguments, missing required arguments
  * use of symbols carrying a deprecation marker in that version
For every problem it reports when the symbol last existed / first appeared, so the fix is obvious.
"""
import argparse, ast, json, os, re, sys

STDLIB = set(sys.stdlib_module_names)
from functools import lru_cache
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from packaging.version import Version, InvalidVersion

DATA = os.environ.get("APIDRIFT_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
CALLABLE = {"function", "method", "classmethod", "staticmethod", "class", "class_open"}


class Store:
    """Reads only what a check needs: the row groups of api_history that hold the pinned projects (located from the
    Parquet footer statistics) and the signatures those projects reference. Memory therefore scales with the
    libraries being checked, not with the dataset."""

    def __init__(self, data=DATA):
        self.data = data
        rel = pq.read_table(f"{data}/releases.parquet", columns=["project", "version", "version_index", "status"])
        rows = sorted(zip(rel.column("project").to_pylist(), rel.column("version_index").to_pylist(),
                          rel.column("version").to_pylist(), rel.column("status").to_pylist()))
        self.versions, self.unanalysed = {}, set()
        for p, _, v, st in rows:
            self.versions.setdefault(p, []).append(v)
            if st != "ok":  # releases the pipeline could not analyse are never used as a stand-in
                self.unanalysed.add((p, v))
        tops = pq.read_table(f"{data}/top_level_modules.parquet")
        self.top_to_projects = {}
        for p, m in zip(tops.column("project").to_pylist(), tops.column("module").to_pylist()):
            self.top_to_projects.setdefault(m, []).append(p)
        self._sigs = {}
        self._rg = None

    def _row_groups(self, project):
        if self._rg is None:  # project -> row groups, from footer min/max statistics (api_history is sorted)
            f = pq.ParquetFile(f"{self.data}/api_history.parquet")
            col = f.schema_arrow.get_field_index("project")
            self._rg = []
            for i in range(f.metadata.num_row_groups):
                st = f.metadata.row_group(i).column(col).statistics
                self._rg.append((st.min, st.max) if st is not None and st.has_min_max else (None, None))
        return [i for i, (lo, hi) in enumerate(self._rg) if lo is None or lo <= project <= hi]

    @lru_cache(maxsize=8)
    def intervals(self, project):
        f = pq.ParquetFile(f"{self.data}/api_history.parquet")
        cols = ["project", "path", "kind", "sig_hash", "deprecated", "first_index", "last_index"]
        if "coverage_status" in f.schema_arrow.names:  # v1.1+ tables; older or fixture data may lack it
            cols.append("coverage_status")
        t = f.read_row_groups(self._row_groups(project), columns=cols)
        t = t.filter(pc.equal(t.column("project"), project))
        iv = t.drop(["project"]).to_pandas()
        self._load_sigs(set(iv.sig_hash.dropna()))
        return iv

    def _load_sigs(self, hashes):
        """Read only the signatures a project needs (the full table is ~800k rows), one row group at a time."""
        need = {h for h in hashes if h not in self._sigs}
        if not need:
            return
        f = pq.ParquetFile(f"{self.data}/signatures.parquet")
        want = sorted(need)
        col = f.schema_arrow.get_field_index("sig_hash")
        for i in range(f.metadata.num_row_groups):
            st = f.metadata.row_group(i).column(col).statistics
            if st is not None and st.has_min_max:  # the table is sorted by sig_hash: skip groups with no wanted hash
                import bisect
                k = bisect.bisect_left(want, st.min)
                if k == len(want) or want[k] > st.max:
                    continue
            t = f.read_row_group(i, columns=["sig_hash", "sig"])
            t = t.filter(pc.is_in(t.column("sig_hash"), value_set=pa.array(want)))
            self._sigs.update(zip(t.column("sig_hash").to_pylist(), t.column("sig").to_pylist()))
        self._sigs.update({h: None for h in need if h not in self._sigs})

    def sig(self, h):
        if h is None:
            return None
        if h not in self._sigs:
            self._load_sigs({h})
        v = self._sigs.get(h)
        return json.loads(v) if v else None

    def version_index(self, project, version, nearest=False):
        """(index, version) of the analysed release matching the pin exactly; (None, None) when that release
        was not analysed, unless nearest=True, which falls back to the closest analysed release at or below it."""
        vs = self.versions.get(project, [])
        ok = [i for i, v in enumerate(vs) if (project, v) not in self.unanalysed]
        if version is None:
            return (ok[-1], vs[ok[-1]]) if ok else (None, None)
        if version in vs and (project, version) not in self.unanalysed:
            return vs.index(version), version
        try:
            want = Version(version)
            for i in ok:  # 2.0 vs 2.0.0, 1.0.0rc1 vs 1.0.0-rc.1 style spellings
                try:
                    if Version(vs[i]) == want:
                        return i, vs[i]
                except InvalidVersion:
                    continue
        except InvalidVersion:
            pass
        if not nearest:
            return None, None
        try:
            pv = Version(version)
            below = [i for i in ok if Version(vs[i]) <= pv]
        except InvalidVersion:
            below = []
        if not below:
            return None, None
        return below[-1], vs[below[-1]]

    @lru_cache(maxsize=6)  # one dict per (project, release); big projects hold ~1M paths each
    def api_at(self, project, idx):
        iv = self.intervals(project)
        live = iv[(iv.first_index <= idx) & (iv.last_index >= idx)]
        return {r.path: (r.kind, r.sig_hash if isinstance(r.sig_hash, str) else None, bool(r.deprecated))
                for r in live.itertuples()}

    def history_hint(self, project, path, idx):
        iv = self.intervals(project)
        h = iv[iv.path == path]
        vs = self.versions[project]
        if h.empty:
            return "not found in any analysed release"
        before = h[h.last_index < idx]
        after = h[h.first_index > idx]
        msgs = []
        if not before.empty:
            msgs.append(f"last present in {vs[int(before.last_index.max())]}")
        if not after.empty:
            msgs.append(f"first appears in {vs[int(after.first_index.min())]}")
        return "; ".join(msgs)


def parse_pins(pins, req):
    out = {}
    lines = list(pins or [])
    if req:
        lines += [l.split("#")[0].strip() for l in open(req)]
    for l in lines:
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[.*\])?\s*==\s*([^\s;,]+)", l)
        if m:
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
        elif l.strip():
            n = re.match(r"^\s*([A-Za-z0-9_.\-]+)", l)
            if n:
                out.setdefault(re.sub(r"[-_.]+", "-", n.group(1)).lower(), None)
    return out


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path):
    """Exact versions from a lockfile: uv.lock, poetry.lock, pylock.toml (PEP 751) or Pipfile.lock."""
    base = os.path.basename(path)
    text = open(path, encoding="utf-8").read()
    out = {}
    if base.endswith(".json") or base == "Pipfile.lock":
        data = json.loads(text)
        for sect in ("default", "develop"):
            for name, info in (data.get(sect) or {}).items():
                v = (info or {}).get("version", "")
                if v.startswith("=="):
                    out[_norm(name)] = v[2:]
        return out
    try:
        import tomllib
        data = tomllib.loads(text)
        for pkg in data.get("package", []) + data.get("packages", []):
            if pkg.get("name") and pkg.get("version"):
                out[_norm(pkg["name"])] = str(pkg["version"])
        return out
    except ImportError:  # Python < 3.11: tolerant line scan of [[package]] tables
        name = None
        for line in text.splitlines():
            m = re.match(r'^\s*name\s*=\s*"([^"]+)"', line)
            if m:
                name = m.group(1); continue
            m = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
            if m and name:
                out[_norm(name)] = m.group(1); name = None
        return out


def find_lock(start="."):
    for n in ("uv.lock", "poetry.lock", "pylock.toml", "Pipfile.lock"):
        if os.path.exists(os.path.join(start, n)):
            return os.path.join(start, n)
    return None


def python_files(paths):
    skip = {".git", ".venv", "venv", "env", "site-packages", "node_modules", "__pycache__", "build", "dist", ".tox"}
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
                yield from (os.path.join(root, f) for f in sorted(files) if f.endswith(".py"))
        else:
            yield p


def sarif(results, version="0.1.0"):
    """SARIF 2.1.0 for GitHub code scanning."""
    rules = {"missing": ("API not available in the pinned version", "error"),
             "bad_call": ("Call not accepted by the pinned version's signature", "error"),
             "deprecated": ("API carries a deprecation marker in the pinned version", "note")}
    runs = [{"tool": {"driver": {"name": "apidrift-check", "version": version,
                                 "rules": [{"id": k, "shortDescription": {"text": v[0]},
                                            "defaultConfiguration": {"level": v[1]}} for k, v in rules.items()]}},
             "results": [{"ruleId": i["kind"], "level": rules.get(i["kind"], ("", "warning"))[1],
                          "message": {"text": i["message"]},
                          "locations": [{"physicalLocation": {
                              "artifactLocation": {"uri": os.path.relpath(f).replace(os.sep, "/")},
                              "region": {"startLine": max(1, i["line"]), "startColumn": i["col"] + 1}}}]}
                         for f, i in results]}]
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": runs}


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


class Checker(ast.NodeVisitor):
    def __init__(self, store, pins, strict=False, nearest=False):
        self.s, self.pins, self.strict, self.nearest = store, pins, strict, nearest
        self.coverage = {}   # project -> note about version coverage
        self._ph = {}
        self.lazy_annotations = False
        self.aliases = {}     # local name -> full dotted path
        self.var_types = {}   # local var -> class path
        self.issues = []
        self.ctx = {}         # project -> (idx, version)
        self.class_bases = []  # stack: library classes the enclosing class derives from (for super() calls)

    def project_for(self, path):
        """Distribution owning the longest module prefix of `path` (handles namespace packages)."""
        parts = path.split(".")
        if parts[0] in STDLIB:
            return None  # PyPI backports named like stdlib modules (typing, pathlib, ...) never shadow the stdlib
        for k in range(len(parts), 0, -1):
            cands = self.s.top_to_projects.get(".".join(parts[:k]))
            if cands:
                pinned = [c for c in cands if c in self.pins]
                return (pinned or cands)[0]
        return None

    def api(self, path):
        proj = self.project_for(path)
        if proj is None:
            return None, None, None
        if proj not in self.ctx:
            pin = self.pins.get(proj)
            self.ctx[proj] = self.s.version_index(proj, pin, self.nearest)
            idx, ver = self.ctx[proj]
            if pin is None:
                self.coverage[proj] = f"{proj}: no version pinned; checked against the latest analysed release {ver}"
            elif idx is None:
                self.coverage[proj] = (f"{proj}=={pin} is not among the analysed releases; {proj} was not checked "
                                       f"(use --nearest to check against the closest earlier analysed release)")
            elif ver != pin:
                self.coverage[proj] = f"{proj}=={pin} not analysed; checked against the nearest earlier release {ver}"
        idx, ver = self.ctx[proj]
        if idx is None:
            return proj, None, None
        return proj, (idx, ver), self.s.api_at(proj, idx)

    def report(self, node, kind, path, msg, proj=None, ver=None):
        self.issues.append({"line": getattr(node, "lineno", 0), "col": getattr(node, "col_offset", 0),
                            "kind": kind, "symbol": path, "project": proj, "version": ver, "message": msg})

    def lookup(self, node, path):
        """Return (kind, sig_hash, deprecated) or None; reports missing symbols."""
        proj, iv, api = self.api(path)
        if api is None:
            return None
        idx, ver = iv
        if path in api:
            k, h, dep = api[path]
            if dep:
                self.report(node, "deprecated", path, f"{path} carries a deprecation marker in {proj}=={ver}", proj, ver)
            return api[path]
        if any(seg.startswith("_") for seg in path.split(".")):
            return None  # private and dunder names are outside the tracked public API
        if not self.strict and not (self.s.intervals(proj).path == path).any():
            return None  # never part of the public API in any analysed release: unknown, stay silent
        parent = path.rpartition(".")[0]
        # only judge paths whose parent is a module/class we know, so unknown attribute chains stay silent
        # class_open = class whose members we can't fully see (external base, __getattr__): stay silent
        if parent and parent in api and api[parent][0] == "class_open":
            # open class: only flag members we know existed on this class in another release
            if not (self.s.intervals(proj).path == path).any():
                return None
        if parent and parent in api and api[parent][0] in ("module", "module_alias", "class", "class_open"):
            hint = self.s.history_hint(proj, path, idx)
            if "last present" not in hint and not self.strict:
                return None  # only in later releases: may have existed outside the tracked public API before
            self.report(node, "missing", path, f"{path} does not exist in {proj}=={ver} ({hint})", proj, ver)
        elif parent and parent not in api:
            # a whole module that used to exist is gone (e.g. pkg_resources in setuptools 82): report the module
            segs = path.split(".")
            k = next((j for j in range(len(segs) - 1, 0, -1) if ".".join(segs[:j]) in api), 0)
            gone = ".".join(segs[:k + 1])
            if gone != path and (self.s.intervals(proj).path == gone).any():
                hint = self.s.history_hint(proj, gone, idx)
                if "last present" in hint:
                    self.report(node, "missing", gone, f"module {gone} does not exist in {proj}=={ver} ({hint})",
                                proj, ver)
        return None

    # ---- code that never runs: TYPE_CHECKING blocks and postponed annotations
    def visit_If(self, node):
        t = node.test
        if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"):
            for s in node.orelse:
                self.visit(s)
            return
        self.generic_visit(node)

    def _visit_function(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(d)
        if not self.lazy_annotations:
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [node.args.vararg, node.args.kwarg]:
                if a is not None and a.annotation is not None:
                    self.visit(a.annotation)
            if node.returns is not None:
                self.visit(node.returns)
        for s in node.body:
            self.visit(s)

    visit_FunctionDef = visit_AsyncFunctionDef = _visit_function

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
        if not self.lazy_annotations:
            self.visit(node.annotation)

    # ---- imports
    def visit_Import(self, node):
        for a in node.names:
            if a.asname:
                self.aliases[a.asname] = a.name
            else:
                self.aliases[a.name.split(".")[0]] = a.name.split(".")[0]
            if "." in a.name:
                self.lookup(node, a.name)

    def visit_ImportFrom(self, node):
        if node.level or not node.module:
            return
        for a in node.names:
            if a.name == "*":
                continue
            path = f"{node.module}.{a.name}"
            self.aliases[a.asname or a.name] = path
            self.lookup(node, path)

    # ---- expressions
    def resolve(self, expr):
        d = dotted(expr)
        if not d:
            return None
        if d[0] in self.aliases:
            return ".".join([self.aliases[d[0]]] + d[1:])
        if d[0] in self.var_types and len(d) >= 2:
            return ".".join([self.var_types[d[0]]] + d[1:])
        return None

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load):
            path = self.resolve(node)
            if path:
                self.lookup(node, path)
                return  # don't re-check prefixes
        self.generic_visit(node)

    def visit_Assign(self, node):
        self.generic_visit(node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        name = node.targets[0].id
        if not isinstance(node.value, ast.Call):
            self.var_types.pop(name, None); return
        f = node.value.func
        path = self.resolve(f)
        info = self.lookup_quiet(path) if path else None
        if info and info[0] in ("class", "class_open"):
            self.var_types[name] = path; return
        # x = x.method(...) keeps x's class when the method returns that class (or the method is gone)
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in self.var_types:
            cls = self.var_types[f.value.id]
            short = cls.rpartition(".")[2]
            sig = self.s.sig(info[1]) if info and info[1] else None
            ret = (sig or {}).get("returns") or ""
            if info is None or short in ret or "Self" in ret:
                self.var_types[name] = cls; return
        self.var_types.pop(name, None)

    def lookup_quiet(self, path):
        proj, iv, api = self.api(path)
        return api.get(path) if api else None

    def visit_For(self, node):
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        for d in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(d)
        self.class_bases.append([p for p in (self.resolve(b) for b in node.bases) if p])
        for st in node.body:
            self.visit(st)
        self.class_bases.pop()

    def _super_target(self, node):
        """super().m(...) / super(C, self).m(...) inside a class deriving from a library class -> base path(s)."""
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) and
                isinstance(f.value.func, ast.Name) and f.value.func.id == "super" and self.class_bases):
            return []
        return [b if f.attr == "__init__" else f"{b}.{f.attr}" for b in self.class_bases[-1]]

    def visit_Call(self, node):
        for target in self._super_target(node):
            info = self.lookup(node.func, target) if node.func.attr != "__init__" else self.lookup_quiet(target)
            if info and info[0] in CALLABLE and info[1]:
                self.check_args(node, target, self.s.sig(info[1]))
        path = self.resolve(node.func)
        if path:
            info = self.lookup(node.func, path)
            if info and info[0] in CALLABLE and info[1]:
                self.check_args(node, path, self.s.sig(info[1]))
            for a in node.args + [k.value for k in node.keywords]:
                self.visit(a)
            if isinstance(node.func, ast.Attribute):
                self.generic_visit(node.func.value) if not isinstance(node.func.value, (ast.Name, ast.Attribute)) else None
            return
        self.generic_visit(node)

    def param_history(self, proj, path):
        """{param name: [versions where the callable accepted it]} across all analysed releases."""
        key = (proj, path)
        if key not in self._ph:
            iv = self.s.intervals(proj)
            h = iv[(iv.path == path) & iv.sig_hash.notna()]
            # the same definition re-exported under other paths (same leaf name, a shared signature)
            leaf = path.rpartition(".")[2]
            same = iv[iv.sig_hash.isin(set(h.sig_hash)) & iv.path.str.endswith("." + leaf)]
            h = iv[iv.path.isin(set(same.path) | {path}) & iv.sig_hash.notna()]
            vs = self.s.versions[proj]
            out = {}
            for r in h.itertuples():
                sig = self.s.sig(r.sig_hash)
                for p in (sig or {}).get("params", []):
                    if p[1] in ("PK", "KO", "PO"):
                        out.setdefault(p[0], []).extend(vs[int(r.first_index):int(r.last_index) + 1])
            self._ph[key] = out
        return self._ph[key]

    def check_args(self, node, path, sig):
        """Default: flag keyword arguments the callable accepted in another release but not in the pinned one
        (version drift, near-zero false alarms). --strict adds arity / required-argument checks."""
        if not sig:
            return
        params = sig["params"]
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            return  # *args / **kwargs at call site: can't judge
        proj, iv, _ = self.api(path)
        ver = iv[1] if iv else None
        has_va = any(p[1] == "VA" for p in params)
        has_vk = any(p[1] == "VK" for p in params)
        positional = [p for p in params if p[1] in ("PO", "PK")]
        names = {p[0]: p for p in params if p[1] in ("PK", "KO")}
        for k in node.keywords:
            if k.arg in names or has_vk:
                continue
            seen = self.param_history(proj, path).get(k.arg)
            if seen:
                when = f"accepted up to {seen[-1]}" if Version(seen[-1]) < Version(ver) else f"accepted from {seen[0]}"
                self.report(node, "bad_call", path, f"{path} has no parameter '{k.arg}' in {proj}=={ver} ({when})",
                            proj, ver)
            elif self.strict:
                self.report(node, "bad_call", path, f"{path} has no parameter '{k.arg}' in {proj}=={ver}", proj, ver)
        if not self.strict or sig.get("stub"):
            return
        root = dotted(node.func)
        unbound = root and root[0] not in self.var_types and self.lookup_quiet(path) and \
            self.lookup_quiet(path)[0] == "method"
        n_pos = len(node.args) - (1 if unbound else 0)
        if not has_va and n_pos > len(positional):
            self.report(node, "bad_call", path, f"{path} takes at most {len(positional)} positional "
                        f"argument(s) in {proj}=={ver}, got {n_pos}", proj, ver)
        bound = {p[0] for p in positional[:n_pos]} | {k.arg for k in node.keywords}
        missing = [p[0] for p in params if p[1] in ("PO", "PK", "KO") and not p[2] and p[0] not in bound]
        if missing:
            self.report(node, "bad_call", path, f"{path} is missing required argument(s) {missing} "
                        f"in {proj}=={ver}", proj, ver)


def check_source(src, pins, store=None, strict=False, nearest=False, coverage=None):
    """Check Python source text. pins: {distribution: version or None}. Returns a list of issue dicts.
    strict=True also flags names that were never part of the library's public API (likely hallucinated)."""
    store = store or Store()
    c = Checker(store, pins, strict, nearest)
    tree = ast.parse(src)
    # with `from __future__ import annotations` annotations are never evaluated at runtime
    c.lazy_annotations = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and
                             any(a.name == "annotations" for a in n.names) for n in tree.body)
    c.visit(tree)
    if coverage is not None:
        coverage.update(c.coverage)
    ignored = {n + 1 for n, line in enumerate(src.splitlines()) if "# apidrift: ignore" in line}
    seen, out = set(), []
    for i in c.issues:
        if i["line"] in ignored:
            continue
        key = (i["line"], i["kind"], i["symbol"], i["message"])
        if key not in seen:
            seen.add(key); out.append(i)
    return sorted(out, key=lambda i: (i["line"], i["col"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Python files or directories")
    ap.add_argument("--pin", action="append", help="package==version (repeatable)")
    ap.add_argument("-r", "--requirements", help="requirements.txt with == pins")
    ap.add_argument("--lock", help="uv.lock, poetry.lock, pylock.toml or Pipfile.lock (default: auto-detect in .)")
    ap.add_argument("--json", action="store_true", help="one JSON object per file")
    ap.add_argument("--sarif", metavar="OUT", help="also write a SARIF 2.1.0 report (GitHub code scanning)")
    ap.add_argument("--no-deprecations", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also flag names never seen in the library's public API (catches invented APIs; noisier)")
    ap.add_argument("--nearest", action="store_true",
                    help="if a pinned version was not analysed, check against the closest earlier analysed release")
    ap.add_argument("--data", default=DATA, help="dataset folder (default: $APIDRIFT_DATA or ../data)")
    a = ap.parse_args()
    pins = {}
    lock = a.lock or (None if (a.pin or a.requirements) else find_lock())
    if lock:
        pins.update(parse_lock(lock))
        print(f"note: {len(pins)} pins read from {lock}", file=sys.stderr)
    pins.update(parse_pins(a.pin, a.requirements))
    store = Store(a.data)
    total, coverage, all_issues = 0, {}, []
    for f in python_files(a.paths):
        try:
            issues = check_source(open(f, encoding="utf-8", errors="replace").read(), pins, store, a.strict,
                                  a.nearest, coverage)
        except SyntaxError as e:
            print(f"{f}: skipped (syntax error: {e.msg})", file=sys.stderr); continue
        if a.no_deprecations:
            issues = [i for i in issues if i["kind"] != "deprecated"]
        total += sum(i["kind"] != "deprecated" for i in issues)
        all_issues += [(f, i) for i in issues]
        if a.json:
            print(json.dumps({"file": f, "issues": issues}))
        else:
            for i in issues:
                print(f"{f}:{i['line']}:{i['col']}: [{i['kind']}] {i['message']}")
    if a.sarif:
        json.dump(sarif(all_issues), open(a.sarif, "w"), indent=1)
    for note in sorted(coverage.values()):
        print(f"note: {note}", file=sys.stderr)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
