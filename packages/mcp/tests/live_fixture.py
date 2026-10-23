"""Opt-in input test for the repository-owned macOS fixture ONLY.

Launch the SDK fixture with bundle ID dev.opengcua.mcp.fixture and a state JSON
path, then supply that path. Never point this test at a user's working application.
"""
import asyncio
import argparse
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


async def check(state_path, bundled_app=None):
    initial = json.loads(state_path.read_text())
    params = (StdioServerParameters(command=str(Path(bundled_app) / "Contents/MacOS/OpenGCUA"), args=["--mcp"])
              if bundled_app else StdioServerParameters(command=sys.executable, args=["-m", "opengcua_mcp"]))

    async def expect_state(predicate, description):
        # Fixture snapshots are written on its AppKit timer, independently of MCP receipts.
        try:
            async with asyncio.timeout(3):
                while True:
                    state = json.loads(state_path.read_text())
                    if predicate(state):
                        return state
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise AssertionError(f"{description}: {state}") from exc
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
                await expect_state(lambda s: s["counts"][0] == initial["counts"][0] + 1, "counter increment")
                print("Native MCP click verified by fixture counter", flush=True)
                view = await call("set_text", view_id=meta(view)["view_id"], element=element(view, "Editor 1"), text_value="OpenGCUA MCP integration test")
                await expect_state(lambda s: s["text"][0] == "OpenGCUA MCP integration test", "set_text")
                print("Native MCP set_text verified by fixture state", flush=True)
                for targeted in (True, False):
                    original = "全选测试 OpenGCUA 😀"
                    replacement = "替换成功 typed through MCP"
                    view = await call("set_text", view_id=meta(view)["view_id"], element=element(view, "Editor 1"), text_value=original)
                    kwargs = {"element": element(view, "Editor 1")} if targeted else {}
                    view = await call("press_key", view_id=meta(view)["view_id"], key="a", modifiers=["cmd"], **kwargs)
                    expected = {"location": 0, "length": len(original.encode("utf-16-le")) // 2}
                    await expect_state(lambda s: s["selections"][0] == expected, "Cmd+A must select the entire UTF-16 range")
                    print(f"Native Cmd+A selection verified (targeted={targeted}): {expected}", flush=True)
                    kwargs = {"element": element(view, "Editor 1")} if targeted else {}
                    view = await call("type_text", view_id=meta(view)["view_id"], text_value=replacement, **kwargs)
                    state = await expect_state(lambda s: s["text"][0] == replacement, "typing must replace the selection")
                    assert state["text"][1] == initial["text"][1], "Input reached the sibling window"
                    assert state["counts"][1] == initial["counts"][1]
                    print(f"Native selected-text replacement verified (targeted={targeted})", flush=True)
                # Restore the synthetic field; no user document is involved.
                await call("set_text", view_id=meta(view)["view_id"], element=element(view, "Editor 1"), text_value=initial["text"][0])
                await expect_state(lambda s: s["text"][0] == initial["text"][0], "restore fixture text")
            finally:
                released = await call("release")
                assert json.loads(released.content[0].text)["released"]
    print("Native fixture input checks passed; session released", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("state_path", type=Path)
    parser.add_argument("--bundled-app", help="Test the integrated app entry point")
    args = parser.parse_args()
    asyncio.run(check(args.state_path, args.bundled_app))
