"""Compare apidrift-check predictions with real test failures after dependency upgrades.
For every case: run the checker over the downstream source twice, pinned to the 'before' and the 'after'
environment (pip freeze), keep findings that involve the upgraded library, and compare the 'after' findings with
the API-caused test failures found by running the test suite.
Usage: python compare_downstream.py DATA_DIR validation/downstream OUT_JSON"""
import glob, json, os, re, sys
import pandas as pd

D, V, OUT = sys.argv[1:4]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    from apidrift.check import Store, check_source, parse_pins, python_files
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from apidrift_check import Store, check_source, parse_pins, python_files

norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
store = Store(D)
cases = pd.read_csv(f"{V}/cases.csv")
fails = pd.read_csv(f"{V}/failures.csv")
masked = pd.read_csv(f"{V}/masked.csv") if os.path.exists(f"{V}/masked.csv") else pd.DataFrame(columns=["case_id"])
rows, out = [], {"cases": []}
for c in cases.itertuples():
    folder = f"{V}/{c.case_id}"
    if not os.path.isdir(folder):
        continue
    lib = norm(c.library)
    res = {}
    for env in ("before", "after"):
        fz = f"{folder}/freeze_{env}.txt"
        pins = parse_pins(None, fz) if os.path.exists(fz) else {lib: c.old_version if env == "before" else c.new_version}
        cov = {}
        found = []
        for f in python_files([folder]):
            if "/tests/" in f or os.path.basename(f).startswith("test_"):
                continue
            try:
                for i in check_source(open(f, encoding="utf-8", errors="replace").read(), pins, store, coverage=cov):
                    if i["kind"] != "deprecated" and i["project"] == lib:
                        found.append({"file": os.path.relpath(f, folder), "line": i["line"], "symbol": i["symbol"],
                                      "kind": i["kind"], "message": i["message"]})
            except SyntaxError:
                pass
        res[env] = (found, cov.get(lib))
    after, cov_after = res["after"]
    before, _ = res["before"]
    api = fails[(fails.case_id == c.case_id) & (fails.category == "api")]
    # a real API failure counts as predicted when a finding names the same API (path suffix match) or the same line
    def hit(fr):
        for a in after:
            same_line = os.path.basename(str(fr.file)) == os.path.basename(a["file"]) and int(fr.line) == a["line"]
            same_api = str(fr.library_api).split(".")[-1] == a["symbol"].split(".")[-1]
            if same_line or same_api:
                return True
        return False
    predicted = [hit(fr) for fr in api.itertuples()]
    truth = pd.concat([api, masked[masked.case_id == c.case_id]], ignore_index=True)
    confirmed = []
    for a in after:
        ok = any((os.path.basename(str(fr.file)) == os.path.basename(a["file"]) and int(fr.line) == a["line"]) or
                 str(fr.library_api).split(".")[-1] == a["symbol"].split(".")[-1] for fr in truth.itertuples())
        confirmed.append(ok)
    private = [str(fr.library_api) for fr in api.itertuples()
               if any(seg.startswith("_") for seg in str(fr.library_api).split("."))]
    out["cases"].append({"case_id": c.case_id, "downstream": c.downstream, "library": c.library,
                         "upgrade": f"{c.old_version} -> {c.new_version}", "expected": c.expected,
                         "coverage_note": cov_after,
                         "api_failures": int(len(api)), "api_failures_predicted": int(sum(predicted)),
                         "other_failures": int(((fails.case_id == c.case_id) & (fails.category != "api")).sum()),
                         "findings_after": len(after), "findings_after_matching_a_failure": int(sum(confirmed)),
                         "findings_before": len(before), "api_failures_on_private_names": private,
                         "masked_or_unexercised_sites": int((masked.case_id == c.case_id).sum()), "findings": after})
cs = [x for x in out["cases"] if not x["coverage_note"] or "not checked" not in x["coverage_note"]]
tot_api = sum(x["api_failures"] for x in cs)
out["summary"] = {
    "cases": len(out["cases"]), "cases_checked": len(cs),
    "api_failures": tot_api, "api_failures_predicted": sum(x["api_failures_predicted"] for x in cs),
    "findings_after": sum(x["findings_after"] for x in cs),
    "findings_after_matching_a_failure": sum(x["findings_after_matching_a_failure"] for x in cs),
    "findings_in_before_env": sum(x["findings_before"] for x in cs),
    "api_failures_on_private_names": sum(len(x["api_failures_on_private_names"]) for x in cs),
    "break_cases_flagged": sum(1 for x in cs if x["expected"] == "break" and x["findings_after"] > 0),
    "break_cases": sum(1 for x in cs if x["expected"] == "break"),
    "control_cases_flagged": sum(1 for x in cs if x["expected"] != "break" and x["findings_after"] > 0),
    "control_cases": sum(1 for x in cs if x["expected"] != "break")}
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out["summary"], indent=1))
for x in out["cases"]:
    print(x["case_id"], x["downstream"], x["library"], x["upgrade"], "| api fails", x["api_failures"], "predicted",
          x["api_failures_predicted"], "| findings", x["findings_after"], "matching", x["findings_after_matching_a_failure"],
          "| before-env findings", x["findings_before"], "|", x["coverage_note"] or "")
