---
license: cc-by-4.0
pretty_name: PyPI API Drift
language:
- en
tags:
- code
- python
- api-evolution
- breaking-changes
- benchmark
- llm-evaluation
task_categories:
- text-classification
size_categories:
- 10M<n<100M
configs:
- config_name: versionprobe
  data_files: benchmark/versionprobe.parquet
- config_name: versionprobe_verified
  data_files: benchmark/versionprobe_verified.parquet
  default: true
- config_name: versionprobe_lite
  data_files: benchmark/versionprobe_lite.parquet
- config_name: changes
  data_files: data/changes.parquet
- config_name: api_history
  data_files: data/api_history.parquet
- config_name: releases
  data_files: data/releases.parquet
- config_name: signatures
  data_files: data/signatures.parquet
- config_name: module_coverage
  data_files: data/module_coverage.parquet
---

# PyPI API Drift

Historical public-API snapshots for up to 80 recent final releases of each of the 1,872 most-downloaded
Python libraries, drawn from 2021 to October 2026 (55,565 releases), and every change between consecutive
releases (15,258,652 events, 210,535 distinct breaking changes, 181,023
high-confidence). It is a high-precision, low-recall record, not complete ground truth: names created at runtime are
often missing. This is a research release; all validation was done by pipeline-isolated AI agents, not human
annotators (a 200-item human labelling kit is in the repository).

How it was built: wheels are read with HTTP range requests (only the ZIP directory and changed `.py`/`.pyi` files,
about 8.5% of the wheels' bytes, roughly 26 GB instead of 300 GB) and parsed with `ast` without
importing or executing any package code. A name is labelled `removed` only when it is no longer reachable through any
import path, or when a module `__getattr__` explicitly rejects it; names merely dropped from `__all__` are
`left_public_api`. Filter `changes` on `confidence == "high"` and `representative`, and `api_history` on
`coverage_status == "static"`, for the strictest view.

| Measured on samples the pipeline never saw | Result |
|---|---|
| Precision, high-confidence breaking changes, re-audited on v1.2 | 91.9% (95% CI 86.6–95.2%; n=160) |
| Precision, high-confidence breaking changes, first untuned audit | 88.8% (95% CI 80.0–94.0%; n=80) |
| VersionProbe labels correct (fresh sample) | 91.5% (95% CI 88.4–93.9%; n=400) |
| Recall within the dataset's static public-API definition | 14/16 |
| Recall over all runtime-visible changes | 16/99 (runtime-generated APIs are invisible to static analysis) |

**VersionProbe** (10,958 pairs from 583 libraries; `versionprobe_lite`: 983 pairs)
asks a model whether a snippet is valid for an exact library version. Each pair is valid in one release and invalid in
the next, and carries the release date so you can split results at a model's training cutoff.
**`versionprobe_verified`** (492 pairs, 984 items, 375 libraries) contains only pairs whose
labels were confirmed at runtime by AI agents isolated from the pipeline (no human adjudication; evidence in the
repository); it is the default configuration and the one to rank models on.

| Model, closed book, on `versionprobe_verified` | Item accuracy | Pair accuracy |
|---|---|---|
| `claude-opus-5-5` | 58.4% | 26.6% |
| `claude-fable-5-1` | 56.5% | 22.4% |
| `claude-sonnet-5-5` | 54.3% | 18.3% |
| `claude-haiku-5-5` | 49.6% | 9.2% |

A constant answer scores 50% / 0%. Given API history retrieved from this dataset, `claude-opus-5-5` scored
100.0%; that is an oracle condition showing the answer is in the record, not a general benefit.

In a preregistered code-repair experiment on 143 real breaking changes, repeated across 4 models from
one model family, API
history from this dataset raised API-compatible, intent-preserving migrations (under the evaluation protocol: runtime
check of names and argument binding plus an AI judge of intent; not a behavioural test) to 68.5%–69.9%
for every model, from 7.0%–56.6% unaided and 46.2%–62.2% with the new release's
documentation.

`module_coverage` and the `coverage_status` column of `api_history` mark where static analysis cannot see the whole
API (stub-only, dynamic or compiled modules), so absence of a name is not read as proof that it does not exist.

```python
from datasets import load_dataset
probe = load_dataset("pushkar87dg/pypi-api-drift", "versionprobe_verified", split="train")
changes = load_dataset("pushkar87dg/pypi-api-drift", "changes", split="train")
```

Use `breaking & ~parent_removed & representative & confidence == "high"` for one row per real, high-confidence
breaking change. Before use, check downloaded tables against `DATA_MANIFEST.json` (data version 2026.10-v1.3) with
`python verify_data.py DATA_DIR` from the repository. The checker `apidrift-check`, the lookup tool `apidrift-lookup`,
the MCP server `apidrift-mcp`, the evaluation script, the integrity checks and all audit material are in the GitHub
repository: <https://github.com/pushkardg/pypi-api-drift>.

**Author:** Pushkar Devanahalli Gopalakrishna (Independent Researcher, USA). **Citation:** see `CITATION.cff` or the
the paper, <https://doi.org/10.5281/zenodo.23648339>.
