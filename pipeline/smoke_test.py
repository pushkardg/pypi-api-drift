"""Clean-installation smoke test: install this release into a fresh virtual environment (non-editable, declared
dependencies only), run the test suite there, verify the data against DATA_MANIFEST.json and run the installed
apidrift-check CLI on the bundled example.

    python smoke_test.py /path/to/downloaded/data [--json OUT]

Exit 0 only if every step passes. Uses only the standard library."""
import json, os, platform, shutil, subprocess, sys, tempfile, time, venv

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED = {  # (kind, symbol) the example must produce; see README "Quick start"
    ("missing", "flask.escape"), ("missing", "pandas.DataFrame.append"),
    ("bad_call", "pandas.DataFrame.to_csv"), ("bad_call", "urllib3.util.retry.Retry"),
    ("bad_call", "pandas.read_csv"),
}
PINS = ["pandas==2.2.0", "flask==3.0.0", "urllib3==2.2.0"]


def step(name, argv, results, ok_codes=(0,), **kw):
    t = time.time()
    p = subprocess.run(argv, capture_output=True, text=True, **kw)
    results.append({"step": name, "returncode": p.returncode, "seconds": round(time.time() - t, 1),
                    "tail": (p.stdout + p.stderr).strip().splitlines()[-3:]})
    print(f"[{'ok' if p.returncode in ok_codes else 'FAIL'}] {name} ({results[-1]['seconds']} s)")
    return p


def main():
    args = sys.argv[1:]
    out = args[args.index("--json") + 1] if "--json" in args else None
    data = os.path.abspath(args[0]) if args and not args[0].startswith("--") else None
    if not data:
        sys.exit(__doc__)
    work = tempfile.mkdtemp(prefix="apidrift_smoke_")
    res, ok = [], True
    try:
        src = os.path.join(work, "src")  # a copy without data/, build/ or egg-info, as a user would download it
        shutil.copytree(HERE, src, ignore=shutil.ignore_patterns("data", "build", "*.egg-info", "__pycache__",
                                                                  ".pytest_cache"))
        venv.create(os.path.join(work, "venv"), with_pip=True)
        py = os.path.join(work, "venv", "bin", "python")
        cli = os.path.join(work, "venv", "bin", "apidrift-check")
        env = dict(os.environ, APIDRIFT_DATA=data)
        ok &= step("install (pip install . pytest)", [py, "-m", "pip", "install", "-q", src, "pytest"], res).returncode == 0
        ok &= step("import from site-packages", [py, "-c", "import apidrift, os; assert 'site-packages' in "
                                                     "apidrift.__file__, apidrift.__file__"], res, cwd=work).returncode == 0
        ok &= step("test suite (incl. data-level regressions)", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                                                 "tests"], res, cwd=src, env=env).returncode == 0
        ok &= step("verify data against DATA_MANIFEST.json", [py, "verify_data.py", data], res, cwd=src).returncode == 0
        p = step("apidrift-check on examples/llm_generated_example.py",
                 [cli, os.path.join(src, "examples", "llm_generated_example.py"), "--json",
                  *[a for pin in PINS for a in ("--pin", pin)]], res, ok_codes=(0, 1), cwd=work, env=env)
        try:
            found = {(i["kind"], i["symbol"]) for line in p.stdout.splitlines() if line.strip()
                     for i in json.loads(line)["issues"]}
        except Exception:
            found = set()
        missing = sorted(EXPECTED - found)
        res[-1].update({"findings": len(found), "expected_missing": missing})
        if p.returncode not in (0, 1) or missing:  # exit 1 means "findings reported"
            ok = False
            print("  expected findings not reported:", missing)
        else:
            res[-1]["returncode"] = 0
            print(f"  {len(found)} findings, all {len(EXPECTED)} expected ones present")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {"passed": bool(ok), "python": platform.python_version(), "platform": platform.platform(),
               "data": data, "steps": res}
    if out:
        json.dump(summary, open(out, "w"), indent=1)
    print("SMOKE TEST", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
