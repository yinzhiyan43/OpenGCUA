"""The desktop and MCP share one actual NativeHost, lifecycle and device lease."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'harness/runtime'))
from native_host import NativeHost
from opengcua_mcp.app_runtime import AppRuntime
from opengcua_mcp.model_operations import TERMINAL
from opengcua_mcp.service import SOCKET_NAME
from test_service import client


class AppRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.events = []
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        async def runner(args, host_control):
            self.args = args
            # Real agent events have an inner task ID and a native transport ID.
            host_control.event_context = {'task_id': 'inner-agent-task'}
            host_control.emit('model_status', stage='waiting')
            self.entered.set()
            await self.finish.wait()
            run = self.root/'runs'/'fixture'
            run.mkdir(parents=True, exist_ok=True)
            (run/'traj.json').write_text(json.dumps({'status': 'success'}))
            host_control.run_dir = run
            host_control.complete_turn('success', 'verified by shared runtime')
            await host_control.follow_up()
        self.native = NativeHost(self.events.append, self.root/'runs', runner=runner)
        self.runtime = AppRuntime(self.native, self.events.append)
        self.config = {'llm_base_url': 'http://127.0.0.1:12345/v1',
                       'model_name': 'fixture-model', 'api_key': 'private-test-secret'}
        with patch('opengcua_mcp.app_runtime.service_directory', return_value=self.root):
            await self.runtime.start()
        async with asyncio.timeout(2):
            while not (self.root/SOCKET_NAME).exists():
                await asyncio.sleep(.01)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.temp.cleanup()

    async def start_operation(self):
        result = await self.runtime.operations.start('Read fixture', 'Return title', [], 'once')
        async with asyncio.timeout(2):
            while not any(e['event'] == 'mcp_configuration' for e in self.events):
                await asyncio.sleep(.01)
        request = next(e for e in self.events if e['event'] == 'mcp_configuration')
        await self.runtime.command({'command': 'mcp_configuration', 'id': request['id'], 'config': self.config})
        await asyncio.wait_for(self.entered.wait(), 2)
        return result

    async def test_same_host_ui_progress_and_socket_survive_clients(self):
        async with client(self.root/SOCKET_NAME) as session:
            self.assertIn('perform_operation', {t.name for t in (await session.list_tools()).tools})
        self.assertFalse(self.runtime.server.done())
        result = await self.start_operation()
        self.assertIs(self.runtime.operations.host, self.native)
        self.assertEqual(self.args.model_name, 'fixture-model')
        with self.assertRaisesRegex(ValueError, 'MCP'):
            await self.runtime.command({'command': 'start', 'task_id': 'desktop', 'text': 'Other task'})
        self.finish.set()
        async with asyncio.timeout(2):
            while self.runtime.operations.busy:
                await asyncio.sleep(.01)
        result = await self.runtime.operations.status(result['operation_id'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['model_requests'], 1)
        terminal = next(e for e in self.events if e['event'] == 'mcp_operation_finished')
        self.assertEqual(terminal['text'], 'verified by shared runtime')
        self.assertNotIn('private-test-secret', json.dumps(self.events))
        self.assertIsNone(self.runtime.lease.owner)
        self.assertFalse(self.runtime.server.done())

    async def test_desktop_task_blocks_mcp_and_releases_after_stop(self):
        await self.runtime.command({'command': 'start', 'task_id': 'desktop', 'text': 'Fixture', 'config': self.config})
        await asyncio.wait_for(self.entered.wait(), 2)
        with self.assertRaisesRegex(ValueError, 'RESOURCE_BUSY'):
            await self.runtime.operations.start('Read', 'Title', [], 'once')
        await self.runtime.command({'command': 'stop', 'task_id': 'desktop'})
        async with asyncio.timeout(2):
            while self.runtime.lease.owner:
                await asyncio.sleep(.01)
        self.assertFalse(self.runtime.operations.busy)

    async def test_ui_stop_cancels_mcp_operation_and_releases_lease(self):
        result = await self.start_operation()
        await self.runtime.command({'command': 'stop', 'task_id': result['operation_id']})
        result = await self.runtime.operations.status(result['operation_id'])
        self.assertEqual(result['state'], 'stopped')
        self.assertIsNone(self.runtime.lease.owner)

    async def test_app_shutdown_closes_socket_and_active_operation(self):
        await self.start_operation()
        await self.runtime.close()
        self.assertFalse((self.root/SOCKET_NAME).exists())
        self.assertFalse(self.runtime.operations.busy)
        self.assertTrue(self.native.task.done())
