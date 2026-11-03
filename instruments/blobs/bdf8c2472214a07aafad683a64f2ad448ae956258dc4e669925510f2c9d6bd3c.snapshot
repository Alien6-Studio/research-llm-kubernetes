"""Loopback Ollama forwarding with a separate no-inference transport fixture."""
import asyncio
import base64
import json
import random
import time
from v2.transport import request, serve

# High-entropy padding exceeds the 5,590-byte maximum observed classification response.
PADDING = base64.b64encode(random.Random(20261113).randbytes(6144)).decode()


async def handle(method, path, payload):
    began = time.monotonic()
    if method == 'POST' and path == '/transport-control':
        result = (200, {'transport_control': True, 'padding': PADDING})
    elif path.startswith('/api/'):
        result = await request('http://127.0.0.1:11435' + path,
                               payload if method == 'POST' else None, timeout=180)
    else:
        result = (404, {'reason': 'unknown_endpoint'})
    print(json.dumps({'at': began, 'finished': time.monotonic(), 'path': path,
                      'status': result[0], 'response_json_bytes': len(json.dumps(result[1], separators=(',', ':')).encode())}), flush=True)
    return result


async def main():
    server = await serve(handle, '127.0.0.1', 11437)
    async with server: await server.serve_forever()


if __name__ == '__main__': asyncio.run(main())
