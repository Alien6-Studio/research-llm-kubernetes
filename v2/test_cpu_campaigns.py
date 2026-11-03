"""Host-specific campaign descriptors cannot silently exchange resource limits."""
import copy
import unittest

from v2.cpu_campaigns import CPU_CAMPAIGNS, cpu_campaign_for_plan, get_cpu_campaign
from v2.verify import verify_cpu_envelope


class CPUCampaignDescriptorTests(unittest.TestCase):
    def test_fra_paths_endpoints_and_48_gib_envelope_are_one_explicit_identity(self):
        data = get_cpu_campaign('v2-ollama-fra6s004-cpu')
        self.assertEqual(data['plan_path'], 'protocol/ollama-fra6s004-cpu-plan.json')
        self.assertEqual(data['trace_directory'], 'protocol/ollama-fra6s004-cpu')
        self.assertEqual(data['inference_url'], 'http://172.30.64.3:11437')
        self.assertEqual(data['monitor_url'], 'http://172.30.64.3:11436')
        self.assertEqual(data['client_root'], '/opt/alien6-fra6s004-cpu')
        self.assertEqual(data['inference_memory_bytes'], 48 * 1024 ** 3)
        self.assertEqual(data['host_memory_floor_bytes'], 16 * 1024 ** 3)
        self.assertTrue(data['terminal'])

    def test_confirmation_uses_fresh_paths_with_the_same_registered_resources(self):
        previous = get_cpu_campaign('v2-ollama-fra6s004-cpu')
        data = get_cpu_campaign('v2-ollama-fra6s004-cpu-confirmation')
        self.assertTrue(data['terminal'])
        self.assertEqual(data['plan_path'], 'protocol/ollama-fra6s004-cpu-confirmation-plan.json')
        self.assertEqual(data['trace_directory'], 'protocol/ollama-fra6s004-cpu-confirmation')
        for key in previous.keys() - {'prefix', 'cohort', 'plan_path', 'trace_directory', 'terminal'}:
            self.assertEqual(data[key], previous[key], key)
        self.assertEqual(cpu_campaign_for_plan({'continuation': {'cohort': data['cohort']}}), data)

    def test_long_window_retains_one_complete_calibration_with_distinct_sources(self):
        data = get_cpu_campaign('v2-ollama-fra6s004-cpu-long-window')
        previous = get_cpu_campaign('v2-ollama-fra6s004-cpu-confirmation')
        self.assertFalse(data['terminal'])
        self.assertTrue(previous['terminal'])
        self.assertEqual(data['measurement_seconds'], 335)
        self.assertEqual(data['maximum_measurement_seconds'], 335)
        self.assertEqual(data['maximum_duration_seconds'], 86400)
        self.assertNotEqual(data['calibration_source_id'], data['collection_source_id'])
        self.assertIn('confirmation-pilot', data['retained_calibration_archive'])
        self.assertEqual(len(data['calibration_pilot_sha256']), 64)
        self.assertEqual(data['inference_memory_bytes'], previous['inference_memory_bytes'])

    def test_old_28_gib_defaults_remain_replayable_but_cannot_authorize_fra(self):
        group = {'visible_hierarchy_limits': {'complete': True, 'cpu_quota_cores_upper_bound': 8,
                 'memory_max_bytes_upper_bound': 28 * 1024 ** 3, 'memory_swap_max_bytes_upper_bound': 0}}
        verify_cpu_envelope(group)
        with self.assertRaises(ValueError):
            verify_cpu_envelope(group, 'v2-ollama-fra6s004-cpu')
        group['visible_hierarchy_limits']['memory_max_bytes_upper_bound'] = 48 * 1024 ** 3
        verify_cpu_envelope(group, 'v2-ollama-fra6s004-cpu')
        with self.assertRaises(ValueError):
            verify_cpu_envelope(group)
        self.assertTrue(get_cpu_campaign('v2-ollama-dell-cpu')['terminal'])

    def test_returned_data_cannot_modify_the_shared_registry(self):
        data = get_cpu_campaign('v2-ollama-fra6s004-cpu')
        data['inference_memory_bytes'] = 1
        self.assertEqual(CPU_CAMPAIGNS['v2-ollama-fra6s004-cpu']['inference_memory_bytes'], 48 * 1024 ** 3)
        self.assertEqual(cpu_campaign_for_plan({'continuation': {'cohort': 'ollama-fra6s004-cpu'}})['prefix'], 'v2-ollama-fra6s004-cpu')
        with self.assertRaises(ValueError):
            get_cpu_campaign('v2-unregistered')


if __name__ == '__main__':
    unittest.main()
