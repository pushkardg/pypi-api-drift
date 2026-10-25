"""Post-build corrections and the coverage layer (v1.1 of the release tables).

Usage: python patch_release.py DATA_DIR COVERAGE_DIR OUT_DIR

1. changes: renaming a positional-only parameter is not breaking (callers cannot pass it by name).
2. api_history: an interval never extends over releases that were not analysed (last_index clamped to the
   index of last_version; affected projects had trailing releases skipped for size).
3. top_level_modules: every module seen in ANY analysed release maps to its distribution (previously only the
   latest release, so modules that were later removed, e.g. pkg_resources in setuptools 82, were unmapped).
4. Classes whose constructor is generated or replaced by a class decorator (dataclass, attrs, factories): their
   constructor signature was inherited from a base class. Their intervals lose the signature and constructor
   signature events on them become medium confidence.
5. module_coverage.parquet (new) and api_history.coverage_status (new): how much of each module the static
   extraction can see, so that absence from the dataset is not read as proof that a name does not exist.
     static             module parsed from Python source, nothing dynamic
     stub_only          only a .pyi stub ships for the module (compiled code underneath)
     dynamic_module     module-level __getattr__ that is not closed, globals() injection, or runtime __all__
     compiled_boundary  the module is compiled, or binds names from a compiled module of the same wheel
   For a row spanning several releases the most uncertain status over its range is used, in the order above
   from last to first (stub_only > dynamic_module > compiled_boundary > static).
"""
import glob, json, os, sys
import duckdb, pandas as pd, pyarrow as pa, pyarrow.parquet as pq

D, COV, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET memory_limit='2500MB'")
con.execute("SET preserve_insertion_order=false")
os.makedirs(f"{OUT}/.tmp", exist_ok=True)
con.execute(f"SET temp_directory='{OUT}/.tmp'")
log = {}

# ---- module coverage table
parts = sorted(glob.glob(f"{COV}/proj/*.parquet"))
mc = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
rel = pd.read_parquet(f"{D}/releases.parquet", columns=["project", "version", "version_index", "status"])
vidx = dict(zip(zip(rel.project, rel.version), rel.version_index))
mc["last_index"] = [vidx.get((p, v), li) for p, v, li in zip(mc.project, mc.last_version, mc.last_index)]
mc = mc.sort_values(["project", "module", "first_index"]).reset_index(drop=True)
mc.to_parquet(f"{OUT}/module_coverage.parquet", index=False, compression="zstd")
log["module_coverage_rows"] = len(mc)
log["module_coverage_projects"] = int(mc.project.nunique())
print("module_coverage", len(mc), flush=True)

RANK = {"static": 0, "compiled_boundary": 1, "dynamic_module": 2, "stub_only": 3}
NAME = {v: k for k, v in RANK.items()}


def mstatus(src, dyn, ic):
    if src == "pyi":
        return 3
    if dyn:
        return 2
    if src == "compiled" or ic:
        return 1
    return 0


# generated-constructor classes: (project, defining class path) -> list of (first, last)
gen = {}
for r in mc[mc.generated_init_classes.fillna("") != ""].itertuples():
    for c in r.generated_init_classes.split(","):
        gen.setdefault((r.project, f"{r.module}.{c}"), []).append((r.first_index, r.last_index))
log["generated_init_class_paths"] = len(gen)
gen_by_proj = {}
for (gp, cpath), rng in gen.items():
    gen_by_proj.setdefault(gp, {})[cpath] = rng

# ---- api_history: clamp, coverage_status, generated constructors
ah = pq.ParquetFile(f"{D}/api_history.parquet")
writer = None
cov_by_proj = {p: g for p, g in mc.groupby("project")}
gen_hashes = {}  # project -> set of sig hashes wrongly inherited by generated-constructor classes
stats = {"clamped": 0, "sig_dropped": 0, **{f"status_{k}": 0 for k in RANK}}
projects_done = set()
buf = []


def process(df):
    p = df.project.iloc[0]
    li = df.last_version.map(lambda v: vidx.get((p, v)))
    clamp = li.notna() & (li != df.last_index)
    stats["clamped"] += int(clamp.sum())
    if clamp.any():
        df["last_index"] = li.where(clamp, df.last_index).astype(df.last_index.dtype)
    cov = cov_by_proj.get(p)
    status = [0] * len(df)
    if cov is not None:
        ivs = {}
        for r in cov.itertuples():
            ivs.setdefault(r.module, []).append((r.first_index, r.last_index,
                                                 mstatus(r.source, r.dynamic, r.imports_compiled)))
        mods = set(ivs)
        cache = {}
        for k, (path, a, b) in enumerate(zip(df.path, df.first_index, df.last_index)):
            m = cache.get(path)
            if m is None:
                s = path.split(".")
                m = next((".".join(s[:j]) for j in range(len(s), 0, -1) if ".".join(s[:j]) in mods), "")
                cache[path] = m
            if m:
                status[k] = max((st for f, l, st in ivs[m] if f <= b and l >= a), default=0)
    df["coverage_status"] = [NAME[s] for s in status]
    for s in RANK:
        stats[f"status_{s}"] += sum(1 for x in df.coverage_status if x == s)
    # generated constructors: drop the inherited signature on the defining path and same-named re-exports
    hs = set()
    for cpath, rng in gen_by_proj.get(p, {}).items():
        rows = df[(df.path == cpath) & df.kind.isin(["class", "class_open"]) & df.sig_hash.notna()]
        for r in rows.itertuples():
            if any(f <= r.last_index and l >= r.first_index for f, l in rng):
                hs.add(r.sig_hash)
    if hs:
        leaves = {c.rpartition(".")[2] for c in gen_by_proj.get(p, {})}
        hit = df.kind.isin(["class", "class_open"]) & df.sig_hash.isin(hs) & \
            df.path.str.rpartition(".")[2].isin(leaves)
        stats["sig_dropped"] += int(hit.sum())
        df.loc[hit, "sig_hash"] = None
        gen_hashes[p] = hs
        df = merge_adjacent(df)
    return df


def merge_adjacent(df):
    """Merge back-to-back intervals of one path whose state became identical (e.g. two constructor signatures
    that were both dropped); the merged row keeps the most uncertain coverage_status."""
    cand = set(df.path[df.sig_hash.isna() & df.kind.isin(["class", "class_open"])])
    rest = df[~df.path.isin(cand)]
    df = df[df.path.isin(cand)]
    if df.empty:
        return rest
    df = df.sort_values(["path", "first_index"]).reset_index(drop=True)
    key = df[["path", "kind", "deprecated"]].astype(str).agg("|".join, axis=1) + "|" + df.sig_hash.fillna("")
    new_run = (key != key.shift()) | (df.first_index != df.last_index.shift() + 1)
    run = new_run.cumsum()
    if run.iloc[-1] == len(df):
        return pd.concat([rest, df], ignore_index=True)
    rank = df.coverage_status.map(RANK)
    g = df.assign(_r=rank, _run=run).groupby("_run", sort=False)
    out = g.first()
    out["last_index"] = g.last_index.last()
    out["last_version"] = g.last_version.last()
    out["coverage_status"] = g._r.max().map(NAME)
    stats["merged"] = stats.get("merged", 0) + int(len(df) - len(out))
    return pd.concat([rest, out.drop(columns="_r").reset_index(drop=True)], ignore_index=True)


cols = None
for i in range(ah.num_row_groups):
    t = ah.read_row_group(i).to_pandas()
    buf.append(t)
    # row groups are written in project order; process whole projects only
    cat = pd.concat(buf, ignore_index=True)
    last_p = cat.project.iloc[-1]
    done = cat[cat.project != last_p]
    buf = [cat[cat.project == last_p]]
    for p, g in done.groupby("project", sort=False):
        assert p not in projects_done, f"project {p} split across non-adjacent row groups"
        projects_done.add(p)
        out = process(g.copy())
        tbl = pa.Table.from_pandas(out, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(f"{OUT}/api_history.parquet", tbl.schema, compression="zstd")
        writer.write_table(tbl.cast(writer.schema), row_group_size=200_000)
    if i % 200 == 0:
        print("api_history row group", i, "/", ah.num_row_groups, flush=True)
rest = pd.concat(buf, ignore_index=True)
for p, g in rest.groupby("project", sort=False):
    out = process(g.copy())
    writer.write_table(pa.Table.from_pandas(out, preserve_index=False).cast(writer.schema), row_group_size=200_000)
writer.close()
log.update(stats)
print("api_history", stats, flush=True)

# ---- changes: positional-only renames, generated constructors
gh = pd.DataFrame([(p, h) for p, hs in gen_hashes.items() for h in hs], columns=["project", "h"])
con.register("gh", gh)
gleaf = pd.DataFrame(sorted({(p, c.rpartition(".")[2]) for (p, c) in gen}), columns=["project", "leaf"])
con.register("gleaf", gleaf)
SIG = ("param_removed", "param_renamed", "required_param_added", "became_keyword_only", "became_positional_only",
       "default_removed", "vararg_removed", "varkw_removed", "positional_reordered")
sig_list = ",".join(f"'{x}'" for x in SIG)
con.execute(f"""create temp table po as
  select c.project, c.to_version, c.path, c.param from '{D}/changes.parquet' c
  join '{D}/signatures.parquet' s on s.sig_hash = c.old_sig
  where c.change_type = 'param_renamed' and c.breaking and exists (
    select 1 from (select unnest(cast(json_extract(s.sig, '$.params') as json[])) p)
    where json_extract_string(p, '$[0]') = c.param and json_extract_string(p, '$[1]') = 'PO')""")
log["positional_only_renames_unflagged"] = con.execute("select count(*) from po").fetchone()[0]
con.execute(f"""copy (
  select c.* replace (
      case when po.path is not null then false else c.breaking end as breaking,
      case when g.leaf is not null and c.change_type in ({sig_list})
                and (c.old_sig in (select h from gh where gh.project = c.project)
                     or c.new_sig in (select h from gh where gh.project = c.project))
           then 'medium' else c.confidence end as confidence)
  from '{D}/changes.parquet' c
  left join po on po.project = c.project and po.to_version = c.to_version and po.path = c.path and po.param = c.param
  left join gleaf g on g.project = c.project and g.leaf = split_part(c.path, '.', -1)
     and c.symbol_kind in ('class', 'class_open')
  order by c.project, c.to_version_index, c.path, c.change_type
) to '{OUT}/changes.parquet' (format parquet, compression zstd, row_group_size 500000)""")
log["generated_constructor_events_medium"] = con.execute(f"""
  select count(*) from '{D}/changes.parquet' c join gleaf g on g.project = c.project
     and g.leaf = split_part(c.path, '.', -1) and c.symbol_kind in ('class', 'class_open')
  where c.breaking and c.confidence = 'high' and c.change_type in ({sig_list})
    and (c.old_sig in (select h from gh where gh.project = c.project)
         or c.new_sig in (select h from gh where gh.project = c.project))""").fetchone()[0]
print("changes written", flush=True)

# ---- releases: counters follow the corrected breaking flags
con.execute(f"""copy (
  with c as (select project, to_version, sum(breaking::int) nb, count(distinct case when breaking then path end) ns
             from '{OUT}/changes.parquet' group by 1, 2)
  select r.* replace (
      case when r.status = 'ok' and r.prev_version is not null then coalesce(c.nb, 0) else r.n_breaking_events end
        as n_breaking_events,
      case when r.status = 'ok' and r.prev_version is not null then coalesce(c.ns, 0) else r.n_breaking_symbols end
        as n_breaking_symbols)
  from '{D}/releases.parquet' r left join c on c.project = r.project and c.to_version = r.version
  order by r.project, r.version_index
) to '{OUT}/releases.parquet' (format parquet, compression zstd)""")

# ---- top-level modules: every module seen in any analysed release
con.execute(f"""copy (select distinct project, path as module from '{OUT}/api_history.parquet'
  where kind in ('module', 'extension_module') order by 1, 2) to '{OUT}/top_level_modules.parquet' (format parquet)""")
log["top_level_modules_rows"] = con.execute(f"select count(*) from '{OUT}/top_level_modules.parquet'").fetchone()[0]

# signatures unchanged
if not os.path.exists(f"{OUT}/signatures.parquet"):
    import shutil
    shutil.copy(f"{D}/signatures.parquet", f"{OUT}/signatures.parquet")
json.dump(log, open(f"{OUT}/../patch_log.json", "w"), indent=1, default=str)
print(json.dumps(log, indent=1, default=str))
