"""Removals that follow a period in which the name was carried forward as statically undecidable (a
`left_public_api` event "no longer visible statically") are recorded at the release where the removal first became
provable, which can be later than the release where the name really disappeared (numpy.float: gone in 1.24.0,
provably rejected from 1.24.2). Such events become medium confidence, with the earliest possible release in the detail,
and VersionProbe pairs built on them (whose "valid" label for the earlier release is not established) are dropped.
Usage: python date_hidden_removals.py RELEASE_DIR LOG_JSON   (rewrites RELEASE_DIR/data/changes.parquet and the
benchmark files in place)"""
import json, os, sys
import duckdb, pandas as pd
import pyarrow as pa, pyarrow.parquet as pq

R, LOG = sys.argv[1:3]
D, B = f"{R}/data", f"{R}/benchmark"
con = duckdb.connect(); con.execute("SET memory_limit='3GB'")
rel = pd.read_parquet(f"{D}/releases.parquet", columns=["project", "version", "version_index"])
vers = {(p, int(i)): v for p, v, i in zip(rel.project, rel.version, rel.version_index)}
late = con.execute(f"""
with c as (select project, path, change_type, to_version_index, from_version, to_version, detail
           from '{D}/changes.parquet'),
l as (select project, path, max(to_version_index) li from c
      where change_type = 'left_public_api' and detail like 'no longer visible statically%' group by 1, 2),
r as (select c.project, c.path, c.from_version, c.to_version, c.to_version_index, l.li from c join l using (project, path)
      where c.change_type = 'removed' and c.to_version_index > l.li),
a as (select project, path, to_version_index ai from c where change_type = 'added')
select * from r where not exists (select 1 from a where a.project = r.project and a.path = r.path
                                  and a.ai > r.li and a.ai <= r.to_version_index)""").df()
key = {(r.project, r.path, int(r.to_version_index)): int(r.li) for r in late.itertuples()}
print(len(key), "late-dated removals")

src = pq.ParquetFile(f"{D}/changes.parquet")
tmp = f"{D}/changes.parquet.tmp"
w = pq.ParquetWriter(tmp, src.schema_arrow, compression="zstd")
changed = changed_high_rep = 0
for i in range(src.num_row_groups):
    t = src.read_row_group(i).to_pandas()
    m = t.change_type.eq("removed") & t.project.isin({k[0] for k in key})
    if m.any():
        for j in t.index[m]:
            k = (t.at[j, "project"], t.at[j, "path"], int(t.at[j, "to_version_index"]))
            if k in key:
                changed += 1
                changed_high_rep += int(t.at[j, "confidence"] == "high" and bool(t.at[j, "representative"])
                                        and not bool(t.at[j, "parent_removed"]))
                since = vers.get((k[0], key[k]), "?")
                t.at[j, "confidence"] = "medium"
                t.at[j, "detail"] = (f"removal provable from {t.at[j, 'to_version']}; the name was not statically "
                                     f"visible from {since}, so it may already have been unavailable then")
    w.write_table(pa.Table.from_pandas(t, schema=src.schema_arrow, preserve_index=False), row_group_size=len(t) or 1)
w.close()
os.replace(tmp, f"{D}/changes.parquet")

late_set = {(r.project, r.path, r.from_version, r.to_version) for r in late.itertuples()}
dropped = {}
for f in ("versionprobe", "versionprobe_lite", "versionprobe_verified"):
    b = pd.read_parquet(f"{B}/{f}.parquet")
    bad = b[b.apply(lambda r: r.change_type == "removed" and
                    (r.project, r.symbol, r.version_before, r.version_after) in late_set, axis=1)].pair_id
    dropped[f] = sorted(set(bad))
    b = b[~b.pair_id.isin(set(bad))]
    b.to_parquet(f"{B}/{f}.parquet", index=False)
    if f == "versionprobe":
        b.to_json(f"{B}/versionprobe.jsonl", orient="records", lines=True, force_ascii=False)
log = {"late_dated_removals": changed, "of_which_high_representative": changed_high_rep,
       "benchmark_pairs_dropped": dropped}
json.dump(log, open(LOG, "w"), indent=1)
print(json.dumps({k: (v if not isinstance(v, dict) else {a: len(x) for a, x in v.items()}) for k, v in log.items()}))
