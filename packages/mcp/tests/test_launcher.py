"""Private service endpoints, concurrent startup, and stdio disconnect behavior."""
import asyncio
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from opengcua_mcp import launcher
from opengcua_mcp.service import check_socket, open_lock


class SocketValidationTests(unittest.TestCase):
    def test_launches_normal_app_without_forcing_another_instance(self):
        self.assertEqual(launcher.host_launch_command(Path('/Applications/OpenGCUA.app')),
                         ['/usr/bin/open', '-g', '-a', '/Applications/OpenGCUA.app'])

    def test_private_socket_and_permissions(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / 'mcp.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                os.chmod(path, 0o600)
                check_socket(path)
                os.chmod(root, 0o777)
                with self.assertRaises(ValueError):
                    check_socket(path)
                os.chmod(root, 0o700)
                os.chmod(path, 0o666)
                with self.assertRaises(ValueError):
                    check_socket(path)

    def test_regular_file_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / 'mcp.sock'
            path.touch()
            with self.assertRaises(ValueError):
                check_socket(path)
            path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                actual = Path(root) / 'actual.sock'
                listener.bind(str(actual))
                os.chmod(actual, 0o600)
                path.symlink_to(actual)
                with self.assertRaises(ValueError):
                    check_socket(path)
            lock = Path(root) / 'lock'
            lock.symlink_to(actual)
            with self.assertRaises(OSError):
                open_lock(lock)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_eof_drain(self):
        async def echo(reader, writer):
            while data := await reader.read(65536):
                writer.write(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / 'socket'
            server = await asyncio.start_unix_server(echo, path=path)
            reader, writer = await asyncio.open_unix_connection(path)
            read_fd, write_fd = os.pipe()
            os.write(write_fd, b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
            os.close(write_fd)
            incoming = io.TextIOWrapper(os.fdopen(read_fd, 'rb'))
            outgoing = io.TextIOWrapper(io.BytesIO())
            try:
                with patch.object(launcher, 'connect_shared', return_value=(reader, writer)), \
                     patch.object(launcher.sys, 'stdin', incoming), \
                     patch.object(launcher.sys, 'stdout', outgoing):
                    await asyncio.wait_for(launcher.proxy(), 3)
                self.assertEqual(outgoing.buffer.getvalue(), b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
            finally:
                incoming.close()
                outgoing.close()
                server.close()
                await server.wait_closed()

    async def test_concurrent_clients_start_only_one_host_and_reuse_it(self):
        servers, handlers = [], set()
        async def accept(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.remove(task)

        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            root = Path(root)
            async def launch(*args, **kwargs):
                await asyncio.sleep(0.05)  # Other callers must wait on startup.lock.
                server = await asyncio.start_unix_server(accept, path=root / launcher.SOCKET_NAME)
                os.chmod(root / launcher.SOCKET_NAME, 0o600)
                servers.append(server)
                class Process:
                    returncode = 0
                    async def communicate(self):
                        return b'', b''
                return Process()

            clients = []
            try:
                with patch.object(launcher, 'service_directory', return_value=root), \
                     patch.object(launcher, 'bundled_app', return_value=Path('/private/tmp/Test.app')), \
                     patch.object(launcher.asyncio, 'create_subprocess_exec', side_effect=launch) as start:
                    clients = await asyncio.wait_for(asyncio.gather(
                        *(launcher.connect_shared() for _ in range(8))), 3)
                    self.assertEqual(start.await_count, 1)
                    clients.append(await launcher.connect_shared())
                    self.assertEqual(start.await_count, 1)
            finally:
                for _, writer in clients:
                    writer.close()
                    await writer.wait_closed()
                for server in servers:
                    server.close()
                    await server.wait_closed()
                await asyncio.gather(*tuple(handlers))
