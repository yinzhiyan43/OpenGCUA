"""Opt-in: four concurrent installed MCP clients share one host/worker/driver."""
import asyncio
from pathlib import Path
import subprocess
import json
import os

from AppKit import NSApplicationActivationPolicyRegular, NSRunningApplication
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

APP = Path('/Applications/OpenGCUA.app')
EXECUTABLE = str(APP / 'Contents/MacOS/OpenGCUA')


def processes():
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,command='], text=True)
    return {int(parts[0]): (int(parts[1]), parts[2]) for row in rows.splitlines()
            if len(parts := row.strip().split(None, 2)) == 3}


async def check():
    before = processes()
    loop = asyncio.get_running_loop()
    ready = [loop.create_future() for _ in range(4)]
    queues = [asyncio.Queue() for _ in ready]

    async def worker(index):
        try:
            params = StdioServerParameters(command=EXECUTABLE, args=['--mcp'])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 14
                    ready[index].set_result(True)
                    try:
                        while command := await queues[index].get():
                            name, arguments, reply = command
                            try:
                                reply.set_result(await client.call_tool(name, arguments))
                            except BaseException as exc:
                                reply.set_exception(exc)
                                raise
                    finally:
                        await client.call_tool('release', {})
        except BaseException as exc:
            if not ready[index].done():
                ready[index].set_exception(exc)
            raise

    async def call(index, name, **arguments):
        reply = loop.create_future()
        await queues[index].put((name, arguments, reply))
        return await asyncio.wait_for(reply, 40)

    tasks = [asyncio.create_task(worker(i)) for i in range(4)]
    host_pid = None
    try:
        await asyncio.wait_for(asyncio.gather(*ready), 40)
        rows = processes()
        hosts = [pid for pid, (_, cmd) in rows.items() if cmd == EXECUTABLE]
        assert len(hosts) == 1, hosts
        host_pid = hosts[0]
        workers = [pid for pid, (parent, cmd) in rows.items()
                   if parent == host_pid and cmd.endswith('/runtime/native_host.py')]
        assert len(workers) == 1, workers
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(host_pid)
        assert app is not None and app.activationPolicy() == NSApplicationActivationPolicyRegular
        print(f'PASS 4 concurrent MCP clients: one native host {host_pid}, one worker {workers[0]}, one Dock-visible host', flush=True)

        status = await call(0, 'status')
        assert not status.isError, status
        identity = status.structuredContent
        assert identity['host_pid'] == host_pid and identity['server_pid'] == workers[0]
        assert not (await call(0, 'list_apps')).isError
        busy = await call(1, 'status')
        assert busy.isError and 'RESOURCE_BUSY' in str(busy.content)
        drivers = [pid for pid, (parent, cmd) in processes().items()
                   if parent == workers[0] and 'cua-driver serve' in cmd]
        assert len(drivers) == 1, drivers
        print('PASS one driver; another connection cannot take over the desktop', flush=True)
        await call(0, 'release')
        for index in range(1, 4):
            result = await call(index, 'status')
            assert not result.isError, result
            assert result.structuredContent['server_pid'] == workers[0]
            assert result.structuredContent['host_pid'] == host_pid
            await call(index, 'release')
        await queues[0].put(None)
        await tasks[0]
        assert not (await call(1, 'status')).isError
        await call(1, 'release')
        print('PASS all clients reuse the same host; closing one client leaves others usable', flush=True)
    finally:
        for index, task in enumerate(tasks):
            if not task.done():
                await queues[index].put(None)
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), 20)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    assert host_pid in processes(), 'Disconnecting MCP must not quit the normal app'
    leftovers = [pid for pid, (parent, cmd) in processes().items()
                 if parent == os.getpid() and cmd.endswith(' -m opengcua_mcp.launcher')]
    assert not leftovers, leftovers
    print('PASS normal application stays running; no test proxies left behind', flush=True)


if __name__ == '__main__':
    asyncio.run(check())
