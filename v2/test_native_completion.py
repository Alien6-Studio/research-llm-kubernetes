"""Native completion is evidence; it does not rewrite client deadline outcomes."""
import ast
import asyncio
from contextlib import ExitStack
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from v2 import model
from v2.identity import verify_registered
from v2.run import useful
from v2.service import Application


IDENTITY = {'source_id': 'sha256:' + '1' * 64, 'git_base': '2' * 40, 'proxy_sha256': '3' * 64}


def item(identifier='sample', status=200):
    return {'id': identifier, 'offset': 0, 'tokens': 1000, 'service_seconds': 0,
            'task_status': status, 'prompt_text': 'Controlled note. '}


def config(items=None, direct=True):
    return {'trial_id': 'real-model-low-0-0', 'direct_ollama': direct, 'deadline_seconds': 10,
            'trace': items or [item()], 'provider': {'ollama_url': 'http://private:11437'}}


def request_row(identifier='sample', *, write=True):
    phases = {'clock_domain': 'controller', 'clock': 'time.monotonic', 'request_task_started': 1}
    if write:
        phases['write_started'] = 2
    return {'id': identifier, 'emitted': 3 if write else None, 'transport_phases': phases,
            'status': 0, 'correct': False, 'reason': 'client_deadline', 'latency_seconds': 10}


def state(started=7, finished=7, active=None, records=None):
    value = {**IDENTITY, 'clock_domain': 'proxy-one', 'clock': 'time.monotonic',
             'started_total': started, 'finished_total': finished, 'uncertain_total': 0,
             'active': active or []}
    if records is not None:
        value['records'] = records
    return value


def terminal(sequence=8, sample=None):
    return {'sequence': sequence, 'request_sha256': model.model_request_sha256(sample or item()),
            'started': 20, 'finished': 150, 'status': 200, 'done': True,
            'response': {'response': '{"label":"ok"}', 'done': True, 'eval_count': 6},
            'error': None, 'uncertain': False}


class Clock:
    def __init__(self): self.now = 10
    def monotonic(self): return self.now
    def sleep(self, delay): self.now += delay


class NativeCallCoverageTests(unittest.TestCase):
    def test_direct_write_attempt_counts_even_without_a_successful_drain_callback(self):
        row = request_row()
        row['emitted'] = None
        result = model.expected_native_calls(config(), {'requests': [row]})
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['attempts'][0]['origin'], 'direct_write_started')

    def test_missing_or_ambiguous_write_evidence_is_not_replaced_with_a_guess(self):
        for row in ({'id': 'sample'}, {**request_row(write=False), 'emitted': 3}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                model.expected_native_calls(config(), {'requests': [row]})
        row = request_row(write=False)
        self.assertEqual(model.expected_native_calls(config(), {'requests': [row]})['count'], 0)

    def test_worker_starts_must_match_provider_records_exactly_before_allowed_calls_are_counted(self):
        cfg = config([item(), item('denied', 500)], direct=False)
        workers = [{'id': sample['id'], 'trial_id': cfg['trial_id'], 'inference_attempts': 1,
                    'provider_call_started': 1, 'provider_call_finished': 2, 'worker_finished': 3} for sample in cfg['trace']]
        providers = [{'id': sample['id'], 'trial_id': cfg['trial_id'], 'allowed': sample['id'] == 'sample', 'provider_finished': 2} for sample in cfg['trace']]
        result = {'requests': [request_row('sample'), request_row('denied')],
                  'role_records': {'provider': {'records': providers}, 'worker': {'records': workers}}}
        self.assertEqual(model.expected_native_calls(cfg, result)['count'], 1)
        result['role_records']['provider']['records'].pop()
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            model.expected_native_calls(cfg, result)

    def test_duplicate_or_unknown_provider_outcomes_fail_closed(self):
        cfg = config(direct=False)
        worker = {'id': 'sample', 'trial_id': cfg['trial_id'], 'inference_attempts': 1, 'provider_call_started': 1, 'worker_finished': 3}
        provider = {'id': 'sample', 'trial_id': cfg['trial_id'], 'allowed': True, 'provider_finished': 2}
        result = {'requests': [request_row()], 'role_records': {'provider': {'records': [provider]}, 'worker': {'records': [worker]}}}
        for changed in ({**provider, 'allowed': None}, {**provider, 'trial_id': 'previous-trial'}):
            result['role_records']['provider']['records'] = [changed]
            with self.assertRaises(ValueError): model.expected_native_calls(cfg, result)
        result['role_records']['provider']['records'] = [provider, provider]
        with self.assertRaisesRegex(ValueError, 'Duplicate'): model.expected_native_calls(cfg, result)

    def test_worker_path_cannot_certify_zero_calls_while_a_controller_write_has_no_worker_record(self):
        cfg = config(direct=False)
        result = {'requests': [request_row()], 'role_records': {'provider': {'records': []}, 'worker': {'records': []}}}
        with self.assertRaisesRegex(ValueError, 'terminal worker records'):
            model.expected_native_calls(cfg, result)
        result['requests'] = [request_row(write=False)]
        self.assertEqual(model.expected_native_calls(cfg, result)['count'], 0)
        result['role_records']['worker']['records'] = [{'id': 'sample', 'trial_id': cfg['trial_id'], 'inference_attempts': 0}]
        with self.assertRaisesRegex(ValueError, 'terminal worker records'):
            model.expected_native_calls(cfg, result)


class CompletionCertificateTests(unittest.TestCase):
    def certificate(self, responses, *, cfg=None, result=None, deadline=900):
        clock = Clock()
        with patch.object(model, 'json_http', side_effect=responses) as query, \
                patch.object(model.time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(model.time, 'sleep', side_effect=clock.sleep):
            certificate = model.native_completion_certificate(cfg or config(), result or {'requests': [request_row()]},
                                                                state(), IDENTITY, deadline, 0)
        return certificate, query

    def test_barrier_waits_for_all_native_calls_and_archives_full_terminal_responses_once(self):
        running = state(8, 7, [{'sequence': 8, 'request_sha256': model.model_request_sha256(item()), 'started': 20}])
        certificate, query = self.certificate([running, state(8, 8), state(8, 8, records=[terminal()])])
        self.assertTrue(certificate['valid'])
        self.assertEqual(len(certificate['summary_polls']), 2)
        self.assertEqual(certificate['terminal_state']['records'][0]['response']['eval_count'], 6)
        self.assertEqual(sum('records=1' in call.args[0] for call in query.call_args_list), 1)
        self.assertTrue(all(call.kwargs['timeout'] <= 5 for call in query.call_args_list))

    def test_late_done_response_does_not_become_a_useful_client_response(self):
        result = {'requests': [request_row()]}
        before = copy.deepcopy(result)
        certificate, _ = self.certificate([state(8, 8), state(8, 8, records=[terminal()])], result=result)
        self.assertTrue(certificate['valid'])
        self.assertEqual(result, before)
        self.assertFalse(useful(result['requests'][0], config()))

    def test_archived_queue_full_and_stale_rejections_are_valid_zero_native_call_observations(self):
        for reason in ('worker_queue_full', 'stale_trial'):
            with self.subTest(reason=reason):
                app = Application('worker')
                cfg = config(direct=False)
                if reason == 'worker_queue_full': app.active = 36
                else: app.config['trial_id'] = 'previous-trial'
                status, body = asyncio.run(app.work({**item(), 'trial_id': cfg['trial_id'], 'remaining_budget_seconds': 10, 'policy': 'none'}))
                self.assertIn(status,(409,429))
                self.assertEqual(body['reason'],reason)
                self.assertEqual(len(app.records),1)
                row = {**request_row(),'status':status,'response':body}
                result = {'requests':[row], 'role_records':{'provider':{'records':[]},'worker':{'records':app.records}}}
                certificate, _ = self.certificate([state(), state(records=[])],cfg=cfg,result=result)
                self.assertTrue(certificate['valid'])
                self.assertEqual(certificate['expected']['count'],0)
                self.assertFalse(useful(row,cfg))

    def test_missing_duplicate_or_wrong_payload_records_invalidate_the_certificate(self):
        for records in ([], [terminal(), terminal()], [{**terminal(), 'request_sha256': '4' * 64}]):
            with self.subTest(records=records):
                certificate, query = self.certificate([state(8, 8), state(8, 8, records=records)])
                self.assertFalse(certificate['valid'])
                self.assertEqual(certificate['terminal_state']['records'], records)
                self.assertEqual(sum('records=1' in call.args[0] for call in query.call_args_list), 1)

    def test_terminal_archive_transport_failure_is_not_silently_retried(self):
        certificate, query = self.certificate([state(8,8), TimeoutError('terminal archive transport')])
        self.assertFalse(certificate['valid'])
        self.assertEqual(certificate['issues'][0]['type'],'TimeoutError')
        self.assertEqual(sum('records=1' in call.args[0] for call in query.call_args_list),1)

    def test_incomplete_http_error_or_uncertain_generation_is_not_a_terminal_success(self):
        for changed in ({'status': 502}, {'done': False}, {'response': {'done': False}}, {'uncertain': True}, {'error': 'TimeoutError'}):
            with self.subTest(changed=changed):
                certificate, _ = self.certificate([state(8, 8), state(8, 8, records=[{**terminal(), **changed}])])
                self.assertFalse(certificate['valid'])

    def test_changed_proxy_domain_source_or_uncertainty_aborts_without_poll_retry(self):
        for changed in ({'clock_domain': 'proxy-restarted'}, {'source_id': 'sha256:' + '4' * 64}, {'uncertain_total': 1}):
            with self.subTest(changed=changed):
                certificate, query = self.certificate([{**state(8, 8), **changed}, state(8, 8, records=[terminal()])])
                self.assertFalse(certificate['valid'])
                self.assertEqual(len(certificate['summary_polls']), 1)
                self.assertEqual(query.call_count, 2)

    def test_one_shared_deadline_excludes_incomplete_work_without_an_extra_drain_budget(self):
        running = state(8, 7, [{'sequence': 8}])
        certificate, query = self.certificate([running], deadline=15)
        self.assertFalse(certificate['valid'])
        self.assertEqual(certificate['driver_finished'], 15)
        self.assertEqual(certificate['issues'][0]['type'], 'TimeoutError')
        self.assertEqual(query.call_count, 1)
        self.assertIsNone(certificate['terminal_state'])

    def test_payload_multisets_preserve_repeated_identical_calls(self):
        first, second = item(), item('second')
        cfg = config([first, second])
        records = [terminal(9, second), terminal(8, first)]
        certificate, _ = self.certificate([state(9, 9), state(9, 9, records=records)], cfg=cfg,
                                          result={'requests': [request_row(), request_row('second')]})
        self.assertTrue(certificate['valid'])
        self.assertEqual(list(certificate['expected']['request_sha256_counts'].values()), [2])


class CollectionIntegrationTests(unittest.TestCase):
    def test_failed_certificate_is_saved_before_abort_and_prevents_the_next_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'v2').mkdir()
            for name in ('model_service.py', 'inference_proxy.py'):
                (root / 'v2' / name).write_text('controlled fixture\n')
            encoded = gzip.compress(model.canonical([item()]), mtime=0)
            (root / 'trace.json.gz').write_bytes(encoded)
            reference = {'path': 'trace.json.gz', 'sha256': hashlib.sha256(encoded).hexdigest()}
            configs = [model.configuration('real-model', 'low', 0, rep, reference) for rep in (0, 1)]
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'blocks': {'real-model': configs}}))
            controller_hash = hashlib.sha256((root / 'v2/model_service.py').read_bytes()).hexdigest()
            proxy_hash = hashlib.sha256((root / 'v2/inference_proxy.py').read_bytes()).hexdigest()
            role = {'source_id': IDENTITY['source_id']}
            result = {'requests': [request_row()], 'resource_samples': [{'controller': {**role, 'model_controller_sha256': controller_hash},
                       'provider': role, 'workers': [role]}], 'role_records': {'provider': {'records': []}, 'worker': {'records': []}}, 'drained': True}
            calls = []
            def query(url, *args, **kwargs):
                calls.append(url)
                if '/study-state' in url: return {**state(), 'proxy_sha256': proxy_hash}
                if url.endswith('/run'): return result
                if url.endswith('/api/ps'): return {'models': [{'name': model.MODEL, 'digest': model.DIGEST, 'context_length': 8192}]}
                return {}
            observer, native = MagicMock(), MagicMock()
            observer.__enter__.return_value = observer
            observer.samples = [{}]
            native.__enter__.return_value = native
            native.samples = [{'other_server_model_count': 0}]
            native.metadata = {'source_id': IDENTITY['source_id']}
            failure = {'valid': False, 'terminal_state': {'records': [terminal()]}, 'issues': [{'detail': 'missing response'}]}
            def certificate(*args):
                native.__exit__.assert_not_called()
                observer.__exit__.assert_not_called()
                self.assertAlmostEqual(args[4] - args[5], 900)
                return failure
            def command(argv):
                return '{"nano_cpus":4000000000,"memory_bytes":8589934592}' if argv[:2] == ['docker', 'inspect'] else 'offline'
            with ExitStack() as stack:
                for name, value in {'ROOT': root, 'INFERENCE_MONITOR': 'http://local', 'Observer': MagicMock(return_value=observer),
                                    'InferenceObserver': MagicMock(return_value=native)}.items():
                    stack.enter_context(patch.object(model, name, value))
                for name, value in {'collection_identity': IDENTITY, 'verify_server': {}, 'validate': [],
                                    'scale': {'pods': [{'uid': 'worker', 'ip': '127.0.0.1'}]}, 'kubectl': '{}'}.items():
                    stack.enter_context(patch.object(model, name, return_value=value))
                stack.enter_context(patch.object(model, 'verify_worktree'))
                stack.enter_context(patch.object(model, 'command', side_effect=command))
                stack.enter_context(patch.object(model, 'json_http', side_effect=query))
                stack.enter_context(patch.object(model, 'native_completion_certificate', side_effect=certificate))
                with self.assertRaisesRegex(RuntimeError, 'no subsequent reset'):
                    model.run_block(plan, 'real-model', root / 'out', 'offline-image')
            document = json.loads((root / 'out' / (configs[0]['trial_id'] + '.json')).read_text())
            self.assertEqual(document['native_completion_certificate'], failure)
            self.assertFalse(document['valid'])
            self.assertEqual(sum(url.endswith('/prepare') for url in calls), 1)
            self.assertEqual(sum(url.endswith('/run') for url in calls), 1)
            self.assertFalse((root / 'out' / (configs[1]['trial_id'] + '.json')).exists())
            self.assertEqual(json.loads((root / 'out/block.json').read_text())['reason'], 'native_completion_not_certified')

    def test_retained_pilot_and_workload_planning_functions_are_byte_and_ast_unchanged(self):
        root = Path(model.__file__).resolve().parents[1]
        source_id = 'sha256:c811876fbf14c2af54a18f600044368dd0a92561497e0bbdf8d02c2a6bd86c3b'
        original = verify_registered(root, source_id)
        before = (root / original['files']['v2/model.py']['path']).read_text()
        after = Path(model.__file__).read_text()
        old_nodes = {node.name: node for node in ast.parse(before).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        new_nodes = {node.name: node for node in ast.parse(after).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in ('pilot', 'pilot_item', 'warmup', 'verify_server', 'validate_pilot', 'cpu_measurement_seconds', 'derive_model_plan', 'configuration'):
            with self.subTest(name=name):
                self.assertEqual(ast.dump(old_nodes[name]), ast.dump(new_nodes[name]))
                self.assertEqual(ast.get_source_segment(before, old_nodes[name]), ast.get_source_segment(after, new_nodes[name]))


if __name__ == '__main__': unittest.main()
