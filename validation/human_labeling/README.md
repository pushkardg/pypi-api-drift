# Human labelling kit

Every validation figure for PyPI API Drift so far comes from AI agents. This kit lets two people check a sample by
hand, so the dataset's precision and the AI auditor's reliability can be measured independently.

## What is in it

| File | Purpose |
|---|---|
| `items.csv` | 200 items: the 160 breaking-change claims of the v1.2 precision audit (`C…`, an unbiased random sample, so the human precision is directly comparable with the AI audit's 91.9%) and 40 random VersionProbe pairs from the 400-pair benchmark audit (`P…`) |
| `labels_A.csv`, `labels_B.csv` | blank sheets, one per labeller |
| `check_item.py` | prints whether a dotted path exists in the current environment, and its signature |
| `ai_key_DO_NOT_OPEN_BEFORE_LABELLING.csv` | the AI auditor's verdicts; open only after both sheets are complete |
| `score_agreement.py` | Cohen's kappa between labellers and with the AI, human precision with a 95% CI, items needing adjudication |

## Instructions for labellers

1. Work independently. Do not discuss items, and do not open the AI key.
2. For each row of `items.csv`, decide whether the claim is right:
   * `correct`: code written for the old release that relies on the named API fails on the new release (for a
     benchmark pair: the snippet works on the old release **and** fails on the new one because of the named API).
   * `incorrect`: the old code keeps working on the new release, or it never worked on the old one, or the failure
     has a different cause.
   * `unsure`: you could not decide in reasonable time (leave a note).
3. Evidence may come from running the code (`how_to_check` gives a recipe using `check_item.py`), release notes,
   changelogs or documentation. Write one line of evidence for every verdict.
4. A name counts as public unless it, or a module on its path, starts with an underscore. A deprecation warning is
   **not** a break; only failure is.
5. Record roughly how many minutes the item took. Expect 2–5 minutes per item with a fast package installer such as
   `uv`.

## After labelling

```bash
python score_agreement.py --json results.json
```

The script lists items where the two labellers disagree or one was unsure. A third person decides those in
`labels_adjudicated.csv` (same columns), then re-run. Report: kappa between labellers, kappa of each with the AI,
human precision for claims and for benchmark pairs, and the items on which the AI was wrong.
