#!/usr/bin/env python3
"""Prove termination and restart only the dedicated FRA inference services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

SOURCE_ID = 'sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f'
GIT_BASE = 'db31fdad5e022ae165c1661c41632c1c9a761b84'
PROXY_SHA256 = '098e2c7a82e895affdd8673fa3a799c3cbd0929a7fde9a798b9cd16f6f30d14c'
INSTANCE = 'a6-ollama-study'
SOURCE_ROOT = Path('/opt/alien6-research')
SERVICES = ('alien6-ollama-proxy.service', 'alien6-ollama-monitor.service', 'alien6-ollama.service')
PORTS = '( sport = :11435 or sport = :11436 or sport = :11437 )'
FIELDS = ('ActiveState', 'SubState', 'MainPID', 'ControlGroup', 'Result', 'KillMode', 'SendSIGKILL',
          'CPUAccounting', 'CPUWeight', 'User', 'DropInPaths')
BUDGET_SECONDS = 120
PRIMARY_SECONDS = 80


class RecoveryFailure(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, data):
    if path.is_symlink():
        raise RecoveryFailure('The output must not be a symbolic link')
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def known_processes(proc=Path('/proc')):
    """Inspect this container only; retain identities, never unrelated command lines."""
    known, unavailable = [], []
    for directory in proc.iterdir():
        if not directory.name.isdigit(): continue
        try:
            comm = (directory / 'comm').read_text().strip()
            arguments = (directory / 'cmdline').read_bytes().split(b'\0')
            group = (directory / 'cgroup').read_text()
            executable = os.readlink(directory / 'exe')
            module = next((name for name in ('v2.inference_proxy', 'v2.inference_monitor') if name.encode() in arguments), None)
            native = (executable.startswith('/opt/ollama/') or Path(executable).name in
                      ('ollama', 'runner', 'ollama_llama_server', 'llama-server', 'llama_server'))
            if native or module or any(unit in group for unit in SERVICES):
                known.append({'pid': int(directory.name), 'comm': comm, 'executable': executable,
                              'module': module, 'cgroup': group})
        except OSError as error:
            if directory.exists(): unavailable.append({'pid': int(directory.name), 'error': type(error).__name__})
    return known, unavailable


def assert_terminated(observation):
    units, groups = observation.get('units', {}), observation.get('cgroups', {})
    if set(units) != set(SERVICES) or set(groups) != set(SERVICES):
        raise RecoveryFailure('Termination proof does not cover all three study units')
    for unit in SERVICES:
        state, group = units[unit], groups[unit]
        expected = '/system.slice/' + unit
        if (state.get('ActiveState') != 'inactive' or state.get('SubState') != 'dead'
                or state.get('MainPID') != '0' or state.get('ControlGroup') not in ('', expected)):
            raise RecoveryFailure('Study unit still has an active lifecycle or main PID: ' + unit)
        if group.get('path') != '/sys/fs/cgroup' + expected:
            raise RecoveryFailure('Unexpected unit cgroup path')
        if group.get('exists') is not False and not (group.get('exists') is True
                and group.get('populated') == 0 and group.get('recursive_procs') == []):
            raise RecoveryFailure('Study unit cgroup is not proven absent or empty: ' + unit)
    for field in ('known_processes', 'unavailable_processes', 'listeners'):
        if observation.get(field) != []:
            raise RecoveryFailure('Termination cannot be proved: ' + field)


def verify_recovery_record(record, source_id=SOURCE_ID, git_base=GIT_BASE):
    """Pure offline verification of process termination and a fresh native epoch."""
    if (source_id != SOURCE_ID or git_base != GIT_BASE or record.get('schema') != 'alien6-inference-recovery-v1'
            or record.get('source_id') != source_id or record.get('git_base') != git_base
            or record.get('instance') != INSTANCE or record.get('mode') != 'reset'
            or record.get('status') != 'reset_complete' or record.get('valid') is not True
            or record.get('complete') is not True or record.get('generation_requests') != 0):
        raise RecoveryFailure('Recovery identity or lifecycle differs from the authorized reset')
    proof = record.get('termination_proof', {})
    assert_terminated(proof)
    ledger = record.get('post_proxy_state', {})
    if (ledger.get('source_id') != SOURCE_ID or ledger.get('git_base') != GIT_BASE
            or ledger.get('proxy_sha256') != PROXY_SHA256 or ledger.get('clock') != 'time.monotonic'
            or any(type(ledger.get(field)) is not int or ledger[field] != 0
                   for field in ('started_total', 'finished_total', 'uncertain_total'))
            or ledger.get('active') != [] or ledger.get('records') != []):
        raise RecoveryFailure('New proxy ledger is not a complete empty BBA epoch')
    previous, epoch = record.get('previous_epoch'), ledger.get('clock_domain')
    if (not isinstance(epoch, str) or not re.fullmatch(r'native-inference-proxy:[0-9a-f]{32}', epoch)
            or not isinstance(previous, str) or not re.fullmatch(r'native-inference-proxy:[0-9a-f]{32}', previous)
            or epoch == previous or record.get('new_epoch') != epoch
            or record.get('previous_epoch_origin') not in ('observed', 'supplied')):
        raise RecoveryFailure('Native proxy restart epoch is missing, unchanged or ambiguous')
    if record.get('post_native_version') != {'version': '0.40.16'} or record.get('post_native_model_state') != {'models': []}:
        raise RecoveryFailure('Fresh native server must be pinned and unloaded; warmup is separate')
    post_units = record.get('post_units', {})
    if set(post_units) != set(SERVICES) or any(state.get('ActiveState') != 'active' or state.get('SubState') != 'running'
            or not str(state.get('MainPID', '')).isdigit() or int(state['MainPID']) <= 0 for state in post_units.values()):
        raise RecoveryFailure('Fresh inference services are not all running')
    times = [record.get('started_monotonic'), proof.get('monotonic'), record.get('finished_monotonic')]
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in times)
            or not times[0] <= times[1] <= times[2] or times[2] - times[0] > BUDGET_SECONDS
            or record.get('budget_seconds') != BUDGET_SECONDS):
        raise RecoveryFailure('Recovery does not fit the single 120-second monotonic budget')
    starts = [row for row in record.get('commands', []) if row.get('argv') == ['systemctl', 'start', '--no-block', *SERVICES]]
    stops = [row for row in record.get('commands', []) if row.get('argv') == ['systemctl', 'stop', '--no-block', *SERVICES]]
    if (len(starts) != 1 or not stops or starts[0].get('returncode') != 0
            or not any(row.get('returncode') == 0 and row.get('finished_monotonic', math.inf) <= times[1] for row in stops)
            or starts[0].get('started_monotonic', -math.inf) < times[1]):
        raise RecoveryFailure('Restart did not follow a successful stop and termination proof')
    return {'valid': True, 'new_epoch': epoch, 'source_id': SOURCE_ID, 'generation_requests': 0}


def validate_proof(record):
    return verify_recovery_record(record)


class Recovery:
    def __init__(self, out, reset=False, previous_epoch=None, *, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
        self.out, self.reset, self.previous_epoch = out, reset, previous_epoch
        self.runner, self.clock, self.sleep = runner, clock, sleep
        self.began = clock(); self.deadline = self.began + BUDGET_SECONDS
        self.mutated = False
        self.record = {'schema': 'alien6-inference-recovery-v1', 'mode': 'reset' if reset else 'check-only',
            'instance': INSTANCE, 'source_id': SOURCE_ID, 'git_base': GIT_BASE,
            'status': 'starting', 'complete': False, 'valid': False, 'generation_requests': 0,
            'scope': 'Termination and restart evidence only; no missing generation is reconstructed and no experimental result is qualified.',
            'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'clock_domain': 'inference-recovery:' + uuid.uuid4().hex,
            'started_utc': utc_now(), 'started_monotonic': self.began, 'budget_seconds': BUDGET_SECONDS,
            'primary_seconds': PRIMARY_SECONDS, 'cleanup_reserve_seconds': BUDGET_SECONDS - PRIMARY_SECONDS,
            'commands': [], 'observations': []}

    def save(self):
        self.record['updated_utc'] = utc_now()
        atomic_write(self.out, self.record)

    def remaining(self, limit=None):
        value = min(self.deadline, limit if limit is not None else self.deadline) - self.clock()
        if value <= 0: raise RecoveryFailure('The global recovery budget expired')
        return value

    def command(self, argv, limit=None):
        row = {'argv': argv, 'started_monotonic': self.clock(), 'started_utc': utc_now()}
        self.record['commands'].append(row)
        try:
            result = self.runner(argv, capture_output=True, text=True, timeout=min(5, self.remaining(limit)))
            row.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            if result.returncode: raise RecoveryFailure('Recovery command failed: ' + ' '.join(argv))
            return result.stdout
        except Exception as error:
            row['error'] = {'type': type(error).__name__, 'detail': str(error)}
            if isinstance(error, subprocess.TimeoutExpired):
                row.update(timed_out=True, stdout=(error.stdout or b'').decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout,
                           stderr=(error.stderr or b'').decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr)
            raise
        finally:
            row['finished_monotonic'] = self.clock(); self.save()

    def get(self, url, limit=None):
        with urllib.request.urlopen(url, timeout=min(3, self.remaining(limit))) as response:
            return json.load(response)

    def environment(self):
        if (os.geteuid() != 0 or socket.gethostname().split('.')[0] != INSTANCE
                or Path('/run/systemd/container').read_text().strip() != 'lxc'
                or Path('/etc/alien6-research-instance').read_text().strip() != INSTANCE
                or Path('/etc/alien6-research/site').read_text().strip() != 'fra6s004'):
            raise RecoveryFailure('Run only as root inside the marked FRA inference LXC')
        if self.command(['systemd-detect-virt', '--container']).strip() != 'lxc':
            raise RecoveryFailure('Container virtualization is not LXC')
        addresses = json.loads(self.command(['ip', '-j', '-4', 'address', 'show', 'dev', 'eth0']))
        if not any(info.get('local') == '172.30.64.3' and info.get('prefixlen') == 29 for row in addresses for info in row.get('addr_info', [])):
            raise RecoveryFailure('The dedicated FRA inference address is missing')
        sys.path.insert(0, str(SOURCE_ROOT))
        from v2.identity import capture, verify_worktree
        snapshot = capture(SOURCE_ROOT); verify_worktree(SOURCE_ROOT, SOURCE_ID)
        if snapshot.source_id != SOURCE_ID or snapshot.git_base != GIT_BASE:
            raise RecoveryFailure('Inference source is not the retained BBA instrument')
        for unit in SERVICES:
            if Path('/etc/systemd/system', unit).read_bytes() != (SOURCE_ROOT / 'infra' / unit).read_bytes():
                raise RecoveryFailure('Installed study service differs from the retained template')
        limits = {name: Path('/sys/fs/cgroup', name).read_text().strip() for name in ('cpu.max', 'memory.max', 'memory.swap.max')}
        quota, period = map(int, limits['cpu.max'].split())
        if quota != 8 * period or int(limits['memory.max']) != 48 * 1024 ** 3 or limits['memory.swap.max'] != '0':
            raise RecoveryFailure('Inference resource caps differ from the registered CPU study')
        self.record['environment'] = {'source': snapshot.metadata(), 'limits': limits, 'source_root': str(SOURCE_ROOT)}

    def units(self, limit=None):
        result = {}
        for unit in SERVICES:
            state = dict(line.split('=', 1) for line in self.command(['systemctl', 'show', unit, '--property=' + ','.join(FIELDS)], limit).splitlines() if '=' in line)
            if (state.get('KillMode') != 'control-group' or state.get('SendSIGKILL') != 'yes'
                    or state.get('User') != 'alien6-study' or state.get('DropInPaths') != ''
                    or state.get('CPUAccounting') != 'yes' or state.get('CPUWeight') != '100'
                    or state.get('ControlGroup') not in ('', '/system.slice/' + unit)):
                raise RecoveryFailure('Study service lifecycle or isolation differs from the retained unit: ' + unit)
            result[unit] = state
        return result

    def observe(self, phase, limit=None):
        self.remaining(limit)
        units, groups = self.units(limit), {}
        for unit in SERVICES:
            path = Path('/sys/fs/cgroup/system.slice', unit)
            group = {'path': str(path), 'exists': path.exists()}
            if group['exists']:
                try:
                    events = dict(line.split() for line in (path / 'cgroup.events').read_text().splitlines())
                    group.update(populated=int(events['populated']), recursive_procs=sorted({int(value) for child in (path, *path.rglob('*')) if child.is_dir() for value in (child / 'cgroup.procs').read_text().split()}))
                except OSError:
                    if path.exists(): raise
                    group = {'path': str(path), 'exists': False}
            groups[unit] = group
        processes, unavailable = known_processes()
        listeners = self.command(['ss', '-H', '-ltnp', PORTS], limit).splitlines()
        value = {'phase': phase, 'utc': utc_now(), 'monotonic': self.clock(), 'units': units,
                 'cgroups': groups, 'known_processes': processes, 'unavailable_processes': unavailable, 'listeners': listeners}
        self.record['observations'].append(value); self.save()
        return value

    def wait_stopped(self, limit):
        while True:
            observation = self.observe('stopped', limit)
            try: assert_terminated(observation)
            except RecoveryFailure:
                self.sleep(min(.5, self.remaining(limit)))
                continue
            return observation

    def execute(self):
        try:
            self.environment()
            self.record['before'] = self.observe('before')
            try:
                before = self.get('http://172.30.64.3:11437/study-state?after=0&records=1')
                self.record['pre_proxy_state'] = before
                if before.get('source_id') != SOURCE_ID or before.get('proxy_sha256') != PROXY_SHA256 or before.get('git_base') != GIT_BASE:
                    raise RecoveryFailure('Live proxy source differs from BBA')
                if self.previous_epoch is not None and self.previous_epoch != before.get('clock_domain'):
                    raise RecoveryFailure('Supplied previous epoch differs from the live proxy')
                self.previous_epoch = before.get('clock_domain'); self.record['previous_epoch_origin'] = 'observed'
            except RecoveryFailure: raise
            except Exception as error:
                self.record['pre_proxy_state_error'] = {'type': type(error).__name__, 'detail': str(error)}
                self.record['previous_epoch_origin'] = 'supplied'
            self.record['previous_epoch'] = self.previous_epoch
            if not self.reset:
                try:
                    assert_terminated(self.record['before'])
                    self.record['termination_verified'] = True
                except RecoveryFailure:
                    self.record['termination_verified'] = False
                self.record.update(status='checked', complete=True, valid=True)
                return 0
            if not isinstance(self.previous_epoch, str) or not re.fullmatch(r'native-inference-proxy:[0-9a-f]{32}', self.previous_epoch):
                raise RecoveryFailure('Reset requires a retained previous proxy epoch')
            self.mutated = True; self.record['status'] = 'stopping'; self.save()
            self.command(['systemctl', 'stop', '--no-block', *SERVICES], self.began + 40)
            self.record['termination_proof'] = self.wait_stopped(self.began + 40); self.save()
            self.record['status'] = 'restarting'; self.save()
            self.command(['systemctl', 'start', '--no-block', *SERVICES], self.began + PRIMARY_SECONDS)
            while True:
                self.remaining(self.began + PRIMARY_SECONDS)
                units = self.units(self.began + PRIMARY_SECONDS)
                if any(state.get('ActiveState') == 'failed' for state in units.values()):
                    raise RecoveryFailure('A study service failed during restart')
                if all(state.get('ActiveState') == 'active' and state.get('SubState') == 'running' for state in units.values()):
                    try:
                        self.record['post_proxy_state'] = self.get('http://172.30.64.3:11437/study-state?after=0&records=1', self.began + PRIMARY_SECONDS)
                        self.record['post_native_version'] = self.get('http://127.0.0.1:11435/api/version', self.began + PRIMARY_SECONDS)
                        self.record['post_native_model_state'] = self.get('http://127.0.0.1:11435/api/ps', self.began + PRIMARY_SECONDS)
                        break
                    except Exception as error:
                        self.record.setdefault('readiness_read_failures', []).append({'type': type(error).__name__, 'detail': str(error)})
                self.sleep(min(.5, self.remaining(self.began + PRIMARY_SECONDS)))
            self.record.update(post_units=units, new_epoch=self.record['post_proxy_state'].get('clock_domain'),
                               status='reset_complete', complete=True, valid=True, finished_monotonic=self.clock())
            verify_recovery_record(self.record)
            return 0
        except Exception as error:
            self.record.update(status='failed', complete=False, valid=False,
                               error={'type': type(error).__name__, 'detail': str(error)})
            self.save()
            if self.mutated:
                try:
                    self.command(['systemctl', 'stop', '--no-block', *SERVICES])
                    self.record['failure_cleanup'] = self.wait_stopped(self.deadline)
                except Exception as cleanup_error:
                    self.record['failure_cleanup_error'] = {'type': type(cleanup_error).__name__, 'detail': str(cleanup_error)}
            return 1
        finally:
            self.record.update(finished_utc=utc_now(), finished_monotonic=self.clock()); self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true'); mode.add_argument('--reset', action='store_true')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--previous-epoch')
    args = parser.parse_args()
    path = args.out.absolute()
    if path.parent.resolve() != path.parent or not path.parent.is_dir(): parser.error('Use an existing canonical output directory without symbolic links')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    os.close(descriptor)
    recovery = Recovery(path, args.reset, args.previous_epoch)
    def interrupted(number, frame): raise RecoveryFailure('Recovery interrupted by signal ' + str(number))
    handlers = {number: signal.signal(number, interrupted) for number in (signal.SIGTERM, signal.SIGINT)}
    try: result = recovery.execute()
    finally:
        for number, handler in handlers.items(): signal.signal(number, handler)
    print(json.dumps({'out': str(path), 'status': recovery.record['status'], 'valid': recovery.record['valid'], 'new_epoch': recovery.record.get('new_epoch')}), flush=True)
    return result


if __name__ == '__main__': raise SystemExit(main())
