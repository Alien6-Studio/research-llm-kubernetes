"""Counterexamples identify a code defect, not the prevalence of an old outcome."""
import unittest
import service


class HistoricalAdmissionTests(unittest.TestCase):
    def test_two_independent_windows_can_grant_permission_then_refuse_inference(self):
        config = dict(service.CONFIG)
        windows = {k: list(v) for k, v in service.WINDOWS.items()}
        try:
            service.CONFIG.update(tokens_per_second=10, requests_per_second=1000)
            for window in service.WINDOWS.values(): window[:] = [-1, 0, 0]
            # A crosses the boundary after its preliminary grant.
            self.assertTrue(service.debit('admission', 10, now=100.999)[0])
            self.assertTrue(service.debit('provider', 10, now=101.001)[0])
            # B obtains a fresh admission-window grant, but A already spent provider capacity.
            self.assertTrue(service.debit('admission', 10, now=101.002)[0])
            self.assertFalse(service.debit('provider', 10, now=101.003)[0])
        finally:
            service.CONFIG.clear()
            service.CONFIG.update(config)
            for key, value in windows.items(): service.WINDOWS[key][:] = value
