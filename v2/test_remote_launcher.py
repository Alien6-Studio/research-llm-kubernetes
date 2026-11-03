"""Launcher preflight must reject invalid inputs before any cluster command."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid

from v2.plan import ROOT
from v2.remote_plan import derive


class RemoteLauncherPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.log = self.directory / 'commands.log'
        stub = self.directory / 'python3'
        stub.write_text('#!/usr/bin/env bash\n'
                        'if [[ "${1:-}" == "-" ]]; then\n'
                        f'  exec {shlex.quote(sys.executable)} "$@"\n'
                        'fi\n'
                        'printf "%s\\n" "$*" >> "$STUDY_TEST_COMMAND_LOG"\n'
                        'exit 73\n')
        stub.chmod(0o755)
        self.environment = dict(os.environ)
        self.environment.pop('PYTHONOPTIMIZE', None)
        self.environment.update(PATH=str(self.directory) + os.pathsep + os.environ['PATH'],
                                STUDY_MODEL_IMAGE='test-not-deployed',
                                STUDY_COHORT_PREFIX='v2-launcher-test-' + uuid.uuid4().hex,
                                STUDY_TEST_COMMAND_LOG=str(self.log))

    def launch(self, plan, *, optimized=False):
        path = self.directory / 'plan.json'
        path.write_text(json.dumps(plan))
        environment = {**self.environment, 'STUDY_MODEL_PLAN': str(path)}
        if optimized:
            environment['PYTHONOPTIMIZE'] = '1'
        return subprocess.run(['bash', str(ROOT / 'infra/run-remote-model-controls.sh')],
                              env=environment, text=True, capture_output=True, timeout=15)

    def test_help_is_safe_without_configuration_or_cluster_commands(self):
        environment = dict(self.environment)
        for field in ('STUDY_MODEL_IMAGE', 'STUDY_MODEL_PLAN', 'STUDY_COHORT_PREFIX'):
            environment.pop(field, None)
        result = subprocess.run(['bash', str(ROOT / 'infra/run-remote-model-controls.sh'), '--help'],
                                env=environment, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)
        self.assertFalse(self.log.exists())

    def test_complete_frozen_plan_with_additional_continuation_metadata_reaches_deploy(self):
        plan = derive('172.18.0.1')
        plan['continuation'] = {'transport': 'SSH compression disabled'}
        result = self.launch(plan)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-m v2.run deploy --image test-not-deployed'])

    def test_selected_stdio_plan_preserves_the_base_matrix_and_allows_transport_metadata(self):
        plan = json.loads((ROOT / 'protocol/ollama-isolated-stdio-plan.json').read_text())
        self.assertEqual(plan['continuation']['cohort'], 'ollama-isolated-stdio')
        result = self.launch(plan)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-m v2.run deploy --image test-not-deployed'])

    def test_optimized_python_is_rejected_before_deploy(self):
        result = self.launch(derive('172.18.0.1'), optimized=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('disable optimization', result.stderr)
        self.assertFalse(self.log.exists())

    def test_cpu_plan_with_another_source_is_rejected_before_deploy(self):
        for cohort in ('ollama-dell-cpu', 'ollama-fra6s004-cpu', 'ollama-fra6s004-cpu-confirmation'):
            plan = derive('172.18.0.1')
            plan['continuation'] = {'cohort': cohort, 'source_id': 'sha256:' + '0' * 64,
                                    'git_base': '0' * 40}
            result = self.launch(plan)
            with self.subTest(cohort=cohort):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('CPU plan does not match the registered collection source', result.stderr)
                self.assertFalse(self.log.exists())

    def test_empty_or_partial_blocks_and_changed_rates_are_rejected_before_deploy(self):
        for change in ('empty', 'partial', 'rates', 'trace'):
            plan = derive('172.18.0.1')
            if change == 'empty':
                plan['blocks'] = {key: [] for key in plan['blocks']}
            elif change == 'partial':
                plan['blocks']['model-qualification'].pop()
            elif change == 'rates':
                plan['rates']['high'] += 1
            else:
                plan['blocks']['real-model'][0]['trace_reference']['sha256'] = '0' * 64
            result = self.launch(plan)
            with self.subTest(change=change):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('registered complete study', result.stderr)
                self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
