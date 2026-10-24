"""Private native configuration pipe framing; no credentials or network access."""
import asyncio
import io
import json
import os
import unittest

from opengcua_mcp.configuration import NativeConfiguration


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_pipe_ignores_stale_response_and_serializes_requests(self):
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, 'r')
        output = io.StringIO()
        channel = NativeConfiguration(source, output)
        self.assertIsNone(channel.reader)
        async def reply_once(index, key):
            async with asyncio.timeout(2):
                while len(output.getvalue().splitlines()) <= index:
                    await asyncio.sleep(0.001)
            request = json.loads(output.getvalue().splitlines()[index])
            self.assertEqual(request['operation'], 'operation_configuration')
            payload = json.dumps({'id': 'stale', 'config': {'model_name': 'wrong'}}) + '\n'
            payload += json.dumps({'id': request['id'], 'config': {'model_name': key, 'api_key': 'private'}}) + '\n'
            os.write(write_fd, payload.encode())
        try:
            first = asyncio.create_task(channel.load())
            second = asyncio.create_task(channel.load())
            await reply_once(0, 'first')
            await reply_once(1, 'second')
            self.assertEqual((await first)['model_name'], 'first')
            self.assertEqual((await second)['model_name'], 'second')
            self.assertNotIn('private', output.getvalue())
        finally:
            channel.close()
            os.close(write_fd)

    async def test_eof_returns_error_instead_of_hanging(self):
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, 'r')
        channel = NativeConfiguration(source, io.StringIO())
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(ConnectionError, 'pipe closed'):
                await asyncio.wait_for(channel.load(), 2)
        finally:
            channel.close()
