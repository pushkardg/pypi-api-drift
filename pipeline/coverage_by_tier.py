"""Share of api_history rows by coverage_status for popularity tiers. Usage: python coverage_by_tier.py DATA OUT_JSON"""
import json, sys
import duckdb
D, OUT = sys.argv[1:3]
rows = duckdb.sql(f"""
with r as (select distinct project, popularity_rank from '{D}/releases.parquet'),
h as (select coverage_status, r.popularity_rank rk from '{D}/api_history.parquet' h join r using (project))
select case when rk <= 100 then 'top_100' when rk <= 500 then 'rank_101_500' else 'rank_501_plus' end tier,
  count(*) n, avg((coverage_status = 'static')::int) static, avg((coverage_status = 'dynamic_module')::int) dynamic_module,
  avg((coverage_status = 'compiled_boundary')::int) compiled_boundary, avg((coverage_status = 'stub_only')::int) stub_only
from h group by 1""").fetchall()
out = {t: {"rows": n, "static": round(a, 4), "dynamic_module": round(b, 4), "compiled_boundary": round(c, 4),
           "stub_only": round(d, 4)} for t, n, a, b, c, d in rows}
json.dump(out, open(OUT, "w"), indent=1); print(out)
