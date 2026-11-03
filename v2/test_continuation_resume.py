"""Suffix selection preserves failures; fixtures never generate model results."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from v2 import continuation
from v2.identity import MEASURED_FILES
from v2.identity import capture as capture_exact_bytes
from v2.plan import canonical


class ResumeManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parent = self.root/'protocol/parent.json'
        self.parent.parent.mkdir()
        trace = [{'id': 'synthetic-arrival', 'offset': 10, 'tokens': 1000}]
        raw_trace = gzip.compress(canonical(trace), mtime=0)
        reference = {'path': 'protocol/trace.json.gz', 'sha256': continuation.digest(raw_trace)}
        (self.root/reference['path']).write_bytes(raw_trace)
        retained = sorted(name[:-5] for name in continuation.PRIOR_SHA256
                          if name not in ('block.json', 'manifest.json', continuation.FAILED_TRIAL+'.json'))
        identifiers = retained + [continuation.FAILED_TRIAL] + ['unattempted-'+str(i) for i in range(28)]
        self.plan = {'blocks': {'real-model': [{'trial_id': identifier, 'block': 'real-model',
            'measurement_seconds': 335, 'deadline_seconds': 10, 'trace_reference': reference}
            for identifier in identifiers]}, 'continuation': {'source_id': continuation.PARENT_SOURCE_ID,
            'git_base': continuation.PARENT_GIT_BASE, 'method_sources': [{'path':'v2/verify.py'}],
            'source_compatibility': {'collection_proxy_sha256': 'd'*64}}}
        self.parent.write_text(json.dumps(self.plan))
        self.patch('PARENT_PLAN_SHA256', continuation.digest(self.parent.read_bytes()))
        self.prior = self.root/'runs/parent-real-model'
        self.prior.mkdir(parents=True)
        self.target = self.root/'protocol/continued/manifest.json'
        for name in MEASURED_FILES:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# synthetic instrument fixture\n')
        verifier = self.root/'v2/verify.py'
        verifier.write_bytes(b'# synthetic verifier fixture\n')
        self.patch('VERIFIER_SHA256', continuation.digest(verifier.read_bytes()))
        methods = set(continuation.METHOD_FILES) | {v['path'] for v in self.plan['continuation']['method_sources']}
        for name in methods:
            path = self.root/name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'# synthetic method fixture\n')
        identity = {'source_id': continuation.PARENT_SOURCE_ID, 'git_base': continuation.PARENT_GIT_BASE}
        metadata = {**identity, 'source_commit': identity['git_base'],
                    'plan_sha256': continuation.PARENT_PLAN_SHA256, 'model_controller_sha256': 'c'*64}
        self.write('manifest.json', metadata)
        self.write('block.json', {'complete': False, 'qualified': False, 'trials': 17,
                                 'invalid': [continuation.FAILED_TRIAL], 'reason': 'native_completion_not_certified'})
        for index, cfg in enumerate(self.plan['blocks']['real-model'][:17]):
            trace = json.loads(gzip.decompress((self.root/cfg['trace_reference']['path']).read_bytes()))
            document = {'configuration': {**cfg, 'trace': trace,
                'trace_decoded_sha256': hashlib.sha256(canonical(trace)).hexdigest(), 'worker_addresses': []},
                'result': {}, 'valid': index < 16,
                'issues': [] if index < 16 else ['native_completion_not_certified'],
                'native_completion_certificate': {'valid': index < 16}}
            self.write(cfg['trial_id']+'.json', document)
        self.hashes = {p.name: continuation.digest(p.read_bytes()) for p in self.prior.iterdir()}
        self.patch('PRIOR_SHA256', self.hashes)
        snapshot = SimpleNamespace(**identity, files=tuple((name,b'') for name in MEASURED_FILES), metadata=lambda: identity)
        self.capture = self.patch('capture', return_value=snapshot)
        self.verify = self.patch('verify_worktree')
        self.replay = self.patch('replay_continuation_trial_gate', return_value=[])
        self.certify = self.patch('replay_native_completion_certificate', side_effect=lambda cfg,result,cert,ident: cert['valid'])

    def patch(self, name, *args, **kwargs):
        change = patch.object(continuation, name, *args, **kwargs)
        value = change.start()
        self.addCleanup(change.stop)
        return value

    def write(self, name, value):
        (self.prior/name).write_text(json.dumps(value))

    def prepare(self):
        return continuation.prepare_manifest(self.parent, self.prior, self.target, root=self.root)

    def rewrite_manifest(self, manifest):
        self.target.chmod(0o644)
        self.target.write_text(json.dumps(manifest))

    def test_prepare_checks_all_prefix_rows_and_selects_only_the_exact_28_suffix_in_order(self):
        before = {p.name: p.read_bytes() for p in self.prior.iterdir()}
        manifest = self.prepare()
        self.assertEqual(self.replay.call_count, 17)
        self.assertEqual(self.certify.call_count, 17)
        self.assertEqual(manifest['prior']['trials'], 17)
        self.assertEqual(manifest['prior']['valid'], 16)
        self.assertEqual(manifest['prior']['invalid'], [continuation.FAILED_TRIAL])
        self.assertFalse(manifest['protocol']['global_qualified'])
        self.assertFalse(manifest['protocol']['included_for_causal_inference'])
        self.assertEqual([v['parent_index'] for v in manifest['trials']], list(range(17,45)))
        self.assertEqual([v['trial_id'] for v in manifest['trials']],
                         [v['trial_id'] for v in self.plan['blocks']['real-model'][17:]])
        for row in manifest['trials']:
            selected = json.loads((self.root/row['plan_path']).read_bytes())
            self.assertEqual(selected['blocks'], {'real-model':[self.plan['blocks']['real-model'][row['parent_index']]]})
            self.assertEqual(selected['continuation_selection']['parent_plan'], manifest['parent_plan'])
            self.assertFalse((self.root/row['epoch_directory']).exists())
        self.assertEqual(continuation.check_manifest(self.target, root=self.root), manifest)
        self.assertEqual({p.name:p.read_bytes() for p in self.prior.iterdir()}, before)

    def test_exclusive_outputs_and_prior_missing_failed_trial_are_rejected(self):
        self.prepare()
        with self.assertRaises(FileExistsError): self.prepare()
        (self.prior/(continuation.FAILED_TRIAL+'.json')).unlink()
        with self.assertRaisesRegex(ValueError,'complete original prefix'):
            continuation.check_manifest(self.target, root=self.root)

    def test_offline_replay_keeps_recorded_execution_base_without_reading_current_git_head(self):
        archived = capture_exact_bytes(self.root, git_base=continuation.PARENT_GIT_BASE)
        with patch.object(continuation, 'capture', side_effect=capture_exact_bytes), \
                patch.object(continuation, 'PARENT_SOURCE_ID', archived.source_id), \
                patch('v2.identity.subprocess.check_output', side_effect=AssertionError('Offline replay must not query Git')):
            identity, instrument, methods = continuation.source_records(self.root, self.plan)
        self.assertEqual(identity, archived.metadata())
        self.assertEqual(identity['git_base'], continuation.PARENT_GIT_BASE)
        self.assertEqual(len(instrument), len(MEASURED_FILES))
        self.assertTrue(methods)

    def test_changed_prior_bytes_cannot_be_hidden_by_the_manifest(self):
        self.prepare()
        path=self.prior/'real-model-high-1-1.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'Prior evidence changed'):
            continuation.check_manifest(self.target, root=self.root, deep=False)

    def test_failing_replay_does_not_prepare_outputs(self):
        self.replay.return_value=['collection_source_identity_mismatch']
        with self.assertRaisesRegex(ValueError,'gates do not reproduce'): self.prepare()
        self.assertFalse(self.target.parent.exists())

    def test_reclassifying_original_failure_is_rejected_even_with_revised_fixture_hash(self):
        path=self.prior/(continuation.FAILED_TRIAL+'.json')
        data=json.loads(path.read_bytes());data['valid']=True;data['issues']=[]
        path.write_text(json.dumps(data));self.hashes[path.name]=continuation.digest(path.read_bytes())
        with self.assertRaisesRegex(ValueError,'seventeenth failed trial'): self.prepare()

    def test_suffix_reordering_and_unsupported_aggregate_claim_fail(self):
        manifest=self.prepare()
        changed=copy.deepcopy(manifest);changed['trials'][0],changed['trials'][1]=changed['trials'][1],changed['trials'][0]
        self.rewrite_manifest(changed)
        with self.assertRaisesRegex(ValueError,'reordered'):
            continuation.check_manifest(self.target, root=self.root)
        changed=copy.deepcopy(manifest);changed['protocol']['global_qualified']=True
        self.rewrite_manifest(changed)
        with self.assertRaisesRegex(ValueError,'unsupported qualification'):
            continuation.check_manifest(self.target, root=self.root)

    def test_changed_selected_configuration_and_source_methods_are_rejected(self):
        manifest=self.prepare()
        plan_path=self.root/manifest['trials'][0]['plan_path']
        plan_path.chmod(0o644)
        selected=json.loads(plan_path.read_bytes());selected['blocks']['real-model'][0]['deadline_seconds']=99
        raw=continuation.encoded(selected);plan_path.write_bytes(raw)
        manifest['trials'][0]['plan_sha256']=continuation.digest(raw)
        self.rewrite_manifest(manifest)
        with self.assertRaisesRegex(ValueError,'exact frozen configuration'):
            continuation.check_manifest(self.target, root=self.root)
        self.target.chmod(0o644)
        manifest['trials'][0]['plan_sha256']=continuation.digest(continuation.encoded(
            continuation.selection_plan(self.plan,manifest['parent_plan'],self.target.relative_to(self.root).as_posix(),manifest['created_utc'],17)))
        plan_path.write_bytes(continuation.encoded(continuation.selection_plan(self.plan,manifest['parent_plan'],self.target.relative_to(self.root).as_posix(),manifest['created_utc'],17)))
        self.rewrite_manifest(manifest)
        (self.root/'infra/recover-fra6s004-inference.py').write_bytes(b'# changed method\n')
        with self.assertRaisesRegex(ValueError,'method_files'):
            continuation.check_manifest(self.target, root=self.root)

    def test_symlinks_traversal_and_foreign_output_paths_are_rejected(self):
        manifest=self.prepare()
        changed=copy.deepcopy(manifest);changed['trials'][0]['recovery_path']='../outside.json'
        self.rewrite_manifest(changed)
        with self.assertRaisesRegex(ValueError,'repository-relative'):
            continuation.check_manifest(self.target, root=self.root)
        self.rewrite_manifest(manifest)
        original=self.root/manifest['trace_files'][0]['path']
        copy_path=original.with_name('copy.json.gz');original.rename(copy_path);original.symlink_to(copy_path.name)
        with self.assertRaisesRegex(ValueError,'symbolic links'):
            continuation.check_manifest(self.target, root=self.root)

    def test_fast_check_still_hashes_every_prior_byte_without_replaying_large_json(self):
        self.prepare()
        self.replay.reset_mock();self.certify.reset_mock()
        checked=continuation.check_manifest(self.target, root=self.root, deep=False)
        self.assertEqual(len(checked['trials']),28)
        self.replay.assert_not_called();self.certify.assert_not_called()

    def test_exact_verifier_and_measurement_bytes_must_stay_pinned(self):
        self.prepare()
        path=self.root/'v2/model.py'
        path.write_bytes(path.read_bytes()+b'# changed\n')
        with self.assertRaisesRegex(ValueError,'instrument_files'):
            continuation.check_manifest(self.target, root=self.root, deep=False)
        path.write_bytes(b'# synthetic instrument fixture\n')
        (self.root/'v2/verify.py').write_bytes(b'# replacement verifier\n')
        with self.assertRaisesRegex(ValueError,'preserved BBA verifier'):
            continuation.check_manifest(self.target, root=self.root, deep=False)

    def test_stored_absolute_and_noncanonical_paths_are_rejected(self):
        manifest=self.prepare()
        for value in (str(self.root/'runs/result.json'), 'runs//result.json', './runs/result.json'):
            changed=copy.deepcopy(manifest);changed['trials'][0]['result_path']=value
            self.rewrite_manifest(changed)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'repository-relative'):
                continuation.check_manifest(self.target, root=self.root, deep=False)


if __name__=='__main__':unittest.main()
