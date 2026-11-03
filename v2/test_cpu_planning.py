"""Synthetic pilots freeze their own rates and transport traces without Ollama."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from v2 import model, network_probe
from v2.workload import DIGEST, MODEL, OPTIONS


def complete_pilot():
    def answer(index):
        item = model.pilot_item(index)
        return {'item': item, 'status': 200,
                'raw': {'response': json.dumps({'label': 'ok' if item['task_status'] == 200 else 'error'})},
                'correct': True}
    return {'valid': True, 'metadata': {'version': {'version': '0.40.16'},
                'model': {'name': MODEL, 'digest': DIGEST}, 'options': dict(OPTIONS)},
            'warmup': [{**answer(index), 'elapsed': .1} for index in range(4)],
            'results': [{'concurrency': concurrency, 'elapsed_seconds': elapsed, 'completion_rate': 16 / elapsed,
                         'observations': [{**answer(index), 'began': index * .1, 'finished': index * .1 + .05}
                                          for index in range(16)]}
                        for concurrency, elapsed in [(1, 32.), (2, 18.), (4, 12.)]],
            'loaded_model': {'models': [{'name': MODEL, 'digest': DIGEST, 'context_length': OPTIONS['num_ctx']}]}}


class FreshPilotPlanningTests(unittest.TestCase):
    def test_complete_native_pilot_recomputes_its_own_peak_capacity(self):
        self.assertAlmostEqual(model.validate_pilot(complete_pilot()), 16 / 12)

    def test_incomplete_sweep_or_task_failures_cannot_set_rates(self):
        cases = []
        data = complete_pilot(); data['warmup'].pop(); cases.append(data)
        data = complete_pilot(); data['results'][0]['observations'].pop(); cases.append(data)
        data = complete_pilot(); data['results'][0]['concurrency'] = 2; cases.append(data)
        data = complete_pilot(); data['results'][0]['completion_rate'] += 1; cases.append(data)
        data = complete_pilot(); data['results'][0]['observations'][0]['raw'] = {'response': '{"label":"ok"}'}; cases.append(data)
        data = complete_pilot(); data['metadata']['options']['num_ctx'] = 4096; cases.append(data)
        data = complete_pilot(); data['loaded_model']['models'][0]['digest'] = '0' * 64; cases.append(data)
        for index, data in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                model.validate_pilot(data)

    def test_freeze_binds_fresh_pilot_bytes_rates_traces_and_complete_matrices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            pilot = root / 'pilot.json'
            pilot.write_text(json.dumps(complete_pilot()))
            destination = root / 'protocol/cpu-synthetic'
            with patch.object(model, 'ROOT', root):
                plan = model.freeze(pilot, destination)
            self.assertEqual(plan['pilot_sha256'], hashlib.sha256(pilot.read_bytes()).hexdigest())
            self.assertEqual(plan['rates'], {'low': .33, 'near': 1.13, 'high': 2.})
            self.assertEqual(len(plan['blocks']['model-qualification']), 12)
            self.assertEqual(len(plan['blocks']['real-model']), 45)
            for rows in plan['blocks'].values():
                for row in rows:
                    reference = row['trace_reference']
                    self.assertTrue(reference['path'].startswith('protocol/cpu-synthetic/'))
                    raw = (root / reference['path']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), reference['sha256'])
                    self.assertIsInstance(json.loads(gzip.decompress(raw)), list)

    def test_cpu_window_is_fixed_only_from_fresh_pilot_low_rate(self):
        data = complete_pilot()
        self.assertEqual(model.cpu_measurement_seconds(data), 35)
        for setting in data['results']:
            setting['elapsed_seconds'] *= 10
            setting['completion_rate'] = 16 / setting['elapsed_seconds']
        self.assertEqual(model.cpu_measurement_seconds(data), 335)
        plan, _ = model.derive_model_plan(data, 'pilot-hash', 'protocol/cpu',
                                         measurement_seconds=model.cpu_measurement_seconds(data))
        self.assertTrue(all(row['measurement_seconds'] == 335 and row['warmup_seconds'] == 5 and row['deadline_seconds'] == 10
                            for rows in plan['blocks'].values() for row in rows))

    def test_empty_measurement_trace_fails_without_selecting_a_new_seed(self):
        with self.assertRaisesRegex(RuntimeError, 'without selecting another seed'):
            model.derive_model_plan(complete_pilot(), 'pilot-hash', 'protocol/cpu', measurement_seconds=1e-9)

    def test_pure_replay_can_preserve_retained_gzip_header_without_changing_workload(self):
        def encode(_path, items):
            raw = bytearray(gzip.compress(model.canonical(items), mtime=0))
            raw[9] = 3
            return bytes(raw)
        plan, traces = model.derive_model_plan(complete_pilot(), 'pilot-hash', 'protocol/cpu', encode_trace=encode)
        for rows in plan['blocks'].values():
            for row in rows:
                reference = row['trace_reference']
                self.assertEqual(hashlib.sha256(traces[reference['path']]).hexdigest(), reference['sha256'])
                self.assertIsInstance(json.loads(gzip.decompress(traces[reference['path']])), list)


class FreshHeadroomTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'protocol/cpu').mkdir(parents=True)
        self.rows = []
        for repetition in range(3):
            path = self.root / f'protocol/cpu/headroom-{repetition}.json.gz'
            path.write_bytes(gzip.compress(json.dumps([{'id': str(repetition), 'offset': 0,
                                                      'prompt_text': '', 'task_status': 200}]).encode(), mtime=0))
            reference = {'path': str(path.relative_to(self.root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for replicas in (1, 8):
                self.rows.append({'profile': 'headroom', 'repetition': repetition, 'replicas': replicas,
                                  'trace_reference': dict(reference)})
        self.path = self.root / 'protocol/cpu-plan.json'
        self.write()

    def write(self):
        self.path.write_text(json.dumps({'blocks': {'model-qualification': self.rows}}))

    def references(self):
        with patch.object(network_probe, 'ROOT', self.root):
            return network_probe.headroom_references(self.path)

    def test_selected_plan_binds_all_three_fresh_traces_and_its_exact_bytes(self):
        references, metadata = self.references()
        self.assertEqual(set(references), {0, 1, 2})
        self.assertEqual(metadata['plan_path'], 'protocol/cpu-plan.json')
        self.assertEqual(metadata['plan_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertTrue(all(row['path'].startswith('protocol/cpu/') for row in references.values()))

    def test_missing_repetitions_mixed_replica_traces_and_altered_bytes_are_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows = self.rows[:-2]; self.write()
        with self.assertRaisesRegex(ValueError, 'all three'):
            self.references()
        self.rows = copy.deepcopy(original)
        self.rows[1]['trace_reference']['sha256'] = '0' * 64; self.write()
        with self.assertRaisesRegex(ValueError, 'replica arms'):
            self.references()
        self.rows = original; self.write()
        (self.root / self.rows[0]['trace_reference']['path']).write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError, 'frozen plan'):
            self.references()


class FreshHeadroomCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_records_the_selected_plan_and_own_headroom_references(self):
        fixture = FreshHeadroomTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        snapshot = MagicMock(source_id='sha256:' + '1' * 64, git_base='2' * 40)
        snapshot.metadata.return_value = {'source_id': snapshot.source_id, 'git_base': snapshot.git_base}

        async def observe(_url, item, start):
            scheduled = start + item['offset']
            return {'id': item['id'], 'scheduled': scheduled, 'emitted': scheduled + .005,
                    'finished': scheduled + .05, 'latency_seconds': .05, 'status': 200,
                    'transport_fixture_returned': True}

        with patch.object(network_probe, 'ROOT', fixture.root), patch.object(network_probe, 'capture', return_value=snapshot), \
                patch.object(network_probe, 'verify_worktree'), patch.object(network_probe, 'observe', side_effect=observe), \
                patch.object(network_probe.asyncio, 'sleep', new=AsyncMock()):
            state = await network_probe.collect('http://local/transport-control', fixture.root / 'control.json', fixture.path)
        self.assertTrue(state['qualified'])
        self.assertEqual(state['plan_path'], 'protocol/cpu-plan.json')
        self.assertEqual(state['plan_sha256'], hashlib.sha256(fixture.path.read_bytes()).hexdigest())
        self.assertEqual(len(state['trials']), 3)
        for trial in state['trials']:
            self.assertTrue(trial['trace_reference']['path'].startswith('protocol/cpu/'))
            self.assertEqual(trial['trace_sha256'], trial['trace_reference']['sha256'])


if __name__ == '__main__':
    unittest.main()
