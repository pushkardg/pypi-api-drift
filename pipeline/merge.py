"""Merge per-project outputs into the release tables.
Usage: python merge.py OUT_DIR SELECTED_PARQUET DATA_DIR"""
import glob, json, os, sys
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

OUT, SEL, DATA = sys.argv[1:4]
os.makedirs(DATA, exist_ok=True)
projs = sorted(glob.glob(f"{OUT}/proj/*/"))
sel = pd.read_parquet(SEL)


def stream(name, path, sort_cols, schema_fix=None):
    w = None
    n = 0
    for d in projs:
        fs = sorted(glob.glob(f"{d}{name}*.parquet"))
        if not fs:
            continue
        df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
        if df.empty:
            continue
        if schema_fix:
            df = schema_fix(df)
        df = df.sort_values(sort_cols)
        t = pa.Table.from_pandas(df, preserve_index=False)
        if w is None:
            w = pq.ParquetWriter(path, t.schema, compression="zstd")
        w.write_table(t.cast(w.schema)); n += len(df)
    if w:
        w.close()
    return n


def fix_events(df):
    for c in ["param", "detail", "old_sig", "new_sig", "bump"]:
        df[c] = df[c].astype("string")
    for c in ["breaking", "parent_removed", "deprecated_before"]:
        df[c] = df[c].astype(bool)
    df["to_version_index"] = df.to_version_index.astype("int32")
    df["confidence"] = df.get("confidence", pd.Series("high", index=df.index)).fillna("high").astype("string")
    return df[["project", "from_version", "to_version", "to_version_index", "bump", "path", "symbol_kind",
               "change_type", "breaking", "confidence", "parent_removed", "deprecated_before", "param", "detail",
               "old_sig", "new_sig"]]


def fix_iv(df):
    df["sig_hash"] = df.sig_hash.astype("string")
    df["deprecated"] = df.deprecated.astype(bool)
    df["first_index"] = df.first_index.astype("int32"); df["last_index"] = df.last_index.astype("int32")
    return df


RCOLS = ["project", "version", "version_index", "popularity_rank", "upload_time", "wheel_filename", "wheel_size",
         "pure_python_wheel", "status", "error", "n_modules", "n_modules_parse_failed", "n_extension_modules",
         "n_public_symbols", "n_members_fetched", "prev_version", "bump", "likely_calver", "n_added", "n_removed",
         "n_breaking_events", "n_breaking_symbols"]


def fix_rel(df):
    for c in RCOLS:
        if c not in df:
            df[c] = None
    df = df[RCOLS].copy()
    for c in ["error", "prev_version", "bump", "status"]:
        df[c] = df[c].astype("string")
    for c in ["n_modules", "n_modules_parse_failed", "n_extension_modules", "n_public_symbols", "n_members_fetched",
              "n_added", "n_removed", "n_breaking_events", "n_breaking_symbols"]:
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    df["likely_calver"] = df.likely_calver.astype("boolean")
    df["upload_time"] = pd.to_datetime(df.upload_time, utc=True, format="ISO8601")
    return df


if "--sigs-only" not in sys.argv:
  print("api_history", stream("intervals", f"{DATA}/api_history.parquet", ["path", "first_index"], fix_iv))
  print("changes", stream("events", f"{DATA}/changes.parquet", ["to_version_index", "path", "change_type"], fix_events))
  print("releases", stream("releases", f"{DATA}/releases.parquet", ["version_index"], fix_rel))

import pyarrow.compute as pc
rows = [pq.read_table(f) for f in sorted(glob.glob(f"{OUT}/proj/*/sigs.parquet"))]
sig = pa.concat_tables([t for t in rows if t.num_rows]).to_pandas().drop_duplicates("sig_hash")
sig.to_parquet(f"{DATA}/signatures.parquet", index=False, compression="zstd")
print("signatures", len(sig))

# public module paths per project (any analysed release, so modules removed later still map to their
# distribution) -> used to map import paths to distributions
iv = pq.read_table(f"{DATA}/api_history.parquet", columns=["project", "path", "kind", "last_index"],
                   filters=[("kind", "in", ["module", "extension_module"])]).to_pandas()
tops = iv[["project", "path"]].rename(columns={"path": "module"}).drop_duplicates()
tops.to_parquet(f"{DATA}/top_level_modules.parquet", index=False)
print("modules", len(tops))
