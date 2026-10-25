# Validation protocol

Precision of the claimed breaking changes was measured by independent audits. The audit agent never had access to
the extraction pipeline or the dataset files. It only saw a CSV of claims.

For each claim (project, from_version, to_version, path, change_type, param, detail) the auditor:

1. created isolated virtual environments and installed `project==from_version` and `project==to_version` from PyPI,
   along with any optional dependency needed to import the module;
2. imported the module part of `path`, walked attributes (including inherited and lazily served ones) and compared
   `inspect.signature` where the claim concerned parameters;
3. if installation was impossible (platform, heavy dependencies), read both versions' released source instead;
4. recorded a verdict (`confirmed`, `false_positive`, `partially`, `unverifiable`), the method used and one sentence
   of evidence.

| Round | Files | Sample | Notes |
|---|---|---|---|
| 1 | `sample.csv`, `results.csv` | 60 claims, 32 libraries | first pipeline version: 36 confirmed, 22 false positives, 2 partially |
| 2 | `sample2.csv`, `results2.csv` | 60 claims, 32 other libraries | after fixing round-1 patterns: 43 / 15 / 2 |
| 3 | `sample3.csv`, `results3.csv` | 100 claims (80 high, 20 medium confidence), 53 libraries not used in rounds 1–2 | released pipeline: high 71 confirmed / 6 false positive / 3 partially; medium 9 / 9 / 2 |

`audits.json` re-scores every audited claim against the *released* tables. A claim counts only if the released data
still makes it, at its released confidence tier. Two confidence rules were added after round 3 and should be read
as in-sample for that round: removing `*args`/`**kwargs` and turning a class into a plain attribute both move to
`medium`. The out-of-sample estimate for high-confidence breaking changes is therefore the as-audited round-3 figure
(71/80 = 88.8%, or 92.5% counting partially correct claims). After the post-hoc rules, 67 of the 71 high-confidence
round-3 claims that remain are confirmed (94.4%).

`checker_false_alarm_test.json` records a separate test of `apidrift-check`. It ran over 1,823 source files of 14
widely used packages, each checked against the dependency versions actually installed with them.

## Further studies (all by independent AI agents, no access to the pipeline or data)

| Folder | Study | Out-of-sample result (before the fixes it prompted) |
|---|---|---|
| `benchmark_check/` | 200 VersionProbe pairs (one per library, libraries not in the precision audits) resolved at runtime in both releases | 172/200 labels correct (86.0%, 95% CI 80.5–90.1%) |
| `recall/` | Runtime introspection of both releases of 30 random release pairs; every breaking change listed (99) | 14/16 (87.5%) within this dataset's public-API definition; 16/27 (59.3%) excluding `trio`; 16/99 (16.2%) overall |
| `downstream/` | Test suites of 12 real projects run before/after a dependency upgrade (9 breaking, 3 controls); API-caused failures located | checker: 7/7 findings correct, 0 on old environments or controls, 4/12 failures predicted |

The files ending in `_before_fixes.json` hold those out-of-sample scores. The files without the suffix hold the scores
re-computed on the released data after the fixes: TYPE_CHECKING and Python-2 branches skipped, later
redefinitions win, `X as X` re-exports, imported expired-attribute tables, alias-aware parameter history, and
instance attributes excluded from benchmark snippets. Those after-fix scores are in-sample. Downstream source
trees are not redistributed; `downstream/<case>/freeze_*.txt` pins the exact environments, and `cases.csv` names the
project releases.

## Second review (all pipeline-isolated AI agents; no human adjudication)

| Folder / file | Study | Design |
|---|---|---|
| `benchmark_check2/` | Fresh VersionProbe label audit | 400 pairs from the rebuilt benchmark, restricted to 265 libraries never used in any earlier audit, study or fix; at most two pairs per library, signature changes over-sampled. Both releases installed; every name resolved and every keyword bound with `inspect.signature`; snippets never executed. |
| `downstream2/` | Unseen upgrades | Checker and data frozen first (`FROZEN_sha256.txt`; scored with that checker). Two agents, who never ran the checker, collected 35 cases (25 expected breaks, 10 controls) over libraries split between them, including compiled packages and lazily loaded APIs; test suites run before/after, API-caused failures located to file and line, and masked or unexercised incompatible uses listed separately. `comparison_frozen.json` is the out-of-sample score; `comparison_after_fixes.json` is in-sample. Source trees are not redistributed; `cases.csv` names each release and `<case>/freeze_*.txt` pins the environments. |
| `tool_comparison/` | griffe and AexPy | Both tools run on the 30 recall pairs (commands in `NOTES.md`, scripts in `scripts/`); every method scored with one matching rule against `recall/ground_truth.csv` (`comparison.json`). |
| `versioning_policy.csv`, `semver_split.json` | Stated versioning policy | Policy sentences matched in each project's PyPI description; every match reviewed by hand (`manual_override` marks the corrections). |
| `dataset_checks.json` | Integrity | `pipeline/validate_dataset.py` over the released tables. |
| `checker_performance.json` | Checker cost | `pipeline/bench_checker.py`: wall time, CPU time and peak memory on four workloads. |
| `patch_log.json` | Corrections | Counts of the corrections applied by `pipeline/patch_release.py`. |

## Fourth review

| Folder | Study | Design |
|---|---|---|
| `audit4/` | Fresh precision audit of v1.2 | 160 high-confidence representative breaking changes from 134 libraries never used in any earlier audit or study, stratified 80/80 into claims new in v1.2 (8,274 in the population) and claims unchanged since v1.1; four agents, same protocol as round 3. `summary.json` gives per-stratum and stratum-weighted precision. |
| `code_repair/` | Preregistered code-repair experiment | `PREREGISTRATION.md` (task list hash recorded before any model output); 150 fresh VersionProbe pairs; three conditions (closed book, griffe-generated documentation of the new release, PyPI API Drift history); runtime check with only the new release installed (`pipeline/repair_resolver.py`); blind faithfulness judge; exact McNemar tests. |
| `../benchmark/versionprobe_verified_evidence.csv` | Verified split evidence | Per-pair runtime verdicts from the two benchmark audits for every pair in `versionprobe_verified.parquet`. |

## Sixth review: full-binding sensitivity check and clean-install test (2026-10-21)

- Code repair, post hoc and not preregistered: every task was re-verified in a fresh environment, judging each answer
  twice, with the preregistered `bind_partial` and with full `inspect.signature(...).bind`
  (`pipeline/verify_repair.py ... --strict`, scored by `pipeline/score_repair_strict.py`). The AI-judge verdicts were
  reused unchanged. Output: `code_repair/verify_strict.jsonl`, `results_strict_binding.json`, `scored_tasks_strict.csv`.
- Clean install: `smoke_test.py` installs the release non-editably into a fresh venv, runs the tests (including
  data-level regressions against the downloaded data), verifies the data hashes and runs the installed CLI on the
  bundled example. Output: `clean_install.json`. CI has a matching `clean-install` job on the fixture data.

## Third review: placeholder releases, container shims, model replication, human-labelling kit (2026-10-22)

- Placeholder releases (`pipeline/drop_placeholders.py`, log `placeholder_releases.json`): an all-zero version
  uploaded after the release it precedes is removed with its diffs and dependent benchmark pairs. Selection
  (`select_releases.py`) and `validate_dataset.py` now exclude/check them.
- Module `__getattr__` container shims (`modparse.py`: `_served_containers`, `_getattr_container_lookups`): the
  affected libraries were found by re-parsing every dynamic module at the ends of its recorded ranges with the old and
  new extractor (`pipeline/scan_getattr_shims.py`) and only those were re-extracted; the rest of the extraction is
  unchanged. Summary of the data change: `v13_changes.json`.
- Model replication (`multi_model/`): VersionProbe-verified and the code-repair experiment re-run unchanged with three
  more models, judged by the same blind judge, re-verified under both binding rules.
- Human-labelling kit (`human_labeling/`): 200 items (all 160 v1.2 audit claims, 40 random benchmark-audit pairs),
  blind sheets for two labellers, an AI key and an agreement script. Not yet labelled.
