# Code-repair experiment: preregistration (written before any model output was produced)

Date: 2026-10-21. Task list frozen in `tasks.csv` (sha256 below) before prompts were built or models run.

## Question
Does API history from PyPI API Drift help a model migrate code to a newer library release more than ordinary
documentation of that release does?

## Tasks
150 VersionProbe pairs (v1.2) never used in any audit, the verified split or the classification baseline; change
released 2023 or later; at most one task per library and change type. Strata: 60 removals that the dataset records as
"still available as X", 45 renamed parameters, 45 removed parameters. Each task gives a snippet written for the old
release and asks for a version that works with the new release. Tasks whose original snippet turns out to be valid in
the new release at runtime are excluded from scoring (reported).

## Conditions (same model, same instructions, items shuffled, one task at a time)
A. Closed book. B. Documentation: an API reference page for the imported module(s) of the new release, generated
with griffe from the release wheel (independent of this dataset). C. PyPI API Drift: the dataset's API history for
the snippet's names and keywords, the recorded change between the two releases, and new signatures.

## Outcomes
1. Runtime validity (primary, automatic): in an isolated environment with only the new release installed, every
   library name resolves and every keyword binds (`inspect.signature(...).bind_partial`); placeholder values are not
   executed. The candidate must reference the library at least once.
2. Intent preserved: a separate model judge, blind to the condition, sees the original snippet, the target version
   and the candidate, and answers whether the candidate is a faithful migration (same purpose, uses the replacement
   API) rather than deleting or neutering the library use.
3. Success = valid AND intent preserved. Report per condition with Wilson 95% intervals, by stratum, and paired
   McNemar tests C vs A and C vs B.

## Hypotheses
H1: C > A on success. H2: C > B on success. No result will be dropped; all prompts, answers, verdicts and scripts
are published.
9b295971852886b8acc2cc61b183cb2356ea0a684115ccc9584dcd3ac92a069e  repair/tasks.csv
