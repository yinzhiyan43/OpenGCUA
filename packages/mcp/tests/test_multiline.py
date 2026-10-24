import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from opengcua_mcp.operations import DesktopTools
from opengcua_mcp.backend import Bridge
from opengcua_mcp.keyboard import type_literal_text


class MultilineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events=[]
        self.target=SimpleNamespace(activate=AsyncMock(),ui=SimpleNamespace(input=SimpleNamespace(
            text=AsyncMock(return_value={}),press_key=AsyncMock(return_value={'effect':'unknown'}))))
        @contextmanager
        def clipboard(value):
            self.events.append(('stage',value))
            try: yield
            finally: self.events.append(('restore',))
        self.patch=patch('opengcua_mcp.keyboard.temporary_literal_text',clipboard)
        self.patch.start();self.addCleanup(self.patch.stop)

    async def test_multiline_is_one_paste_preserving_all_line_breaks(self):
        value='\n中文 😀\n\nend\n'
        await type_literal_text(self.target,value)
        self.assertEqual(self.events,[('stage',value),('restore',)])
        self.target.ui.input.text.assert_not_awaited()
        self.target.ui.input.press_key.assert_awaited_once_with('v',modifiers=['cmd'],delivery='foreground')
        self.target.activate.assert_awaited_once()

    async def test_target_focus_does_not_click_or_replace_the_selection(self):
        events=[]
        self.target.ui.input.text.side_effect=lambda *a,**k: events.append('focus')
        self.target.ui.input.press_key.side_effect=lambda *a,**k: events.append('paste')
        await type_literal_text(self.target,'first\nsecond',7)
        self.assertEqual(events,['focus','paste'])
        self.target.ui.input.text.assert_awaited_once_with('',target=7,delivery='foreground')
        self.target.ui.input.press_key.assert_awaited_once_with('v',modifiers=['cmd'],delivery='foreground')

    async def test_crlf_and_cr_normalize_without_dropping_blank_lines(self):
        await type_literal_text(self.target,'a\r\nb\rc\r\n\r\n')
        self.assertEqual(self.events[0],('stage','a\nb\nc\n\n'))

    async def test_newline_only_pastes_exactly_one_newline(self):
        await type_literal_text(self.target,'\n')
        self.assertEqual(self.events[0],('stage','\n'))
        self.assertEqual(self.target.ui.input.press_key.await_count,1)

    async def test_plain_or_empty_text_never_touches_clipboard(self):
        for value in ('unchanged 😀',''):
            await type_literal_text(self.target,value)
        self.assertEqual(self.events,[])
        self.target.ui.input.press_key.assert_not_awaited()
        self.assertEqual(self.target.ui.input.text.await_count,2)

    async def test_focus_failure_never_touches_clipboard_or_pastes(self):
        self.target.ui.input.text.side_effect=RuntimeError('focus refused')
        with self.assertRaisesRegex(RuntimeError,'focus refused'):
            await type_literal_text(self.target,'a\nb',7)
        self.assertEqual(self.events,[])
        self.target.ui.input.press_key.assert_not_awaited()

    async def test_paste_failure_restores_clipboard_and_consumes_view_without_replay(self):
        bridge=Bridge();bridge.target=self.target;bridge.view_id='current';bridge.window_id='window'
        bridge.capture=AsyncMock(return_value=[])
        self.target.ui.input.press_key.side_effect=RuntimeError('INPUT_REFUSED')
        tools = DesktopTools(bridge=bridge)
        with self.assertRaisesRegex(RuntimeError,'INPUT_REFUSED'):
            await tools.type_text('current','a\nb')
        with self.assertRaisesRegex(ValueError,'STALE_VIEW'):
            await tools.type_text('current','a\nb')
        self.assertEqual(self.events,[('stage','a\nb'),('restore',)])
        self.assertEqual(self.target.ui.input.press_key.await_count,1)
        bridge.capture.assert_not_awaited()

    async def test_cancellation_restores_clipboard_and_is_not_retried(self):
        self.target.ui.input.press_key.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await type_literal_text(self.target,'a\nb')
        self.assertEqual(self.events,[('stage','a\nb'),('restore',)])
        self.assertEqual(self.target.ui.input.press_key.await_count,1)
