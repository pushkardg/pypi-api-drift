#!/bin/sh
# v1.3 rebuild: v1.2 extraction with the 56 container-shim projects re-extracted (out/full9), then the v1.2 steps,
# the placeholder fix, stats, validation and the manifest.
set -e
cd "${APIDRIFT_WORKDIR:-.}"  # workspace root holding code/, out/, raw/ and release/
rm -rf out/v13 raw/v13 && mkdir -p out/v13/proj raw/v13/data
for d in out/full7/proj/*/; do p=$(basename $d); if [ -d out/full9/proj/$p ]; then ln -s ../../full9/proj/$p out/v13/proj/$p; else ln -s ../../full7/proj/$p out/v13/proj/$p; fi; done
python3 code/merge.py out/v13 raw/selected.parquet raw/v13/data
python3 code/finalize_changes.py raw/v13/data raw/v13/tmp
python3 code/patch_release.py raw/v13/data out/cov raw/v13/data_p
mv raw/v13/data raw/v13/data_unpatched && mv raw/v13/data_p raw/v13/data && rm -rf raw/v13/data/.tmp raw/v13/tmp
python3 - <<'PY'
import pyarrow.parquet as pq
t = pq.read_table('raw/v13/data/signatures.parquet').sort_by('sig_hash')
pq.write_table(t, 'raw/v13/data/signatures.parquet', row_group_size=10000, compression='zstd')
PY
rm -rf raw/v13/rel && mkdir -p raw/v13/rel/validation raw/v13/rel/benchmark
python3 code/drop_placeholders.py raw/v13/data raw/v13/rel/data raw/bench_v12_backup raw/v13/rel/benchmark raw/v13/placeholders_log.json
cp release/benchmark/versionprobe_verified_evidence.csv raw/v13/rel/benchmark/
python3 code/stats.py raw/v13/rel/data
echo REBUILD_DONE
