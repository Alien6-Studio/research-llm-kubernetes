"""CPU freeze fixtures are synthetic and never start a server or measurement."""
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import MagicMock, patch

from scripts import freeze_cpu_campaign as freezer
from v2 import test_cpu_replay as cpu_fixtures
from v2 import verify


class CPUFreezeCommandTests(unittest.TestCase):
    def setUp(self):
        fixture = cpu_fixtures.CPUCampaignReplayTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.pilot = fixture.root, fixture.pilot
        self.prefix, self.campaign = fixture.prefix, fixture.campaign
        shutil.rmtree(self.root / 'archives' / (self.prefix + '-pilot'))
        shutil.rmtree(self.root / self.campaign['trace_directory'])
        (self.root / self.campaign['plan_path']).unlink()
        self.raw_pilot = self.root / 'runs' / (self.prefix + '-pilot') / 'pilot.json'
        self.raw_pilot.parent.mkdir(parents=True)
        self.raw_pilot.write_text(json.dumps(self.pilot))
        self.method = self.root / 'infra/synthetic-guard.py'
        self.method.parent.mkdir()
        self.method.write_text('# Synthetic guard source, not executed\n')
        self.snapshot = MagicMock(source_id=self.pilot['source_id'], git_base=self.pilot['git_base'])
        self.snapshot.metadata.return_value = {'source_id': self.snapshot.source_id, 'git_base': self.snapshot.git_base}
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(freezer, 'ROOT', self.root))
        stack.enter_context(patch.object(verify, 'ROOT', self.root))
        stack.enter_context(patch.object(freezer, 'capture', return_value=self.snapshot))
        stack.enter_context(patch.object(freezer, 'verify_worktree'))
        clock = stack.enter_context(patch.object(freezer, 'datetime'))
        clock.now.return_value = datetime.fromisoformat('2026-11-13T12:00:00+00:00')

    def freeze(self):
        return freezer.freeze_campaign(self.raw_pilot, Path(self.campaign['plan_path']), [self.method], cohort_prefix=self.prefix)

    def test_one_command_preserves_pilot_freezes_window_and_publishes_replayable_flat_plan(self):
        result = self.freeze()
        output = self.root / self.campaign['plan_path']
        self.assertEqual(result['measurement_seconds'], 35)
        self.assertEqual(result['qualification_trials'], 12)
        self.assertEqual(result['real_model_trials'], 45)
        self.assertEqual(result['plan_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        plan = json.loads(output.read_text())
        self.assertEqual(plan['pilot_sha256'], hashlib.sha256(self.raw_pilot.read_bytes()).hexdigest())
        self.assertEqual(plan['continuation']['method_sources'][0]['path'], 'infra/synthetic-guard.py')
        self.assertEqual(output.stat().st_mode & 0o222, 0)
        self.assertTrue(freezer.check_campaign(Path(self.campaign['plan_path']))['checked'])
        preserved = output.read_bytes()
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertEqual(output.read_bytes(), preserved)

    def test_method_drift_is_rejected_by_offline_check(self):
        self.freeze()
        self.method.write_text('# Changed guard source\n')
        with self.assertRaisesRegex(ValueError, 'method source differs'):
            freezer.check_campaign(Path(self.campaign['plan_path']))

    def test_wrong_pilot_source_stops_before_creating_any_archives_or_traces(self):
        self.pilot['source_id'] = 'sha256:' + '0' * 64
        self.raw_pilot.write_text(json.dumps(self.pilot))
        with self.assertRaisesRegex(ValueError, 'registered collection source'):
            self.freeze()
        self.assertFalse((self.root / self.campaign['trace_directory']).exists())
        self.assertFalse((self.root / 'archives' / (self.prefix + '-pilot')).exists())

    def test_unknown_output_path_and_duplicate_methods_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'registered flat CPU plan'):
            freezer.freeze_campaign(self.raw_pilot, Path('protocol/unregistered-plan.json'), cohort_prefix=self.prefix)
        with self.assertRaisesRegex(ValueError, 'Duplicate campaign method'):
            freezer.freeze_campaign(self.raw_pilot, Path(self.campaign['plan_path']), [self.method, self.method], cohort_prefix=self.prefix)

    def test_infeasible_window_archives_pilot_but_launches_no_trace_or_flat_plan(self):
        for setting, elapsed in zip(self.pilot['results'], (128., 64., 50.), strict=True):
            setting['elapsed_seconds'] = elapsed
            setting['completion_rate'] = 16 / elapsed
        self.raw_pilot.write_text(json.dumps(self.pilot))
        with self.assertRaises(freezer.CPUPlanningInfeasible) as caught:
            self.freeze()
        self.assertEqual(caught.exception.record['required_measurement_seconds'], 125)
        self.assertFalse((self.root / self.campaign['trace_directory']).exists())
        self.assertFalse((self.root / self.campaign['plan_path']).exists())
        self.assertTrue((self.root / 'archives' / (self.prefix + '-pilot') / 'pilot.json.gz').is_file())
        decision = json.loads((self.root / 'runs' / (self.prefix + '-planning') / 'planning.json').read_text())
        self.assertFalse(decision['feasible'])
        self.assertEqual(decision['comparison_trials_launched'], 0)

    def test_registered_feasibility_cap_accepts_120_and_rejects_longer_windows(self):
        self.assertEqual(verify.validate_cpu_window(120), 120)
        with self.assertRaisesRegex(ValueError, '120-second feasibility cap'):
            verify.validate_cpu_window(121)

    def test_terminal_attempts_cannot_be_reused_for_new_freeze(self):
        for prefix in (verify.CPU_COHORT, 'v2-ollama-fra6s004-cpu', 'v2-ollama-fra6s004-cpu-confirmation'):
            campaign = freezer.get_cpu_campaign(prefix)
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, 'terminal'):
                freezer.freeze_campaign(self.raw_pilot, Path(campaign['plan_path']), cohort_prefix=prefix)


if __name__ == '__main__':
    unittest.main()
