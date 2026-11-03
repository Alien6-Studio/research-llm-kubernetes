"""Synthetic CPU records bind a fresh pilot and never reuse Mac outcomes."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from v2 import model, network_probe, verify
from v2 import cpu_campaigns
from v2 import test_continuation_gate as gate_fixtures
from v2.test_cpu_planning import complete_pilot
from v2.cpu_campaigns import get_cpu_campaign


class CPUCampaignReplayTests(unittest.TestCase):
    def setUp(self):
        fixture = gate_fixtures.ContinuationGateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.identity = fixture.identity
        self.prefix = 'v2-ollama-cpu-synthetic-fixture'
        descriptor = get_cpu_campaign('v2-ollama-fra6s004-cpu-confirmation')
        descriptor.update(prefix=self.prefix, cohort='ollama-cpu-synthetic-fixture', terminal=False,
                          plan_path='protocol/ollama-cpu-synthetic-fixture-plan.json',
                          trace_directory='protocol/ollama-cpu-synthetic-fixture')
        registry = patch.object(cpu_campaigns, 'CPU_CAMPAIGNS', {**cpu_campaigns.CPU_CAMPAIGNS, self.prefix: descriptor})
        registry.start(); self.addCleanup(registry.stop)
        attempts = patch.dict(verify.PLAIN_ATTEMPTS, {self.prefix: {'plan': descriptor['plan_path'],
            'cohort': descriptor['cohort'], 'interrupted': False, 'fresh_cpu_pilot': True}})
        attempts.start(); self.addCleanup(attempts.stop)
        self.campaign = get_cpu_campaign(self.prefix)
        self.pilot = complete_pilot()
        self.pilot.update(self.identity, utc='2026-11-13T11:00:00+00:00')
        artifact = (verify.ROOT / verify.MODEL_ARTIFACT_PATH).read_bytes()
        path = self.root / verify.MODEL_ARTIFACT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifact)
        manifest = json.loads(artifact)
        staging = {'model': model.MODEL, 'manifest_sha256': model.DIGEST, 'manifest_bytes': len(artifact),
                   'registry_blobs': 'https://registry.ollama.ai/v2/library/qwen3.6/blobs/',
                   'blobs': [{'digest': blob['digest'], 'bytes': blob['size'], 'verified': True, 'path': '/synthetic/' + blob['digest']}
                             for blob in [manifest['config'], *manifest['layers']]]}
        self.envelope = {'visible_hierarchy_limits': {'complete': True, 'cpu_quota_cores_upper_bound': 8.,
            'memory_max_bytes_upper_bound': self.campaign['inference_memory_bytes'], 'memory_swap_max_bytes_upper_bound': 0}}
        self.pilot['metadata'].update(client_hardware={'system': 'Linux'},
            inference_environment={**self.identity, 'hardware': {'system': 'Linux',
                'observer_cgroup': copy.deepcopy(self.envelope),
                'deployment_artifacts': {'ollama_binary': dict(verify.CPU_BINARY_ARTIFACT), 'model_staging': staging}}})
        self.pilot['loaded_model']['models'][0]['size_vram'] = 0
        self.pilot_hash = self.write_pilot()
        self.plan, traces = model.derive_model_plan(self.pilot, self.pilot_hash, self.campaign['trace_directory'],
            measurement_seconds=model.cpu_measurement_seconds(self.pilot), provider_url=self.campaign['inference_url'])
        for relative, encoded in traces.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
        descriptor = (verify.ROOT / 'v2/cpu_campaigns.py').read_bytes()
        (self.root / 'v2').mkdir(exist_ok=True)
        (self.root / 'v2/cpu_campaigns.py').write_bytes(descriptor)
        self.plan['continuation'] = {**self.identity, 'cohort': self.campaign['cohort'], 'campaign_descriptor': self.campaign,
            'method_sources': [{'path': 'v2/cpu_campaigns.py', 'sha256': hashlib.sha256(descriptor).hexdigest(), 'bytes': len(descriptor)}],
            'frozen_utc': '2026-11-13T12:00:00+00:00', 'transport_control': self.campaign['inference_url'] + '/transport-control'}
        self.path = self.root / self.campaign['plan_path']
        self.write_plan()
        previous = fixture.prefix
        self.archives = {self.prefix + name.removeprefix(previous): copy.deepcopy(data) for name, data in fixture.archives.items()}
        self.data('network')[0].update(plan_path=self.campaign['plan_path'], plan_sha256=self.plan_hash,
                                      endpoint=self.plan['continuation']['transport_control'])
        self.data('warmup')[0].update(metadata=copy.deepcopy(self.pilot['metadata']),
                                     loaded_model=copy.deepcopy(self.pilot['loaded_model']))
        for stage, block_name in [('qualification', 'model-qualification'), ('real-model', 'real-model')]:
            self.data(stage)[0].update(plan_sha256=self.plan_hash, metadata=copy.deepcopy(self.pilot['metadata']))
            for trial, config in zip(self.data(stage)[2:], self.plan['blocks'][block_name], strict=True):
                trial['configuration'] = copy.deepcopy(config)
                trial['model_after_trial'] = copy.deepcopy(self.pilot['loaded_model'])
                trial['native_observations'] = [{'linux_cgroups': {'native': copy.deepcopy(self.envelope)}}]

    def data(self, stage):
        return self.archives[self.prefix + '-' + stage]

    def write_pilot(self):
        raw = json.dumps(self.pilot).encode()
        encoded = gzip.compress(raw, mtime=0)
        relative = 'archives/' + self.prefix + '-pilot/pilot.json.gz'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        entry = {'path': relative, 'sha256': hashlib.sha256(encoded).hexdigest(),
                 'decoded_sha256': hashlib.sha256(raw).hexdigest(), 'decoded_bytes': len(raw)}
        (path.parent / 'manifest.json').write_text(json.dumps({'files': [entry]}))
        return entry['decoded_sha256']

    def write_plan(self):
        self.path.write_text(json.dumps(self.plan))
        self.plan_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def replay_plan(self):
        with patch.object(verify, 'ROOT', self.root):
            return verify.replay_cpu_plan(self.plan)

    def audit(self):
        with patch.object(verify, 'ROOT', self.root), patch.object(verify, 'replay_network_control', return_value=True), \
                patch.object(verify, 'replay_continuation_trial_gate', return_value=[]):
            return verify.audit_plain_continuation(self.archives, prefix=self.prefix)

    def test_fresh_cpu_pilot_window_workloads_and_own_complete_gates_are_required(self):
        expected = self.replay_plan()
        self.assertEqual(expected['rates'], {'low': .33, 'near': 1.13, 'high': 2.})
        self.assertTrue(all(config['measurement_seconds'] == 35 for rows in expected['blocks'].values() for config in rows))
        self.assertTrue(self.audit()['eligible'])

    def test_missing_or_other_pilot_bytes_cannot_set_cpu_rates(self):
        self.plan['pilot_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'own archived pilot'):
            self.replay_plan()
        (self.root / ('archives/' + self.prefix + '-pilot/pilot.json.gz')).unlink()
        with self.assertRaises(OSError):
            self.replay_plan()

    def test_mac_placement_or_gpu_execution_cannot_be_presented_as_the_cpu_pilot(self):
        for changed in ('Mac', 'GPU', 'source'):
            original = copy.deepcopy(self.pilot)
            if changed == 'Mac':
                self.pilot['metadata']['client_hardware']['system'] = 'Darwin'
            elif changed == 'GPU':
                self.pilot['loaded_model']['models'][0]['size_vram'] = 100
            else:
                self.pilot['source_id'] = 'sha256:' + '9' * 64
            self.plan['pilot_sha256'] = self.write_pilot()
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.replay_plan()
            self.pilot = original
        self.plan['pilot_sha256'] = self.write_pilot()

    def test_wrong_binary_or_unverified_model_blob_cannot_qualify_cpu_metadata(self):
        artifacts = self.pilot['metadata']['inference_environment']['hardware']['deployment_artifacts']
        original = copy.deepcopy(artifacts)
        artifacts['ollama_binary']['sha256'] = '0' * 64
        with patch.object(verify, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'binary artifact'):
            verify.verify_cpu_metadata(self.pilot['metadata'], self.prefix)
        artifacts.clear(); artifacts.update(original)
        artifacts['model_staging']['blobs'][0]['verified'] = False
        with patch.object(verify, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'staged model blobs'):
            verify.verify_cpu_metadata(self.pilot['metadata'], self.prefix)

    def test_unknown_or_changed_resource_envelope_cannot_qualify_cpu_metadata(self):
        group = self.pilot['metadata']['inference_environment']['hardware']['observer_cgroup']
        group['visible_hierarchy_limits']['complete'] = False
        with patch.object(verify, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'cgroup envelope'):
            verify.verify_cpu_metadata(self.pilot['metadata'], self.prefix)
        group['visible_hierarchy_limits']['complete'] = True
        group['visible_hierarchy_limits']['cpu_quota_cores_upper_bound'] = 4
        with patch.object(verify, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'cgroup envelope'):
            verify.verify_cpu_metadata(self.pilot['metadata'], self.prefix)

    def test_stale_mac_rates_or_default_twenty_second_window_cannot_match_cpu_plan(self):
        original = copy.deepcopy(self.plan)
        self.plan['rates']['high'] = 7.03
        with self.assertRaisesRegex(ValueError, 'fresh pilot'):
            self.replay_plan()
        self.plan = original
        for rows in self.plan['blocks'].values():
            for config in rows:
                config['measurement_seconds'] = 20
        with self.assertRaisesRegex(ValueError, 'prospective window'):
            self.replay_plan()

    def test_changed_prompt_or_offset_is_detected_from_retained_trace_bytes(self):
        path = self.root / self.plan['blocks']['real-model'][0]['trace_reference']['path']
        items = json.loads(gzip.decompress(path.read_bytes()))
        items[0]['prompt_text'] += ' changed'
        path.write_bytes(gzip.compress(json.dumps(items).encode(), mtime=0))
        with self.assertRaises(AssertionError):
            self.replay_plan()

    def test_equal_decoded_inputs_with_another_gzip_os_header_remain_reproducible(self):
        relative = self.plan['blocks']['real-model'][0]['trace_reference']['path']
        path = self.root / relative
        encoded = bytearray(path.read_bytes()); encoded[9] = 3
        path.write_bytes(encoded)
        for rows in self.plan['blocks'].values():
            for config in rows:
                if config['trace_reference']['path'] == relative:
                    config['trace_reference']['sha256'] = hashlib.sha256(encoded).hexdigest()
        self.replay_plan()

    def test_cpu_attempt_cannot_borrow_mac_prerequisite_archives(self):
        for stage in ('network', 'qualification', 'warmup'):
            self.archives[verify.STDIO_COHORT + '-' + stage] = self.archives.pop(self.prefix + '-' + stage)
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.audit()

    def test_fra_attempt_cannot_borrow_dell_prerequisite_archives(self):
        for stage in ('network', 'qualification', 'warmup'):
            self.archives[verify.CPU_COHORT + '-' + stage] = self.archives.pop(self.prefix + '-' + stage)
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.audit()

    def test_confirmation_cannot_borrow_the_interrupted_fra_attempt_prerequisites(self):
        for stage in ('network', 'qualification', 'warmup'):
            self.archives['v2-ollama-fra6s004-cpu-' + stage] = self.archives.pop(self.prefix + '-' + stage)
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.audit()

    def test_fra_descriptor_and_shared_registry_identity_are_bound_before_replay(self):
        self.plan['continuation']['campaign_descriptor']['inference_memory_bytes'] = 28 * 1024 ** 3
        with self.assertRaisesRegex(ValueError, 'campaign descriptor'):
            self.replay_plan()
        self.plan['continuation']['campaign_descriptor'] = get_cpu_campaign(self.prefix)
        self.plan['continuation']['method_sources'] = []
        with self.assertRaisesRegex(ValueError, 'descriptor method identity'):
            self.replay_plan()

    def test_cpu_network_identity_and_each_real_gpu_placement_are_checked(self):
        self.data('network')[0]['plan_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'network_fresh_cpu_plan_identity_mismatch'):
            self.audit()
        self.data('network')[0]['plan_sha256'] = self.plan_hash
        self.data('real-model')[2]['model_after_trial']['models'][0]['size_vram'] = 1
        with self.assertRaisesRegex(ValueError, 'validity_claim_not_reproduced'):
            self.audit()

    def test_model_cgroup_caps_cannot_be_substituted_by_monitor_caps(self):
        model_group = self.data('qualification')[2]['native_observations'][0]['linux_cgroups']['native']
        model_group['visible_hierarchy_limits']['cpu_quota_cores_upper_bound'] = 4
        with self.assertRaisesRegex(ValueError, 'validity_claim_not_reproduced'):
            self.audit()

    def test_pilot_only_cohort_is_retained_but_cannot_be_qualified(self):
        self.archives = {self.prefix + '-pilot': [self.pilot]}
        result = self.audit()
        self.assertFalse(result['eligible'])
        self.assertFalse(result['claimed_qualified'])
        self.assertEqual(result['real_model_trials'], 0)

    def test_interrupted_fra_pilot_is_retained_without_a_plan_or_successful_gates(self):
        prefix = 'v2-ollama-fra6s004-cpu'
        archives = {prefix + '-pilot': [{**self.pilot, 'valid': False, 'results': []}]}
        with patch.object(verify, 'ROOT', self.root):
            result = verify.audit_plain_continuation(archives, prefix=prefix)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['claimed_qualified'])
        self.assertEqual(result['issues'], ['attempt_interrupted_before_completion'])
        self.assertEqual(result['real_model_trials'], 0)

    def test_fresh_network_replay_uses_only_cpu_headroom_files(self):
        control = {'endpoint': self.campaign['inference_url'] + '/transport-control', 'plan_path': self.campaign['plan_path'],
                   'plan_sha256': self.plan_hash, 'qualified': True, 'trials': []}
        refs = {config['repetition']: config['trace_reference'] for config in self.plan['blocks']['model-qualification']
                if config['profile'] == 'headroom'}
        for repetition, reference in sorted(refs.items()):
            items = json.loads(gzip.decompress((self.root / reference['path']).read_bytes()))
            rows = []
            for item in items:
                body = json.dumps(model.model_request(item), separators=(',', ':')).encode()
                scheduled = repetition * 100 + item['offset']
                rows.append({'id': item['id'], 'scheduled': scheduled, 'emitted': scheduled + .004,
                             'finished': scheduled + .05, 'latency_seconds': .05, 'status': 200,
                             'transport_fixture_returned': True, 'request_body_bytes': len(body),
                             'request_body_sha256': hashlib.sha256(body).hexdigest()})
            control['trials'].append({'repetition': repetition, 'trace_reference': reference,
                'trace_sha256': reference['sha256'], 'requests': rows,
                'emission_lag_p95_seconds': .004, 'latency_p95_seconds': .05, 'passed': True})
        self.assertFalse((self.root / 'protocol/v2-model').exists())
        with patch.object(verify, 'ROOT', self.root), patch.object(network_probe, 'ROOT', self.root):
            self.assertTrue(verify.replay_network_control(control))


if __name__ == '__main__':
    unittest.main()
