"""Publication preserves raw identities and keeps lifecycle regimes separate."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_cpu_continuation as evidence
from v2 import publication, report


class LosslessEvidenceTests(unittest.TestCase):
    def entry(self, root, raw=b'{"response":"complete"}\n'):
        encoded = evidence.compressed_bytes(raw)
        archive = root / 'archives' / 'sample.json.gz'; archive.parent.mkdir()
        archive.write_bytes(encoded)
        return {'path': 'archives/sample.json.gz', 'source_relative_path': 'runs/sample.json',
                'bytes': len(encoded), 'sha256': evidence.digest(encoded),
                'decoded_bytes': len(raw), 'decoded_sha256': evidence.digest(raw)}, raw

    def test_lossless_gzip_has_no_editorial_timestamp_and_restoration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); row, raw = self.entry(root)
            encoded = (root / row['path']).read_bytes()
            self.assertEqual(encoded[4:8], b'\0' * 4)
            self.assertEqual(gzip.decompress(encoded), raw)
            self.assertTrue(evidence.materialize_entry(root, row))
            before = (root / row['source_relative_path']).stat()
            self.assertFalse(evidence.materialize_entry(root, row))
            after = (root / row['source_relative_path']).stat()
            self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_decoded_or_compressed_mismatch_never_materializes_a_result(self):
        for field in ('sha256', 'decoded_sha256'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); row, _ = self.entry(root)
                row[field] = '0' * 64
                with self.assertRaises(ValueError): evidence.materialize_entry(root, row)
                self.assertFalse((root / row['source_relative_path']).exists())

    def test_existing_different_raw_bytes_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); row, _ = self.entry(root)
            path = root / row['source_relative_path']; path.parent.mkdir(); path.write_bytes(b'prior evidence')
            with self.assertRaisesRegex(ValueError, 'Existing decoded evidence differs'):
                evidence.materialize_entry(root, row)
            self.assertEqual(path.read_bytes(), b'prior evidence')

    def test_unsafe_archive_paths_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ('../outside', '/outside', 'runs//sample.json', 'runs/./sample.json'):
                with self.subTest(name=name), self.assertRaises(ValueError): evidence.safe_path(root, name)
            (root / 'linked').symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError): evidence.safe_path(root, 'linked/sample.json')


class ReportProvenanceTests(unittest.TestCase):
    def test_only_python_patch_provenance_can_differ(self):
        stored = {'python': '3.12.3', 'observed_trials': 45, 'status_counts': {'certified_observation': 44}}
        replayed = {**stored, 'python': '3.12.9'}
        self.assertEqual(len(evidence.compare_reports(stored, replayed)), 64)
        for changed in ({**replayed, 'observed_trials': 44}, {**replayed, 'extra_scientific_field': None},
                        {**replayed, 'python': '3.13.0'}, {**replayed, 'python': '3.12'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                evidence.compare_reports(stored, changed)

    def test_native_schema_is_not_added_to_legacy_paired_datasets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'analysis' / 'v2-native-observations'; target.mkdir(parents=True)
            (target / 'results.json').write_text(json.dumps({'schema': 'alien6-cpu-continuation-report-v1',
                'included_for_inference': False, 'trials': [{'observation_status': 'certified_observation'}]}))
            with patch.object(report, 'ROOT', root): value = report.prepare()
        self.assertEqual(value['datasets'], {})
        self.assertEqual(value['contrasts'], [])

    def test_publication_examples_cannot_mix_reset_and_shared_epochs(self):
        original = json.loads((publication.ROOT / 'analysis/v2-ollama-fra6s004-cpu-continuation/results.json').read_bytes())
        changed = copy.deepcopy(original)
        row = next(row for row in changed['trials'] if row['profile'] == 'low' and row['repetition'] == 0 and row['path'] == 'direct-model')
        row['segment'] = 'original_shared_epoch'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'analysis/v2-ollama-fra6s004-cpu-continuation'; folder.mkdir(parents=True)
            (folder / 'results.json').write_text(json.dumps(changed))
            with patch.object(publication, 'ROOT', root), self.assertRaisesRegex(ValueError, 'lifecycle regimes'):
                publication.native_baseline_lines()


if __name__ == '__main__': unittest.main()
