"""CI separates a valid protocol from a completed paired observation grid."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_shared_admission as checker
from v2 import admission_study as study
from v2 import report as legacy_report


class AdmissionCheckTests(unittest.TestCase):
    def fixtures(self):
        cases = []
        for pair in range(12):
            for mode in ('bypass', 'global'):
                identifier = f'synthetic-pair-{pair}-{mode}'
                directory = 'runs/synthetic/trials/' + identifier
                cases.append({'case_id': identifier, 'pair_id': f'synthetic-pair-{pair}', 'mode': mode,
                    'result_path': directory + '/real-model/' + identifier + '.json',
                    'recovery_path': directory + '/recovery.json', 'warmup_path': directory + '/warmup.json'})
        rows = [{**{key: case[key] for key in ('case_id', 'pair_id', 'mode')},
                 'status': 'observed', 'primary_observable': True, 'resource_qualified': True,
                 'execution_issues': [], 'native_issues': [], 'counts': {}} for case in cases]
        report = {'schema': 'alien6-shared-admission-analysis-v1', 'study_source_id': checker.SOURCE_ID,
            'complete': True, 'all_primary_observable': True, 'controls': {'qualified': True},
            'confirmatory_inference': False, 'parent_observations_pooled': False, 'trials': rows,
            'pairs': [{'pair_id': f'synthetic-pair-{pair}', 'complete': True} for pair in range(12)]}
        return {'run_root': 'runs/synthetic', 'cases': cases}, report

    def test_actual_protocol_passes_without_claiming_missing_experimental_results(self):
        absent = {'available_arm_files':0, 'planned_arms':24,
                  'missing_case_ids':[str(index) for index in range(24)],
                  'data_state':'absent','publication_artifacts_present':[], 'final_validation_required':False}
        with patch('v2.identity.subprocess.check_output', side_effect=AssertionError('Offline check must not query Git')), patch.object(checker,'data_availability',return_value=absent):
            report = checker.check(checker.ROOT, 'protocol')
        self.assertTrue(report['protocol_valid'])
        self.assertFalse(report['experimental_data_verified'])
        self.assertFalse(report['study_completion_verified'])
        self.assertEqual(report['availability']['available_arm_files'], 0)
        self.assertEqual(len(report['availability']['missing_case_ids']), 24)
        self.assertFalse(report['availability']['final_validation_required'])

    def test_final_mode_cannot_pass_without_all24_observation_files(self):
        manifest,_=self.fixtures()
        with tempfile.TemporaryDirectory() as tmp, patch.object(checker,'protocol_check',return_value=manifest):
            with self.assertRaisesRegex(ValueError, 'all 24 observed arm files'):
                checker.check(Path(tmp), 'final')

    def test_one_partial_arm_requires_final_verification_but_is_not_a_completed_study(self):
        manifest, _ = self.fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); result = root/manifest['cases'][0]['result_path']
            result.parent.mkdir(parents=True); result.write_bytes(b'{}')
            value = checker.data_availability(root, manifest)
        self.assertEqual(value['available_arm_files'], 1)
        self.assertEqual(value['data_state'], 'partial_unverified')
        self.assertTrue(value['final_validation_required'])

    def test_report_files_alone_trigger_final_validation_with_zero_raw_arms(self):
        manifest, _ = self.fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); report = root/checker.REPORT_DIRECTORY/'results.json'
            report.parent.mkdir(parents=True); report.write_bytes(b'{}')
            value = checker.data_availability(root, manifest)
        self.assertEqual(value['available_arm_files'], 0)
        self.assertTrue(value['final_validation_required'])

    def test_final_grid_rejects_missing_duplicate_defective_or_relabelled_arms(self):
        manifest, report = self.fixtures()
        self.assertEqual(checker.validate_complete_report(report, manifest), {'observed': 24})
        mutations = []
        bad = copy.deepcopy(report); bad['trials'].pop(); mutations.append(bad)
        bad = copy.deepcopy(report); bad['trials'][1] = bad['trials'][0]; mutations.append(bad)
        bad = copy.deepcopy(report); bad['trials'][0]['execution_issues'] = ['changed-source']; mutations.append(bad)
        bad = copy.deepcopy(report); bad['trials'][0]['mode'] = 'global'; mutations.append(bad)
        bad = copy.deepcopy(report); bad['pairs'][0]['complete'] = False; mutations.append(bad)
        bad = copy.deepcopy(report); bad['parent_observations_pooled'] = True; mutations.append(bad)
        bad = copy.deepcopy(report); bad['controls']['qualified'] = False; mutations.append(bad)
        for bad in mutations:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                checker.validate_complete_report(bad, manifest)

    def test_native_censoring_is_retained_separately_from_client_coverage(self):
        manifest, report = self.fixtures()
        report['trials'][0].update(status='resource_censored', resource_qualified=False,
                                  native_issues=['synthetic native completion unavailable'])
        self.assertEqual(checker.validate_complete_report(report, manifest), {'resource_censored': 1, 'observed': 23})
        report['trials'][0]['primary_observable'] = False
        with self.assertRaises(ValueError): checker.validate_complete_report(report, manifest)

    def test_paired_study_is_not_added_to_the_historical_emulator_condition_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();folder=root/'analysis/v2-ollama-shared-admission';folder.mkdir(parents=True)
            (folder/'results.json').write_text(json.dumps({'schema':'alien6-shared-admission-analysis-v1',
                'included_for_inference':True,'trials':[{'synthetic_paired_arm':True}]}))
            with patch.object(legacy_report,'ROOT',root): result=legacy_report.prepare()
        self.assertEqual(result['datasets'],{})
        self.assertEqual(result['contrasts'],[])

    def test_export_inventory_is_exact_and_rejects_changed_duplicate_or_escaping_raw_paths(self):
        manifest = {'run_root': 'runs/synthetic', 'cases': []}
        required = ('initial-recovery.json','network/control.json','qualification/block.json',
                    'qualification/manifest.json','study-control-binding.json')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); rows = []
            for name in required:
                path = root/manifest['run_root']/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'{}')
                rows.append({'path':path.relative_to(root).as_posix(),'bytes':2,'sha256':hashlib.sha256(b'{}').hexdigest()})
            inventory = {'archive_sha256':'a'*64,'files':rows}; target=root/checker.COLLECTION
            target.parent.mkdir(parents=True); target.write_text(json.dumps(inventory))
            self.assertEqual(checker.verify_collection_inventory(root,manifest)['raw_files'],5)
            for mutation in ('changed','duplicate','escape'):
                changed=copy.deepcopy(inventory)
                if mutation=='changed': changed['files'][0]['sha256']='0'*64
                if mutation=='duplicate': changed['files'].append(changed['files'][0])
                if mutation=='escape': changed['files'][0]['path']='runs/synthetic/../../outside'
                target.write_text(json.dumps(changed))
                with self.subTest(mutation=mutation),self.assertRaises((ValueError,OSError)):
                    checker.verify_collection_inventory(root,manifest)

    def test_csv_matches_the_frozen_report_writer_including_line_endings(self):
        _, report = self.fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'report'
            with patch.object(study, 'analyze_study', return_value=report):
                study.write_analysis('synthetic-manifest',target)
            self.assertEqual(checker.csv_bytes(report),(target/'trials.csv').read_bytes())

    def test_final_mode_compares_the_entire_report_and_exact_csv_without_ignoring_fields(self):
        manifest, report = self.fixtures()
        availability={'available_arm_files':24}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); folder=root/checker.REPORT_DIRECTORY;folder.mkdir(parents=True)
            target=folder/'results.json';target.write_text(json.dumps(report))
            table=folder/'trials.csv';table.write_bytes(checker.csv_bytes(report))
            with patch.object(checker,'protocol_check',return_value=manifest), patch.object(checker,'data_availability',return_value=availability), patch.object(checker,'verify_collection_inventory',return_value={'raw_files':1}), patch.object(study,'analyze_study',return_value=report):
                self.assertTrue(checker.check(root,'final')['study_completion_verified'])
                changed=copy.deepcopy(report);changed['extra_unreplayed_claim']=True
                target.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError,'report differs'):checker.check(root,'final')
                target.write_text(json.dumps(report));table.write_bytes(checker.csv_bytes(report)+b'\n')
                with self.assertRaisesRegex(ValueError,'CSV differs'):checker.check(root,'final')

    def test_ci_final_step_is_conditioned_on_each_actual_publication_or_raw_artifact(self):
        workflow=(checker.ROOT/'.github/workflows/ci.yml').read_text()
        self.assertIn('--mode protocol --github-summary',workflow)
        self.assertIn('--mode final --github-summary',workflow)
        self.assertIn("hashFiles('"+checker.PUBLIC_INDEX+"'",workflow)
        self.assertIn(checker.COLLECTION,workflow)
        self.assertIn(checker.REPORT_DIRECTORY+'/results.json',workflow)
        self.assertIn(checker.REPORT_DIRECTORY+'/trials.csv',workflow)
        self.assertIn('runs/shared-admission/trials/*/real-model/*.json',workflow)

    def test_lossless_pack_restores_exact_bytes_idempotently_and_refuses_changed_originals(self):
        manifest={'run_root':'runs/synthetic','git_base':'a'*40}
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            root, restored=Path(first).resolve(),Path(second).resolve()
            rows=[]; before={}
            for name,raw in [('one.json',b'{"response":"retained"}\n'),('two.json',b'{"native":"complete"}\n')]:
                path=root/'runs/synthetic'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                rows.append({'path':path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':checker.sha(raw)})
                before[path]=path.stat()
            collection={'archive_sha256':checker.ORIGINAL_ARCHIVE_SHA256,'files':rows}
            inventory=root/checker.COLLECTION;inventory.parent.mkdir(parents=True);inventory.write_text(json.dumps(collection))
            with patch.object(checker,'protocol_check',return_value=manifest),patch.object(checker,'EXPECTED_RAW_FILES',2),patch.object(checker,'COLLECTION_SHA256',checker.sha(inventory.read_bytes())),patch.object(checker,'check',return_value={'study_completion_verified':True}):
                result=checker.pack(root)
                self.assertEqual(result['compressed_files'],2)
                index=json.loads((root/checker.PUBLIC_INDEX).read_bytes())
                for name in [checker.COLLECTION,checker.PUBLIC_INDEX,*[row['path'] for row in index['files']]]:
                    target=restored/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,target)
                self.assertFalse((restored/'runs').exists())
                self.assertEqual(checker.restore(restored)['restored_raw_files'],2)
                self.assertEqual(checker.restore(restored)['restored_raw_files'],0)
                for row in rows:
                    self.assertEqual((root/row['path']).read_bytes(),(restored/row['path']).read_bytes())
                bad=restored/rows[0]['path'];bad.chmod(0o644);bad.write_bytes(b'prior different evidence')
                with self.assertRaisesRegex(ValueError,'Existing decoded evidence differs'):checker.restore(restored)
                self.assertEqual(bad.read_bytes(),b'prior different evidence')
                index['files'][0]['decoded_sha256']='0'*64
                target=restored/checker.PUBLIC_INDEX;target.chmod(0o644);target.write_text(json.dumps(index))
                with self.assertRaisesRegex(ValueError,'original raw SHA'):checker.restore(restored)
            for path,stat in before.items():
                after=path.stat();self.assertEqual((stat.st_ino,stat.st_mtime_ns),(after.st_ino,after.st_mtime_ns))


if __name__ == '__main__': unittest.main()
