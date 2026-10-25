"""The GitHub Action helper must reject hostile inputs and pass paths to the checker as arguments, not shell text."""
import os, subprocess, sys
import pytest

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "integrations", "github-action",
                      "run_action.py")


def run(cmd, tmp_path, **env):
    full = dict(os.environ, GITHUB_WORKSPACE=str(tmp_path), GITHUB_OUTPUT=str(tmp_path / "out"),
                GITHUB_ENV=str(tmp_path / "env"), HOME=str(tmp_path / "home"), **env)
    return subprocess.run([sys.executable, HELPER, cmd], cwd=tmp_path, env=full, capture_output=True, text=True)


@pytest.mark.parametrize("version", ["2026.10", "v1_2-3"])
def test_valid_inputs(tmp_path, version):
    r = run("validate", tmp_path, IN_DATA_VERSION=version, IN_DATA_URL="https://example.org/data")
    assert r.returncode == 0, r.stdout
    assert f"data_version={version}" in (tmp_path / "out").read_text()


@pytest.mark.parametrize("version", ["$(touch pwned)", "../../etc", "a b", "x;rm -rf /", "..", "`id`", ""])
def test_hostile_data_version_rejected(tmp_path, version):
    r = run("validate", tmp_path, IN_DATA_VERSION=version, IN_DATA_URL="https://example.org/data")
    assert r.returncode == 2 and "::error::" in r.stdout
    assert not (tmp_path / "pwned").exists()


@pytest.mark.parametrize("url", ["http://example.org/data", "file:///etc", "https://x.org/$(id)", "https://x.org/a b",
                                 "https://x.org/a;id", "ftp://x"])
def test_hostile_data_url_rejected(tmp_path, url):
    assert run("validate", tmp_path, IN_DATA_VERSION="1", IN_DATA_URL=url).returncode == 2


def fake_data(tmp_path, tamper=False):
    """Fake data files under HOME/.cache/apidrift/1 and a manifest beside the helper that pins their hashes."""
    import hashlib, json
    d = tmp_path / "home" / ".cache" / "apidrift" / "1"
    d.mkdir(parents=True, exist_ok=True)
    files = {}
    for f in ("releases", "api_history", "signatures", "top_level_modules"):
        (d / f"{f}.parquet").write_bytes(f.encode())
        files[f"{f}.parquet"] = {"sha256": hashlib.sha256(f.encode()).hexdigest()}
    if tamper:
        (d / "releases.parquet").write_bytes(b"tampered")
    return {"data_version": "test", "files": files}


def with_manifest(m):
    import json, shutil
    root = os.path.join(os.path.dirname(HELPER), "..", "..")
    path = os.path.join(root, "DATA_MANIFEST.json")
    keep = open(path).read() if os.path.exists(path) else None
    json.dump(m, open(path, "w"))
    return path, keep


def restore_manifest(path, keep):
    if keep is None:
        os.remove(path)
    else:
        open(path, "w").write(keep)


@pytest.fixture
def manifest_ok(tmp_path):
    path, keep = with_manifest(fake_data(tmp_path))
    yield
    restore_manifest(path, keep)


def test_tampered_data_rejected(tmp_path):
    path, keep = with_manifest(fake_data(tmp_path, tamper=True))
    try:
        r = run("check", tmp_path, PATH=fake_checker(tmp_path), IN_PATHS=".", IN_DATA_VERSION="1")
        assert r.returncode == 2 and "does not match DATA_MANIFEST.json" in r.stdout
    finally:
        restore_manifest(path, keep)


def fake_checker(tmp_path):
    """Put a stand-in apidrift-check on PATH that records its argv, one argument per line."""
    b = tmp_path / "bin"
    b.mkdir()
    exe = b / "apidrift-check"
    exe.write_text(f"#!{sys.executable}\nimport sys\nopen({str(tmp_path / 'argv')!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
                   "sys.exit(1)\n")
    exe.chmod(0o755)
    return str(b) + os.pathsep + os.environ["PATH"]


def test_paths_with_spaces_and_metacharacters_passed_literally(tmp_path, manifest_ok):
    (tmp_path / "src dir").mkdir()
    (tmp_path / "a;b $(x).py").write_text("")
    path = fake_checker(tmp_path)
    r = run("check", tmp_path, PATH=path, IN_PATHS="src dir\na;b $(x).py\n", IN_DATA_VERSION="1", IN_OPTIONS="--strict")
    assert r.returncode == 0, r.stdout + r.stderr
    argv = (tmp_path / "argv").read_text().splitlines()
    assert argv[0].endswith("src dir") and argv[1].endswith("a;b $(x).py") and "--strict" in argv
    assert "APIDRIFT_FINDINGS_EXIT=1" in (tmp_path / "env").read_text()


@pytest.mark.parametrize("opt", ["--data=/etc", "--sarif=/tmp/x", ";id", "--lock"])
def test_options_allowlist(tmp_path, opt, manifest_ok):
    path = fake_checker(tmp_path)
    assert run("check", tmp_path, PATH=path, IN_PATHS=".", IN_DATA_VERSION="1", IN_OPTIONS=opt).returncode == 2


@pytest.mark.parametrize("p", ["../outside", "/etc/passwd"])
def test_paths_outside_repository_rejected(tmp_path, p, manifest_ok):
    path = fake_checker(tmp_path)
    assert run("check", tmp_path, PATH=path, IN_PATHS=p, IN_DATA_VERSION="1").returncode == 2
