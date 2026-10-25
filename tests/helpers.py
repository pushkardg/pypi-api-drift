"""Test helpers: build API snapshots and a small dataset from in-memory "wheels" ({member path: source}),
using the same steps as pipeline/run.py (module mapping, .py/.pyi merge, Snapshot, carry-forward of names that
are still accessible, diff). Keep in step with run.py."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))
sys.path.insert(0, os.path.join(HERE, ".."))

import pandas as pd
from modparse import parse_module, merge_stub
from api import Snapshot, diff, module_of

MEMBER_KINDS = ("method", "classmethod", "staticmethod", "property")


def snapshot(files):
    mods, ext, is_pkg = {}, set(), {}
    for name in files:
        mk = module_of(name)
        if not mk:
            continue
        dotted, kind, pkg = mk
        is_pkg[dotted] = is_pkg.get(dotted, False) or pkg
        if kind == "ext":
            ext.add(dotted)
            continue
        mods.setdefault(dotted, {})[kind] = name
    parsed, failed = {}, set()
    for dotted, m in mods.items():
        p = parse_module(files[m["py"]], False) if "py" in m else parse_module(files[m["pyi"]], True)
        if "py" in m and "pyi" in m:
            p = merge_stub(p, parse_module(files[m["pyi"]], True))
        if p["error"]:
            failed.add(dotted)
        else:
            parsed[dotted] = p
    return Snapshot(parsed, is_pkg, ext - set(parsed), opaque=failed)


def symbols(files, sigs=None):
    return snapshot(files).symbols({} if sigs is None else sigs)


def transition(old_files, new_files):
    """Events between two releases, with run.py's carry-forward rule applied. Returns (events, sigs)."""
    sigs = {}
    A = symbols(old_files, sigs)
    snap = snapshot(new_files)
    B = snap.symbols(sigs)
    hidden = []
    for path, st in A.items():
        if path in B:
            continue
        acc = snap.accessible(path)
        if acc is None and snap.last_reason == "class_getattr" and st[0] in MEMBER_KINDS:
            acc = False
        if acc is not False:
            B[path] = st
            hidden.append(path)
    ev = diff(A, B, sigs)
    for p in hidden:
        ev.append({"path": p, "change_type": "left_public_api", "breaking": False})
    return ev, sigs


def breaking(old_files, new_files, confidence=None):
    ev, _ = transition(old_files, new_files)
    return {(e["path"], e["change_type"], e.get("param", "")) for e in ev
            if e["breaking"] and not e.get("parent_removed") and (confidence is None or e["confidence"] == confidence)}


def changes(old_files, new_files):
    ev, _ = transition(old_files, new_files)
    return {(e["path"], e["change_type"], e.get("param", "")): e for e in ev}


def build_dataset(outdir, projects):
    """projects: {project: [(version, files), ...]} in release order -> writes the four tables the checker reads."""
    os.makedirs(outdir, exist_ok=True)
    rels, ivs, tops, allsigs = [], [], [], {}
    for proj, versions in projects.items():
        open_iv = {}
        for i, (ver, files) in enumerate(versions):
            rels.append({"project": proj, "version": ver, "version_index": i, "status": "ok"})
            syms = symbols(files, allsigs)
            for path, st in syms.items():
                key = st[:3]
                cur = open_iv.get(path)
                if cur is None or cur[0] != key:
                    if cur is not None:
                        ivs.append((proj, path, *cur[0], cur[1], cur[2], versions[i - 1][0], i - 1))
                    open_iv[path] = [key, ver, i]
            for path in list(open_iv):
                if path not in syms:
                    cur = open_iv.pop(path)
                    ivs.append((proj, path, *cur[0], cur[1], cur[2], versions[i - 1][0], i - 1))
            for name in files:
                mk = module_of(name)
                if mk and "." not in mk[0]:
                    tops.append((proj, mk[0]))
        for path, cur in open_iv.items():
            ivs.append((proj, path, *cur[0], cur[1], cur[2], versions[-1][0], len(versions) - 1))
    pd.DataFrame(rels).to_parquet(f"{outdir}/releases.parquet", index=False)
    pd.DataFrame(ivs, columns=["project", "path", "kind", "sig_hash", "deprecated", "first_version", "first_index",
                               "last_version", "last_index"]).to_parquet(f"{outdir}/api_history.parquet", index=False)
    pd.DataFrame({"sig_hash": list(allsigs), "sig": [json.dumps(s, separators=(",", ":")) for s in allsigs.values()]}
                 ).to_parquet(f"{outdir}/signatures.parquet", index=False)
    pd.DataFrame(sorted(set(tops)), columns=["project", "module"]).to_parquet(f"{outdir}/top_level_modules.parquet",
                                                                              index=False)
    return outdir
