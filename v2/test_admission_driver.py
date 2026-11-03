"""An arm can start only after the same complete, unconfigured warmup epoch."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v2 import admission_driver, model
from v2.admission_proxy import AdmissionProxy
from v2.workload import model_request

BASE_ID = {'source_id': 'base-fixture', 'git_base': '1' * 40}
STUDY_ID = {'source_id': 'study-fixture', 'git_base': '1' * 40}


class AdmissionDriverPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.proxy = AdmissionProxy(BASE_ID, STUDY_ID)
        async def native(*args, **kwargs):
            return 200, {'done': True, 'response': '{"label":"ok"}'}
        with patch('v2.inference_proxy.request', side_effect=native):
            for index in range(4):
                await self.proxy.handle('POST', '/api/generate', model_request(model.pilot_item(index)))
        self.before_native = self.proxy._state('GET', '/study-state?records=1', None)[1]
        self.before_admission = self.proxy.admission_state('GET', '/study-admission/state?records=1', None)[1]
        self.configuration = {'trial_id': 'admission-test-global',
            'provider': {'ollama_url': 'http://127.0.0.1:12345'},
            'shared_admission': {'mode': 'global', 'capacity': 1}}
        self.configured = self.proxy.configure('POST', {'mode': 'global', 'capacity': 1,
            'trial_id': 'admission-test-global', 'study_source_id': STUDY_ID['source_id']})[1]
        self.after = self.proxy.admission_state('GET', '/study-admission/state?records=1', None)[1]
        self.base = SimpleNamespace(**BASE_ID, metadata=lambda: dict(BASE_ID))
        self.study = SimpleNamespace(**STUDY_ID, metadata=lambda: dict(STUDY_ID))

    async def test_preflight_configures_once_after_exact_warmup_and_returns_atomic_state(self):
        responses = [self.before_native, self.before_admission, self.configured, self.after]
        with patch.object(admission_driver, 'json_http', side_effect=responses) as http:
            native, admission, identity = admission_driver.preflight(self.configuration, self.study, self.base)
        self.assertEqual(native, admission['native_state'])
        self.assertEqual(identity['source_id'], BASE_ID['source_id'])
        self.assertEqual(http.call_count, 4)
        self.assertEqual(http.call_args_list[2].args[1]['mode'], 'global')
        self.assertEqual(admission['configuration_record']['native_started_before'], 4)

    async def test_missing_extra_changed_or_uncertain_warmup_prevents_any_configuration(self):
        for kind in ('missing', 'changed_hash', 'uncertain', 'wrong_source', 'already_configured'):
            native, admission = copy.deepcopy(self.before_native), copy.deepcopy(self.before_admission)
            if kind == 'missing': native['records'].pop()
            elif kind == 'changed_hash': native['records'][0]['request_sha256'] = '0' * 64
            elif kind == 'uncertain': native['uncertain_total'] = 1
            elif kind == 'wrong_source': admission['source_id'] = 'another-study'
            else: admission['configured'] = True
            with self.subTest(kind=kind), patch.object(admission_driver, 'json_http', side_effect=[native, admission]) as http:
                with self.assertRaises(ValueError):
                    admission_driver.preflight(self.configuration, self.study, self.base)
                self.assertEqual(http.call_count, 2)


if __name__ == '__main__':
    unittest.main()
