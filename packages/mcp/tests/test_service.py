"""Real socket transport with isolated sessions, device ownership and idle shutdown."""
import asyncio
from contextlib import asynccontextmanager
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mcp import ClientSession
from opengcua_mcp.backend import Bridge
from opengcua_mcp.service import (SOCKET_NAME, open_lock, run_host, serve,
                                  socket_transport)


@asynccontextmanager
async def client(path):
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        async with socket_transport(reader, writer) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                await session.initialize()
                yield session
    finally:
        writer.close()
        await writer.wait_closed()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_closes_connected_client_and_releases_bridge(self):
        bridge = Bridge()
        bridge.status = AsyncMock(return_value={'ok': True})
        bridge.close = AsyncMock()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / SOCKET_NAME
            stop = asyncio.Event()
            host = asyncio.create_task(serve(path, bridge_factory=lambda: bridge, stop=stop))
            try:
                async with asyncio.timeout(3):
                    while not path.exists():
                        await asyncio.sleep(0.01)
                    async with client(path) as session:
                        self.assertFalse((await session.call_tool('status', {})).isError)
                        stop.set()
                        await asyncio.shield(host)
                        self.assertFalse(path.exists())
                        bridge.close.assert_awaited()
            finally:
                stop.set()
                await host

    async def test_real_socket_connections_reuse_service_and_release_owner_on_eof(self):
        bridges = []
        def factory():
            bridge = Bridge()
            bridge.status = AsyncMock(return_value={'ok': True})
            bridge.list_apps = AsyncMock(return_value=[])
            bridges.append(bridge)
            return bridge
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / SOCKET_NAME
            stop = asyncio.Event()
            host = asyncio.create_task(serve(path, idle_seconds=0.3, bridge_factory=factory, stop=stop))
            try:
                async with asyncio.timeout(3):
                    while not path.exists():
                        await asyncio.sleep(0.01)
                    async with client(path) as second:
                        async with client(path) as first:
                            self.assertFalse((await first.call_tool('list_apps', {})).isError)
                            self.assertFalse((await second.call_tool('status', {})).isError)
                            self.assertTrue((await second.call_tool('list_apps', {})).isError)
                            # A connected client keeps the shared host alive.
                            await asyncio.sleep(0.4)
                            self.assertFalse(host.done())
                        # EOF cleanup completes before the departed owner's channel closes.
                        async with asyncio.timeout(2):
                            while (await second.call_tool('list_apps', {})).isError:
                                await asyncio.sleep(0.01)
                    await host
                self.assertFalse(path.exists())
                self.assertEqual(len(bridges), 2)
            finally:
                stop.set()
                await host

    async def test_host_lock_refuses_second_worker(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            root = Path(root)
            fd = open_lock(root / 'host.lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch('opengcua_mcp.service.service_directory', return_value=root), \
                     patch('opengcua_mcp.service.asyncio.run') as run:
                    run_host(Path('/Applications/OpenGCUA.app'))
                    run.assert_not_called()
            finally:
                import os
                os.close(fd)
