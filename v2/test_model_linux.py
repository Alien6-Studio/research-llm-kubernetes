"""Linux host observations and interruption checkpoints use no live inference."""
from contextlib import ExitStack
import errno
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from v2 import model


class ProcessTimeTests(unittest.TestCase):
    def test_posix_and_linux_day_prefixes_preserve_cpu_seconds(self):
        for text, expected in [('00:01', 1), ('03:04.25', 184.25), ('02:03:04', 7384),
                               ('1-02:03:04', 93784), ('12-23:59:59', 1123199), ('4432:03.1', 265923.1)]:
            with self.subTest(text=text):
                self.assertAlmostEqual(model.ps_cpu_seconds(text), expected)

    def test_invalid_or_nonfinite_cpu_times_fail_explicitly(self):
        for text in ('1', '-1:00', 'day-01:00:00', '01:nan', '01:inf', '1:2:3:4'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                model.ps_cpu_seconds(text)


class LinuxSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.proc, self.sys, self.cgroups = [self.root / name for name in ('proc', 'sys', 'cgroups')]
        self.artifacts = self.root / 'public-artifacts'
        self.artifacts.mkdir()
        (self.artifacts / 'ollama-artifact.json').write_text('{"tar_sha256":"public-binary-digest","bytes":123}')
        (self.artifacts / 'model-staging.json').write_text('{"manifest_sha256":"public-model-digest"}')
        for path, value in [('stat', 'cpu 1 2 3 4\n'), ('meminfo', 'MemTotal: 1024 kB\n'),
                            ('loadavg', '0.1 0.2 0.3 1/10 123\n'), ('cpuinfo', 'model name : Test CPU\n'),
                            ('pressure/cpu', 'some avg10=0.00 total=100\n'),
                            ('pressure/memory', 'some avg10=0.00 total=0\n'), ('pressure/io', 'some avg10=0.00 total=0\n')]:
            target = self.proc / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
        (self.proc / 'self').mkdir()
        (self.proc / 'self/mountinfo').write_text(f'29 23 0:26 / {self.cgroups} rw - cgroup2 cgroup rw\n')
        for pid in (100, 101):
            (self.proc / str(pid)).mkdir()
            (self.proc / str(pid) / 'cgroup').write_text('0::/study\n')
            (self.proc / str(pid) / 'status').write_text('Name:\tollama\nCpus_allowed_list:\t2,4\nVmRSS:\t10 kB\n')
        group = self.cgroups / 'study'
        group.mkdir(parents=True)
        for name, value in [('cpu.max', '800000 100000\n'), ('memory.max', '30064771072\n'),
                            ('memory.swap.max', '0\n'), ('memory.current', '123456\n')]:
            (self.cgroups / name).write_text(value)
        for name, value in [('cpu.stat', 'usage_usec 123\nnr_throttled 0\n'), ('cpu.max', '800000 100000\n'),
                            ('memory.current', '1024\n'), ('memory.max', '4096\n'),
                            ('memory.events', 'oom 0\noom_kill 0\n'), ('cpuset.cpus.effective', '2,4\n')]:
            (group / name).write_text(value)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(model, 'PROC_ROOT', self.proc))
        stack.enter_context(patch.object(model, 'SYS_ROOT', self.sys))
        stack.enter_context(patch.object(model, 'ETC_RESEARCH_ROOT', self.artifacts))
        stack.enter_context(patch.object(model.platform, 'system', return_value='Linux'))
        stack.enter_context(patch.object(model.os, 'sched_getaffinity', return_value={2, 4}, create=True))
        stack.enter_context(patch.object(model, 'command', side_effect=self.command))

    @staticmethod
    def command(args):
        if args[0] == 'lsof':
            return '100\n'
        if args[0] == 'ps':
            return '100 1 1-02:03:04 10 ollama\n101 100 03:04.25 20 runner\n999 1 01:00 30 other\n'
        return ''

    def snapshot(self, other):
        def query(url):
            if ':11434/' in url:
                if isinstance(other, Exception):
                    raise other
                return other
            return {'models': []}
        with patch.object(model, 'json_http', side_effect=query):
            return model.native_snapshot()

    def test_absent_normal_linux_server_is_explicit_and_dedicated_processes_are_retained(self):
        data = self.snapshot(URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused')))
        self.assertNotIn('unavailable', data)
        self.assertEqual(data['other_server_model_count'], 0)
        self.assertEqual(data['other_server_observation'], 'not_present')
        self.assertEqual([process['pid'] for process in data['processes']], [100, 101])
        self.assertEqual(data['processes'][0]['cpu_seconds'], 93784)
        self.assertEqual(data['processes'][0]['linux']['cpu_affinity'], [2, 4])
        self.assertEqual(data['linux_host']['pressure']['cpu'], 'some avg10=0.00 total=100\n')
        self.assertIn('share this host', data['linux_host']['scope'])
        group = next(iter(data['linux_cgroups'].values()))
        self.assertEqual(group['files']['cpu.max'], '800000 100000\n')
        self.assertEqual(group['files']['memory.max'], '4096\n')
        self.assertEqual(group['files']['cpu.stat'], 'usage_usec 123\nnr_throttled 0\n')
        self.assertEqual(group['files']['memory.pressure'], {'unavailable': 'FileNotFoundError'})

    def test_only_connection_refusal_counts_as_absence(self):
        for error in (TimeoutError(), URLError(TimeoutError()), PermissionError(),
                      HTTPError('http://local', 503, 'unavailable', {}, None)):
            with self.subTest(error=type(error).__name__):
                data = self.snapshot(error)
                self.assertIsNone(data['other_server_model_count'])
                self.assertEqual(data['other_server_observation'], 'unavailable')

    def test_invalid_model_metadata_is_unavailable_and_present_server_is_not_absent(self):
        data = self.snapshot({'error': 'unexpected response'})
        self.assertIsNone(data['other_server_model_count'])
        self.assertEqual(data['other_server_observation'], 'unavailable')
        data = self.snapshot({'models': [{}, {}]})
        self.assertEqual(data['other_server_model_count'], 2)
        self.assertEqual(data['other_server_observation'], 'present')

    def test_darwin_normal_server_behavior_is_unchanged(self):
        with patch.object(model.platform, 'system', return_value='Darwin'):
            data = self.snapshot(ConnectionRefusedError())
        self.assertIsNone(data['other_server_model_count'])
        self.assertNotIn('other_server_observation', data)

    def test_unresolved_or_unsafe_cgroups_are_explicit(self):
        for membership in ('2:cpu:/study\n', '0::/../escape\n', None):
            with self.subTest(membership=membership):
                self.assertIn('unavailable', model.linux_cgroup_observations(membership))

    def test_cgroup_namespace_root_resolves_to_the_visible_container_mount(self):
        mount = self.cgroups / 'study'
        (self.proc / 'self/mountinfo').write_text(f'29 23 0:26 /lxc.payload/a6-study {mount} rw - cgroup2 cgroup rw\n')
        result = model.linux_cgroup_observations('0::/\n')
        self.assertEqual(result['path_resolution'], 'cgroup_namespace_relative')
        self.assertEqual(result['path'], str(mount))
        self.assertEqual(result['files']['cpu.max'], '800000 100000\n')
        self.assertEqual(len(result['visible_hierarchy']), 1)
        self.assertEqual(result['visible_hierarchy'][0]['path'], str(mount))
        self.assertIn('not model-only process RSS', result['container_root_aggregate']['scope'])

    def test_unlimited_service_inherits_visible_container_cpu_memory_and_swap_caps(self):
        group = self.cgroups / 'study'
        for name, value in [('cpu.max', 'max 100000\n'), ('memory.max', 'max\n'), ('memory.swap.max', 'max\n')]:
            (group / name).write_text(value)
        result = model.linux_cgroup_observations('0::/study\n')
        limits = result['visible_hierarchy_limits']
        self.assertEqual(limits['cpu_quota_cores_upper_bound'], 8.)
        self.assertEqual(limits['memory_max_bytes_upper_bound'], 30064771072)
        self.assertEqual(limits['memory_swap_max_bytes_upper_bound'], 0)
        self.assertTrue(limits['complete'])
        self.assertEqual(len(result['visible_hierarchy']), 2)
        self.assertEqual(result['container_root_aggregate']['files']['memory.current'], '123456\n')
        self.assertEqual(result['files']['memory.max'], 'max\n')

    def test_missing_ancestor_constraints_remain_unknown_instead_of_unbounded(self):
        group = self.cgroups / 'study'
        (group / 'cpu.max').write_text('max 100000\n')
        (group / 'memory.swap.max').write_text('max\n')
        (self.cgroups / 'cpu.max').unlink()
        limits = model.linux_cgroup_observations('0::/study\n')['visible_hierarchy_limits']
        self.assertFalse(limits['complete'])
        self.assertIsNone(limits['cpu_quota_cores_upper_bound'])
        self.assertFalse(limits['finite_caps_observed']['cpu'])
        self.assertTrue(any(row['file'] == 'cpu.max' and row['path'] == str(self.cgroups)
                            for row in limits['unavailable_constraints']))

    def test_hardware_captures_only_the_named_public_deployment_records(self):
        (self.artifacts / 'unrelated.json').write_text('{"private":"not-an-artifact"}')
        hardware = model.hardware()
        self.assertEqual(hardware['deployment_artifacts']['ollama_binary']['tar_sha256'], 'public-binary-digest')
        self.assertEqual(hardware['deployment_artifacts']['model_staging']['manifest_sha256'], 'public-model-digest')
        self.assertNotIn('not-an-artifact', json.dumps(hardware))
        with self.assertRaisesRegex(ValueError, 'Unknown public deployment artifact'):
            model.read_deployment_artifact('unrelated.json')
        (self.artifacts / 'model-staging.json').write_text('invalid JSON')
        self.assertIn('unavailable', model.read_deployment_artifact('model-staging.json'))


class ModelCheckpointTests(unittest.TestCase):
    def test_api_state_failure_preserves_measured_result_and_stops_before_next_trial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'v2').mkdir()
            controller = b'test controller source\n'
            (root / 'v2/model_service.py').write_bytes(controller)
            (root / 'v2/inference_proxy.py').write_bytes(b'test proxy source\n')
            controller_hash = hashlib.sha256(controller).hexdigest()
            trace = [{'id': 'sample', 'offset': 0, 'tokens': 1000, 'service_seconds': 0,
                      'task_status': 200, 'prompt_text': ''}]
            encoded = gzip.compress(model.canonical(trace), mtime=0)
            (root / 'trace.json.gz').write_bytes(encoded)
            reference = {'path': 'trace.json.gz', 'sha256': hashlib.sha256(encoded).hexdigest()}
            configs = [model.configuration('real-model', 'low', 0, index, reference) for index in range(2)]
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'blocks': {'real-model': configs}}))
            identity = {'source_id': 'sha256:' + '1' * 64, 'git_base': '2' * 40, 'source_commit': '2' * 40}
            role = {'source_id': identity['source_id']}
            result = {'resource_samples': [{'controller': {**role, 'model_controller_sha256': controller_hash},
                                           'provider': role, 'workers': [role]}],
                      'role_records': {'provider': {'records': []}, 'worker': {'records': []}},
                      'requests': [{'id': 'sample', 'status': 200}], 'drained': True}
            calls = []

            def query(url, *args, **kwargs):
                calls.append(url)
                if url.endswith('/run'):
                    return result
                if url.endswith('/api/ps'):
                    raise TimeoutError('offline checkpoint fixture')
                if '/study-state' in url:
                    return {**identity, 'proxy_sha256': hashlib.sha256((root / 'v2/inference_proxy.py').read_bytes()).hexdigest(),
                            'clock_domain': 'fixture-proxy', 'clock': 'time.monotonic', 'started_total': 0, 'finished_total': 0,
                            'uncertain_total': 0, 'active': []}
                return {}

            observer, native = MagicMock(), MagicMock()
            observer.__enter__.return_value = observer
            observer.samples = [{}]
            native.__enter__.return_value = native
            native.samples = [{'other_server_model_count': 0}]
            native.metadata = {'source_id': identity['source_id']}

            def command(args):
                if args[:2] == ['docker', 'inspect']:
                    return '{"nano_cpus":4000000000,"memory_bytes":8589934592}'
                return 'offline'

            with ExitStack() as stack:
                for name, value in {'ROOT': root, 'INFERENCE_MONITOR': 'http://local', 'Observer': MagicMock(return_value=observer),
                                    'InferenceObserver': MagicMock(return_value=native)}.items():
                    stack.enter_context(patch.object(model, name, value))
                for name, value in {'collection_identity': identity, 'verify_server': {}, 'validate': [],
                                    'scale': {'pods': [{'uid': 'worker', 'ip': '127.0.0.1'}]}, 'kubectl': '{}'}.items():
                    stack.enter_context(patch.object(model, name, return_value=value))
                stack.enter_context(patch.object(model, 'verify_worktree'))
                stack.enter_context(patch.object(model, 'command', side_effect=command))
                stack.enter_context(patch.object(model, 'json_http', side_effect=query))
                stack.enter_context(patch.object(model, 'native_completion_certificate', return_value={'valid': True}))
                with self.assertRaisesRegex(RuntimeError, 'measured trial retained'):
                    model.run_block(plan, 'real-model', root / 'out', 'offline-image')
            document = json.loads((root / 'out' / (configs[0]['trial_id'] + '.json')).read_text())
            self.assertEqual(document['result'], result)
            self.assertFalse(document['valid'])
            self.assertIn('model_state_query_failed', document['issues'])
            self.assertEqual(document['model_after_trial'], {'unavailable': 'TimeoutError'})
            block = json.loads((root / 'out/block.json').read_text())
            self.assertFalse(block['complete'])
            self.assertFalse(block['qualified'])
            self.assertEqual(block['trials'], 1)
            self.assertEqual(block['reason'], 'model_state_query_failed')
            self.assertEqual(sum(url.endswith('/run') for url in calls), 1)


if __name__ == '__main__':
    unittest.main()
