import asyncio
import dataclasses
import unittest
from unittest.mock import AsyncMock

from opengcua_mcp.backend import Bridge
from opengcua_mcp.server import mcp


@dataclasses.dataclass
class Receipt:
    effect: str = "unknown"


class BridgeTests(unittest.IsolatedAsyncioTestCase):
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
        tools = {t.name: t for t in await mcp.list_tools()}
        self.assertEqual(len(tools), 11)
        for name in ("status", "list_apps", "list_windows", "observe"):
            self.assertTrue(tools[name].annotations.readOnlyHint)
        for name in ("click", "set_text", "type_text", "press_key", "scroll", "click_image"):
            self.assertFalse(tools[name].annotations.readOnlyHint)
            self.assertIn("view_id", tools[name].inputSchema["required"])


if __name__ == "__main__":
    unittest.main()
