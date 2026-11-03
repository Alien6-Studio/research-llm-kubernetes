#!/usr/bin/env python3
"""Collect the unattempted suffix with unchanged instruments and bounded epochs.

Each condition is attempted once. A native terminal timeout is retained as an
incomplete observation; it never becomes a successful completion certificate.
The next condition requires a separately archived process-termination proof.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.observe_study_host import atomic_checkpoint
from v2.identity import capture, verify_worktree
from v2.model import expected_native_calls, validate_proxy_state
from v2.verify import (replay_continuation_trial_gate, replay_native_completion_certificate,
                       replay_network_control, verify_standalone_warmup)

_spec = importlib.util.spec_from_file_location('cpu_supervisor', ROOT / 'infra/run-dell-cpu-campaign.py')
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
PREFIX = 'v2-ollama-fra6s004-cpu-continuation'
PARENT_PREFIX = 'v2-ollama-fra6s004-cpu-long-window'
SOURCE_ID = 'sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f'
# Operational caps are explicit, including the BBA collector's unchanged 900s.
CAPS = {'recovery': 150, 'warmup': 750, 'trial': legacy.TRIAL_BUDGET_SECONDS,
        'inspection': 60, 'network': legacy.phase_budgets(335)['network'],
        'qualification': legacy.phase_budgets(335)['qualification']}
RESERVE = 900


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def prospective_budget(count):
    if type(count) is not int or not 0 < count <= 28:
        raise ValueError('Only the frozen, unattempted suffix may be collected')
    return (CAPS['recovery'] + CAPS['network'] + CAPS['qualification']
            + count * sum(CAPS[name] for name in ('recovery', 'warmup', 'trial', 'inspection')))


def classify_trial(document, identity, controller_hash):
    """Keep original validity; distinguish observed overload from broken capture."""
    config, result = document['configuration'], document['result']
    issues = replay_continuation_trial_gate(document, identity['source_id'], controller_hash,
                                            identity['git_base'])
    if issues or not result.get('drained'):
        raise ValueError('Client, role or resource integrity failed: ' + repr(issues))
    certificate = document.get('native_completion_certificate')
    if not isinstance(certificate, dict):
        raise ValueError('Native ledger evidence is missing')
    if certificate.get('valid') is True:
        if document.get('valid') is not True or document.get('issues') != []:
            raise ValueError('The recorded trial validity contradicts its certificate')
        replay_native_completion_certificate(config, result, certificate, identity)
        return {'classification': 'strictly_valid', 'strict_trial_valid': True,
                'client_integrity_valid': True, 'native_completion_certified': True}
    if document.get('valid') is not False or document.get('issues') != ['native_completion_not_certified']:
        raise ValueError('An incomplete native outcome also has an execution defect')
    details = certificate.get('issues')
    allowed = ('Native proxy recorded an uncertain terminal outcome',
               'Shared controller and native-completion budget expired',
               'Shared controller and native-completion budget expired before terminal archive',
               'Shared controller and native-completion budget expired while archiving')
    if not isinstance(details, list) or len(details) != 1 or details[0].get('detail') not in allowed:
        raise ValueError('An unrecognized completion failure cannot be skipped')
    began, deadline, finished = (certificate.get(name) for name in ('driver_began', 'driver_deadline', 'driver_finished'))
    if (certificate.get('schema') != 'alien6-native-completion-v1' or certificate.get('budget_seconds') != 900
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in (began, deadline, finished))
            or abs(deadline - began - 900) > 1e-6 or finished < began):
        raise ValueError('The unchanged native observation budget cannot be established')
    before, terminal = certificate.get('pre_state'), certificate.get('terminal_state')
    validate_proxy_state(before, identity)
    validate_proxy_state(terminal, identity, before)
    if before['active'] or before['uncertain_total']:
        raise ValueError('The trial did not begin with a clean native ledger')
    expected = expected_native_calls(config, result)
    if certificate.get('expected') != expected:
        raise ValueError('Expected native calls are not reproducible')
    records, active = terminal.get('records'), terminal['active']
    previous, previous_at = before, began
    polls = certificate.get('summary_polls')
    if not isinstance(polls, list) or not polls:
        raise ValueError('Native completion observations are missing')
    for poll in polls:
        at, state = poll.get('driver_at'), poll.get('state')
        if type(at) not in (int, float) or not math.isfinite(at) or not previous_at <= at <= finished:
            raise ValueError('Native observation timestamps are not ordered')
        validate_proxy_state(state, identity, previous)
        previous, previous_at = state, at
    validate_proxy_state(terminal, identity, previous)
    if not isinstance(records, list):
        raise ValueError('Terminal native ledger is missing')
    rows = records + active
    baseline = before['started_total']
    if (terminal['started_total'] - baseline != expected['count']
            or len(records) != terminal['finished_total'] - before['finished_total']
            or Counter(row.get('sequence') for row in rows) != Counter(range(baseline + 1, terminal['started_total'] + 1))
            or Counter(row.get('request_sha256') for row in rows) != Counter(expected['request_sha256_counts'])):
        raise ValueError('Native attempts cannot be accounted for exactly')
    uncertain = 0
    for row in records:
        started, finished = row.get('started'), row.get('finished')
        if (type(started) not in (int, float) or type(finished) not in (int, float)
                or not math.isfinite(started) or not math.isfinite(finished) or finished < started):
            raise ValueError('Invalid native terminal timestamps')
        known_success = (row.get('status') == 200 and row.get('done') is True
                         and row.get('error') is None and isinstance(row.get('response'), dict)
                         and row['response'].get('done') is True)
        if row.get('uncertain') is not (not known_success):
            raise ValueError('Native terminal uncertainty accounting is inconsistent')
        uncertain += int(not known_success)
    if terminal['uncertain_total'] - before['uncertain_total'] != uncertain:
        raise ValueError('Native uncertain counters are inconsistent')
    return {'classification': 'native_completion_incomplete', 'strict_trial_valid': False,
            'client_integrity_valid': True, 'native_completion_certified': False,
            'expected_native_calls': expected['count'], 'terminal_native_calls': len(records),
            'uncertain_terminal_calls': uncertain, 'active_at_capture': len(active),
            'interpretation': 'Observed client outcomes only; native completion/resource lifetime is censored. '
                              'This does not establish a unique cause or qualify the original comparison block.'}


class Continuation:
    def __init__(self, args):
        from v2.continuation import check_manifest
        if socket.gethostname().split('.')[0] != 'fra6s004':
            raise RuntimeError('This supervisor may only run on fra6s004')
        self.args = args
        self.campaign = legacy.get_cpu_campaign(PARENT_PREFIX)
        self.manifest_path = (ROOT / args.manifest).resolve()
        check_manifest(self.manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text())
        self.identity = capture(ROOT).metadata()
        if (self.identity['source_id'] != SOURCE_ID
                or self.identity['git_base'] != self.manifest['git_base']):
            raise RuntimeError('Live collection requires the exact BBA bytes and actual registered Git base')
        self.controller_hash = hashlib.sha256((ROOT / 'v2/model_service.py').read_bytes()).hexdigest()
        self.rows = self.manifest['trials']
        self.budget_until = time.monotonic() + prospective_budget(len(self.rows))
        self.hard_deadline = time.monotonic() + 86400 - RESERVE
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.interrupted, self.active = False, None
        args.state_dir.mkdir(parents=True, exist_ok=False)
        self.checkpoint = args.state_dir / 'status.json'
        self.state = {'status': 'starting', 'complete': False, 'qualified': False,
            'scope': 'Exploratory unattempted suffix; the interrupted parent block remains unqualified.',
            'started_utc': utc_now(), **self.identity,
            'manifest_path': str(self.manifest_path.relative_to(ROOT)),
            'manifest_sha256': hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
            'supervisor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'prospective_budget_seconds': prospective_budget(len(self.rows)),
            'shutdown_reserve_seconds': RESERVE, 'phase_caps_seconds': CAPS,
            'trials': [], 'phases': []}
        self.save()

    def save(self):
        self.state['updated_utc'] = utc_now()
        atomic_checkpoint(self.checkpoint, self.state)

    def guard(self):
        if self.interrupted or time.monotonic() >= self.hard_deadline:
            raise RuntimeError('Supervisor signal or lifetime limit reached')
        now = time.monotonic()
        expiry = legacy.validate_guard(json.loads(self.args.guard_status.read_text()), now, self.boot_id,
            self.campaign, max(0, self.budget_until - now),
            hashlib.sha256((ROOT / 'scripts/observe_study_host.py').read_bytes()).hexdigest())
        self.hard_deadline = min(self.hard_deadline, expiry - RESERVE)

    def argv(self, instance, command):
        root = self.campaign['client_root' if instance == legacy.CLIENT else 'inference_root']
        return legacy.lxd_command(instance, root, command, self.campaign)

    def capture(self, instance, command, *, input=None, timeout=60):
        self.guard()
        result = subprocess.run(self.argv(instance, command), capture_output=True, text=True,
                                input=input, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Study command failed')
        return result.stdout

    def write_vm(self, relative, raw):
        code = "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.open('x').write(sys.stdin.read())"
        self.capture(legacy.CLIENT, ['python3', '-c', code, relative], input=raw)

    def read_vm(self, relative):
        return json.loads(self.capture(legacy.CLIENT, ['cat', relative]))

    def verify_sources(self):
        from v2.continuation import check_manifest
        check_manifest(self.manifest_path, deep=False)
        verify_worktree(ROOT, SOURCE_ID)
        # The manifest and every registered method are copied to both guests.
        for instance in (legacy.CLIENT, legacy.INFERENCE):
            code = "from pathlib import Path; from v2.continuation import check_manifest; check_manifest(Path(__import__('sys').argv[1]),deep=False); print('verified')"
            self.capture(instance, ['python3', '-c', code, str(self.manifest_path.relative_to(ROOT))])

    def phase(self, name, instance, command, cap, *, allow_failure=False):
        self.guard()
        unit = PREFIX + '-' + name
        if not all(char.isalnum() or char == '-' for char in unit):
            raise ValueError('Invalid fixed study unit name')
        root = self.campaign['client_root' if instance == legacy.CLIENT else 'inference_root']
        log = root + '/study-logs/' + unit + '.log'
        self.capture(instance, ['python3', '-c', "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.parent.mkdir(exist_ok=True); p.open('x').close()", log])
        entry = {'name': name, 'instance': instance, 'unit': unit + '.service', 'command': command,
                 'started_utc': utc_now(), 'cap_seconds': cap, 'log_path': log, 'status': 'starting'}
        self.state['phases'].append(entry)
        self.state['status'] = name
        self.save()
        self.active = (instance, unit + '.service')
        launch = ['systemd-run', '--quiet', '--no-block', '--unit=' + unit,
                  '--property=Type=oneshot', '--property=TimeoutStartSec=' + str(cap),
                  '--property=TimeoutStopSec=30', '--property=KillMode=control-group',
                  '--property=RemainAfterExit=yes', '--property=StandardOutput=append:' + log,
                  '--property=StandardError=inherit', '--working-directory=' + root]
        launch += ['--setenv=' + key + '=' + value for key, value in legacy.campaign_environment(self.campaign).items()]
        self.capture(instance, launch + ['--', *command])
        deadline = time.monotonic() + cap + 45
        try:
            while True:
                self.guard()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Persistent phase did not terminate within its operational cap')
                def retain(failure):
                    self.state.setdefault('inspection_failures', []).append({'phase': name, **failure})
                    self.save()
                status = legacy.systemd_state(self.argv(instance, ['systemctl', 'show', unit + '.service']), on_failure=retain)
                entry['unit_state'] = status
                self.save()
                try:
                    finished = legacy.phase_finished(status)
                except legacy.GateFailure:
                    if not allow_failure:
                        raise
                    if (status.get('ActiveState') not in ('failed', 'inactive')
                            or status.get('Result') != 'exit-code' or status.get('ExecMainStatus') != '1'):
                        raise
                    finished = True
                if finished:
                    entry['status'] = 'passed' if status.get('Result') == 'success' and status.get('ExecMainStatus') == '0' else 'failed_retained'
                    self.active = None
                    return entry
                time.sleep(5)
        finally:
            entry['finished_utc'] = utc_now()
            try:
                raw = self.capture(instance, ['cat', log])
                (self.args.state_dir / (name + '.log')).write_text(raw)
            except Exception as error:
                entry['log_copy_error'] = str(error)
            self.save()

    def recovery(self, name, target):
        self.capture(legacy.INFERENCE, ['python3', '-c',
            "from pathlib import Path; import sys; Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True)", target])
        self.phase(name, legacy.INFERENCE, ['python3', 'infra/recover-fra6s004-inference.py',
            '--reset', '--out', target, '--previous-epoch', self.previous_epoch], CAPS['recovery'])
        raw = self.capture(legacy.INFERENCE, ['cat', target])
        proof = json.loads(raw)
        # The helper owns detailed checks; replay them independently of its flag.
        from_inference = proof.get('post_ledger', proof.get('post_state', {}))
        # Concrete helper schema is validated by its read-only checker below.
        helper_check = "import importlib.util,json,sys; from pathlib import Path; p=Path('infra/recover-fra6s004-inference.py'); s=importlib.util.spec_from_file_location('recovery',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.validate_proof(json.loads(Path(sys.argv[1]).read_text())); print('verified')"
        self.capture(legacy.INFERENCE, ['python3', '-c', helper_check, target])
        self.write_vm(target, raw)
        self.previous_epoch = from_inference.get('clock_domain') or proof.get('new_epoch')
        if not self.previous_epoch:
            raise ValueError('Certified recovery has no new proxy epoch')
        return proof

    def cleanup(self):
        results = []
        if self.active:
            instance, unit = self.active
            try:
                done = subprocess.run(self.argv(instance, ['systemctl', 'stop', unit]), capture_output=True, text=True, timeout=45)
                results.append({'target': unit, 'returncode': done.returncode, 'stderr': done.stderr})
            except Exception as error:
                results.append({'target': unit, 'error': str(error)})
        try:
            done = subprocess.run(self.argv(legacy.INFERENCE, ['systemctl', 'stop', *legacy.SERVICES]),
                                  capture_output=True, text=True, timeout=100)
            results.append({'target': legacy.INFERENCE, 'services': legacy.SERVICES,
                            'returncode': done.returncode, 'stderr': done.stderr})
        except Exception as error:
            results.append({'target': legacy.INFERENCE, 'error': str(error)})
        self.state['cleanup'] = results

    def export(self):
        """Preserve the whole suffix and recalculate its report after collection."""
        run_root = self.manifest.get('run_root', 'runs/' + PREFIX)
        archive_dir = ROOT / 'archives' / PREFIX
        archive_dir.mkdir(parents=True, exist_ok=False)
        guest_archive = '/tmp/' + PREFIX + '-collection.tar.gz'
        result = subprocess.run(self.argv(legacy.CLIENT, ['tar', '-C', self.campaign['client_root'],
            '-czf', guest_archive, '--', run_root]), capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Raw collection export failed: ' + result.stderr)
        archive = archive_dir / 'collection.tar.gz'
        result = subprocess.run(['lxc', 'file', 'pull', 'local:' + legacy.CLIENT + guest_archive,
                                 str(archive)], capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError('Raw collection copy failed: ' + result.stderr)
        if (ROOT / run_root).exists():
            raise FileExistsError('Refusing to overwrite a previously exported collection')
        with tarfile.open(archive, 'r:gz') as stream:
            for member in stream.getmembers():
                path = Path(member.name)
                if ('..' in path.parts or path.is_absolute() or not path.is_relative_to(run_root)
                        or not (member.isfile() or member.isdir())):
                    raise ValueError('Unexpected path or object in the raw collection archive')
            stream.extractall(ROOT, filter='data')
        files = []
        for path in sorted((ROOT / run_root).rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                files.append({'path': str(path.relative_to(ROOT)), 'bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest()})
        (archive_dir / 'manifest.json').write_text(json.dumps({'files': files,
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'exported_utc': utc_now(), 'scope': 'All retained continuation files, including failed observations.'}, indent=2) + '\n')
        self.state['preservation'] = {'archive': str(archive.relative_to(ROOT)), 'files': len(files),
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        report = subprocess.run(['python3', 'scripts/report_cpu_continuation.py', '--manifest',
            str(self.manifest_path.relative_to(ROOT)), '--out', 'analysis/' + PREFIX], cwd=ROOT,
            capture_output=True, text=True, timeout=180)
        self.state['offline_report'] = {'returncode': report.returncode, 'stdout': report.stdout, 'stderr': report.stderr}
        if report.returncode:
            raise RuntimeError('Offline continuation report failed; raw files remain preserved')

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'interrupted', True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, 'interrupted', True))
        code = 1
        try:
            self.guard()
            self.verify_sources()
            self.previous_epoch = self.args.previous_epoch
            run_root = self.manifest.get('run_root', 'runs/' + PREFIX)
            self.recovery('initial-recovery', run_root + '/initial-recovery.json')
            parent = self.manifest['parent_plan']['path']
            self.phase('network', legacy.CLIENT, ['python3', '-m', 'v2.network_probe', '--plan', parent,
                '--url', self.campaign['inference_url'] + '/transport-control', '--out', run_root + '/network/control.json'], CAPS['network'])
            network = self.read_vm(run_root + '/network/control.json')
            if network.get('qualified') is not True or len(network.get('trials', [])) != 3 or not replay_network_control(network):
                raise RuntimeError('Fresh transport qualification failed')
            self.phase('qualification', legacy.CLIENT, ['python3', '-m', 'v2.model', 'block', '--plan', parent,
                '--block', 'model-qualification', '--image', self.args.image, '--out', run_root + '/qualification'], CAPS['qualification'])
            qualification = self.read_vm(run_root + '/qualification/block.json')
            if qualification != {'complete': True, 'qualified': True, 'trials': 12, 'invalid': []}:
                raise RuntimeError('The complete fresh model-path control block did not qualify')
            for row in self.rows:
                self.verify_sources()
                # Bound remaining operational time; never extend the 24h guard.
                remaining = len(self.rows) - len(self.state['trials'])
                self.budget_until = time.monotonic() + remaining * sum(CAPS[n] for n in ('recovery', 'warmup', 'trial', 'inspection'))
                self.guard()
                self.recovery('recovery-' + row['trial_id'], row['recovery_path'])
                self.phase('warmup-' + row['trial_id'], legacy.CLIENT, ['python3', '-m', 'v2.model', 'warmup', '--out', row['warmup_path']], CAPS['warmup'])
                warm = self.read_vm(row['warmup_path'])
                if warm.get('valid') is not True or len(warm.get('observations', [])) != 4 or not all(x.get('correct') is True for x in warm['observations']):
                    raise RuntimeError('Common four-prompt technical warmup did not pass')
                verify_standalone_warmup(warm)
                if any(warm.get(key) != value for key, value in self.identity.items()):
                    raise RuntimeError('Warmup measurement source identity changed')
                phase = self.phase('trial-' + row['trial_id'], legacy.CLIENT,
                    ['python3', '-m', 'v2.model', 'block', '--plan', row['plan_path'], '--block', 'real-model',
                     '--image', self.args.image, '--out', row['result_directory']], CAPS['trial'], allow_failure=True)
                document = self.read_vm(row['result_path'])
                assessment = classify_trial(document, {**self.identity,
                    'proxy_sha256': hashlib.sha256((ROOT / 'v2/inference_proxy.py').read_bytes()).hexdigest()}, self.controller_hash)
                if (phase['status'] == 'passed') != assessment['strict_trial_valid']:
                    raise RuntimeError('Phase exit status contradicts the replayed trial outcome')
                assessment.update(trial_id=row['trial_id'], parent_index=row['parent_index'],
                    result_path=row['result_path'], recorded_utc=utc_now(), epoch=self.previous_epoch)
                self.write_vm(row['epoch_directory'] + '/assessment.json', json.dumps(assessment, indent=2) + '\n')
                self.state['trials'].append(assessment)
                self.save()
            self.state['status'] = 'exploratory_collection_complete'
            self.state['complete'] = True
            self.state['strict_valid_trials'] = sum(row['strict_trial_valid'] for row in self.state['trials'])
            self.state['native_completion_incomplete_trials'] = sum(not row['strict_trial_valid'] for row in self.state['trials'])
            code = 0
        except Exception as error:
            self.state['status'] = 'stopped_without_retry'
            self.state['error'] = {'type': type(error).__name__, 'detail': str(error)}
        finally:
            self.cleanup()
            self.state['finished_utc'] = utc_now()
            self.save()
            try:
                # Copy the checkpoint even on interruption, without overwriting a
                # previous completed/failed continuation under the same prefix.
                raw = json.dumps(self.state, indent=2) + '\n'
                result = subprocess.run(self.argv(legacy.CLIENT, ['python3', '-c',
                    "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.open('x').write(sys.stdin.read())",
                    self.manifest.get('run_root', 'runs/' + PREFIX) + '/supervisor-final.json']),
                    input=raw, capture_output=True, text=True, timeout=30)
                if result.returncode:
                    self.state['final_checkpoint_copy_error'] = result.stderr
                    self.save()
            except Exception as error:
                self.state['final_checkpoint_copy_error'] = str(error)
                self.save()
            try:
                self.export()
            except Exception as error:
                self.state['preservation_error'] = {'type': type(error).__name__, 'detail': str(error)}
                code = 1
            self.save()
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--guard-status', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--image', default='alien6-capacity:fra6s004-cpu-bba1acee')
    parser.add_argument('--previous-epoch', required=True)
    args = parser.parse_args()
    raise SystemExit(Continuation(args).run())


if __name__ == '__main__':
    main()
