"""Helper for the apidrift-check GitHub Action. Reads workflow inputs from IN_* environment variables, validates
them and runs the checker with an argument list (no shell), so input values cannot inject commands.

    run_action.py validate | download | check
"""
import hashlib, json, os, re, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

FILES = ("releases", "api_history", "signatures", "top_level_modules")
ALLOWED_OPTIONS = {"--strict", "--nearest", "--no-deprecations"}


def fail(msg):
    print(f"::error::apidrift-check: {msg}")
    sys.exit(2)


def data_version():
    v = os.environ.get("IN_DATA_VERSION", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", v) or v in (".", ".."):
        fail("data-version may only contain letters, digits, '.', '_' and '-'")
    return v


def data_url():
    u = os.environ.get("IN_DATA_URL", "").strip().rstrip("/")
    p = urllib.parse.urlparse(u)
    if p.scheme != "https" or not p.netloc or any(c in u for c in "\r\n\t \"'`$;|&<>"):
        fail("data-url must be a plain https:// URL")
    return u


def manifest():
    """DATA_MANIFEST.json at the repository root pins the SHA-256 of every data file of the release."""
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "DATA_MANIFEST.json")
    return json.load(open(f)) if os.path.exists(f) else None


def verify(d):
    m = manifest()
    if m is None:
        fail("DATA_MANIFEST.json is missing; refusing to use unverified data")
    for name, info in m["files"].items():
        if name not in [f + ".parquet" for f in FILES]:
            continue
        path = d / name
        if not path.exists():
            fail(f"data file missing: {name}")
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != info["sha256"]:
            fail(f"{name} does not match DATA_MANIFEST.json (expected {info['sha256'][:12]}..., got {h.hexdigest()[:12]}...)")
    print(f"data verified against DATA_MANIFEST.json ({m.get('data_version')})")


def data_dir():
    return Path.home() / ".cache" / "apidrift" / data_version()


def inside_repo(p):
    root = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
    full = (root / p).resolve()
    if full != root and root not in full.parents:
        fail(f"path outside the repository: {p!r}")
    if not full.exists():
        fail(f"path does not exist: {p!r}")
    return str(full)


def output(name, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{name}={value}\n")


def main(cmd):
    if cmd == "validate":
        data_url()
        output("data_version", data_version())
    elif cmd == "download":
        base, d = data_url(), data_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f in FILES:
            print(f"downloading {f}.parquet")
            urllib.request.urlretrieve(f"{base}/{f}.parquet", d / f"{f}.parquet")
        verify(d)
    elif cmd == "check":
        paths = [p.strip() for p in os.environ.get("IN_PATHS", ".").splitlines() if p.strip()] or ["."]
        argv = ["apidrift-check", *[inside_repo(p) for p in paths]]
        lock, req = os.environ.get("IN_LOCKFILE", "").strip(), os.environ.get("IN_REQUIREMENTS", "").strip()
        if lock:
            argv += ["--lock", inside_repo(lock)]
        if req:
            argv += ["-r", inside_repo(req)]
        for opt in os.environ.get("IN_OPTIONS", "").split():
            if opt not in ALLOWED_OPTIONS:
                fail(f"option not allowed: {opt!r} (allowed: {', '.join(sorted(ALLOWED_OPTIONS))})")
            argv.append(opt)
        verify(data_dir())  # also covers data restored from the Actions cache
        argv += ["--sarif", "apidrift.sarif", "--data", str(data_dir())]
        code = subprocess.run(argv).returncode
        env = os.environ.get("GITHUB_ENV")
        if env:
            with open(env, "a") as f:
                f.write(f"APIDRIFT_FINDINGS_EXIT={code}\n")
        if code not in (0, 1):
            fail(f"checker failed with exit code {code}")
    else:
        fail(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
