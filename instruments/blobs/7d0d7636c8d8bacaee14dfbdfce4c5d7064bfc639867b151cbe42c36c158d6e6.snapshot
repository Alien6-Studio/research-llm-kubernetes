"""Small asynchronous HTTP/1.1 JSON transport for the controlled local experiment."""
import asyncio
import json
from urllib.parse import urlsplit


async def request(url, payload=None, timeout=10, sent=None):
    target = urlsplit(url)
    if target.scheme != 'http':
        raise ValueError('The study transport accepts local HTTP endpoints only')
    writer = None
    async with asyncio.timeout(max(.001, timeout)):
        try:
            reader, writer = await asyncio.open_connection(target.hostname, target.port or 80, limit=32 * 1024 * 1024)
            body = b'' if payload is None else json.dumps(payload, separators=(',', ':')).encode()
            path = target.path or '/'
            header = (f"{'GET' if payload is None else 'POST'} {path} HTTP/1.1\r\n"
                      f'Host: {target.hostname}\r\nConnection: close\r\nContent-Type: application/json\r\n'
                      f'Content-Length: {len(body)}\r\n\r\n').encode()
            writer.write(header + body)
            await writer.drain()
            if sent:
                sent()
            status = int((await reader.readline()).split()[1])
            headers = {}
            while line := await reader.readline():
                if line == b'\r\n':
                    break
                key, value = line.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            if 'chunked' in headers.get('transfer-encoding', ''):
                chunks = []
                while size := int((await reader.readline()).split(b';')[0], 16):
                    chunks.append(await reader.readexactly(size))
                    await reader.readexactly(2)
                raw = b''.join(chunks)
            elif 'content-length' in headers:
                raw = await reader.readexactly(int(headers['content-length']))
            else:
                raw = await reader.read()
            return status, json.loads(raw)
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass


async def serve(handler, host='0.0.0.0', port=8080):
    async def connection(reader, writer):
        try:
            first = await asyncio.wait_for(reader.readline(), 10)
            method, path, _ = first.decode().split()
            headers = {}
            while line := await reader.readline():
                if line == b'\r\n':
                    break
                key, value = line.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            size = int(headers.get('content-length', '0'))
            if not 0 <= size <= 24 * 1024 * 1024:
                raise ValueError('Invalid body size')
            body = await reader.readexactly(size)
            payload = json.loads(body) if body else None
            status, data = await handler(method, path, payload)
        except Exception as error:
            status, data = 500, {'reason': type(error).__name__}
        raw = json.dumps(data, separators=(',', ':')).encode()
        try:
            writer.write(f'HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n'.encode() + raw)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
    return await asyncio.start_server(connection, host, port, limit=32 * 1024 * 1024)
