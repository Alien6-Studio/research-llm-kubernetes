"""Prospective paired plans and full-arrival metrics use no generated outcomes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from v2 import admission_study as study
from v2.identity import capture, register
from v2.plan import ROOT


class AdmissionStudyTests(unittest.TestCase):
    def setUp(self):
        self.parent = json.loads((ROOT/'protocol/ollama-fra6s004-cpu-long-window-plan.json').read_bytes())

    def test_capacity_uses_original_latency_groups_and_marks_failed_deadline_feasibility(self):
        pilot = json.loads((ROOT/study.PILOT_PATH).read_bytes())
        selected = study.select_capacity(pilot)
        self.assertEqual(selected['capacity'], 1)
        self.assertFalse(selected['pilot_deadline_feasible'])
        self.assertEqual([row['requests'] for row in selected['groups']], [16, 16, 16])
        self.assertEqual([row['within_deadline'] for row in selected['groups']], [8, 0, 0])
        self.assertAlmostEqual(selected['groups'][0]['p95_seconds'], 11.098176331)
        original = copy.deepcopy(pilot); pilot['source_id'] = 'other-source'
        with self.assertRaises(ValueError): study.select_capacity(pilot)
        self.assertEqual(original['valid'], True)

    def test_deterministic24_case_matrix_preserves_every_config_except_new_trial_id(self):
        cases = study.cases(self.parent, 1)
        self.assertEqual(len(cases), 24)
        self.assertEqual(study.cases(self.parent, 1), cases)
        originals = {row['trial_id']: row for row in self.parent['blocks']['real-model']}
        pairs = {}
        for row in cases:
            actual = copy.deepcopy(row['configuration']); actual['trial_id'] = row['parent_trial_id']
            self.assertEqual(actual, originals[row['parent_trial_id']])
            pairs.setdefault(row['pair_id'], []).append(row)
        self.assertEqual(len(pairs), 12)
        for index in range(0,24,2):
            self.assertEqual(cases[index]['pair_id'], cases[index+1]['pair_id'])
            self.assertEqual({cases[index]['mode'], cases[index+1]['mode']}, {'global','bypass'})
            self.assertEqual(cases[index]['configuration']['trace_reference'], cases[index+1]['configuration']['trace_reference'])
        changed = copy.deepcopy(self.parent); changed['blocks']['real-model'] = [row for row in changed['blocks']['real-model'] if row['trial_id'] != cases[0]['parent_trial_id']]
        with self.assertRaises(ValueError): study.cases(changed, 1)

    def test_actual_archived_inputs_have12_retained_traces_without_generating_new_ones(self):
        parent, plan, _, selected, traces = study.inputs(ROOT, 'protocol/ollama-fra6s004-cpu-continuation/manifest.json')
        self.assertEqual(parent['sha256'], study.PARENT_MANIFEST_SHA256)
        self.assertEqual(plan['sha256'], study.PARENT_PLAN_SHA256)
        self.assertEqual(len(traces), 12)
        self.assertEqual(selected['capacity'], 1)

    def test_offline_source_check_uses_archived_git_base_and_rejects_changed_base_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'base').write_bytes(b'preserved measurement'); (root/'extension').write_bytes(b'new study')
            git_base = 'a'*40
            baseline = capture(root, files=['base'], git_base=git_base); register(root, baseline)
            extended = capture(root, files=['base', 'extension'], git_base=git_base); register(root, extended)
            with patch.object(study, 'MEASURED_FILES', ('base',)), patch.object(study, 'STUDY_FILES', ('base','extension')), patch.object(study, 'BASE_SOURCE_ID', baseline.source_id), patch('v2.identity.subprocess.check_output', side_effect=AssertionError('Offline must not read HEAD')):
                self.assertEqual(study.verify_sources(root, extended.source_id, git_base).source_id, extended.source_id)
                (root/'base').write_bytes(b'changed measurement')
                changed = capture(root, files=['base','extension'], git_base=git_base); register(root, changed)
                with self.assertRaisesRegex(ValueError, 'instrument changed'):
                    study.verify_sources(root, changed.source_id, git_base)

    def test_prepare_is_exclusive_and_check_rejects_changed_arm_or_selected_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'fixture-source').write_bytes(b'synthetic source')
            git_base = 'a'*40
            snapshot = capture(root, files=['fixture-source'], git_base=git_base)
            selection = {'capacity':1}
            pilot = root/study.PILOT_PATH; pilot.parent.mkdir(parents=True); pilot.write_text(json.dumps({'git_base':git_base}))
            pinned = ({'path':'parent-manifest','sha256':'1'*64,'bytes':1},
                {'path':'parent-plan','sha256':'2'*64,'bytes':1}, self.parent, selection, [])
            target = root/'protocol/new-study/manifest.json'
            with patch.object(study, 'STUDY_FILES', ('fixture-source',)), patch.object(study, 'capture', return_value=snapshot), patch.object(study, 'verify_sources', return_value=snapshot), patch.object(study, 'inputs', return_value=pinned):
                manifest = study.prepare_study('parent-manifest', target, study_source_id=snapshot.source_id, root=root)
                self.assertEqual(len(manifest['cases']), 24)
                self.assertTrue(manifest['created_utc'].endswith('+00:00'))
                self.assertFalse((root/manifest['run_root']).exists())
                self.assertEqual(study.check_study(target, root=root), manifest)
                with self.assertRaises(FileExistsError):
                    study.prepare_study('parent-manifest', target, study_source_id=snapshot.source_id, root=root)
                modified = copy.deepcopy(manifest); modified['cases'][0]['mode'] = 'unregistered'
                target.chmod(0o644); target.write_text(json.dumps(modified))
                with self.assertRaisesRegex(ValueError,'Paired condition'):
                    study.check_study(target, root=root)
                target.write_text(json.dumps(manifest))
                selected = root/manifest['cases'][0]['plan_path']; selected.chmod(0o644)
                selected.write_bytes(selected.read_bytes()+b' ')
                with self.assertRaisesRegex(ValueError,'Selected plan'):
                    study.check_study(target, root=root)

    def test_actual_source_and_inputs_prepare_and_replay24_pending_cases_in_isolated_fixture(self):
        # The source registry, selected plans and report exist only in TemporaryDirectory.
        # Original scientific input bytes are copied unchanged; no observations are generated.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = 'protocol/ollama-fra6s004-cpu-continuation/manifest.json'
            _, plan_record, _, _, traces = study.inputs(ROOT, parent_path)
            paths = {*study.STUDY_FILES, parent_path, plan_record['path'], study.PILOT_PATH,
                     *(row['path'] for row in traces)}
            for name in paths:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((ROOT/name).read_bytes())
            git_base = json.loads((root/study.PILOT_PATH).read_bytes())['git_base']
            baseline = capture(root, git_base=git_base); register(root, baseline)
            extended = capture(root, files=study.STUDY_FILES, git_base=git_base); register(root, extended)
            target = root/'protocol/test-admission/manifest.json'
            with patch('v2.identity.subprocess.check_output', return_value=git_base):
                manifest = study.prepare_study(parent_path, target, study_source_id=extended.source_id, root=root)
            with patch('v2.identity.subprocess.check_output', side_effect=AssertionError('Replay must not inspect current HEAD')):
                self.assertEqual(study.check_study(target, root=root), manifest)
                report = study.analyze_study(target, root=root)
            self.assertEqual(len(report['trials']), 24)
            self.assertEqual(len(report['pairs']), 12)
            self.assertTrue(all(row['status'] == 'pending' for row in report['trials']))
            self.assertFalse(report['complete'])
            self.assertFalse(report['controls']['qualified'])
            self.assertFalse(report['all_primary_observable'])
            self.assertFalse(report['parent_observations_pooled'])
            self.assertFalse(report['confirmatory_inference'])

    def test_control_binding_requires_all16_hashes_new_source_empty_epoch_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); run = root/'runs/new'
            (run/'network').mkdir(parents=True); (run/'qualification').mkdir()
            state = {'clock_domain':'fresh-study-epoch','active':[],'started_total':0}
            recovery = {'started_utc':'2026-11-15T08:01:00+00:00','finished_utc':'2026-11-15T08:02:00+00:00','post_admission_state':state}
            network = {'utc':'2026-11-15T08:03:00+00:00'}
            collection = {'utc':'2026-11-15T08:04:00+00:00'}
            (run/'initial-recovery.json').write_bytes(study.encoded(recovery))
            (run/'network/control.json').write_bytes(study.encoded(network))
            (run/'qualification/manifest.json').write_bytes(study.encoded(collection))
            (run/'qualification/block.json').write_bytes(b'{}')
            for index in range(12): (run/f'qualification/trial-{index}.json').write_bytes(b'{}')
            manifest = {'created_utc':'2026-11-15T08:00:00+00:00','run_root':'runs/new','study_source_id':'new-study','git_base':'a'*40}
            paths = [run/'network/control.json',run/'initial-recovery.json',*sorted((run/'qualification').glob('*.json'))]
            binding = {'schema':'alien6-admission-control-binding-v1','source_id':'new-study',
                'base_source_id':study.BASE_SOURCE_ID,'git_base':'a'*40,'manifest_sha256':study.digest(study.encoded(manifest)),
                'created_utc':'2026-11-15T08:05:00+00:00','files':[study.file_record(root,p) for p in paths],
                'after_controls_admission_state':state}
            target = run/'study-control-binding.json'; target.write_bytes(study.encoded(binding))
            verifier = SimpleNamespace(verify_admission_recovery_record=lambda *args: {'valid':True})
            with patch.object(study,'recovery_module',return_value=verifier):
                self.assertTrue(study.control_binding(root,manifest,network,collection)['sha256'])
                for key,value in [('source_id','old-study'),('created_utc','2026-11-14T08:00:00+00:00'),('after_controls_admission_state',{'started_total':1})]:
                    bad=copy.deepcopy(binding);bad[key]=value;target.write_bytes(study.encoded(bad))
                    with self.assertRaises(ValueError): study.control_binding(root,manifest,network,collection)
                target.write_bytes(study.encoded(binding))
                (run/'qualification/trial-0.json').write_bytes(b'{"changed":true}')
                with self.assertRaisesRegex(ValueError,'sixteen exact'):
                    study.control_binding(root,manifest,network,collection)

    def test_case_manifest_cannot_change_identity_plan_component_image_or_node(self):
        manifest = {'study_source_id':'new-source','git_base':'a'*40}
        case = {'plan_sha256':'b'*64}
        snapshot = {'files':{name:{'sha256':str(index)*64} for index,name in enumerate(
            ('v2/admission_driver.py','v2/admission_proxy.py','v2/model_service.py'),start=1)}}
        controls = {'image_id':'sha256:'+'f'*64,'binding':{'created_utc':'2026-11-15T08:00:00+00:00'}}
        collection = {'schema':'alien6-admission-case-v1','source_id':'new-source','base_source_id':study.BASE_SOURCE_ID,
            'git_base':'a'*40,'plan_sha256':'b'*64,'driver_sha256':'1'*64,'admission_proxy_sha256':'2'*64,
            'model_controller_sha256':'3'*64,'node_limits':{'nano_cpus':4_000_000_000,'memory_bytes':8*1024**3},
            'image_id':controls['image_id'],'utc':'2026-11-15T08:01:00+00:00'}
        study.verify_case_collection(collection,case,manifest,snapshot,controls)
        for key,value in [('source_id','wrong-source'),('base_source_id','wrong-base'),('git_base','c'*40),
                          ('plan_sha256','c'*64),('driver_sha256','c'*64),('admission_proxy_sha256','c'*64),
                          ('model_controller_sha256','c'*64),('image_id','sha256:'+'e'*64),
                          ('node_limits',{'nano_cpus':8_000_000_000,'memory_bytes':8*1024**3}),
                          ('utc','2026-11-15T07:59:00+00:00')]:
            bad=copy.deepcopy(collection);bad[key]=value
            with self.subTest(field=key),self.assertRaises(ValueError):
                study.verify_case_collection(bad,case,manifest,snapshot,controls)

    def test_primary_denominator_includes_rejection_unemitted_timeout_and_late_success(self):
        trace = [{'id': str(i), 'offset': 5+i, 'task_status':200} for i in range(5)]
        rows = []
        for i, (status, correct, latency) in enumerate([(200,True,9),(429,False,1),(0,False,10),(200,True,11),(0,False,10)]):
            rows.append({'id':str(i),'status':status,'correct':correct,'latency_seconds':latency,
                'scheduled':100+5+i,'finished':100+5+i+latency,
                'response':{'inference':{'response':'{"label":"ok"}'},'correct':True} if status==200 else {}})
        doc = {'configuration':{'trace':trace,'measurement_seconds':335,'deadline_seconds':10,'warmup_seconds':5}, 'result':{'start':100,'requests':rows}}
        metrics = study.primary_metrics(doc)
        self.assertEqual(metrics['measurement_arrivals'], 5)
        self.assertEqual(metrics['timely_correct_arrivals'], 1)
        self.assertEqual(metrics['timely_correct_fraction'], .2)
        self.assertEqual(metrics['http429_arrivals'], 1)
        doc['result']['requests'][1]['correct'] = True
        with self.assertRaises(ValueError): study.primary_metrics(doc)

    def test_pairs_keep_pending_or_invalid_arm_visible_without_survivor_selection(self):
        rows = [{'pair_id':'high-8-0','case_id':'b','mode':'bypass','primary_observable':True,
                 'measurement_arrivals':10,'timely_correct_fraction':.2,'timely_correct_per_second':2/335},
                {'pair_id':'high-8-0','case_id':'g','mode':'global','primary_observable':False}]
        self.assertIsNone(study.paired_summary(rows)[0]['global_minus_bypass_fraction'])
        rows[1].update(primary_observable=True,measurement_arrivals=10,timely_correct_fraction=.5,timely_correct_per_second=5/335)
        self.assertAlmostEqual(study.paired_summary(rows)[0]['global_minus_bypass_fraction'], .3)
        rows[1]['measurement_arrivals'] = 9
        with self.assertRaises(ValueError): study.paired_summary(rows)


if __name__ == '__main__': unittest.main()
