"""Readiness contract tests use synthetic metadata and no network or inference."""
import copy
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from scripts import check_cpu_readiness as checker
from v2 import test_cpu_replay as cpu_fixtures
from v2 import verify


class CPUReadinessTests(unittest.TestCase):
    def setUp(self):
        fixture = cpu_fixtures.CPUCampaignReplayTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.identity = fixture.identity
        self.monitor = copy.deepcopy(fixture.pilot['metadata']['inference_environment'])
        self.monitor.update(monitor_sha256='monitor-hash', clock_domain='native-test-domain',
                            version={'version': '0.40.16'}, model_state={'models': []})
        self.calls = []
        self.refuse_start = False

    def query(self, url, payload=None):
        self.calls.append((url, payload))
        if url.endswith('/api/version'):
            return {'version': '0.40.16'}
        if url.endswith('/api/tags'):
            return {'models': [copy.deepcopy(self.fixture.pilot['metadata']['model'])]}
        if url.endswith('/api/ps'):
            return {'models': []}
        if url.endswith('/metadata'):
            return copy.deepcopy(self.monitor)
        if url.endswith('/start'):
            if self.refuse_start:
                raise HTTPError(url, 409, 'another_trial_is_monitored', {}, None)
            self.trial_id = payload['trial_id']
            return {'trial_id': self.trial_id, 'clock_domain': self.monitor['clock_domain']}
        if url.endswith('/stop'):
            return {'metadata': {**self.identity, 'monitor_sha256': 'monitor-hash', 'trial_id': self.trial_id,
                                  'clock_domain': self.monitor['clock_domain'], 'started': 1., 'finished': 3.5},
                    'samples': [{'processes': [{'pid': 100}], 'other_server_model_count': 0,
                                 'linux_cgroups': {'native': copy.deepcopy(self.fixture.envelope)}} for _ in range(2)]}
        raise AssertionError('Unexpected endpoint: ' + url)

    def run_readiness(self, **options):
        snapshot = MagicMock(source_id=self.identity['source_id'], git_base=self.identity['git_base'])
        registered = {'files': {'v2/inference_monitor.py': {'sha256': 'monitor-hash'}}}
        with patch.object(checker, 'ROOT', self.fixture.root), patch.object(verify, 'ROOT', self.fixture.root), \
                patch.object(checker, 'capture', return_value=snapshot), patch.object(checker, 'verify_worktree'), \
                patch.object(checker, 'verify_registered', return_value=registered), \
                patch.object(checker, 'hardware', return_value={'system': 'Linux'}), \
                patch.object(checker, 'query', side_effect=self.query), patch.object(checker.time, 'sleep'):
            return checker.readiness(self.fixture.prefix, self.identity['source_id'], self.identity['git_base'], **options)

    def test_default_readiness_uses_get_only_and_accepts_an_unloaded_pinned_model(self):
        result = self.run_readiness()
        self.assertTrue(result['ready'])
        self.assertFalse(result['model_loaded'])
        self.assertEqual(result['generation_requests'], 0)
        self.assertTrue(all(payload is None for _url, payload in self.calls))
        self.assertFalse(any('/generate' in url or url.endswith('/run') for url, _payload in self.calls))

    def test_explicit_native_observation_starts_and_stops_its_own_session_without_generation(self):
        result = self.run_readiness(sample_observer=True)
        self.assertEqual(result['observer_sampling']['samples'], 2)
        self.assertEqual(result['observer_sampling']['model_process_pids'], [100])
        posts = [(url, body) for url, body in self.calls if body is not None]
        self.assertEqual([url.rsplit('/', 1)[1] for url, _body in posts], ['start', 'stop'])
        self.assertEqual(posts[0][1], posts[1][1])
        self.assertEqual(result['generation_requests'], 0)

    def test_other_active_observation_is_not_stopped(self):
        self.refuse_start = True
        with self.assertRaises(HTTPError):
            self.run_readiness(sample_observer=True)
        self.assertFalse(any(url.endswith('/stop') for url, _payload in self.calls))

    def test_wrong_monitor_identity_or_28_gib_envelope_cannot_authorize_fra(self):
        self.monitor['source_id'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'source, Git base'):
            self.run_readiness()
        self.monitor['source_id'] = self.identity['source_id']
        self.monitor['hardware']['observer_cgroup']['visible_hierarchy_limits']['memory_max_bytes_upper_bound'] = 28 * 1024 ** 3
        with self.assertRaisesRegex(ValueError, 'cgroup envelope'):
            self.run_readiness()

    def test_require_loaded_never_initiates_a_model_load(self):
        with self.assertRaisesRegex(ValueError, 'unexpectedly empty'):
            self.run_readiness(require_loaded=True)
        self.assertTrue(all(payload is None for _url, payload in self.calls))


if __name__ == '__main__':
    unittest.main()
