"""Terminal-certificate replay uses archived values and never makes HTTP calls."""
import copy
import unittest
from unittest.mock import patch

from v2 import model, verify
from v2 import test_native_completion as fixtures


class NativeCompletionReplayTests(unittest.TestCase):
    def setUp(self):
        self.config = fixtures.config()
        self.result = {'requests': [fixtures.request_row()]}
        clock = fixtures.Clock()
        responses = [fixtures.state(8, 8), fixtures.state(8, 8, records=[fixtures.terminal()])]
        with patch.object(model, 'json_http', side_effect=responses), \
                patch.object(model.time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(model.time, 'sleep', side_effect=clock.sleep):
            self.certificate = model.native_completion_certificate(self.config, self.result,
                fixtures.state(), fixtures.IDENTITY, 900, 0)
        self.assertTrue(self.certificate['valid'])

    def replay(self, certificate=None):
        with patch.object(model, 'json_http', side_effect=AssertionError('Offline replay must not perform HTTP')):
            return verify.replay_native_completion_certificate(self.config, self.result,
                certificate if certificate is not None else self.certificate, fixtures.IDENTITY)

    def test_complete_terminal_certificate_replays_without_replacing_a_late_client_outcome(self):
        result = copy.deepcopy(self.result)
        self.assertTrue(self.replay())
        self.assertEqual(self.result, result)
        self.assertFalse(self.result['requests'][0]['correct'])
        self.assertEqual(self.result['requests'][0]['status'], 0)

    def test_failed_or_absent_certificate_remains_excluded(self):
        self.assertFalse(self.replay({}))
        self.assertFalse(self.replay({**self.certificate, 'valid': False}))

    def test_missing_or_changed_terminal_payload_cannot_keep_a_successful_claim(self):
        for kind in ('missing', 'wrong_hash', 'duplicate', 'not_done', 'uncertain'):
            certificate = copy.deepcopy(self.certificate)
            records = certificate['terminal_state']['records']
            if kind == 'missing': records.clear()
            elif kind == 'wrong_hash': records[0]['request_sha256'] = '0' * 64
            elif kind == 'duplicate': records.append(copy.deepcopy(records[0]))
            elif kind == 'not_done': records[0]['response']['done'] = False
            else: records[0]['uncertain'] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.replay(certificate)

    def test_source_process_counter_or_clock_drift_is_rejected(self):
        for field, value in (('source_id', 'other-source'), ('clock_domain', 'other-process'),
                             ('finished_total', 6), ('uncertain_total', 1)):
            certificate = copy.deepcopy(self.certificate)
            certificate['terminal_state'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.replay(certificate)

    def test_expected_call_coverage_and_recorded_driver_budget_are_recomputed(self):
        for kind in ('expected', 'deadline', 'finished', 'poll_order'):
            certificate = copy.deepcopy(self.certificate)
            if kind == 'expected': certificate['expected']['count'] = 0
            elif kind == 'deadline': certificate['driver_deadline'] = 950
            elif kind == 'finished': certificate['driver_finished'] = 901
            else: certificate['summary_polls'][0]['driver_at'] = -1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.replay(certificate)


if __name__ == '__main__':
    unittest.main()
