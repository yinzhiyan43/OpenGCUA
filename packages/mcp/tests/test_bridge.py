import asyncio
import dataclasses
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from opengcua_mcp.backend import Bridge
from opengcua_mcp.catalog import TOOLS


@dataclasses.dataclass
class Receipt:
    effect: str = "unknown"


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_preserves_active_runtime_without_second_native_connection(self):
        b = Bridge()
        b.embedded = True
        b.start_driver = AsyncMock()
        b.driver = SimpleNamespace(returncode=None)
        b.runtime = object()
        b.session = object()
        b.view_id = 'existing-view'
        with patch('importlib.metadata.version', return_value='test'), \
             patch('opengcua_mcp.diagnostics.host_diagnostics', return_value={'dock': {'count': 1}}), \
             patch.dict('sys.modules', {'mai_ui': None}):
            result = await b.status()
        self.assertTrue(result['session_active'])
        self.assertEqual(result['host']['dock']['count'], 1)
        self.assertEqual(result['driver']['diagnostic'], 'active_session')
        self.assertEqual(b.view_id, 'existing-view')
        b.start_driver.assert_not_awaited()

    async def test_cold_and_repeated_status_never_start_driver_or_refresh_idle_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            app = Path(root) / 'CuaDriver.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '0.36.0'}))
            b = Bridge(driver_app=app)
            b.embedded = True
            b.start_driver = AsyncMock(side_effect=AssertionError('status launched driver'))
            b._command = AsyncMock(side_effect=AssertionError('status spawned process'))
            before = b.last_used
            with patch('importlib.metadata.version', return_value='test'), \
                 patch('opengcua_mcp.diagnostics.host_diagnostics', return_value={'accessibility_trusted': True}), \
                 patch.dict('sys.modules', {'mai_ui': None, 'qwen_intelligence': None}):
                for _ in range(3):
                    result = await b.status()
                    self.assertFalse(result['driver']['running'])
                    self.assertEqual(result['driver']['version'], '0.36.0')
                    self.assertFalse(result['driver']['signature_checked'])
                    self.assertTrue(result['driver']['permissions']['accessibility'])
                    self.assertIsNone(result['driver']['permissions']['screen_recording'])
            self.assertEqual(b.last_used, before)
            self.assertIsNone(b.runtime)
            self.assertIsNone(b.socket_dir)
            b.start_driver.assert_not_awaited()
            b._command.assert_not_awaited()

    async def test_app_inventory_preserves_ids_and_incomplete_flag(self):
        @dataclasses.dataclass
        class App:
            id: str
            label: str
            native_id: str

        @dataclasses.dataclass
        class Inventory:
            apps: tuple
            complete: bool

        apps = (App("app:1:start", "Same name", "example.app"),
                App("app:2:start", "Same name", "example.app"))
        for complete in (False, True):
            with self.subTest(complete=complete):
                b = Bridge()
                b.connect = AsyncMock()
                b.mac = SimpleNamespace(apps=SimpleNamespace(
                    list=AsyncMock(return_value=Inventory(apps, complete))))
                result = json.loads((await b.list_apps())[0].text)
                self.assertEqual(result["complete"], complete)
                self.assertEqual([a["id"] for a in result["apps"]],
                                 ["app:1:start", "app:2:start"])
                self.assertEqual(result["apps"][0]["native_id"], "example.app")
                self.assertIn("apps[].id", result["next"])

    async def test_stale_view_never_dispatches(self):
        b = Bridge()
        b.target, b.view_id = object(), "new"
        operation = AsyncMock()
        with self.assertRaisesRegex(ValueError, "STALE_VIEW"):
            await b.action("old", operation)
        operation.assert_not_awaited()

    async def test_failure_consumes_view_and_does_not_retry(self):
        b = Bridge()
        b.target, b.view_id = object(), "view"
        operation = AsyncMock(side_effect=RuntimeError("uncertain outcome"))
        with self.assertRaisesRegex(RuntimeError, "uncertain outcome"):
            await b.action("view", operation)
        self.assertIsNone(b.view_id)
        with self.assertRaisesRegex(ValueError, "STALE_VIEW"):
            await b.action("view", operation)
        self.assertEqual(operation.await_count, 1)

    async def test_cancelled_action_consumes_view(self):
        b = Bridge()
        b.target, b.view_id = object(), "view"
        with self.assertRaises(asyncio.CancelledError):
            await b.action("view", AsyncMock(side_effect=asyncio.CancelledError()))
        self.assertIsNone(b.view_id)

    async def test_receipt_survives_observation_failure(self):
        b = Bridge()
        b.target, b.view_id = object(), "view"
        b.capture = AsyncMock(side_effect=RuntimeError("capture unavailable"))
        result = await b.action("view", AsyncMock(return_value=Receipt()))
        self.assertIn('"effect": "unknown"', result[0].text)
        self.assertIn("do not repeat", result[1].text)
        self.assertIsNone(b.view_id)

    async def test_image_is_returned_as_mcp_image_without_data_in_text(self):
        b = Bridge()
        await b.collect({"type": "image", "data": b"pixels", "mime_type": "image/png",
                         "image_id": "i1", "width": 3, "height": 2})
        self.assertEqual(b.images, {"i1"})
        self.assertNotIn('"data"', b.output[0].text)
        self.assertEqual(b.output[1].type, "image")
        self.assertEqual(b.output[1].data, "cGl4ZWxz")

    async def test_close_releases_driver_even_when_runtime_close_fails(self):
        b = Bridge()
        b.runtime = type("Runtime", (), {"close": AsyncMock(side_effect=RuntimeError("close"))})()
        driver = type("Driver", (), {"returncode": None, "wait": AsyncMock(),
                                      "terminate": lambda self: setattr(self, "returncode", 0)})()
        b.driver = driver
        b.stop_private_driver = AsyncMock(side_effect=driver.terminate)
        with self.assertRaisesRegex(RuntimeError, "close"):
            await b.close()
        self.assertIsNone(b.runtime)
        self.assertIsNone(b.driver)
        self.assertEqual(driver.returncode, 0)

    async def test_tool_schema_and_annotations(self):
        tools = {t.name: t for t in TOOLS}
        self.assertEqual(len(tools), 14)
        for name in ("status", "list_apps", "list_windows", "observe"):
            self.assertTrue(tools[name].annotations.readOnlyHint)
        for name in ("click", "set_text", "type_text", "press_key", "scroll", "click_image"):
            self.assertFalse(tools[name].annotations.readOnlyHint)
            self.assertIn("view_id", tools[name].inputSchema["required"])


if __name__ == "__main__":
    unittest.main()
