"""Remove placeholder releases (an all-zero version such as 0.0.0, 0 or 0.0 uploaded AFTER the project's next
release) from a built dataset. Version order puts them first, so the next release was diffed against an unrelated
build, producing bogus changes. Future builds exclude them at selection time (select_releases.py).

For each affected project: the placeholder's release row is dropped and later version indexes shift down by one;
the first remaining release becomes the project's first (no previous version, no change counts); the changes into it
are dropped; api_history / module_coverage intervals that only covered the placeholder are dropped and intervals that
started at it now start at the first remaining release; benchmark pairs
that trace to a dropped change are dropped.
Usage: python drop_placeholders.py DATA_IN DATA_OUT BENCH_IN BENCH_OUT LOG_JSON"""
import json, os, sys
import pandas as pd
import pyarrow as pa, pyarrow.parquet as pq
from packaging.version import Version

DIN, DOUT, BIN, BOUT, LOG = sys.argv[1:6]
os.makedirs(DOUT, exist_ok=True); os.makedirs(BOUT, exist_ok=True)


def is_placeholder(v):
    try:
        r = Version(v).release
    except Exception:
        return False
    return bool(r) and all(x == 0 for x in r)


rel = pd.read_parquet(f"{DIN}/releases.parquet")
rel["_t"] = pd.to_datetime(rel.upload_time)
aff = {}  # project -> (placeholder version, first remaining version)
for p, g in rel[rel.status == "ok"].sort_values("version_index").groupby("project"):
    g = g.reset_index(drop=True)
    if len(g) >= 2 and g.version_index[0] == 0 and is_placeholder(g.version[0]) and g._t[0] > g._t[1]:
        aff[p] = (g.version[0], g.version[1])
print("affected:", aff)
log = {"affected": {p: {"placeholder": a, "next": b} for p, (a, b) in aff.items()}}


def shift(df, cols_idx, cols_ver=None):
    m = df.project.isin(aff)
    for c in cols_idx:
        df.loc[m, c] = df.loc[m, c] - 1
    return df


# releases
drop = rel.project.isin(aff) & rel.apply(lambda r: r.project in aff and r.version == aff[r.project][0], axis=1)
log["releases_dropped"] = int(drop.sum())
rel = rel[~drop].copy()
rel = shift(rel, ["version_index"])
first = rel.project.isin(aff) & (rel.version_index == 0)
for c in ("prev_version", "bump", "likely_calver"):
    rel.loc[first, c] = None
for c in ("n_added", "n_removed", "n_breaking_events", "n_breaking_symbols"):
    rel.loc[first, c] = float("nan")
rel = rel.drop(columns="_t")

# changes (big: process by row group, keep schema)
src = pq.ParquetFile(f"{DIN}/changes.parquet")
w, dropped_changes, dropped_breaking = None, 0, 0
for i in range(src.num_row_groups):
    t = src.read_row_group(i).to_pandas()
    m = t.project.isin(aff)
    if m.any():
        bad = m & t.apply(lambda r: r.project in aff and r.from_version == aff[r.project][0], axis=1)
        dropped_changes += int(bad.sum()); dropped_breaking += int((bad & t.breaking & t.representative).sum())
        t = t[~bad].copy()
        mm = t.project.isin(aff)
        t.loc[mm, "to_version_index"] = t.loc[mm, "to_version_index"] - 1
    tb = pa.Table.from_pandas(t, schema=src.schema_arrow, preserve_index=False)
    if w is None:
        w = pq.ParquetWriter(f"{DOUT}/changes.parquet", src.schema_arrow, compression="zstd")
    w.write_table(tb, row_group_size=len(t) or 1)
w.close()
log["changes_dropped"] = dropped_changes
log["breaking_representative_dropped"] = dropped_breaking


def fix_intervals(d):
    m = d.project.isin(aff)
    only = m & (d.last_index == 0)
    d = d[~only].copy()
    m = d.project.isin(aff)
    start = m & (d.first_index == 0)
    d.loc[start, "first_index"] = 1
    d.loc[start, "first_version"] = d.loc[start, "project"].map(lambda p: aff[p][1])
    d.loc[m, "first_index"] -= 1
    d.loc[m, "last_index"] -= 1
    return d, int(only.sum()), int(start.sum())


# api_history: rewrite group by group so the per-project row groups the checker prunes on are kept
src = pq.ParquetFile(f"{DIN}/api_history.parquet")
schema = src.schema_arrow.remove_metadata()
w = pq.ParquetWriter(f"{DOUT}/api_history.parquet", schema, compression="zstd")
n_only = n_start = 0
used_sigs = set()
for i in range(src.num_row_groups):
    t = src.read_row_group(i)
    if any(p in aff for p in set(t.column("project").to_pylist())):
        d, a, b = fix_intervals(t.to_pandas()); n_only += a; n_start += b
        t = pa.Table.from_pandas(d, schema=schema, preserve_index=False)
    else:
        t = t.cast(schema)
    used_sigs.update(x for x in t.column("sig_hash").to_pylist() if x is not None)
    w.write_table(t, row_group_size=max(1, t.num_rows))
w.close()
log["api_history"] = {"dropped_placeholder_only": n_only, "restarted": n_start}
mc, a, b = fix_intervals(pd.read_parquet(f"{DIN}/module_coverage.parquet"))
log["module_coverage"] = {"dropped_placeholder_only": a, "restarted": b}

# top-level modules: recompute affected projects from module_coverage
tl = pd.read_parquet(f"{DIN}/top_level_modules.parquet")
keep = tl[~tl.project.isin(aff)]
old_aff = tl[tl.project.isin(aff)]
mods = mc[mc.project.isin(aff)][["project", "module"]].drop_duplicates()
new_aff = old_aff[old_aff.apply(lambda r: ((mods.project == r.project) & ((mods.module == r.module) |
                                 mods.module.str.startswith(r.module + "."))).any(), axis=1)]
log["top_level_modules_dropped"] = int(len(old_aff) - len(new_aff))
tl = pd.concat([keep, new_aff])

# signatures: unchanged (an unreferenced signature is harmless; the table is copied byte for byte)
import shutil
shutil.copyfile(f"{DIN}/signatures.parquet", f"{DOUT}/signatures.parquet")


def write_like(df, name, sort=None, rg=None):
    schema = pq.read_schema(f"{DIN}/{name}.parquet").remove_metadata()
    if sort:
        df = df.sort_values(sort)
    t = pa.Table.from_pandas(df[[f.name for f in schema]], schema=schema, preserve_index=False)
    pq.write_table(t, f"{DOUT}/{name}.parquet", compression="zstd", row_group_size=rg or 1_000_000)


# keep the physical layouts the checker's row-group pruning relies on
write_like(rel, "releases", ["project", "version_index"])
write_like(mc, "module_coverage", ["project", "module", "first_index"])
write_like(tl, "top_level_modules", ["project", "module"])

# benchmark
ch = pd.read_parquet(f"{DOUT}/changes.parquet", filters=[("project", "in", list(aff)), ("breaking", "=", True)],
                     columns=["project", "from_version", "to_version", "path", "change_type", "param"])
ok = set(zip(ch.project, ch.from_version, ch.to_version, ch.path, ch.change_type, ch.param.fillna("")))
log["benchmark_pairs_dropped"] = {}
for f in ("versionprobe", "versionprobe_lite", "versionprobe_verified"):
    b = pd.read_parquet(f"{BIN}/{f}.parquet")
    bad = b.project.isin(aff) & ~b.apply(lambda r: (r.project, r.version_before, r.version_after, r.symbol,
                                                    r.change_type, "" if pd.isna(r.param) else r.param) in ok, axis=1)
    bad_pairs = set(b[bad].pair_id)
    b = b[~b.pair_id.isin(bad_pairs)]
    log["benchmark_pairs_dropped"][f] = sorted(bad_pairs)
    b.to_parquet(f"{BOUT}/{f}.parquet", index=False)
    if f == "versionprobe":
        b.to_json(f"{BOUT}/versionprobe.jsonl", orient="records", lines=True, force_ascii=False)
json.dump(log, open(LOG, "w"), indent=1)
print(json.dumps({k: (v if k != "benchmark_pairs_dropped" else {a: len(x) for a, x in v.items()}) for k, v in log.items()}, indent=1))
