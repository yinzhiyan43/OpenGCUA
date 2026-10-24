import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from opengcua_mcp.proxy import relay


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_exit_keeps_stdio_alive_and_never_replays_input(self):
        seen, tasks, writers = [], set(), []
        async def accept(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            writers.append(writer)
            generation = len(writers)
            try:
                while line := await reader.readline():
                    message = json.loads(line)
                    seen.append((generation, message))
                    if message.get('method') == 'tools/call':
                        # Input may have happened just before the process died.
                        break
                    if 'id' in message:
                        writer.write((json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                                                   'result': {'generation': generation}}) + '\n').encode())
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / 'host.sock'
            server = await asyncio.start_unix_server(accept, path=path)
            stdin, output = asyncio.StreamReader(), asyncio.Queue()
            async def connect():
                return await asyncio.open_unix_connection(path)
            proxy = asyncio.create_task(relay(stdin, output.put_nowait, connect))
            def send(message):
                stdin.feed_data((json.dumps({'jsonrpc': '2.0', **message}) + '\n').encode())
            async def reply():
                return json.loads(await asyncio.wait_for(output.get(), 2))
            try:
                send({'id': 1, 'method': 'initialize', 'params': {'protocolVersion': 'test'}})
                self.assertEqual((await reply())['result']['generation'], 1)
                send({'method': 'notifications/initialized'})
                send({'id': 'input-1', 'method': 'tools/call', 'params': {'name': 'type_text'}})
                lost = await reply()
                self.assertEqual(lost['id'], 'input-1')
                self.assertIn('NOT replayed', lost['error']['message'])
                self.assertFalse(proxy.done())
                send({'id': 3, 'method': 'tools/list'})
                self.assertEqual((await reply())['result']['generation'], 2)
                self.assertEqual(sum(m.get('method') == 'tools/call' for _, m in seen), 1)
                second = [m['method'] for g, m in seen if g == 2]
                self.assertEqual(second, ['initialize', 'notifications/initialized', 'tools/list'])
                # An idle host exit also leaves the caller pipe open.
                writers[-1].close()
                await writers[-1].wait_closed()
                await asyncio.sleep(0.05)
                self.assertFalse(proxy.done())
                send({'id': 4, 'method': 'tools/list'})
                self.assertEqual((await reply())['result']['generation'], 3)
            finally:
                stdin.feed_eof()
                await asyncio.wait_for(proxy, 3)
                server.close()
                await server.wait_closed()
                await asyncio.gather(*tuple(tasks))

    async def test_connection_error_does_not_close_client_transport(self):
        stdin, output = asyncio.StreamReader(), asyncio.Queue()
        async def connect():
            raise ConnectionRefusedError('offline')
        task = asyncio.create_task(relay(stdin, output.put_nowait, connect))
        try:
            for request_id in (1, 2):
                stdin.feed_data((json.dumps({'jsonrpc': '2.0', 'id': request_id,
                                            'method': 'initialize'}) + '\n').encode())
                reply = json.loads(await asyncio.wait_for(output.get(), 1))
                self.assertEqual(reply['id'], request_id)
                self.assertIn('request was not sent', reply['error']['message'])
                self.assertFalse(task.done())
        finally:
            stdin.feed_eof()
            await task
