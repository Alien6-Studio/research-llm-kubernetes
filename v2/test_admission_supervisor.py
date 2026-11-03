"""Fresh controls are bound to the extended proxy before any comparison arm."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location('admission_supervisor_contract',
    Path(__file__).resolve().parents[1]/'infra/run-fra6s004-admission.py')
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class AdmissionSupervisorTests(unittest.TestCase):
    def supervisor(self):
        obj = MODULE.AdmissionStudy.__new__(MODULE.AdmissionStudy)
        obj.manifest = {'run_root': 'runs/shared-admission'}
        obj.identity = {'source_id': 'study', 'git_base': '1'*40}
        obj.base_identity = SimpleNamespace(source_id='base')
        obj.previous_epoch = 'native-inference-proxy:test'
        obj.state = {'manifest_sha256': 'm'*64}
        obj.write_vm, obj.save = Mock(), Mock()
        obj.files = [{'path': 'runs/shared-admission/file'+str(i)+'.json',
                      'sha256': 'f'*64, 'bytes': 1} for i in range(16)]
        obj.proxy = {'source_id': 'study', 'clock_domain': obj.previous_epoch,
            'configured': False, 'active': [], 'occupied': 0, 'quarantined': 0,
            'started_total': 0, 'finished_total': 0,
            'native_state': {'started_total': 0, 'finished_total': 0, 'uncertain_total': 0}}
        obj.capture = Mock(side_effect=lambda *a, **k: json.dumps(obj.files if obj.capture.call_count == 1 else obj.proxy))
        return obj

    def test_empty_same_epoch_controls_get_exclusive_binding_with_all_hashes(self):
        obj = self.supervisor(); obj.bind_controls()
        path, raw = obj.write_vm.call_args.args
        value = json.loads(raw)
        self.assertEqual(path, 'runs/shared-admission/study-control-binding.json')
        self.assertEqual(value['files'], obj.files)
        self.assertEqual(value['manifest_sha256'], obj.state['manifest_sha256'])
        self.assertEqual(value['after_controls_admission_state'], obj.proxy)
        self.assertEqual(obj.state['control_binding']['path'], path)

    def test_native_generation_source_drift_or_reconfiguration_blocks_comparison(self):
        for name, value in (('source_id', 'other'), ('clock_domain', 'other'), ('configured', True),
                            ('occupied', 1), ('quarantined', 1), ('started_total', 1)):
            obj = self.supervisor(); obj.proxy[name] = value
            with self.subTest(name=name), self.assertRaises(RuntimeError): obj.bind_controls()
            obj.write_vm.assert_not_called()

    def test_budget_fits_24_hour_guard_including_smoke_and_shutdown(self):
        self.assertLess(MODULE.base.prospective_budget(24) + 240 + MODULE.base.RESERVE, 86400)
        self.assertIn('alien6-admission-proxy.service', MODULE.SERVICES)
        self.assertNotIn('alien6-ollama-proxy.service', MODULE.SERVICES)
        self.assertEqual(MODULE.PREFIX, 'v2-ollama-shared-admission')
        self.assertEqual(MODULE.base.CAPS['recovery'], 150)


if __name__ == '__main__': unittest.main()
