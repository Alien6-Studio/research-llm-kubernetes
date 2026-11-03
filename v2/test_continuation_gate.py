"""Synthetic gate fixtures exercise exclusion; they are not collected evidence."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v2 import verify
from v2.model import pilot_item
from v2.remote_plan import derive
from v2.workload import DIGEST, MODEL, OPTIONS, model_request


class ContinuationGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix = verify.PLAIN_COHORT + '-restart'
        self.plan = derive('172.18.0.1')
        self.identity = {'source_id': 'sha256:' + '1' * 64, 'git_base': '2' * 40,
                         'source_commit': '2' * 40}
        self.plan['continuation'] = {'cohort': 'ollama-isolated-plain', **self.identity,
                                     'frozen_utc': '2026-11-13T12:00:00+00:00',
                                     'transport_control': 'http://172.18.0.1:11435/transport-control',
                                     'ssh_compression': False}
        path = self.root / verify.PLAIN_PLAN
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.plan))
        self.plan_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.archives = {}
        network = {**self.identity, 'utc': '2026-11-13T12:01:00+00:00',
                   'endpoint': self.plan['continuation']['transport_control'], 'qualified': True,
                   'trials': [{'repetition': index, 'passed': True,
                               'requests': [{'clock_domain': 'client-vm', 'launched': 1. + index,
                                             'finished': 1.1 + index}]} for index in range(3)]}
        self.archives[self.prefix + '-network'] = [network]
        observations = []
        for index in range(4):
            item = pilot_item(index)
            label = 'ok' if item['task_status'] == 200 else 'error'
            observations.append({'item': item, 'status': 200, 'raw': {'response': json.dumps({'label': label})},
                                 'correct': True, 'elapsed': .1})
        warmup = {**self.identity, 'utc': '2026-11-13T12:04:00+00:00', 'valid': True,
                  'metadata': {'version': {'version': '0.40.16'}, 'model': {'name': MODEL, 'digest': DIGEST},
                               'options': dict(OPTIONS)}, 'observations': observations,
                  'loaded_model': {'models': [{'name': MODEL, 'digest': DIGEST, 'context_length': OPTIONS['num_ctx']}]}}
        self.archives[self.prefix + '-warmup'] = [warmup]
        for stage, block_name, size, utc, start in (
                ('qualification', 'model-qualification', 12, '2026-11-13T12:02:00+00:00', 100.),
                ('real-model', 'real-model', 45, '2026-11-13T12:05:00+00:00', 200.)):
            manifest = {**self.identity, 'utc': utc, 'plan_sha256': self.plan_hash,
                        'model_controller_sha256': '3' * 64}
            block = {'complete': True, 'qualified': True, 'trials': size, 'invalid': []}
            trials = []
            for index, config in enumerate(self.plan['blocks'][block_name]):
                trials.append({'configuration': copy.deepcopy(config), 'valid': True, 'issues': [],
                               'result': {'requests': [{'clock_domain': 'client-vm', 'launched': 10. + index,
                                                        'finished': 10.1 + index}]},
                               'inference_observer_metadata': {**self.identity, 'clock_domain': 'native-monitor',
                                   'started': start + index, 'finished': start + index + .1}})
            self.archives[self.prefix + '-' + stage] = [manifest, block, *trials]

    def data(self, stage):
        return self.archives[self.prefix + '-' + stage]

    def audit(self, *, network=True, trial_issues=()):
        with patch.object(verify, 'ROOT', self.root), \
                patch.object(verify, 'replay_network_control', return_value=network), \
                patch.object(verify, 'replay_continuation_trial_gate', return_value=list(trial_issues)):
            return verify.audit_plain_continuation(self.archives, prefix=self.prefix)

    def switch_to_stdio(self):
        previous = self.prefix
        self.prefix = verify.STDIO_COHORT
        self.archives = {self.prefix + name.removeprefix(previous): value for name, value in self.archives.items()}
        self.plan['continuation']['cohort'] = 'ollama-isolated-stdio'
        path = self.root / verify.STDIO_PLAN
        path.write_text(json.dumps(self.plan))
        self.plan_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        for stage in ('qualification', 'real-model'):
            self.data(stage)[0]['plan_sha256'] = self.plan_hash

    def test_complete_prerequisites_and_45_trials_are_eligible(self):
        result = self.audit()
        self.assertTrue(result['eligible'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['qualification_trials'], 12)
        self.assertEqual(result['warmup_observations'], 4)
        self.assertEqual(result['real_model_trials'], 45)
        self.assertIn('Network has no UTC finish anchor', result['chronology']['unavailable'])

    def test_historical_real_blocks_do_not_activate_the_new_audit(self):
        self.assertIsNone(verify.audit_plain_continuation({'v2-local-real-model': [{'qualified': True}]}))

    def test_qualified_real_block_requires_all_three_passing_network_repetitions(self):
        self.data('network')[0]['trials'].pop()
        with self.assertRaisesRegex(ValueError, 'network_prerequisite_not_qualified'):
            self.audit()

    def test_replayed_network_failures_cannot_be_hidden_by_passed_flags(self):
        with self.assertRaisesRegex(ValueError, 'network_prerequisite_not_qualified'):
            self.audit(network=False)

    def test_missing_or_duplicate_prerequisite_documents_cannot_qualify(self):
        self.archives.pop(self.prefix + '-network')
        with self.assertRaisesRegex(ValueError, 'network_control_missing_or_duplicate'):
            self.audit()

    def test_source_or_plan_hash_mismatch_cannot_qualify(self):
        for stage, field, value in [('network', 'source_id', 'sha256:' + '9' * 64),
                                    ('warmup', 'git_base', '9' * 40),
                                    ('qualification', 'plan_sha256', '9' * 64),
                                    ('real-model', 'plan_sha256', '9' * 64)]:
            data = self.data(stage)[0]
            before = data[field]
            data[field] = value
            with self.subTest(stage=stage, field=field), self.assertRaisesRegex(ValueError, 'mismatch'):
                self.audit()
            data[field] = before

    def test_control_and_real_grids_must_be_complete_and_unique(self):
        for stage in ('qualification', 'real-model'):
            documents = self.data(stage)
            removed = documents.pop()
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, 'incomplete_or_unqualified'):
                self.audit()
            documents.append(removed)
        self.data('real-model')[-1] = copy.deepcopy(self.data('real-model')[-2])
        with self.assertRaisesRegex(ValueError, 'incomplete_or_unqualified'):
            self.audit()

    def test_configurations_cannot_drift_from_the_frozen_plan(self):
        self.data('qualification')[2]['configuration']['deadline_seconds'] += 1
        with self.assertRaisesRegex(ValueError, 'trial_configuration_mismatch'):
            self.audit()

    def test_replayed_trial_failure_cannot_be_hidden_by_valid_flags(self):
        with self.assertRaisesRegex(ValueError, 'validity_claim_not_reproduced'):
            self.audit(trial_issues=['emission_lag_p95_over_50ms'])

    def test_partial_or_failed_blocks_remain_inspectable_and_excluded(self):
        self.data('network')[0]['qualified'] = False
        self.data('network')[0]['trials'][0]['passed'] = False
        documents = self.data('real-model')
        documents[1].update(complete=False, qualified=False, trials=1, invalid=[])
        del documents[3:]
        result = self.audit(network=False)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['claimed_qualified'])
        self.assertEqual(result['real_model_trials'], 1)
        self.assertIn('network_prerequisite_not_qualified', result['issues'])

    def test_failed_network_only_attempt_is_retained_without_a_real_archive(self):
        network = self.data('network')[0]
        network['qualified'] = False
        network['trials'][0]['passed'] = False
        self.archives = {self.prefix + '-network': [network]}
        result = self.audit(network=False)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['claimed_qualified'])
        self.assertEqual(result['network_repetitions'], 3)
        self.assertEqual(result['real_model_trials'], 0)
        self.assertIn('network_prerequisite_not_qualified', result['issues'])

    def test_failed_warmup_is_excluded_without_inventing_missing_answers(self):
        self.data('real-model')[1]['qualified'] = False
        warmup = self.data('warmup')[0]
        warmup.update(valid=False, observations=warmup['observations'][:1])
        warmup['observations'][0].update(status=0, raw={'transport_error': 'TimeoutError'}, correct=False)
        result = self.audit()
        self.assertEqual(result['warmup_observations'], 1)
        self.assertFalse(result['eligible'])
        self.assertIn('warmup_prerequisite_not_qualified', result['issues'])

    def test_utc_and_minimum_warmup_finish_order_are_checked(self):
        self.data('warmup')[0]['utc'] = '2026-11-13T12:04:59.900000+00:00'
        with self.assertRaisesRegex(ValueError, 'warmup_duration_overlaps_real_model_start'):
            self.audit()
        self.data('warmup')[0]['utc'] = '2026-11-13T12:06:00+00:00'
        with self.assertRaisesRegex(ValueError, 'warmup_before_real_model_utc_order_violation'):
            self.audit()

    def test_missing_or_naive_recorded_utc_cannot_be_silently_erased(self):
        self.data('qualification')[0]['utc'] = '2026-11-13T12:02:00'
        with self.assertRaisesRegex(ValueError, 'qualification_start_utc_invalid'):
            self.audit()
        del self.data('qualification')[0]['utc']
        with self.assertRaisesRegex(ValueError, 'qualification_start_utc_missing'):
            self.audit()

    def test_same_domain_finish_after_next_stage_start_cannot_qualify(self):
        self.data('network')[0]['trials'][0]['requests'][0]['finished'] = 99.
        with self.assertRaisesRegex(ValueError, 'network_finished_before_qualification_requests_monotonic_order_violation'):
            self.audit()

    def test_native_monitor_intervals_are_ordered_only_in_the_same_domain(self):
        self.data('real-model')[2]['inference_observer_metadata']['started'] = 110.
        with self.assertRaisesRegex(ValueError, 'qualification_monitor_finished_before_real_model_monitor_monotonic_order_violation'):
            self.audit()
        for trial in self.data('real-model')[2:]:
            trial['inference_observer_metadata']['clock_domain'] = 'another-native-monitor'
        result = self.audit()
        self.assertTrue(result['eligible'])
        self.assertTrue(any('no shared explicit monotonic domain' in note for note in result['chronology']['unavailable']))

    def test_interrupted_first_attempt_is_retained_without_successful_prerequisites(self):
        documents = copy.deepcopy(self.data('real-model')[:31])
        documents[1].update(complete=False, qualified=False, trials=29,
                            reason='inference_metadata_transport_interrupted')
        original = {verify.PLAIN_COHORT + '-real-model': documents}
        result = verify.audit_plain_continuation(original)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['real_model_trials'], 29)
        self.assertEqual(result['network_repetitions'], 0)
        self.assertEqual(result['issues'], ['attempt_interrupted_before_completion'])

    def test_first_attempt_cannot_claim_qualified_even_with_a_complete_valid_subset(self):
        original = {verify.PLAIN_COHORT + '-real-model': self.data('real-model')}
        with self.assertRaisesRegex(ValueError, 'Interrupted continuation attempt'):
            verify.audit_plain_continuation(original)

    def test_restart_cannot_borrow_prerequisites_from_the_first_attempt(self):
        for suffix in ('network', 'qualification', 'warmup'):
            self.archives[verify.PLAIN_COHORT + '-' + suffix] = self.archives.pop(self.prefix + '-' + suffix)
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.audit()

    def test_plural_audit_preserves_excluded_and_qualified_attempts_separately(self):
        documents = copy.deepcopy(self.data('real-model')[:31])
        documents[1].update(complete=False, qualified=False, trials=29)
        self.archives[verify.PLAIN_COHORT + '-real-model'] = documents
        with patch.object(verify, 'ROOT', self.root), \
                patch.object(verify, 'replay_network_control', return_value=True), \
                patch.object(verify, 'replay_continuation_trial_gate', return_value=[]):
            result = verify.audit_plain_continuations(self.archives)
        self.assertFalse(result[verify.PLAIN_COHORT]['eligible'])
        self.assertTrue(result[self.prefix]['eligible'])

    def test_arbitrary_prefix_cannot_silently_reuse_the_registered_plan(self):
        with self.assertRaisesRegex(ValueError, 'explicitly registered'):
            verify.audit_plain_continuation({}, prefix=self.prefix + '-unregistered')

    def test_stdio_uses_its_own_frozen_plan_and_complete_prerequisites(self):
        self.switch_to_stdio()
        result = self.audit()
        self.assertEqual(result['cohort'], verify.STDIO_COHORT)
        self.assertTrue(result['eligible'])
        self.data('real-model')[0]['plan_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'real-model_plan_hash_mismatch'):
            self.audit()

    def test_stdio_cannot_borrow_the_plain_restart_prerequisites(self):
        self.switch_to_stdio()
        for suffix in ('network', 'qualification', 'warmup'):
            self.archives[verify.PLAIN_COHORT + '-restart-' + suffix] = self.archives.pop(self.prefix + '-' + suffix)
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.audit()

    def test_failed_stdio_network_only_attempt_is_excluded(self):
        self.switch_to_stdio()
        network = self.data('network')[0]
        network['qualified'] = False
        self.archives = {self.prefix + '-network': [network]}
        result = self.audit(network=False)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['network_repetitions'], 3)
        self.assertEqual(result['real_model_trials'], 0)


class NetworkGateReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'protocol/v2-model').mkdir(parents=True)
        self.control = {'endpoint': 'http://local/transport-control', 'qualified': True, 'trials': []}
        for repetition in range(3):
            item = {**pilot_item(0), 'id': str(repetition), 'offset': 0}
            encoded = gzip.compress(json.dumps([item]).encode(), mtime=0)
            (self.root / f'protocol/v2-model/headroom-{repetition}.json.gz').write_bytes(encoded)
            body = json.dumps(model_request(item), separators=(',', ':')).encode()
            row = {'id': item['id'], 'scheduled': 1., 'emitted': 1.005, 'finished': 1.05,
                   'latency_seconds': .05, 'status': 200, 'transport_fixture_returned': True,
                   'request_body_sha256': hashlib.sha256(body).hexdigest(), 'request_body_bytes': len(body)}
            self.control['trials'].append({'repetition': repetition, 'trace_sha256': hashlib.sha256(encoded).hexdigest(),
                                          'requests': [row], 'emission_lag_p95_seconds': .005,
                                          'latency_p95_seconds': .05, 'passed': True})

    def replay(self):
        with patch.object(verify, 'ROOT', self.root):
            return verify.replay_network_control(self.control)

    def test_transport_gate_is_recomputed_from_archived_requests(self):
        self.assertTrue(self.replay())
        trial = self.control['trials'][0]
        trial['requests'][0].update(finished=1.2, latency_seconds=.2)
        trial['latency_p95_seconds'] = .2
        with self.assertRaises(AssertionError):
            self.replay()

    def test_duplicate_request_cannot_preserve_a_passing_trace_set(self):
        self.control['trials'][0]['requests'] *= 2
        with self.assertRaises(AssertionError):
            self.replay()


if __name__ == '__main__':
    unittest.main()
