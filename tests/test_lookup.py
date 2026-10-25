"""apidrift-lookup and the MCP tool functions, on the demo-lib fixture (1.0.0 -> 1.5.0 -> 2.0.0)."""
import json, os
import pytest

from apidrift.lookup import Lookup, main


@pytest.fixture(scope="module")
def lk(store):
    return Lookup(store=store)


def test_project_name_spellings(lk):
    assert lk.project("demo-lib") == lk.project("Demo_Lib") == lk.project("demo") == "demo-lib"
    with pytest.raises(LookupError):
        lk.project("not-a-package")


def test_history_without_top_level_module(lk):
    h = lk.history("demo-lib", "Frame.append")
    assert [i["path"] for i in h["intervals"]] == ["demo.Frame.append"]
    i = h["intervals"][0]
    assert (i["first_version"], i["last_version"], i["still_present_in_latest"]) == ("1.0.0", "1.5.0", False)
    assert i["signature"] == "(other, ignore_index=False)"


def test_history_signature_change_and_deprecation(lk):
    rows = lk.history("demo-lib", "demo.connect")["intervals"]
    assert rows[-1]["signature"] == "(host, port=80, *, timeout=None)" and rows[-1]["still_present_in_latest"]
    legacy = lk.history("demo-lib", "legacy")["intervals"]
    assert legacy and all(r["deprecated"] for r in legacy)


def test_history_unknown_symbol(lk):
    assert lk.history("demo-lib", "nope")["intervals"] == []


def test_diff_non_adjacent_releases(lk):
    d = lk.diff("demo-lib", "1.0.0", "2.0.0", include_added=True)
    got = {(f["path"], f["change"], f.get("param")) for f in d["breaking"]}
    assert ("demo.old_helper", "removed", None) in got
    assert ("demo.Frame.append", "removed", None) in got
    assert ("demo.connect", "became_keyword_only", "timeout") in got
    assert ("demo.Frame.to_csv", "param_removed", "line_terminator") in got
    assert ("demo.read", "param_removed", "error_bad_lines") in got
    assert "demo.brand_new" in d["added"] and d["releases_between"] == 1
    assert not any(f["path"] == "demo.util.tool" for f in d["breaking"])  # unchanged


def test_diff_removed_child_folded_into_removed_parent(lk):
    d = lk.diff("demo-lib", "1.0.0", "2.0.0", every_path=True)
    assert not any(f["path"].startswith("demo.old_helper.") for f in d["breaking"])


def test_diff_rejects_bad_order_and_unknown_versions(lk):
    with pytest.raises(LookupError):
        lk.diff("demo-lib", "2.0.0", "1.0.0")
    with pytest.raises(LookupError):
        lk.diff("demo-lib", "1.0.0", "9.9.9")


def test_cli_json(data_dir, capsys):
    assert main(["--data", data_dir, "--json", "diff", "demo-lib", "1.0.0", "2.0.0"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["from"] == "1.0.0" and out["breaking_total"] >= 5
    assert main(["--data", data_dir, "versions", "demo"]) == 0
    assert "1.0.0, 1.5.0, 2.0.0" in capsys.readouterr().out
    assert main(["--data", data_dir, "diff", "demo-lib", "1.0.0", "7.0"]) == 2


def test_mcp_tool_functions(store, monkeypatch):
    from apidrift import mcp_server
    monkeypatch.setattr(mcp_server, "_lookup", Lookup(store=store))
    assert mcp_server.api_versions("demo")["analysed"] == ["1.0.0", "1.5.0", "2.0.0"]
    assert mcp_server.api_history("demo-lib", "old_helper")["intervals"][0]["last_version"] == "1.5.0"
    assert mcp_server.api_diff("demo-lib", "1.5.0", "2.0.0")["breaking_total"] >= 4
    assert "error" in mcp_server.api_diff("nope", "1", "2")
    r = mcp_server.check_code("from demo import old_helper\n", ["demo-lib==2.0.0"])
    assert r["n_issues"] == 1 and r["issues"][0]["symbol"] == "demo.old_helper"
    assert "error" in mcp_server.check_code("x = (", ["demo-lib==2.0.0"])
    assert "error" in mcp_server.check_code("import demo\n", ["demo-lib>=2"])


def test_mcp_server_registers_tools():
    pytest.importorskip("mcp")
    from apidrift import mcp_server
    import asyncio
    srv = mcp_server.build_server()
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert names == {"api_versions", "api_history", "api_diff", "check_code"}


def test_absence_carries_a_caveat(lk):
    h = lk.history("demo-lib", "never_existed")
    assert h["intervals"] == [] and "weaker evidence" in h["note"]


def test_fixture_without_coverage_column_has_no_coverage_notes(lk):
    d = lk.diff("demo-lib", "1.0.0", "2.0.0")
    assert all(f.get("coverage_note") is None for f in d["breaking"]) and "coverage_warning" not in d


DATA = os.environ.get("APIDRIFT_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


@pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "api_history.parquet")), reason="data not present")
def test_real_data_reports_coverage_for_dynamic_modules():
    real = Lookup(DATA)
    h = real.history("numpy", "float")
    assert h["intervals"] and h["intervals"][0]["coverage_status"] == "dynamic_module" and h["note"]
    d = real.diff("flask", "2.3.3", "3.0.0")
    assert {"flask.escape", "flask.Markup"} <= {f["path"] for f in d["breaking"]}
