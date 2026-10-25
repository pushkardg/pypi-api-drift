"""apidrift-lookup: one-line queries against the PyPI API Drift dataset, offline.

  apidrift-lookup versions pandas
  apidrift-lookup history pandas DataFrame.append            # when it existed, with which signatures
  apidrift-lookup diff pandas 1.5.3 2.2.0                     # what breaks when upgrading (and what was added)
  apidrift-lookup diff pandas 1.5.3 2.2.0 --all --json

Paths may be given with or without the top-level module (``DataFrame.append`` or ``pandas.DataFrame.append``).
``diff`` compares the two releases' public-API snapshots directly, so it is correct for any two analysed releases,
not only adjacent ones. When changes.parquet is present, each finding also carries the dataset's confidence label.
Absence from the dataset is strong evidence only where coverage_status is "static" (see the dataset card).
"""
import argparse, json, os, sys
import pyarrow.parquet as pq

from .check import DATA, Store

COVERAGE_NOTE = {
    "static": None,
    "dynamic_module": "the module can serve names at runtime that static analysis cannot see",
    "compiled_boundary": "the module is compiled or binds names from compiled code",
    "stub_only": "only a type stub ships for this module; the runtime may differ",
}
ABSENCE_NOTE = ("Not finding a name is weaker evidence than a recorded change: names created at runtime "
                "(module __getattr__, compiled extensions) can be missing from the dataset.")

BREAKING_KINDS = ("removed", "param_removed", "param_renamed", "required_param_added", "became_keyword_only",
                  "became_positional_only", "default_removed", "vararg_removed", "varkw_removed",
                  "positional_reordered", "kind_changed")


class Lookup:
    def __init__(self, data=DATA, store=None):
        self.s = store or Store(data)
        self.data = self.s.data

    # ------------------------------------------------------------------ helpers
    def project(self, name):
        """Accept a distribution name in any spelling (Pandas, scikit_learn) or a top-level module (sklearn)."""
        norm = name.strip().lower().replace("_", "-").replace(".", "-")
        for p in self.s.versions:
            if p.lower() == norm:
                return p
        cands = self.s.top_to_projects.get(name.strip(), [])
        if len(cands) == 1:
            return cands[0]
        if cands:
            raise LookupError(f"{name!r} is provided by several projects: {', '.join(sorted(cands))}")
        raise LookupError(f"{name!r} is not in the dataset")

    def _index(self, project, version):
        i, v = self.s.version_index(project, version)
        if i is None:
            raise LookupError(f"{project}=={version} was not analysed; analysed releases: "
                              f"{', '.join(self.analysed(project)[-8:])} ...")
        return i, v

    def analysed(self, project):
        return [v for v in self.s.versions[project] if (project, v) not in self.s.unanalysed]

    def _paths(self, project, path):
        """Candidate full paths for a user-supplied path."""
        iv = self.s.intervals(project)
        known = set(iv.path)
        if path in known:
            return [path]
        tops = sorted({p.split(".")[0] for p in known})
        out = [f"{t}.{path}" for t in tops if f"{t}.{path}" in known]
        if not out:  # last resort: a unique suffix match (e.g. "read_csv" -> "pandas.io.parsers.read_csv")
            suf = [p for p in known if p.endswith("." + path)]
            out = sorted(suf, key=len)[:5]
        return out

    @staticmethod
    def fmt_sig(sig):
        if not sig:
            return None
        parts, kw_marker, pos_marker = [], False, False
        for name, kind, has_default, default, _ in sig.get("params", []):
            if kind == "KO" and not kw_marker:
                parts.append("*"); kw_marker = True
            if kind == "VP":
                parts.append("*" + name); kw_marker = True; continue
            if kind == "VK":
                parts.append("**" + name); continue
            parts.append(f"{name}={default}" if has_default else name)
            if kind == "PO":
                pos_marker = True
            elif pos_marker:
                parts.insert(len(parts) - 1, "/"); pos_marker = False
        if pos_marker:
            parts.append("/")
        return "(" + ", ".join(parts) + ")"

    # ------------------------------------------------------------------ queries
    def history(self, project, path):
        """Every interval during which `path` existed: versions, kind, signature, deprecation marker."""
        project = self.project(project)
        iv = self.s.intervals(project)
        vs = self.s.versions[project]
        out = []
        for full in self._paths(project, path):
            h = iv[iv.path == full].sort_values("first_index")
            for r in h.itertuples():
                sh = r.sig_hash if isinstance(r.sig_hash, str) else None
                cov = getattr(r, "coverage_status", None)
                out.append({"path": full, "kind": r.kind, "first_version": vs[int(r.first_index)],
                            "last_version": vs[int(r.last_index)],
                            "still_present_in_latest": int(r.last_index) == len(vs) - 1,
                            "signature": self.fmt_sig(self.s.sig(sh)), "deprecated": bool(r.deprecated),
                            "coverage_status": cov, "coverage_note": COVERAGE_NOTE.get(cov)})
        res = {"project": project, "query": path, "analysed_releases": len(self.analysed(project)),
               "first_analysed": self.analysed(project)[0], "latest_analysed": self.analysed(project)[-1],
               "intervals": out}
        if not out:
            res["note"] = ABSENCE_NOTE
        elif any(i["coverage_note"] for i in out):
            res["note"] = "Some intervals come from modules static analysis cannot fully see (coverage_note)."
        return res

    def _coverage_at(self, project, idx):
        """{path: coverage_status} of the API at release index idx (empty when the column is absent)."""
        iv = self.s.intervals(project)
        if "coverage_status" not in iv.columns:
            return {}
        live = iv[(iv.first_index <= idx) & (iv.last_index >= idx)]
        return dict(zip(live.path, live.coverage_status))

    def _confidence(self, project, a, b):
        """{(path, change_type, param): confidence} for breaking events between releases a < b, if changes exist."""
        f = f"{self.data}/changes.parquet"
        if not os.path.exists(f):
            return None
        t = pq.read_table(f, columns=["path", "change_type", "param", "confidence", "to_version_index",
                                      "representative"],
                          filters=[("project", "=", project), ("breaking", "=", True),
                                   ("to_version_index", ">", a), ("to_version_index", "<=", b)]).to_pandas()
        out = {}
        for r in t.itertuples():
            k = (r.path, r.change_type, r.param if isinstance(r.param, str) and r.param else None)
            cur = out.get(k)
            out[k] = (r.confidence if cur is None or cur[0] != "high" else cur[0],
                      bool(r.representative) or (cur is not None and cur[1]))
        return out

    def diff(self, project, old, new, include_added=False, limit=None, every_path=False):
        """Breaking differences between two analysed releases, from their API snapshots. By default each change is
        listed once (the dataset's representative path; subclass and re-export copies are folded) when
        changes.parquet is available; every_path=True lists every affected path."""
        project = self.project(project)
        a, va = self._index(project, old)
        b, vb = self._index(project, new)
        if a >= b:
            raise LookupError(f"{va} is not older than {vb} in the dataset's version order")
        A, B = self.s.api_at(project, a), self.s.api_at(project, b)
        conf = self._confidence(project, a, b)
        cov_a = self._coverage_at(project, a)
        removed_set = set(A) - set(B)
        leaves = {}
        for p in B:
            leaves.setdefault(p.rpartition(".")[2], []).append(p)
        findings = []

        def label(path, ctype, param=None):
            """(confidence, representative) of the matching recorded event, or (None, None)."""
            if conf is None:
                return None, None
            return conf.get((path, ctype, param)) or conf.get((path, ctype, None)) or (None, False)

        for p in sorted(removed_set):
            parent = p.rpartition(".")[0]
            if parent in removed_set:  # reported through its removed parent
                continue
            leaf = p.rpartition(".")[2]
            tail2 = ".".join(p.split(".")[-2:])
            kind = A[p][0]
            # a new path ending in the same Class.member, or a same-kind module-level name; a hint, not a mapping
            cands = sorted((q for q in leaves.get(leaf, []) if q not in A and B[q][0] == kind and
                            (q.endswith("." + tail2) or kind in ("function", "class", "class_open"))), key=len)[:3]
            c, rep = label(p, "removed")
            cov = cov_a.get(p)
            findings.append({"path": p, "change": "removed", "kind": kind, "confidence": c, "representative": rep,
                             "same_name_in_new_release": cands, "coverage_status": cov,
                             "coverage_note": COVERAGE_NOTE.get(cov)})
        for p in sorted(set(A) & set(B)):
            ka, sa, _ = A[p]; kb, sb, _ = B[p]
            if sa == sb or not sa or not sb:
                continue
            old_s, new_s = self.s.sig(sa) or {}, self.s.sig(sb) or {}
            op = {x[0]: x for x in old_s.get("params", [])}
            np_ = {x[0]: x for x in new_s.get("params", [])}
            has_varkw = any(x[1] == "VK" for x in np_.values())
            problems = []
            for name, x in op.items():
                if x[1] in ("VP", "VK"):
                    if not any(y[1] == x[1] for y in np_.values()):
                        problems.append(("vararg_removed" if x[1] == "VP" else "varkw_removed", name))
                    continue
                y = np_.get(name)
                if y is None:
                    if not (has_varkw and x[1] != "PO"):
                        problems.append(("param_removed", name))
                elif x[1] == "PK" and y[1] == "KO":
                    problems.append(("became_keyword_only", name))
                elif x[1] == "PK" and y[1] == "PO":
                    problems.append(("became_positional_only", name))
                elif x[2] and not y[2]:
                    problems.append(("default_removed", name))
            for name, y in np_.items():
                if name not in op and y[1] in ("PO", "PK", "KO") and not y[2]:
                    problems.append(("required_param_added", name))
            for ctype, param in problems:
                c, rep = label(p, ctype, param)
                findings.append({"path": p, "change": ctype, "param": param, "kind": kb, "confidence": c,
                                 "representative": rep, "old_signature": self.fmt_sig(old_s),
                                 "new_signature": self.fmt_sig(new_s)})
        total_paths = len(findings)
        if conf is not None and not every_path:
            findings = [f for f in findings if f["representative"] is not False]
        added = sorted(p for p in set(B) - set(A) if p.rpartition(".")[0] not in set(B) - set(A))
        res = {"project": project, "from": va, "to": vb, "releases_between": b - a - 1,
               "breaking": findings[:limit] if limit else findings, "breaking_total": len(findings),
               "affected_paths_total": total_paths,
               "added_total": len(added)}
        if include_added:
            res["added"] = added[:limit] if limit else added
        n_cov = sum(1 for f in findings if f.get("coverage_note"))
        if n_cov:
            res["coverage_warning"] = (f"{n_cov} removal(s) are in modules static analysis cannot fully see. A removal "
                                       f"there is recorded only where it is provable (see confidence), but such "
                                       f"modules may serve names, old or new, that this diff does not list.")
        if conf is None:
            res["note"] = "changes.parquet not found: confidence labels unavailable"
        return res


def _print_history(h):
    print(f"{h['project']}: {h['analysed_releases']} analysed releases, {h['first_analysed']} .. {h['latest_analysed']}")
    if not h["intervals"]:
        print(f"  {h['query']}: not found in any analysed release")
    for r in h["intervals"]:
        rng = f"{r['first_version']} .. {r['last_version']}" + (" (latest)" if r["still_present_in_latest"] else "")
        dep = "  [deprecation marker]" if r["deprecated"] else ""
        cov = f"  [coverage: {r['coverage_status']}]" if r.get("coverage_note") else ""
        print(f"  {r['path']}  {r['kind']}  {rng}  {r['signature'] or ''}{dep}{cov}")
    if h.get("note"):
        print(f"note: {h['note']}")


def _print_diff(d):
    print(f"{d['project']} {d['from']} -> {d['to']} ({d['releases_between']} analysed releases in between): "
          f"{d['breaking_total']} breaking changes ({d['affected_paths_total']} affected paths incl. subclass and "
          f"re-export copies), {d['added_total']} added")
    for f in d["breaking"]:
        c = f" [{f['confidence']}]" if f.get("confidence") else ""
        if f["change"] == "removed":
            alt = (f"  (same name in {d['to']}: {', '.join(f['same_name_in_new_release'])})"
                   if f["same_name_in_new_release"] else "")
            cov = f" [coverage: {f['coverage_status']}]" if f.get("coverage_note") else ""
            print(f"  removed  {f['path']}{c}{cov}{alt}")
        else:
            print(f"  {f['change']}  {f['path']} ({f['param']}){c}\n      {f['old_signature']}\n   -> {f['new_signature']}")
    for p in d.get("added", []):
        print(f"  added  {p}")
    if d.get("coverage_warning"):
        print(f"warning: {d['coverage_warning']}")
    if d.get("note"):
        print(f"note: {d['note']}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="apidrift-lookup", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=DATA, help="dataset directory (default: $APIDRIFT_DATA)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("versions"); s.add_argument("project")
    s = sub.add_parser("history"); s.add_argument("project"); s.add_argument("path")
    s = sub.add_parser("diff"); s.add_argument("project"); s.add_argument("old"); s.add_argument("new")
    s.add_argument("--all", action="store_true", help="also list added names")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--every-path", action="store_true", help="list subclass and re-export copies too")
    a = ap.parse_args(argv)
    lk = Lookup(a.data)
    try:
        if a.cmd == "versions":
            p = lk.project(a.project)
            out = {"project": p, "analysed": lk.analysed(p),
                   "not_analysed": [v for v in lk.s.versions[p] if (p, v) in lk.s.unanalysed]}
            print(json.dumps(out, indent=1) if a.json else f"{p}: {', '.join(out['analysed'])}")
        elif a.cmd == "history":
            h = lk.history(a.project, a.path)
            print(json.dumps(h, indent=1)) if a.json else _print_history(h)
        else:
            d = lk.diff(a.project, a.old, a.new, include_added=a.all, limit=a.limit, every_path=a.every_path)
            print(json.dumps(d, indent=1)) if a.json else _print_diff(d)
    except LookupError as e:
        print(f"apidrift-lookup: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
