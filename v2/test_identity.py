"""Content identities retain exact instruments independently of Git ancestry."""
from contextlib import redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from v2 import identity


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'v2').mkdir()
        self.files = ('v2/model.py', 'v2/service.py')
        (self.root / self.files[0]).write_bytes(b'model\r\n\x00exact bytes\n')
        (self.root / self.files[1]).write_bytes(b'service\n')

    def snapshot(self, **kwargs):
        return identity.capture(self.root, self.files, git_base='1' * 40, **kwargs)

    def manifest(self):
        return self.root / 'instruments/manifest.json'

    def test_identity_is_stable_across_order_and_git_bases(self):
        first = self.snapshot()
        second = identity.capture(self.root, reversed(self.files), git_base='2' * 40)
        self.assertEqual(first.source_id, second.source_id)
        self.assertEqual(first.files, second.files)
        self.assertNotEqual(first.git_base, second.git_base)
        self.assertEqual(second.metadata(), {'source_id': second.source_id, 'git_base': '2' * 40})

    def test_byte_change_produces_a_new_identity(self):
        first = self.snapshot()
        (self.root / self.files[0]).write_bytes(b'model\n\x00exact bytes\n')
        second = self.snapshot()
        self.assertNotEqual(first.source_id, second.source_id)
        identity.register(self.root, first)
        identity.register(self.root, second)
        self.assertEqual(len(json.loads(self.manifest().read_text())['sources']), 2)
        identity.verify_registered(self.root, first.source_id)
        identity.verify_registered(self.root, second.source_id)

    def test_identity_is_independent_of_repository_location(self):
        first = self.snapshot()
        other = self.root / 'other-checkout'
        (other / 'v2').mkdir(parents=True)
        for name, raw in first.files:
            (other / name).write_bytes(raw)
        second = identity.capture(other, self.files, git_base='2' * 40)
        self.assertEqual(first.source_id, second.source_id)

    def test_registration_preserves_captured_bytes_not_later_worktree(self):
        snapshot = self.snapshot()
        (self.root / self.files[0]).write_bytes(b'changed after capture')
        identity.register(self.root, snapshot)
        entry = identity.verify_registered(self.root, snapshot.source_id)
        stored = self.root / entry['files'][self.files[0]]['path']
        self.assertEqual(stored.read_bytes(), snapshot.files[0][1])
        with self.assertRaisesRegex(ValueError, 'Working source changed'):
            identity.verify_worktree(self.root, snapshot.source_id)

    def test_identical_registration_is_idempotent_and_git_is_separate(self):
        snapshot = self.snapshot()
        identity.register(self.root, snapshot)
        before = self.manifest().read_bytes()
        other_base = replace(snapshot, git_base='2' * 40)
        self.assertEqual(identity.register(self.root, other_base), other_base.metadata())
        self.assertEqual(self.manifest().read_bytes(), before)
        self.assertNotIn('git_base', json.loads(before)['sources'][snapshot.source_id])
        identity.verify_worktree(self.root, snapshot.source_id)

    def test_existing_different_entry_is_never_overwritten(self):
        snapshot = self.snapshot()
        self.manifest().parent.mkdir()
        self.manifest().write_text(json.dumps({'sources': {snapshot.source_id: {'files': {}}}}))
        before = self.manifest().read_bytes()
        with self.assertRaisesRegex(ValueError, 'different source registry entry'):
            identity.register(self.root, snapshot)
        self.assertEqual(self.manifest().read_bytes(), before)
        self.assertFalse((self.root / 'instruments/blobs').exists())

    def test_tampered_blob_is_detected_and_never_repaired_silently(self):
        snapshot = self.snapshot()
        identity.register(self.root, snapshot)
        entry = identity.verify_registered(self.root, snapshot.source_id)
        blob = self.root / entry['files'][self.files[0]]['path']
        blob.write_bytes(b'tampered')
        before = self.manifest().read_bytes()
        with self.assertRaisesRegex(ValueError, 'Registered source bytes differ'):
            identity.verify_registered(self.root, snapshot.source_id)
        with self.assertRaisesRegex(ValueError, 'different source blob'):
            identity.register(self.root, snapshot)
        self.assertEqual(blob.read_bytes(), b'tampered')
        self.assertEqual(self.manifest().read_bytes(), before)

    def test_forged_snapshot_identity_is_rejected_before_writes(self):
        forged = replace(self.snapshot(), source_id='sha256:' + '0' * 64)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            identity.register(self.root, forged)
        self.assertFalse(self.manifest().exists())

    def test_paths_must_be_canonical_relative_files_without_symlinks(self):
        for name in ['../escape', '/absolute', 'v2/../model.py', 'v2//model.py', './v2/model.py']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                identity.capture(self.root, [name], git_base='1' * 40)
        (self.root / 'v2/linked.py').symlink_to(self.root / self.files[0])
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            identity.capture(self.root, ['v2/linked.py'], git_base='1' * 40)

    def test_registry_lock_refuses_a_concurrent_writer(self):
        instruments = self.root / 'instruments'
        instruments.mkdir()
        lock = instruments / '.identity.lock'
        lock.write_text('another writer')
        with self.assertRaises(FileExistsError):
            identity.register(self.root, self.snapshot())
        self.assertEqual(lock.read_text(), 'another writer')
        self.assertFalse(self.manifest().exists())

    def test_existing_materializer_reconstructs_exact_bytes(self):
        snapshot = self.snapshot()
        identity.register(self.root, snapshot)
        source = Path(__file__).resolve().parents[1] / 'scripts/materialize_instrument.py'
        spec = importlib.util.spec_from_file_location('identity_materializer_test', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        destination = self.root / 'reconstructed'
        with patch.object(module, 'ROOT', self.root), patch.object(
            sys, 'argv', ['materialize_instrument.py', snapshot.source_id, str(destination)]
        ), redirect_stdout(io.StringIO()):
            module.main()
        for name, raw in snapshot.files:
            self.assertEqual((destination / name).read_bytes(), raw)
