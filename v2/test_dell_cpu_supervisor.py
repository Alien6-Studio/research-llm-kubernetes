"""Supervisor contracts without starting services, instances or model requests."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("cpu_supervisor", Path(__file__).resolve().parents[1] / "infra/run-dell-cpu-campaign.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

STATE = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
         "Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "10"}


def completed(state=None, *, returncode=0, stdout=None, stderr=""):
    if stdout is None:
        stdout = "".join(key + "=" + value + "\n" for key, value in (state or STATE).items())
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class SupervisorContractTests(unittest.TestCase):
    def test_all_remote_python_programs_compile_without_executing_them(self):
        for name in ("SOURCE_CHECK", "CALIBRATION_CHECK", "PREFLIGHT", "CHECK_PHASE", "PARTIAL_BLOCK"):
            with self.subTest(name=name):
                compile(getattr(MODULE, name), name, "exec")

    def test_collection_sequence_is_complete_without_retry_or_pilot_reselection(self):
        campaign = MODULE.get_cpu_campaign(MODULE.DEFAULT_PREFIX)
        phases = MODULE.phase_commands("synthetic:test-image", campaign)
        self.assertEqual([name for name, _ in phases], ["calibration", "freeze", "network", "qualification", "warmup", "real-model"])
        self.assertEqual(phases[0][1], ["python3", "-c", MODULE.CALIBRATION_CHECK, campaign['prefix']])
        self.assertFalse(any(command[:5] == ['python3', '-m', 'v2.model', 'pilot', '--out'] for _, command in phases))
        self.assertIn("scripts/freeze_cpu_campaign.py", phases[1][1])
        self.assertIn("--plan", phases[2][1])
        self.assertIn("model-qualification", phases[3][1])
        self.assertIn("real-model", phases[5][1])
        self.assertIn('http://172.30.64.3:11437/transport-control', phases[2][1])
        self.assertIn('--cohort-prefix',phases[1][1])
        self.assertIn('v2-ollama-fra6s004-cpu-long-window', phases[1][1])
        pilot_index = phases[1][1].index('--pilot') + 1
        self.assertEqual(phases[1][1][pilot_index], campaign['retained_calibration_pilot'])
        self.assertIn('v2/cpu_campaigns.py',phases[1][1])
        self.assertIn('v2/calibration.py',phases[1][1])
        self.assertIn('v2/verify.py',phases[1][1])
        self.assertTrue(all('172.16.0.244' not in word for _,command in phases for word in command))
        self.assertFalse(any("ssh" in word for _, command in phases for word in command))

    def test_lxd_target_and_private_endpoints_are_explicit(self):
        campaign = MODULE.get_cpu_campaign(MODULE.DEFAULT_PREFIX)
        command = MODULE.lxd_command(MODULE.CLIENT, "/opt/fixture", ["python3", "-V"], campaign)
        self.assertEqual(command[:3], ["lxc", "exec", "local:a6-capacity-v2"])
        self.assertIn("STUDY_INFERENCE_ORIGIN=http://172.30.64.3:11437", command)
        with self.assertRaises(ValueError):
            MODULE.lxd_command("unrelated-instance", "/opt/fixture", ["true"], campaign)

    def test_stale_inactive_or_wrong_host_guard_cannot_authorize_collection(self):
        data = {"status": "running", "complete": False, "guard_action": None,
                "metadata": {"instance": MODULE.INFERENCE, "boot_id": "fixture-boot", "host": "fra6s004.example.test", "memory_floor_bytes": 16 * 1024 ** 3, "interval_seconds": 5, "started_monotonic": 0, "max_duration_seconds": 21600},
                "samples": [{"monotonic": 100}]}
        campaign = MODULE.get_cpu_campaign(MODULE.DEFAULT_PREFIX)
        MODULE.validate_guard(data, 105, "fixture-boot",campaign)
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data, 121, "fixture-boot",campaign)
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data, 105, "different-boot",campaign)
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data, 105, "fixture-boot",campaign,required_seconds=21600)
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data,105,'fixture-boot',campaign,expected_script_sha256='different-source')
        data['metadata']['memory_floor_bytes']=8*1024**3
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data,105,'fixture-boot',campaign)
        data['metadata']['memory_floor_bytes']=16*1024**3
        data["status"] = "observer_failed"
        with self.assertRaises(MODULE.GateFailure): MODULE.validate_guard(data, 105, "fixture-boot",campaign)

    def test_terminal_attempt_cannot_create_a_new_supervisor(self):
        for prefix in ('v2-ollama-dell-cpu', 'v2-ollama-fra6s004-cpu'):
            with self.subTest(prefix=prefix), self.assertRaises(MODULE.GateFailure):
                MODULE.Supervisor(SimpleNamespace(cohort_prefix=prefix))

    def test_comparison_lifetime_is_checked_against_the_prospective_window(self):
        self.assertEqual(MODULE.comparison_budget_seconds(335),79554)
        self.assertGreater(MODULE.comparison_budget_seconds(120),MODULE.comparison_budget_seconds(20))
        budgets = MODULE.phase_budgets(335)
        total = sum(budgets[name] for name, _ in MODULE.phase_commands('fixture', MODULE.get_cpu_campaign(MODULE.DEFAULT_PREFIX)))
        self.assertEqual(total,80154)
        self.assertLess(total + MODULE.SHUTDOWN_MARGIN_SECONDS, MODULE.MAX_DURATION_SECONDS)
        self.assertGreaterEqual(budgets['qualification'],12 * 900)
        self.assertGreaterEqual(budgets['real-model'],45 * 900)
        with self.assertRaises(MODULE.GateFailure): MODULE.comparison_budget_seconds(336)

    def test_guard_reserves_shutdown_time_without_borrowing_the_expired_observer_lifetime(self):
        campaign = MODULE.get_cpu_campaign(MODULE.DEFAULT_PREFIX)
        required = MODULE.comparison_budget_seconds(335)
        now = 1000
        data = {'status':'running','complete':False,'guard_action':None,
                'metadata':{'instance':MODULE.INFERENCE,'boot_id':'same-boot','host':'fra6s004',
                            'memory_floor_bytes':16*1024**3,'interval_seconds':5,
                            'started_monotonic':0,'max_duration_seconds':now+required+900},
                'samples':[{'monotonic':now}]}
        self.assertEqual(MODULE.validate_guard(data,now,'same-boot',campaign,required),now+required+900)
        data['metadata']['max_duration_seconds'] -= 1
        with self.assertRaisesRegex(MODULE.GateFailure,'shutdown margin'):
            MODULE.validate_guard(data,now,'same-boot',campaign,required)

    def test_guard_caps_global_deadline_with_one_shutdown_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            required = 100
            now = 1000
            expires = now + required + MODULE.SHUTDOWN_MARGIN_SECONDS
            supervisor.deadline = 10000
            supervisor.budget_until = now + required
            supervisor.args.guard_status.write_text('{}')
            with patch.object(MODULE.time,'monotonic',return_value=now), \
                    patch.object(MODULE,'validate_guard',return_value=expires):
                supervisor.guard()
            self.assertEqual(supervisor.deadline,now+required)

    def test_a_failed_or_never_started_unit_is_not_treated_as_finished_successfully(self):
        state = {"ActiveState": "activating"}
        self.assertFalse(MODULE.phase_finished(state))
        state = {"ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "10"}
        self.assertTrue(MODULE.phase_finished(state))
        state.update(ActiveState="active", SubState="exited")
        self.assertTrue(MODULE.phase_finished(state))
        state["ExecMainStartTimestampMonotonic"] = "0"
        with self.assertRaises(MODULE.GateFailure): MODULE.phase_finished(state)
        state.update(ActiveState="failed", ExecMainStartTimestampMonotonic="10", Result="exit-code")
        with self.assertRaises(MODULE.GateFailure): MODULE.phase_finished(state)

    def test_stdout_only_failed_inspection_is_retained_before_read_only_retry(self):
        command = ['lxc', 'exec', 'local:a6-capacity-v2', '--', 'systemctl', 'show', 'fixture.service']
        retained = []
        responses = [completed(returncode=23, stdout='exec transport detail\n'), completed()]
        with patch.object(MODULE.subprocess, 'run', side_effect=responses) as run, \
                patch.object(MODULE.time, 'sleep') as sleep, redirect_stderr(io.StringIO()) as log:
            self.assertEqual(MODULE.systemd_state(command, on_failure=retained.append), STATE)
        self.assertEqual(len(retained), 1)
        failure = retained[0]
        self.assertEqual(failure['argv'], command + ['--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestampMonotonic'])
        self.assertEqual(failure['returncode'], 23)
        self.assertEqual(failure['stdout'], 'exec transport detail\n')
        self.assertEqual(failure['stderr'], '')
        self.assertFalse(failure['timed_out'])
        self.assertEqual(json.loads(log.getvalue())['unit_inspection_failure'], failure)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs['timeout'] == 10 for call in run.call_args_list))
        self.assertTrue(all(call.args[0] == failure['argv'] for call in run.call_args_list))
        sleep.assert_called_once_with(1)

    def test_three_failed_inspections_abort_without_launch_or_unbounded_retry(self):
        failures = []
        with patch.object(MODULE.subprocess, 'run', return_value=completed(returncode=1, stdout='out', stderr='err')) as run, \
                patch.object(MODULE.time, 'sleep') as sleep, redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(MODULE.GateFailure, 'after three read-only attempts') as caught:
                MODULE.systemd_state(['systemctl', 'show', 'fixture.service'], on_failure=failures.append)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([row['attempt'] for row in failures], [1, 2, 3])
        self.assertTrue(all(row['stdout'] == 'out' and row['stderr'] == 'err' for row in failures))
        self.assertIn('"returncode": 1', str(caught.exception))
        self.assertTrue(all(call.args[0][:2] == ['systemctl', 'show'] for call in run.call_args_list))

    def test_timeout_preserves_partial_output_and_the_ten_second_bound(self):
        failure = subprocess.TimeoutExpired(['fixture'], 10, output=b'partial-out', stderr=b'partial-err')
        retained = []
        with patch.object(MODULE.subprocess, 'run', side_effect=[failure, completed()]), \
                patch.object(MODULE.time, 'sleep'), redirect_stderr(io.StringIO()):
            MODULE.systemd_state(['systemctl', 'show', 'fixture.service'], on_failure=retained.append)
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]['reason'], 'inspection_timeout')
        self.assertEqual(retained[0]['timeout_seconds'], 10)
        self.assertTrue(retained[0]['timed_out'])
        self.assertIsNone(retained[0]['returncode'])
        self.assertEqual(retained[0]['stdout'], 'partial-out')
        self.assertEqual(retained[0]['stderr'], 'partial-err')

    def test_successfully_observed_failed_phase_is_not_retried(self):
        state = {**STATE, 'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': '1'}
        with patch.object(MODULE.subprocess, 'run', return_value=completed(state)) as run, \
                patch.object(MODULE.time, 'sleep') as sleep:
            observed = MODULE.systemd_state(['systemctl', 'show', 'fixture.service'])
            with self.assertRaisesRegex(MODULE.GateFailure, 'Persistent phase failed'):
                MODULE.phase_finished(observed)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_inspection_failures_are_saved_before_the_next_read_and_survive_success(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            calls = 0
            def read(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return completed(returncode=1, stdout='first failure')
                checkpoint = json.loads(supervisor.checkpoint.read_text())
                self.assertEqual(checkpoint['unit_inspection_failures'][0]['stdout'], 'first failure')
                self.assertEqual(checkpoint['unit_inspection_failures'][0]['context'], {'phase': 'pilot'})
                return completed()
            with patch.object(MODULE.subprocess, 'run', side_effect=read), \
                    patch.object(MODULE.time, 'sleep'), redirect_stderr(io.StringIO()):
                self.assertEqual(supervisor.inspect_unit(['systemctl', 'show', 'fixture.service'], {'phase': 'pilot'}), STATE)
            supervisor.save()
            self.assertEqual(len(json.loads(supervisor.checkpoint.read_text())['unit_inspection_failures']), 1)

    def test_phase_launch_has_a_finite_source_declared_cap_and_no_retry_of_phase_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            supervisor.guard = Mock()
            supervisor.check_sources = Mock()
            supervisor.capture = Mock(return_value='')
            state = {**STATE, 'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': '1'}
            with patch.object(MODULE.subprocess, 'run', return_value=completed(state)) as run:
                with self.assertRaisesRegex(MODULE.GateFailure, 'Persistent phase failed'):
                    supervisor.run_phase('calibration', ['python3', '-c', MODULE.CALIBRATION_CHECK, MODULE.DEFAULT_PREFIX])
            launch = [call.args[0] for call in supervisor.capture.call_args_list if 'systemd-run' in call.args[0]]
            self.assertEqual(len(launch), 1)
            self.assertIn('--property=TimeoutStartSec=300', launch[0])
            self.assertIn('--property=TimeoutStopSec=60', launch[0])
            self.assertIn('--property=KillMode=control-group', launch[0])
            self.assertIn('--property=Type=oneshot', launch[0])
            self.assertIn('--property=RemainAfterExit=yes', launch[0])
            run.assert_called_once()
            self.assertEqual(supervisor.state['phases'][0]['status'], 'failed')

    def test_partial_block_program_writes_parseable_json_and_preserves_saved_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            block = root / 'runs' / (MODULE.DEFAULT_PREFIX + '-real-model')
            block.mkdir(parents=True)
            rows = [
                {'configuration': {'trial_id': 'trial-valid'}, 'result': {}, 'valid': True},
                {'configuration': {'trial_id': 'trial-invalid'}, 'result': {}, 'valid': False},
            ]
            for index, row in enumerate(rows):
                (block / f'trial-{index}.json').write_text(json.dumps(row))
            (block / 'manifest.json').write_text(json.dumps({'source_id': 'synthetic'}))
            before = {path.name: path.read_bytes() for path in block.iterdir()}
            program = 'import sys; sys.path.insert(0, ' + repr(str(MODULE.ROOT)) + ')\n' + MODULE.PARTIAL_BLOCK
            subprocess.run([MODULE.sys.executable, '-c', program, 'real-model', MODULE.DEFAULT_PREFIX],
                           cwd=root, capture_output=True, text=True, timeout=10, check=True)
            raw = (block / 'block.json').read_bytes()
            state = json.loads(raw)
            self.assertFalse(state['complete'])
            self.assertFalse(state['qualified'])
            self.assertEqual(state['trials'], 2)
            self.assertEqual(state['invalid'], ['trial-invalid'])
            self.assertTrue(raw.endswith(b'\n'))
            self.assertTrue(all((block / name).read_bytes() == encoded for name, encoded in before.items()))
            subprocess.run([MODULE.sys.executable, '-c', program, 'real-model', MODULE.DEFAULT_PREFIX],
                           cwd=root, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual((block / 'block.json').read_bytes(), raw)

    @staticmethod
    def make_supervisor(root):
        with patch.object(Path, 'read_text', return_value='fixture-boot'):
            return MODULE.Supervisor(SimpleNamespace(cohort_prefix=MODULE.DEFAULT_PREFIX, client_root=None,
                inference_root=None, max_duration_seconds=86400, state_dir=root / 'state',
                source_id='sha256:' + '1' * 64, git_base='2' * 40, image='fixture:test', guard_status=root / 'guard.json'))


if __name__ == "__main__": unittest.main()
