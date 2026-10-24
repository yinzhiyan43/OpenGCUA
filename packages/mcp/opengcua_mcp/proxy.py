"""Keep client stdio alive across shared-host exits, without replaying calls."""
from __future__ import annotations

import asyncio
import json


async def relay(stdin, emit, connect):
    reader = writer = receive = None
    incoming = asyncio.create_task(stdin.readline())
    pending = {}
    initialize = None
    initialized = False

    def error(request_id, message):
        emit((json.dumps({"jsonrpc": "2.0", "id": request_id,
                          "error": {"code": -32000, "message": message}}) + "\n").encode())

    async def disconnect():
        nonlocal reader, writer, receive
        if receive is not None:
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
        receive = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        reader = writer = None
        for request_id in pending.values():
            error(request_id, "CONNECTION_LOST: shared OpenGCUA service disconnected. "
                  "The call may have run and was NOT replayed. Observe again before "
                  "any further input; the next request can reconnect.")
        pending.clear()

    def response(line):
        nonlocal initialized
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("Expected JSON-RPC object")
        if "id" in message and "method" not in message:
            pending.pop(json.dumps(message["id"]), None)
            if initialize and message["id"] == initialize["id"] and "result" in message:
                initialized = True
        emit(line)

    async def attach():
        nonlocal reader, writer
        reader, writer = await connect()
        if initialize and initialized:
            # Only the protocol handshake is repeated. Views/session state and
            # tool calls are never replayed into the new desktop connection.
            writer.write((json.dumps(initialize) + "\n").encode())
            await writer.drain()
            async with asyncio.timeout(25):
                while line := await reader.readline():
                    reply = json.loads(line)
                    if reply.get("id") == initialize["id"]:
                        if "error" in reply:
                            raise ConnectionError("MCP reinitialization failed")
                        writer.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                        await writer.drain()
                        return
                raise ConnectionError("MCP service closed during reinitialization")

    try:
        while True:
            done, _ = await asyncio.wait(
                [incoming] + ([receive] if receive is not None else []),
                return_when=asyncio.FIRST_COMPLETED)
            if receive in done:
                try:
                    line = receive.result()
                    if not line:
                        await disconnect()
                    else:
                        response(line)
                        receive = asyncio.create_task(reader.readline())
                except (ConnectionError, OSError, ValueError):
                    await disconnect()
            if incoming not in done:
                continue
            line = incoming.result()
            if not line:
                # Client EOF still drains replies already in flight.
                if writer is not None:
                    try:
                        writer.write_eof()
                        async with asyncio.timeout(15):
                            while True:
                                reply = await receive if receive is not None else await reader.readline()
                                receive = None
                                if not reply:
                                    break
                                response(reply)
                    except (ConnectionError, OSError, ValueError, asyncio.TimeoutError):
                        pass
                break
            incoming = asyncio.create_task(stdin.readline())
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Expected JSON-RPC object")
            except ValueError:
                error(None, "Invalid JSON-RPC object")
                continue
            if message.get("method") == "initialize":
                initialize, initialized = message, False
            if writer is None:
                # Old cancellation/response notifications cannot address a new
                # host. Reconnect only for an explicit new client request.
                if "id" not in message or "method" not in message:
                    continue
                try:
                    await attach()
                except (ConnectionError, OSError, ValueError, asyncio.TimeoutError) as exc:
                    await disconnect()
                    error(message["id"], f"CONNECTION_UNAVAILABLE: {exc}; request was not sent")
                    continue
            if "id" in message and "method" in message:
                pending[json.dumps(message["id"])] = message["id"]
            try:
                writer.write(line)
                await writer.drain()
                if receive is None:
                    receive = asyncio.create_task(reader.readline())
            except (ConnectionError, OSError):
                await disconnect()
    finally:
        incoming.cancel()
        await asyncio.gather(incoming, return_exceptions=True)
        await disconnect()
