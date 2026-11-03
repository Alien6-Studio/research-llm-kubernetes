"""Artificial report fixtures test denominators, censorship and epoch separation."""
import copy
import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import report_cpu_continuation as report


def document():
    trace = [{'id': name, 'offset': offset, 'task_status': 200, 'prompt_text': 'Controlled note.'}
             for name, offset in (('warmup', 1), ('good', 5), ('late', 6), ('refused', 7), ('not-emitted', 8))]
    config = {'trial_id': 'synthetic-trial', 'block': 'real-model', 'warmup_seconds': 5,
        'measurement_seconds': 335, 'deadline_seconds': 10, 'direct_ollama': True, 'trace': trace}
    outcomes = []
    for item in trace:
        name = item['id']; scheduled = 100 + item['offset']
        good = name in ('warmup', 'good')
        row = {'id': name, 'scheduled': scheduled, 'finished': scheduled + (1 if good else 10),
               'latency_seconds': 1 if good else 10, 'emitted': None if name == 'not-emitted' else scheduled,
               'status': 200 if good else 429 if name == 'refused' else 0, 'correct': good}
        if good:
            row['response'] = {'inference': {'response': '{"label":"ok"}', 'done': True}, 'correct': True}
        elif name == 'late':
            row.update(reason='client_deadline', late_response={'status': 200,
                'inference': {'response': '{"label":"ok"}', 'done': True}, 'correct': True})
        elif name == 'refused':
            row['response'] = {'inference': {'error': 'busy'}, 'correct': False, 'reason': 'busy'}
        else:
            row['reason'] = 'generator_not_emitted_deadline'
        outcomes.append(row)
    return {'configuration': config, 'result': {'start': 100, 'requests': outcomes},
            'native_observations': [{'linux_cgroups': {'native': {}}}], 'valid': False,
            'issues': ['native_completion_not_certified'], 'native_completion_certificate': {'valid': False,
                'pre_state': {'clock_domain': 'synthetic-epoch'}}}


class ClientMetricTests(unittest.TestCase):
    def test_all_scheduled_arrivals_are_denominator_and_late_native_answer_does_not_help(self):
        data = document(); before = copy.deepcopy(data)
        metrics = report.primary_metrics(data)
        self.assertEqual(metrics['measurement_arrivals'], 4)
        self.assertEqual(metrics['timely_correct_arrivals'], 1)
        self.assertEqual(metrics['timely_correct_fraction'], .25)
        self.assertEqual(metrics['timely_correct_per_arrival_second'], 1 / 335)
        self.assertEqual(metrics['client_status_counts'], {'0': 2, '200': 1, '429': 1})
        self.assertEqual(data, before)

    def test_missing_duplicate_nonfinite_or_falsified_client_outcomes_fail_closed(self):
        for change in ('missing', 'duplicate', 'clock', 'correctness'):
            data = document()
            if change == 'missing': data['result']['requests'].pop()
            elif change == 'duplicate': data['result']['requests'][-1] = copy.deepcopy(data['result']['requests'][0])
            elif change == 'clock': data['result']['requests'][1]['scheduled'] = float('nan')
            else: data['result']['requests'][1]['response']['inference']['response'] = '{"label":"error"}'
            with self.subTest(change=change), self.assertRaises(ValueError):
                report.primary_metrics(data)

    def test_empty_measurement_denominator_remains_unknown(self):
        data = document()
        data['configuration']['trace'] = data['configuration']['trace'][:1]
        data['result']['requests'] = data['result']['requests'][:1]
        metrics = report.primary_metrics(data)
        self.assertEqual(metrics['measurement_arrivals'], 0)
        self.assertIsNone(metrics['timely_correct_fraction'])

    def test_native_uncertainty_keeps_observed_primary_metrics_separate_from_validity(self):
        data = document(); frozen = {k:v for k,v in data['configuration'].items() if k != 'trace'}
        metrics = report.primary_metrics(data)
        existing = {'offered': 4, 'useful': 1, 'useful_fraction': .25, 'useful_cohort_per_second': 1 / 335}
        with patch.object(report, 'replay_continuation_trial_gate', return_value=[]), \
                patch.object(report, 'verify_cpu_envelope'), patch.object(report, 'verify_cpu_metadata'), \
                patch.object(report, 'summarize', return_value=existing), \
                patch.object(report, 'replay_native_completion_certificate', return_value=False), \
                patch.object(report, 'account_incomplete_native', return_value={'classification':'native_completion_incomplete',
                    'strict_trial_valid':False,'expected_native_calls':4,'terminal_native_calls':3,
                    'uncertain_terminal_calls':1,'active_at_capture':1}):
            row = report.classify_document(data, {'model_controller_sha256':'c','metadata':{}}, frozen,
                {'source_id':'s','git_base':'b','proxy_sha256':'p'})
        self.assertFalse(row['bba_valid'])
        self.assertFalse(row['resource_qualified'])
        self.assertTrue(row['resource_envelope_qualified'])
        self.assertIn('native_resource_lifetime_censored',row['resource_issues'])
        self.assertEqual(row['primary_scope'], 'complete_arrival_cohort')
        self.assertEqual(row['timely_correct_fraction'], metrics['timely_correct_fraction'])
        self.assertEqual(row['execution_defects'], [])
        self.assertEqual(row['observation_status'], 'native_completion_uncertain')


class ObservationTableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.plan = {'blocks': {'real-model': []}}
        trace = document()['configuration']['trace']
        encoded = gzip.compress(report.canonical(trace), mtime=0)
        path = self.root/'protocol/trace.json.gz'; path.parent.mkdir(); path.write_bytes(encoded)
        reference = {'path':'protocol/trace.json.gz','sha256':report.digest(encoded)}
        self.prior = self.root/'prior'; self.prior.mkdir()
        (self.prior/'manifest.json').write_text(json.dumps({'model_controller_sha256':'controller',
            'source_id':'source','git_base':'base','plan_sha256':'parent-sha'}))
        self.manifest = {'source_id':'source','git_base':'base','parent_plan':{'path':'protocol/parent.json','sha256':'parent-sha'},
            'prior':{'directory':'prior'},'trials':[],'network_path':'network/control.json','qualification_directory':'qualification'}
        for index in range(45):
            cfg = {k:v for k,v in document()['configuration'].items() if k != 'trace'}
            cfg.update(trial_id=f'synthetic-{index:02}',profile='low',replicas=1,repetition=index % 3,
                       trace_reference=reference)
            self.plan['blocks']['real-model'].append(cfg)
            if index >= 17:
                self.manifest['trials'].append({'parent_index':index,'trial_id':cfg['trial_id'],
                    'result_path':f'epochs/{index}/real-model/{cfg["trial_id"]}.json',
                    'result_directory':f'epochs/{index}/real-model','plan_sha256':f'selected-{index}',
                    'recovery_path':f'epochs/{index}/recovery.json','warmup_path':f'epochs/{index}/warmup.json'})
            else:
                self.save_trial(index)
        (self.root/'protocol/parent.json').write_text(json.dumps(self.plan))
        self.path = self.root/'manifest.json'; self.path.write_text(json.dumps(self.manifest))
        registry = self.root/'instruments'; registry.mkdir()
        (registry/'manifest.json').write_text('{"sources":{}}')

    def save_trial(self, index):
        cfg = self.plan['blocks']['real-model'][index]
        data = document(); data['configuration'] = {**cfg,'trace':document()['configuration']['trace'],
            'trace_decoded_sha256':report.digest(report.canonical(document()['configuration']['trace']))}
        path = self.prior/(cfg['trial_id']+'.json') if index < 17 else self.root/f'epochs/{index}/real-model'/(cfg['trial_id']+'.json')
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(data))
        if index >= 17:
            (path.parent/'manifest.json').write_text(json.dumps({'model_controller_sha256':'controller',
                'source_id':'source','git_base':'base','plan_sha256':f'selected-{index}'}))

    def build(self, shared_epoch=False):
        def classify(data,*args,**kwargs):
            index=int(data['configuration']['trial_id'].split('-')[-1]); failed=index==16
            return {'primary_scope':'complete_arrival_cohort','observation_status':'native_completion_uncertain' if failed else 'certified_observation',
                'epoch':'original-epoch' if index < 17 else 'original-epoch' if shared_epoch else f'epoch-{index}',
                'resource_qualified':True,'native_completion':'uncertain' if failed else 'certified','bba_valid':not failed,
                'execution_defects':[],'resource_issues':[],'bba_gate_issues':[]}
        with patch.object(report,'check_manifest',return_value=self.manifest), \
                patch.object(report,'verify_registered',return_value={'files':{'v2/inference_proxy.py':{'sha256':'proxy'},'v2/model_service.py':{'sha256':'controller'}}}), \
                patch.object(report,'verify_source_metadata'), patch.object(report,'refresh_controls',return_value={'qualified':True,'issues':[]}), \
                patch.object(report,'epoch_issues',return_value=[]), patch.object(report,'classify_document',side_effect=classify):
            return report.build_report(self.path,root=self.root)

    def test_partial_report_keeps_failed_seventeenth_and_all_twenty_eight_pending_rows(self):
        result=self.build()
        self.assertEqual(len(result['trials']),45)
        self.assertEqual(result['observed_trials'],17)
        self.assertEqual(result['remaining_observations'],28)
        self.assertEqual(result['trials'][16]['native_completion'],'uncertain')
        self.assertFalse(result['exploratory_observations_complete'])
        self.assertFalse(result['qualified']); self.assertFalse(result['included_for_inference'])

    def test_complete_observation_table_never_qualifies_or_pools_the_repaired_aggregate(self):
        for index in range(17,45): self.save_trial(index)
        result=self.build()
        self.assertEqual(result['observed_trials'],45)
        self.assertEqual(result['continuation_observed'],28)
        self.assertTrue(result['exploratory_observations_complete'])
        self.assertFalse(result['parent_block_qualified']); self.assertFalse(result['qualified'])
        self.assertFalse(result['included_for_inference'])
        self.assertTrue(all(key not in result for key in ('contrasts','confidence_intervals','pooled_mean')))
        repeated=self.build(shared_epoch=True)
        self.assertFalse(repeated['exploratory_observations_complete'])
        self.assertIn('proxy_epoch_reused',repeated['trials'][17]['execution_defects'])

    def test_json_and_csv_are_deterministic_and_existing_output_is_never_overwritten(self):
        result=self.build(); a=self.root/'a'; b=self.root/'b'
        report.write_report(result,a); report.write_report(result,b)
        self.assertEqual((a/'results.json').read_bytes(),(b/'results.json').read_bytes())
        self.assertEqual((a/'trials.csv').read_bytes(),(b/'trials.csv').read_bytes())
        with (a/'trials.csv').open() as stream: self.assertEqual(len(list(csv.DictReader(stream))),45)
        with self.assertRaises(FileExistsError): report.write_report(result,a)


if __name__ == '__main__':
    unittest.main()
