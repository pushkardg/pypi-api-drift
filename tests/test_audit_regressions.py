"""Data-level regression tests: every claim the three precision audits judged (validation/sample*.csv and
results*.csv) is re-checked against the released changes table. Skipped when data/ is not present."""
import os
import pandas as pd
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.environ.get("APIDRIFT_DATA") or os.path.join(ROOT, "data")
VAL = os.path.join(ROOT, "validation")
pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "changes.parquet")), reason="data not present (download it and set APIDRIFT_DATA)")


def audited():
    rows = []
    for k, (s, r) in enumerate([("sample.csv", "results.csv"), ("sample2.csv", "results2.csv"),
                                ("sample3.csv", "results3.csv")], 1):
        a = pd.read_csv(os.path.join(VAL, s)).merge(pd.read_csv(os.path.join(VAL, r))[["id", "verdict"]], on="id")
        a["round"] = k
        rows.append(a)
    a = pd.concat(rows, ignore_index=True)
    a["param"] = a.param.fillna("").astype(str)
    return a


@pytest.fixture(scope="module")
def claims():
    a = audited()
    ch = pd.read_parquet(os.path.join(DATA, "changes.parquet"),
                         columns=["project", "to_version", "path", "change_type", "param", "breaking", "confidence",
                                  "representative", "parent_removed"],
                         filters=[("project", "in", sorted(set(a.project)))])
    ch["param"] = ch.param.fillna("").astype(str)
    ch = ch[ch.breaking]
    m = a.merge(ch, on=["project", "to_version", "path", "change_type", "param"], how="left", suffixes=("", "_now"))
    m["still_claimed"] = m.breaking.fillna(False).astype(bool)
    m["high_now"] = m.still_claimed & (m.confidence_now == "high")
    return m.drop_duplicates(["round", "id"])


# claims the audits judged false that the released data still makes at high confidence (documented in
# validation/audits.json: one each in rounds 2 and 3)
KNOWN_REMAINING_HIGH_FP = 2


def test_false_positives_rarely_survive_at_high_confidence(claims):
    fp = claims[claims.verdict == "false_positive"]
    assert len(fp) == 52
    assert int(fp.high_now.sum()) <= KNOWN_REMAINING_HIGH_FP, fp[fp.high_now][["round", "project", "path"]]


@pytest.mark.parametrize("rnd", [1, 2, 3])
def test_confirmed_claims_still_reported(claims, rnd):
    ok = claims[(claims["round"] == rnd) & (claims.verdict == "confirmed")]
    assert ok.still_claimed.mean() >= 0.85, ok[~ok.still_claimed][["project", "path", "change_type"]]


def test_round3_high_confidence_precision_floor(claims):
    r3 = claims[(claims["round"] == 3) & claims.high_now]
    assert (r3.verdict == "confirmed").mean() >= 0.94
