"""Loopback-only native inference observations, bracketed by remote trial RPCs."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
import uuid

from v2.model import BASE, NativeObserver, hardware, json_http, command
from v2.transport import serve


class Monitor:
    def __init__(self):
        self.identifier = uuid.uuid4().hex
        self.source_commit = command(['git', 'rev-parse', 'HEAD']).strip()
        self.code_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.active = None

    def metadata(self):
        return {'clock_domain': 'native-inference-monitor:' + self.identifier,
                'source_commit': self.source_commit, 'monitor_sha256': self.code_sha256,
                'hardware': hardware(), 'model_state': json_http(BASE + '/api/ps'),
                'version': json_http(BASE + '/api/version'),
                'scope': 'Native inference process CPU/RSS and API model placement. GPU energy and other applications remain unmeasured.'}

    async def handle(self, method, path, payload):
        if method == 'GET' and path == '/metadata': return 200, self.metadata()
        if path == '/start':
            if self.active: return 409, {'reason': 'another_trial_is_monitored'}
            observer = NativeObserver()
            observer.__enter__()
            self.active = (payload['trial_id'], observer, time.monotonic())
            return 200, {'trial_id': payload['trial_id'], 'clock_domain': 'native-inference-monitor:' + self.identifier}
        if path == '/stop':
            if not self.active or self.active[0] != payload['trial_id']:
                return 409, {'reason': 'trial_identity_mismatch'}
            trial, observer, began = self.active
            observer.__exit__()
            self.active = None
            return 200, {'samples': observer.samples, 'metadata': {'trial_id': trial,
                'clock_domain': 'native-inference-monitor:' + self.identifier,
                'started': began, 'finished': time.monotonic(), 'source_commit': self.source_commit,
                'monitor_sha256': self.code_sha256,
                'clock_rule': 'The native sample interval is bracketed by start/stop RPCs; no cross-host monotonic subtraction.'}}
        return 404, {'reason': 'unknown_endpoint'}


async def main():
    monitor = Monitor()
    server = await serve(monitor.handle, '127.0.0.1', 11436)
    async with server: await server.serve_forever()


if __name__ == '__main__': asyncio.run(main())
