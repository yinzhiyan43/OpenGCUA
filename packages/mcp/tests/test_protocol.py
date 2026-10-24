"""Actual MCP requests over memory streams; no desktop input or model calls."""
import asyncio
from datetime import timedelta
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.shared.exceptions import McpError
from mcp.types import (ImageContent, TextContent, ClientNotification,
                       CancelledNotification, CancelledNotificationParams)

from opengcua_mcp.backend import Bridge
from opengcua_mcp.server import create_server
from opengcua_mcp.operations import DeviceLease


def connect(server):
    return create_connected_server_and_client_session(
        server, read_timeout_seconds=timedelta(seconds=3))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_passive_while_device_ownership_stays_exclusive(self):
        def factory():
            bridge = Bridge()
            bridge.status = AsyncMock(return_value={"ok": True})
            bridge.list_apps = AsyncMock(return_value=[])
            return bridge
        lease = DeviceLease()
        async with connect(create_server(factory, lease=lease)) as second, \
                   connect(create_server(factory, lease=lease)) as first:
            self.assertFalse((await first.call_tool("status", {})).isError)
            self.assertFalse((await second.call_tool("status", {})).isError)
            self.assertIsNone(lease.owner)
            self.assertFalse((await first.call_tool("list_apps", {})).isError)
            owner = lease.owner
            self.assertIsNotNone(owner)
            self.assertFalse((await second.call_tool("status", {})).isError)
            self.assertIs(lease.owner, owner)
            self.assertTrue((await second.call_tool("list_apps", {})).isError)
            await second.call_tool("release", {})
            self.assertIs(lease.owner, owner)
            await first.call_tool("release", {})
            self.assertFalse((await second.call_tool("list_apps", {})).isError)
        self.assertIsNone(lease.owner)

    async def test_discovery_ping_and_release_need_no_native_sdk(self):
        factory = Mock(side_effect=RuntimeError("native SDK unavailable"))
        # Even on a machine without the native SDK, clients must discover tools.
        with patch.dict(sys.modules, {"qwen_intelligence": None, "mai_ui": None}):
            async with connect(create_server(factory)) as client:
                await client.send_ping()
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools), 14)
                self.assertTrue({'perform_operation', 'operation_status', 'stop_operation'} <= {t.name for t in tools})
                for _ in range(2):
                    result = await client.call_tool("release", {})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent, {"released": True})
                factory.assert_not_called()

    async def test_bad_arguments_and_unknown_tools_never_allocate_device(self):
        factory = Mock(side_effect=AssertionError("must not allocate device"))
        async with connect(create_server(factory)) as client:
            for name, arguments in (
                ("status", {"unexpected": True}),
                ("click", {"view_id": "v", "element": "1"}),
                ("click", {"view_id": "v", "element": 1, "count": 3}),
                ("scroll", {"view_id": "v", "image_id": "i", "x": 1, "y": 1,
                            "direction": "down", "lines": 51}),
                ("observe", {}),
                ("close", {}),
            ):
                result = await client.call_tool(name, arguments)
                self.assertTrue(result.isError, (name, result))
            factory.assert_not_called()

    async def test_tool_error_does_not_break_discovery_or_release(self):
        bridge = Bridge()
        bridge.status = AsyncMock(side_effect=RuntimeError("DRIVER_OFFLINE: test"))
        bridge.close = AsyncMock()
        async with connect(create_server(lambda: bridge)) as client:
            result = await client.call_tool("status", {})
            self.assertTrue(result.isError)
            self.assertIn("DRIVER_OFFLINE", result.content[0].text)
            self.assertEqual(len((await client.list_tools()).tools), 14)
            self.assertFalse((await client.call_tool("release", {})).isError)
            bridge.close.assert_awaited_once()

    async def test_native_images_are_not_json_wrapped(self):
        bridge = Bridge()
        bridge.observe = AsyncMock(return_value=[
            TextContent(type="text", text=json.dumps({"view_id": "v"})),
            ImageContent(type="image", mimeType="image/png", data="cGl4ZWxz")])
        async with connect(create_server(lambda: bridge)) as client:
            result = await client.call_tool("observe", {"app": "Test", "screenshot": True})
            self.assertFalse(result.isError)
            self.assertEqual(result.content[1].type, "image")
            self.assertEqual(result.content[1].data, "cGl4ZWxz")
            bridge.observe.assert_awaited_once_with("Test", window_id=None, screenshot=True)

    async def test_disconnect_closes_owned_session_under_cancellation(self):
        bridge = Bridge()
        bridge.status = AsyncMock(return_value={"session_active": True})
        cleaned = asyncio.Event()

        async def close():
            # This checkpoint would be cancelled without the lifespan shield.
            await asyncio.sleep(0)
            cleaned.set()

        bridge.close = AsyncMock(side_effect=close)
        async with connect(create_server(lambda: bridge)) as client:
            await client.call_tool("status", {})
        self.assertTrue(cleaned.is_set())

    async def test_connections_do_not_share_views_and_release_allows_reuse(self):
        bridges = []

        def factory():
            bridge = Bridge()
            bridge.target = SimpleNamespace(ui=SimpleNamespace(ax=SimpleNamespace(click=AsyncMock())))
            bridge.view_id = "first" if not bridges else "second"
            bridge.capture = AsyncMock(return_value=[])
            bridge.status = AsyncMock(return_value={"session_active": False})
            bridges.append(bridge)
            return bridge

        server = create_server(factory)
        async with connect(server) as first, connect(server) as second:
            await first.call_tool("status", {})
            await second.call_tool("status", {})
            target = bridges[1].target
            result = await second.call_tool("click", {"view_id": "first", "element": 1})
            self.assertTrue(result.isError)
            target.ui.ax.click.assert_not_awaited()
            await first.call_tool("release", {})
            self.assertEqual(bridges[1].view_id, "second")
            self.assertFalse((await first.call_tool("status", {})).isError)

    async def test_cancelled_input_consumes_view_and_releases_lock(self):
        bridge = Bridge()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        request_ids = []

        async def click(*args, **kwargs):
            request_ids.append(server.request_context.request_id)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        bridge.target = SimpleNamespace(ui=SimpleNamespace(ax=SimpleNamespace(click=click)))
        bridge.view_id = "current"
        server = create_server(lambda: bridge)
        async with connect(server) as client:
            request = asyncio.create_task(client.call_tool("click", {"view_id": "current", "element": 1}))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await client.send_notification(ClientNotification(CancelledNotification(
                    params=CancelledNotificationParams(requestId=request_ids[0], reason="test"))))
                with self.assertRaisesRegex(McpError, "Request cancelled"):
                    await request
                await asyncio.wait_for(cancelled.wait(), 2)
                self.assertIsNone(bridge.view_id)
                self.assertFalse((await client.call_tool("release", {})).isError)
            finally:
                request.cancel()


if __name__ == "__main__":
    unittest.main()
