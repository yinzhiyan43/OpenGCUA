"""Bundle entry point: stdio client -> private socket -> LaunchServices app host.

The host stays a child of the signed OpenGCUA executable. Its embedded driver
therefore uses the same macOS permission identity as the desktop application.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile


def bundled_app() -> Path:
    app = Path(sys.executable).resolve().parents[4]
    if app.suffix != ".app" or not (app / "Contents/MacOS/OpenGCUA").is_file():
        raise RuntimeError("This entry point must run from OpenGCUA.app's bundled Python")
    return app


def validate_socket(path: Path):
    parent = path.parent
    info = parent.lstat()
    if (parent.parent != Path("/private/tmp") or not parent.name.startswith("opengcua-bridge-")
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("Invalid private MCP connection directory")
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Invalid private MCP socket")


def host(path: Path):
    app = bundled_app()
    validate_socket(path)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(path))
    os.dup2(connection.fileno(), 0)
    os.dup2(connection.fileno(), 1)
    connection.close()
    os.environ["OPENGCUA_DRIVER_APP"] = str(app / "Contents/Helpers/CuaDriver.app")
    os.environ["OPENGCUA_EMBEDDED_HOST"] = "1"
    os.execv(sys.executable, [sys.executable, "-I", "-B", "-u", "-m", "opengcua_mcp"])


async def proxy():
    app = bundled_app()
    loop = asyncio.get_running_loop()
    connected = loop.create_future()
    active = []

    def accept(reader, writer):
        if connected.done():
            writer.close()
        else:
            active.append(writer)
            connected.set_result((reader, writer))

    with tempfile.TemporaryDirectory(prefix="opengcua-bridge-", dir="/private/tmp") as directory:
        path = Path(directory) / "mcp.sock"
        server = await asyncio.start_unix_server(accept, path=path)
        os.chmod(path, 0o600)
        launcher = None
        stdin_transport = None
        tasks = []
        try:
            launcher = await asyncio.create_subprocess_exec(
                "/usr/bin/open", "-W", "-n", "-g", "-a", str(app),
                "--args", "--mcp-host", str(path),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=sys.stderr)
            reader, writer = await asyncio.wait_for(connected, 20)
            server.close()
            # Python 3.12 waits for accepted connections too. Drain this host
            # before wait_closed(), otherwise initialization would deadlock.
            stdin = asyncio.StreamReader()
            stdin_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer)

            async def send():
                while data := await stdin.read(65536):
                    writer.write(data)
                    await writer.drain()
                writer.write_eof()

            async def receive():
                while data := await reader.read(65536):
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()

            tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if tasks[0] in done:
                # EOF lets FastMCP release the SDK lease and driver before exiting.
                await asyncio.wait_for(tasks[1], 15)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if stdin_transport:
                stdin_transport.close()
            for writer in active:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            if launcher is not None:
                try:
                    await asyncio.wait_for(launcher.wait(), 15)
                except asyncio.TimeoutError:
                    launcher.terminate()
                    await launcher.wait()


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
    group.add_argument("--host", type=Path)
    group.add_argument("--install-codex", action="store_true")
    args = parser.parse_args()
    if args.host:
        host(args.host)
    elif args.install_codex:
        install_codex()
    else:
        try:
            asyncio.run(run_proxy())
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    main()
