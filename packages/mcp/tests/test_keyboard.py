import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opengcua_mcp.backend import Bridge
from opengcua_mcp.operations import DesktopTools


class KeyboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = SimpleNamespace(
            activate=AsyncMock(),
            ui=SimpleNamespace(input=SimpleNamespace(
                press_key=AsyncMock(return_value={}), text=AsyncMock(return_value={}))))
        self.bridge = Bridge()
        self.bridge.target = self.target
        self.bridge.view_id = "current"
        self.bridge.window_id = "observed-window"
        self.bridge.capture = AsyncMock(return_value=[])
        self.tools = DesktopTools(bridge=self.bridge)

    async def test_stale_view_never_activates_or_types(self):
        with self.assertRaisesRegex(ValueError, "STALE_VIEW"):
            await self.tools.press_key("old", "a", ["cmd"], 7)
        self.target.activate.assert_not_awaited()
        self.target.ui.input.press_key.assert_not_awaited()

    async def test_chord_without_exact_window_refuses_before_activation(self):
        self.bridge.window_id = None
        with self.assertRaisesRegex(ValueError, "WINDOW_REQUIRED"):
            await self.tools.press_key("current", "a", ["cmd"], 7)
        self.target.activate.assert_not_awaited()
        self.target.ui.input.press_key.assert_not_awaited()

    async def test_chord_activates_bound_window_before_foreground_input(self):
        events = []
        self.target.activate.side_effect = lambda: events.append("activate")
        self.target.ui.input.press_key.side_effect = lambda *a, **kw: events.append("key")
        await self.tools.press_key("current", "a", ["cmd"], 7)
        self.assertEqual(events, ["activate", "key"])
        self.target.ui.input.press_key.assert_awaited_once_with(
            "a", modifiers=["cmd"], target=7, delivery="foreground")

    async def test_activation_failure_never_dispatches_chord(self):
        self.target.activate.side_effect = RuntimeError("window closed")
        with self.assertRaisesRegex(RuntimeError, "window closed"):
            await self.tools.press_key("current", "a", ["cmd"], 7)
        self.target.ui.input.press_key.assert_not_awaited()
        self.assertIsNone(self.bridge.view_id)

    async def test_focus_refusal_never_falls_back_or_replays(self):
        self.target.ui.input.press_key.side_effect = RuntimeError("INPUT_REFUSED: focus moved")
        with self.assertRaisesRegex(RuntimeError, "INPUT_REFUSED"):
            await self.tools.press_key("current", "a", ["cmd"], 7)
        self.assertEqual(self.target.ui.input.press_key.await_count, 1)
        self.assertIsNone(self.bridge.view_id)
        self.bridge.capture.assert_not_awaited()

    async def test_plain_key_does_not_activate(self):
        await self.tools.press_key("current", "left", element=7)
        self.target.activate.assert_not_awaited()
        self.target.ui.input.press_key.assert_awaited_once_with(
            "left", modifiers=[], target=7, delivery="auto")

    async def test_typing_requires_current_view_and_exact_window(self):
        with self.assertRaisesRegex(ValueError, "STALE_VIEW"):
            await self.tools.type_text("old", "replacement")
        self.bridge.window_id = None
        with self.assertRaisesRegex(ValueError, "WINDOW_REQUIRED"):
            await self.tools.type_text("current", "replacement")
        self.target.activate.assert_not_awaited()
        self.target.ui.input.text.assert_not_awaited()

    async def test_typing_activates_bound_window_before_foreground_input(self):
        events = []
        self.target.activate.side_effect = lambda: events.append("activate")
        self.target.ui.input.text.side_effect = lambda *a, **kw: events.append("text")
        await self.tools.type_text("current", "replacement", 7)
        self.assertEqual(events, ["activate", "text"])
        self.target.ui.input.text.assert_awaited_once_with(
            "replacement", target=7, delivery="foreground")

    async def test_typing_activation_failure_never_dispatches(self):
        self.target.activate.side_effect = RuntimeError("window closed")
        with self.assertRaisesRegex(RuntimeError, "window closed"):
            await self.tools.type_text("current", "replacement")
        self.target.ui.input.text.assert_not_awaited()
        self.assertIsNone(self.bridge.view_id)

    async def test_typing_focus_refusal_never_replays(self):
        self.target.ui.input.text.side_effect = RuntimeError("INPUT_REFUSED: focus moved")
        with self.assertRaisesRegex(RuntimeError, "INPUT_REFUSED"):
            await self.tools.type_text("current", "replacement")
        self.assertEqual(self.target.ui.input.text.await_count, 1)
        self.assertIsNone(self.bridge.view_id)
        self.bridge.capture.assert_not_awaited()
