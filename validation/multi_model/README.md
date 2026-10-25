# Multi-model replication

Both model experiments were repeated, unchanged, after the preregistered `claude-opus-5-5` run (whose files are in
`../model_baseline/` and `../code_repair/`) with `claude-sonnet-5-5`, `claude-haiku-5-5` and `claude-fable-5-1`:

* VersionProbe-verified, closed book: the same 984 items and prompts, the same agent setup (read the questions, write
  answers; no other tools). `versionprobe/answers_a_batch*.csv`.
* Code repair: the same 150 tasks and three conditions (`../code_repair/cond_*`), the same runtime check in fresh
  environments (recorded with both the preregistered `bind_partial` and full `bind`, `repair/verify_strict.jsonl`), and
  the same blind judge model (`claude-opus-5-5`, condition and answering model hidden; `repair/judge_*`).

`results.json` summarizes all four models (scored by `pipeline/score_models.py`), including the share of answers that
returned the snippet unchanged and pooled discordant-pair counts (indicative only: pooling treats model-task pairs as
independent). The replications were not preregistered separately; they reuse the preregistered protocol unchanged.
