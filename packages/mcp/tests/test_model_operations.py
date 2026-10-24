"""Exercise the real native host lifecycle with an in-memory model runner."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'harness/runtime'))
from native_host import NativeHost
from opengcua_mcp.model_operations import ModelOperations, TERMINAL
from opengcua_mcp.operations import DeviceLease, DesktopTools
from opengcua_mcp.server import create_server
from mcp.shared.memory import create_connected_server_and_client_session


class ModelOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lease = DeviceLease()
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.args = None
        self.config = AsyncMock(return_value={'llm_base_url': 'http://127.0.0.1:12345/v1',
            'model_name': 'test-local-model', 'llm_protocol': 'openai', 'api_key': 'unit-secret',
            'max_steps': 999, 'api_retries': 99})
        async def runner(args, host_control):
            self.args = args
            host_control.emit('model_status', stage='waiting', text='Requesting model')
            self.entered.set()
            await self.finish.wait()
            await self.complete(host_control)
        self.runner = runner
        def factory(send, runs):
            return NativeHost(send, runs, runner=self.runner)
        self.ops = ModelOperations(self.lease, self.config, host_factory=factory, runs_dir=self.root/'runs')

    async def asyncTearDown(self):
        await self.ops.close()
        self.temp.cleanup()

    async def complete(self, control):
        run = self.root/'runs'/'fixture'
        run.mkdir(parents=True, exist_ok=True)
        # Native session completion may carry only a status word after turn_finished.
        (run/'traj.json').write_text(json.dumps({'status': 'success', 'final_answer': 'success'}))
        control.run_dir = run
        control.complete_turn('success', 'Verified fixture')
        self.assertIsNone(await control.follow_up())

    async def start(self, **extra):
        return await self.ops.start('Read the fixture', 'Return its title',
            ['Do not click or send messages'], 'request-1', **extra)

    async def final(self, operation_id):
        async with asyncio.timeout(3):
            while True:
                result = await self.ops.status(operation_id)
                if result['state'] in TERMINAL:
                    return result
                await asyncio.sleep(0.01)

    async def test_uses_configured_model_bounds_scope_and_real_host_completion(self):
        result = await self.start(max_steps=2)
        await asyncio.wait_for(self.entered.wait(), 2)
        self.assertEqual(self.args.model_name, 'test-local-model')
        self.assertEqual(self.args.max_steps, 2)
        self.assertEqual(self.args.api_retries, 0)
        self.assertIn('Do not click or send messages', self.args.task)
        self.assertIn('Return its title', self.args.task)
        self.assertIs(self.lease.owner, self.ops)
        self.finish.set()
        result = await self.final(result['operation_id'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['model_requests'], 1)
        self.assertEqual(result['answer'], 'Verified fixture')
        self.assertNotIn('unit-secret', json.dumps(result))
        self.assertIsNone(self.lease.owner)

    async def test_duplicate_submission_never_runs_twice_and_conflict_rejected(self):
        first = await self.start()
        second = await self.start()
        self.assertEqual(first['operation_id'], second['operation_id'])
        with self.assertRaisesRegex(ValueError, 'REQUEST_CONFLICT'):
            await self.ops.start('Different operation', 'Return its title', [], 'request-1')
        await asyncio.wait_for(self.entered.wait(), 2)
        self.config.assert_awaited_once()
        self.finish.set()
        await self.final(first['operation_id'])
        self.assertEqual((await self.start())['operation_id'], first['operation_id'])
        self.config.assert_awaited_once()

    async def test_question_stops_and_returns_control_to_harness(self):
        async def runner(args, host_control):
            await host_control.ask('Which contact?')
            self.fail('A reply must not be invented')
        self.runner = runner
        first = await self.start()
        result = await self.final(first['operation_id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['blocker']['text'], 'Which contact?')
        self.assertIsNone(self.lease.owner)

    async def test_foreground_approval_is_not_automatically_granted(self):
        async def runner(args, host_control):
            await host_control.choose_approval(SimpleNamespace(request_id='approval-1',
                app_id='fixture', window_id='window', text='Allow foreground input?'))
            self.fail('Foreground input was not authorized')
        self.runner = runner
        result = await self.final((await self.start())['operation_id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['blocker']['kind'], 'approval')

    async def test_timeout_survives_native_hosts_cancel_handler(self):
        result = await self.final((await self.start(max_seconds=0.05))['operation_id'])
        self.assertEqual(result['state'], 'timed_out')
        self.assertIsNone(self.lease.owner)

    async def test_immediate_stop_releases_lease_and_is_idempotent(self):
        first = await self.start()
        result = await self.ops.stop(first['operation_id'])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual((await self.ops.stop(first['operation_id']))['state'], 'stopped')
        self.assertIsNone(self.lease.owner)

    async def test_diagnostics_cannot_steal_active_operation_and_release_does_not_cancel_it(self):
        first = await self.start()
        desktop = DesktopTools(bridge_factory=Mock(side_effect=AssertionError('No new driver')),
                               lease=self.lease, model_operations=self.ops)
        with self.assertRaisesRegex(ValueError, 'RESOURCE_BUSY'):
            await desktop.call('list_apps', {})
        self.assertEqual((await desktop.call('status', {}))['operation_id'], first['operation_id'])
        await desktop.call('release', {})
        self.assertIs(self.lease.owner, self.ops)

    async def test_existing_diagnostic_owner_blocks_operation_without_loading_config(self):
        owner = object()
        self.lease.claim(owner)
        with self.assertRaisesRegex(ValueError, 'RESOURCE_BUSY'):
            await self.start()
        self.config.assert_not_awaited()
        self.lease.release(owner)

    async def test_error_redacts_credentials(self):
        async def runner(args, host_control):
            raise ValueError('Failure contains unit-secret')
        self.runner = runner
        result = await self.final((await self.start())['operation_id'])
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('unit-secret', json.dumps(result))
        self.assertIn('[REDACTED]', result['answer'])

    async def test_mcp_disconnect_keeps_bounded_operation_queryable_on_new_connection(self):
        server = create_server(lease=self.lease, model_operations=self.ops)
        async with create_connected_server_and_client_session(server) as first:
            bad = await first.call_tool('perform_operation', {'instruction': 'bad'})
            self.assertTrue(bad.isError)
            self.config.assert_not_awaited()
            accepted = await first.call_tool('perform_operation', {'instruction': 'Read fixture',
                'expected_result': 'Title', 'constraints': ['No input'], 'request_id': 'wire-1'})
            self.assertFalse(accepted.isError)
            operation_id = accepted.structuredContent['operation_id']
        self.assertTrue(self.ops.busy)
        async with create_connected_server_and_client_session(server) as second:
            status = await second.call_tool('operation_status', {'operation_id': operation_id})
            self.assertFalse(status.isError)
            stopped = await second.call_tool('stop_operation', {'operation_id': operation_id})
            self.assertEqual(stopped.structuredContent['state'], 'stopped')
            missing = await second.call_tool('operation_status', {'operation_id': 'old-service-operation'})
            self.assertTrue(missing.isError)
            self.assertIn('do not resubmit blindly', missing.content[0].text)
