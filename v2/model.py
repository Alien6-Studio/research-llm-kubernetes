"""Separate native pilot, frozen rate selection, and real-model Kubernetes trials."""
import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import threading
import time

from v2.plan import ROOT, canonical
from v2.run import CONTROL, Observer, command, json_http, scale, validate, kubectl
from v2.service import code_hash
from v2.transport import request
from v2.workload import DIGEST, MODEL, OPTIONS, correct, model_request

BASE = os.environ.get('STUDY_OLLAMA_URL', 'http://127.0.0.1:11435')
BRIDGE = os.environ.get('STUDY_OLLAMA_BRIDGE_URL', 'http://host.docker.internal:11435')
INFERENCE_MONITOR = os.environ.get('STUDY_INFERENCE_MONITOR')


def hardware():
    result = {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()}
    result['logical_cpus'] = os.cpu_count()
    if Path('/proc/meminfo').exists():
        result['memory'] = Path('/proc/meminfo').read_text()
        result['cpu_models'] = sorted(set(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')))
    if platform.system() == 'Darwin':
        for field in ['hw.model', 'hw.memsize', 'hw.logicalcpu', 'machdep.cpu.brand_string']:
            try: result[field] = command(['sysctl', '-n', field]).strip()
            except Exception: result[field] = None
        try:
            displays = json.loads(command(['system_profiler', 'SPDisplaysDataType', '-json']))['SPDisplaysDataType']
            result['gpu'] = [{k: d.get(k) for k in ['sppci_model', 'spdisplays_cores', 'spdisplays_metal']} for d in displays]
        except Exception: result['gpu'] = None
    return result


def verify_server():
    version = json_http(BASE + '/api/version')
    model = next(m for m in json_http(BASE + '/api/tags')['models'] if m['name'] == MODEL)
    if version['version'] != '0.40.16' or model['digest'] != DIGEST:
        raise RuntimeError('Model or native server version differs from registration')
    data = {'version': version, 'model': {k: model[k] for k in ['name', 'digest', 'size', 'details']},
            'options': OPTIONS, 'client_hardware': hardware()}
    if INFERENCE_MONITOR:
        data['inference_environment'] = json_http(INFERENCE_MONITOR + '/metadata')
    else:
        data['inference_environment'] = {'hardware': data['client_hardware'], 'placement': 'same physical host; Docker VM clients and native inference'}
    return data


def pilot_item(index):
    long = (index // 2) % 2
    note = 'This is a controlled benchmark note. Classify only the HTTP status in the final line. '
    return {'id': f'pilot-{index}', 'prompt_text': note * (60 if long else 30),
            'task_status': 200 if index % 2 else 500}


async def pilot(output=None):
    metadata = verify_server()
    warmup = []
    results = []
    state = {'utc': datetime.now(timezone.utc).isoformat(), 'source_commit': command(['git', 'rev-parse', 'HEAD']).strip(),
             'metadata': metadata, 'warmup': warmup, 'results': results, 'valid': False,
             'scope': 'Closed-loop native capacity characterization, not open-loop latency or a production task.'}
    def checkpoint():
        if output: output.write_text(json.dumps(state, indent=2) + '\n')
    checkpoint()
    for i in range(4):
        item = pilot_item(i)
        start = time.monotonic()
        try:
            status, raw = await request(BASE + '/api/generate', model_request(item), timeout=180)
        except Exception as error:
            status, raw = 0, {'transport_error': type(error).__name__}
        warmup.append({'item': item, 'status': status, 'raw': raw, 'elapsed': time.monotonic() - start,
                       'correct': status == 200 and correct(raw, item)})
        checkpoint()
        if status != 200 or not correct(raw, item):
            state['failure'] = 'pilot_warmup_failed'
            checkpoint()
            return state
    for concurrency in [1, 2, 4]:
        semaphore = asyncio.Semaphore(concurrency)
        async def one(i):
            async with semaphore:
                item = pilot_item(i)
                began = time.monotonic()
                try:
                    status, raw = await request(BASE + '/api/generate', model_request(item), timeout=180)
                except Exception as error:
                    status, raw = 0, {'transport_error': type(error).__name__}
                return {'item': item, 'status': status, 'raw': raw, 'began': began,
                        'finished': time.monotonic(), 'correct': status == 200 and correct(raw, item)}
        began = time.monotonic()
        observations = await asyncio.gather(*(one(i) for i in range(16)))
        elapsed = time.monotonic() - began
        results.append({'concurrency': concurrency, 'elapsed_seconds': elapsed,
                        'completion_rate': 16 / elapsed, 'observations': observations})
        checkpoint()
    state['loaded_model'] = json_http(BASE + '/api/ps')
    state['valid'] = all(r['correct'] for setting in results for r in setting['observations'])
    checkpoint()
    return state


def freeze(pilot_path, destination):
    destination = destination.resolve()
    data = json.loads(pilot_path.read_text())
    if not data['valid'] or len(data['results']) != 3 or any(not row['correct'] for setting in data['results'] for row in setting['observations']):
        raise RuntimeError('Pilot has a failed task; retain it before planning confirmation')
    capacity = max(row['completion_rate'] for row in data['results'])
    rates = {label: max(.01, round(capacity * factor, 2)) for label, factor in [('low', .25), ('near', .85), ('high', 1.5)]}
    destination.mkdir(parents=True, exist_ok=False)
    blocks = {'model-qualification': [], 'real-model': []}
    for profile, rate in [*rates.items(), ('headroom', rates['high'] * 2)]:
        for rep in range(3):
            rng = random.Random(20261112 + rep)
            offset, items = 0, []
            while True:
                offset += rng.expovariate(rate)
                if offset >= 25: break
                long = rng.random() < .5
                base = pilot_item(2 if long else 0)
                item = {**base, 'id': f'{profile}:{rep}:{len(items)}', 'offset': offset,
                        'tokens': 2200 if long else 1000, 'service_seconds': 0,
                        'task_status': 200 if len(items) % 2 else 500}
                items.append(item)
            encoded = gzip.compress(canonical(items), mtime=0)
            path = destination / f'{profile}-{rep}.json.gz'
            path.write_bytes(encoded)
            reference = {'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(encoded).hexdigest()}
            for replicas in [1, 8] if profile in ['high', 'headroom'] else []:
                blocks['model-qualification'].append(configuration('model-qualification', profile, replicas, rep, reference, emulator=True))
            if profile != 'headroom':
                for replicas in [0, 1, 2, 4, 8]:
                    blocks['real-model'].append(configuration('real-model', profile, replicas, rep, reference))
    rng = random.Random(20261113)
    for rows in blocks.values(): rng.shuffle(rows)
    plan = {'pilot_sha256': hashlib.sha256(pilot_path.read_bytes()).hexdigest(), 'native_pilot_capacity': capacity,
            'rates': rates, 'blocks': blocks,
            'routing_rule': 'Record exact serving UIDs. For sparse real load require a subset of the verified ready UID set, not that random low-volume traffic reaches every ready replica.'}
    (destination / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    return plan


def configuration(block, profile, replicas, rep, reference, emulator=False):
    return {'trial_id': f'{block}-{profile}-{replicas}-{rep}', 'block': block, 'profile': profile,
            'replicas': max(1, replicas), 'direct_ollama': replicas == 0,
            'policy': 'none', 'repetition': rep, 'node_cpus': 4, 'fastpath': emulator,
            'warmup_seconds': 5, 'measurement_seconds': 20, 'deadline_seconds': 10,
            'provider': {'mode': 'emulator' if emulator else 'ollama', 'tokens_per_second': 1000000000,
                         'requests_per_second': 1000, 'ollama_url': BRIDGE}, 'trace_reference': reference}


def native_snapshot():
    result = {'at': time.monotonic(), 'utc': datetime.now(timezone.utc).isoformat()}
    try:
        listeners = {int(value) for value in command(['lsof', '-nP', '-t', '-iTCP:11435', '-sTCP:LISTEN']).split()}
        processes = []
        for line in command(['ps', '-axo', 'pid=,ppid=,time=,rss=,comm=']).splitlines():
            parts = line.split(maxsplit=4)
            if len(parts) != 5: continue
            pid, parent, elapsed, rss, executable = parts
            factors = [float(value) for value in elapsed.split(':')]
            seconds = sum(value * 60 ** index for index, value in enumerate(reversed(factors)))
            processes.append({'pid': int(pid), 'ppid': int(parent), 'cpu_seconds': seconds,
                              'rss_bytes': int(rss) * 1024, 'executable': Path(executable).name})
        selected = set(listeners)
        while True:
            expanded = selected | {p['pid'] for p in processes if p['ppid'] in selected}
            if expanded == selected: break
            selected = expanded
        result['processes'] = [p for p in processes if p['pid'] in selected]
        result['model'] = json_http(BASE + '/api/ps')
        result['other_server_model_count'] = len(json_http('http://127.0.0.1:11434/api/ps').get('models', []))
        if platform.system() == 'Darwin':
            try: result['thermal'] = command(['pmset', '-g', 'therm'])
            except Exception as error: result['thermal'] = {'unavailable': type(error).__name__}
    except Exception as error:
        result['unavailable'] = type(error).__name__
    return result


class NativeObserver:
    def __init__(self):
        self.stopped = threading.Event()
        self.samples = []
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stopped.is_set():
            began = time.monotonic()
            row = native_snapshot()
            row['sampling_seconds'] = time.monotonic() - began
            self.samples.append(row)
            self.stopped.wait(max(.01, 1 - row['sampling_seconds']))

    def __enter__(self): self.thread.start(); return self

    def __exit__(self, *_): self.stopped.set(); self.thread.join(timeout=15)


class InferenceObserver:
    def __init__(self, trial):
        self.trial = trial
        self.samples = []
        self.metadata = {}
        self.local = None

    def __enter__(self):
        if INFERENCE_MONITOR:
            self.metadata = json_http(INFERENCE_MONITOR + '/start', {'trial_id': self.trial})
        else:
            self.local = NativeObserver()
            self.local.__enter__()
        return self

    def __exit__(self, *_):
        if INFERENCE_MONITOR:
            result = json_http(INFERENCE_MONITOR + '/stop', {'trial_id': self.trial})
            self.samples, self.metadata = result['samples'], result['metadata']
        else:
            self.local.__exit__()
            self.samples = self.local.samples


def run_block(plan_path, block, out, image):
    metadata = verify_server()
    command(['docker', 'update', '--cpus', '4', 'a6-capacity-v2-control-plane'])
    plan = json.loads(plan_path.read_text())
    out.mkdir(parents=True, exist_ok=False)
    (out / 'manifest.json').write_text(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
        'source_commit': command(['git', 'rev-parse', 'HEAD']).strip(), 'code_sha256': code_hash(),
        'model_controller_sha256': hashlib.sha256((ROOT / 'v2/model_service.py').read_bytes()).hexdigest(),
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'metadata': metadata,
        'kubernetes': json.loads(kubectl('version', '-o', 'json')),
        'kind_version': command(['kind', 'version']).strip(),
        'docker_version': command(['docker', 'version', '--format', '{{.Server.Version}}']).strip(),
        'node_limits': json.loads(command(['docker', 'inspect', 'a6-capacity-v2-control-plane', '--format',
               '{"nano_cpus":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}'])),
        'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'image_id': command(['docker', 'image', 'inspect', image, '--format', '{{.Id}}']).strip()}, indent=2) + '\n')
    recorded = []
    for original in plan['blocks'][block]:
        config = dict(original)
        routing = scale(config['replicas'])
        config['worker_addresses'] = ['http://' + p['ip'] + ':8080' for p in routing['pods']]
        encoded = (ROOT / config['trace_reference']['path']).read_bytes()
        assert hashlib.sha256(encoded).hexdigest() == config['trace_reference']['sha256']
        config['trace'] = json.loads(gzip.decompress(encoded))
        config['trace_decoded_sha256'] = hashlib.sha256(canonical(config['trace'])).hexdigest()
        json_http(CONTROL + '/prepare', config)
        with Observer() as observer, InferenceObserver(out.name + ':' + config['trial_id']) as native:
            result = json_http(CONTROL + '/run', config, timeout=360)
        issues = validate(config, result, routing, observer.samples)
        expected = {p['uid'] for p in routing['pods']}
        hit = {r['uid'] for key, archive in result['role_records'].items() if key != 'provider' for r in archive['records']}
        if block == 'real-model' and hit <= expected:
            issues = [x for x in issues if x != 'observed_worker_uid_set_differs']
        expected_hash = hashlib.sha256((ROOT / 'v2/model_service.py').read_bytes()).hexdigest()
        if any(s['controller'].get('model_controller_sha256') != expected_hash for s in result['resource_samples']):
            issues.append('model_controller_hash_mismatch')
        if not native.samples or any('unavailable' in row for row in native.samples):
            issues.append('inference_process_observations_missing')
        document = {'configuration': config, 'routing': routing, 'observed_serving_uids': sorted(hit),
                    'observations': observer.samples, 'result': result, 'issues': issues, 'valid': not issues,
                    'native_observations': native.samples,
                    'inference_observer_metadata': native.metadata,
                    'model_after_trial': json_http(BASE + '/api/ps'),
                    'other_server_loaded_model_count': native.samples[-1].get('other_server_model_count') if native.samples else None}
        (out / (config['trial_id'] + '.json')).write_text(json.dumps(document, separators=(',', ':')) + '\n')
        recorded.append(document)
        print(json.dumps({'trial': config['trial_id'], 'valid': not issues, 'issues': issues}), flush=True)
    complete = {'complete': True, 'qualified': all(r['valid'] for r in recorded), 'trials': len(recorded),
                'invalid': [r['configuration']['trial_id'] for r in recorded if not r['valid']]}
    (out / 'block.json').write_text(json.dumps(complete, indent=2) + '\n')
    print(json.dumps(complete), flush=True)
    if not complete['qualified']: raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['pilot', 'freeze', 'block'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--block')
    parser.add_argument('--image', default='alien6-capacity:model-v2')
    args = parser.parse_args()
    if args.stage == 'pilot':
        if args.out.exists(): raise RuntimeError('Refusing to replace pilot results')
        args.out.parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(pilot(args.out))
    elif args.stage == 'freeze':
        print(json.dumps(freeze(args.pilot, args.out)['rates']))
    else:
        run_block(args.plan, args.block, args.out, args.image)
