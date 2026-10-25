"""Tests for apidrift-check against a small dataset built by the pipeline (see conftest.py)."""
import json, os, subprocess, sys
import pytest
from apidrift.check import check_source, parse_lock, parse_pins, sarif, python_files

V1, V15, V2 = {"demo-lib": "1.0.0"}, {"demo-lib": "1.5.0"}, {"demo-lib": "2.0.0"}


def kinds(src, pins, store, **kw):
    return [(i["line"], i["kind"], i["symbol"]) for i in check_source(src, pins, store, **kw)]


def test_valid_code_has_no_findings(store):
    src = "import demo\nf = demo.Frame()\nf.append(None, ignore_index=True)\ndemo.read('x', error_bad_lines=False)\n"
    assert kinds(src, V1, store) == []


def test_removed_function_import(store):
    out = check_source("from demo import old_helper\n", V2, store)
    assert out and out[0]["kind"] == "missing" and "last present in 1.5.0" in out[0]["message"]


def test_removed_module_attribute(store):
    assert (2, "missing", "demo.old_helper") in kinds("import demo\ndemo.old_helper(1)\n", V2, store)


def test_removed_method_on_constructed_object(store):
    src = "import demo\nf = demo.Frame()\nf.append(None)\n"
    assert any(k == "missing" and s.endswith("Frame.append") for _, k, s in kinds(src, V2, store))


def test_renamed_keyword(store):
    src = "import demo\nf = demo.Frame()\nf.to_csv('p', line_terminator='\\n')\n"
    out = check_source(src, V2, store)
    assert out and out[0]["kind"] == "bad_call" and "line_terminator" in out[0]["message"]


def test_removed_keyword_on_function(store):
    src = "from demo import read\nread('x', error_bad_lines=False)\n"
    assert any(k == "bad_call" for _, k, _s in kinds(src, V2, store))


def test_keyword_only_change_flags_positional_call(store):
    src = "import demo\ndemo.connect('h', 80, 5)\n"
    # arity checks are opt-in (--strict)
    assert any(k == "bad_call" for _, k, _s in kinds(src, V2, store, strict=True))
    assert not kinds(src, V1, store, strict=True)


def test_new_name_used_on_old_version_not_flagged_by_default(store):
    assert kinds("import demo\ndemo.brand_new()\n", V1, store) == []


def test_strict_flags_names_not_yet_present(store):
    assert kinds("import demo\ndemo.brand_new()\n", V1, store, strict=True)


def test_invented_name_only_in_strict_mode(store):
    assert kinds("import demo\ndemo.does_not_exist()\n", V2, store) == []
    assert kinds("import demo\ndemo.does_not_exist()\n", V2, store, strict=True)


def test_deprecated_symbol_reported(store):
    assert any(k == "deprecated" for _, k, _s in kinds("import demo\ndemo.legacy()\n", V2, store))


def test_exact_version_required(store):
    cov = {}
    assert check_source("from demo import old_helper\n", {"demo-lib": "1.9.0"}, store, coverage=cov) == []
    assert "demo-lib" in cov and "not checked" in cov["demo-lib"]


def test_nearest_is_opt_in(store):
    out = check_source("import demo\ndemo.brand_new()\n", {"demo-lib": "1.9.0"}, store, strict=True, nearest=True)
    assert out  # checked against 1.5.0


def test_version_spelling_normalised(store):
    assert check_source("from demo import old_helper\n", {"demo-lib": "2.0"}, store)


def test_unpinned_project_checked_against_latest_with_note(store):
    cov = {}
    assert check_source("from demo import old_helper\n", {}, store, coverage=cov)
    assert "demo-lib" in cov


def test_ignore_comment(store):
    assert check_source("from demo import old_helper  # apidrift: ignore\n", V2, store) == []


def test_type_checking_block_skipped(store):
    src = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from demo import old_helper\n"
    assert check_source(src, V2, store) == []


def test_postponed_annotations_skipped(store):
    src = "from __future__ import annotations\nimport demo\ndef f(x: demo.old_helper) -> demo.old_helper: ...\n"
    assert check_source(src, V2, store) == []


def test_stdlib_never_mapped(store):
    assert check_source("import os\nos.path.join('a')\nimport json\njson.loads('1')\n", V2, store) == []


def test_private_names_skipped(store):
    assert check_source("import demo\ndemo._internal()\n", V2, store, strict=True) == []


def test_submodule_function(store):
    src = "from demo.util import tool\ntool(1, b=2)\n"
    assert check_source(src, V2, store) == []
    assert check_source("from demo.util import tool\ntool(1, c=2)\n", V2, store, strict=True)


def test_syntax_error_raises(store):
    with pytest.raises(SyntaxError):
        check_source("def (:\n", V2, store)


# ---------------------------------------------------------------- pins and lockfiles
def test_requirements_pins(tmp_path):
    r = tmp_path / "requirements.txt"
    r.write_text("Demo_Lib==2.0.0\nrequests>=2\n# comment\nnumpy==1.26.4 ; python_version>'3.8'\n")
    pins = parse_pins(["flask==3.0.0"], str(r))
    assert pins["demo-lib"] == "2.0.0" and pins["flask"] == "3.0.0" and pins["numpy"] == "1.26.4"
    assert pins["requests"] is None  # unpinned: checked against the latest analysed release


LOCKS = {
    "uv.lock": 'version = 1\n[[package]]\nname = "demo-lib"\nversion = "2.0.0"\n\n[[package]]\nname = "pandas"\n'
               'version = "2.2.0"\n',
    "poetry.lock": '[[package]]\nname = "demo-lib"\nversion = "2.0.0"\n\n[[package]]\nname = "pandas"\n'
                   'version = "2.2.0"\n',
    "pylock.toml": 'lock-version = "1.0"\n[[packages]]\nname = "demo-lib"\nversion = "2.0.0"\n\n[[packages]]\n'
                   'name = "pandas"\nversion = "2.2.0"\n',
    "Pipfile.lock": json.dumps({"default": {"demo-lib": {"version": "==2.0.0"}}, "develop": {"pandas": {"version": "==2.2.0"}}}),
}


@pytest.mark.parametrize("name", sorted(LOCKS))
def test_lockfiles(tmp_path, name):
    p = tmp_path / name
    p.write_text(LOCKS[name])
    pins = parse_lock(str(p))
    assert pins.get("demo-lib") == "2.0.0" and pins.get("pandas") == "2.2.0"


def test_python_files_recurse_and_skip_venvs(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "b.py").write_text("")
    (tmp_path / "with space.py").write_text("")
    files = list(python_files([str(tmp_path)]))
    assert any(f.endswith("a.py") for f in files) and any(f.endswith("with space.py") for f in files)
    assert not any(".venv" in f for f in files)


# ---------------------------------------------------------------- SARIF and CLI
def test_sarif_structure(store):
    issues = check_source("from demo import old_helper\n", V2, store)
    doc = sarif([("src/x.py", i) for i in issues])
    assert doc["version"] == "2.1.0" and doc["$schema"].startswith("https://")
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "apidrift-check"
    r = run["results"][0]
    loc = r["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/x.py" and loc["region"]["startLine"] == 1
    assert r["ruleId"] in {rule["id"] for rule in run["tool"]["driver"]["rules"]}


def cli(args, data_dir, cwd):
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    env = dict(os.environ, PYTHONPATH=root)
    return subprocess.run([sys.executable, "-m", "apidrift.check", *args, "--data", data_dir], cwd=cwd, env=env,
                          capture_output=True, text=True)


def test_cli_exit_codes_and_sarif(tmp_path, data_dir):
    (tmp_path / "ok.py").write_text("import demo\ndemo.util.tool(1)\n")
    (tmp_path / "bad file.py").write_text("from demo import old_helper\n")
    r = cli([str(tmp_path / "ok.py"), "--pin", "demo-lib==2.0.0"], data_dir, tmp_path)
    assert r.returncode == 0, r.stderr
    r = cli([str(tmp_path), "--pin", "demo-lib==2.0.0", "--sarif", "out.sarif"], data_dir, tmp_path)
    assert r.returncode == 1 and "old_helper" in r.stdout
    doc = json.load(open(tmp_path / "out.sarif"))
    assert len(doc["runs"][0]["results"]) == 1


def test_cli_reads_lockfile_automatically(tmp_path, data_dir):
    (tmp_path / "uv.lock").write_text(LOCKS["uv.lock"])
    (tmp_path / "a.py").write_text("from demo import old_helper\n")
    r = cli(["a.py"], data_dir, tmp_path)
    assert r.returncode == 1 and "pins read from" in r.stderr


def test_unanalysed_release_is_never_a_stand_in(tmp_path):
    import pandas as pd
    from helpers import build_dataset
    from conftest import DEMO_V1
    from apidrift.check import Store
    d = build_dataset(str(tmp_path), {"demo-lib": [("1.0.0", DEMO_V1), ("1.1.0", DEMO_V1)]})
    rel = pd.read_parquet(f"{d}/releases.parquet")
    rel.loc[rel.version == "1.1.0", "status"] = "skipped_too_many_modules"
    rel.to_parquet(f"{d}/releases.parquet", index=False)
    s = Store(d)
    assert s.version_index("demo-lib", "1.1.0") == (None, None)
    assert s.version_index("demo-lib", None) == (0, "1.0.0")
    assert s.version_index("demo-lib", "1.1.0", nearest=True) == (0, "1.0.0")


def test_removed_module_is_reported(tmp_path):
    from helpers import build_dataset
    from apidrift.check import Store
    v1 = {"demo/__init__.py": "", "demo/legacy.py": "def f(): pass\n"}
    v2 = {"demo/__init__.py": ""}
    s = Store(build_dataset(str(tmp_path), {"demo-lib": [("1.0.0", v1), ("2.0.0", v2)]}))
    out = check_source("from demo.legacy import f\n", {"demo-lib": "2.0.0"}, s)
    assert out and out[0]["symbol"] == "demo.legacy" and "last present in 1.0.0" in out[0]["message"]
    assert check_source("from demo.legacy import f\n", {"demo-lib": "1.0.0"}, s) == []


def test_super_init_keyword_checked_against_library_base(store):
    src = ("from demo.frame import Frame\nclass MyFrame(Frame):\n    def __init__(self):\n"
           "        super().__init__(data=None, copy=True)\n    def save(self):\n"
           "        super().to_csv('p', line_terminator='x')\n")
    out = check_source(src, V2, store)
    assert any("line_terminator" in i["message"] for i in out)
    assert not any("'copy'" in i["message"] or "'data'" in i["message"] for i in out)
    assert check_source(src, V1, store) == []
