"""Resource deltas retain the actual observation interval and clock domain."""
import unittest
from v2.analyze import host_resources, native_resources


class ResourceTests(unittest.TestCase):
    def test_native_memory_integral_and_cpu_are_process_deltas(self):
        samples = [{'at': 100, 'processes': [{'pid': 1, 'cpu_seconds': 50, 'rss_bytes': 10}]},
                   {'at': 102, 'processes': [{'pid': 1, 'cpu_seconds': 51.5, 'rss_bytes': 30}]}]
        result = native_resources(samples)
        self.assertEqual(result['process_cpu_seconds_observed'], 1.5)
        self.assertEqual(result['process_rss_byte_seconds'], 40)
        self.assertEqual(result['observed_seconds'], 2)

    def test_vm_steal_is_separate_and_guest_ticks_are_not_double_counted(self):
        rows = [{'at': 10, 'host_cpu': 'cpu 10 0 0 10 0 0 0 0 9 0', 'node_cgroup': 'usage_usec 100\nthrottled_usec 10'},
                {'at': 12, 'host_cpu': 'cpu 30 0 10 60 0 0 0 20 19 0', 'node_cgroup': 'usage_usec 2000100\nthrottled_usec 20'}]
        result = host_resources(rows)
        self.assertEqual(result['vm_steal_fraction'], .2)
        self.assertEqual(result['vm_cpu_nonidle_fraction'], .5)
        self.assertEqual(result['node_cpu_seconds'], 2)
