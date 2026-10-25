"""End-to-end check of the MCP server as a client sees it: start `apidrift-mcp` over stdio, list its tools, call
each one and check the answers against known facts (Flask 3.0 removed `escape` and `Markup`; pandas 2.0 removed
`DataFrame.append`).
    APIDRIFT_DATA=/path/to/data python mcp_smoke.py [--server-cmd apidrift-mcp] [--json OUT]
Exit 0 when every check passes. Needs the MCP SDK (pip install "pypi-api-drift[mcp]")."""
import asyncio, json, os, shutil, sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

args = sys.argv[1:]
cmd = args[args.index("--server-cmd") + 1] if "--server-cmd" in args else (shutil.which("apidrift-mcp") or "apidrift-mcp")
out_path = args[args.index("--json") + 1] if "--json" in args else None
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}")


async def main():
    params = StdioServerParameters(command=cmd, args=[], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("initialize", init is not None, getattr(getattr(init, "serverInfo", None), "name", ""))
            tools = {t.name for t in (await s.list_tools()).tools}
            check("list_tools", tools == {"api_versions", "api_history", "api_diff", "check_code"}, str(sorted(tools)))

            async def call(name, a):
                res = await s.call_tool(name, a)
                sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
                if sc:  # mcp 2.x: structured_content; 1.x: structuredContent
                    return sc.get("result", sc) if isinstance(sc, dict) else sc
                return json.loads(res.content[0].text)

            v = await call("api_versions", {"project": "flask"})
            check("api_versions flask", "3.0.0" in v.get("analysed", []), f"{len(v.get('analysed', []))} releases")
            d = await call("api_diff", {"project": "flask", "old_version": "2.3.3", "new_version": "3.0.0"})
            paths = {f["path"] for f in d.get("breaking", [])}
            check("api_diff flask 2.3.3 -> 3.0.0", {"flask.escape", "flask.Markup"} <= paths, f"{len(paths)} changes")
            h = await call("api_history", {"project": "pandas", "path": "DataFrame.append"})
            last = max((i["last_version"] for i in h.get("intervals", [])), default=None, key=lambda x: x or "")
            check("api_history pandas DataFrame.append", h.get("intervals") and not any(
                i["still_present_in_latest"] for i in h["intervals"]), f"last present in {last}")
            c = await call("check_code", {"code": "from flask import escape\nimport pandas as pd\n"
                                                  "df = pd.DataFrame()\ndf.append({})\n",
                                          "pins": ["flask==3.0.0", "pandas==2.2.0"]})
            syms = {i["symbol"] for i in c.get("issues", [])}
            check("check_code", {"flask.escape", "pandas.DataFrame.append"} <= syms, str(sorted(syms)))
            e = await call("api_diff", {"project": "not-a-real-package", "old_version": "1", "new_version": "2"})
            check("errors are returned, not raised", "error" in e, e.get("error", ""))


asyncio.run(main())
ok = all(c["ok"] for c in CHECKS)
if out_path:
    json.dump({"passed": ok, "checks": CHECKS}, open(out_path, "w"), indent=1)
print("MCP SMOKE TEST", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
