# VersionProbe results

| Model | Split | Condition | Items | Item accuracy | Pair accuracy | Notes |
|---|---|---|---|---|---|---|
| Claude (claude-opus-5-5, via agent with tools forbidden) | versionprobe_verified | closed book | 984 | 58.4% | 26.6% | constant predictor: 50% item, 0% pair |
| same | versionprobe_verified | + API history retrieved from this dataset | 984 | 100.0% | 100.0% | retrieval covers every name and keyword in the snippet; no label given |

Run on 2026-10-21. Details, prompts, raw answers and per-item scores: `validation/model_baseline/`.
