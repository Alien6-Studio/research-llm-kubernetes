from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

import service
from run import configuration, trace


class QuotaTests(unittest.TestCase):
    def setUp(self):
        service.CONFIG.update({"tokens_per_second": 4000, "requests_per_second": 1000, "mode": "emulator"})
        for name in service.WINDOWS:
            service.WINDOWS[name][:] = [-1, 0, 0]

    def test_token_reservations_share_a_global_window(self):
        self.assertTrue(service.debit("provider", 2200, now=100.1)[0])
        self.assertTrue(service.debit("provider", 1000, now=100.2)[0])
        self.assertFalse(service.debit("provider", 1000, now=100.3)[0])
        self.assertTrue(service.debit("provider", 2200, now=101.1)[0])

    def test_request_limit_is_independent_of_token_limit(self):
        service.CONFIG["requests_per_second"] = 1
        self.assertTrue(service.debit("provider", 1, now=100.1)[0])
        self.assertFalse(service.debit("provider", 1, now=100.2)[0])

    def test_admission_does_not_double_debit_the_provider(self):
        self.assertTrue(service.debit("admission", 3000, now=100.1)[0])
        self.assertTrue(service.debit("provider", 3000, now=100.1)[0])

    def test_concurrent_requests_cannot_overdraw_quota(self):
        service.CONFIG["tokens_per_second"] = 10
        with ThreadPoolExecutor(max_workers=16) as executor:
            outcomes = list(executor.map(lambda _: service.debit("provider", 1, now=100.1)[0], range(100)))
        self.assertEqual(sum(outcomes), 10)

    def test_http_responses_record_real_quota_rejections(self):
        server = service.Server(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(service.time, "time", return_value=100.1):
                payload = {"id": "request", "tokens": 3000, "service_seconds": 0}
                first, body = service.http(base + "/infer", payload)
                second, denied = service.http(base + "/infer", payload)
            self.assertEqual(first, 200)
            self.assertTrue(body["correct"])
            self.assertEqual(second, 429)
            self.assertEqual(denied["reason"], "upstream_quota")
        finally:
            server.shutdown()
            server.server_close()


class TraceTests(unittest.TestCase):
    def test_load_is_identical_across_replica_and_policy_arms(self):
        left = configuration("quota", "none", 1, 0)
        right = configuration("quota", "global", 8, 0)
        self.assertEqual(left["trace"], right["trace"])

    def test_repetitions_use_distinct_seeded_traces(self):
        self.assertNotEqual(trace("quota", 0, 3, "emulator"), trace("quota", 1, 3, "emulator"))
        self.assertEqual(trace("quota", 0, 3, "emulator"), trace("quota", 0, 3, "emulator"))

    def test_scheduled_arrivals_are_ordered_and_within_interval(self):
        items = trace("bursty", 0, 5, "emulator")
        self.assertTrue(all(0 < item["offset"] < 5 for item in items))
        self.assertEqual(sorted(item["offset"] for item in items), [item["offset"] for item in items])


if __name__ == "__main__":
    unittest.main()
