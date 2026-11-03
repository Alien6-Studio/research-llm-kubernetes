"""Recovery proofs use fake services; no process or instance is stopped."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('inference_recovery', Path(__file__).resolve().parents[1] / 'infra/recover-fra6s004-inference.py')
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)
OLD_EPOCH = 'native-inference-proxy:' + '1' * 32
NEW_EPOCH = 'native-inference-proxy:' + '2' * 32


class Clock:
    def __init__(self): self.now = 1000
    def monotonic(self): return self.now
    def sleep(self, duration): self.now += duration


def unit_states(running):
    return {unit: {'ActiveState': 'active' if running else 'inactive', 'SubState': 'running' if running else 'dead',
                  'MainPID': '123' if running else '0', 'ControlGroup': '/system.slice/' + unit if running else '',
                  'KillMode': 'control-group', 'SendSIGKILL': 'yes', 'User': 'alien6-study', 'DropInPaths': '',
                  'CPUAccounting': 'yes', 'CPUWeight': '100'} for unit in RECOVERY.SERVICES}


def ledger(epoch):
    return {'source_id': RECOVERY.SOURCE_ID, 'git_base': RECOVERY.GIT_BASE, 'proxy_sha256': RECOVERY.PROXY_SHA256,
            'clock_domain': epoch, 'clock': 'time.monotonic', 'started_total': 0, 'finished_total': 0,
            'uncertain_total': 0, 'active': [], 'records': []}


class FakeRecovery(RECOVERY.Recovery):
    def __init__(self, out, *, reset=True, running=True, previous=OLD_EPOCH, refuse_stop=False, fail_start=False, orphan=False):
        self.test_clock = Clock()
        self.running, self.restarted = running, False
        self.refuse_stop, self.fail_start, self.orphan = refuse_stop, fail_start, orphan
        self.queries = []
        super().__init__(out, reset, previous, runner=self.fake_command,
                         clock=self.test_clock.monotonic, sleep=self.test_clock.sleep)

    def environment(self):
        self.record['environment'] = {'source': {'source_id': RECOVERY.SOURCE_ID, 'git_base': RECOVERY.GIT_BASE}}

    def fake_command(self, argv, **kwargs):
        self.test_clock.now += .01
        self.assert_timeout = kwargs['timeout']
        if argv == ['systemctl', 'stop', '--no-block', *RECOVERY.SERVICES]:
            if not self.refuse_stop: self.running = False
        elif argv == ['systemctl', 'start', '--no-block', *RECOVERY.SERVICES]:
            if self.fail_start: return subprocess.CompletedProcess(argv, 1, '', 'controlled start failure')
            self.running, self.restarted = True, True
        else: raise AssertionError('Unexpected mutation or command: ' + str(argv))
        return subprocess.CompletedProcess(argv, 0, '', '')

    def units(self, limit=None):
        self.remaining(limit)
        return unit_states(self.running)

    def observe(self, phase, limit=None):
        self.remaining(limit)
        value = {'phase': phase, 'utc': RECOVERY.utc_now(), 'monotonic': self.clock(),
                 'units': self.units(limit),
                 'cgroups': {unit: {'path': '/sys/fs/cgroup/system.slice/' + unit,
                                   'exists': self.running, 'populated': int(self.running),
                                   'recursive_procs': [123] if self.running else []} for unit in RECOVERY.SERVICES},
                 'known_processes': [{'pid': 123}] if self.running or self.orphan else [],
                 'unavailable_processes': [], 'listeners': ['127.0.0.1:11435'] if self.running else []}
        self.record['observations'].append(value); self.save()
        return value

    def get(self, url, limit=None):
        self.remaining(limit); self.queries.append(url)
        if not self.running: raise ConnectionRefusedError('controlled stopped state')
        if '/study-state' in url: return ledger(NEW_EPOCH if self.restarted else OLD_EPOCH)
        if url.endswith('/api/version'): return {'version': '0.40.16'}
        if url.endswith('/api/ps'): return {'models': []}
        raise AssertionError('Unexpected API endpoint: ' + url)


class RecoveryLifecycleTests(unittest.TestCase):
    def execute(self, **kwargs):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'recovery.json'
        recovery = FakeRecovery(path, **kwargs)
        status = recovery.execute()
        return status, recovery, json.loads(path.read_text())

    def test_restart_follows_proven_termination_and_yields_a_new_empty_epoch_without_generation(self):
        status, recovery, record = self.execute()
        self.assertEqual(status, 0)
        self.assertTrue(RECOVERY.validate_proof(record)['valid'])
        self.assertEqual(record['new_epoch'], NEW_EPOCH)
        self.assertEqual(record['previous_epoch_origin'], 'observed')
        self.assertTrue(all('/api/generate' not in url and not url.endswith('/start') for url in recovery.queries))
        self.assertEqual([row['argv'][1] for row in record['commands']], ['stop', 'start'])
        self.assertLess(record['finished_monotonic'] - record['started_monotonic'], 120)
        self.assertFalse(any(path.name.startswith('recovery.json.') for path in recovery.out.parent.iterdir()))

    def test_previously_stopped_services_need_a_retained_epoch_and_keep_its_origin_explicit(self):
        status, _, record = self.execute(running=False)
        self.assertEqual(status, 0)
        self.assertEqual(record['previous_epoch_origin'], 'supplied')
        self.assertTrue(RECOVERY.verify_recovery_record(record, RECOVERY.SOURCE_ID, RECOVERY.GIT_BASE)['valid'])
        status, _, record = self.execute(running=False, previous=None)
        self.assertEqual(status, 1)
        self.assertEqual(record['commands'], [])

    def test_a_supplied_epoch_mismatch_aborts_before_any_service_change(self):
        status, _, record = self.execute(previous='native-inference-proxy:' + '9' * 32)
        self.assertEqual(status, 1)
        self.assertEqual(record['commands'], [])

    def test_stop_failure_cannot_restart_and_is_bounded_by_one_global_budget(self):
        status, recovery, record = self.execute(refuse_stop=True)
        self.assertEqual(status, 1)
        self.assertFalse(record['valid'])
        self.assertTrue(all(row['argv'][1] == 'stop' for row in record['commands']))
        self.assertIn('failure_cleanup_error', record)
        self.assertLessEqual(recovery.clock() - recovery.began, 120)
        self.assertTrue(all(row['finished_monotonic'] - row['started_monotonic'] <= 5 for row in record['commands']))

    def test_an_orphan_native_process_prevents_restart_even_after_units_are_dead(self):
        status, _, record = self.execute(orphan=True)
        self.assertEqual(status, 1)
        self.assertFalse(any(row['argv'][1] == 'start' for row in record['commands']))

    def test_restart_failure_stops_the_dedicated_services_once_more_without_retrying_start(self):
        status, _, record = self.execute(fail_start=True)
        self.assertEqual(status, 1)
        self.assertEqual([row['argv'][1] for row in record['commands']], ['stop', 'start', 'stop'])
        RECOVERY.assert_terminated(record['failure_cleanup'])
        self.assertFalse(record['valid'])

    def test_check_only_never_changes_services_and_cannot_be_used_as_a_reset_certificate(self):
        status, _, record = self.execute(reset=False)
        self.assertEqual(status, 0)
        self.assertEqual(record['status'], 'checked')
        self.assertEqual(record['commands'], [])
        with self.assertRaises(RECOVERY.RecoveryFailure): RECOVERY.validate_proof(record)

    def test_guard_failure_never_stops_or_starts_any_service(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery = FakeRecovery(Path(directory) / 'proof.json')
            with patch.object(recovery, 'environment', side_effect=RECOVERY.RecoveryFailure('wrong instance')):
                self.assertEqual(recovery.execute(), 1)
            self.assertEqual(recovery.record['commands'], [])

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'proof.json'; path.write_text('preserved evidence')
            with patch.object(RECOVERY.sys, 'argv', ['recovery', '--check-only', '--out', str(path)]), \
                    patch.object(RECOVERY, 'Recovery') as constructor:
                with self.assertRaises(FileExistsError): RECOVERY.main()
            constructor.assert_not_called()
            self.assertEqual(path.read_text(), 'preserved evidence')


class OfflineProofTests(unittest.TestCase):
    def test_offline_validation_rechecks_evidence_instead_of_trusting_valid_true(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery = FakeRecovery(Path(directory) / 'proof.json')
            self.assertEqual(recovery.execute(), 0)
            good = recovery.record
            alterations = [
                lambda value: value['termination_proof']['units'][RECOVERY.SERVICES[0]].update(MainPID='42'),
                lambda value: value['termination_proof']['cgroups'][RECOVERY.SERVICES[0]].update(exists=True, populated=1),
                lambda value: value['termination_proof'].update(known_processes=[{'pid': 42}]),
                lambda value: value['termination_proof'].update(listeners=['127.0.0.1:11435']),
                lambda value: value['post_proxy_state'].update(uncertain_total=1),
                lambda value: value['post_proxy_state'].update(clock_domain=OLD_EPOCH),
                lambda value: value.update(post_native_model_state={'models': [{'name': 'already-loaded'}]}),
                lambda value: value.update(finished_monotonic=value['started_monotonic'] + 121),
                lambda value: value['commands'][1].update(started_monotonic=value['termination_proof']['monotonic'] - 1),
            ]
            for change in alterations:
                changed = copy.deepcopy(good); change(changed)
                with self.subTest(change=change), self.assertRaises(RECOVERY.RecoveryFailure):
                    RECOVERY.validate_proof(changed)

    def test_native_and_orphan_process_detection_does_not_archive_unrelated_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            rows = [('1', 'ollama', '/opt/ollama/bin/ollama', b'ollama\0serve\0'),
                    ('2', 'runner', '/tmp/runner', b'runner\0'),
                    ('3', 'python3', '/usr/bin/python3', b'python3\0-m\0v2.inference_proxy\0'),
                    ('4', 'unrelated', '/usr/bin/unrelated', b'unrelated\0private-argument\0')]
            for pid, comm, exe, arguments in rows:
                path = proc / pid; path.mkdir()
                (path / 'comm').write_text(comm); (path / 'cmdline').write_bytes(arguments)
                (path / 'cgroup').write_text('0::/system.slice/unrelated.service\n')
                (path / 'exe').symlink_to(exe)
            known, unavailable = RECOVERY.known_processes(proc)
            self.assertEqual({row['pid'] for row in known}, {1, 2, 3})
            self.assertEqual(unavailable, [])
            self.assertNotIn('private-argument', json.dumps(known))


if __name__ == '__main__': unittest.main()
