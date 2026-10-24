"""One MCP connection owns one SDK session and a private signed driver.

No HTTP listener, arbitrary code tool, automatic driver installation, or TCC edits.
SDK receipts and stale-target checks remain authoritative.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import uuid

from mcp.types import ImageContent, TextContent


DEFAULT_DRIVER = "/Applications/OpenGCUA.app/Contents/Helpers/CuaDriver.app"
DRIVER_REQUIREMENT = ('=anchor apple generic and identifier "com.trycua.driver" '
                      'and certificate leaf[subject.OU] = "YCK386LBJ7"')


def text(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return TextContent(type="text", text=value)


class Bridge:
    def __init__(self, driver_app=None, idle_seconds=120):
        self.driver_app = Path(driver_app or os.environ.get("OPENGCUA_DRIVER_APP", DEFAULT_DRIVER))
        self.idle_seconds = idle_seconds
        self.embedded = os.environ.get("OPENGCUA_EMBEDDED_HOST") == "1"
        self.lock = asyncio.Lock()
        self.driver = self.socket_dir = self.runtime = self.session = self.mac = None
        self.target = self.view_id = None
        self.target_name = self.window_id = None
        self.images = set()
        self.output = []
        self.last_used = time.monotonic()

    async def collect(self, value):
        # SDK calls this only for explicit emit. Never let SDK output touch MCP stdout.
        if value["type"] == "image":
            image_id = value.get("image_id")
            if image_id:
                self.images.add(image_id)
            self.output.append(text({k: v for k, v in value.items() if k != "data"}))
            self.output.append(ImageContent(type="image", mimeType=value["mime_type"],
                                           data=base64.b64encode(value["data"]).decode("ascii")))
        else:
            self.output.append(text(value["text"]))

    async def _command(self, *args):
        proc = await asyncio.create_subprocess_exec(
            *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(stderr.decode(errors="replace")[:2000])
        return stdout.decode().strip()

    async def start_driver(self):
        if self.driver is not None and self.driver.returncode is None:
            return
        if self.driver is not None:
            await self.close()
        executable = self.driver_app / "Contents/MacOS/cua-driver"
        if not executable.is_file():
            raise RuntimeError(f"DRIVER_MISSING: install OpenGCUA.app or set OPENGCUA_DRIVER_APP: {self.driver_app}")
        await self._command("/usr/bin/codesign", "--verify", "--deep", "--strict",
                            "-R", DRIVER_REQUIREMENT, self.driver_app)
        version = await self._command(executable, "--version")
        if version != "cua-driver 0.36.0":
            raise RuntimeError(f"VERSION_MISMATCH: SDK 0.3.1 needs cua-driver 0.36.0; found {version}")
        # Keep the Unix socket below sockaddr_un's path limit and private to this process.
        self.socket_dir = tempfile.TemporaryDirectory(prefix="opengcua-mcp-", dir="/private/tmp")
        try:
            # In the bundled host, --embedded inherits OpenGCUA's TCC identity.
            # Standalone development instead launches the signed CuaDriver app.
            # Never attribute an embedded driver to an unbundled Python process.
            command = ([str(executable), "serve", "--embedded", "--socket", self.socket]
                       if self.embedded else
                       ["/usr/bin/open", "-W", "-n", "-g", "-a", str(self.driver_app),
                        "--args", "serve", "--socket", self.socket])
            self.driver = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            async with asyncio.timeout(12):
                while not Path(self.socket).is_socket():
                    if self.driver.returncode is not None:
                        raise RuntimeError("DRIVER_OFFLINE: signed driver exited during startup")
                    await asyncio.sleep(0.1)
        except BaseException:
            await self.close()
            raise

    @property
    def socket(self):
        return str(Path(self.socket_dir.name) / "driver.sock")

    async def connect(self):
        from qwen_intelligence import Runtime
        self.last_used = time.monotonic()
        await self.start_driver()
        if self.runtime is not None:
            return
        self.runtime = Runtime()
        try:
            await self.runtime.add_macos(name="local-mac", app_path=str(self.driver_app), socket=self.socket)
            self.session = self.runtime.session(output=self.collect)
            self.mac = await self.session.register("local-mac", name="mac")
        except BaseException:
            await self.close()
            raise

    async def status(self):
        from .diagnostics import driver_metadata, host_diagnostics
        # A diagnostic query must not start a native process, open a device
        # session, call doctor, or extend an existing desktop lease's lifetime.
        host = host_diagnostics() if self.embedded else None
        report = driver_metadata(self.driver_app)
        report.update({
            "running": self.driver is not None and self.driver.returncode is None,
            "diagnostic": "active_session" if self.runtime is not None else "passive_snapshot",
            "permissions_rechecked": False,
            "permissions": {
                "accessibility": host.get("accessibility_trusted") if host else None,
                "screen_recording": None,
                "source": "native_host_snapshot" if host else "not_checked",
                "note": "Unknown grants are null. Device permissions and the signed driver "
                        "are checked when an actual desktop operation starts.",
            },
        })
        return {"sdk_version": importlib.metadata.version("qwen-intelligence-sdk"),
                "server_pid": os.getpid(), "host_pid": os.getppid(),
                "host": host,
                "driver_app": str(self.driver_app), "session_active": self.session is not None,
                "permission_host": "OpenGCUA" if self.embedded else "CuaDriver",
                "idle_release_seconds": self.idle_seconds, "driver": report}

    async def list_apps(self):
        await self.connect()
        # The SDK's compact renderer hides IDs for unique labels. macOS only
        # lists running apps (complete=False), so its resolver rejects labels
        # even when they appear in that list. Keep the observed IDs available.
        inventory = dataclasses.asdict(await self.mac.apps.list())
        return [text({**inventory,
                      "next": "Use an exact apps[].id with list_windows and observe. "
                              "Incomplete inventories cannot resolve application names."})]

    async def list_windows(self, app):
        await self.connect()
        target = await self.mac.apps.get(app)
        self.output = []
        await self.session.emit(await target.windows.list())
        return list(self.output)

    async def observe(self, app, *, window_id=None, screenshot=False):
        await self.connect()
        self.invalidate()
        self.target = await self.mac.apps.get(app)
        self.target_name, self.window_id = app, window_id
        if window_id:
            self.target = await self.target.windows.get(window_id)
        return await self.capture(screenshot=screenshot)

    def invalidate(self):
        self.view_id = None
        self.images.clear()

    async def capture(self, *, screenshot=False):
        self.invalidate()
        self.output = []
        await self.session.emit(await self.target.ui.ax.observe(), full=True)
        if screenshot:
            await self.session.emit(await self.target.ui.vision.observe())
        self.view_id = uuid.uuid4().hex
        return [text({"view_id": self.view_id, "app": self.target_name,
                      "window_id": self.window_id, "image_ids": sorted(self.images),
                      "next": "Use this view_id for one action; use only returned element/image IDs."}),
                *self.output]

    def require_view(self, view_id):
        if not self.view_id or view_id != self.view_id or self.target is None:
            raise ValueError("STALE_VIEW: call observe again; use only its current view_id and observed targets")
        self.last_used = time.monotonic()

    async def action(self, view_id, operation, *, screenshot=False):
        self.require_view(view_id)
        target = self.target
        # Consume before dispatch, including failure/cancellation: do not blindly retry an action.
        self.invalidate()
        receipt = await operation(target)
        result = [text({"action_receipt": dataclasses.asdict(receipt) if dataclasses.is_dataclass(receipt) else receipt})]
        try:
            result.extend(await self.capture(screenshot=screenshot))
        except Exception as exc:
            self.invalidate()
            result.append(text({"observation_error": str(exc),
                                "next": "Action was dispatched. Observe again; do not repeat it blindly."}))
        return result

    async def close(self):
        self.invalidate()
        self.target = self.mac = self.session = None
        try:
            if self.runtime is not None:
                await self.runtime.close()
        finally:
            self.runtime = None
            if self.driver is not None and self.driver.returncode is None:
                try:
                    await self.stop_private_driver()
                    await asyncio.wait_for(self.driver.wait(), 3)
                except ProcessLookupError:
                    pass
                except asyncio.TimeoutError:
                    # Only kill the launcher we own; never stop a shared system daemon.
                    self.driver.kill()
                    await self.driver.wait()
            self.driver = None
            if self.socket_dir is not None:
                self.socket_dir.cleanup()
                self.socket_dir = None

    async def stop_private_driver(self):
        """Identify only our exact executable + random private socket before SIGTERM."""
        if self.embedded:
            if self.driver is not None and self.driver.returncode is None:
                self.driver.terminate()
            return
        if self.socket_dir is None:
            return
        expected = f"{self.driver_app / 'Contents/MacOS/cua-driver'} serve --socket {self.socket}"
        rows = await self._command("/bin/ps", "-axo", "pid=,command=")
        for row in rows.splitlines():
            parts = row.strip().split(None, 1)
            if len(parts) == 2 and parts[1] == expected:
                try:
                    os.kill(int(parts[0]), signal.SIGTERM)
                except ProcessLookupError:
                    pass
