"""Separate native pilot, frozen rate selection, and real-model Kubernetes trials."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import errno
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import subprocess
import threading
import time
from urllib.error import URLError

from v2.identity import capture, verify_worktree
from v2.plan import ROOT, canonical
from v2.run import CONTROL, Observer, command, json_http, scale, validate, kubectl
from v2.service import code_hash
from v2.transport import request
from v2.workload import DIGEST, MODEL, OPTIONS, correct, model_request

BASE = os.environ.get('STUDY_OLLAMA_URL', 'http://127.0.0.1:11435')
BRIDGE = os.environ.get('STUDY_OLLAMA_BRIDGE_URL', 'http://host.docker.internal:11435')
INFERENCE_MONITOR = os.environ.get('STUDY_INFERENCE_MONITOR')
PROC_ROOT = Path('/proc')
SYS_ROOT = Path('/sys')
ETC_RESEARCH_ROOT = Path('/etc/alien6-research')


def read_observation(path):
    try:
        return path.read_text()
    except OSError as error:
        return {'unavailable': type(error).__name__}


def read_deployment_artifact(name):
    """Read an explicitly named public deployment record, never environment data."""
    if name not in ('ollama-artifact.json', 'model-staging.json'):
        raise ValueError('Unknown public deployment artifact')
    raw = read_observation(ETC_RESEARCH_ROOT / name)
    if not isinstance(raw, str):
        return raw
    try:
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError('Artifact must be a JSON object')
        return document
    except ValueError as error:
        return {'unavailable': type(error).__name__}


def ps_cpu_seconds(value):
    """Parse the POSIX/GNU CPU-time field, including its optional day prefix."""
    days, separator, rest = value.partition('-')
    if not separator:
        rest, days = days, '0'
    fields = rest.split(':')
    if not days.isdigit() or len(fields) not in (2, 3):
        raise ValueError('Invalid process CPU-time field')
    numbers = [float(field) for field in fields]
    if any(not math.isfinite(number) or number < 0 for number in numbers):
        raise ValueError('Invalid process CPU-time field')
    return int(days) * 86400 + sum(number * 60 ** index for index, number in enumerate(reversed(numbers)))


def connection_refused(error):
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(reason, ConnectionRefusedError) or isinstance(reason, OSError) and reason.errno == errno.ECONNREFUSED


def linux_host_observations():
    return {'scope': 'Client and model share this host; CPU affinity does not isolate memory bandwidth, caches or other host services.',
            'proc_stat': read_observation(PROC_ROOT / 'stat'),
            'memory': read_observation(PROC_ROOT / 'meminfo'),
            'loadavg': read_observation(PROC_ROOT / 'loadavg'),
            'pressure': {name: read_observation(PROC_ROOT / 'pressure' / name) for name in ('cpu', 'memory', 'io')}}


def linux_process_observations(pid):
    result = {'status': read_observation(PROC_ROOT / str(pid) / 'status'),
              'cgroup_membership': read_observation(PROC_ROOT / str(pid) / 'cgroup')}
    try:
        result['cpu_affinity'] = sorted(os.sched_getaffinity(pid))
    except (AttributeError, OSError) as error:
        result['cpu_affinity'] = {'unavailable': type(error).__name__}
    return result


def visible_cgroup_limits(hierarchy):
    """Known visible upper bounds are not available or physically isolated capacity."""
    values = {'cpu': [], 'memory': [], 'swap': []}
    unavailable = []
    for group in hierarchy:
        for resource, name in [('cpu', 'cpu.max'), ('memory', 'memory.max'), ('swap', 'memory.swap.max')]:
            raw = group['files'].get(name)
            try:
                if not isinstance(raw, str):
                    raise ValueError('Constraint unreadable')
                if resource == 'cpu':
                    quota, period = raw.split()
                    if not period.isdigit() or int(period) <= 0:
                        raise ValueError('Invalid quota period')
                    value = None if quota == 'max' else int(quota) / int(period)
                    if value is not None and (not math.isfinite(value) or value <= 0):
                        raise ValueError('Invalid CPU quota')
                else:
                    limit = raw.strip()
                    if limit != 'max' and not limit.isdigit():
                        raise ValueError('Invalid memory/swap cap')
                    value = None if limit == 'max' else int(limit)
                if value is not None:
                    values[resource].append(value)
            except (TypeError, ValueError, OverflowError):
                unavailable.append({'path': group['path'], 'file': name, 'observed': raw})
    return {'scope': 'Upper bounds imposed by the visible cgroup hierarchy; available capacity and ancestors outside this namespace are not measured.',
            'cpu_quota_cores_upper_bound': min(values['cpu']) if values['cpu'] else None,
            'memory_max_bytes_upper_bound': min(values['memory']) if values['memory'] else None,
            'memory_swap_max_bytes_upper_bound': min(values['swap']) if values['swap'] else None,
            'finite_caps_observed': {resource: bool(limits) for resource, limits in values.items()},
            'complete': not unavailable, 'unavailable_constraints': unavailable}


def linux_cgroup_observations(membership):
    """Resolve a unified process cgroup through the observer's recorded mount."""
    result = {'membership': membership}
    if not isinstance(membership, str):
        return {**result, 'unavailable': 'cgroup_membership_unreadable'}
    unified = [line.split(':', 2)[2] for line in membership.splitlines() if line.startswith('0::')]
    if len(unified) != 1:
        return {**result, 'unavailable': 'unified_cgroup_v2_not_identified'}
    mountinfo = read_observation(PROC_ROOT / 'self/mountinfo')
    if not isinstance(mountinfo, str):
        return {**result, 'unavailable': 'cgroup_mountinfo_unreadable'}
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(' - ')
        if not separator or not after.split() or after.split()[0] != 'cgroup2':
            continue
        fields = before.split()
        if len(fields) < 5:
            continue
        decode = lambda text: re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), text)
        mount_root, mount_path = Path(decode(fields[3])), Path(decode(fields[4]))
        group = Path(unified[0])
        if '..' in group.parts or not group.is_absolute():
            continue
        if group.is_relative_to(mount_root):
            path = mount_path / group.relative_to(mount_root)
            resolution = 'mount_root_relative'
        else:
            # In a cgroup namespace, /proc reports paths relative to its root,
            # while mountinfo can retain the corresponding host cgroup root.
            path = mount_path / group.relative_to('/')
            resolution = 'cgroup_namespace_relative'
            if not path.is_dir():
                continue
        names = ('cpu.stat', 'cpu.max', 'cpu.pressure', 'cpuset.cpus.effective', 'cpuset.mems.effective',
                 'memory.current', 'memory.max', 'memory.swap.current', 'memory.swap.max',
                 'memory.events', 'memory.pressure', 'io.pressure',
                 'pids.current', 'pids.max')
        hierarchy, current = [], path
        while current.is_relative_to(mount_path):
            hierarchy.append({'path': str(current),
                              'scope': ('visible_mount_root_shared_aggregate' if current == mount_path else 'selected_process_cgroup' if current == path else 'visible_ancestor'),
                              'files': {name: read_observation(current / name) for name in names}})
            if current == mount_path:
                break
            current = current.parent
        return {**result, 'version': 2, 'mount_root': str(mount_root), 'mount_path': str(mount_path),
                'path': str(path), 'path_resolution': resolution, 'files': hierarchy[0]['files'],
                'visible_hierarchy': hierarchy, 'visible_hierarchy_limits': visible_cgroup_limits(hierarchy),
                'container_root_aggregate': {'path': str(mount_path),
                    'scope': 'All services in the visible container root, including model, proxy and monitor; not model-only process RSS.',
                    'files': hierarchy[-1]['files']}}
    return {**result, 'unavailable': 'process_cgroup_mount_not_resolved'}


def collection_identity():
    snapshot = capture(ROOT)
    verify_worktree(ROOT, snapshot.source_id)
    return {**snapshot.metadata(), 'source_commit': snapshot.git_base}


def hardware():
    result = {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()}
    result['logical_cpus'] = os.cpu_count()
    if (PROC_ROOT / 'meminfo').exists():
        result['memory'] = read_observation(PROC_ROOT / 'meminfo')
        cpuinfo = read_observation(PROC_ROOT / 'cpuinfo')
        result['cpu_models'] = (sorted(set(line.split(':', 1)[1].strip() for line in cpuinfo.splitlines() if line.startswith('model name')))
                                if isinstance(cpuinfo, str) else cpuinfo)
    if platform.system() == 'Linux':
        result['observer_process'] = linux_process_observations(os.getpid())
        result['observer_cgroup'] = linux_cgroup_observations(result['observer_process']['cgroup_membership'])
        result['cpu_online'] = read_observation(SYS_ROOT / 'devices/system/cpu/online')
        result['placement_scope'] = 'Shared Linux host; CPU affinity does not provide physical isolation or separate memory bandwidth.'
        result['deployment_artifacts'] = {'ollama_binary': read_deployment_artifact('ollama-artifact.json'),
                                          'model_staging': read_deployment_artifact('model-staging.json')}
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
        placement = ('Shared Linux host; selected CPU affinities do not isolate memory bandwidth, caches or host services.'
                     if platform.system() == 'Linux' else 'same physical host; Docker VM clients and native inference')
        data['inference_environment'] = {'hardware': data['client_hardware'], 'placement': placement}
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
    state = {'utc': datetime.now(timezone.utc).isoformat(), **collection_identity(),
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


async def warmup(output):
    state = {'utc': datetime.now(timezone.utc).isoformat(), 'scope': 'Technical model loading/warmup, outside the comparison cohort',
             **collection_identity(), 'metadata': verify_server(), 'observations': [], 'valid': False}
    for i in range(4):
        item = pilot_item(i)
        began = time.monotonic()
        try:
            status, raw = await request(BASE + '/api/generate', model_request(item), timeout=180)
        except Exception as error:
            status, raw = 0, {'transport_error': type(error).__name__}
        state['observations'].append({'item': item, 'status': status, 'raw': raw,
                                     'elapsed': time.monotonic() - began, 'correct': status == 200 and correct(raw, item)})
        output.write_text(json.dumps(state, indent=2) + '\n')
        if not state['observations'][-1]['correct']: return state
    state['valid'] = True
    state['loaded_model'] = json_http(BASE + '/api/ps')
    output.write_text(json.dumps(state, indent=2) + '\n')
    return state


def validate_pilot(data):
    """A rate-setting pilot must contain the complete pinned native experiment."""
    if data.get('valid') is not True or len(data.get('warmup', [])) != 4:
        raise RuntimeError('Pilot has incomplete or failed warmup; retain it without freezing rates')
    metadata = data['metadata']
    if metadata['version']['version'] != '0.40.16' or metadata['model']['name'] != MODEL or metadata['model']['digest'] != DIGEST or metadata['options'] != OPTIONS:
        raise RuntimeError('Pilot server, model or options differ from the pinned experiment')
    settings = data.get('results', [])
    if [setting['concurrency'] for setting in settings] != [1, 2, 4]:
        raise RuntimeError('Pilot must retain the complete ordered 1/2/4 concurrency sweep')
    groups = [data['warmup'], *[setting['observations'] for setting in settings]]
    for group, size in zip(groups, (4, 16, 16, 16), strict=True):
        if len(group) != size:
            raise RuntimeError('Pilot generation cohort is incomplete')
        for index, row in enumerate(group):
            if row['item'] != pilot_item(index) or row['status'] != 200 or row['correct'] is not True or not correct(row['raw'], row['item']):
                raise RuntimeError('Pilot task or archived answer differs from the complete successful experiment')
    for row in data['warmup']:
        if isinstance(row['elapsed'], bool) or not isinstance(row['elapsed'], (int, float)) or not math.isfinite(row['elapsed']) or row['elapsed'] < 0:
            raise RuntimeError('Pilot warmup duration is invalid')
    for setting in settings:
        elapsed = setting['elapsed_seconds']
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed <= 0:
            raise RuntimeError('Pilot elapsed duration is invalid')
        expected = 16 / elapsed
        rate = setting['completion_rate']
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or abs(rate - expected) > 1e-12 * max(1, abs(expected)):
            raise RuntimeError('Pilot completion rate cannot be reproduced')
        for row in setting['observations']:
            began, finished = row['began'], row['finished']
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (began, finished)) or finished < began:
                raise RuntimeError('Pilot request interval is invalid')
    loaded = data.get('loaded_model', {}).get('models', [])
    if len(loaded) != 1 or loaded[0].get('name') != MODEL or loaded[0].get('digest') != DIGEST or loaded[0].get('context_length') != OPTIONS['num_ctx']:
        raise RuntimeError('Pilot loaded model identity or context differs from the pinned experiment')
    return max(setting['completion_rate'] for setting in settings)


def cpu_measurement_seconds(data):
    """Prospective CPU window: at least ten expected low-load arrivals."""
    capacity = validate_pilot(data)
    low_rate = max(.01, round(capacity * .25, 2))
    return max(20, math.ceil(math.ceil(10 / low_rate) / 5) * 5)


def derive_model_plan(data, pilot_sha256, trace_directory, *, measurement_seconds=20, provider_url=None, encode_trace=None):
    """Pure planning shared by collection and deterministic offline verification.

    An offline encoder may return the retained gzip bytes after verifying the
    decoded items; gzip platform/header differences are not workload differences.
    """
    capacity = validate_pilot(data)
    if isinstance(measurement_seconds, bool) or not isinstance(measurement_seconds, (int, float)) or not math.isfinite(measurement_seconds) or measurement_seconds <= 0:
        raise ValueError('Measurement window must be finite and positive')
    trace_directory = Path(trace_directory)
    if trace_directory.is_absolute() or '..' in trace_directory.parts:
        raise ValueError('Frozen trace directory must stay relative to the repository')
    rates = {label: max(.01, round(capacity * factor, 2)) for label, factor in [('low', .25), ('near', .85), ('high', 1.5)]}
    encoded_traces = {}
    blocks = {'model-qualification': [], 'real-model': []}
    for profile, rate in [*rates.items(), ('headroom', rates['high'] * 2)]:
        for rep in range(3):
            rng = random.Random(20261112 + rep)
            offset, items = 0, []
            while True:
                offset += rng.expovariate(rate)
                if offset >= 5 + measurement_seconds: break
                long = rng.random() < .5
                base = pilot_item(2 if long else 0)
                item = {**base, 'id': f'{profile}:{rep}:{len(items)}', 'offset': offset,
                        'tokens': 2200 if long else 1000, 'service_seconds': 0,
                        'task_status': 200 if len(items) % 2 else 500}
                items.append(item)
            if not any(5 <= item['offset'] < 5 + measurement_seconds for item in items):
                raise RuntimeError('Frozen trace has no measurement arrivals; register a longer window without selecting another seed')
            path = str(trace_directory / f'{profile}-{rep}.json.gz')
            encoded = gzip.compress(canonical(items), mtime=0) if encode_trace is None else encode_trace(path, items)
            if not isinstance(encoded, bytes):
                raise TypeError('Frozen trace encoder must return exact bytes')
            encoded_traces[path] = encoded
            reference = {'path': path, 'sha256': hashlib.sha256(encoded).hexdigest()}
            for replicas in [1, 8] if profile in ['high', 'headroom'] else []:
                blocks['model-qualification'].append(configuration('model-qualification', profile, replicas, rep, reference, emulator=True))
            if profile != 'headroom':
                for replicas in [0, 1, 2, 4, 8]:
                    blocks['real-model'].append(configuration('real-model', profile, replicas, rep, reference))
    rng = random.Random(20261113)
    for rows in blocks.values():
        rng.shuffle(rows)
        for config in rows:
            config['measurement_seconds'] = measurement_seconds
            if provider_url is not None:
                config['provider']['ollama_url'] = provider_url
    plan = {'pilot_sha256': pilot_sha256, 'native_pilot_capacity': capacity,
            'rates': rates, 'blocks': blocks,
            'routing_rule': 'Record exact serving UIDs. For sparse real load require a subset of the verified ready UID set, not that random low-volume traffic reaches every ready replica.'}
    return plan, encoded_traces


def freeze(pilot_path, destination, measurement_seconds=20):
    destination = destination.resolve()
    encoded_pilot = pilot_path.read_bytes()
    data = json.loads(encoded_pilot)
    plan, traces = derive_model_plan(data, hashlib.sha256(encoded_pilot).hexdigest(), destination.relative_to(ROOT),
                                    measurement_seconds=measurement_seconds)
    destination.mkdir(parents=True, exist_ok=False)
    for relative, encoded in traces.items():
        (ROOT / relative).write_bytes(encoded)
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
    linux = platform.system() == 'Linux'
    if linux:
        result['linux_host'] = linux_host_observations()
    try:
        listeners = {int(value) for value in command(['lsof', '-nP', '-t', '-iTCP:11435', '-sTCP:LISTEN']).split()}
        processes = []
        for line in command(['ps', '-axo', 'pid=,ppid=,time=,rss=,comm=']).splitlines():
            parts = line.split(maxsplit=4)
            if len(parts) != 5: continue
            pid, parent, elapsed, rss, executable = parts
            seconds = ps_cpu_seconds(elapsed)
            processes.append({'pid': int(pid), 'ppid': int(parent), 'cpu_seconds': seconds,
                              'rss_bytes': int(rss) * 1024, 'executable': Path(executable).name})
        selected = set(listeners)
        while True:
            expanded = selected | {p['pid'] for p in processes if p['ppid'] in selected}
            if expanded == selected: break
            selected = expanded
        result['processes'] = [p for p in processes if p['pid'] in selected]
        if linux:
            groups = {}
            for process in result['processes']:
                process['linux'] = linux_process_observations(process['pid'])
                membership = process['linux']['cgroup_membership']
                key = membership if isinstance(membership, str) else str(process['pid'])
                if key not in groups:
                    groups[key] = linux_cgroup_observations(membership)
                process['linux']['cgroup_observation_key'] = key
            result['linux_cgroups'] = groups
        result['model'] = json_http(BASE + '/api/ps')
        try:
            other = json_http('http://127.0.0.1:11434/api/ps')
            if linux and (not isinstance(other, dict) or not isinstance(other.get('models'), list)):
                raise ValueError('Normal server model list missing or invalid')
            result['other_server_model_count'] = len(other.get('models', []))
            if linux:
                result['other_server_observation'] = 'present'
        except Exception as error:
            absent = linux and connection_refused(error)
            result['other_server_model_count'] = 0 if absent else None
            result['other_server_observation_error'] = type(error).__name__
            if linux:
                result['other_server_observation'] = 'not_present' if absent else 'unavailable'
        if platform.system() == 'Darwin':
            try: result['thermal'] = command(['pmset', '-g', 'therm'])
            except Exception as error: result['thermal'] = {'unavailable': type(error).__name__}
            try: result['host_memory'] = command(['vm_stat'])
            except Exception as error: result['host_memory'] = {'unavailable': type(error).__name__}
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
            try:
                result = json_http(INFERENCE_MONITOR + '/stop', {'trial_id': self.trial})
                self.samples, self.metadata = result['samples'], result['metadata']
            except Exception as error:
                self.metadata['stop_error'] = type(error).__name__
                self.samples = []
        else:
            self.local.__exit__()
            self.samples = self.local.samples


def model_request_sha256(item):
    """Match the proxy's semantic JSON hash, independently of HTTP formatting."""
    encoded = json.dumps(model_request(item), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def expected_native_calls(config, result):
    """Reconcile attempts before accepting a native completion certificate."""
    if not isinstance(result, dict):
        raise ValueError('Controller result is unavailable; native call coverage is unknown')
    trace = {item['id']: item for item in config['trace']}
    if len(trace) != len(config['trace']):
        raise ValueError('Duplicate trace IDs make native call coverage ambiguous')
    requests = result.get('requests')
    if (not isinstance(requests, list) or Counter(row.get('id') for row in requests) != Counter(trace.keys())):
        raise ValueError('Missing or duplicate controller request records')
    attempts = []
    written = []
    for row in requests:
        phases = row.get('transport_phases')
        if not isinstance(phases, dict) or not phases.get('clock_domain') or phases.get('clock') != 'time.monotonic':
            raise ValueError('Missing controller transport phases; writes are unknown')
        timed_phases = ('request_task_started', 'write_started', 'write_queued', 'emitted', 'status_received', 'headers_received', 'body_received', 'body_decoded')
        if any(phases.get(name) is not None and (type(phases[name]) not in (int, float) or not math.isfinite(phases[name])) for name in timed_phases):
            raise ValueError('Controller write-phase timestamps are invalid')
        attempted = phases.get('write_started') is not None
        later = ('write_queued', 'emitted', 'status_received', 'headers_received', 'body_received', 'body_decoded')
        if not attempted and (row.get('emitted') is not None or any(phases.get(name) is not None for name in later)):
            raise ValueError('Controller transport has an ambiguous write')
        if attempted:
            written.append(row['id'])
        elif row.get('reason') != 'generator_not_emitted_deadline' and phases.get('request_task_started') is None:
            raise ValueError('Controller request has no proof that its write was not attempted')
    if config.get('direct_ollama'):
        attempts = [{'id': identifier, 'origin': 'direct_write_started',
                     'request_sha256': model_request_sha256(trace[identifier])} for identifier in written]
    else:
        archives = result.get('role_records')
        if not isinstance(archives, dict) or 'provider' not in archives:
            raise ValueError('Provider archive is unavailable')
        providers = archives['provider'].get('records')
        workers = []
        for key, archive in archives.items():
            if not isinstance(archive, dict) or not isinstance(archive.get('records'), list):
                raise ValueError('Role archive is unavailable')
            if key != 'provider':
                workers.extend(archive['records'])
        if not isinstance(providers, list):
            raise ValueError('Provider records are unavailable')
        for rows in (workers, providers):
            if any(row.get('id') not in trace or row.get('trial_id') != config['trial_id'] for row in rows):
                raise ValueError('Unexpected or out-of-trial role record')
            if any(count != 1 for count in Counter(row['id'] for row in rows).values()):
                raise ValueError('Duplicate role IDs make native call coverage ambiguous')
        if Counter(written) != Counter(row['id'] for row in workers):
            raise ValueError('Controller write attempts do not exactly match terminal worker records')
        for rows, field in ((workers, 'worker_finished'), (providers, 'provider_finished')):
            if any(type(row.get(field)) not in (int, float) or not math.isfinite(row[field]) for row in rows):
                raise ValueError('Role archive has no terminal completion timestamp')
        started = []
        for row in workers:
            count = row.get('inference_attempts')
            if type(count) is not int or count not in (0, 1):
                raise ValueError('Unregistered worker inference-attempt count')
            if (row.get('provider_call_started') is not None) != (count == 1):
                raise ValueError('Worker call-start and attempt count disagree')
            if count:
                started.append(row['id'])
            elif row.get('provider_call_finished') is not None:
                raise ValueError('Worker call completion has no start')
        if Counter(started) != Counter(row['id'] for row in providers):
            raise ValueError('Worker call starts do not exactly match provider records')
        for row in providers:
            if type(row.get('allowed')) is not bool:
                raise ValueError('Provider admission outcome is unknown')
            if row['allowed']:
                attempts.append({'id': row['id'], 'origin': 'provider_allowed',
                                 'request_sha256': model_request_sha256(trace[row['id']])})
    counts = Counter(row['request_sha256'] for row in attempts)
    return {'count': len(attempts), 'attempts': attempts, 'request_sha256_counts': dict(sorted(counts.items()))}


def validate_proxy_state(state, identity, before=None):
    """Reject missing counters, process changes and unresolved native outcomes."""
    if not isinstance(state, dict):
        raise ValueError('Native proxy state is unavailable')
    for name in ('source_id', 'git_base', 'proxy_sha256'):
        if state.get(name) != identity[name]:
            raise ValueError('Native proxy source identity changed: ' + name)
    if state.get('clock') != 'time.monotonic' or not isinstance(state.get('clock_domain'), str) or not state['clock_domain']:
        raise ValueError('Native proxy clock domain is unavailable')
    for name in ('started_total', 'finished_total', 'uncertain_total'):
        if type(state.get(name)) is not int or state[name] < 0:
            raise ValueError('Native proxy counter is unavailable: ' + name)
    active = state.get('active')
    if not isinstance(active, list) or any(not isinstance(row, dict) or type(row.get('sequence')) is not int for row in active):
        raise ValueError('Native proxy active-call identities are unavailable')
    sequences = [row['sequence'] for row in active]
    if (len(set(sequences)) != len(sequences) or any(not 0 < seq <= state['started_total'] for seq in sequences)
            or state['started_total'] - state['finished_total'] != len(active)
            or state['uncertain_total'] > state['finished_total']):
        raise ValueError('Native proxy counters and active-call identities disagree')
    if before is not None:
        if state['clock_domain'] != before['clock_domain']:
            raise ValueError('Native proxy process or clock domain changed')
        if any(state[name] < before[name] for name in ('started_total', 'finished_total', 'uncertain_total')):
            raise ValueError('Native proxy counters moved backwards')


def native_completion_certificate(config, result, before, identity, deadline, began):
    """Observe native terminal responses within the controller's one 900s budget.

    Proxy responses are evidence only. They never replace deadline-scored client
    outcomes, including a late response completed after the ten-second deadline.
    """
    certificate = {'schema': 'alien6-native-completion-v1', 'valid': False,
        'scope': 'Native completion coverage, separate from useful client response scoring.',
        'driver_clock': {'clock': 'time.monotonic', 'scope': 'requesting_VM_process', 'pid': os.getpid(),
                         'clock_domain': platform.node() + ':pid:' + str(os.getpid())},
        'driver_began': began, 'driver_deadline': deadline, 'budget_seconds': 900,
        'pre_state': before, 'expected': None, 'summary_polls': [], 'terminal_state': None, 'issues': []}
    fetched = False
    baseline = before.get('started_total') if isinstance(before, dict) else None
    url = config['provider']['ollama_url'].rstrip('/') + '/study-state'
    try:
        validate_proxy_state(before, identity)
        if before['active'] or before['uncertain_total']:
            raise ValueError('Native proxy was not idle with fully known outcomes before the trial')
        expected = expected_native_calls(config, result)
        certificate['expected'] = expected
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Shared controller and native-completion budget expired')
            state = json_http(url + '?after=' + str(baseline) + '&records=0', timeout=min(5, remaining))
            certificate['summary_polls'].append({'driver_at': time.monotonic(), 'state': state})
            validate_proxy_state(state, identity, before)
            if state['uncertain_total'] != before['uncertain_total']:
                raise ValueError('Native proxy recorded an uncertain terminal outcome')
            started = state['started_total'] - baseline
            finished = state['finished_total'] - before['finished_total']
            if started > expected['count'] or finished > expected['count']:
                raise ValueError('Native proxy observed extra unaccounted calls')
            if not state['active'] and started == finished == expected['count']:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Shared controller and native-completion budget expired before terminal archive')
                fetched = True
                terminal = json_http(url + '?after=' + str(baseline) + '&records=1', timeout=min(5, remaining))
                certificate['terminal_state'] = terminal
                validate_proxy_state(terminal, identity, before)
                if (terminal['active'] or terminal['uncertain_total'] != before['uncertain_total']
                        or terminal['started_total'] - baseline != expected['count']
                        or terminal['finished_total'] - before['finished_total'] != expected['count']):
                    raise ValueError('Native proxy state changed before terminal archive')
                records = terminal.get('records')
                if not isinstance(records, list) or len(records) != expected['count']:
                    raise ValueError('Native terminal response records are missing')
                if Counter(row.get('sequence') for row in records) != Counter(range(baseline + 1, terminal['started_total'] + 1)):
                    raise ValueError('Native terminal sequence coverage is incomplete or duplicated')
                if Counter(row.get('request_sha256') for row in records) != Counter(expected['request_sha256_counts']):
                    raise ValueError('Native terminal request-payload multiset differs from the expected calls')
                for row in records:
                    if (row.get('status') != 200 or row.get('done') is not True or row.get('error') is not None or row.get('uncertain') is not False
                            or not isinstance(row.get('response'), dict) or row['response'].get('done') is not True
                            or type(row.get('started')) not in (int, float) or type(row.get('finished')) not in (int, float)
                            or not math.isfinite(row['started']) or not math.isfinite(row['finished']) or row['finished'] < row['started']):
                        raise ValueError('Native response has no complete successful terminal generation')
                if time.monotonic() > deadline:
                    raise TimeoutError('Shared controller and native-completion budget expired while archiving')
                certificate['valid'] = True
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    except Exception as error:
        certificate['issues'].append({'type': type(error).__name__, 'detail': str(error)})
        # A failed certificate still retains every terminal response currently
        # available. No retry, reset or next trial follows this failure.
        remaining = deadline - time.monotonic()
        if not fetched and type(baseline) is int and remaining > 0:
            try:
                certificate['terminal_state'] = json_http(url + '?after=' + str(baseline) + '&records=1', timeout=min(5, remaining))
            except Exception as archive_error:
                certificate['terminal_archive_error'] = {'type': type(archive_error).__name__, 'detail': str(archive_error)}
    certificate['driver_finished'] = time.monotonic()
    return certificate


def run_block(plan_path, block, out, image):
    identity = collection_identity()
    metadata = verify_server()
    command(['docker', 'update', '--cpus', '4', 'a6-capacity-v2-control-plane'])
    plan = json.loads(plan_path.read_text())
    out.mkdir(parents=True, exist_ok=False)
    (out / 'manifest.json').write_text(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
        **identity, 'code_sha256': code_hash(),
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
        verify_worktree(ROOT, identity['source_id'])
        config = dict(original)
        proxy_before, completion = None, None
        proxy_identity = {**identity, 'proxy_sha256': hashlib.sha256((ROOT / 'v2/inference_proxy.py').read_bytes()).hexdigest()}
        if block == 'real-model':
            try:
                proxy_before = json_http(config['provider']['ollama_url'].rstrip('/') + '/study-state?after=0&records=0', timeout=5)
                validate_proxy_state(proxy_before, proxy_identity)
                if proxy_before['active'] or proxy_before['uncertain_total']:
                    raise ValueError('Native inference has active or uncertain work before reset')
            except Exception as error:
                failure = {'configuration': config, 'trial_started': False, 'valid': False,
                    'native_completion_certificate': {'valid': False, 'pre_state': proxy_before,
                        'issues': [{'type': type(error).__name__, 'detail': str(error)}]}}
                (out / (config['trial_id'] + '-native-preflight.json')).write_text(json.dumps(failure, separators=(',', ':')) + '\n')
                (out / 'block.json').write_text(json.dumps({'complete': False, 'qualified': False,
                    'trials': len(recorded), 'invalid': [row['configuration']['trial_id'] for row in recorded if not row['valid']],
                    'reason': 'native_preflight_failed', 'trial_not_started': config['trial_id']}, indent=2) + '\n')
                raise RuntimeError('Native inference is not certified idle; no trial may reset this state') from error
        routing = scale(config['replicas'])
        config['worker_addresses'] = ['http://' + p['ip'] + ':8080' for p in routing['pods']]
        encoded = (ROOT / config['trace_reference']['path']).read_bytes()
        assert hashlib.sha256(encoded).hexdigest() == config['trace_reference']['sha256']
        config['trace'] = json.loads(gzip.decompress(encoded))
        config['trace_decoded_sha256'] = hashlib.sha256(canonical(config['trace'])).hexdigest()
        json_http(CONTROL + '/prepare', config)
        result, collection_error = None, None
        with Observer() as observer, InferenceObserver(out.name + ':' + config['trial_id']) as native:
            began = time.monotonic()
            deadline = began + 900
            try:
                result = json_http(CONTROL + '/run', config, timeout=max(.001, deadline - time.monotonic()))
            except Exception as error:
                collection_error = {'type': type(error).__name__, 'detail': str(error)}
            if block == 'real-model':
                completion = native_completion_certificate(config, result, proxy_before, proxy_identity, deadline, began)
        issues, hit = [], set()
        try:
            if result is None:
                issues.append('controller_run_failed')
            else:
                issues.extend(validate(config, result, routing, observer.samples))
                if any(role.get('source_id') != identity['source_id'] for sample in result['resource_samples']
                       for role in [sample['controller'], sample['provider'], *sample['workers']]):
                    issues.append('collection_source_identity_mismatch')
                hit = {r['uid'] for key, archive in result['role_records'].items() if key != 'provider' for r in archive['records']}
                expected_hash = hashlib.sha256((ROOT / 'v2/model_service.py').read_bytes()).hexdigest()
                if any(s['controller'].get('model_controller_sha256') != expected_hash for s in result['resource_samples']):
                    issues.append('model_controller_hash_mismatch')
        except Exception as error:
            issues.append('controller_result_validation_failed')
            collection_error = collection_error or {'type': type(error).__name__, 'detail': str(error)}
        if completion is not None and not completion['valid']:
            issues.append('native_completion_not_certified')
        expected = {p['uid'] for p in routing['pods']}
        if block == 'real-model' and hit <= expected:
            issues = [x for x in issues if x != 'observed_worker_uid_set_differs']
        if not native.samples or any('unavailable' in row for row in native.samples):
            issues.append('inference_process_observations_missing')
        if any(row.get('other_server_model_count') != 0 for row in native.samples):
            issues.append('other_ollama_server_not_observed_idle')
        if INFERENCE_MONITOR and native.metadata.get('source_id') != identity['source_id']:
            issues.append('native_monitor_source_identity_mismatch')
        model_state_error = None
        try:
            after_model = json_http(BASE + '/api/ps')
        except Exception as error:
            model_state_error = type(error).__name__
            after_model = {'unavailable': model_state_error}
            issues.append('model_state_query_failed')
        if block == 'real-model' and model_state_error is None:
            loaded = [m for m in after_model.get('models', []) if m.get('name') == MODEL]
            if len(loaded) != 1 or loaded[0].get('digest') != DIGEST or loaded[0].get('context_length') != 8192:
                issues.append('loaded_model_identity_or_context_changed')
        document = {'configuration': config, 'routing': routing, 'observed_serving_uids': sorted(hit),
                    'observations': observer.samples, 'result': result, 'issues': issues, 'valid': not issues,
                    'native_observations': native.samples,
                    'inference_observer_metadata': native.metadata,
                    'model_after_trial': after_model,
                    'other_server_loaded_model_count': native.samples[-1].get('other_server_model_count') if native.samples else None}
        if completion is not None:
            document['native_completion_certificate'] = completion
        if collection_error is not None:
            document['controller_run_error'] = collection_error
        (out / (config['trial_id'] + '.json')).write_text(json.dumps(document, separators=(',', ':')) + '\n')
        recorded.append(document)
        print(json.dumps({'trial': config['trial_id'], 'valid': not issues, 'issues': issues}), flush=True)
        if (completion is not None and not completion['valid']) or collection_error is not None:
            reason = 'native_completion_not_certified' if completion is not None and not completion['valid'] else 'controller_run_failed'
            (out / 'block.json').write_text(json.dumps({'complete': False, 'qualified': False,
                'trials': len(recorded), 'invalid': [row['configuration']['trial_id'] for row in recorded if not row['valid']],
                'reason': reason}, indent=2) + '\n')
            raise RuntimeError('Native completion or controller result unavailable; measured trial retained and no subsequent reset allowed')
        if not result['drained']:
            (out / 'block.json').write_text(json.dumps({'complete': False, 'qualified': False,
                'trials': len(recorded), 'reason': 'residual_work_not_drained'}, indent=2) + '\n')
            raise RuntimeError('Residual work remains; no subsequent trial may reset this state')
        if native.metadata.get('stop_error'):
            (out / 'block.json').write_text(json.dumps({'complete': False, 'qualified': False,
                'trials': len(recorded), 'reason': 'native_monitor_stop_failed'}, indent=2) + '\n')
            raise RuntimeError('Native monitor could not be drained; partial block retained')
        if model_state_error is not None:
            (out / 'block.json').write_text(json.dumps({'complete': False, 'qualified': False,
                'trials': len(recorded), 'invalid': [row['configuration']['trial_id'] for row in recorded if not row['valid']],
                'reason': 'model_state_query_failed', 'error': model_state_error}, indent=2) + '\n')
            raise RuntimeError('Model state query failed; measured trial retained and block stopped')
    complete = {'complete': True, 'qualified': all(r['valid'] for r in recorded), 'trials': len(recorded),
                'invalid': [r['configuration']['trial_id'] for r in recorded if not r['valid']]}
    (out / 'block.json').write_text(json.dumps(complete, indent=2) + '\n')
    print(json.dumps(complete), flush=True)
    if not complete['qualified']: raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['pilot', 'warmup', 'freeze', 'block'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--block')
    parser.add_argument('--image', default='alien6-capacity:model-v2')
    window = parser.add_mutually_exclusive_group()
    window.add_argument('--measurement-seconds', type=int, help='Prospective measurement window for freeze; default 20')
    window.add_argument('--cpu-window', action='store_true', help='Freeze the pilot-derived CPU window for at least ten expected low-load arrivals')
    args = parser.parse_args()
    if args.stage in ['pilot', 'warmup']:
        if args.out.exists(): raise RuntimeError('Refusing to replace pilot results')
        args.out.parent.mkdir(parents=True, exist_ok=True)
        result = asyncio.run(pilot(args.out) if args.stage == 'pilot' else warmup(args.out))
        if not result['valid']: raise SystemExit(1)
    elif args.stage == 'freeze':
        seconds = cpu_measurement_seconds(json.loads(args.pilot.read_text())) if args.cpu_window else (20 if args.measurement_seconds is None else args.measurement_seconds)
        print(json.dumps(freeze(args.pilot, args.out, seconds)['rates']))
    else:
        run_block(args.plan, args.block, args.out, args.image)
