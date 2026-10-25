# Tool comparison run: griffe and AexPy on 30 release pairs

Date: 2026-10-20. Machine: 2 CPUs, about 7 GB RAM, shared with other jobs. Only one install ran at a time, and every per-pair environment was deleted after use.
Scripts: `scripts/fetch.py`, `scripts/run_griffe.py`, `scripts/run_aexpy.py`. The scratch work dir lived outside the repo and was deleted.
Nothing under apidrift/code, release, out, pkg or raw, and nothing elsewhere in validation/, was read or used.

## Versions
- Host python 3.13.16; uv 0.11.32; system pip 24.0 (AexPy calls `/usr/bin/pip download` itself)
- griffe 2.4.0, in a `uv venv --python 3.12` venv
- AexPy 0.4.3 (bb37ad3, 2024-03-13; the latest on PyPI), run with Python 3.12.13, mypy 2.4.0, pydantic 2.14.1, pip 26.2.2

## griffe
```
uv venv --python 3.12 griffe-venv && VIRTUAL_ENV=griffe-venv uv pip install griffe
griffe-venv/bin/python -I scripts/run_griffe.py <workdir> [ids]
```
For each release, `fetch.py` downloads one artifact from the PyPI JSON API. It prefers a `py3-none-any` wheel, then a cp312 manylinux wheel, then any wheel, then an sdist. In practice all 60 releases had pure wheels. It unpacks the artifact into its own directory and lists the top-level importable names (directories that contain .py/.pyi files, plus top-level .py modules; `tests`/`test` excluded).
Each release gets a `griffe.GriffeLoader(search_paths=[<that dir>], allow_inspection=False)`. The loader loads every top-level name, including private ones such as `_griffe`, so that public re-exports resolve. It then calls `resolve_aliases(implicit=False, external=False)`, and `griffe.find_breaking_changes(old_module, new_module)` runs for each public top-level name present in both releases.
The analysis is purely static: no dependencies are installed and nothing is imported.
Timing (`seconds` in runs.csv) is wall-clock for the whole pair: download, unpack, load and diff. `notes` splits out `fetch_s` and `analysis_s`.

griffe classes every result as breaking, so `breaking=yes` on every row. The `tool_label` column holds the `BreakageKind` name. Kinds were mapped to change_type as follows:

| griffe BreakageKind | change_type |
|---|---|
| OBJECT_REMOVED | removed |
| PARAMETER_REMOVED | param_removed (vararg_removed / varkw_removed when the old param is `*args` / `**kwargs`) |
| PARAMETER_ADDED_REQUIRED | required_param_added |
| PARAMETER_CHANGED_REQUIRED (default removed, param became required) | default_removed |
| PARAMETER_MOVED | positional_reordered |
| PARAMETER_CHANGED_KIND | became_keyword_only / became_positional_only by the new kind, otherwise other |
| PARAMETER_CHANGED_DEFAULT, RETURN_CHANGED_TYPE, OBJECT_CHANGED_KIND, ATTRIBUTE_CHANGED_TYPE, ATTRIBUTE_CHANGED_VALUE, CLASS_REMOVED_BASE | other |

For parameter breakages, `path` is the function and `param` is the parameter name. griffe has no rename detection: a rename shows up as a removal plus an addition, or as nothing if the added parameter is optional. So `param_renamed` never occurs.

Fix during the run: `build` (pair 4) first produced an empty package list, because the sdist exclusion list contained `build`. That exclusion now applies to sdists only, and pair 4 was re-run (along with 22 and 27, which had suspiciously low analysis times; their results were unchanged). Only the latest row per tool and id is kept in runs.csv.

## AexPy
```
uv venv --python 3.12 env && uv pip install --python env/bin/python aexpy==0.4.3 pip
echo <project>@<version> | PYTHONUTF8=1 env/bin/aexpy extract - <side>.json -r --no-temp
aexpy diff old.json new.json diff.json          # run from a separate aexpy tool venv
```
(`python3 -I scripts/run_aexpy.py <workdir> 0.4.3 [ids]`)
AexPy's default `--temp` mode needs micromamba or conda, which this machine does not have. Docker exists, but the README's documented `--no-temp` mode (use the current environment) works, so a fresh uv venv was built for every release. That venv holds AexPy and pip; AexPy then downloads the release wheel and runs `pip install <wheel>` with dependencies into the venv, imports it, and runs its mypy-based extractors. The venv is deleted right after extraction.
AexPy 0.4.3 requires Python>=3.12, so a Python 3.11 fallback is impossible. The fallback the runner originally had produced only an install error and was removed.
`pip` must be installed in the venv explicitly, because `uv venv` leaves it out. Without it, AexPy fails with "No module named 'pip'".
Timing (`seconds`) is wall-clock for the whole pair: both venv builds and installs, both extractions, and the diff. `notes` holds `extract_old_s`, `extract_new_s` and `diff_s`, plus a count of every diff entry by AexPy rank.

AexPy assigns each diff entry a BreakingRank: Unknown=-1, Compatible=0, Low=30, Medium=60, High=100. Its text report lists ranks of Low and above under "Breakings". In aexpy_changes.csv:
- `breaking=yes` means rank High.
- `breaking=potential` means rank Medium or Low. Low is AexPy's downgrade of a breaking change on a private API (`privateRank`). The `tool_label` is `<Kind>|<Rank>`, so Low rows can be filtered out.
- Ranks Compatible and Unknown are not written. Unknown is mostly ChangeExternalAlias, as in `collections.abc.Callable -> typing.Callable`. Their counts are in runs.csv notes, and the full diffs (messages only) are in `aexpy_raw/<id>_<project>_diff.jsonl`.

| AexPy kind | change_type |
|---|---|
| RemoveModule/Class/Function/Method/Attribute/InstanceAttribute/Alias/ExternalAlias | removed |
| RemoveRequiredParameter, RemoveOptionalParameter, Remove*Candidate | param_removed, refined to became_keyword_only / became_positional_only when a parameter with the same name exists in the new signature as keyword-only (kind 3) / positional-only (kind 0). AexPy itself reports positional-to-keyword-only as removal of the positional parameter. |
| RemoveVarPositional / RemoveVarKeyword | vararg_removed / varkw_removed |
| AddRequiredParameter, AddRequiredCandidate | required_param_added |
| RemoveParameterDefault | default_removed |
| MoveParameter | positional_reordered |
| everything else (ChangeParameterDefault, ChangeParameterType, ChangeReturnType, ChangeAttributeType, RemoveBaseClass, ChangeMethodResolutionOrder, ...) | other |

For alias entries, AexPy attaches the change to the containing module or class, so `path` is that container plus `.<alias name>`. For all other entries, `path` is the old entry's id, or the new entry's id if there is no old one.
AexPy has no rename detection either.

## Failures and fixes (AexPy)
- **13 gitpython** failed at first with `Different name between release and dist-info`. AexPy asserts that the release id matches the METADATA `Name` exactly, and here that name is `GitPython`. The runner now passes PyPI `info.name` as the release id (`GitPython@3.1.56`), and the pair was re-run: ok.
- **27 requirements-parser** failed at first with `No top level module extracted`. Version 0.10.2 imports `pkg_resources` but does not declare setuptools, and a fresh 3.12 venv has no setuptools. Fix: pre-install `setuptools<81` into both extraction envs for this project only, then re-run: ok. This was the only per-project environment tweak.
- **26 googleapis-common-protos** extracted fine but **failed** in `aexpy diff`. The sandbox memory cgroup OOM-killed the process (rc=-9; dmesg shows about 4.2 GB anon RSS). Each API json is about 700 MB, for two reasons. First, the `google` namespace also covers the installed protobuf dependency (`google._upb`, `google.protobuf`), so AexPy analysed protobuf too. Second, AexPy stores huge per-entry `alias` lists. I retried once with manually re-extracted files (same sizes); a diff alone on those files was not attempted. I did not work around the failure, because stripping fields would alter the tool's input. It might succeed on a machine with more than 8 GB free.
- 20 yarl and 30 rich-toolkit were re-run as sanity checks, because both produced zero diff entries. Their API entry counts were non-trivial and identical across versions (yarl 9/8/70/124 modules/classes/functions/attributes), and the zero-entry result held.
- runs.csv keeps only the latest row per (tool, id). Rows in runs.csv written before the patch lack the `api=` counts.

## Caveats for comparison
- Scope differs. griffe analyses the wheel's own top-level packages, statically and without dependencies. AexPy imports the installed package, so for namespace packages (googleapis-common-protos) it can include the namespace content of dependencies. It also counts private APIs (rank Low) and external aliases.
- trio 0.22.2 to 0.23.0 accounts for 299 of AexPy's 473 rows. Many are `RemoveVarPositional`/`RemoveVarKeyword`/`AddRequiredParameter` on High, probably from wrapper functions with `*args, **kwargs` in 0.22 that gained real signatures. These rows are reported as the tool gave them.
- griffe flags `ATTRIBUTE_CHANGED_VALUE` (e.g. `__version__`, constants), which AexPy does not report. The phonenumbers metadata constants are one case: 15 griffe rows against 0 AexPy rows.
- Timing: griffe seconds include the download. AexPy seconds include two venv builds and dependency installs, which AexPy needs because it imports the package. The machine was shared with other CPU-heavy jobs (C++ builds, coverage runs), so the times are noisy.

## Results
| tool | version | pairs ok | change rows | pairs with >=1 row | median s/pair (ok) |
|---|---|---|---|---|---|
| griffe | 2.4.0 | 30/30 | 96 (all breaking=yes) | 19 | 1.3 |
| AexPy | 0.4.3 | 29/30 (26 OOM in diff) | 473: 178 yes (High) + 295 potential (69 Medium, 226 Low) | 14 (13 with >=1 High) | 62.7 |

The full per-entry AexPy diffs, including Compatible and Unknown entries, are in `aexpy_raw/` (one jsonl per pair, messages only).
