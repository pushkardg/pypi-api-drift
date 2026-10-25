"""Collect every number quoted in README / paper. Usage: python numbers.py RELEASE_DIR OUT_JSON"""
import json, re, sys
import pandas as pd

R, OUT = sys.argv[1:3]
S = json.load(open(f"{R}/stats.json"))
A = json.load(open(f"{R}/validation/audits.json"))
FP = json.load(open(f"{R}/validation/checker_false_alarm_test.json"))
try:
    P = pd.read_parquet(f"{R}/benchmark/versionprobe.parquet")
    probe_pairs, probe_projects = int(P.pair_id.nunique()), int(P.project.nunique())
except Exception:
    probe_pairs, probe_projects = "TBD", "TBD"
pct = lambda x: f"{100 * x + 1e-9:.1f}%"
a3 = A["audit3"]
type_names = {"removed": "Symbol removed", "param_removed": "Parameter removed", "became_keyword_only": "Became keyword-only",
              "required_param_added": "Required parameter added", "param_renamed": "Positional parameter renamed",
              "default_removed": "Default removed", "became_positional_only": "Became positional-only",
              "positional_reordered": "Positional parameters reordered", "kind_changed": "Kind changed"}
rows = " \\\\\n".join(f"{type_names.get(k, k).replace('_', ' ')} & {v:,}"
                       for k, v in S["breaking_representative_high_by_type"].items()) + " \\\\"
N = {
    "projects": S["projects"], "releases": S["releases"], "releases_ok": S["release_status"]["ok"],
    "skipped_releases": S["release_status"].get("skipped_too_many_modules", 0),
    "modules_parsed": S["modules_parsed"], "parse_fail": S["parse_fail_modules"],
    "distinct_symbol_paths": S["distinct_symbol_paths"], "api_history_rows": S["api_history_rows"],
    "change_events": S["change_events"], "signatures": S["signatures"],
    "modules": len(pd.read_parquet(f"{R}/data/top_level_modules.parquet")),
    "breaking_representative": S["breaking_representative"],
    "breaking_representative_high": S["breaking_representative_high"],
    "breaking_2025": S["breaking_representative_high_2025_plus"],
    "release_transitions": S["release_transitions"],
    "patch_pct": pct(S["patch_with_breaking_share"]), "minor_pct": pct(S["minor_with_breaking_share"]),
    "major_pct": pct(S["major_with_breaking_share"]), "patch_transitions": S["patch_transitions"],
    "projects_with_breaking_in_patch": S["projects_with_breaking_in_patch"],
    "depr_pct": pct(S["removals_with_prior_deprecation_marker_share"]),
    "pure_pct": pct(S["pure_python_share"]),
    "upload_min_year": "2021", "snapshot_month": "October 2026", "top_n": "2,000",
    "top_date": "October 2026",
    "audit3_n": a3["sample"], "audit3_libs": a3["libraries"],
    "precision_high": pct(a3["as_audited_high"]["precision"]),
    "precision_high_partial": pct(a3["as_audited_high"]["precision_incl_partial"]),
    "audit_n_high": a3["as_audited_high"]["n"],
    "precision_medium": pct(a3["as_audited_medium"]["precision"]), "audit_n_medium": a3["as_audited_medium"]["n"],
    "precision_high_final": pct(a3["high"]["precision"]), "audit_n_high_final": a3["high"]["n"],
    "probe_pairs": probe_pairs, "probe_projects": probe_projects,
    "fp_files": FP["files"], "fp_pkgs": FP["packages"], "fp_flags": sum(FP["flags"].values()),
    "type_rows": rows,
}
# uncertainty and per-category precision from the round-3 audit
import math
def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{100 * (c - h):.1f}–{100 * (c + h):.1f}%"
s3 = pd.read_csv(f"{R}/validation/sample3.csv").merge(
    pd.read_csv(f"{R}/validation/results3.csv")[["id", "verdict", "method"]], on="id")
hi, me = s3[s3.confidence == "high"], s3[s3.confidence == "medium"]
ok = lambda t: int((t.verdict == "confirmed").sum())
rem, kind = hi[hi.change_type == "removed"], hi[hi.change_type == "kind_changed"]
sig = hi[~hi.change_type.isin(["removed", "kind_changed"])]
N.update({"ci_high": wilson(ok(hi), len(hi)), "ci_medium": wilson(ok(me), len(me)),
          "prec_removed": pct(ok(rem) / len(rem)), "n_removed_audit": len(rem), "ci_removed": wilson(ok(rem), len(rem)),
          "prec_sig": pct(ok(sig) / len(sig)), "n_sig_audit": len(sig), "ci_sig": wilson(ok(sig), len(sig)),
          "n_kind_audit": len(kind), "audit3_runtime": int((s3.method == "runtime").sum()),
          "audit3_source": int((s3.method != "runtime").sum()), "projects_usable": S["projects_with_ok_release"]})
# benchmark label check, recall study, downstream pilot (before = out-of-sample, after = after the fixes they prompted)
def J(path):
    try:
        return json.load(open(path))
    except Exception:
        return None
pc = lambda r: "n/a" if r is None else pct(r)
bc = J(f"{R}/validation/benchmark_check/summary_before_fixes.json")
rb, ra = J(f"{R}/validation/recall/recall_before_fixes.json"), J(f"{R}/validation/recall/recall.json")
db, da = (J(f"{R}/validation/downstream/comparison_before_fixes.json") or {}).get("summary"), \
         (J(f"{R}/validation/downstream/comparison.json") or {}).get("summary")
ci = lambda c: f"{c[0]}–{c[1]}%" if c else "n/a"
if bc:
    N.update({"bench_check_n": bc["decided"], "bench_check_ok": bc["pairs_correct"],
              "bench_check_prec": pc(bc["pair_label_precision"]), "bench_check_ci": ci(bc["ci"]),
              "bench_before_valid": pc(bc["before_valid_rate"]), "bench_after_invalid": pc(bc["after_invalid_rate"])})
ba = J(f"{R}/validation/benchmark_check/summary_after_fixes.json")
if ba:
    N.update({"bench_after_n": ba["still_in_rebuilt_benchmark"], "bench_after_ok": ba["correct_among_them"],
              "bench_after_prec": pc(ba["precision_among_them"]), "bench_wrong_removed": ba["wrong_removed"],
              "bench_wrong_total": ba["wrong_total"]})
for tag, r in (("b", rb), ("a", ra)):
    if r:
        N.update({f"rec_def_{tag}": pc(r["recall_within_definition"]["recall"]), f"rec_def_n_{tag}": r["recall_within_definition"]["n"],
                  f"rec_def_k_{tag}": r["recall_within_definition"]["detected"], f"rec_def_ci_{tag}": ci(r["recall_within_definition"]["ci"]),
                  f"rec_nt_{tag}": pc(r["recall_without_trio"]["recall"]), f"rec_nt_n_{tag}": r["recall_without_trio"]["n"],
                  f"rec_nt_k_{tag}": r["recall_without_trio"]["detected"],
                  f"rec_all_{tag}": pc(r["recall_any_breaking"]), f"rec_all_n_{tag}": r["ground_truth_breaking"],
                  f"rec_all_k_{tag}": int(round(r["recall_any_breaking"] * r["ground_truth_breaking"]))})
for tag, d in (("b", db), ("a", da)):
    if d:
        N.update({f"ds_api_{tag}": d["api_failures"], f"ds_pred_{tag}": d["api_failures_predicted"],
                  f"ds_find_{tag}": d["findings_after"], f"ds_findok_{tag}": d["findings_after_matching_a_failure"],
                  f"ds_before_{tag}": d["findings_in_before_env"], f"ds_priv_{tag}": d["api_failures_on_private_names"],
                  f"ds_bflag_{tag}": d["break_cases_flagged"], f"ds_bcases_{tag}": d["break_cases"],
                  f"ds_cflag_{tag}": d["control_cases_flagged"], f"ds_ccases_{tag}": d["control_cases"]})
# ---- second review: fresh benchmark audit, unseen upgrades, tool comparison, semver, coverage, tests, cost
b2 = J(f"{R}/validation/benchmark_check2/summary.json")
if b2:
    N.update({"b2_n": b2["decided"], "b2_ok": b2["pairs_correct"], "b2_prec": pc(b2["pairs_correct"] / b2["decided"]),
              "b2_ci": ci(b2["ci"]), "b2_unknown": b2["unknown"], "b2_libs": b2["libraries"],
              "b2_before_valid": pc(b2["before_valid_rate"]), "b2_after_invalid": pc(b2["after_invalid_rate"]),
              "b2_wrong_before": b2["wrong_before_only"], "b2_wrong_after": b2["wrong_after_only"]})
    for t, v in b2["by_type"].items():
        N.update({f"b2_{t}_n": v["n"], f"b2_{t}_prec": pc(v["precision"]), f"b2_{t}_ci": ci(v["ci"])})
for tag, f in (("f", "comparison_frozen.json"), ("a", "comparison_after_fixes.json")):
    d = (J(f"{R}/validation/downstream2/{f}") or {}).get("summary")
    if d:
        pf, fp_, uc = d["per_failure_recall"], d["finding_precision"], d["upgrade_classification"]
        N.update({f"u_{tag}_cases": d["cases"], f"u_{tag}_checked": d["cases_checked"],
                  f"u_{tag}_fail": pf["api_failures"], f"u_{tag}_pred": pf["predicted"], f"u_{tag}_rec": pc(pf["predicted"] / pf["api_failures"]),
                  f"u_{tag}_rec_ci": ci(pf["ci"]), f"u_{tag}_find": fp_["findings"], f"u_{tag}_findok": fp_["confirmed"],
                  f"u_{tag}_fprec": pc(fp_["confirmed"] / fp_["findings"]), f"u_{tag}_fprec_ci": ci(fp_["ci"]),
                  f"u_{tag}_before": d["findings_in_before_env"],
                  f"u_{tag}_full": d["fully_predicted_upgrades"]["full"], f"u_{tag}_brk": d["fully_predicted_upgrades"]["of"],
                  f"u_{tag}_acc": pc(uc["accuracy"]), f"u_{tag}_cls_prec": pc(uc["precision"]),
                  f"u_{tag}_cls_rec": pc(uc["recall"]), f"u_{tag}_flag_brk": uc["flagged_of_truly_breaking"],
                  f"u_{tag}_nonbrk": uc["not_api_breaking"], f"u_{tag}_flag_nonbrk": uc["flagged_of_not_api_breaking"]})
    if d and tag == "f":
        cs = J(f"{R}/validation/downstream2/{f}")["cases"]
        N.update({"u_controls": sum(1 for c in cs if c["expected"] == "control"),
                  "u_breaks": sum(1 for c in cs if c["expected"] == "break"),
                  "u_libs": len({c["library"].lower() for c in cs})})
tc = J(f"{R}/validation/tool_comparison/comparison.json")
if tc:
    for key, name in (("ours", "PyPI API Drift (all confidence)"), ("griffe", "griffe (breaking)"),
                      ("aexpy", "AexPy (breaking)")):
        v = tc[name]
        N.update({f"t_{key}_all": f'{v["recall_all"]["detected"]}/{v["recall_all"]["n"]}',
                  f"t_{key}_nt": f'{v["recall_without_trio"]["detected"]}/{v["recall_without_trio"]["n"]}',
                  f"t_{key}_def": f'{v["recall_within_api_drift_definition"]["detected"]}/{v["recall_within_api_drift_definition"]["n"]}',
                  f"t_{key}_pairs": v["pairs"], f"t_{key}_claims": v["public_claims"],
                  f"t_{key}_agree": v["public_claims_in_ground_truth"]})
        if "time" in v:
            N[f"t_{key}_sec"] = v["time"]["median_seconds_per_pair"]
sv = J(f"{R}/validation/semver_split.json")
if sv:
    st = sv["semver_stable_patch"]
    N.update({"sv_projects": sv["policy_counts"].get("semver", 0), "sv_not": sv["policy_counts"].get("not_semver", 0),
              "sv_calver": sv["policy_counts"].get("calver", 0), "sv_unknown": sv["policy_counts"].get("unknown", 0),
              "sv_patch_n": st["transitions"], "sv_patch_pct": pct(st["share"]), "sv_patch_proj": st["projects_with"],
              "sv_patch_projects": st["projects"],
              "sv_minor_pct": pct(sv["semver|minor|ge1"]["high"]), "sv_minor_n": sv["semver|minor|ge1"]["n"],
              "sv_major_pct": pct(sv["semver|major|ge1"]["high"]),
              "unk_patch_pct": pct(sv["unknown|patch|ge1"]["high"]), "unk_minor_pct": pct(sv["unknown|minor|ge1"]["high"]),
              "all_stable_patch_pct": pct(sv["all_stable_patch"]["share"])})
pl = J(f"{R}/validation/patch_log.json")
if pl:
    tot = sum(pl[f"status_{k}"] for k in ("static", "compiled_boundary", "dynamic_module", "stub_only"))
    N.update({"cov_rows": pl["module_coverage_rows"], "po_fixed": pl["positional_only_renames_unflagged"],
              "gen_classes": pl["generated_init_class_paths"], "gen_events": pl["generated_constructor_events_medium"],
              "sig_dropped": pl["sig_dropped"], "clamped": pl["clamped"],
              **{f"cov_{k}": pct(pl[f"status_{k}"] / tot) for k in ("static", "compiled_boundary", "dynamic_module",
                                                                     "stub_only")}})
    p12_ = J(f"{R}/validation/patch_log_v12.json")
    if p12_:  # coverage shares of the current tables
        tot = sum(p12_[f"status_{k}"] for k in ("static", "compiled_boundary", "dynamic_module", "stub_only"))
        N.update({f"cov_{k}": pct(p12_[f"status_{k}"] / tot) for k in ("static", "compiled_boundary",
                                                                         "dynamic_module", "stub_only")})
dc = J(f"{R}/validation/dataset_checks.json")
if dc:
    N.update({"checks_n": dc["checks"], "checks_failed": dc["failed"]})
pf = J(f"{R}/validation/checker_performance.json")
if pf:
    r = {x["scenario"].split(",")[0].split(" (")[0]: x for x in pf["results"]}
    one, big = pf["results"][0], pf["results"][-1]
    N.update({"perf_cold_cpu": one["cpu_seconds"], "perf_cold_wall": one["seconds"], "perf_rss": one["peak_rss_mb"],
              "perf_big_files": big["files"], "perf_big_cpu": big["cpu_seconds"], "perf_big_wall": big["seconds"],
              "perf_big_rss": big["peak_rss_mb"]})
try:
    L = pd.read_parquet(f"{R}/benchmark/versionprobe_lite.parquet")
    N.update({"probe_lite": int(L.pair_id.nunique()), "probe_lite_libs": int(L.project.nunique())})
except Exception:
    pass
try:
    N["v12_dropped_pairs"] = len(pd.read_csv(f"{R}/validation/v12_dropped_pairs.csv"))
except Exception:
    pass
p12 = J(f"{R}/validation/v12_changes.json")
if p12:
    N.update(p12)
a4 = J(f"{R}/validation/audit4/summary.json")
if a4:
    N.update({"a4_n": a4["all"]["decided"], "a4_ok": a4["all"]["confirmed"], "a4_prec": pc(a4["all"]["confirmed"] / a4["all"]["decided"]),
              "a4_ci": ci(a4["all"]["ci"]), "a4_libs": a4["libraries"], "a4_runtime": a4["all"]["runtime"],
              "a4_new_n": a4["by_stratum"]["new_in_v12"]["decided"], "a4_new_prec": pc(a4["by_stratum"]["new_in_v12"]["precision"]),
              "a4_new_ci": ci(a4["by_stratum"]["new_in_v12"]["ci"]),
              "a4_old_n": a4["by_stratum"]["unchanged"]["decided"], "a4_old_prec": pc(a4["by_stratum"]["unchanged"]["precision"]),
              "a4_old_ci": ci(a4["by_stratum"]["unchanged"]["ci"]),
              "a4_w_prec": pc(a4["weighted_precision"]), "a4_w_ci": ci(a4["weighted_ci"]),
              "a4_rem_prec": pc(a4["by_type"]["removed"]["precision"]), "a4_rem_n": a4["by_type"]["removed"]["decided"],
              "a4_sig_prec": pc(a4["by_type"]["signature"]["precision"]), "a4_sig_n": a4["by_type"]["signature"]["decided"],
              "a4_new_pop": a4["population"]["new_in_v12"]})
rp = J(f"{R}/validation/code_repair/results.json")
if rp:
    for c_ in ("a", "b", "c"):
        v = rp["conditions"][c_]
        N.update({f"rp_{c_}_valid": pc(v["valid_rate"]), f"rp_{c_}_faith": pc(v["faithful_rate"]),
                  f"rp_{c_}_succ": pc(v["success_rate"]), f"rp_{c_}_succ_ci": ci(v["success_ci"]),
                  f"rp_{c_}_k": v["success"]})
    N.update({"rp_n": rp["tasks_scored"], "rp_excluded": rp["tasks_excluded"], "rp_libs": rp["libraries"],
              "rp_ca_p": rp["mcnemar"]["c_vs_a"]["p"], "rp_cb_p": rp["mcnemar"]["c_vs_b"]["p"],
              "rp_ca_w": rp["mcnemar"]["c_vs_a"]["c_only"], "rp_ca_l": rp["mcnemar"]["c_vs_a"]["other_only"],
              "rp_cb_w": rp["mcnemar"]["c_vs_b"]["c_only"], "rp_cb_l": rp["mcnemar"]["c_vs_b"]["other_only"]})
    for st, v in rp["by_stratum"].items():
        for c_ in ("a", "b", "c"):
            N[f"rp_{st}_{c_}"] = pc(v[c_])
        N[f"rp_{st}_n"] = v["n"]
tr = J(f"{R}/validation/transfer_measurement.json")
if tr:
    gb = lambda b: f"{b / 1e9:,.0f}"
    N.update({"tr_share": pc(tr["fetched_share"]), "tr_share_ci": ci([round(100 * x, 1) for x in tr["fetched_share_ci95_bootstrap_projects"]]),
              "tr_gb": gb(tr["extrapolated_fetched_bytes_all"]),
              "tr_gb_ci": f"{gb(tr['extrapolated_fetched_bytes_all_ci95'][0])}–{gb(tr['extrapolated_fetched_bytes_all_ci95'][1])}",
              "tr_total_gb": gb(tr["all_analysed_wheel_bytes"]), "tr_projects": tr["sample"]["projects"],
              "tr_releases": f"{tr['sample']['releases']:,}",
              "tr_big_share": pc(tr["by_wheel_size"][">=1MB"]["fetched_share"]),
              "tr_big_bytes": pc(tr["by_wheel_size"][">=1MB"]["share_of_wheel_bytes"]),
              "tr_small_share": pc(tr["by_wheel_size"]["<1MB"]["fetched_share"]),
              "tr_crc_members": pc(tr["py_members_fetched_share"]),
              "tr_med_kb": f"{tr['fetched_per_release_median_kb']:.0f}"})
rs = J(f"{R}/validation/code_repair/results_strict_binding.json")
if rs:
    for c_ in ("a", "b", "c"):
        v = rs["conditions"][c_]
        N.update({f"rs_{c_}_succ": pc(v["success_strict_rate"]), f"rs_{c_}_lost": v["lost_to_missing_required_args"]})
    N.update({"rs_ca_p": rs["mcnemar_strict"]["c_vs_a"]["p"], "rs_cb_p": rs["mcnemar_strict"]["c_vs_b"]["p"],
              "rs_lost_total": sum(v["lost_to_missing_required_args"] for v in rs["conditions"].values())})
mb = J(f"{R}/validation/model_baseline/results.json")
if mb:
    a_, b_ = mb["closed_book"], mb["with_api_history"]
    N.update({"mb_items": mb["items"], "mb_pairs": mb["pairs"], "mb_libs": mb["libraries"],
              "mb_a_item": pc(a_["item_acc"]), "mb_a_item_ci": ci(a_["item_ci"]), "mb_a_pair": pc(a_["pair_acc"]),
              "mb_a_valid": pc(a_["acc_on_valid"]), "mb_a_invalid": pc(a_["acc_on_invalid"]),
              "mb_a_after": pc(a_["after_cutoff"]["item_acc"]), "mb_a_before": pc(a_["before_cutoff"]["item_acc"]),
              "mb_after_n": a_["after_cutoff"]["n"],
              "mb_b_item": pc(b_["item_acc"]), "mb_b_item_ci": ci(b_["item_ci"]), "mb_b_pair": pc(b_["pair_acc"]),
              "mb_fixed": mb["mcnemar"]["fixed_by_history"], "mb_broken": mb["mcnemar"]["broken_by_history"]})
try:
    v = pd.read_parquet(f"{R}/benchmark/versionprobe_verified.parquet")
    N.update({"ver_pairs": int(v.pair_id.nunique()), "ver_items": len(v), "ver_libs": int(v.project.nunique())})
except Exception:
    pass
try:
    et = pd.read_csv(f"{R}/validation/tool_comparison/apidrift_extraction_times.csv")
    N.update({"t_ours_sec": round(float(et.seconds.median()), 1)})
except Exception:
    pass
import subprocess
try:
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "--co", "tests"], capture_output=True, text=True,
                         cwd=R).stdout
    N["tests_n"] = int(re.search(r"(\d+) tests? collected", out).group(1))
except Exception:
    N["tests_n"] = "n/a"
json.dump(N, open(OUT, "w"), indent=1)
print(json.dumps({k: v for k, v in N.items() if k != "type_rows"}, indent=1))
