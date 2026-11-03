"""Asynchronous V2 roles with complete client outcomes and provider-owned leases."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
import uuid

from v2.quota import Ledger
from v2.transport import request, serve
from v2.workload import model_request, correct
from v2.network_policy import normalized_config, NetworkPolicyError


def clock_domain():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + str(os.stat('/proc/self/ns/time').st_ino)
    except OSError:
        return socket.gethostname() + ':local-monotonic'


def code_hash():
    digest = hashlib.sha256()
    for name in ['service.py', 'transport.py', 'quota.py', 'workload.py','network_policy.py']:
        digest.update(name.encode() + b'\0' + Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


class Application:
    def __init__(self, role=None, provider=None):
        self.role = role or os.environ.get('ROLE', 'provider')
        self.provider = provider or os.environ.get('PROVIDER_URL', 'http://provider:8080')
        self.pod = os.environ.get('HOSTNAME', socket.gethostname())
        self.uid = os.environ.get('POD_UID', self.pod)
        self.domain = clock_domain()
        self.config = {'mode': 'emulator', 'tokens_per_second': 1000000000,
                       'requests_per_second': 1000, 'ollama_url': 'http://host.docker.internal:11435'}
        self.ledger = Ledger()
        self.slots = asyncio.Semaphore(4)
        self.active = self.queued = 0
        self.records = []
        self.heartbeat_lags = []
        self.running = False

    async def heartbeat(self):
        previous = time.monotonic()
        while True:
            await asyncio.sleep(.02)
            now = time.monotonic()
            self.heartbeat_lags.append({'at': now, 'lag': max(0, now - previous - .02)})
            self.heartbeat_lags = self.heartbeat_lags[-3000:]
            previous = now

    def stats(self):
        usage = resource.getrusage(resource.RUSAGE_SELF)
        data = {'pod': self.pod, 'uid': self.uid, 'role': self.role, 'clock_domain': self.domain,
                'trial_id': self.config.get('trial_id'), 'trial_binding': True,
                'at': time.monotonic(), 'wall_utc_seconds': time.time(),
                'active': self.active, 'queued': self.queued, 'records': len(self.records),
                'cpu_seconds': usage.ru_utime + usage.ru_stime,
                'peak_rss_bytes': usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                'code_sha256': code_hash(), 'source_commit': os.environ.get('SOURCE_COMMIT'),
                'python': sys.version, 'heartbeat_recent': self.heartbeat_lags[-50:]}
        for name in ['cpu.stat', 'cpu.max', 'cpu.pressure', 'memory.current', 'memory.max', 'memory.events', 'memory.pressure']:
            try:
                data[name] = Path('/sys/fs/cgroup', name).read_text().strip()
            except OSError:
                data[name] = None
        return data

    async def infer(self, item):
        received = time.monotonic()
        identifier = item.get('reservation') or str(item.get('trial_id')) + ':' + item['id'] + ':direct'
        stale = self.config.get('trial_id') is not None and item.get('trial_id') != self.config['trial_id']
        if stale:
            row = {'id': item['id'], 'trial_id': item.get('trial_id'), 'clock_domain': self.domain,
                   'provider_received': received, 'provider_finished': time.monotonic(), 'provider_consumed': None,
                   'reservation': identifier, 'units': item['tokens'], 'allowed': False,
                   'status': 409, 'reason': 'stale_trial', 'correct': False}
            self.records.append(row)
            return 409, {'id': item['id'], 'reason': 'stale_trial', 'correct': False, 'provider_events': row}
        if item.get('reservation'):
            allowed = self.ledger.consume(identifier, item['tokens'])
            reason = 'reservation_unavailable'
        else:
            allowed, permission = self.ledger.direct(identifier, item['tokens'])
            reason = 'provider_quota'
        row = {'id': item['id'], 'clock_domain': self.domain, 'provider_received': received,
               'trial_id': item.get('trial_id'),
               'provider_consumed': time.monotonic() if allowed else None, 'reservation': identifier,
               'units': item['tokens'], 'allowed': allowed}
        status, result = 429, {'reason': reason, 'correct': False}
        if not allowed and item.get('reservation'):
            status = 409
        if allowed:
            self.active += 1
            try:
                if self.config['mode'] == 'ollama':
                    # Started inference is allowed to drain; client expiry is measured separately.
                    status, raw = await request(self.config['ollama_url'] + '/api/generate', model_request(item), timeout=120)
                    result = {'correct': status == 200 and correct(raw, item), 'inference': raw}
                else:
                    await asyncio.sleep(item['service_seconds'])
                    status, result = 200, {'correct': True}
            except Exception as error:
                status, result = 502, {'reason': 'inference_' + type(error).__name__, 'correct': False}
            finally:
                self.active -= 1
        row.update({'provider_finished': time.monotonic(), 'status': status, **result})
        self.records.append(row)
        return status, {'id': item['id'], **result, 'provider_events': row}

    async def work(self, item):
        received = time.monotonic()
        deadline = received + max(0, item['remaining_budget_seconds'])
        row = {'id': item['id'], 'pod': self.pod, 'uid': self.uid, 'clock_domain': self.domain,
               'trial_id': item.get('trial_id'),
               'worker_received': received, 'queue_entered': received, 'queue_left': None,
               'admission_started': None, 'admission_finished': None,
               'provider_call_started': None, 'provider_call_finished': None,
               'admission_attempts': [], 'inference_attempts': 0}
        if self.config.get('trial_id') is not None and item.get('trial_id') != self.config['trial_id']:
            row.update({'status': 409, 'reason': 'stale_trial', 'correct': False, 'worker_finished': time.monotonic()})
            self.records.append(row)
            return 409, row
        if self.active + self.queued >= 36:
            row.update({'status': 429, 'reason': 'worker_queue_full', 'correct': False, 'worker_finished': time.monotonic()})
            self.records.append(row)
            return 429, row
        acquired = False
        reservation = None
        self.queued += 1
        status, result = 504, {'reason': 'worker_queue_deadline', 'correct': False}
        try:
            try:
                async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                    await self.slots.acquire()
                acquired = True
            except TimeoutError:
                pass
            self.queued -= 1
            row['queue_left'] = time.monotonic()
            if acquired:
                self.active += 1
                row['admission_started'] = time.monotonic()
                while time.monotonic() < deadline:
                    reservation = None
                    if item['policy'] == 'global':
                        key = str(uuid.uuid5(uuid.NAMESPACE_URL, item['trial_id'] + item['id'] + str(len(row['admission_attempts']))))
                        code, permission = await request(self.provider + '/reserve', {
                            'reservation': key, 'tokens': item['tokens'], 'trial_id': item['trial_id'],
                            'ttl': min(.25, max(0, deadline - time.monotonic()))}, timeout=5)
                        row['admission_attempts'].append({'at': time.monotonic(), 'status': code, 'permission': permission})
                        if code == 409:
                            status, result = 409, {'reason': 'stale_trial', 'correct': False}
                            break
                        if not permission.get('allowed'):
                            await asyncio.sleep(min(permission.get('retry_after', .01) + .001, max(0, deadline - time.monotonic())))
                            continue
                        reservation = permission['reservation']
                    row['admission_finished'] = time.monotonic()
                    if time.monotonic() >= deadline:
                        break
                    row['provider_call_started'] = time.monotonic()
                    row['inference_attempts'] += 1
                    status, result = await request(self.provider + '/infer', {**item, 'reservation': reservation}, timeout=125)
                    row['provider_call_finished'] = time.monotonic()
                    # Expired unconsumed leases may be reacquired; no inference was started.
                    if status == 409 and item['policy'] == 'global':
                        continue
                    break
                else:
                    status, result = 504, {'reason': 'admission_deadline', 'correct': False}
                if row['provider_call_started'] is None and status != 409:
                    status, result = 504, {'reason': 'admission_deadline', 'correct': False}
                elif time.monotonic() > deadline:
                    result = {**result, 'reason': 'worker_response_deadline'}
                    status = 504
        except Exception as error:
            status, result = 502, {'reason': 'worker_' + type(error).__name__, 'correct': False}
        finally:
            if reservation:
                try:
                    await request(self.provider + '/release', {'reservation': reservation}, timeout=5)
                except Exception:
                    row['release_transport_failed'] = True
            if acquired:
                self.active -= 1
                self.slots.release()
            row.update({**result, 'status': status, 'worker_finished': time.monotonic()})
            self.records.append(row)
        return status, row

    async def invoke(self, item, config, start):
        scheduled = start + item['offset']
        deadline = scheduled + config['deadline_seconds']
        launched = time.monotonic()
        row = {'id': item['id'], 'clock_domain': self.domain, 'scheduled': scheduled,
               'trial_id': config['trial_id'],
               'launched': launched, 'emitted': None, 'status': 0, 'correct': False,
               'scheduling_lag_seconds': max(0, launched - scheduled)}
        if launched >= deadline:
            row['reason'] = 'generator_not_emitted_deadline'
        else:
            def emitted():
                row['emitted'] = time.monotonic()
            try:
                if config.get('direct_provider'):
                    status, result = await request(self.provider + '/infer', {**item, 'trial_id': config['trial_id']},
                                                   timeout=max(.001, deadline - time.monotonic()), sent=emitted)
                elif config.get('direct_ollama'):
                    status, raw = await request(config['provider']['ollama_url'] + '/api/generate', model_request(item),
                                                timeout=max(.001, deadline - time.monotonic()), sent=emitted)
                    result = {'inference': raw, 'correct': status == 200 and correct(raw, item)}
                else:
                    status, result = await request(config.get('worker_url', 'http://workers:8080') + '/work', {
                        **item, 'trial_id': config['trial_id'], 'policy': config['policy'],
                        'remaining_budget_seconds': max(0, deadline - time.monotonic())},
                        timeout=max(.001, deadline - time.monotonic()), sent=emitted)
                row.update({'status': status, 'response': result, 'correct': result.get('correct', False)})
            except TimeoutError:
                row['reason'] = 'client_deadline'
            except Exception as error:
                row['reason'] = 'client_' + type(error).__name__
        row['finished'] = time.monotonic()
        row['latency_seconds'] = row['finished'] - scheduled
        row['emitted_latency_seconds'] = None if row['emitted'] is None else row['finished'] - row['emitted']
        return row

    async def run_load(self, config):
        samples = []
        stopped = asyncio.Event()
        async def snapshot():
            async def role(url):
                try:
                    return (await request(url + '/stats', timeout=2))[1]
                except Exception as error:
                    return {'unavailable': type(error).__name__}
            values = await asyncio.gather(*(role(url) for url in config['worker_addresses']), role(self.provider))
            return {'at': time.monotonic(), 'controller': self.stats(), 'workers': values[:-1], 'provider': values[-1]}
        async def sample():
            while not stopped.is_set():
                began = time.monotonic()
                data = await snapshot()
                data['sampling_seconds'] = time.monotonic() - began
                samples.append(data)
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=max(.01, .5 - data['sampling_seconds']))
                except TimeoutError:
                    pass
        sampler = asyncio.create_task(sample())
        start = time.monotonic() + .25
        wall = time.time() + .25
        tasks = []
        for item in config['trace']:
            await asyncio.sleep(max(0, start + item['offset'] - time.monotonic()))
            tasks.append(asyncio.create_task(self.invoke(item, config, start)))
        outcomes = await asyncio.gather(*tasks)
        drain_started = time.monotonic()
        drain_limit = 180 if config['provider']['mode'] == 'ollama' else 15
        drained = False
        while time.monotonic() - drain_started < drain_limit:
            state = await snapshot()
            roles = state['workers'] + [state['provider']]
            if all('active' in role and role['active'] == 0 and role['queued'] == 0 for role in roles):
                drained = True
                break
            await asyncio.sleep(.2)
        stopped.set()
        await sampler
        samples.append(await snapshot())
        archives = {}
        for name, url in [('provider', self.provider)] + [(url, url) for url in config['worker_addresses']]:
            archives[name] = (await request(url + '/records', timeout=15))[1]
        return {'start': start, 'wall_start': wall, 'clock_domain': self.domain,
                'finished': time.monotonic(), 'drained': drained, 'drain_seconds': time.monotonic() - drain_started,
                'requests': outcomes, 'resource_samples': samples, 'role_records': archives}

    async def handle(self, method, path, payload):
        if method=='POST' and path in ['/configure','/prepare','/run']:
            try:payload=normalized_config(payload)
            except NetworkPolicyError:return 400,{'reason':'unapproved_network_target'}
        if method == 'GET':
            if path == '/records':
                return 200, {'records': self.records, 'ledger': self.ledger.snapshot() if self.role == 'provider' else None}
            return 200, self.stats()
        if path == '/configure':
            if self.active or self.queued or self.running:
                return 409, {'reason': 'not_drained'}
            self.config.update(payload)
            self.ledger = Ledger(self.config['tokens_per_second'], self.config['requests_per_second'])
            self.records = []
            self.heartbeat_lags = []
            return 200, {'configured': True}
        if self.role == 'provider':
            if path == '/reserve':
                if self.config.get('trial_id') is not None and payload.get('trial_id') != self.config['trial_id']:
                    return 409, {'allowed': False, 'reason': 'stale_trial'}
                permission = self.ledger.reserve(payload['reservation'], payload['tokens'], payload['ttl'])
                return (200 if permission['allowed'] else 429), permission
            if path == '/release':
                return 200, {'released': self.ledger.release(payload['reservation'])}
            if path == '/infer':
                return await self.infer(payload)
        if self.role == 'worker' and path == '/work':
            return await self.work(payload)
        if self.role == 'controller' and path == '/run':
            if self.running:
                return 409, {'reason': 'already_running'}
            self.running = True
            try:
                return 200, await self.run_load(payload)
            finally:
                self.running = False
        if self.role == 'controller' and path == '/prepare':
            if self.running:
                return 409, {'reason': 'already_running'}
            states = await asyncio.gather(*(request(url + '/stats') for url in [self.provider, *payload['worker_addresses']]))
            if any(code != 200 or state.get('active') or state.get('queued') for code, state in states):
                return 409, {'reason': 'previous_work_not_idle'}
            role_config = {**payload['provider'], 'trial_id': payload['trial_id']}
            results = await asyncio.gather(*(request(url + '/configure', role_config)
                         for url in [self.provider, *payload['worker_addresses']]))
            if any(code != 200 for code, _ in results):
                return 409, {'reason': 'previous_work_not_drained', 'responses': results}
            self.config['trial_id'] = payload['trial_id']
            self.heartbeat_lags = []
            return 200, {'prepared': True}
        return 404, {'reason': 'unknown_endpoint'}


async def main():
    app = Application()
    heartbeat = asyncio.create_task(app.heartbeat())
    server = await serve(app.handle, port=int(os.environ.get('PORT', '8080')))
    try:
        async with server:
            await server.serve_forever()
    finally:
        heartbeat.cancel()


if __name__ == '__main__':
    asyncio.run(main())
