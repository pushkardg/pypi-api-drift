# PyPI API Drift

**How the public APIs of the {{projects}} most-downloaded Python libraries changed, release by release: up to 80 recent final releases per library, drawn from 2021–{{snapshot_month}}.**

> This is a v1 research release. Precision is measured and good; recall outside the dataset's static definition of
> public API is limited (see [Validation](#validation)). All validation was done by AI agents isolated from the
> pipeline, not by human annotators.

Code written against one version of a library often breaks on another. LLM code assistants hit this
constantly: they suggest `df.append(...)` for pandas 2, `read_csv(error_bad_lines=...)`, `Retry(method_whitelist=...)`
for urllib3 2, `from flask import escape` for Flask 3. Existing version-aware benchmarks are small and frozen.
This dataset is a large, refreshable record of which public API existed in which release: high precision, with
known blind spots (names created at runtime are often missing), so treat it as evidence, not as complete truth.

It contains:

| | |
|---|---|
| Libraries | {{projects}} selected from the top of PyPI by downloads ({{projects_usable}} with at least one analysable release) |
| Releases analysed | {{releases_ok}} final releases, {{upload_min_year}}–{{snapshot_month}} (at most the 80 most recent per library, so not every release since 2021) |
| Python modules parsed | {{modules_parsed}} |
| Public API symbols tracked | {{distinct_symbol_paths}} distinct paths (functions, classes, methods, attributes) |
| API history rows | {{api_history_rows}} (symbol × signature × version interval) |
| Change events | {{change_events}} between consecutive releases |
| Breaking changes (deduplicated) | {{breaking_representative}} ({{breaking_representative_high}} high-confidence) |
| Measured precision of high-confidence breaking changes | **{{precision_high}}** (95% CI {{ci_high}}; n={{audit_n_high}}) on an audit of libraries never used for tuning. Removals: {{prec_removed}}; signature changes: {{prec_sig}} |
| Measured recall (runtime reference set, 30 release pairs) | {{rec_def_k_b}}/{{rec_def_n_b}} within the dataset's public-API definition; {{rec_all_k_b}}/{{rec_all_n_b}} of all runtime-visible changes |
| Precision re-audited on v1.2 (fresh libraries) | **{{a4_prec}}** (95% CI {{a4_ci}}; n={{a4_n}}); {{a4_new_prec}} on claims new in v1.2, {{a4_old_prec}} on unchanged ones |
| VersionProbe labels correct (fresh untouched sample) | **{{b2_prec}}** (95% CI {{b2_ci}}; n={{b2_n}}) |
| Code repair, {{rp_n}} real breaks (preregistered; repeated on {{mm_n_models}} models of one family) | success {{rp_a_succ}} unaided, {{rp_b_succ}} with docs, **{{rp_c_succ}}** with this dataset; API history best for {{mm_gain_b}} of {{mm_n_models}} models |
| `apidrift-check` on {{u_f_checked}} unseen real upgrades | findings {{u_f_fprec}} correct; {{u_f_pred}} of {{u_f_fail}} API-caused test failures predicted |

Plus tools built on it:

* **`apidrift-check`**, which flags code that uses an API that doesn't exist, or a keyword argument that isn't
  accepted, in the library versions you pin. It works offline and never imports the libraries.
* **`apidrift-lookup`**, one-line queries: `apidrift-lookup diff pandas 1.5.3 2.2.0` (what breaks on upgrade),
  `apidrift-lookup history pandas DataFrame.append` (when it existed, with which signatures).
* **`apidrift-mcp`**, an MCP server exposing the lookups and the checker to coding agents (Claude Code, Claude
  Desktop and other MCP clients).
* **VersionProbe**, a benchmark of {{probe_pairs}} paired snippets that are *valid* in one release and *invalid*
  in the next. It tests whether a model knows which API works in which version.

> Author: **Pushkar Devanahalli Gopalakrishna** (Independent Researcher, USA) · Data: CC BY 4.0 · Code: Apache-2.0
> Code: <https://github.com/pushkardg/pypi-api-drift> · Data: <https://huggingface.co/datasets/pushkar87dg/pypi-api-drift>

## What makes it work

* **Cheap: range reads, not downloads.** Wheels are ZIP files, so the extractor reads the central directory with HTTP
  range requests and fetches only the `.py`/`.pyi` members whose CRC changed since the previous release. Replayed over
  the full histories of {{tr_projects}} random projects, this transfers {{tr_share}} of the wheels' bytes (95% CI
  {{tr_share_ci}}): about {{tr_gb}} GB ({{tr_gb_ci}} GB) for the whole dataset instead of {{tr_total_gb}} GB. Large compiled
  wheels are where it pays off ({{tr_big_share}} of their bytes fetched); a small pure-Python wheel is mostly source and
  costs about as much as downloading it (`validation/transfer_measurement.json`, `pipeline/measure_transfer.py`).
* **Safe: nothing is imported or executed during extraction.** Every module is parsed with `ast`. Untrusted package
  code never runs, so the whole PyPI history can be processed on ordinary infrastructure without sandboxing each
  release. (Runtime environments are used only by the separate validation studies.)
* **Removed means unreachable, not merely missing from `__all__`.** A name that leaves the public API is labelled
  `removed` only when static analysis shows it can no longer be reached through any import path (re-exports, star and
  relative imports, module `__getattr__`). Names still reachable, or undecidable statically, are labelled
  `left_public_api` instead, which removes the largest source of false removals in naive API diffs.
* **Audited, then fixed, then re-audited.** Agents isolated from the pipeline installed both releases and checked
  claimed breaking changes at runtime. Each error pattern they found became a rule fix plus a regression test, and the
  next round audited libraries never used for tuning ({{a4_prec}} precision on v1.2). The fixes were made by hand between
  rounds; this is an audit loop, not automatic self-correction, and no human adjudicated the audits.
* **Pick your own strictness.** Filter `changes` on `confidence == "high"` to keep only the strongest breaking
  changes, on `representative` to count each change once, and `api_history` on `coverage_status == "static"` to keep
  only modules where a missing name is strong evidence of absence (details under "What's in `data/`").
* **Useful to code models.** In a preregistered repair experiment, API history from the dataset raised the share of
  migrations that were API-compatible with the new release from {{rp_a_valid}} (closed book) and {{rp_b_valid}}
  (documentation) to {{rp_c_valid}}, and the share that were also judged intent-preserving from {{rp_a_succ}} / {{rp_b_succ}}
  to {{rp_c_succ}}. Compatibility means names resolve and arguments bind; nothing is executed (see "Code repair").

## Quick start

```bash
pip install apidrift            # or: pip install -e . from this folder
export APIDRIFT_DATA=/path/to/data
apidrift-check my_script.py --pin pandas==2.2.0 --pin flask==3.0.0
apidrift-check my_script.py -r requirements.txt --json
```

Pins must match an analysed release exactly. Otherwise the package is skipped with a note on stderr; pass `--nearest`
to check against the closest earlier analysed release instead.

```text
my_script.py:3:0: [missing] flask.escape does not exist in flask==3.0.0 (last present in 2.3.3)
my_script.py:7:5: [missing] pandas.DataFrame.append does not exist in pandas==2.2.0 (last present in 1.5.3)
my_script.py:11:6: [bad_call] pandas.DataFrame.to_csv has no parameter 'line_terminator' in pandas==2.2.0 (accepted up to 1.4.4)
my_script.py:12:8: [bad_call] urllib3.util.retry.Retry has no parameter 'method_whitelist' in urllib3==2.2.0 (accepted up to 1.26.20)
my_script.py:18:4: [bad_call] pandas.read_csv has no parameter 'error_bad_lines' in pandas==2.2.0 (accepted up to 1.5.3)
```

The checker's default mode only flags names and keyword arguments that *were* part of the library's public API
in some other release. Those are version-drift errors, and false alarms are rare. `--strict` also flags names that never
existed (useful for catching invented APIs) and checks arity, but it is noisier.

```python
import pandas as pd
ch = pd.read_parquet("data/changes.parquet",
                     filters=[("project", "=", "pandas"), ("breaking", "=", True), ("confidence", "=", "high")])
ch[ch.representative & ~ch.parent_removed].groupby("to_version").size()
```

### Look things up, or give a coding agent the same lookups

```bash
apidrift-lookup diff flask 2.3.3 3.0.0          # breaking changes on upgrade, each with its confidence label
apidrift-lookup history pandas DataFrame.append  # pandas.DataFrame.append  method  1.4.0 .. 1.5.3  [deprecation marker]
apidrift-lookup versions sklearn                 # analysed releases (distribution or top-level module name)
```

`diff` compares the two releases' API snapshots, so any two analysed releases work, not only adjacent ones. Each
change is listed once (subclass and re-export copies are folded; `--every-path` lists them all). For a removed name it
shows where the same name still exists in the new release, as a hint rather than a verified replacement.

`pip install "apidrift[mcp]"` adds `apidrift-mcp`, a stdio MCP server with four read-only tools: `api_versions`,
`api_history`, `api_diff` and `check_code`. Add it to a client with

```json
{"mcpServers": {"apidrift": {"command": "apidrift-mcp", "env": {"APIDRIFT_DATA": "/path/to/data"}}}}
```

Answers carry the dataset's blind spots with them: every `history` interval and every removal in a `diff` has a
`coverage_status` (`static`, `dynamic_module`, `compiled_boundary`, `stub_only`) and, when it is not `static`, a
`coverage_note`; a name that is not found comes with a reminder that absence is weaker evidence than a recorded change.
`integrations/mcp_smoke.py` checks a running server end to end (initialize, list tools, call each one against known
facts), and `smoke_test.py` runs it against the installed `apidrift-mcp`.

Known limit of `check_code` and the checker: methods are tracked on objects assigned to a name
(`df = pd.DataFrame(); df.append(...)`), not on chained calls (`pd.DataFrame().append(...)`).

## Use it in CI

* **Lockfiles.** `apidrift-check src/` picks up `uv.lock`, `poetry.lock`, `pylock.toml` or `Pipfile.lock` in the current
  folder automatically. You can also pass `--lock FILE`, `-r requirements.txt` or `--pin pkg==ver`. Directories are
  scanned recursively.
* **GitHub code scanning.** `--sarif apidrift.sarif` writes SARIF 2.1.0. `integrations/github-action/` has a composite
  action that downloads the data once (cached), runs the checker and uploads the findings. The example workflow
  runs it on pull requests that touch Python files or lockfiles. **The action is experimental**: it has not yet run on
  a live GitHub runner (`.github/workflows/tests.yml` exercises it end to end, including hostile inputs, once the repo
  is on GitHub). Workflow inputs reach the checker only through environment variables, are validated by
  `run_action.py`, and are passed as an argument list, never as shell text; extra flags are limited to `options:`
  (`--strict`, `--nearest`, `--no-deprecations`). Downloaded data (and data restored from the cache) is checked
  against the SHA-256 hashes in `DATA_MANIFEST.json` before the checker runs.
* **Cost.** The checker reads only the Parquet row groups of the pinned libraries and the signatures they
  reference. A cold start (one file, one pin) takes about {{perf_cold_cpu}} s of CPU and {{perf_rss}} MB of memory. Checking
  all {{perf_big_files}} files of pandas against its installed dependencies took {{perf_big_cpu}} s of CPU
  ({{perf_big_wall}} s wall on a shared two-core machine). See `validation/checker_performance.json`.
* **pre-commit.** `.pre-commit-hooks.yaml` defines an `apidrift-check` hook. Set `APIDRIFT_DATA` to the data folder.
* **Suppression.** Add `# apidrift: ignore` to a line to silence findings on it.

## VersionProbe benchmark

`benchmark/versionprobe.parquet` holds {{probe_pairs}} pairs ({{probe_projects}} libraries). `versionprobe_lite.parquet`
is a {{probe_lite}}-pair subset with at most 3 pairs per library, spread across years. Each pair is one snippet that is
`valid` for the release before a real breaking change and `invalid` for the release after it. Both halves of every pair
were verified with `apidrift-check`. Each item has `change_released`, the date of the breaking release, so you can
split results at a model's training cutoff. A constant answer scores 50%; `pair accuracy` (both halves right) is the
stricter metric.

```bash
pip install openai anthropic pandas pyarrow
python eval_versionprobe.py --data benchmark/versionprobe_verified.parquet --provider openai --model <model> --cutoff 2024-10-01
# local models: --provider openai --base-url http://localhost:8000/v1 (vLLM, Ollama, llama.cpp server)
```

Results are appended to `results/leaderboard.md`.

### VersionProbe-verified (the default evaluation split) and a first baseline

**Use `benchmark/versionprobe_verified.parquet` to evaluate and rank models.** It holds the {{ver_pairs}} pairs
({{ver_items}} items, {{ver_libs}} libraries) whose labels were confirmed at runtime by the two benchmark audits; the
evidence for every pair is in `benchmark/versionprobe_verified_evidence.csv`. The verification was done by AI agents
isolated from the pipeline (both releases installed, every name resolved and every keyword bound), without human
adjudication, and the pairs come from audited samples, so they may be slightly easier than the benchmark as a whole.
The full {{probe_pairs}}-pair benchmark is a larger, noisier research resource (about 8.5% label error measured).

| Claude (`claude-opus-5-5`), one question at a time | Item accuracy | Pair accuracy |
|---|---|---|
| From its own knowledge | {{mb_a_item}} (CI {{mb_a_item_ci}}) | {{mb_a_pair}} |
| With API history retrieved from this dataset | {{mb_b_item}} (CI {{mb_b_item_ci}}) | {{mb_b_pair}} |
| Constant answer | 50% | 0% |

Without help the model accepted most valid snippets ({{mb_a_valid}}) but caught only {{mb_a_invalid}} of invalid
ones, and did worse on changes released after its training cutoff ({{mb_a_after}}, n={{mb_after_n}}). The retrieved
history lists, for every name and keyword in the snippet, the releases in which it existed or was accepted; it gives no
label and doesn't say what changed. Because that evidence answers the question once read correctly, the second row is
close to an upper bound for retrieval; the code-repair experiment below measures the effect on writing code. Prompts, raw
answers and scores are in `validation/model_baseline/`.

### Code repair: does the dataset help a model fix real breaks?

A preregistered experiment (`validation/code_repair/PREREGISTRATION.md`, task list hashed before any model output):
{{rp_n}} real breaking changes from {{rp_libs}} libraries, each given as code written for the old release with the
instruction to update it for the new release while keeping its purpose. Each answer was checked at runtime with
only the new release installed (every name resolves and every call's arguments bind to its signature; the call is not executed, and
missing required arguments are not checked), and a separate model, blind to the
condition, judged whether it preserved the snippet's intent. "Both" means API-compatible and intent-preserving
under this protocol; it is not a behavioural test, and the intent check is an AI judgement, not human verification.

| Claude (`claude-opus-5-5`) | Closed book | + documentation of the new release | + PyPI API Drift history |
|---|---|---|---|
| API-compatible with the new release | {{rp_a_valid}} | {{rp_b_valid}} | {{rp_c_valid}} |
| Intent preserved (AI judge) | {{rp_a_faith}} | {{rp_b_faith}} | {{rp_c_faith}} |
| **Both** | **{{rp_a_succ}}** (CI {{rp_a_succ_ci}}) | **{{rp_b_succ}}** (CI {{rp_b_succ_ci}}) | **{{rp_c_succ}}** (CI {{rp_c_succ_ci}}) |

API history beat closed book (exact McNemar p={{rp_ca_p}}) and documentation (p={{rp_cb_p}}). The documentation
condition is an API reference page for the imported module(s), generated with griffe from the new release's wheel.
The gain is moderate and comes from validity; a few more history-guided answers were judged unfaithful, mostly where
a "still available as" hint pointed to a same-named but different object. One model, an AI judge, and a check of
names and signatures rather than behaviour are the main limits. Everything is in `validation/code_repair/`.

**Full-binding sensitivity check (post hoc, not preregistered).** The preregistered check uses `bind_partial`, so a call
missing a required argument could pass. We re-verified every answer in fresh environments with full binding
(`inspect.signature(...).bind`). The re-run reproduced every preregistered verdict; full binding removed {{rs_lost_total}}
successes ({{rs_a_lost}} closed book, {{rs_b_lost}} documentation, {{rs_c_lost}} API Drift), giving {{rs_a_succ}} / {{rs_b_succ}} / {{rs_c_succ}}
(exact McNemar p={{rs_ca_p}} vs closed book, p={{rs_cb_p}} vs documentation). See `results_strict_binding.json`; reproduce
with `pipeline/verify_repair.py ... --strict` and `pipeline/score_repair_strict.py`.

**Replication across models.** Both experiments were then repeated, unchanged, with three more Claude models (same
items and tasks, same runtime check, same blind judge):

| Model | VersionProbe-verified item / pair accuracy | Repair: closed book | + docs | + PyPI API Drift | API Drift vs docs (exact McNemar) |
|---|---|---|---|---|---|
| `claude-opus-5-5` (preregistered) | {{mm_opus_pi}} / {{mm_opus_pp}} | {{mm_opus_a}} | {{mm_opus_b}} | **{{mm_opus_c}}** | p={{mm_opus_cb_p}} |
| `claude-sonnet-5-5` | {{mm_sonnet_pi}} / {{mm_sonnet_pp}} | {{mm_sonnet_a}} | {{mm_sonnet_b}} | **{{mm_sonnet_c}}** | p={{mm_sonnet_cb_p}} |
| `claude-haiku-5-5` | {{mm_haiku_pi}} / {{mm_haiku_pp}} | {{mm_haiku_a}} | {{mm_haiku_b}} | **{{mm_haiku_c}}** | p={{mm_haiku_cb_p}} |
| `claude-fable-5-1` | {{mm_fable_pi}} / {{mm_fable_pp}} | {{mm_fable_a}} | {{mm_fable_b}} | **{{mm_fable_c}}** | p={{mm_fable_cb_p}} |

With API history every model reached {{mm_haiku_c}}–{{mm_fable_c}}, whatever its closed-book score ({{mm_haiku_a}}–{{mm_opus_a}};
without help the smallest model left {{mm_haiku_unch_a}} of snippets unchanged). API history beat documentation for all
{{mm_n_models}} models, significantly for {{mm_sig_b}} (the exception: p={{mm_fable_cb_p}}). The models are of one family,
so this replicates the protocol rather than establishing the effect across model families. Answers, judge verdicts and per-task scores are in `validation/multi_model/`.

## What's in `data/`

The six tables are published separately from the code (they are too large for the repository), on Hugging Face:
<https://huggingface.co/datasets/pushkar87dg/pypi-api-drift>. Download them with

```bash
pip install huggingface_hub
huggingface-cli download pushkar87dg/pypi-api-drift --repo-type dataset --include "data/*" --local-dir .
export APIDRIFT_DATA=$PWD/data
```

and check them against the pinned hashes before use:

```bash
python verify_data.py /path/to/data     # prints OK / MISMATCH / MISSING per file; exit 1 on any problem
```

`python smoke_test.py /path/to/data` goes one step further: it installs this release into a fresh virtual environment
(non-editable, declared dependencies only), runs the test suite including the data-level regression tests, verifies
the data and runs the installed `apidrift-check` on `examples/llm_generated_example.py`. The result of our own run is in
`validation/clean_install.json`.

| File | Rows | One row per |
|---|---|---|
| `releases.parquet` | {{releases}} | analysed release: wheel used, module/symbol counts, bump type, breaking counts |
| `api_history.parquet` | {{api_history_rows}} | public symbol path × (kind, signature, deprecation) × contiguous range of releases |
| `changes.parquet` | {{change_events}} | change between consecutive releases (added, removed, parameter-level changes, …) |
| `signatures.parquet` | {{signatures}} | distinct signature (JSON list of parameters with kind, default and annotation) |
| `top_level_modules.parquet` | {{modules}} | public module path → distribution (any analysed release), used to map imports to PyPI projects |
| `module_coverage.parquet` | {{cov_rows}} | module × coverage state × contiguous range of releases (see below) |

### Coverage: when absence is not proof

Static analysis cannot see everything, so a name missing from `api_history` does not prove that it does not exist.
`module_coverage.parquet` records, for every module and range of releases, its `source` (`py`, `py+pyi`, `pyi` = stub
only, `compiled`, `unparseable`), whether it is `dynamic` (an open module `__getattr__`, `globals()` injection or a
runtime `__all__`), whether it `imports_compiled` names, and which classes have decorator-generated constructors.
Every `api_history` row has a `coverage_status` summarizing its module over the row's range:

| `coverage_status` | Share of rows | Meaning |
|---|---|---|
| `static` | {{cov_static}} | parsed from Python source; absence is strong evidence |
| `compiled_boundary` | {{cov_compiled_boundary}} | the module is compiled or binds names from compiled code |
| `dynamic_module` | {{cov_dynamic_module}} | the module can serve names the dataset does not list |
| `stub_only` | {{cov_stub_only}} | only a `.pyi` stub ships; the stub may disagree with the runtime |

The coverage pass flags a module as dynamic on any use of `globals()`, which is broader than the extractor's rule, so
it errs toward warning.

Key columns of `changes.parquet`:

* `change_type`: `removed`, `added`, `param_removed`, `param_renamed`, `required_param_added`,
  `became_keyword_only`, `became_positional_only`, `default_removed`, `vararg_removed`, `varkw_removed`,
  `positional_reordered`, `kind_changed`, plus non-breaking `optional_param_added`, `default_changed`,
  `annotation_changed`, `return_annotation_changed`, `deprecation_marker_added`, `became_dynamic`, `left_public_api`.
* `breaking`: True when callers written for the old release can fail on the new one.
* `confidence`: `high`, or `medium` when the evidence is weaker (a member missing from a class whose bases live
  in another package, symbols known only from type stubs, removed `**kwargs`).
* `representative`: one row per underlying change. The same method change shows up under every subclass and
  re-export path; filter on this to count real changes.
* `parent_removed`: True when the whole enclosing class or module was removed in the same release.
* `deprecated_before`: True when the symbol carried a deprecation marker in an earlier release.
* `detail`: e.g. `old -> new` for renames, `still available as X` when a removed path survives elsewhere.

## Headline findings

* {{patch_pct}} of patch releases ({{patch_transitions}} analysed) and {{minor_pct}} of minor releases break
  their public API, compared with {{major_pct}} of major releases. {{projects_with_breaking_in_patch}} libraries shipped at least one
  breaking patch release.
* Only {{depr_pct}} of removed functions, classes and methods carried a detectable deprecation marker in an
  earlier release.
* {{breaking_2025}} high-confidence breaking changes were released in 2025–2026, after the training cutoff of
  many current code models.

* Among the {{sv_projects}} libraries whose PyPI description says they follow Semantic Versioning, {{sv_patch_pct}}
  of stable (≥1.0) patch releases and {{sv_minor_pct}} of stable minor releases contain a high-confidence breaking
  change ({{sv_patch_proj}} of {{sv_patch_projects}} such projects at least once). These are candidate SemVer violations;
  rarely used members count too. {{sv_calver}} libraries use calendar versioning, {{sv_not}} say they don't follow
  SemVer, and {{sv_unknown}} state no policy on PyPI. The classification and every reviewed match are in
  `validation/versioning_policy.csv`.

## How it was built

1. **Selection.** The top {{top_n}} projects by PyPI downloads (hugovk/top-pypi-packages, {{top_date}}). For each one we
   take final releases uploaded since 2021-01-01, capped at the 80 most recent, using one wheel per release
   (pure-Python preferred, else CPython manylinux x86_64).
2. **Range reads.** Only the ZIP central directory and the `.py`/`.pyi` members are downloaded, and members
   unchanged since the previous release (same CRC) are reused: {{tr_share}} of the wheels' bytes in our measurement.
3. **Static extraction.** We parse with Python's `ast`, without importing anything. We resolve `__all__`, re-exports, star imports,
   relative imports, inherited members from classes in the same package, conditional and `try/except` imports,
   module `__getattr__` lazy loading (including names it explicitly rejects), PEP 484 `__x` positional-only
   parameters, `@overload` stubs, and runtime source over `.pyi` stubs.
4. **Accessibility check.** A symbol that leaves the public API is recorded as `removed` only when it is
   provably no longer reachable. If it's still reachable (dropped from `__all__`, re-exported from elsewhere,
   lazily served) or can't be decided statically (compiled modules, dynamically generated namespaces), it is
   recorded as `left_public_api` and kept in the history.
5. **Coverage.** A second pass records per-module coverage (above).
6. **Integrity.** `pipeline/validate_dataset.py` runs {{checks_n}} checks on every build (keys, version order, interval
   consistency, signature references, change/release consistency, label domains, benchmark provenance, and a recount of
   every number in `stats.json`); this build passes all of them (`validation/dataset_checks.json`). `tests/` holds
   {{tests_n}} regression tests, including every false-positive pattern the audits found.
7. **Validation.** Pipeline-isolated AI agents re-checked random samples of claimed breaking changes by installing both
   releases and inspecting them at runtime. See below.

## Validation

All studies below are **pipeline-isolated, AI-agent-conducted runtime audits**. The independence comes from the agent
never seeing the pipeline, the data or the checker, not from a separate human researcher, and disputed cases were not
adjudicated by humans. The audits were carried out by an AI agent (an LLM with shell access), not by human reviewers. It had no access to the
pipeline or the dataset files, only to a CSV of sampled claims. For each claim it installed both releases in fresh
virtual environments and checked the claim at runtime, falling back to reading the released source when a release
could not be installed. In round 3, {{audit3_runtime}} of 100 claims were checked at runtime and {{audit3_source}} from source.
Treat these figures as a systematic, reproducible spot check, not as human-annotated ground truth.

| Audit | Sample | Result |
|---|---|---|
| 1 (first version of the pipeline) | 60 claims, 32 libraries | 37% false positives → fixed |
| 2 (after fixes) | 60 claims, 32 new libraries | 25% false positives → fixed |
| 3 (released pipeline, libraries never used for tuning) | {{audit3_n}} claims, {{audit3_libs}} libraries | high confidence: **{{precision_high}}** precision ({{precision_high_partial}} incl. partially correct), n={{audit_n_high}}; medium: {{precision_medium}}, n={{audit_n_medium}} |

By category, round-3 high-confidence precision was {{prec_removed}} for removals (n={{n_removed_audit}}, 95% CI {{ci_removed}}) and
{{prec_sig}} for signature changes (n={{n_sig_audit}}, 95% CI {{ci_sig}}). All {{n_kind_audit}} sampled kind changes were wrong, and they are now `medium`.
Medium-confidence precision was {{precision_medium}} (95% CI {{ci_medium}}, n={{audit_n_medium}}).

After round 3, two rules moved to `medium`: removed `*args`/`**kwargs`, which are often only forwarded to a base class,
and classes turned into plain attributes, which are often `Name = OtherClass` aliases. Re-scored against the released
tables, {{precision_high_final}} of the remaining high-confidence round-3 claims are confirmed (n={{audit_n_high_final}}).
That figure is partly in-sample, so the out-of-sample estimate stays {{precision_high}}. Use `confidence == "high"` when
you need the most reliable claims, and treat `medium` as candidates. The audit samples, verdicts, evidence and protocol are in
`validation/`. Errors that remain usually involve names created at
runtime in ways static analysis can't see (decorators that rename parameters, attributes set via `setattr`).

### VersionProbe labels, checked at runtime

**Fresh sample (out of sample).** After the benchmark was rebuilt, a new sample of 400 pairs was drawn from libraries
never used in any audit, study or fix ({{b2_libs}} libraries). {{b2_ok}} of the {{b2_n}} decided pairs were labelled
correctly: **{{b2_prec}}** (95% CI {{b2_ci}}); every pair was decided.

| Change type | n | Correct |
|---|---|---|
| Symbol removed | {{b2_removed_n}} | {{b2_removed_prec}} (CI {{b2_removed_ci}}) |
| Parameter removed | {{b2_param_removed_n}} | {{b2_param_removed_prec}} (CI {{b2_param_removed_ci}}) |
| Parameter renamed | {{b2_param_renamed_n}} | {{b2_param_renamed_prec}} (CI {{b2_param_renamed_ci}}) |

"Invalid after" held {{b2_after_invalid}} of the time and "valid before" {{b2_before_valid}}. Nearly every error is a name
that looks present statically but doesn't exist at runtime: removed with `del` after use (versioneer's
`get_versions`), defined only in a Python-2 or `if False:` branch, present only in a `.pyi` stub, or a class attribute
read as a module attribute. One came from a bogus `0.0.0` release ordered before real ones. The `del` case is fixed in
the extractor and applies from the next refresh. Per-pair results are in `validation/benchmark_check2/`.

**First sample.** A separate AI agent verified {{bench_check_n}} randomly sampled VersionProbe pairs, one per library and from libraries not
used in the precision audits. It installed both releases and resolved every name and keyword in the snippet.
{{bench_check_ok}} pairs ({{bench_check_prec}}, 95% CI {{bench_check_ci}}) were labelled correctly. "Invalid after" held
{{bench_after_invalid}} of the time, but "valid before" only {{bench_before_valid}}. Most wrong labels came from snippets that read
instance-only attributes through the class (`Class.attr`), and from names that exist only for type checkers
(`if TYPE_CHECKING:` blocks, `.pyi`-only aliases). Both are now excluded and the benchmark was rebuilt. {{bench_wrong_removed}} of the {{bench_wrong_total}} wrong pairs dropped out, and
{{bench_after_ok}} of the {{bench_after_n}} audited pairs still in the benchmark are correct ({{bench_after_prec}}). That figure is in-sample, so the
out-of-sample estimate stays {{bench_check_prec}}.

### Recall

A separate AI agent introspected both releases of 30 random release pairs at runtime and listed every breaking change
it found (99 in total). The dataset contained:

| View | Before the fixes these studies prompted | After |
|---|---|---|
| Changes within this dataset's public-API definition (excluding incidental imports and `*args` pass-throughs) | {{rec_def_b}} ({{rec_def_k_b}}/{{rec_def_n_b}}, 95% CI {{rec_def_ci_b}}) | {{rec_def_a}} ({{rec_def_k_a}}/{{rec_def_n_a}}) |
| All runtime-visible changes except `trio` | {{rec_nt_b}} ({{rec_nt_k_b}}/{{rec_nt_n_b}}) | {{rec_nt_a}} ({{rec_nt_k_a}}/{{rec_nt_n_a}}) |
| All runtime-visible changes | {{rec_all_b}} ({{rec_all_k_b}}/{{rec_all_n_b}}) | {{rec_all_a}} ({{rec_all_k_a}}/{{rec_all_n_a}}) |

The third row is dominated by `trio` 0.23: 72 of its wrapper methods were generated at runtime in 0.22, which static
analysis can't see. The other misses are mostly names a module imported for its own use, such as
`from .util import timedelta_seconds`. They are reachable at runtime, but this dataset doesn't count them as public API.

### Comparison with griffe and AexPy

The same 30 release pairs were analysed with griffe 2.4 (static) and AexPy 0.4.3 (imports both releases), and every
method was scored with the same matching rule against the runtime reference set
(`validation/tool_comparison/`):

| Ground-truth breaking changes detected | PyPI API Drift | griffe | AexPy |
|---|---|---|---|
| Within this dataset's public-API definition | {{t_ours_def}} | {{t_griffe_def}} | {{t_aexpy_def}} |
| All runtime-visible except `trio` | {{t_ours_nt}} | {{t_griffe_nt}} | {{t_aexpy_nt}} |
| All runtime-visible | {{t_ours_all}} | {{t_griffe_all}} | {{t_aexpy_all}} |
| Median time per pair (extract both releases) | {{t_ours_sec}} s (range reads, nothing installed) | {{t_griffe_sec}} s (downloads wheels) | {{t_aexpy_sec}} s (installs both releases) |

AexPy sees runtime-generated API that static tools can't, at the cost of installing and importing every release (one
pair ran out of memory). Within the shared definition the three are close. The reference set turned out to be incomplete
(e.g. removed `bs4` entity constants that this dataset and AexPy both report), so the tools' extra claims are
reported as agreement with it ({{t_ours_agree}}/{{t_ours_claims}}, {{t_griffe_agree}}/{{t_griffe_claims}},
{{t_aexpy_agree}}/{{t_aexpy_claims}} public claims), not as precision.

### Downstream pilot: real upgrade failures

A separate AI agent ran the test suites of 12 real projects before and after a dependency upgrade: 9 upgrades that
broke the project and 3 controls. Examples are requests-toolbelt on urllib3 2, Flask-Login on Werkzeug 3, lmfit on
numpy 2, dataset on SQLAlchemy 2 and jinjasql on Jinja2 3.1. It recorded {{ds_api_b}} distinct API-caused failures.
`apidrift-check` was then run on the same code with the same pinned environments.

| | Before the fixes | After |
|---|---|---|
| Findings that point at a real break | {{ds_findok_b}}/{{ds_find_b}} | {{ds_findok_a}}/{{ds_find_a}} |
| Findings on the old environment or on the controls | {{ds_before_b}} / {{ds_cflag_b}} of {{ds_ccases_b}} | {{ds_before_a}} / {{ds_cflag_a}} of {{ds_ccases_a}} |
| API failures predicted | {{ds_pred_b}}/{{ds_api_b}} | {{ds_pred_a}}/{{ds_api_a}} |
| Breaking upgrades flagged | {{ds_bflag_b}}/{{ds_bcases_b}} | {{ds_bflag_a}}/{{ds_bcases_a}} |

{{ds_priv_b}} of the {{ds_api_b}} failures were caused by downstream code using private names (`flask._app_ctx_stack`,
`click._bashcomplete`, `marshmallow.utils._Missing`). These are outside the tracked public API by design. One more came
from a method call on a function parameter whose type the checker can't infer (`df.iteritems()`).

### Unseen upgrades: the checker frozen, new cases

The checker and data were frozen (hashes in `validation/downstream2/FROZEN_sha256.txt`) before two agents, who never
ran the checker, collected {{u_f_cases}} new upgrade cases: {{u_breaks}} expected breaks and {{u_controls}} controls
over {{u_libs}} libraries, including compiled packages (numpy, scipy, lxml, aiohttp, cryptography) and lazily loaded
APIs (pydantic 2, numpy 2, marshmallow 4). {{u_f_checked}} could be scored; the other three pin releases the dataset never
analysed, and the checker said so instead of guessing.

| Frozen checker | Result |
|---|---|
| Findings that point at real incompatible code | {{u_f_findok}}/{{u_f_find}} ({{u_f_fprec}}, CI {{u_f_fprec_ci}}) |
| API-caused test failures predicted (per failure) | {{u_f_pred}}/{{u_f_fail}} ({{u_f_rec}}, CI {{u_f_rec_ci}}) |
| Truly breaking upgrades predicted completely | {{u_f_full}}/{{u_f_brk}} |
| Upgrades with no API failure that got findings | {{u_f_flag_nonbrk}}/{{u_f_nonbrk}} (two of them real but untested incompatibilities) |
| Findings on the old environments | {{u_f_before}} |

Misses: names removed behind module `__getattr__` deprecation shims (`numpy.bool8`, NetworkX), names only ever served
by such shims (`numpy.float`), a module that now re-exports a split-out package (`lxml.html.clean`), static misreads
(`scipy.integrate.simps`, `matplotlib.cm.get_cmap`), a dunder name (skipped by design), a call through a subclass's
`super().__init__`, and one checker bug: modules removed in later releases were not mapped to their distribution
(`pkg_resources` in setuptools 82). That bug is fixed; re-scored in-sample with the fix and the data corrections, the
checker predicts {{u_a_pred}}/{{u_a_fail}}, and {{u_a_findok}} of its {{u_a_find}} findings match what the agents observed at runtime
(most of the rest are uses of modules removed in the new release, such as `pandas.util.testing` in pandas 2.0, in test
code the agents did not list).

### Corrections in this release

The new integrity checks, tests and studies found four defects in the first build, fixed here:
{{po_fixed}} renames of positional-only parameters were marked breaking (callers can't pass them by name);
{{gen_events}} breaking constructor-signature events concerned classes whose constructor a decorator builds
(dataclasses, attrs, factories; {{gen_classes}} such class definitions were found). They used an inherited base-class
signature and are now `medium`, with the signature dropped from {{sig_dropped}} history rows;
{{clamped}} history rows of two projects extended over releases too large to analyse; and the module map used only each
library's latest release. `validation/patch_log.json` lists the counts.

**Version 1.2 was re-audited** on {{a4_n}} fresh claims from {{a4_libs}} libraries never used before, stratified
into claims new in v1.2 and unchanged ones: {{a4_prec}} overall (CI {{a4_ci}}), {{a4_new_prec}} on new claims,
{{a4_old_prec}} on unchanged ones, {{a4_w_prec}} weighted by stratum size (`validation/audit4/`). By verification
method: 92.0% (115/125) for claims checked at runtime and 91.4% (32/35) for claims checked from source. (In round 3:
97.1% at runtime, 67/69, and 36.4% from source, 4/11 — packages that could not be installed.)

**Version 1.2.** The unseen-upgrade misses traced to three over-cautious rules, now fixed: any use of `globals()`, even
a read inside `__dir__`, marked a module as dynamic and hid every removal in it (numpy 2.0's `bool8`, `float`); a
runtime-built `__all__` blocked attribute checks although it only affects star exports (scipy's `simps`); and
star-import chains were abandoned instead of followed (networkx). With these and the `del` fix the whole dataset was
re-extracted: high-confidence breaking changes went from {{v12_high_before}} to {{v12_high_after}} ({{v12_removed_gain}}
more removals found), {{v12_dropped_pairs}} VersionProbe pairs whose change no longer holds were dropped (including
{{v12_fresh_wrong_dropped}} the fresh audit had judged wrong; `validation/v12_dropped_pairs.csv`), and the checker now
checks `super().__init__(...)` and other `super()` calls against library base classes. The round-3 audit re-scored on
the new tables keeps {{precision_high_final}} precision (n={{audit_n_high_final}}); all after-fix figures are in-sample.

**Version 1.3.** Two more defects, found by the third review: {{ph_releases}} placeholder releases ({{ph_projects_list}};
version `0.0.0` or `0.0` uploaded after real releases) had been sorted first and diffed against unrelated builds, and
are removed ({{ph_changes}} change events and {{ph_pairs}} VersionProbe pairs dropped, including 2 the fresh benchmark
audit had judged wrong; `validation/placeholder_releases.json`), with selection and a new integrity check excluding
them. And names that a module `__getattr__` serves from module-level containers (deprecation shims such as numpy's
`__deprecated_attrs__`, lazy-import maps) were invisible; they are now followed when the container's filling is static.
A name counts as served only where the lookup returns an object, and as removed only where `__getattr__` explicitly
rejects it (numpy's `__former_attrs__`); a name merely leaving a lazy map is `left_public_api`. This re-extracted
{{v13_projects}} libraries (public symbol paths +{{v13_paths_added}}) and exposed a dating problem: a name that becomes
statically undecidable is carried forward, and its removal is recorded where it becomes provable, which can be later
than the real removal (numpy dropped `float` in 1.24.0 but rejects it explicitly only from 1.24.2). The {{v13_late}}
distinct high-confidence removals with that history are now `medium`, with the earliest possible release in `detail`,
and {{v13_late_pairs}} VersionProbe pairs built on them were dropped. High-confidence breaking changes:
{{v13_high_before}} → {{v13_high_after}} (`validation/v13_changes.json`). The audits above were run on v1.2. Re-scored on
v1.3, the unseen-upgrade study is unchanged ({{u_a_pred}}/{{u_a_fail}}): the numpy case is exactly such an undecidable
release, so the checker still cannot flag `numpy.float` for numpy 1.24.0.

## Limitations

* This is static analysis. Compiled extensions are only visible through their `.pyi` stubs, and dynamically generated
  namespaces are treated as unknown rather than guessed.
* The history covers at most the 80 most recent final releases per library; it is not a complete history since 2021.
  Release ordering trusts version numbers (one bogus `0.0.0` upload was mis-ordered). {{skipped_releases}} releases of very large packages
  (over 8,000 modules, e.g. ansible) were skipped.
* Inherited members from classes in *other* packages aren't resolved, so these cases are marked `medium`.
* Behavioural changes with identical signatures aren't captured.
* Recall depends on what counts as public. Names a module imports for internal use and objects generated at runtime are
  not tracked (see Recall).
* The checker only infers types for objects created in the same scope (`df = pd.DataFrame(...)`) and follows
  `super()` calls into library base classes. Calls on function parameters and return values are not resolved, and
  private (`_name`) APIs are never flagged.
* VersionProbe labels were generated from this dataset. Runtime checks of 600 pairs measured their correctness
  (see above); the rest have not been run.
* All audits were done by AI agents isolated from the pipeline, without human adjudication, on samples of 60–400
  items, so figures carry the intervals shown.
* A tool that imports releases (AexPy) finds many more runtime-generated changes than this static dataset.
* The model experiments use one model; the code-repair study checks names and signatures at runtime and intent with an AI judge, not program behaviour.
* "No findings" does not mean an upgrade is safe: the checker is a conservative warning tool (see the unseen-upgrade study).

## Citation

See `CITATION.cff`.

## License

Data: CC BY 4.0. Code: Apache-2.0. See `LICENSE` and `LICENSE-DATA.md`. Copyright 2026 Pushkar Devanahalli Gopalakrishna.
