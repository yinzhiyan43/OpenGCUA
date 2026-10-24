"""One user-owned Unix-socket host, with isolated MCP sessions and one device lease."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stdout, redirect_stderr
import fcntl
import logging
import os
from pathlib import Path
import signal
import stat
import sys
import time

import anyio
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage

from .operations import DeviceLease
from .server import create_server, initialization_options

IDLE_SECONDS = 30
SOCKET_NAME = "mcp-v1.sock"


def service_directory():
    path = Path(f"/private/tmp/opengcua-service-{os.getuid()}")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    check_directory(path)
    return path


def check_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("Invalid private MCP service directory")


def check_socket(path):
    check_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("Invalid private MCP service socket")


def open_lock(path):
    check_directory(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        os.close(fd)
        raise ValueError("Invalid MCP service lock")
    return fd


def check_peer(writer):
    # macOS getpeereid authenticates the local connection without credentials.
    sock = writer.get_extra_info("socket")
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                               ctypes.POINTER(ctypes.c_uint)]
    libc.getpeereid.restype = ctypes.c_int
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if libc.getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise OSError(ctypes.get_errno(), "Cannot authenticate MCP socket peer")
    if uid.value != os.getuid():
        raise PermissionError("MCP peer belongs to another user")


@asynccontextmanager
async def socket_transport(reader, writer):
    incoming, read_stream = anyio.create_memory_object_stream(0)
    write_stream, outgoing = anyio.create_memory_object_stream(0)

    async def receive():
        async with incoming:
            while line := await reader.readline():
                try:
                    message = SessionMessage(JSONRPCMessage.model_validate_json(line))
                except Exception as exc:
                    message = exc
                await incoming.send(message)

    async def send():
        async with outgoing:
            async for message in outgoing:
                writer.write((message.message.model_dump_json(
                    by_alias=True, exclude_none=True) + "\n").encode())
                await writer.drain()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(receive)
        tasks.start_soon(send)
        try:
            yield read_stream, write_stream
        finally:
            tasks.cancel_scope.cancel()


async def serve(path, *, idle_seconds=IDLE_SECONDS, bridge_factory=None, stop=None, configuration=None,
                lease=None, model_operations=None):
    """Caller holds host.lock for the entire lifetime, including socket removal."""
    lease = lease or DeviceLease()
    from .model_operations import ModelOperations
    if model_operations is None and configuration is not None:
        model_operations = ModelOperations(lease, configuration)
    connections = set()
    writers = set()
    stop = stop or asyncio.Event()
    last_empty = time.monotonic()
    logger = logging.getLogger(__name__)

    async def connection(reader, writer):
        nonlocal last_empty
        task = asyncio.current_task()
        connections.add(task)
        writers.add(writer)
        try:
            check_peer(writer)
            server = create_server(bridge_factory=bridge_factory, lease=lease, model_operations=model_operations)
            async with socket_transport(reader, writer) as (incoming, outgoing):
                await server.run(incoming, outgoing, initialization_options(server))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MCP client disconnected with an error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
            connections.discard(task)
            writers.discard(writer)
            if not connections:
                last_empty = time.monotonic()

    if path.exists() or path.is_symlink():
        check_socket(path)
        path.unlink()  # Only a lock-owning host can retire an old socket.
    # Bind without accepting connections until restrictive permissions are set.
    server = await asyncio.start_unix_server(connection, path=path,
                                             limit=64 * 1024 * 1024, start_serving=False)
    os.chmod(path, 0o600)
    await server.start_serving()

    async def idle():
        if idle_seconds is None:
            await stop.wait()  # Desktop app owns the runtime lifetime.
            return
        while not stop.is_set():
            await asyncio.sleep(min(1, idle_seconds))
            if model_operations is not None and model_operations.busy:
                continue
            if not connections and time.monotonic() - last_empty >= idle_seconds:
                stop.set()

    reaper = asyncio.create_task(idle())
    try:
        await stop.wait()
    finally:
        server.close()
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        if model_operations is not None:
            await model_operations.close()
        # Closing channels wakes idle MCP readers, including SDK cancellation
        # scopes that otherwise wait for the client to disconnect on host quit.
        for writer in tuple(writers):
            writer.close()
        await server.wait_closed()
        remaining = tuple(connections)
        if remaining:
            _, pending = await asyncio.wait(remaining, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
        path.unlink(missing_ok=True)


def run_host(app):
    root = service_directory()
    fd = open_lock(root / "host.lock")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # A launch race must never start another server or driver.
        os.environ["OPENGCUA_DRIVER_APP"] = str(app / "Contents/Helpers/CuaDriver.app")
        os.environ["OPENGCUA_EMBEDDED_HOST"] = "1"
        os.environ['GENERALIST_DRIVER_APP'] = os.environ['OPENGCUA_DRIVER_APP']
        sys.path.insert(0, str(app / 'Contents/Resources/runtime'))
        from .configuration import NativeConfiguration
        configuration = NativeConfiguration(sys.stdin, sys.stdout)

        async def run():
            stop = asyncio.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            try:
                await serve(root / SOCKET_NAME, stop=stop, configuration=configuration.load)
            finally:
                configuration.close()

        # Match native_host: console output stays out of the private protocol;
        # execution evidence is stored in the app's private run directory.
        with open(os.devnull, 'w') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            asyncio.run(run())
    finally:
        os.close(fd)
