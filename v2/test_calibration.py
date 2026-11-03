"""Replay the retained calibration and exact timeout delta without inference."""
from contextlib import ExitStack
import copy
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import freeze_cpu_campaign as freezer
from v2 import calibration, identity, verify
from v2 import test_continuation_gate as gate_fixtures
from v2.cpu_campaigns import get_cpu_campaign


class RetainedCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.prefix = 'v2-ollama-fra6s004-cpu-long-window'
        self.campaign = get_cpu_campaign(self.prefix)
        self.original_root = verify.ROOT
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.base = 'db31fdad5e022ae165c1661c41632c1c9a761b84'
        for source in (self.campaign['calibration_source_id'], self.campaign['collection_source_id']):
            registered = identity.verify_registered(self.original_root, source)
            files = tuple((name, (self.original_root / row['path']).read_bytes()) for name, row in registered['files'].items())
            identity.register(self.root, identity.SourceSnapshot(source, self.base, files))
            if source == self.campaign['collection_source_id']:
                for name, raw in files:
                    path = self.root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
        archive = Path(self.campaign['retained_calibration_archive'])
        for relative in (archive, archive.parent / 'manifest.json',
                         Path(self.campaign['retained_calibration_pilot']), Path(verify.MODEL_ARTIFACT_PATH),
                         Path('v2/cpu_campaigns.py'), Path('v2/calibration.py'), Path('v2/verify.py')):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((self.original_root / relative).read_bytes())
        self.snapshot = identity.capture(self.root, git_base=self.base)
        self.assertEqual(self.snapshot.source_id, self.campaign['collection_source_id'])
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(verify, 'ROOT', self.root))
        stack.enter_context(patch.object(freezer, 'ROOT', self.root))
        stack.enter_context(patch.object(freezer, 'capture', return_value=self.snapshot))

    def check(self):
        return verify.verify_retained_cpu_calibration(self.prefix, self.snapshot.source_id, self.base,
            pilot_path=Path(self.campaign['retained_calibration_pilot']))

    def changed_source(self, name, transform):
        path = self.root / name
        path.write_bytes(transform(path.read_bytes()))
        source = identity.capture(self.root, git_base=self.base)
        identity.register(self.root, source)
        return source.source_id

    def test_real_archived_pilot_keeps_c811_and_reproduces_exact_window_without_generation(self):
        result = self.check()
        self.assertTrue(result['valid'])
        self.assertEqual(result['measurement_seconds'], 335)
        self.assertEqual(result['calibration_pilot_sha256'], self.campaign['calibration_pilot_sha256'])
        self.assertNotEqual(result['calibration_source_id'], result['collection_source_id'])
        self.assertEqual(len(result['source_compatibility']['unchanged_files']), 16)

    def test_another_driver_identity_cannot_adopt_the_pilot(self):
        source = self.changed_source('v2/model.py', lambda raw: raw + b'\n# Unapproved source variant\n')
        with self.assertRaisesRegex(ValueError, 'exact registered'):
            calibration.verify_calibration_compatibility(self.root, self.campaign['calibration_source_id'], source)

    def test_pilot_ast_and_raw_body_changes_are_rejected_even_inside_an_allowed_model_file(self):
        before = identity.verify_registered(self.root, self.campaign['calibration_source_id'])
        old = (self.root / before['files']['v2/model.py']['path']).read_bytes()
        new = (self.root / 'v2/model.py').read_bytes()
        for changed in (new.replace(b'long = (index // 2) % 2', b'long = 0', 1),
                        new.replace(b'    long = (index // 2) % 2', b'    # Unapproved pilot comment\n    long = (index // 2) % 2', 1),
                        new + b'\ndef unapproved_helper():\n    pass\n'):
            with self.subTest(changed=changed != new), self.assertRaisesRegex(ValueError, 'calibration nodes changed'):
                calibration._model_delta(old, changed)

    def test_proxy_target_payload_timeout_and_original_result_must_be_preserved(self):
        before = identity.verify_registered(self.root, self.campaign['calibration_source_id'])
        old = (self.root / before['files']['v2/inference_proxy.py']['path']).read_bytes()
        new = (self.root / 'v2/inference_proxy.py').read_bytes()
        for changed in (new.replace(b'timeout=180', b'timeout=181', 1),
                        new.replace(b"payload if method == 'POST' else None", b"{} if method == 'POST' else None", 1),
                        new.replace(b'            return result', b'            return (status, response)', 1)):
            with self.subTest(changed=changed != new), self.assertRaises(ValueError):
                calibration._proxy_delta(old, changed)

    def test_changed_workload_options_or_other_instrument_bytes_are_rejected(self):
        source = self.changed_source('v2/workload.py', lambda raw: raw + b'\n# Changed workload\n')
        with self.assertRaisesRegex(ValueError, 'exact registered'):
            calibration.verify_calibration_compatibility(self.root, self.campaign['calibration_source_id'], source)
        with self.assertRaisesRegex(ValueError, 'exact registered'):
            calibration.verify_calibration_compatibility(self.root, self.snapshot.source_id, self.snapshot.source_id)

    def test_raw_pilot_or_archive_substitution_is_rejected(self):
        path = self.root / self.campaign['retained_calibration_pilot']
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'raw pilot differs'):
            self.check()
        path.write_bytes(original)
        archive = self.root / self.campaign['retained_calibration_archive']
        archive.write_bytes(archive.read_bytes() + b'\n')
        with self.assertRaises((ValueError, OSError, EOFError)):
            self.check()

    def test_rewriting_both_archive_and_manifest_cannot_replace_the_registered_calibration(self):
        archive = self.root / self.campaign['retained_calibration_archive']
        raw = gzip.decompress(archive.read_bytes()) + b'\n'
        encoded = gzip.compress(raw, mtime=0)
        archive.write_bytes(encoded)
        manifest = archive.parent / 'manifest.json'
        data = json.loads(manifest.read_bytes())
        data['files'][0].update(sha256=hashlib.sha256(encoded).hexdigest(),
                               decoded_sha256=hashlib.sha256(raw).hexdigest(), decoded_bytes=len(raw))
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'original manifest bytes changed'):
            self.check()
        with self.assertRaisesRegex(ValueError, 'registered extended collection source'):
            verify.verify_retained_cpu_calibration(self.prefix, self.campaign['calibration_source_id'], self.base)

    def test_window_is_exact_not_a_global_relaxation_of_the_old_cap(self):
        self.assertEqual(verify.validate_cpu_window(335, self.prefix), 335)
        for value in (120, 330, 336, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify.validate_cpu_window(value, self.prefix)
        with self.assertRaisesRegex(ValueError, '120-second'):
            verify.validate_cpu_window(335)

    def test_freeze_reuses_original_bytes_and_offline_replay_checks_the_source_declaration(self):
        preserved = {relative: (self.root / relative).read_bytes() for relative in (
            self.campaign['retained_calibration_pilot'], self.campaign['retained_calibration_archive'],
            str(Path(self.campaign['retained_calibration_archive']).parent / 'manifest.json'))}
        result = freezer.freeze_campaign(Path(self.campaign['retained_calibration_pilot']),
            Path(self.campaign['plan_path']), cohort_prefix=self.prefix)
        self.assertEqual(result['measurement_seconds'], 335)
        plan = json.loads((self.root / self.campaign['plan_path']).read_bytes())
        registration = plan['continuation']
        self.assertEqual(registration['pilot_archive'], self.campaign['retained_calibration_archive'])
        self.assertEqual(registration['calibration_source_id'], self.campaign['calibration_source_id'])
        self.assertEqual(registration['collection_source_id'], self.snapshot.source_id)
        self.assertFalse((self.root / 'archives' / (self.prefix + '-pilot')).exists())
        self.assertTrue(all((self.root / relative).read_bytes() == raw for relative, raw in preserved.items()))
        verify.replay_cpu_plan(plan)
        self.assertTrue(freezer.check_campaign(Path(self.campaign['plan_path']))['checked'])
        for key in ('calibration_source_id', 'collection_source_id', 'calibration_pilot_sha256'):
            changed = copy.deepcopy(plan)
            changed['continuation'][key] = 'tampered'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'calibration declaration'):
                verify.replay_cpu_plan(changed)
        changed = copy.deepcopy(plan)
        changed['continuation']['pilot_archive'] = 'archives/duplicate/pilot.json.gz'
        with self.assertRaisesRegex(ValueError, 'original archive'):
            verify.replay_cpu_plan(changed)

    def test_extended_comparison_still_needs_its_own_complete_gates_under_the_new_source(self):
        freezer.freeze_campaign(Path(self.campaign['retained_calibration_pilot']),
            Path(self.campaign['plan_path']), cohort_prefix=self.prefix)
        plan = json.loads((self.root / self.campaign['plan_path']).read_bytes())
        plan_hash = verify.digest((self.root / self.campaign['plan_path']).read_bytes())
        fixture = gate_fixtures.ContinuationGateTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        archives = {self.prefix + name.removeprefix(fixture.prefix): copy.deepcopy(rows)
                    for name, rows in fixture.archives.items()}
        pilot, _ = verify.load_cpu_pilot(self.prefix)
        metadata = copy.deepcopy(pilot['metadata'])
        current = {'source_id': self.snapshot.source_id, 'git_base': self.base, 'source_commit': self.base}
        metadata['inference_environment'].update(current)
        began = datetime.fromisoformat(plan['continuation']['frozen_utc'])
        for stage, minutes in (('network', 1), ('qualification', 2), ('warmup', 4), ('real-model', 5)):
            data = archives[self.prefix + '-' + stage][0]
            data.update(current, utc=(began + timedelta(minutes=minutes)).isoformat(), metadata=metadata)
            if stage == 'network':
                data.update(endpoint=self.campaign['inference_url'] + '/transport-control',
                            plan_path=self.campaign['plan_path'], plan_sha256=plan_hash)
            elif stage == 'warmup':
                data['loaded_model'] = copy.deepcopy(pilot['loaded_model'])
            else:
                data['plan_sha256'] = plan_hash
                block_name = 'model-qualification' if stage == 'qualification' else 'real-model'
                for trial, config in zip(archives[self.prefix + '-' + stage][2:], plan['blocks'][block_name], strict=True):
                    trial['configuration'] = copy.deepcopy(config)
                    trial['inference_observer_metadata'].update(current)
                    trial['native_observations'] = [{'linux_cgroups': {
                        'native': copy.deepcopy(metadata['inference_environment']['hardware']['observer_cgroup'])}}]
                    trial['model_after_trial'] = copy.deepcopy(pilot['loaded_model'])
        with patch.object(verify, 'replay_network_control', return_value=True), \
                patch.object(verify, 'replay_continuation_trial_gate', side_effect=lambda *args: []), \
                patch.object(verify, 'replay_native_completion_certificate', return_value=True):
            self.assertTrue(verify.audit_plain_continuation(archives, prefix=self.prefix)['eligible'])
            with patch.object(verify, 'replay_native_completion_certificate', return_value=False), \
                    self.assertRaisesRegex(ValueError, 'native_completion_not_certified'):
                verify.audit_plain_continuation(archives, prefix=self.prefix)
            original = archives.pop(self.prefix + '-network')
            archives['v2-ollama-fra6s004-cpu-confirmation-network'] = original
            with self.assertRaisesRegex(ValueError, 'prerequisites'):
                verify.audit_plain_continuation(archives, prefix=self.prefix)


if __name__ == '__main__':
    unittest.main()
