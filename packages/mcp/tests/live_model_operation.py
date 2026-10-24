"""Opt-in installed-app model operation; caller supplies a bounded, authorized fixture task."""
import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(args):
    records = []
    parameters = StdioServerParameters(command='/Applications/OpenGCUA.app/Contents/MacOS/OpenGCUA', args=['--mcp'])
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        names = {t.name for t in (await client.list_tools()).tools}
        assert {'perform_operation', 'operation_status', 'stop_operation'} <= names
        async def call(name, values):
            response = await client.call_tool(name, values)
            assert not response.isError, response
            result = response.structuredContent or json.loads(response.content[0].text)
            records.append({'tool': name, 'result': result})
            args.report.write_text(json.dumps(records, ensure_ascii=False, indent=2))
            return result
        operation_id = None
        try:
            initial = await call('perform_operation', {'instruction': args.instruction,
                'expected_result': args.expected_result, 'constraints': args.constraint,
                'request_id': args.request_id or 'live-' + uuid.uuid4().hex, 'max_steps': args.max_steps,
                'max_seconds': args.max_seconds, 'foreground_allowed': args.foreground_allowed})
            operation_id = initial['operation_id']
            print('Accepted operation:', operation_id, flush=True)
            sequence = 0
            async with asyncio.timeout(args.max_seconds + 50):
                while True:
                    result = await call('operation_status', {'operation_id': operation_id,
                        'after_sequence': sequence, 'wait_seconds': 10})
                    sequence = result['sequence']
                    print(json.dumps({k: result[k] for k in ('state', 'model', 'model_requests', 'sequence')}, ensure_ascii=False), flush=True)
                    status = await call('status', {})
                    assert status['host']['dock']['status'] == 'ok', status
                    assert status['host']['dock']['count'] == 1, status
                    if result['state'] in {'completed', 'failed', 'stopped', 'blocked', 'timed_out'}:
                        print('Result:', result['answer'], flush=True)
                        assert result['state'] == 'completed', result
                        assert result['model_requests'] > 0, result
                        if args.contains:
                            assert args.contains in result['answer'], result
                        break
            print('PASS real configured-model operation and actual Dock count=1', flush=True)
        finally:
            if operation_id is not None:
                await client.call_tool('stop_operation', {'operation_id': operation_id})
            await client.call_tool('release', {})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--expected-result', required=True)
    parser.add_argument('--constraint', action='append', required=True)
    parser.add_argument('--max-steps', type=int, default=4)
    parser.add_argument('--max-seconds', type=int, default=180)
    parser.add_argument('--foreground-allowed', action='store_true')
    parser.add_argument('--request-id')
    parser.add_argument('--contains')
    parser.add_argument('--report', type=Path, default=Path('/private/tmp/opengcua-model-operation-verification.json'))
    asyncio.run(check(parser.parse_args()))
