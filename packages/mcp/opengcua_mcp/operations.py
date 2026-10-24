"""Desktop operations owned by one MCP connection, separate from the transport."""
from __future__ import annotations

import asyncio
import time

from .catalog import TOOL_NAMES, MODEL_TOOLS


def native_bridge():
    from .backend import Bridge
    return Bridge()


class DeviceLease:
    """One active desktop owner inside the shared host; never share view tokens."""
    def __init__(self):
        self.owner = None

    def claim(self, owner):
        if self.owner is not None and self.owner is not owner:
            raise ValueError("RESOURCE_BUSY: another MCP connection owns the desktop; "
                             "it must release or become idle before this connection can use it")
        self.owner = owner

    def release(self, owner):
        if self.owner is owner:
            self.owner = None


class DesktopTools:
    def __init__(self, bridge=None, bridge_factory=None, lease=None, model_operations=None):
        self._bridge = bridge
        self._bridge_factory = bridge_factory or native_bridge
        self._lease = lease
        self._calls = asyncio.Lock()
        self.model_operations = model_operations

    @property
    def bridge(self):
        if self._bridge is None:
            self._bridge = self._bridge_factory()
        return self._bridge

    async def call(self, name, arguments):
        if name not in TOOL_NAMES:
            raise ValueError(f"UNKNOWN_TOOL: {name}")
        async with self._calls:
            return await self._call(name, arguments)

    async def _call(self, name, arguments):
        if name in MODEL_TOOLS:
            if self.model_operations is None:
                raise ValueError('MODEL_UNAVAILABLE: use the installed OpenGCUA app MCP entry point')
            if name == 'perform_operation':
                await self.close()
                return await self.model_operations.start(**arguments)
            method = 'status' if name == 'operation_status' else 'stop'
            return await getattr(self.model_operations, method)(**arguments)
        if name == 'status' and self.model_operations is not None and self.model_operations.busy:
            from .diagnostics import host_diagnostics
            import os
            return {'host_pid': os.getppid(), 'server_pid': os.getpid(), 'host': host_diagnostics(),
                    'session_active': True, 'mode': 'model_operation',
                    'operation_id': self.model_operations.active}
        if self._lease is not None and name not in {"release", "status"}:
            self._lease.claim(self)
        try:
            return await getattr(self, name)(**arguments)
        except BaseException:
            # A failed first call must not strand the shared lease without a
            # bridge/reaper. Once a bridge exists, normal idle/close owns cleanup.
            if self._bridge is None and self._lease is not None:
                self._lease.release(self)
            raise

    async def status(self):
        async with self.bridge.lock:
            result = await self.bridge.status()
            if self.model_operations is not None:
                result['model_operations_available'] = True
            return result

    async def list_apps(self):
        async with self.bridge.lock:
            return await self.bridge.list_apps()

    async def list_windows(self, app):
        async with self.bridge.lock:
            return await self.bridge.list_windows(app)

    async def observe(self, app, window_id=None, screenshot=False):
        async with self.bridge.lock:
            return await self.bridge.observe(app, window_id=window_id, screenshot=screenshot)

    async def click(self, view_id, element, button="primary", count=1):
        async with self.bridge.lock:
            return await self.bridge.action(view_id, lambda target:
                target.ui.ax.click(element, button=button, count=count))

    async def set_text(self, view_id, element, text_value):
        async with self.bridge.lock:
            return await self.bridge.action(view_id, lambda target:
                target.ui.ax.set_text(element, text_value))

    async def type_text(self, view_id, text_value, element=None):
        from .keyboard import type_literal_text
        async with self.bridge.lock:
            self.bridge.require_view(view_id)
            if not self.bridge.window_id:
                raise ValueError("WINDOW_REQUIRED: list_windows, then observe with an exact window_id before typing")
            return await self.bridge.action(view_id, lambda target:
                type_literal_text(target, text_value, element))

    async def press_key(self, view_id, key, modifiers=None, element=None):
        async with self.bridge.lock:
            self.bridge.require_view(view_id)
            chord = modifiers or []
            if chord and not self.bridge.window_id:
                raise ValueError("WINDOW_REQUIRED: list_windows, then observe with an exact window_id before a modifier chord")

            async def dispatch(target):
                if chord:
                    await target.activate()
                return await target.ui.input.press_key(key, modifiers=chord, target=element,
                    delivery="foreground" if chord else "auto")

            return await self.bridge.action(view_id, dispatch)

    async def scroll(self, view_id, image_id, x, y, direction, lines=3):
        async with self.bridge.lock:
            self.bridge.require_view(view_id)
            if image_id not in self.bridge.images or not 1 <= lines <= 50:
                raise ValueError("Use a current screenshot image_id and lines between 1 and 50")
            return await self.bridge.action(view_id, lambda target:
                target.ui.vision.scroll(direction, x=x, y=y, image=image_id,
                                        amount=lines, unit="lines"), screenshot=True)

    async def click_image(self, view_id, image_id, x, y, button="primary", count=1):
        async with self.bridge.lock:
            self.bridge.require_view(view_id)
            if image_id not in self.bridge.images:
                raise ValueError("STALE_IMAGE: use an image_id from the current screenshot observation")
            return await self.bridge.action(view_id, lambda target:
                target.ui.vision.click(x, y, image=image_id, button=button, count=count), screenshot=True)

    async def release(self):
        await self.close()
        return {"released": True}

    async def close(self):
        try:
            if self._bridge is not None:
                async with self._bridge.lock:
                    await self._bridge.close()
        finally:
            if self._lease is not None:
                self._lease.release(self)

    async def expire_idle(self):
        while True:
            await asyncio.sleep(5)
            async with self._calls:
                await self.close_if_idle()

    async def close_if_idle(self):
        bridge = self._bridge
        if bridge is not None:
            async with bridge.lock:
                if time.monotonic() - bridge.last_used > bridge.idle_seconds:
                    try:
                        await bridge.close()
                    finally:
                        if self._lease is not None:
                            self._lease.release(self)
