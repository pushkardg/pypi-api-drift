"""apidrift-mcp: a Model Context Protocol server (stdio) that lets coding agents query the PyPI API Drift dataset
offline and check generated code against the library versions a project pins.

    pip install "apidrift[mcp]"
    APIDRIFT_DATA=/path/to/data apidrift-mcp

Example client configuration (Claude Code, Claude Desktop and most MCP clients):

    {"mcpServers": {"apidrift": {"command": "apidrift-mcp", "env": {"APIDRIFT_DATA": "/path/to/data"}}}}

Tools (all read-only, no network, no package code is imported):
  api_versions(project)                      analysed releases of a library
  api_history(project, path)                 when a symbol existed and with which signatures
  api_diff(project, old_version, new_version) breaking changes between two releases, with confidence labels
  check_code(code, pins)                     apidrift-check on a snippet, e.g. pins=["pandas==2.2.0"]
"""
import os, sys

from .check import DATA, Store, check_source
from .lookup import Lookup

INSTRUCTIONS = ("Offline public-API history for ~1,900 popular PyPI libraries (static analysis of up to 80 recent "
                "releases each). Use api_diff before upgrading a dependency, api_history to see when a function "
                "or parameter existed, and check_code to validate generated code against pinned versions. "
                "Findings are high-precision but not complete: names generated at runtime (module __getattr__, "
                "compiled extensions) can be missing, so 'not found' is weaker evidence than a reported change.")

_lookup = None


def lookup():
    global _lookup
    if _lookup is None:
        _lookup = Lookup(store=Store(os.environ.get("APIDRIFT_DATA", DATA)))
    return _lookup


def _err(e):
    return {"error": str(e)}


def api_versions(project: str) -> dict:
    """List the analysed releases of a PyPI project (distribution name or top-level module, e.g. 'sklearn')."""
    try:
        lk = lookup(); p = lk.project(project)
        return {"project": p, "analysed": lk.analysed(p)}
    except LookupError as e:
        return _err(e)


def api_history(project: str, path: str) -> dict:
    """When a public symbol existed, under which signatures, and whether it carried a deprecation marker.
    path may omit the top-level module, e.g. project='pandas', path='DataFrame.append'."""
    try:
        return lookup().history(project, path)
    except LookupError as e:
        return _err(e)


def api_diff(project: str, old_version: str, new_version: str, limit: int = 200) -> dict:
    """Breaking changes (removed names, removed/renamed/required parameters, ...) between two analysed releases,
    each listed once with the dataset's confidence label; 'same_name_in_new_release' gives places where a removed
    name still exists, as hints rather than verified replacements."""
    try:
        return lookup().diff(project, old_version, new_version, limit=limit)
    except LookupError as e:
        return _err(e)


def check_code(code: str, pins: list[str]) -> dict:
    """Check Python source against pinned library versions (e.g. pins=['pandas==2.2.0', 'flask==3.0.0']) without
    running it. Reports imports, attributes and call arguments that do not exist in those versions."""
    pinned = {}
    for p in pins:
        if "==" not in p:
            return _err(f"pin {p!r} must look like name==version")
        n, v = p.split("==", 1)
        pinned[n.strip()] = v.strip()
    try:
        issues = check_source(code, pinned, lookup().s)
    except SyntaxError as e:
        return _err(f"syntax error: {e}")
    return {"issues": issues, "n_issues": len(issues)}


TOOLS = (api_versions, api_history, api_diff, check_code)


def build_server():
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        try:  # mcp 1.x
            from mcp.server.fastmcp import FastMCP as Server
        except ImportError:
            sys.exit("apidrift-mcp needs the MCP SDK: pip install 'apidrift[mcp]'")
    srv = Server("apidrift", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        srv.tool()(fn)
    return srv


def main():
    build_server().run("stdio")


if __name__ == "__main__":
    main()
