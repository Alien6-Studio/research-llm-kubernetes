"""Standalone loading records must be replayed without a running model server."""
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
from v2.workload import DIGEST, MODEL, OPTIONS


class StandaloneWarmupReplayTests(unittest.TestCase):
    def complete(self):
        rows = []
        for index in range(4):
            item = pilot_item(index)
            label = 'ok' if item['task_status'] == 200 else 'error'
            rows.append({'item': item, 'status': 200, 'raw': {'response': json.dumps({'label': label})},
                         'correct': True, 'elapsed': index + .25})
        return {'metadata': {'version': {'version': '0.40.16'},
                             'model': {'name': MODEL, 'digest': DIGEST}, 'options': dict(OPTIONS)},
                'observations': rows, 'valid': True,
                'loaded_model': {'models': [{'name': MODEL, 'digest': DIGEST,
                                             'context_length': OPTIONS['num_ctx']}]}}

    def test_four_answers_replay_offline_and_return_the_actual_record_count(self):
        self.assertEqual(verify.verify_standalone_warmup(self.complete()), 4)

    def test_failed_attempt_and_interrupted_prefix_remain_invalid(self):
        for count in (0, 1, 3):
            data = self.complete()
            data.update(observations=data['observations'][:count], valid=False)
            del data['loaded_model']
            self.assertEqual(verify.verify_standalone_warmup(data), count)
        for status, raw in ((0, {'transport_error': 'TimeoutError'}), (500, {'error': 'unavailable'}),
                            (200, {'response': '{"label":"ok"}'})):
            data = self.complete()
            data.update(observations=data['observations'][:1], valid=False)
            del data['loaded_model']
            data['observations'][0].update(status=status, raw=raw, correct=False)
            self.assertEqual(verify.verify_standalone_warmup(data), 1)

    def test_correctness_is_recomputed_from_the_exact_raw_answer_and_status(self):
        for changes in ({'raw': {'response': '{"label":"ok"}'}}, {'status': 500}, {'correct': 1},
                        {'raw': {'response': '{"label":"error","extra":true}'}}):
            data = self.complete()
            data['observations'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'correctness'):
                verify.verify_standalone_warmup(data)

    def test_failed_attempt_must_still_retain_its_actual_raw_error(self):
        data = self.complete()
        data.update(observations=data['observations'][:1], valid=False)
        data['observations'][0].update(status=0, correct=False)
        del data['observations'][0]['raw']
        with self.assertRaises(KeyError):
            verify.verify_standalone_warmup(data)

    def test_four_successes_are_required_and_sufficient_for_the_validity_flag(self):
        for count, valid in ((3, True), (4, False), (4, 1)):
            data = self.complete()
            data.update(observations=data['observations'][:count], valid=valid)
            with self.subTest(count=count, valid=valid), self.assertRaisesRegex(ValueError, 'validity'):
                verify.verify_standalone_warmup(data)

    def test_inputs_cannot_be_replaced_or_reordered(self):
        for field, value in (('id', 'different-task'), ('task_status', 200), ('prompt_text', 'replacement')):
            data = self.complete()
            data['observations'][0]['item'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'frozen task'):
                verify.verify_standalone_warmup(data)
        data = self.complete()
        data['observations'].reverse()
        with self.assertRaisesRegex(ValueError, 'frozen task'):
            verify.verify_standalone_warmup(data)

    def test_warmup_cannot_have_more_than_four_attempts_or_continue_after_failure(self):
        data = self.complete()
        data['observations'].append(copy.deepcopy(data['observations'][0]))
        with self.assertRaisesRegex(ValueError, 'four attempted'):
            verify.verify_standalone_warmup(data)
        data = self.complete()
        data['observations'][0].update(status=0, raw={'transport_error': 'TimeoutError'}, correct=False)
        data['valid'] = False
        with self.assertRaisesRegex(ValueError, 'continued after'):
            verify.verify_standalone_warmup(data)

    def test_timings_and_status_values_must_be_well_formed(self):
        for elapsed in (-.1, float('nan'), float('inf'), True, '1'):
            data = self.complete()
            data['observations'][0]['elapsed'] = elapsed
            with self.subTest(elapsed=elapsed), self.assertRaisesRegex(ValueError, 'elapsed time'):
                verify.verify_standalone_warmup(data)
        for status in (True, '200', -1, 600):
            data = self.complete()
            data['observations'][0]['status'] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'HTTP status'):
                verify.verify_standalone_warmup(data)

    def test_pinned_metadata_cannot_silently_change(self):
        for section, field, value in (('version', 'version', 'different'), ('model', 'name', 'other'),
                                      ('model', 'digest', '0' * 64), ('options', 'num_ctx', 4096),
                                      ('options', 'temperature', .1)):
            data = self.complete()
            data['metadata'][section][field] = value
            with self.subTest(section=section, field=field), self.assertRaisesRegex(ValueError, 'pinned'):
                verify.verify_standalone_warmup(data)

    def test_valid_warmup_requires_the_actual_loaded_digest_and_context(self):
        for field, value in (('name', 'other'), ('digest', '0' * 64), ('context_length', 4096),
                             ('context_length', True), ('context_length', 8192.0)):
            data = self.complete()
            data['loaded_model']['models'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'Warmup loaded'):
                verify.verify_standalone_warmup(data)
        data = self.complete()
        del data['loaded_model']
        with self.assertRaises(KeyError):
            verify.verify_standalone_warmup(data)
        for models in ([], self.complete()['loaded_model']['models'] * 2):
            data = self.complete()
            data['loaded_model']['models'] = models
            with self.assertRaisesRegex(ValueError, 'single loaded model'):
                verify.verify_standalone_warmup(data)

    def test_archive_dispatch_counts_standalone_warmup_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            archive = root / 'archives/v2-standalone-warmup'
            archive.mkdir(parents=True)
            raw = json.dumps(self.complete()).encode()
            encoded = gzip.compress(raw, mtime=0)
            (archive / 'warmup.json.gz').write_bytes(encoded)
            entry = {'path': 'archives/v2-standalone-warmup/warmup.json.gz',
                     'sha256': hashlib.sha256(encoded).hexdigest(),
                     'decoded_sha256': hashlib.sha256(raw).hexdigest(), 'decoded_bytes': len(raw)}
            (archive / 'manifest.json').write_text(json.dumps({'files': [entry]}))
            (root / 'instruments').mkdir()
            (root / 'instruments/manifest.json').write_text('{"sources":{}}')
            (root / 'v2').mkdir()
            (root / 'v2/evidence-counts.json').write_text(json.dumps({
                'files': 1, 'trials': 0, 'model_predictions': 4,
                'blocks': {'archives/v2-standalone-warmup/manifest.json': 0}}))
            (root / 'analysis/v2-study').mkdir(parents=True)
            (root / 'analysis/v2-study/results.json').write_text('{"deadline_illustration":{}}')
            with patch.object(verify, 'ROOT', root), patch('v2.report.prepare', return_value={}), \
                    patch('v2.report.illustrate_deadline_loss', return_value={}):
                result = verify.verify()
            self.assertEqual(result['verified_model_predictions'], 4)
            self.assertEqual(result['verified_trials'], 0)


class DirectModelAnswerReplayTests(unittest.TestCase):
    def setUp(self):
        self.item = {'task_status': 200}
        self.raw = {'response': '{"label":"ok"}'}

    def test_timely_and_late_answers_replay_with_their_own_status(self):
        for status, expected in ((200, True), (500, False)):
            for late in (False, True):
                body = {'inference': self.raw, 'correct': expected}
                if late:
                    row = {'status': 0, 'correct': False, 'late_response': {**body, 'status': status}}
                else:
                    row = {'status': status, 'correct': expected, 'response': body}
                with self.subTest(status=status, late=late):
                    self.assertEqual(verify.verify_direct_model_answers(row, self.item), 1)

    def test_matching_label_in_http_error_cannot_be_misclassified_as_correct(self):
        row = {'status': 500, 'correct': True, 'response': {'inference': self.raw, 'correct': True}}
        with self.assertRaisesRegex(ValueError, 'HTTP status'):
            verify.verify_direct_model_answers(row, self.item)
        row = {'status': 0, 'correct': False,
               'late_response': {'status': 500, 'inference': self.raw, 'correct': True}}
        with self.assertRaisesRegex(ValueError, 'HTTP status'):
            verify.verify_direct_model_answers(row, self.item)

    def test_empty_or_null_generation_responses_are_counted_and_replayed(self):
        for inference in ({}, None):
            row = {'status': 200, 'correct': False, 'response': {'inference': inference, 'correct': False}}
            with self.subTest(inference=inference):
                self.assertEqual(verify.verify_direct_model_answers(row, self.item), 1)
        self.assertEqual(verify.verify_direct_model_answers({'status': 0, 'correct': False}, self.item), 0)

    def test_client_and_nested_answer_must_agree_for_a_timely_response(self):
        row = {'status': 200, 'correct': False, 'response': {'inference': self.raw, 'correct': True}}
        with self.assertRaisesRegex(ValueError, 'client correctness'):
            verify.verify_direct_model_answers(row, self.item)

    def test_one_generation_cannot_be_counted_as_both_timely_and_late(self):
        row = {'status': 200, 'correct': True, 'response': {'inference': self.raw, 'correct': True},
               'late_response': {'status': 200, 'inference': self.raw, 'correct': True}}
        with self.assertRaisesRegex(ValueError, 'both timely and late'):
            verify.verify_direct_model_answers(row, self.item)


if __name__ == '__main__':
    unittest.main()
