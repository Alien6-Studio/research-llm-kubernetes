"""An observed, non-queuing native concurrency budget shared by all clients.

The original forwarding and native ledger remain byte-preserved in
v2.inference_proxy. This extension records every API attempt, including refusals,
and links admitted attempts to that native ledger. Client disconnection cannot
release a permit. An uncertain native outcome quarantines it until process reset.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlsplit

from v2.identity import capture, verify_worktree
from v2.inference_proxy import InferenceProxy
from v2.transport import serve


class AdmissionProxy(InferenceProxy):
    def __init__(self, identity=None, study_identity=None):
        super().__init__(identity)
        self.study_identity = dict(study_identity or {})
        self.admission_proxy_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.mode, self.capacity, self.trial_id = 'bypass', 1, 'technical-warmup'
        self.configured = False
        self.admission_started = 0
        self.admission_active = {}
        self.admission_records = []
        self.occupied = self.quarantined = 0
        self.configuration_record = None

    def admission_state(self, method, path, payload):
        if not self.identity.get('source_id') or not self.study_identity.get('source_id'):
            return 503, {'reason': 'admission_source_identity_unavailable'}
        target = urlsplit(path)
        try:
            if method != 'GET' or payload is not None or target.fragment or target.netloc or target.scheme:
                raise ValueError('Expected local GET')
            pairs = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            options = dict(pairs)
            after = options.get('after', '0')
            if (len(options) != len(pairs) or set(options) - {'after', 'records'}
                    or not re.fullmatch(r'0|[1-9][0-9]*', after)
                    or options.get('records', '0') not in ('0', '1')
                    or int(after) > self.admission_started):
                raise ValueError('Invalid admission state query')
            after = int(after)
        except ValueError:
            return 400, {'reason': 'invalid_admission_state_query'}
        state = {'schema': 'alien6-shared-admission-state-v1', **self.study_identity,
            'base_source_id': self.identity.get('source_id'),
            'admission_proxy_sha256': self.admission_proxy_sha256,
            'clock': 'time.monotonic', 'clock_domain': self.clock_domain,
            'mode': self.mode, 'capacity': self.capacity, 'trial_id': self.trial_id,
            'configured': self.configured, 'configuration_record': self.configuration_record,
            'started_total': self.admission_started, 'finished_total': len(self.admission_records),
            'active': [dict(row) for _, row in sorted(self.admission_active.items())],
            'occupied': self.occupied, 'quarantined': self.quarantined}
        # One event-loop turn, with no await: avoid cross-ledger races between
        # separate HTTP reads. Preserve all native records, including warmup;
        # the verifier uses its recorded sequence baseline, never drops evidence.
        native_path = '/study-state?after=0&records=' + options.get('records', '0')
        code, native = self._state('GET', native_path, None)
        if code != 200:
            return code, native
        state['native_state'] = native
        if options.get('records', '0') == '1':
            state['records'] = [dict(row) for row in self.admission_records if row['sequence'] > after]
        return 200, state

    def configure(self, method, payload):
        if not self.identity.get('source_id') or not self.study_identity.get('source_id'):
            return 503, {'reason': 'admission_source_identity_unavailable'}
        if method != 'POST' or not isinstance(payload, dict) or set(payload) != {'mode', 'capacity', 'trial_id', 'study_source_id'}:
            return 400, {'reason': 'invalid_admission_configuration'}
        if (payload['mode'] not in ('bypass', 'global') or type(payload['capacity']) is not int
                or not 1 <= payload['capacity'] <= 4 or not isinstance(payload['trial_id'], str)
                or not re.fullmatch(r'admission-[a-z0-9-]+', payload['trial_id'])
                or payload['study_source_id'] != self.study_identity.get('source_id')):
            return 400, {'reason': 'invalid_admission_configuration'}
        if self.configured or self.active or self.uncertain_total or self.admission_active or self.occupied or self.quarantined:
            return 409, {'reason': 'admission_epoch_not_fresh_idle'}
        self.mode, self.capacity, self.trial_id = payload['mode'], payload['capacity'], payload['trial_id']
        self.configured = True
        self.configuration_record = {'at': time.monotonic(), **payload,
            'native_started_before': self.started_total, 'api_started_before': self.admission_started}
        return 200, dict(self.configuration_record)

    async def _admit(self, method, path, payload):
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        self.admission_started += 1
        sequence = self.admission_started
        row = {'sequence': sequence, 'trial_id': self.trial_id, 'mode': self.mode, 'capacity': self.capacity,
               'clock_domain': self.clock_domain, 'request_sha256': hashlib.sha256(canonical).hexdigest(),
               'received': time.monotonic(), 'occupied_before': self.occupied,
               'native_sequence': None, 'decision': None, 'status': None, 'error': None,
               'permit_released': False, 'quarantined': False}
        if self.mode == 'global' and self.occupied >= self.capacity:
            row.update(decision='rejected', status=429, finished=time.monotonic(),
                       occupied_after=self.occupied)
            self.admission_records.append(row)
            return 429, {'reason': 'shared_native_capacity_exhausted', 'correct': False,
                         'admission_sequence': sequence, 'study_trial_id': self.trial_id}
        # No await separates allocation from the inherited native sequence write.
        row.update(decision='admitted', native_sequence=self.started_total + 1)
        self.occupied += 1
        self.admission_active[sequence] = row
        try:
            status, raw = await super()._generate(method, path, payload)
            row['status'] = status
            return status, raw
        except BaseException as error:
            row['error'] = type(error).__name__
            raise
        finally:
            terminal = next((item for item in reversed(self.completed)
                             if item['sequence'] == row['native_sequence']), None)
            known = (isinstance(terminal, dict) and terminal['request_sha256'] == row['request_sha256']
                     and terminal.get('status') == 200 and terminal.get('done') is True
                     and terminal.get('error') is None and terminal.get('uncertain') is False)
            if known:
                self.occupied -= 1
                row['permit_released'] = True
            else:
                self.quarantined += 1
                row['quarantined'] = True
            row.update(finished=time.monotonic(), occupied_after=self.occupied)
            self.admission_records.append(row)
            del self.admission_active[sequence]

    async def handle(self, method, path, payload):
        target = urlsplit(path)
        if target.path == '/study-admission/state':
            return self.admission_state(method, path, payload)
        if path == '/study-admission/configure':
            return self.configure(method, payload)
        if method == 'POST' and path == '/api/generate':
            return await self._admit(method, path, payload)
        if target.path.startswith('/api/') and not (
                method == 'GET' and path in ('/api/version', '/api/tags', '/api/ps')
                or method == 'POST' and path == '/api/show'):
            return 404, {'reason': 'native_endpoint_outside_registered_study'}
        return await super().handle(method, path, payload)


async def main():
    from v2.admission_study import STUDY_FILES
    root = Path(__file__).resolve().parents[1]
    base, study = capture(root), capture(root, files=STUDY_FILES)
    verify_worktree(root, base.source_id)
    verify_worktree(root, study.source_id)
    proxy = AdmissionProxy(base.metadata(), study.metadata())
    host, port = os.environ.get('STUDY_PROXY_BIND', '127.0.0.1'), int(os.environ.get('STUDY_PROXY_PORT', '11437'))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid admission proxy port')
    server = await serve(proxy.handle, host, port)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
