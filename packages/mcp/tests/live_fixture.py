"""Opt-in input test for the repository-owned macOS fixture ONLY.

Launch the SDK fixture with bundle ID dev.opengcua.mcp.fixture and a state JSON
path, then supply that path. Never point this test at a user's working application.
"""
import asyncio
import json
from pathlib import Path
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def meta(result):
    for block in result.content:
        if block.type == "text" and block.text.startswith("{"):
            data = json.loads(block.text)
            if "view_id" in data:
                return data
    raise AssertionError("No fresh view returned")


def element(result, name):
    for block in result.content:
        if block.type == "text":
            match = re.search(r'\[(\d+)\] [^\n]*name="' + re.escape(name) + '"', block.text)
            if match:
                return int(match[1])
    raise AssertionError(f"Fixture element not observed: {name}")


async def check(state_path):
    initial = json.loads(state_path.read_text())
    params = StdioServerParameters(command=sys.executable, args=["-m", "opengcua_mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as client:
            await client.initialize()
            async def call(tool, **args):
                result = await client.call_tool(tool, args)
                assert not result.isError, result
                return result
            try:
                windows = await call("list_windows", app="dev.opengcua.mcp.fixture")
                inventory = json.loads(windows.content[0].text)
                window_id = next(x["id"] for x in inventory["windows"] if x["title"] == "MAI Fixture 1")
                view = await call("observe", app="dev.opengcua.mcp.fixture", window_id=window_id)
                view = await call("click", view_id=meta(view)["view_id"], element=element(view, "Increment 1"))
                assert json.loads(state_path.read_text())["counts"][0] == initial["counts"][0] + 1
                print("Native MCP click verified by fixture counter", flush=True)
                view = await call("set_text", view_id=meta(view)["view_id"], element=element(view, "Editor 1"), text_value="OpenGCUA MCP integration test")
                assert json.loads(state_path.read_text())["text"][0] == "OpenGCUA MCP integration test"
                print("Native MCP set_text verified by fixture state", flush=True)
                view = await call("press_key", view_id=meta(view)["view_id"], key="a", modifiers=["cmd"], element=element(view, "Editor 1"))
                view = await call("type_text", view_id=meta(view)["view_id"], text_value="typed through MCP", element=element(view, "Editor 1"))
                assert json.loads(state_path.read_text())["text"][0] == "typed through MCP"
                print("Native MCP key chord and typing verified", flush=True)
                # Restore the synthetic field; no user document is involved.
                await call("set_text", view_id=meta(view)["view_id"], element=element(view, "Editor 1"), text_value=initial["text"][0])
            finally:
                await call("release")
    print("Native fixture input checks passed; session released", flush=True)


if __name__ == "__main__":
    asyncio.run(check(Path(sys.argv[1])))
