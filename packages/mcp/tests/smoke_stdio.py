"""Real stdio round trip; optional read-only live driver check. No model calls."""
import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(live, app, screenshot, bundled_app=None):
    params = (StdioServerParameters(command=bundled_app + "/Contents/MacOS/OpenGCUA", args=["--mcp"])
              if bundled_app else StdioServerParameters(command=sys.executable, args=["-m", "opengcua_mcp"]))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"status", "observe", "click", "release"} <= names, names
            print("MCP initialize/tools/list OK:", ", ".join(sorted(names)), flush=True)
            invalid = await client.call_tool("click", {"view_id": "not-observed", "element": 1})
            assert invalid.isError
            assert "STALE_VIEW" in str(invalid.content)
            print("Unobserved action rejected OK", flush=True)
            try:
                if live:
                    status = await client.call_tool("status", {})
                    print("STATUS:", status.model_dump_json(), flush=True)
                    assert not status.isError, status
                    apps = await client.call_tool("list_apps", {})
                    assert not apps.isError, apps
                    print("Live app inventory OK", flush=True)
                    if app:
                        observed = await client.call_tool("observe", {"app": app, "screenshot": screenshot})
                        assert not observed.isError, observed
                        meta = json.loads(observed.content[0].text)
                        assert meta["view_id"]
                        print("Live observation OK; content blocks:", [x.type for x in observed.content], flush=True)
                        if screenshot:
                            assert any(x.type == "image" for x in observed.content), "Screenshot was not returned"
                        stale = await client.call_tool("click", {"view_id": "old-view", "element": 1})
                        assert stale.isError
                        print("Live stale view rejected without input OK", flush=True)
            finally:
                result = await client.call_tool("release", {})
                assert not result.isError, result
                print("Release OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--app", help="Already-running app to observe; does not launch it")
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--bundled-app", help="Test the signed app entry point, including shared permissions")
    args = parser.parse_args()
    asyncio.run(check(args.live, args.app, args.screenshot, args.bundled_app))
