"""Score a frozen apidrift-check against real upgrade failures (unseen-upgrade study).

Like compare_downstream.py, plus per-upgrade outcomes. The checker module and data are passed explicitly so the
study is scored with the versions frozen before its cases were collected.
Usage: python compare_downstream2.py CHECKER_PY DATA_DIR CASES_DIR OUT_JSON

Per failure: an API-caused test failure is predicted when an "after" finding names the same API (leaf match) or
the same source line. The whole source tree of each case is checked (tests included). Per upgrade (break cases with >= 1 API failure): full = every API failure predicted;
partial = some; missed = none. Controls and break cases without API failures: correct when the checker reports
nothing. Upgrade-level classification: predicted breaking <=> at least one "after" finding."""
import importlib.util, json, math, os, re, sys
import pandas as pd

CHK, D, V, OUT = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("frozen_check", CHK)
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
store = chk.Store(D)


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


cases = pd.read_csv(f"{V}/cases.csv")
fails = pd.read_csv(f"{V}/failures.csv")
masked = pd.read_csv(f"{V}/masked.csv") if os.path.exists(f"{V}/masked.csv") else pd.DataFrame(columns=["case_id"])
leaf = lambda s: re.sub(r"\.__init__$", "", str(s)).split(".")[-1]  # Class.__init__ and Class name the same API
out = {"checker": CHK, "cases": []}
for c in cases.itertuples():
    folder = f"{V}/{c.case_id}"
    if not os.path.isdir(folder):
        continue
    lib = norm(c.library)
    res = {}
    for env in ("before", "after"):
        fz = f"{folder}/freeze_{env}.txt"
        pins = chk.parse_pins(None, fz) if os.path.exists(fz) else {}
        pins[lib] = c.old_version if env == "before" else c.new_version  # the case defines the library version
        cov, found = {}, []
        for f in chk.python_files([folder]):
            rel = os.path.relpath(f, folder)  # the whole tree is checked, tests included, as a CI run would
            try:
                src = open(f, encoding="utf-8", errors="replace").read()
                for i in chk.check_source(src, pins, store, coverage=cov):
                    if i["kind"] != "deprecated" and i["project"] == lib:
                        found.append({"file": rel, "line": i["line"], "symbol": i["symbol"], "kind": i["kind"],
                                      "message": i["message"]})
            except SyntaxError:
                pass
        res[env] = (found, cov.get(lib))
    after, cov_after = res["after"]
    before, _ = res["before"]
    api = fails[(fails.case_id == c.case_id) & (fails.category == "api")]
    truth = pd.concat([api, masked[(masked.case_id == c.case_id)]], ignore_index=True)

    def matches(fr, a):
        same_line = os.path.basename(str(fr.file)) == os.path.basename(a["file"]) and int(fr.line) == a["line"]
        return same_line or leaf(fr.library_api) == leaf(a["symbol"])

    predicted = [any(matches(fr, a) for a in after) for fr in api.itertuples()]
    confirmed = [any(matches(fr, a) for fr in truth.itertuples()) for a in after]
    n_api = len(api)
    if c.expected == "break" and n_api:
        outcome = "full" if all(predicted) and all(confirmed) else ("partial" if any(predicted) else "missed")
    else:
        outcome = ("correct_silent" if not after else
                   "flagged_real_but_untested" if all(confirmed) else "false_alarm")
    out["cases"].append({
        "case_id": c.case_id, "downstream": c.downstream, "library": c.library,
        "upgrade": f"{c.old_version} -> {c.new_version}", "expected": c.expected,
        "l_kind": getattr(c, "l_kind", None), "l_dynamic": getattr(c, "l_dynamic", None),
        "coverage_note": cov_after, "api_failures": n_api, "api_failures_predicted": int(sum(predicted)),
        "other_failures": int(((fails.case_id == c.case_id) & (fails.category != "api")).sum()),
        "findings_after": len(after), "findings_after_confirmed": int(sum(confirmed)),
        "findings_before": len(before), "outcome": outcome,
        "api_failures_on_private_names": [str(fr.library_api) for fr in api.itertuples()
                                          if any(s.startswith("_") for s in str(fr.library_api).split("."))],
        "findings": after})

cs = [x for x in out["cases"] if not (x["coverage_note"] and "not checked" in x["coverage_note"])]
skipped = [x["case_id"] for x in out["cases"] if x not in cs]
k_pred = sum(x["api_failures_predicted"] for x in cs); n_fail = sum(x["api_failures"] for x in cs)
k_conf = sum(x["findings_after_confirmed"] for x in cs); n_find = sum(x["findings_after"] for x in cs)
truly_breaking = [x for x in cs if x["api_failures"] > 0]
not_breaking = [x for x in cs if x["api_failures"] == 0]
tp = sum(1 for x in truly_breaking if x["findings_after"] > 0)
fp = sum(1 for x in not_breaking if x["findings_after"] > 0)
out["summary"] = {
    "cases": len(out["cases"]), "cases_checked": len(cs), "cases_not_checked": skipped,
    "per_failure_recall": {"predicted": k_pred, "api_failures": n_fail,
                           "recall": round(k_pred / n_fail, 4) if n_fail else None, "ci": wilson(k_pred, n_fail)},
    "finding_precision": {"confirmed": k_conf, "findings": n_find,
                          "precision": round(k_conf / n_find, 4) if n_find else None, "ci": wilson(k_conf, n_find)},
    "findings_in_before_env": sum(x["findings_before"] for x in cs),
    "per_upgrade_outcomes": pd.Series([x["outcome"] for x in cs]).value_counts().to_dict(),
    "upgrade_classification": {
        "truly_breaking": len(truly_breaking), "flagged_of_truly_breaking": tp,
        "not_api_breaking": len(not_breaking), "flagged_of_not_api_breaking": fp,
        "accuracy": round((tp + len(not_breaking) - fp) / len(cs), 4) if cs else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / len(truly_breaking), 4) if truly_breaking else None},
    "fully_predicted_upgrades": {"full": sum(x["outcome"] == "full" for x in truly_breaking),
                                 "of": len(truly_breaking)},
    "by_l_kind": {k: {"api_failures": sum(x["api_failures"] for x in cs if x["l_kind"] == k),
                      "predicted": sum(x["api_failures_predicted"] for x in cs if x["l_kind"] == k)}
                  for k in sorted({x["l_kind"] for x in cs if x["l_kind"]})},
    "by_l_dynamic": {k: {"api_failures": sum(x["api_failures"] for x in cs if x["l_dynamic"] == k),
                         "predicted": sum(x["api_failures_predicted"] for x in cs if x["l_dynamic"] == k)}
                     for k in sorted({x["l_dynamic"] for x in cs if x["l_dynamic"]})},
    "api_failures_on_private_names": sum(len(x["api_failures_on_private_names"]) for x in cs)}
json.dump(out, open(OUT, "w"), indent=1, default=str)
print(json.dumps(out["summary"], indent=1, default=str))
for x in out["cases"]:
    print(f'{x["case_id"]:7s} {x["downstream"][:22]:22s} {x["library"][:12]:12s} {x["upgrade"]:22s} {x["expected"]:7s} '
          f'api {x["api_failures"]:2d} pred {x["api_failures_predicted"]:2d} | find {x["findings_after"]:2d} ok '
          f'{x["findings_after_confirmed"]:2d} | before {x["findings_before"]} | {x["outcome"]} {x["coverage_note"] or ""}')
