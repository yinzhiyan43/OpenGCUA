"""Private request/reply pipe to the signed app's preferences and Keychain."""
import asyncio
import json
import uuid


class NativeConfiguration:
    def __init__(self, source, output):
        self.source, self.output = source, output
        self.reader = self.transport = None
        self.lock = asyncio.Lock()

    async def load(self):
        async with self.lock:
            if self.reader is None:
                self.reader = asyncio.StreamReader(limit=2 * 1024 * 1024)
                protocol = asyncio.StreamReaderProtocol(self.reader)
                self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                    lambda: protocol, self.source)
            request_id = uuid.uuid4().hex
            self.output.write(json.dumps({'operation': 'operation_configuration', 'id': request_id}) + '\n')
            self.output.flush()
            async with asyncio.timeout(60):
                while line := await self.reader.readline():
                    reply = json.loads(line)
                    if reply.get('id') != request_id:
                        continue
                    if 'error' in reply:
                        raise ValueError(reply['error'])
                    if not isinstance(reply.get('config'), dict):
                        raise ValueError('Invalid native operation configuration')
                    return reply['config']
                raise ConnectionError('Native configuration pipe closed')

    def close(self):
        if self.transport is not None:
            self.transport.close()
