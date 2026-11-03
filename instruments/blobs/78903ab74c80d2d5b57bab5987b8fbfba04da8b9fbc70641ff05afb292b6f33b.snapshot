#!/usr/bin/env python3
"""Reset only the isolated admission services and prove both fresh ledgers."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import types
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE_HELPER_SHA256 = 'a403a20827f17a6f7115ecbaccd56dbad2044363b921f20f05f92efffe3b3a24'
BASE_HELPER = ROOT / 'infra/recover-fra6s004-inference.py'
if hashlib.sha256(BASE_HELPER.read_bytes()).hexdigest() != BASE_HELPER_SHA256:
    raise RuntimeError('The byte-preserved recovery helper changed')
_spec = importlib.util.spec_from_file_location('_admission_recovery_base_' + uuid.uuid4().hex, BASE_HELPER)
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
BASE_VERIFY = BASE.verify_recovery_record
BASE_KNOWN_PROCESSES = BASE.known_processes
BASE_SOURCE_ID = BASE.SOURCE_ID
SOURCE_ROOT = BASE.SOURCE_ROOT
OLD_PROXY = 'alien6-ollama-proxy.service'
SERVICES = ('alien6-admission-proxy.service', 'alien6-ollama-monitor.service', 'alien6-ollama.service')
BASE.SERVICES = SERVICES
RecoveryFailure = BASE.RecoveryFailure
SCHEMA = 'alien6-admission-recovery-v1'
ADMISSION_URL = 'http://172.30.64.3:11437/study-admission/state?after=0&records=1'
SCHEDULER_PATH = Path('/etc/alien6-research/ollama.env')
SCHEDULER_SHA256 = '327851ffe67396b01de45905c47391ad2f93500b6855f22703e7015908e80a55'
SCHEDULER_SETTINGS = {'OLLAMA_NUM_PARALLEL': '4', 'OLLAMA_MAX_QUEUE': '256',
    'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_CONTEXT_LENGTH': '8192',
    'OLLAMA_KEEP_ALIVE': '30m', 'OLLAMA_NO_CLOUD': 'true'}


def read_scheduler(path=SCHEDULER_PATH):
    """Retain only allowed public settings and the complete file's digest."""
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise RecoveryFailure('The dedicated public scheduler file is not UTF-8') from None
    settings = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(('#', ';')):
            continue
        key, separator, value = stripped.partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '\x00' in stripped:
            raise RecoveryFailure('The dedicated public scheduler file has an invalid assignment')
        if key not in SCHEDULER_SETTINGS:
            continue
        if key in settings:
            raise RecoveryFailure('An allowed public scheduler setting is repeated')
        settings[key] = value.strip()
    if settings != SCHEDULER_SETTINGS:
        raise RecoveryFailure('The six allowed public scheduler settings differ from the observed study')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SCHEDULER_SHA256:
        raise RecoveryFailure('The dedicated public scheduler file differs from its observed source hash')
    return {'path': str(path), 'sha256': digest, 'settings': settings}


def descriptor_id(descriptor):
    if not isinstance(descriptor, dict) or set(descriptor) != {'format', 'files'} or descriptor['format'] != 'alien6-collection-source-v1':
        raise RecoveryFailure('A complete collection source descriptor is required')
    files = descriptor['files']
    if not isinstance(files, dict) or not files:
        raise RecoveryFailure('The study source descriptor is empty')
    for name, row in files.items():
        path = PurePosixPath(name) if isinstance(name, str) else None
        if (not name or path is None or path.is_absolute() or path.as_posix() != name or '\\' in name
                or any(part in ('.', '..') for part in path.parts) or not isinstance(row, dict)
                or set(row) != {'sha256', 'bytes'} or type(row['bytes']) is not int or row['bytes'] < 0
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise RecoveryFailure('Invalid collection source descriptor entry')
    return 'sha256:' + hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def assert_old_proxy_absent(proof):
    state, group = proof.get('state', {}), proof.get('cgroup', {})
    expected = '/system.slice/' + OLD_PROXY
    if (proof.get('unit') != OLD_PROXY or state.get('ActiveState') != 'inactive'
            or state.get('SubState') != 'dead' or state.get('MainPID') != '0'
            or state.get('ControlGroup') not in ('', expected)
            or group.get('path') != '/sys/fs/cgroup' + expected
            or not (group.get('exists') is False or (group.get('exists') is True
                    and group.get('populated') == 0 and group.get('recursive_procs') == []))):
        raise RecoveryFailure('The old BBA proxy is not proven inactive with an empty cgroup')


def replay_base(record, git_base):
    """Reuse the frozen verifier with isolated service/Git-context globals."""
    namespace = dict(vars(BASE))
    namespace.update(GIT_BASE=git_base, SERVICES=SERVICES)
    namespace['assert_terminated'] = types.FunctionType(BASE.assert_terminated.__code__, namespace)
    verify = types.FunctionType(BASE_VERIFY.__code__, namespace, argdefs=BASE_VERIFY.__defaults__)
    return verify(record, BASE_SOURCE_ID, git_base)


def verify_admission_recovery_record(record, study_source_id, git_base):
    """Pure archived-proof replay: no files, Git, network or service calls."""
    from v2.identity import MEASURED_FILES
    from v2.admission_study import STUDY_FILES
    if (not isinstance(study_source_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', study_source_id)
            or not isinstance(git_base, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', git_base)
            or record.get('admission_recovery_schema') != SCHEMA
            or record.get('study_source_id') != study_source_id or record.get('git_base') != git_base
            or record.get('admission_postcheck_error')):
        raise RecoveryFailure('Admission recovery identity differs from the expected study')
    descriptor = record.get('study_source_descriptor')
    if descriptor_id(descriptor) != study_source_id or set(descriptor['files']) != set(STUDY_FILES):
        raise RecoveryFailure('The recorded study source does not reproduce its complete content ID')
    files = descriptor['files']
    baseline = {'format': descriptor['format'], 'files': {name: files[name] for name in MEASURED_FILES}}
    if descriptor_id(baseline) != BASE_SOURCE_ID:
        raise RecoveryFailure('The study does not retain the byte-preserved BBA instrument')
    if (record.get('script_sha256') != BASE_HELPER_SHA256
            or files['infra/recover-fra6s004-inference.py']['sha256'] != BASE_HELPER_SHA256
            or record.get('admission_recovery_sha256') != files['infra/recover-fra6s004-admission.py']['sha256']):
        raise RecoveryFailure('Recovery helper identities differ from the study source')
    replay_base(record, git_base)
    for key in ('old_proxy_before', 'old_proxy_after'):
        assert_old_proxy_absent(record.get(key, {}))
    assert_old_proxy_absent(record['termination_proof'].get('old_proxy', {}))
    expected_templates = {unit: files['infra/' + unit]['sha256'] for unit in SERVICES}
    environment = record.get('environment', {})
    if (environment.get('service_templates') != expected_templates
            or environment.get('source') != {'source_id': BASE_SOURCE_ID, 'git_base': git_base}
            or environment.get('study_source') != {'source_id': study_source_id, 'git_base': git_base}
            or environment.get('source_root') != str(SOURCE_ROOT)):
        raise RecoveryFailure('Installed service templates are not bound to the study source')
    if environment.get('ollama_scheduler') != {'path': str(SCHEDULER_PATH),
            'sha256': SCHEDULER_SHA256, 'settings': SCHEDULER_SETTINGS}:
        raise RecoveryFailure('The observed public scheduler settings or source hash differ')
    try:
        limits = environment['limits']
        quota, period = map(int, limits['cpu.max'].split())
        if period <= 0 or quota != 8 * period or int(limits['memory.max']) != 48 * 1024 ** 3 or limits['memory.swap.max'] != '0':
            raise ValueError('Different resource envelope')
    except (KeyError, TypeError, ValueError):
        raise RecoveryFailure('The recorded 8-CPU/48-GiB/zero-swap envelope is unavailable') from None
    for states in (record['post_units'], record['termination_proof']['units']):
        for state in states.values():
            if (state.get('KillMode') != 'control-group' or state.get('SendSIGKILL') != 'yes'
                    or state.get('CPUAccounting') != 'yes' or state.get('CPUWeight') != '100'
                    or state.get('User') != 'alien6-study' or state.get('DropInPaths') != ''):
                raise RecoveryFailure('Recorded service lifecycle settings differ from the required templates')
    state = record.get('post_admission_state', {})
    if (state.get('schema') != 'alien6-shared-admission-state-v1'
            or state.get('source_id') != study_source_id or state.get('git_base') != git_base
            or state.get('base_source_id') != BASE_SOURCE_ID
            or state.get('admission_proxy_sha256') != files['v2/admission_proxy.py']['sha256']
            or state.get('clock') != 'time.monotonic' or state.get('clock_domain') != record['new_epoch']
            or state.get('configured') is not False or state.get('configuration_record') is not None
            or state.get('mode') != 'bypass' or type(state.get('capacity')) is not int or state['capacity'] != 1
            or state.get('trial_id') != 'technical-warmup'
            or any(type(state.get(name)) is not int or state[name] != 0
                   for name in ('started_total', 'finished_total', 'occupied', 'quarantined'))
            or state.get('active') != [] or state.get('records') != []
            or state.get('native_state') != record.get('post_proxy_state')):
        raise RecoveryFailure('Admission and native ledgers are not the same clean, unconfigured epoch')
    return {'valid': True, 'new_epoch': record['new_epoch'], 'source_id': BASE_SOURCE_ID,
            'study_source_id': study_source_id, 'generation_requests': 0}


def validate_proof(record):
    return verify_admission_recovery_record(record, record.get('study_source_id'), record.get('git_base'))


def known_processes(proc=Path('/proc')):
    """Extend the private base observer to catch an escaped admission module."""
    known, unavailable = BASE_KNOWN_PROCESSES(proc)
    by_pid = {row['pid']: row for row in known}
    unavailable_pids = {row['pid'] for row in unavailable}
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if b'v2.admission_proxy' not in (directory / 'cmdline').read_bytes().split(b'\0'):
                continue
            by_pid[int(directory.name)] = {'pid': int(directory.name),
                'comm': (directory / 'comm').read_text().strip(),
                'executable': os.readlink(directory / 'exe'), 'module': 'v2.admission_proxy',
                'cgroup': (directory / 'cgroup').read_text()}
        except OSError as error:
            if directory.exists() and int(directory.name) not in unavailable_pids:
                unavailable.append({'pid': int(directory.name), 'error': type(error).__name__})
    return list(sorted(by_pid.values(), key=lambda row: row['pid'])), unavailable


BASE.known_processes = known_processes
# Keep the added post-start validation inside the base try/cleanup and its budget.
BASE.verify_recovery_record = lambda record: validate_proof(record)


class AdmissionRecovery(BASE.Recovery):
    def __init__(self, out, study_source_id, reset=False, previous_epoch=None, **kwargs):
        if not isinstance(study_source_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', study_source_id):
            raise RecoveryFailure('Require an explicit full study source ID')
        self.study_source_id = study_source_id
        super().__init__(out, reset, previous_epoch, **kwargs)
        self.record.update(admission_recovery_schema=SCHEMA, study_source_id=study_source_id,
            admission_recovery_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

    def environment(self):
        from v2.admission_study import STUDY_FILES
        from v2.identity import capture, verify_worktree
        snapshot = capture(SOURCE_ROOT, files=STUDY_FILES)
        if snapshot.source_id != self.study_source_id:
            raise RecoveryFailure('Live admission source differs from the explicitly selected study')
        verify_worktree(SOURCE_ROOT, snapshot.source_id)
        descriptor = {'format': 'alien6-collection-source-v1', 'files': {
            name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in snapshot.files}}
        self.record['study_source_descriptor'] = descriptor
        BASE.GIT_BASE = snapshot.git_base
        self.record['git_base'] = snapshot.git_base
        super().environment()
        self.record['environment'].update(study_source=snapshot.metadata(),
            service_templates={unit: descriptor['files']['infra/' + unit]['sha256'] for unit in SERVICES},
            ollama_scheduler=read_scheduler())
        self.record['old_proxy_before'] = self.old_proxy()
        self.save()

    def old_proxy(self, limit=None):
        fields = 'ActiveState,SubState,MainPID,ControlGroup'
        state = dict(line.split('=', 1) for line in self.command(
            ['systemctl', 'show', OLD_PROXY, '--property=' + fields], limit).splitlines() if '=' in line)
        path = Path('/sys/fs/cgroup/system.slice') / OLD_PROXY
        group = {'path': str(path), 'exists': path.exists()}
        if group['exists']:
            try:
                events = dict(line.split() for line in (path / 'cgroup.events').read_text().splitlines())
                group.update(populated=int(events['populated']), recursive_procs=sorted({int(value)
                    for child in (path, *path.rglob('*')) if child.is_dir()
                    for value in (child / 'cgroup.procs').read_text().split()}))
            except OSError:
                if path.exists():
                    raise
                group = {'path': str(path), 'exists': False}
        proof = {'unit': OLD_PROXY, 'state': state, 'cgroup': group}
        assert_old_proxy_absent(proof)
        return proof

    def observe(self, phase, limit=None):
        value = super().observe(phase, limit)
        value['old_proxy'] = self.old_proxy(limit)
        self.save()
        return value

    def get(self, url, limit=None):
        value = super().get(url, limit)
        if '/study-state?' in url and not self.mutated:
            try:
                admission = super().get(ADMISSION_URL, limit)
            except Exception as error:
                raise RecoveryFailure('Live admission identity could not be verified before reset') from error
            if (admission.get('source_id') != self.study_source_id
                    or admission.get('base_source_id') != BASE_SOURCE_ID
                    or admission.get('git_base') != self.record['git_base']
                    or admission.get('clock_domain') != value.get('clock_domain')):
                raise RecoveryFailure('Live pre-reset admission identity or epoch differs')
            self.record['pre_admission_state'] = admission
        elif url.endswith('/api/ps') and self.mutated:
            try:
                self.record['post_admission_state'] = super().get(ADMISSION_URL, limit)
                self.record['old_proxy_after'] = self.old_proxy(limit)
            except Exception as error:
                # Return to the base's final validator once. It rejects the
                # incomplete evidence inside its existing failure-stop path.
                self.record['admission_postcheck_error'] = {'type': type(error).__name__, 'detail': str(error)}
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--reset', action='store_true')
    mode.add_argument('--check-only', action='store_true')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--previous-epoch')
    parser.add_argument('--study-source-id', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.study_source_id):
        parser.error('Use a full sha256 study source identifier')
    path = args.out.absolute()
    if path.parent.resolve() != path.parent or not path.parent.is_dir():
        parser.error('Use an existing canonical output directory without symbolic links')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    os.close(descriptor)
    recovery = AdmissionRecovery(path, args.study_source_id, args.reset, args.previous_epoch)
    def interrupted(number, frame):
        raise RecoveryFailure('Admission recovery interrupted by signal ' + str(number))
    handlers = {number: signal.signal(number, interrupted) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = recovery.execute()
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    print(json.dumps({'out': str(path), 'status': recovery.record['status'], 'valid': recovery.record['valid'],
                      'new_epoch': recovery.record.get('new_epoch'), 'study_source_id': args.study_source_id}))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
