"""Qualified model reporting must not turn excluded or untested work into evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v2.plan import ROOT
from v2.publication import actual_model_lines, build, excluded_model_lines
from v2.report import prepare, qualified_real_model_blocks, real_model_figure_name


def model_fixture():
    """Artificial unit-test observations only; never written as study results."""
    rows = []
    for profile in ('low', 'near', 'high'):
        for path, replicas in [('direct-model', 1), *[('workers', n) for n in (1, 2, 4, 8)]]:
            for repetition in (0, 1, 2):
                rows.append({'block': 'real-model', 'profile': profile, 'path': path,
                    'replicas': replicas, 'repetition': repetition, 'valid': True,
                    'policy': 'none', 'quota': 1000000000,
                    'useful_cohort_per_second': 1.0, 'useful_fraction': .5})
    return {'block': {'complete': True, 'qualified': True, 'trials': len(rows), 'invalid': []},
            'included_for_inference': True, 'trials': rows}


class ActualModelPublicationTests(unittest.TestCase):
    def test_only_complete_qualified_grid_is_eligible(self):
        data = model_fixture()
        self.assertEqual(qualified_real_model_blocks({'v2-unit-model': data}), [('v2-unit-model', data)])
        for change in ('missing', 'duplicate', 'invalid', 'unqualified'):
            with self.subTest(change=change):
                altered = copy.deepcopy(data)
                if change == 'missing':
                    altered['trials'].pop()
                elif change == 'duplicate':
                    altered['trials'][-1] = copy.deepcopy(altered['trials'][0])
                elif change == 'invalid':
                    altered['trials'][0]['valid'] = False
                else:
                    altered['block']['qualified'] = False
                with self.assertRaisesRegex(ValueError, 'complete qualified 45-trial'):
                    qualified_real_model_blocks({'v2-unit-model': altered})

    def test_excluded_block_is_not_rescued_by_its_passing_trials(self):
        data = model_fixture()
        data['included_for_inference'] = False
        data['block']['qualified'] = False
        for row in data['trials'][:15]:
            row['valid'] = False
        self.assertEqual(qualified_real_model_blocks({'v2-unit-excluded': data}), [])

    def test_unsupported_interventions_fail_closed(self):
        for field, value in [('policy', 'global'), ('quota', 40000)]:
            with self.subTest(field=field):
                data = model_fixture()
                data['trials'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'quota/admission interventions'):
                    qualified_real_model_blocks({'v2-unit-model': data})

    def test_qualified_section_reports_observations_without_direction_or_transfer_claims(self):
        data = model_fixture()
        text = '\n'.join(actual_model_lines(qualified_real_model_blocks({'v2-unit-model': data})))
        self.assertIn('analysis/v2-unit-model/results.json', text)
        self.assertIn('Direct Ollama | —', text)
        self.assertIn('Kubernetes workers | 8', text)
        self.assertIn('qualification alone does not imply a positive scaling effect', text)
        self.assertIn('does not establish transfer of the emulator quota/admission effects', text)
        self.assertIn('real-model-replicas.png', text)
        self.assertIn('exploratory, descriptive', text)
        self.assertIn('n=3 independently seeded trace repetitions', text)
        self.assertIn('do not infer general causal hardware explanations', text)

    def test_preparation_pairs_real_model_results_within_each_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, worker_rate in [('v2-unit-a', 2.0), ('v2-unit-b', 5.0)]:
                data = model_fixture()
                for row in data['trials']:
                    if row['path'] == 'workers':
                        row['useful_cohort_per_second'] = worker_rate
                target = root / 'analysis' / name
                target.mkdir(parents=True)
                (target / 'results.json').write_text(json.dumps(data))
            with patch('v2.report.ROOT', root):
                report = prepare()
        self.assertEqual(len(report['contrasts']), 48)
        for contrast in report['contrasts']:
            self.assertEqual(contrast['question'], 'H4')
            effect = contrast['right_minus_left']
            self.assertEqual(effect['n'], 3)
            if contrast['metric'] == 'useful_cohort_per_second':
                self.assertEqual(effect['mean'], 1 if contrast['dataset'] == 'v2-unit-a' else 4)

    def test_registered_continuation_needs_its_own_archived_prerequisites(self):
        name = 'v2-ollama-isolated-plain-restart-real-model'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'analysis' / name
            target.mkdir(parents=True)
            (target / 'results.json').write_text(json.dumps(model_fixture()))
            with patch('v2.report.ROOT', root), patch(
                    'v2.verify.audit_plain_continuations', return_value={
                        'v2-ollama-isolated-plain': {'eligible': True}}):
                with self.assertRaisesRegex(ValueError, 'continuation prerequisites'):
                    prepare()
            with patch('v2.report.ROOT', root), patch(
                    'v2.verify.audit_plain_continuations', return_value={
                        name.removesuffix('-real-model'): {'eligible': True}}):
                self.assertEqual(len(prepare()['contrasts']), 24)

    def test_partial_attempt_is_visible_excluded_and_never_pooled_with_restart(self):
        partial = model_fixture()
        partial['trials'] = partial['trials'][:29]
        partial['included_for_inference'] = False
        partial['block'].update(complete=False, qualified=False, trials=29)
        name = 'v2-unit-partial-real-model'
        qualified = model_fixture()
        datasets = {name: partial, 'v2-unit-restart-real-model': qualified}
        self.assertEqual([name for name, _ in qualified_real_model_blocks(datasets)],
                         ['v2-unit-restart-real-model'])
        text = '\n'.join(excluded_model_lines(datasets))
        self.assertIn(f'[{name}](analysis/{name}/results.json) | 29 | 29 | False | False |', text)
        self.assertNotIn('v2-unit-restart-real-model', text)
        self.assertIn('not combined with another attempt', text)
        report = json.loads((ROOT / 'analysis/v2-study/results.json').read_text())
        report['datasets'].update(datasets)
        with patch('v2.publication.prepare', return_value=report):
            output = build()
        self.assertIn('| Dataset | Archived trials |', output['RESULTS.md'])
        self.assertIn('## Additional excluded actual-model blocks', output['RESULTS.md'])
        self.assertIn('## Additional excluded actual-model blocks', output['ARTICLE_EVIDENCE.md'])

    def test_multiple_qualified_blocks_have_separate_figures(self):
        names = ['v2-unit-a', 'v2-unit-b']
        figures = [real_model_figure_name(name, len(names)) for name in names]
        self.assertEqual(len(set(figures)), len(names))
        text = '\n'.join(actual_model_lines(qualified_real_model_blocks({
            name: model_fixture() for name in names})))
        for figure in figures:
            self.assertIn(figure + '.png', text)

    def test_new_qualified_block_does_not_reclassify_historical_exclusions(self):
        report = json.loads((ROOT / 'analysis/v2-study/results.json').read_text())
        report['datasets']['v2-unit-qualified-model'] = model_fixture()
        with patch('v2.publication.prepare', return_value=report):
            output = build()
        self.assertIn('## Qualified actual-model comparisons', output['RESULTS.md'])
        self.assertIn('| v2-unit-qualified-model | 45 | 45 | True |', output['RESULTS.md'])
        self.assertIn('| v2-local-real-model | 45 | 30 | False |', output['RESULTS.md'])
        self.assertIn('historical blocks provide no qualified', output['RESULTS.md'])
        self.assertNotIn('Transfer to actual LLM inference is **not demonstrated**.', output['RESULTS.md'])
        self.assertIn('## Historical failed actual-model instruments', output['ARTICLE_EVIDENCE.md'])
        self.assertIn('**Not demonstrated:** transfer of quota/admission effects', output['ARTICLE_EVIDENCE.md'])

    def test_no_new_qualified_data_preserves_existing_publication_bytes(self):
        report = json.loads((ROOT / 'analysis/v2-study/results.json').read_text())
        self.assertEqual(qualified_real_model_blocks(report['datasets']), [])
        with patch('v2.publication.prepare', return_value=report):
            output = build()
        for name, text in output.items():
            self.assertEqual(text, (ROOT / name).read_text(), name)


if __name__ == '__main__':
    unittest.main()
