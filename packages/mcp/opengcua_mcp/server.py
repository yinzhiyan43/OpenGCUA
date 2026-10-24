"""MCP protocol endpoint. Discovery never imports the native desktop SDK."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stdout, suppress
import logging
import sys

import anyio
from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server

from .catalog import TOOLS, INSTRUCTIONS
from .operations import DesktopTools


def create_server(bridge_factory=None, lease=None, model_operations=None) -> Server:
    """Create an endpoint; every connection gets its own lazily allocated bridge."""
    @asynccontextmanager
    async def lifespan(_server):
        desktop = DesktopTools(bridge_factory=bridge_factory, lease=lease, model_operations=model_operations)
        desktop.catalog_announced = False
        reaper = asyncio.create_task(desktop.expire_idle())
        try:
            yield desktop
        finally:
            # Requests are cancelled on disconnect; shield the driver's cleanup.
            with anyio.CancelScope(shield=True):
                reaper.cancel()
                try:
                    with suppress(asyncio.CancelledError):
                        await reaper
                finally:
                    await desktop.close()

    server = Server("OpenGCUA", version="0.3.0", instructions=INSTRUCTIONS,
                    lifespan=lifespan)

    @server.list_tools()
    async def list_tools():
        return [tool.model_copy(deep=True) for tool in TOOLS]

    @server.call_tool(validate_input=True)
    async def call_tool(name, arguments):
        # The SDK validates the schema and converts exceptions to isError results.
        # Cancellation propagates; input is never replayed.
        desktop = server.request_context.lifespan_context
        if not desktop.catalog_announced:
            desktop.catalog_announced = True
            # A surviving stdio proxy may have reinitialized against an updated
            # host while its client still caches the previous tool catalogue.
            await server.request_context.session.send_tool_list_changed()
        return await desktop.call(name, arguments)

    return server


async def serve_stdio():
    server = create_server()
    async with stdio_server() as (reader, writer):
        # The transport has captured stdout. Incidental SDK prints go to stderr.
        with redirect_stdout(sys.stderr):
            await server.run(reader, writer, initialization_options(server))


def initialization_options(server):
    return server.create_initialization_options(NotificationOptions(tools_changed=True))


def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        asyncio.run(serve_stdio())
    except KeyboardInterrupt:
        pass
