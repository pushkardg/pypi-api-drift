"""Breaking-change rates by stated versioning policy (validation/versioning_policy.csv, from versioning_policy.py
plus a manual review of every match). A transition counts as breaking when it has at least one high-confidence,
representative breaking change. CalVer-looking releases are excluded; "stable" = previous version >= 1.0.
Usage: python semver_split.py DATA_DIR POLICY_CSV OUT_JSON"""
import json, sys
import duckdb, pandas as pd

D, POL, OUT = sys.argv[1:4]
pol = pd.read_csv(POL)
con = duckdb.connect()
con.execute("SET memory_limit='2GB'")
con.register("pol", pol[["project", "policy"]])
con.execute(f"""create table hb as select distinct project, to_version from '{D}/changes.parquet'
  where breaking and representative and not parent_removed and confidence = 'high'""")
con.execute(f"""create table t as select r.project, r.version, r.bump, coalesce(r.likely_calver, false) calver,
  try_cast(split_part(r.prev_version, '.', 1) as int) prev_major, hb.project is not null high_break
  from '{D}/releases.parquet' r left join hb on hb.project = r.project and hb.to_version = r.version
  where r.status = 'ok' and r.prev_version is not null and r.bump in ('patch', 'minor', 'major')""")
res = con.execute("""select p.policy, t.bump, (t.prev_major >= 1) is_stable, count(*) n, avg(high_break::int) high,
  count(distinct t.project) projects from t join pol p using (project) where not t.calver group by all""").df()
out = {f"{r.policy}|{r.bump}|{'ge1' if r.is_stable else 'lt1'}": {"n": int(r.n), "high": round(r.high, 4),
                                                                  "projects": int(r.projects)}
       for r in res.itertuples()}
for name, cond in {"semver": "p.policy = 'semver'", "all": "true"}.items():
    v = con.execute(f"""select count(*), sum(high_break::int), count(distinct case when high_break then project end),
      count(distinct project) from t join pol p using (project)
      where {cond} and not calver and prev_major >= 1 and bump = 'patch'""").fetchone()
    out[f"{name}_stable_patch"] = {"transitions": v[0], "with_high_break": int(v[1]), "share": round(v[1] / v[0], 4),
                                   "projects_with": v[2], "projects": v[3]}
out["policy_counts"] = pol.policy.value_counts().to_dict()
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
