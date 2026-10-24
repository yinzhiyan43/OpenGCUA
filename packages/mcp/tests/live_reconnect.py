"""Opt-in installed-app test: same stdio clients reconnect, actual Dock stays singular."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import subprocess
import time

from AppKit import NSRunningApplication
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

APP = '/Applications/OpenGCUA.app'
EXE = APP + '/Contents/MacOS/OpenGCUA'
REPORT = Path(f'/private/tmp/opengcua-service-{os.getuid()}/dock-status.json')
OUTPUT = Path('/private/tmp/opengcua-reconnect-verification.json')


def processes():
    return {int(p[0]): (int(p[1]), p[2]) for line in subprocess.check_output(
        ['ps', '-axo', 'pid=,ppid=,command='], text=True).splitlines()
        if len(p := line.strip().split(None, 2)) == 3}


async def check():
    samples = []
    def sample():
        rows = processes()
        hosts = [p for p, (_, c) in rows.items() if c == EXE]
        workers = [p for p, (parent, c) in rows.items() if parent in hosts and c.endswith('/runtime/native_host.py')]
        item = {'time': time.time(), 'hosts': hosts, 'workers': workers}
        try:
            report = json.loads(REPORT.read_text())
            if report['host_pid'] in hosts and 0 <= time.time() - report['captured_at'] < 5:
                item['dock'] = report['dock']
        except (OSError, ValueError):
            pass
        samples.append(item)
        assert len(hosts) <= 1 and len(workers) <= 1, item
        if item.get('dock', {}).get('status') == 'ok':
            assert item['dock']['count'] <= 1, item
        return rows

    async def monitor():
        while True:
            sample()
            await asyncio.sleep(0.2)

    async def status(client):
        result = await client.call_tool('status', {})
        assert not result.isError, result
        data = json.loads(result.content[0].text)
        assert data['host']['dock']['status'] == 'ok', data['host']
        assert data['host']['dock']['count'] == 1, data['host']
        assert data['host']['dock']['items'][0]['running'], data['host']
        bounds = data['host']['dock']['items'][0]['bounds']
        assert bounds['width'] >= 16 and bounds['height'] >= 16, data['host']
        return data

    async with AsyncExitStack() as stack:
        clients = []
        for _ in range(4):
            r, w = await stack.enter_async_context(stdio_client(StdioServerParameters(command=EXE, args=['--mcp'])))
            client = await stack.enter_async_context(ClientSession(r, w))
            await client.initialize()
            clients.append(client)
        watcher = asyncio.create_task(monitor())
        try:
            initial = await status(clients[0])
            assert not (await clients[0].call_tool('list_apps', {})).isError
            # Regression: doctor must not open a competing native connection.
            assert (await status(clients[0]))['session_active']
            await clients[0].call_tool('release', {})
            before = sample()
            proxies = {p for p, (parent, c) in before.items() if parent == os.getpid() and c == APP + '/Contents/Resources/python/bin/python3 -I -B -u -m opengcua_mcp.launcher'}
            assert len(proxies) >= 4
            first_host = initial['host_pid']
            print(f'PASS four clients, one host {first_host}, actual Dock count=1; status works during active session', flush=True)
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(first_host)
            assert app.terminate()
            async with asyncio.timeout(10):
                while first_host in sample():
                    await asyncio.sleep(.1)
            # Concurrent new requests reconnect all existing stdio channels.
            results = await asyncio.gather(*(c.list_tools() for c in clients))
            assert all(len(r.tools) == 14 for r in results)
            after = await status(clients[0])
            assert after['host_pid'] != first_host
            assert proxies <= sample().keys(), 'A proxy died instead of reconnecting'
            await clients[0].call_tool('release', {})
            print(f"PASS same stdio clients recovered after host quit; new single host {after['host_pid']}, actual Dock count=1", flush=True)
            for client in clients[1:]:
                report = await status(client)
                assert report['host_pid'] == after['host_pid']
                await client.call_tool('release', {})
            print('PASS all four connections reuse the same replacement host and release their drivers', flush=True)
        finally:
            for client in clients:
                await client.call_tool('release', {})
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            OUTPUT.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
            # Surface monitor failures too, rather than hiding them during cleanup.
            if watcher.done() and not watcher.cancelled() and watcher.exception():
                raise watcher.exception()
            sample()
            OUTPUT.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
            print(f'Verification samples: {len(samples)}; report: {OUTPUT}', flush=True)


if __name__ == '__main__':
    asyncio.run(check())
