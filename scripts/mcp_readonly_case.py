"""Read-only OpenGCUA MCP case, mirroring the README demo prompt:
status -> list_apps -> list_windows -> observe (AX + screenshot) -> release.
No clicks, no typing. Run with the app's bundled Python.
"""
import argparse
import asyncio
import base64
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def meta(result):
    for block in result.content:
        if block.type == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"text": block.text}
    return {}


async def main(bundled_app, out_dir, observe_app, window_id, screenshot=True):
    params = StdioServerParameters(command=bundled_app + "/Contents/MacOS/OpenGCUA", args=["--mcp"])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            info = await client.initialize()
            print(f"[1] initialize OK: server={info.serverInfo.name} {info.serverInfo.version}, "
                  f"protocol={info.protocolVersion}")

            tools = await client.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"[2] tools/list OK ({len(names)}): {', '.join(names)}")

            status = await client.call_tool("status", {})
            assert not status.isError, status.content
            print("[3] status:", json.dumps(meta(status), ensure_ascii=False, indent=2))

            apps = await client.call_tool("list_apps", {})
            assert not apps.isError, apps.content
            app_meta = meta(apps)
            app_list = app_meta.get("apps", app_meta) if isinstance(app_meta, dict) else app_meta
            if not isinstance(app_list, list):
                app_list = []
                print("[4] list_apps raw head:", json.dumps(app_meta, ensure_ascii=False)[:800])
            print(f"[4] list_apps OK: {len(app_list)} apps")
            for a in app_list[:25]:
                label = a if isinstance(a, str) else (a.get("bundle_id") or a.get("id") or a.get("name", "?"))
                print("    -", label)
            if len(app_list) > 25:
                print(f"    ... {len(app_list) - 25} more")

            candidates = [observe_app] if observe_app else [
                "com.apple.finder", "com.apple.TextEdit", "com.apple.Terminal",
                "com.apple.Safari", "com.google.Chrome"]
            chosen, win_meta = None, {}
            for cand in candidates:
                if not cand:
                    continue
                win = await client.call_tool("list_windows", {"app": cand})
                if win.isError:
                    print(f"[5] list_windows({cand}) -> {str(win.content)[:200]}")
                    continue
                win_meta = meta(win)
                windows = win_meta.get("windows") if isinstance(win_meta, dict) else win_meta
                if isinstance(windows, list) and windows:
                    chosen = cand
                    print(f"[5] list_windows({cand}) OK: {len(windows)} window(s)")
                    break
            assert chosen, "no candidate app with a window found"
            print("[5] windows:", json.dumps(win_meta, ensure_ascii=False)[:800])

            args = {"app": chosen}
            windows = win_meta.get("windows") if isinstance(win_meta, dict) else None
            if window_id:
                args["window_id"] = window_id
            elif isinstance(windows, list) and windows:
                first = windows[0]
                if isinstance(first, dict):
                    for key in ("window_id", "id"):
                        if key in first:
                            args["window_id"] = first[key]
                            break
                elif isinstance(first, (str, int)):
                    args["window_id"] = first
            if screenshot:
                args["screenshot"] = True
            print(f"[6] observe args: {args}")
            obs = await client.call_tool("observe", args)
            assert not obs.isError, obs.content
            obs_meta = meta(obs)
            view_id = obs_meta.get("view_id")
            assert view_id, "no view_id returned"
            kinds = [b.type for b in obs.content]
            print(f"[6] observe OK: view_id={view_id}, blocks={kinds}, "
                  f"elements={len(obs_meta.get('elements', obs_meta.get('ax', [])) or [])}")
            text = json.dumps(obs_meta, ensure_ascii=False)
            print("    ax/meta preview:", text[:1000].replace("\\n", " | "))
            for i, block in enumerate(obs.content):
                if block.type == "image" and getattr(block, "data", None):
                    shot = out_dir / f"observe-{i}.png"
                    shot.write_bytes(base64.b64decode(block.data))
                    print(f"    screenshot saved: {shot} ({len(block.data)} b64 bytes, "
                          f"mime={getattr(block, 'mimeType', '?')})")

            rel = await client.call_tool("release", {})
            assert not rel.isError, rel.content
            print("[7] release OK — session closed, Mac lease freed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundled-app", default="/Applications/OpenGCUA.app")
    parser.add_argument("--out", default="mcp-case-output")
    parser.add_argument("--app", help="app identity to observe; omit for current/frontmost")
    parser.add_argument("--window-id")
    parser.add_argument("--no-screenshot", action="store_true")
    ns = parser.parse_args()
    out = Path(ns.out); out.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(ns.bundled_app, out, ns.app, ns.window_id, not ns.no_screenshot))
