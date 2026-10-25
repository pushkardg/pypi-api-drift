"""Post-merge pass over changes.parquet:
  * representative: one row per underlying change (same leaf name, change, parameter and signatures within a
    release transition), so inherited / re-exported copies of one change can be filtered out;
  * confidence rules added after audit 3: removal of *args (often forwarded to a base class) and a class turning
    into a plain attribute (often `Name = OtherClass` aliases) are downgraded to medium.
Usage: python finalize_changes.py DATA_DIR TMP_DIR"""
import os, sys
import duckdb

D, TMP = sys.argv[1], sys.argv[2]
os.makedirs(TMP, exist_ok=True)
con = duckdb.connect()
con.execute("SET memory_limit='4GB'")
con.execute(f"SET temp_directory='{TMP}'")
con.execute(f"""copy (
  select * replace (
      case when change_type in ('vararg_removed', 'varkw_removed') then 'medium'
           when change_type = 'kind_changed' and symbol_kind = 'attribute' then 'medium'
           else confidence end as confidence),
    row_number() over (partition by project, to_version_index, split_part(path, '.', -1), change_type,
                                    coalesce(param, ''), coalesce(old_sig, ''), coalesce(new_sig, '')
                       order by length(path), path) = 1 as representative
  from '{D}/changes.parquet'
  order by project, to_version_index, path, change_type
) to '{D}/changes_final.parquet' (format parquet, compression zstd, row_group_size 500000)""")
os.replace(f"{D}/changes_final.parquet", f"{D}/changes.parquet")
print(con.execute(f"select confidence, count(*) from '{D}/changes.parquet' where breaking group by 1").fetchall())
