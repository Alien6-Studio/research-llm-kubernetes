"""Recovery cannot promote a censored native outcome to a valid experiment."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from v2 import test_native_completion as fixtures
from v2.model import expected_native_calls

SPEC = importlib.util.spec_from_file_location('continuation_supervisor',
    Path(__file__).resolve().parents[1] / 'infra/run-fra6s004-continuation.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ContinuationSupervisorTests(unittest.TestCase):
    def document(self):
        config = fixtures.config()
        result = {'requests': [fixtures.request_row()], 'drained': True}
        uncertain = fixtures.terminal()
        uncertain.update(status=None, done=False, response=None, error='TimeoutError', uncertain=True)
        state = fixtures.state(8, 8, records=[uncertain])
        state['uncertain_total'] = 1
        return {'configuration': config, 'result': result, 'valid': False,
                'issues': ['native_completion_not_certified'],
                'native_completion_certificate': {'schema': 'alien6-native-completion-v1',
                    'valid': False, 'pre_state': fixtures.state(), 'driver_began': 0,
                    'driver_deadline': 900, 'driver_finished': 200, 'budget_seconds': 900,
                    'summary_polls': [{'driver_at': 190, 'state': state}],
                    'expected': expected_native_calls(config, result), 'terminal_state': state,
                    'issues': [{'type': 'ValueError', 'detail': 'Native proxy recorded an uncertain terminal outcome'}]}}

    def classify(self, document, issues=()):
        with patch.object(MODULE, 'replay_continuation_trial_gate', return_value=list(issues)):
            return MODULE.classify_trial(document, fixtures.IDENTITY, 'fixture-controller')

    def test_native_timeout_is_retained_as_incomplete_without_changing_original_validity(self):
        doc = self.document()
        original = copy.deepcopy(doc)
        outcome = self.classify(doc)
        self.assertEqual(outcome['classification'], 'native_completion_incomplete')
        self.assertFalse(outcome['strict_trial_valid'])
        self.assertTrue(outcome['client_integrity_valid'])
        self.assertEqual(outcome['uncertain_terminal_calls'], 1)
        self.assertEqual(doc, original)

    def test_generator_or_capture_defects_stop_instead_of_becoming_overload(self):
        with self.assertRaisesRegex(ValueError, 'integrity failed'):
            self.classify(self.document(), ['emission_lag_p95_over_50ms'])
        doc = self.document()
        doc['result']['drained'] = False
        with self.assertRaises(ValueError):
            self.classify(doc)

    def test_missing_extra_changed_or_duplicate_native_calls_stop_collection(self):
        for kind in ('missing', 'extra', 'hash', 'epoch', 'counter', 'false_validity'):
            doc = self.document()
            terminal = doc['native_completion_certificate']['terminal_state']
            if kind == 'missing': terminal['records'].clear()
            elif kind == 'extra': terminal['records'].append(copy.deepcopy(terminal['records'][0]))
            elif kind == 'hash': terminal['records'][0]['request_sha256'] = '0' * 64
            elif kind == 'epoch': terminal['clock_domain'] = 'another-process'
            elif kind == 'counter': terminal['uncertain_total'] = 0
            else: doc['valid'] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.classify(doc)

    def test_unknown_native_failure_is_not_automatically_skipped(self):
        doc = self.document()
        doc['native_completion_certificate']['issues'][0]['detail'] = 'Native proxy observed extra unaccounted calls'
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            self.classify(doc)

    def test_28_trials_and_fresh_controls_fit_one_guard_with_shutdown_reserve(self):
        budget = MODULE.prospective_budget(28)
        self.assertLess(budget + MODULE.RESERVE, 86400)
        self.assertGreater(budget, 28 * 900)
        self.assertEqual(MODULE.CAPS['recovery'], 150)
        for invalid in (0, 29, True, 28.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                MODULE.prospective_budget(invalid)

    def test_failed_recovery_prevents_warmup_and_trial(self):
        obj = MODULE.Continuation.__new__(MODULE.Continuation)
        obj.previous_epoch = 'previous'
        obj.phase = Mock(side_effect=RuntimeError('cgroup still populated'))
        obj.capture = Mock()
        obj.write_vm = Mock()
        with self.assertRaisesRegex(RuntimeError, 'cgroup still populated'):
            obj.recovery('recovery-fixture', 'runs/fixture/recovery.json')
        self.assertEqual(obj.capture.call_count, 1)  # Only the output directory, never a restart retry.
        obj.write_vm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
