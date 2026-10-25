"""Headline numbers for the data card. Usage: python stats.py DATA_DIR"""
import json, sys
import duckdb, re
D = sys.argv[1]; S = {}
con = duckdb.connect(); con.execute("SET memory_limit='3GB'")
T = {"R": f"'{D}/releases.parquet'", "C": f"'{D}/changes.parquet'", "H": f"'{D}/api_history.parquet'"}
q = lambda sql: con.execute(re.sub(r"\b(from|join) ([RCH])\b", lambda m: f"{m.group(1)} {T[m.group(2)]} {m.group(2)}", sql)).fetchall()
S["projects"], S["releases"] = q("select count(distinct project), count(*) from R")[0]
S["release_status"] = dict(q("select status, count(*) from R group by 1"))
S["projects_with_ok_release"] = q("select count(distinct project) from R where status='ok'")[0][0]
S["upload_min"], S["upload_max"] = map(str, q("select min(upload_time), max(upload_time) from R")[0])
S["pure_python_share"] = round(q("select avg(pure_python_wheel::int) from R")[0][0], 3)
S["modules_parsed"], S["parse_fail_modules"], S["median_public_symbols"] = q(
    "select sum(n_modules), sum(n_modules_parse_failed), median(n_public_symbols) from R where status='ok'")[0]
S["release_transitions"], S["transitions_with_breaking"] = q(
    "select count(*), sum((n_breaking_symbols>0)::int) from R where status='ok' and prev_version is not null")[0]
for b in ["major", "minor", "patch"]:
    n, sh = q(f"select count(*), avg((n_breaking_symbols>0)::int) from R where status='ok' and bump='{b}' "
              "and not coalesce(likely_calver,false)")[0]
    S[f"{b}_transitions"] = n; S[f"{b}_with_breaking_share"] = round(sh, 3)
S["projects_with_breaking_in_patch"] = q("select count(distinct project) from R where bump='patch' and n_breaking_symbols>0 "
                                         "and not coalesce(likely_calver,false)")[0][0]
S["change_events"] = q("select count(*) from C")[0][0]
S["change_types"] = dict(q("select change_type, count(*) from C group by 1 order by 2 desc"))
S["representative_events"] = q("select sum(representative::int) from C")[0][0]
S["breaking_representative"] = q("select count(*) from C where breaking and not parent_removed and representative")[0][0]
S["breaking_representative_by_type"] = dict(q("select change_type, count(*) from C where breaking and not parent_removed and representative group by 1 order by 2 desc"))
S["breaking_representative_2025_plus"] = q("select count(*) from C join R on C.project=R.project and C.to_version=R.version "
    "where C.breaking and not C.parent_removed and C.representative and R.upload_time >= '2025-01-01'")[0][0]
S["breaking_representative_high"] = q("select count(*) from C where breaking and not parent_removed and representative and confidence='high'")[0][0]
S["breaking_representative_high_by_type"] = dict(q("select change_type, count(*) from C where breaking and not parent_removed and representative and confidence='high' group by 1 order by 2 desc"))
S["breaking_representative_high_2025_plus"] = q("select count(*) from C join R on C.project=R.project and C.to_version=R.version "
    "where C.breaking and not C.parent_removed and C.representative and C.confidence='high' and R.upload_time >= '2025-01-01'")[0][0]
S["projects_with_high_breaking"] = q("select count(distinct project) from C where breaking and confidence='high'")[0][0]
S["breaking_root_events"] = q("select count(*) from C where breaking and not parent_removed")[0][0]
S["breaking_root_by_type"] = dict(q("select change_type, count(*) from C where breaking and not parent_removed group by 1 order by 2 desc"))
S["breaking_root_by_kind"] = dict(q("select symbol_kind, count(*) from C where breaking and not parent_removed group by 1 order by 2 desc"))
S["removals_with_prior_deprecation_marker_share"] = round(q(
    "select avg(deprecated_before::int) from C where breaking and not parent_removed and representative and change_type='removed' "
    "and symbol_kind in ('function','method','class','class_open','classmethod','staticmethod')")[0][0], 3)
S["api_history_rows"], S["distinct_symbol_paths"] = q("select count(*), count(distinct project||path) from H")[0]
S["signatures"] = q(f"select count(*) from '{D}/signatures.parquet'")[0][0]
S["breaking_root_events_2025_plus"] = q(
    "select count(*) from C join R on C.project=R.project and C.to_version=R.version "
    "where C.breaking and not C.parent_removed and R.upload_time >= '2025-01-01'")[0][0]
json.dump(S, open(f"{D}/../stats.json", "w"), indent=1, default=str)
print(json.dumps(S, indent=1, default=str))
