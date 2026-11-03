"""Operator-bound Ollama forwarding with a no-inference transport fixture."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time
import uuid
from urllib.parse import parse_qsl, urlsplit
from v2.identity import capture, verify_worktree
from v2.transport import request, serve

# High-entropy padding exceeds the 5,590-byte maximum observed classification response.
PADDING = base64.b64encode(random.Random(20261113).randbytes(6144)).decode()


class InferenceProxy:
    """Keep generation lifetimes visible beyond a downstream disconnection.

    This ledger observes native HTTP completion, not Ollama's internal queue.
    An unknown or unsuccessful terminal outcome stays uncertain for the entire
    process lifetime: an empty active set alone never clears that uncertainty.
    """

    def __init__(self, identity=None):
        self.identity = dict(identity or {})
        self.clock_domain = 'native-inference-proxy:' + uuid.uuid4().hex
        self.proxy_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.started_total = 0
        self.uncertain_total = 0
        self.active = {}
        self.completed = []

    async def _generate(self, method, path, payload):
        # This digest is an identity only; the original payload is forwarded.
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False).encode('utf-8')
        self.started_total += 1
        row = {'sequence': self.started_total,
               'request_sha256': hashlib.sha256(canonical).hexdigest(),
               'started': time.monotonic()}
        self.active[row['sequence']] = row
        status, response, error = None, None, None
        try:
            result = await request('http://127.0.0.1:11435' + path,
                                   payload if method == 'POST' else None, timeout=180)
            status, response = result
            return result
        except BaseException as failure:
            error = type(failure).__name__
            raise
        finally:
            done = isinstance(response, dict) and response.get('done') is True
            uncertain = error is not None or status != 200 or not done
            self.uncertain_total += int(uncertain)
            self.completed.append({**row, 'finished': time.monotonic(),
                                   'status': status, 'response': response,
                                   'done': done, 'error': error,
                                   'uncertain': uncertain})
            del self.active[row['sequence']]

    def _state(self, method, path, payload):
        target = urlsplit(path)
        try:
            if method != 'GET' or payload is not None or target.fragment or target.netloc or target.scheme:
                raise ValueError('Expected a local GET without a body')
            pairs = parse_qsl(target.query, keep_blank_values=True,
                              strict_parsing=True, max_num_fields=2)
            options = dict(pairs)
            if len(options) != len(pairs) or set(options) - {'after', 'records'}:
                raise ValueError('Unknown or repeated state parameter')
            after = options.get('after', '0')
            if not re.fullmatch(r'0|[1-9][0-9]*', after) or options.get('records', '0') not in ('0', '1'):
                raise ValueError('Invalid state parameter')
            after = int(after)
            if after > self.started_total:
                raise ValueError('Sequence is outside this proxy ledger')
        except ValueError:
            return 400, {'reason': 'invalid_study_state_query'}
        if not self.identity:
            return 503, {'reason': 'proxy_source_identity_unavailable'}
        state = {**self.identity, 'proxy_sha256': self.proxy_sha256,
                 'clock_domain': self.clock_domain, 'clock': 'time.monotonic',
                 'started_total': self.started_total,
                 'finished_total': len(self.completed),
                 'uncertain_total': self.uncertain_total,
                 'active': [dict(row) for _, row in sorted(self.active.items())]}
        if options.get('records', '0') == '1':
            state['records'] = [dict(row) for row in self.completed if row['sequence'] > after]
        return 200, state

    async def handle(self, method, path, payload):
        began = time.monotonic()
        if urlsplit(path).path == '/study-state':
            return self._state(method, path, payload)
        if method == 'POST' and path == '/transport-control':
            result = (200, {'transport_control': True, 'padding': PADDING})
        elif method == 'POST' and urlsplit(path).path == '/api/generate':
            result = await self._generate(method, path, payload)
        elif path.startswith('/api/'):
            result = await request('http://127.0.0.1:11435' + path,
                                   payload if method == 'POST' else None, timeout=180)
        else:
            result = (404, {'reason': 'unknown_endpoint'})
        print(json.dumps({'at': began, 'finished': time.monotonic(), 'path': path,
                          'status': result[0], 'response_json_bytes': len(json.dumps(result[1], separators=(',', ':')).encode())}), flush=True)
        return result


_default_proxy = InferenceProxy()


async def handle(method, path, payload):
    return await _default_proxy.handle(method, path, payload)


async def main():
    host = os.environ.get('STUDY_PROXY_BIND', '127.0.0.1')
    port = int(os.environ.get('STUDY_PROXY_PORT', '11437'))
    if not 1 <= port <= 65535:
        raise ValueError('Proxy port must be between 1 and 65535')
    root = Path(__file__).resolve().parents[1]
    snapshot = capture(root)
    verify_worktree(root, snapshot.source_id)
    proxy = InferenceProxy(snapshot.metadata())
    server = await serve(proxy.handle, host, port)
    async with server: await server.serve_forever()


if __name__ == '__main__': asyncio.run(main())
