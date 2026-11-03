"""Concurrent and boundary controls for actual reservation semantics."""
from concurrent.futures import ThreadPoolExecutor
import unittest

from v2.quota import Ledger


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.1
        self.ledger = Ledger(10, 100, clock=lambda: self.now)

    def test_concurrent_reservations_then_consumption_share_one_limit(self):
        def attempt(i):
            if self.ledger.reserve(str(i), 1)['allowed']:
                return self.ledger.consume(str(i), 1)
            return False
        with ThreadPoolExecutor(max_workers=32) as pool:
            self.assertEqual(sum(pool.map(attempt, range(100))), 10)
        self.assertEqual(self.ledger.snapshot()['windows'][100]['consumed_units'], 10)

    def test_unconsumed_lease_expires_and_restores_capacity(self):
        self.assertTrue(self.ledger.reserve('unused', 10, ttl=.2)['allowed'])
        self.assertFalse(self.ledger.reserve('blocked', 1)['allowed'])
        self.now = 100.31
        self.assertTrue(self.ledger.direct('available', 10)[0])
        self.assertFalse(self.ledger.consume('unused', 10))

    def test_consumption_never_debits_twice_or_refunds_executed_work(self):
        self.assertTrue(self.ledger.reserve('one', 10)['allowed'])
        self.assertTrue(self.ledger.consume('one', 10))
        self.assertFalse(self.ledger.consume('one', 10))
        self.assertFalse(self.ledger.release('one'))
        self.assertFalse(self.ledger.direct('two', 1)[0])

    def test_window_boundary_invalidates_old_permission_without_spending_new_quota(self):
        self.now = 100.999
        self.assertTrue(self.ledger.reserve('old', 10)['allowed'])
        self.now = 101.001
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda i: self.ledger.direct(f'new-{i}', 1)[0], range(50)))
        self.assertFalse(self.ledger.consume('old', 10))
        self.assertEqual(sum(results), 10)
        self.assertEqual(self.ledger.snapshot()['windows'][100]['consumed_units'], 0)

    def test_cancellation_releases_only_an_unconsumed_lease(self):
        self.ledger.reserve('cancelled', 10)
        self.assertTrue(self.ledger.release('cancelled'))
        self.assertFalse(self.ledger.release('cancelled'))
        self.assertTrue(self.ledger.direct('next', 10)[0])

    def test_request_and_unit_limits_and_payload_integrity(self):
        ledger = Ledger(100, 1, clock=lambda: self.now)
        ledger.reserve('one', 2)
        self.assertFalse(ledger.consume('one', 1))
        self.assertTrue(ledger.consume('one', 2))
        self.assertFalse(ledger.direct('two', 1)[0])
        with self.assertRaises(ValueError):
            ledger.reserve('negative', -1)


if __name__ == '__main__':
    unittest.main()
