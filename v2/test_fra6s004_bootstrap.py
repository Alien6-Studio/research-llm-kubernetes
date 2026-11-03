"""Bootstrap guard checks without installing software or starting services."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'infra/bootstrap-fra6s004-client.sh'
VALID_SOURCE = 'sha256:' + 'a' * 64
VALID_BASE = 'b' * 40


class FraClientBootstrapGuardTests(unittest.TestCase):
    def setUp(self):
        if socket.gethostname().split('.')[0] == 'a6-capacity-v2' or Path('/etc/alien6-research-instance').exists():
            self.skipTest('Do not execute bootstrap guard tests inside an operator-marked instance')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.log = self.directory / 'installer-calls'
        for command in ('apt-get', 'curl', 'systemctl', 'docker', 'kind', 'kubectl', 'cloud-init'):
            path = self.directory / command
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$BOOTSTRAP_TEST_LOG"\nexit 99\n')
            path.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.directory) + os.pathsep + os.environ['PATH'],
                    'BOOTSTRAP_TEST_LOG': str(self.log)}

    def test_script_has_valid_bash_syntax(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True, capture_output=True)

    def test_wrong_machine_is_rejected_before_any_installer_or_download(self):
        completed = subprocess.run(['bash', str(SCRIPT), '--source-id', VALID_SOURCE,
                                    '--git-base', VALID_BASE], env=self.env, text=True, capture_output=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertRegex(completed.stderr, 'root only inside|dedicated-instance marker|VM hostname')
        self.assertFalse(self.log.exists())

    def test_both_full_identities_are_required_before_machine_or_installer_commands(self):
        for arguments in ([], ['--source-id', VALID_SOURCE], ['--git-base', VALID_BASE]):
            with self.subTest(arguments=arguments):
                completed = subprocess.run(['bash', str(SCRIPT), *arguments], env=self.env,
                                           text=True, capture_output=True)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn('Both --source-id and --git-base are required', completed.stderr)
                self.assertFalse(self.log.exists())

    def test_invalid_source_or_git_id_is_rejected_before_installation(self):
        for source, base, message in (
            ('sha256:123', VALID_BASE, '--source-id'),
            ('sha256:' + 'g' * 64, VALID_BASE, '--source-id'),
            (VALID_SOURCE, 'b' * 39, '--git-base'),
            (VALID_SOURCE, '--upload-pack=unexpected', '--git-base')):
            with self.subTest(source=source, base=base):
                completed = subprocess.run(['bash', str(SCRIPT), '--source-id', source,
                    '--git-base', base], env=self.env, text=True, capture_output=True)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn('Expected ' + message, completed.stderr)
                self.assertFalse(self.log.exists())

    def source_program(self):
        # Execute the exact pre-install registration checker embedded in Bash;
        # no VM identity bypass or installation test mode is added to production.
        return SCRIPT.read_text().split("<<'SOURCE_CHECK'\n", 1)[1].split('\nSOURCE_CHECK', 1)[0]

    def source_fixture(self):
        root = self.directory / 'staged-source'
        (root / 'instruments').mkdir(parents=True)
        (root / 'v2').mkdir()
        (root / 'v2/kind.yaml').write_bytes((SCRIPT.parent.parent / 'v2/kind.yaml').read_bytes())
        raw = b'unit-test registered measurement source\n'
        (root / 'measurement.py').write_bytes(raw)
        descriptor = {'format': 'alien6-collection-source-v1', 'files': {
            'measurement.py': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}
        source = 'sha256:' + hashlib.sha256(json.dumps(descriptor, sort_keys=True,
                                                     separators=(',', ':')).encode()).hexdigest()
        (root / 'instruments/manifest.json').write_text(json.dumps({'sources': {source: {
            'content_format': descriptor['format'], 'files': descriptor['files']}}}))
        return root, source

    def test_unregistered_source_is_rejected_by_the_actual_preinstall_checker(self):
        root, _ = self.source_fixture()
        completed = subprocess.run([sys.executable, '-', str(root), VALID_SOURCE],
            input=self.source_program(), env=self.env, text=True, capture_output=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('not registered', completed.stderr)
        self.assertFalse(self.log.exists())

    def test_exact_registered_bytes_pass_and_a_changed_file_is_rejected(self):
        root, source = self.source_fixture()
        command = [sys.executable, '-', str(root), source]
        completed = subprocess.run(command, input=self.source_program(), env=self.env,
                                   text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (root / 'measurement.py').write_bytes(b'different bytes\n')
        completed = subprocess.run(command, input=self.source_program(), env=self.env,
                                   text=True, capture_output=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('differs from the frozen instrument', completed.stderr)
        self.assertFalse(self.log.exists())

    def test_help_is_available_without_running_installation_commands(self):
        completed = subprocess.run(['bash', str(SCRIPT), '--help'], env=self.env, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn('172.30.64.2', completed.stdout)
        self.assertIn('--source-id', completed.stdout)
        self.assertIn('--git-base', completed.stdout)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
