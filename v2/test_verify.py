"""Publication verification must fail closed on missing or altered evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v2 import verify


class ReplayIntegrityTests(unittest.TestCase):
    def test_missing_archives_cannot_pass_as_a_zero_trial_study(self):
        expected=(verify.ROOT/'v2/evidence-counts.json').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            (root/'instruments').mkdir();(root/'v2').mkdir()
            (root/'instruments/manifest.json').write_text('{"sources":{}}')
            (root/'v2/evidence-counts.json').write_text(expected)
            with patch.object(verify,'ROOT',root),self.assertRaisesRegex(ValueError,'Incomplete published evidence'):
                verify.verify()

    def test_altered_captured_instrument_fails_before_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();(root/'instruments').mkdir()
            (root/'instruments/source.snapshot').write_text('changed')
            entry={'path':'instruments/source.snapshot','sha256':'0'*64,'bytes':7}
            (root/'instruments/manifest.json').write_text(json.dumps({'sources':{'sample':{'files':{'service.py':entry}}}}))
            with patch.object(verify,'ROOT',root),self.assertRaisesRegex(ValueError,'Instrument source identity mismatch'):
                verify.verify()
