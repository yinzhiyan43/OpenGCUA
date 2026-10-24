"""Opt-in installed-app check: connecting/querying status creates no control driver."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import subprocess
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

APP = '/Applications/OpenGCUA.app'
EXE = APP + '/Contents/MacOS/OpenGCUA'
REPORT = Path('/private/tmp/opengcua-passive-status-verification.json')
DOCK = Path(f'/private/tmp/opengcua-service-{os.getuid()}/dock-status.json')


def processes():
    return {int(parts[0]): (int(parts[1]), parts[2]) for row in subprocess.check_output(
        ['ps', '-axo', 'pid=,ppid=,command='], text=True).splitlines()
        if len(parts := row.strip().split(None, 2)) == 3}


async def check():
    samples, replies = [], []

    def sample():
        rows = processes()
        hosts = [pid for pid, (_, command) in rows.items() if command == EXE]
        workers = [pid for pid, (parent, command) in rows.items()
                   if parent in hosts and command.endswith('/runtime/native_host.py')]
        descendants = set(hosts)
        while True:
            children = {pid for pid, (parent, _) in rows.items() if parent in descendants}
            if children <= descendants:
                break
            descendants |= children
        drivers = [pid for pid in descendants
                   if '/CuaDriver.app/Contents/MacOS/cua-driver' in rows[pid][1]]
        item = {'time': time.time(), 'hosts': hosts, 'workers': workers, 'drivers': drivers}
        try:
            dock = json.loads(DOCK.read_text())
            if dock['host_pid'] in hosts and 0 <= time.time() - dock['captured_at'] < 5:
                item['dock'] = dock['dock']
        except (OSError, ValueError):
            pass
        samples.append(item)
        assert len(hosts) == len(workers) == 1, item
        assert not drivers, 'A passive query started a native control process: ' + repr(item)
        if item.get('dock', {}).get('status') == 'ok':
            assert item['dock']['count'] == 1, item
        return hosts[0], workers[0]

    async def monitor():
        while True:
            sample()
            await asyncio.sleep(.05)

    host, worker = sample()
    watcher = asyncio.create_task(monitor())
    passed = False
    try:
        # All connections exist concurrently. Status must not own a device lease.
        async with AsyncExitStack() as stack:
            clients = []
            for _ in range(4):
                r, w = await stack.enter_async_context(stdio_client(
                    StdioServerParameters(command=EXE, args=['--mcp'])))
                client = await stack.enter_async_context(ClientSession(r, w))
                await client.initialize()
                assert len((await client.list_tools()).tools) == 14
                clients.append(client)
            for _ in range(3):
                for client in clients:
                    result = await client.call_tool('status', {})
                    assert not result.isError, result
                    data = result.structuredContent or json.loads(result.content[0].text)
                    replies.append(data)
                    assert (data['host_pid'], data['server_pid']) == (host, worker)
                    assert data['driver']['diagnostic'] == 'passive_snapshot'
                    assert data['driver']['running'] is False
                    assert data['driver']['version'] == '0.36.0'
                    assert data['session_active'] is False
                    assert data['host']['dock']['status'] == 'ok'
                    assert data['host']['dock']['count'] == 1
                    bounds = data['host']['dock']['items'][0]['bounds']
                    assert bounds['width'] >= 16 and bounds['height'] >= 16
                await asyncio.sleep(.5)
            for client in clients:
                await client.call_tool('release', {})
        await asyncio.sleep(.5)
        assert sample() == (host, worker), 'Disconnect must preserve the running application'
        if watcher.done():
            watcher.result()
        passed = True
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        REPORT.write_text(json.dumps({'passed': passed, 'samples': samples, 'replies': replies}, indent=2))
    print(f'PASS {len(replies)} status queries across four MCP connections; '
          f'{len(samples)} samples; zero control-driver processes; '
          f'one unchanged host {host}, worker {worker}, and normal Dock icon', flush=True)
    print(REPORT, flush=True)


if __name__ == '__main__':
    asyncio.run(check())
