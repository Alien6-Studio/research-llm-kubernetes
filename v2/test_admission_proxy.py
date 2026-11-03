"""Shared capacity follows native lifetimes, not client waiting lifetimes."""
import asyncio
import unittest
from unittest.mock import patch

from v2.admission_proxy import AdmissionProxy

BASE = {'source_id': 'base-test', 'git_base': '1' * 40}
STUDY = {'source_id': 'study-test', 'git_base': '1' * 40}
PAYLOAD = {'model': 'test-fixture', 'prompt': 'synthetic', 'stream': False}


def configure(proxy, mode='global', capacity=1):
    return proxy.configure('POST', {'mode': mode, 'capacity': capacity,
        'trial_id': 'admission-synthetic', 'study_source_id': STUDY['source_id']})


class AdmissionProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_timeout_does_not_release_the_shared_permit(self):
        proxy = AdmissionProxy(BASE, STUDY)
        self.assertEqual(configure(proxy)[0], 200)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def native(url, payload, timeout):
            calls.append((url, payload, timeout)); entered.set()
            await release.wait()
            return 200, {'done': True, 'response': '{"label":"ok"}'}

        with patch('v2.inference_proxy.request', side_effect=native):
            pending = asyncio.create_task(proxy.handle('POST', '/api/generate', PAYLOAD))
            await entered.wait()
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), .001)
            rejected = await proxy.handle('POST', '/api/generate', PAYLOAD)
            self.assertEqual(rejected[0], 429)
            self.assertEqual(proxy.occupied, 1)
            self.assertEqual(len(calls), 1)
            release.set(); self.assertEqual((await pending)[0], 200)
            self.assertEqual(proxy.occupied, 0)
            self.assertEqual((await proxy.handle('POST', '/api/generate', PAYLOAD))[0], 200)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call == ('http://127.0.0.1:11435/api/generate', PAYLOAD, 180) for call in calls))
        records = sorted(proxy.admission_records, key=lambda row: row['sequence'])
        self.assertEqual([row['decision'] for row in records], ['admitted', 'rejected', 'admitted'])
        self.assertEqual([row['native_sequence'] for row in records], [1, None, 2])
        self.assertFalse(records[1]['permit_released'])

    async def test_bypass_uses_identical_forwarding_without_the_shared_cap(self):
        proxy = AdmissionProxy(BASE, STUDY); configure(proxy, mode='bypass')
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def native(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: entered.set()
            await release.wait()
            return 200, {'done': True}

        with patch('v2.inference_proxy.request', side_effect=native):
            pending = [asyncio.create_task(proxy.handle('POST', '/api/generate', PAYLOAD)) for _ in range(2)]
            await entered.wait(); self.assertEqual(proxy.occupied, 2)
            release.set(); await asyncio.gather(*pending)
        self.assertEqual(proxy.occupied, 0)
        self.assertEqual([row['native_sequence'] for row in proxy.admission_records], [1, 2])

    async def test_uncertain_native_outcome_quarantines_capacity_until_fresh_process(self):
        for outcome in ('timeout', 'not_done', 'http_error'):
            proxy = AdmissionProxy(BASE, STUDY); configure(proxy)
            async def native(*args, **kwargs):
                if outcome == 'timeout': raise TimeoutError('synthetic native timeout')
                return (200, {'done': False}) if outcome == 'not_done' else (500, {'error': 'synthetic'})
            with self.subTest(outcome=outcome), patch('v2.inference_proxy.request', side_effect=native):
                if outcome == 'timeout':
                    with self.assertRaises(TimeoutError): await proxy.handle('POST', '/api/generate', PAYLOAD)
                else:
                    await proxy.handle('POST', '/api/generate', PAYLOAD)
                self.assertEqual((proxy.occupied, proxy.quarantined, proxy.uncertain_total), (1, 1, 1))
                self.assertEqual(proxy.admission_active, {})
                self.assertEqual((await proxy.handle('POST', '/api/generate', PAYLOAD))[0], 429)
                self.assertEqual(configure(proxy)[0], 409)
            fresh = AdmissionProxy(BASE, STUDY)
            self.assertNotEqual(fresh.clock_domain, proxy.clock_domain)
            self.assertEqual((fresh.occupied, fresh.quarantined), (0, 0))

    async def test_configuration_is_source_bound_and_cannot_change_during_or_after_trial(self):
        proxy = AdmissionProxy(BASE, STUDY)
        invalid = {'mode': 'global', 'capacity': True, 'trial_id': 'admission-test', 'study_source_id': 'study-test'}
        self.assertEqual(proxy.configure('POST', invalid)[0], 400)
        self.assertEqual(configure(proxy)[0], 200)
        self.assertEqual(configure(proxy, mode='bypass')[0], 409)
        self.assertEqual(proxy.mode, 'global')
        self.assertEqual(configure(AdmissionProxy())[0], 503)

    async def test_state_queries_reconcile_rejected_attempts_without_native_generations(self):
        proxy = AdmissionProxy(BASE, STUDY); configure(proxy)
        proxy.occupied = proxy.quarantined = 1
        for _ in range(3): await proxy.handle('POST', '/api/generate', PAYLOAD)
        code, state = await proxy.handle('GET', '/study-admission/state?after=2&records=1', None)
        self.assertEqual(code, 200)
        self.assertEqual(state['source_id'], 'study-test')
        self.assertEqual(state['base_source_id'], 'base-test')
        self.assertEqual((state['started_total'], state['finished_total']), (3, 3))
        self.assertEqual([row['sequence'] for row in state['records']], [3])
        self.assertEqual(proxy.started_total, 0)
        for suffix in ('?after=4', '?after=-1', '?after=01', '?after=0&after=0', '?records=2'):
            self.assertEqual((await proxy.handle('GET', '/study-admission/state' + suffix, None))[0], 400)

    async def test_unregistered_native_generation_or_model_mutation_cannot_bypass_admission(self):
        proxy = AdmissionProxy(BASE, STUDY); configure(proxy)
        with patch('v2.inference_proxy.request', side_effect=AssertionError('No unregistered native call')):
            for method, path in (('POST', '/api/chat'), ('POST', '/api/embed'), ('POST', '/api/pull'),
                                 ('POST', '/api/generate?unexpected=1'), ('GET', '/api/generate')):
                self.assertEqual((await proxy.handle(method, path, PAYLOAD))[0], 404)
        self.assertEqual(proxy.started_total, 0)


if __name__ == '__main__':
    unittest.main()
