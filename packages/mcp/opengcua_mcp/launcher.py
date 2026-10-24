"""Lightweight stdio channels share one signed LaunchServices background host."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from .service import (SOCKET_NAME, check_peer, check_socket, open_lock,
                      run_host, service_directory)


def bundled_app() -> Path:
    app = Path(sys.executable).resolve().parents[4]
    if app.suffix != ".app" or not (app / "Contents/MacOS/OpenGCUA").is_file():
        raise RuntimeError("This entry point must run from OpenGCUA.app's bundled Python")
    return app


def host_launch_command(app):
    # MCP belongs to the normal application. LaunchServices reuses it if open;
    # never create a second native application for a connection.
    return ["/usr/bin/open", "-g", "-a", str(app)]


async def connect_shared():
    app = bundled_app()
    root = service_directory()
    path = root / SOCKET_NAME
    fd = open_lock(root / "startup.lock")
    try:
        async with asyncio.timeout(25):
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)

            async def connect():
                check_socket(path)
                reader, writer = await asyncio.open_unix_connection(path, limit=64 * 1024 * 1024)
                try:
                    check_peer(writer)
                except BaseException:
                    writer.close()
                    await writer.wait_closed()
                    raise
                return reader, writer

            try:
                return await connect()
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            # Dock retires the exited app asynchronously after its PID/socket
            # disappear. Coalesce launches under startup.lock and allow that
            # retirement before registering a replacement native application.
            await asyncio.sleep(1.5)
            try:
                return await connect()
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            # open returns after launch submission; the singleton host owns its
            # own lifetime and is never terminated when one client disconnects.
            launch = await asyncio.create_subprocess_exec(
                *host_launch_command(app),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            _, error = await launch.communicate()
            if launch.returncode:
                raise RuntimeError(error.decode(errors="replace"))
            while True:
                try:
                    return await connect()
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(0.05)
    finally:
        os.close(fd)


async def proxy():
    from .proxy import relay
    stdin_transport = None
    try:
        stdin = asyncio.StreamReader(limit=64 * 1024 * 1024)
        stdin_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer)
        def emit(data):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        await relay(stdin, emit, connect_shared)
    finally:
        if stdin_transport:
            stdin_transport.close()


def install_codex():
    executable = bundled_app() / "Contents/MacOS/OpenGCUA"
    codex = shutil.which("codex")
    if not codex:
        codex = next((p for p in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex")
                      if os.access(p, os.X_OK)), None)
    if not codex:
        raise RuntimeError("Codex CLI was not found. Install it, then try connecting again.")
    subprocess.run([codex, "mcp", "add", "opengcua", "--", str(executable), "--mcp"], check=True)


async def run_proxy():
    task = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
    await proxy()


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--host", action="store_true")
    group.add_argument("--install-codex", action="store_true")
    args = parser.parse_args()
    if args.host:
        run_host(bundled_app())
    elif args.install_codex:
        install_codex()
    else:
        try:
            asyncio.run(run_proxy())
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    main()
