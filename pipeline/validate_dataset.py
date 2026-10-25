"""Integrity checks for a PyPI API Drift build. Run on every published build:

    python validate_dataset.py RELEASE_DIR            (expects RELEASE_DIR/data, /benchmark, /stats.json)

Checks keys, cross-table references, version intervals, label domains, benchmark provenance, and recomputes the
headline counts in stats.json directly from the tables. Writes RELEASE_DIR/validation/dataset_checks.json and exits
non-zero if any check fails."""
import json, os, sys, time
import duckdb
from packaging.version import Version, InvalidVersion

R = sys.argv[1]
D, B = f"{R}/data", f"{R}/benchmark"
con = duckdb.connect()
con.execute("SET memory_limit='2GB'")
con.execute("SET preserve_insertion_order=false")
os.makedirs(f"{R}/.duckdb_tmp", exist_ok=True)
con.execute(f"SET temp_directory='{R}/.duckdb_tmp'")
for t in ("releases", "api_history", "changes", "signatures", "top_level_modules"):
    con.execute(f"create view {t} as select * from '{D}/{t}.parquet'")
has_cov = os.path.exists(f"{D}/module_coverage.parquet")
if has_cov:
    con.execute(f"create view module_coverage as select * from '{D}/module_coverage.parquet'")
con.execute(f"create view probe as select * from '{B}/versionprobe.parquet'")
one = lambda sql: con.execute(sql).fetchone()[0]
results = []


def check(name, sql, expect=0, detail=None):
    t = time.time()
    got = one(sql)
    ok = got == expect
    results.append({"check": name, "ok": ok, "value": got, "expected": expect, "seconds": round(time.time() - t, 1),
                    **({"detail": detail} if detail else {})})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got}" + ("" if ok else f" (expected {expect})"), flush=True)


BREAKING = ("removed", "param_removed", "param_renamed", "required_param_added", "default_removed",
            "became_keyword_only", "became_positional_only", "positional_reordered", "vararg_removed",
            "varkw_removed", "kind_changed")
CHANGE_TYPES = BREAKING + ("added", "annotation_changed", "left_public_api", "return_annotation_changed",
                           "optional_param_added", "default_changed", "deprecation_marker_added", "became_dynamic")
KINDS = ("module", "module_alias", "function", "class", "class_open", "method", "classmethod", "staticmethod",
         "property", "attribute", "dynamic_attribute", "external_reexport", "unresolved", "extension_module")
lst = lambda xs: "(" + ",".join(f"'{x}'" for x in xs) + ")"

# ---- releases
check("releases: unique (project, version)", "select count(*) - count(distinct (project, version)) from releases")
check("releases: unique (project, version_index)",
      "select count(*) - count(distinct (project, version_index)) from releases")
check("releases: version_index is 0..n-1 within each project",
      "select count(*) from (select project, min(version_index) mn, max(version_index) mx, count(*) n "
      "from releases group by 1) where mn <> 0 or mx <> n - 1")
check("releases: status in {ok, skipped_too_many_modules, error}",
      "select count(*) from releases where status not in ('ok', 'skipped_too_many_modules', 'error')")
check("releases: bump label domain",
      "select count(*) from releases where bump is not null and bump not in ('major', 'minor', 'patch', 'other')")
check("releases: prev_version is the previous analysed release",
      "with ok as (select project, version, version_index, prev_version, lag(version) over (partition by project "
      "order by version_index) lagv from releases where status = 'ok') "
      "select count(*) from ok where prev_version is distinct from lagv")
bad_order = 0
for proj, vs in con.execute("select project, list(version order by version_index) from releases group by 1").fetchall():
    try:
        v = [Version(x) for x in vs]
        bad_order += sum(1 for a, b in zip(v, v[1:]) if not a < b)
    except InvalidVersion:
        bad_order += 1
results.append({"check": "releases: version_index follows PEP 440 order", "ok": bad_order == 0, "value": bad_order,
                "expected": 0})
print(f"{'PASS' if bad_order == 0 else 'FAIL'}  releases: version_index follows PEP 440 order: {bad_order}")
placeholder = 0
for proj, vs, ts in con.execute("select project, list(version order by version_index), "
                                "list(upload_time order by version_index) from releases where status = 'ok' "
                                "group by 1").fetchall():
    if len(vs) >= 2:
        try:
            r = Version(vs[0]).release
        except InvalidVersion:
            continue
        placeholder += int(bool(r) and not any(r) and ts[0] > ts[1])
results.append({"check": "releases: no placeholder version (0.0.0) uploaded after the release it precedes",
                "ok": placeholder == 0, "value": placeholder, "expected": 0})
print(f"{'PASS' if placeholder == 0 else 'FAIL'}  releases: no placeholder version uploaded after the next release: "
      f"{placeholder}")

# ---- api_history
check("api_history: kind domain", f"select count(*) from api_history where kind not in {lst(KINDS)}")
check("api_history: first_index <= last_index", "select count(*) from api_history where first_index > last_index")
check("api_history: indices and versions match releases",
      "select count(*) from api_history h left join releases a on a.project = h.project and a.version_index = "
      "h.first_index left join releases b on b.project = h.project and b.version_index = h.last_index "
      "where a.version is distinct from h.first_version or b.version is distinct from h.last_version")
check("api_history: intervals only cover analysed releases",
      "select count(*) from api_history h join releases r on r.project = h.project and r.version_index = "
      "h.first_index where r.status <> 'ok'")
check("api_history: no overlapping intervals for one (project, path)",
      "select count(*) from (select first_index, lag(last_index) over (partition by project, path "
      "order by first_index) prev_last from api_history) where first_index <= prev_last")
check("api_history: adjacent intervals differ in state",
      "select count(*) from (select first_index, kind, sig_hash, deprecated, lag(last_index) over w pl, "
      "lag(kind) over w pk, lag(sig_hash) over w ps, lag(deprecated) over w pd from api_history "
      "window w as (partition by project, path order by first_index)) "
      "where pl = first_index - 1 and pk = kind and ps is not distinct from sig_hash and pd = deprecated")
check("api_history: every sig_hash resolves to a signature",
      "select count(*) from (select distinct sig_hash from api_history where sig_hash is not null) h "
      "anti join signatures s using (sig_hash)")

# ---- signatures
check("signatures: unique sig_hash", "select count(*) - count(distinct sig_hash) from signatures")
check("signatures: valid JSON with a params list",
      "select count(*) from signatures where try_cast(json_extract(sig, '$.params') as json[]) is null")

# ---- changes
check("changes: change_type domain", f"select count(*) from changes where change_type not in {lst(CHANGE_TYPES)}")
check("changes: confidence domain", "select count(*) from changes where confidence not in ('high', 'medium')")
check("changes: breaking only for breaking change types",
      f"select count(*) from changes where breaking and change_type not in {lst(BREAKING)}")
check("changes: to_version exists with matching index",
      "select count(*) from changes c anti join releases r on r.project = c.project and r.version = c.to_version "
      "and r.version_index = c.to_version_index")
check("changes: from_version is the to-release's prev_version",
      "select count(*) from changes c join releases r on r.project = c.project and r.version = c.to_version "
      "where r.prev_version is distinct from c.from_version")
check("changes: bump matches releases",
      "select count(*) from changes c join releases r on r.project = c.project and r.version = c.to_version "
      "where r.bump is distinct from c.bump")
check("changes: old_sig/new_sig resolve",
      "select count(*) from (select old_sig h from changes where old_sig is not null union "
      "select new_sig from changes where new_sig is not null) anti join signatures s on s.sig_hash = h")
check("changes: representative rows are unique per underlying change",
      "select count(*) - count(distinct (project, to_version_index, split_part(path, '.', -1), change_type, "
      "coalesce(param, ''), coalesce(old_sig, ''), coalesce(new_sig, ''))) from changes where representative")
check("changes: release counters match change rows",
      "with c as (select project, to_version, sum((breaking)::int) nb, count(distinct case when breaking then path end) ns "
      "from changes group by 1, 2) select count(*) from releases r left join c on c.project = r.project "
      "and c.to_version = r.version where r.status = 'ok' and r.prev_version is not null and "
      "(coalesce(c.nb, 0) <> r.n_breaking_events or coalesce(c.ns, 0) <> r.n_breaking_symbols)")

# ---- top-level modules and coverage
check("top_level_modules: projects exist", "select count(*) from (select distinct project from top_level_modules) "
      "anti join (select distinct project from releases) using (project)")
check("top_level_modules: no duplicate rows", "select count(*) - count(distinct (project, module)) from top_level_modules")
if has_cov:
    check("module_coverage: source domain", "select count(*) from module_coverage where source not in "
          "('py', 'py+pyi', 'pyi', 'compiled', 'unparseable')")
    check("module_coverage: indices and versions match releases",
          "select count(*) from module_coverage m left join releases a on a.project = m.project and a.version_index = "
          "m.first_index left join releases b on b.project = m.project and b.version_index = m.last_index "
          "where a.version is distinct from m.first_version or b.version is distinct from m.last_version")
    check("module_coverage: no overlapping intervals per module",
          "select count(*) from (select first_index, lag(last_index) over (partition by project, module "
          "order by first_index) pl from module_coverage) where first_index <= pl")
    check("api_history: coverage_status domain", "select count(*) from api_history where coverage_status not in "
          "('static', 'stub_only', 'dynamic_module', 'compiled_boundary')")

# ---- benchmark provenance
check("versionprobe: two items per pair, one valid and one invalid",
      "select count(*) from (select pair_id, count(*) n, sum((label = 'valid')::int) v from probe group by 1) "
      "where n <> 2 or v <> 1")
check("versionprobe: item versions are the pair's before/after releases",
      "select count(*) from probe where not ((label = 'valid' and version = version_before) or "
      "(label = 'invalid' and version = version_after))")
check("versionprobe: every pair traces to a breaking change row between its releases",
      "select count(*) from (select distinct pair_id, project, version_before, version_after, symbol, change_type, "
      "coalesce(param, '') param from probe) p anti join (select project, from_version, to_version, path, "
      "change_type, coalesce(param, '') param from changes where breaking) c on c.project = p.project and "
      "c.from_version = p.version_before and c.to_version = p.version_after and c.path = p.symbol and "
      "c.change_type = p.change_type and c.param = p.param")
check("versionprobe: unique item_id", "select count(*) - count(distinct item_id) from probe")

# ---- nulls in key columns
for t, cols in {"releases": ["project", "version", "version_index", "status"],
                "api_history": ["project", "path", "kind", "first_index", "last_index"],
                "changes": ["project", "from_version", "to_version", "path", "change_type", "breaking", "confidence",
                            "representative", "parent_removed"],
                "signatures": ["sig_hash", "sig"]}.items():
    check(f"{t}: no nulls in key columns", f"select count(*) from {t} where " + " or ".join(f"{c} is null" for c in cols))

# ---- recompute stats.json headline counts
S = json.load(open(f"{R}/stats.json"))
recompute = {
    "projects": "select count(distinct project) from releases",
    "releases": "select count(*) from releases",
    "projects_with_ok_release": "select count(distinct project) from releases where status = 'ok'",
    "change_events": "select count(*) from changes",
    "representative_events": "select sum(representative::int) from changes",
    "breaking_representative": "select count(*) from changes where breaking and not parent_removed and representative",
    "breaking_representative_high": "select count(*) from changes where breaking and not parent_removed and "
                                    "representative and confidence = 'high'",
    "breaking_representative_high_2025_plus": "select count(*) from changes c join releases r on c.project = r.project "
        "and c.to_version = r.version where c.breaking and not c.parent_removed and c.representative and "
        "c.confidence = 'high' and r.upload_time >= '2025-01-01'",
    "api_history_rows": "select count(*) from api_history",
    "signatures": "select count(*) from signatures",
    "release_transitions": "select count(*) from releases where status = 'ok' and prev_version is not null",
}
con.execute("SET TimeZone='UTC'")
for k, sql in recompute.items():
    got = one(sql)
    ok = got == S[k]
    results.append({"check": f"stats.json: {k}", "ok": ok, "value": got, "expected": S[k]})
    print(f"{'PASS' if ok else 'FAIL'}  stats.json {k}: {got}" + ("" if ok else f" (stats.json says {S[k]})"), flush=True)

os.makedirs(f"{R}/validation", exist_ok=True)
summary = {"checks": len(results), "failed": sum(not r["ok"] for r in results), "results": results}
json.dump(summary, open(f"{R}/validation/dataset_checks.json", "w"), indent=1, default=str)
print(f"\n{summary['checks']} checks, {summary['failed']} failed")
sys.exit(1 if summary["failed"] else 0)
