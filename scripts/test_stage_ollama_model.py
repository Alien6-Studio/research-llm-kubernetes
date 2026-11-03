"""Model staging must preserve exact identities and never publish partial data."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import stage_ollama_model as staging


class ModelStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / 'models'
        # Small synthetic artifacts for deployment-helper tests only.
        self.payloads = {hashlib.sha256(data).hexdigest(): data for data in
                         (b'unit-config', b'unit-weights', b'unit-license', b'unit-params')}
        blobs = [{'digest': 'sha256:' + digest, 'size': len(data)}
                 for digest, data in self.payloads.items()]
        self.manifest = json.dumps({'schemaVersion': 2, 'config': blobs[0], 'layers': blobs[1:]},
                                   separators=(',', ':')).encode() + b'\n'
        self.manifest_file = self.root / 'manifest.json'
        self.manifest_file.write_bytes(self.manifest)
        self.sha = hashlib.sha256(self.manifest).hexdigest()
        for name, value in [('MANIFEST_FILE', self.manifest_file), ('MANIFEST_SHA256', self.sha)]:
            patcher = patch.object(staging, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, command, *, check):
        self.assertTrue(check)
        self.assertEqual(command[-1].split('/')[-1][:7], 'sha256:')
        target = Path(command[command.index('--output') + 1])
        payload = self.payloads[command[-1].split(':')[-1]]
        existing = target.read_bytes() if target.exists() else b''
        self.assertTrue(payload.startswith(existing))
        target.write_bytes(existing + payload[len(existing):])

    def test_resume_exact_blobs_then_install_byte_identical_manifest_and_reverify(self):
        digest, payload = next(iter(self.payloads.items()))
        partial = self.destination / 'blobs' / ('sha256-' + digest + '.partial')
        partial.parent.mkdir(parents=True)
        partial.write_bytes(payload[:3])
        with patch.object(staging.subprocess, 'run', side_effect=self.download) as download:
            result = staging.stage(self.destination)
        self.assertEqual(download.call_count, 4)
        for call in download.call_args_list:
            command = call.args[0]
            self.assertIn('--retry-all-errors', command)
            self.assertEqual(command[command.index('--retry') + 1], '5')
            self.assertEqual(command[command.index('--continue-at') + 1], '-')
            self.assertEqual(command[command.index('--proto') + 1], '=https')
            self.assertEqual(command[command.index('--proto-redir') + 1], '=https')
            self.assertTrue(command[-1].startswith(staging.REGISTRY_BLOBS + 'sha256:'))
        self.assertEqual(Path(download.call_args_list[0].args[0][
            download.call_args_list[0].args[0].index('--output') + 1]), partial.resolve())
        self.assertEqual(Path(result['manifest_path']).read_bytes(), self.manifest)
        with patch.object(staging.subprocess, 'run') as download:
            self.assertEqual(staging.stage(self.destination, verify_only=True), result)
        download.assert_not_called()
        self.assertEqual(list(self.destination.rglob('*.partial')), [])

    def test_wrong_download_digest_is_retained_but_never_published(self):
        def corrupt(command, *, check):
            target = Path(command[command.index('--output') + 1])
            expected = self.payloads[command[-1].split(':')[-1]]
            target.write_bytes(b'x' * len(expected))
        with patch.object(staging.subprocess, 'run', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                staging.stage(self.destination)
        self.assertFalse((self.destination / staging.MANIFEST_RELATIVE).exists())
        self.assertEqual(len(list(self.destination.rglob('*.partial'))), 1)

    def test_failed_download_preserves_partial_and_does_not_install_manifest(self):
        def fail(command, *, check):
            Path(command[command.index('--output') + 1]).write_bytes(b'un')
            raise subprocess.CalledProcessError(18, command)
        with patch.object(staging.subprocess, 'run', side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                staging.stage(self.destination)
        self.assertFalse((self.destination / staging.MANIFEST_RELATIVE).exists())
        self.assertEqual(len(list(self.destination.rglob('*.partial'))), 1)

    def test_existing_corrupt_blob_or_different_manifest_is_not_replaced(self):
        digest, data = next(iter(self.payloads.items()))
        blob = self.destination / 'blobs' / ('sha256-' + digest)
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b'x' * len(data))
        with patch.object(staging.subprocess, 'run') as download:
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                staging.stage(self.destination)
        download.assert_not_called()
        self.assertEqual(blob.read_bytes(), b'x' * len(data))
        manifest = self.destination / staging.MANIFEST_RELATIVE
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b'another model')
        with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
            staging.stage(self.destination)
        self.assertEqual(manifest.read_bytes(), b'another model')

    def test_verify_only_and_tampered_retained_manifest_do_not_create_directories(self):
        with self.assertRaisesRegex(ValueError, 'has not been installed'):
            staging.stage(self.destination, verify_only=True)
        self.assertFalse(self.destination.exists())
        self.manifest_file.write_bytes(self.manifest + b' ')
        with self.assertRaisesRegex(ValueError, 'pinned study digest'):
            staging.stage(self.destination)
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
