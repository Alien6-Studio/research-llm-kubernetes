"""Real asynchronous HTTP controls, including saturation and complete denominators."""
import asyncio
import unittest
from unittest.mock import patch

from v2.service import Application
from v2.transport import serve, request


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = Application('provider')
        self.provider_server = await serve(self.provider.handle, '127.0.0.1', 0)
        self.provider_url = 'http://127.0.0.1:' + str(self.provider_server.sockets[0].getsockname()[1])
        self.worker = Application('worker', self.provider_url)
        self.worker_server = await serve(self.worker.handle, '127.0.0.1', 0)
        self.worker_url = 'http://127.0.0.1:' + str(self.worker_server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        for server in [self.worker_server, self.provider_server]:
            server.close()
            await server.wait_closed()

    def item(self, i, **values):
        return {'id': str(i), 'trial_id': 'integration', 'tokens': 1000, 'service_seconds': .015,
                'remaining_budget_seconds': 1, 'policy': 'global', **values}

    async def test_real_reservation_is_consumed_once_and_all_stages_are_retained(self):
        status, body = await request(self.worker_url + '/work', self.item(0))
        self.assertEqual(status, 200)
        self.assertTrue(body['correct'])
        events = [body[k] for k in ['worker_received', 'queue_entered', 'queue_left', 'admission_started',
                                    'admission_finished', 'provider_call_started', 'provider_call_finished', 'worker_finished']]
        self.assertEqual(events, sorted(events))
        consumed = [e for e in self.provider.ledger.events if e['action'] == 'consumed']
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]['units'], 1000)

    async def test_queue_refusals_and_expiries_remain_recorded_under_saturation(self):
        results = await asyncio.gather(*(request(self.worker_url + '/work', self.item(i, service_seconds=.08,
                                         remaining_budget_seconds=.035, policy='none')) for i in range(50)))
        self.assertEqual(len(results), 50)
        self.assertTrue(any(code == 429 for code, _ in results))
        self.assertTrue(any(code == 504 for code, _ in results))
        self.assertEqual(len(self.worker.records), 50)
        self.assertEqual((self.worker.active, self.worker.queued), (0, 0))

    async def test_not_emitted_requests_are_not_dropped(self):
        controller = Application('controller', self.provider_url)
        import time
        row = await controller.invoke({'id': 'late', 'offset': 0}, {'deadline_seconds': .01, 'trial_id': 'late'}, time.monotonic() - 1)
        self.assertIsNone(row['emitted'])
        self.assertEqual(row['reason'], 'generator_not_emitted_deadline')
        self.assertGreater(row['latency_seconds'], 1)

    async def test_client_latency_uses_planned_arrival_and_no_remote_monotonic_origin(self):
        controller = Application('controller', self.provider_url)
        import time
        start = time.monotonic() - .02
        row = await controller.invoke(self.item(0, offset=0), {'deadline_seconds': 1, 'policy': 'none',
                    'trial_id': 'client', 'worker_url': self.worker_url}, start)
        self.assertEqual(row['status'], 200)
        self.assertAlmostEqual(row['latency_seconds'], row['finished'] - start)
        self.assertGreater(row['latency_seconds'], row['emitted_latency_seconds'])

    async def test_reset_is_refused_while_inference_is_running(self):
        inference = asyncio.create_task(request(self.provider_url + '/infer', self.item('busy', service_seconds=.1)))
        await asyncio.sleep(.03)
        status, _ = await request(self.provider_url + '/configure', {'tokens_per_second': 10})
        self.assertEqual(status, 409)
        self.assertEqual((await inference)[0], 200)

    async def test_complete_controller_path_preserves_every_arrival_and_role_record(self):
        controller = Application('controller', self.provider_url)
        trace = [self.item(i, offset=i * .01, policy='none') for i in range(8)]
        config = {'trace': trace, 'deadline_seconds': 1, 'policy': 'none', 'trial_id': 'control',
                  'worker_url': self.worker_url, 'worker_addresses': [self.worker_url],
                  'provider': {'mode': 'emulator'}}
        result = await controller.run_load(config)
        self.assertEqual(len(result['requests']), 8)
        self.assertTrue(all(r['emitted'] is not None for r in result['requests']))
        self.assertTrue(all(r['status'] == 200 for r in result['requests']))
        self.assertTrue(result['drained'])
        self.assertEqual(len(result['role_records']['provider']['records']), 8)
        self.assertEqual(len(result['role_records'][self.worker_url]['records']), 8)

    async def test_old_trial_cannot_consume_a_new_trial_budget(self):
        self.provider.config['trial_id'] = 'new'
        self.worker.config['trial_id'] = 'new'
        status, body = await request(self.worker_url + '/work', self.item('stale'))
        self.assertEqual(status, 409)
        self.assertEqual(body['reason'], 'stale_trial')
        status, _ = await request(self.provider_url + '/infer', self.item('stale'))
        self.assertEqual(status, 409)
        self.assertFalse(any(event['action'] == 'consumed' for event in self.provider.ledger.events))


if __name__ == '__main__':
    unittest.main()
