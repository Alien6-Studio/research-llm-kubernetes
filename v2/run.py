"""Isolated kind orchestration, continuous pod identity observations and raw output."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

from v2.plan import ROOT, canonical, load_trace
from v2.service import code_hash

CLUSTER = 'a6-capacity-v2'
NAMESPACE = 'a6-capacity-v2'
KUBE = ROOT / '.kubeconfig-capacity-v2'
CONTROL = 'http://127.0.0.1:18082'


def command(args, data=None):
    return subprocess.check_output(args, input=data, text=True, stderr=subprocess.PIPE)


def kubectl(*args, data=None):
    return command(['kubectl', '--kubeconfig', str(KUBE), '-n', NAMESPACE, *args], data)


def json_http(url, payload=None, timeout=300):
    raw = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=raw, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def identities():
    pods = json.loads(kubectl('get', 'pods', '-l', 'study=capacity-v2', '-o', 'json'))['items']
    return [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
             'role': p['metadata']['labels']['role'], 'deleting': bool(p['metadata'].get('deletionTimestamp')),
             'phase': p['status'].get('phase'), 'ip': p['status'].get('podIP'),
             'ready': bool(p['status'].get('containerStatuses')) and
                      all(s['ready'] for s in p['status'].get('containerStatuses', [])),
             'containers': [{k: s.get(k) for k in ['name', 'imageID', 'restartCount', 'ready', 'state', 'lastState']}
                            for s in p['status'].get('containerStatuses', [])]} for p in pods]


def deploy(image):
    if kubectl('config', 'current-context').strip() != 'kind-' + CLUSTER:
        raise RuntimeError('Refusing any cluster other than the isolated V2 cluster')
    objects = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}}]
    for role, name in [('provider', 'provider'), ('worker', 'workers'), ('controller', 'controller')]:
        labels = {'study': 'capacity-v2', 'role': role}
        env = [{'name': 'ROLE', 'value': role}, {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}]
        pod = {'metadata': {'labels': labels}, 'spec': {'automountServiceAccountToken': False,
            'terminationGracePeriodSeconds': 5,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532},
            'containers': [{'name': name, 'image': image, 'imagePullPolicy': 'Never', 'env': env,
                'ports': [{'containerPort': 8080}],
                'resources': {'requests': {'cpu': '100m', 'memory': '96Mi'},
                              'limits': {'cpu': '1500m' if role == 'controller' else '1000m',
                                         'memory': '1Gi' if role == 'controller' else '512Mi'}},
                'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                    'capabilities': {'drop': ['ALL']}},
                'readinessProbe': {'httpGet': {'path': '/health', 'port': 8080}, 'periodSeconds': 1}}]}}
        objects.extend([{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name},
                         'spec': {'replicas': 1, 'selector': {'matchLabels': labels}, 'template': pod}},
                        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name},
                         'spec': {'selector': labels, 'ports': [{'port': 8080, 'targetPort': 8080}]}}])
    kubectl('apply', '-f', '-', data=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': objects}))
    for name in ['provider', 'workers', 'controller']:
        kubectl('rollout', 'status', 'deployment/' + name, '--timeout=120s')


def scale(replicas):
    kubectl('scale', 'deployment/workers', f'--replicas={replicas}')
    kubectl('rollout', 'status', 'deployment/workers', '--timeout=120s')
    end = time.monotonic() + 120
    while time.monotonic() < end:
        pods = [p for p in identities() if p['role'] == 'worker']
        slices = json.loads(kubectl('get', 'endpointslices', '-l', 'kubernetes.io/service-name=workers', '-o', 'json'))['items']
        endpoints = {e.get('targetRef', {}).get('uid') for s in slices for e in s.get('endpoints', [])
                     if e.get('conditions', {}).get('ready') and not e.get('conditions', {}).get('terminating')}
        expected = {p['uid'] for p in pods if p['ready'] and not p['deleting']}
        if len(pods) == replicas and len(expected) == replicas and endpoints == expected:
            time.sleep(2)
            return {'pods': pods, 'endpoint_slices': slices}
        time.sleep(.25)
    raise RuntimeError('Ready pod UIDs and nonterminating EndpointSlices did not converge')


class Observer:
    def __init__(self):
        self.stopped = threading.Event()
        self.samples = []
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stopped.is_set():
            began = time.monotonic()
            sample = {'utc': datetime.now(timezone.utc).isoformat(), 'at': began}
            try:
                sample['pods'] = identities()
                sample['node_cgroup'] = command(['docker', 'exec', CLUSTER + '-control-plane', 'sh', '-c',
                    'cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.events /sys/fs/cgroup/cpu.pressure /sys/fs/cgroup/memory.pressure'])
                if Path('/proc/stat').exists():
                    sample['host_cpu'] = Path('/proc/stat').read_text()
                    sample['host_memory'] = Path('/proc/meminfo').read_text()
                else:
                    sample['host_memory'] = command(['vm_stat'])
                    sample['host_load'] = command(['sysctl', '-n', 'vm.loadavg'])
            except Exception as error:
                sample['unavailable'] = type(error).__name__
            sample['duration'] = time.monotonic() - began
            self.samples.append(sample)
            self.stopped.wait(max(.01, 1 - sample['duration']))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=15)


def percentile(values, p):
    import math
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


def validate(config, result, routing, observations):
    issues = []
    rows = result['requests']
    if len(rows) != len(config['trace']) or {r['id'] for r in rows} != {r['id'] for r in config['trace']}:
        issues.append('missing_or_duplicate_trace_ids')
    if any(r['emitted'] is None for r in rows): issues.append('trace_not_fully_emitted')
    lag = percentile([r['emitted'] - r['scheduled'] for r in rows if r['emitted'] is not None], .95)
    if lag is None or lag > .05: issues.append('emission_lag_p95_over_50ms')
    expected = {p['uid'] for p in routing['pods']}
    hit = {r.get('response', {}).get('uid') for r in rows} - {None}
    if not config.get('direct_provider') and not config.get('direct_ollama') and hit != expected:
        issues.append('observed_worker_uid_set_differs')
    for sample in result['resource_samples']:
        roles = [sample['controller'], sample['provider'], *sample['workers']]
        if any(role.get('code_sha256') != code_hash() for role in roles): issues.append('code_or_resource_telemetry_missing')
        if {r.get('uid') for r in sample['workers']} != expected: issues.append('sampled_worker_uid_set_differs')
        for role in roles:
            if not role.get('cpu.stat') or role.get('memory.events') is None: issues.append('resource_telemetry_missing')
            events = dict(line.split() for line in (role.get('memory.events') or '').splitlines())
            if int(events.get('oom_kill', 0)): issues.append('observed_oom_kill')
    if not observations or any('unavailable' in s for s in observations): issues.append('kubernetes_or_host_telemetry_missing')
    for sample in observations:
        pods = sample.get('pods', [])
        workers = [p for p in pods if p['role'] == 'worker']
        if {p['uid'] for p in workers} != expected or any(not p['ready'] or p['deleting'] for p in pods):
            issues.append('pod_membership_changed_during_trial')
        if any(c['restartCount'] for p in pods for c in p['containers']): issues.append('pod_restart')
    if not result['drained']: issues.append('residual_work_not_drained')
    role_rows = [row for archive in result['role_records'].values() for row in archive['records']]
    if any(row.get('trial_id') != config['trial_id'] for row in role_rows):
        issues.append('out_of_trial_role_record')
    ledger = result['role_records']['provider']['ledger']
    for window in ledger['windows'].values():
        if window['consumed_units'] > ledger['unit_limit'] or window['consumed_requests'] > ledger['request_limit']:
            issues.append('provider_ledger_overdraw')
    if any(r['state'] == 'reserved' for r in ledger['reservations'].values()): issues.append('unresolved_reservation')
    if config['fastpath']:
        measured = [r for r in rows if config['warmup_seconds'] <= r['scheduled'] - result['start'] < config['warmup_seconds'] + config['measurement_seconds']]
        if sum(useful(r, config) for r in measured) / max(1, len(measured)) < .99: issues.append('fastpath_useful_below_99pct')
    return sorted(set(issues))


def useful(row, config):
    return row['status'] == 200 and row.get('correct', False) and row['latency_seconds'] <= config['deadline_seconds']


def execute(config, out):
    path = out / (config['trial_id'] + '.json')
    if path.exists():
        raise RuntimeError('Trial already exists; no silent replacement or rerun')
    command(['docker', 'update', '--cpus', str(config['node_cpus']), CLUSTER + '-control-plane'])
    routing = scale(config['replicas'])
    config = {**config, 'trace': load_trace(config['trace_reference'], config['fastpath'])}
    config['worker_addresses'] = ['http://' + p['ip'] + ':8080' for p in routing['pods']]
    reset = {'provider': config['provider'], 'workers': {}}
    # Role resets run inside the controller network, before any load is scheduled.
    json_http(CONTROL + '/prepare', config)
    with Observer() as observer:
        result = json_http(CONTROL + '/run', config, timeout=360)
    issues = validate(config, result, routing, observer.samples)
    document = {'utc': datetime.now(timezone.utc).isoformat(), 'configuration': config, 'routing': routing,
                'observations': observer.samples, 'result': result, 'valid': not issues, 'issues': issues}
    path.write_text(json.dumps(document, separators=(',', ':')) + '\n')
    print(json.dumps({'trial': config['trial_id'], 'valid': not issues, 'issues': issues}), flush=True)
    if not result['drained']:
        raise RuntimeError('Residual work remains; no subsequent trial may reset this state')
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['deploy', 'block'])
    parser.add_argument('--image', default='alien6-capacity:v2')
    parser.add_argument('--plan', type=Path, default=ROOT / 'protocol/v2-emulator-plan.json')
    parser.add_argument('--block')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.stage == 'deploy':
        deploy(args.image)
        return
    plan = json.loads(args.plan.read_text())
    configs = plan['blocks'][args.block]
    available = json.loads(command(['docker', 'info', '--format', '{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}']))
    if max(c['node_cpus'] for c in configs) > available['cpus']:
        raise RuntimeError('Host cannot supply the registered CPU envelope')
    args.out.mkdir(parents=True, exist_ok=False)
    manifest = {'utc': datetime.now(timezone.utc).isoformat(), 'source_commit': command(['git', 'rev-parse', 'HEAD']).strip(),
                'code_sha256': code_hash(), 'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'plan_sha256': hashlib.sha256(args.plan.read_bytes()).hexdigest(), 'block': args.block,
                'docker': available, 'kubernetes': json.loads(kubectl('version', '-o', 'json')),
                'node': json.loads(command(['docker', 'inspect', CLUSTER + '-control-plane', '--format',
                    '{"image":"{{.Image}}","memory":{{.HostConfig.Memory}}}'])),
                'image_id': command(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}']).strip()}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rows = []
    for config in configs:
        try:
            rows.append(execute(config, args.out))
        except Exception as error:
            (args.out / 'execution-failure.json').write_text(json.dumps({'trial': config['trial_id'], 'error': type(error).__name__,
                 'message': str(error), 'completed_trials': len(rows)}, indent=2) + '\n')
            raise
    qualified = all(row['valid'] for row in rows)
    summary = {'complete': len(rows) == len(configs), 'qualified': qualified, 'trials': len(rows),
               'invalid': [r['configuration']['trial_id'] for r in rows if not r['valid']],
               'rule': 'A technically invalid block is reported in full and excluded in full.'}
    (args.out / 'block.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)
    if not qualified: raise SystemExit(1)


if __name__ == '__main__':
    main()
