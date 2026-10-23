"""The host must never attach MCP to an attacker-controlled socket location."""
import os
import asyncio
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from opengcua_mcp import launcher
from opengcua_mcp.launcher import validate_socket


class SocketValidationTests(unittest.TestCase):
    def test_private_socket_accepted_but_writable_directory_rejected(self):
        with tempfile.TemporaryDirectory(prefix='opengcua-bridge-', dir='/private/tmp') as root:
            path = Path(root) / 'mcp.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                validate_socket(path)
                os.chmod(root, 0o777)
                with self.assertRaises(ValueError):
                    validate_socket(path)
                os.chmod(root, 0o700)


    def test_regular_file_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory(prefix='opengcua-bridge-', dir='/private/tmp') as root:
            path = Path(root) / 'mcp.sock'
            path.touch()
            with self.assertRaises(ValueError):
                validate_socket(path)
            path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                actual = Path(root) / 'actual.sock'
                listener.bind(str(actual))
                path.symlink_to(actual)
                with self.assertRaises(ValueError):
                    validate_socket(path)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_eof_drain_accepted_connection(self):
        async def launch(*args, **kwargs):
            reader, writer = await asyncio.open_unix_connection(args[-1])

            async def echo():
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
                writer.close()
                await writer.wait_closed()

            task = asyncio.create_task(echo())
            class Process:
                async def wait(self):
                    await task
            return Process()

        read_fd, write_fd = os.pipe()
        os.write(write_fd, b'{"jsonrpc":"2.0","id":1}\n')
        os.close(write_fd)
        incoming = io.TextIOWrapper(os.fdopen(read_fd, "rb"))
        outgoing = io.TextIOWrapper(io.BytesIO())
        with patch.object(launcher, "bundled_app", return_value=Path("/private/tmp/Test.app")), \
             patch.object(launcher.asyncio, "create_subprocess_exec", side_effect=launch), \
             patch.object(launcher.sys, "stdin", incoming), patch.object(launcher.sys, "stdout", outgoing):
            await asyncio.wait_for(launcher.proxy(), 3)
        self.assertEqual(outgoing.buffer.getvalue(), b'{"jsonrpc":"2.0","id":1}\n')
        outgoing.close()

