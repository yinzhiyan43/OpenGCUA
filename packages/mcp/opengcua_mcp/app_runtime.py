"""MCP inside the desktop app's existing Python runtime and NativeHost."""
import asyncio
import fcntl
import os
import uuid

from .model_operations import ModelOperations
from .operations import DeviceLease
from .service import SOCKET_NAME, open_lock, service_directory, serve


class AppRuntime:
    def __init__(self, host, send):
        self.host, self.send = host, send
        self.lease = DeviceLease()
        self.stop = asyncio.Event()
        self.pending = {}
        self.commands = asyncio.Lock()
        self.cleanups = set()
        self.fd = self.server = None
        self.operations = ModelOperations(self.lease, self.configuration,
            host_factory=lambda _send, _runs: self.host, runs_dir=host.runs_dir,
            lifecycle=self.send)
        self.host.send = self.event

    def event(self, event):
        operation = self.operations.active
        if operation and event.get('native_task_id', event.get('task_id')) == operation:
            self.operations.event(operation, event)
        self.send(event)

    async def configuration(self):
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.send({'event': 'mcp_configuration', 'id': request_id})
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(request_id, None)

    async def command(self, command):
        kind = command.get('command')
        if kind == 'mcp_configuration':
            future = self.pending.get(command.get('id'))
            if future and not future.done():
                if 'error' in command:
                    future.set_exception(ValueError(command['error']))
                elif isinstance(command.get('config'), dict):
                    future.set_result(command['config'])
                else:
                    future.set_exception(ValueError('Invalid app model configuration'))
            return
        async with self.commands:
            if self.operations.busy:
                if command.get('task_id') != self.operations.active or kind not in {
                        'stop', 'pause', 'resume', 'overlay_ack'}:
                    raise ValueError('MCP 操作正在执行；可暂停或停止，请结束后再开始其他任务。')
                if kind == 'stop':
                    await self.operations.stop(self.operations.active)
                elif self.operations.host is not None:
                    await self.host.command(command)
                return
            if kind == 'start':
                self.lease.claim(self)
                try:
                    await self.host.command(command)
                except BaseException:
                    if not self.host.task or self.host.task.done():
                        self.lease.release(self)
                    raise
                task = self.host.task
                def done(completed):
                    cleanup = asyncio.create_task(self.release_desktop(completed))
                    self.cleanups.add(cleanup)
                    cleanup.add_done_callback(self.cleanups.discard)
                task.add_done_callback(done)
                return
            await self.host.command(command)

    async def release_desktop(self, completed):
        async with self.commands:
            if self.host.task is completed and self.lease.owner is self:
                try:
                    await self.host.close()
                finally:
                    self.lease.release(self)

    async def start(self):
        root = service_directory()
        self.fd = open_lock(root / 'host.lock')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('OpenGCUA runtime is already running')
        self.server = asyncio.create_task(serve(root / SOCKET_NAME, idle_seconds=None,
            stop=self.stop, lease=self.lease, model_operations=self.operations))
        # Surface bind failures before the desktop declares the runtime ready.
        await asyncio.sleep(0)
        if self.server.done():
            await self.server

    async def close(self):
        self.stop.set()
        try:
            if self.server:
                await self.server
            await self.host.close()
            if self.cleanups:
                await asyncio.gather(*self.cleanups)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.cancel()
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
