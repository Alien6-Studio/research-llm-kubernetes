"""Replay actual in-memory proxy records, including rejected and uncertain calls."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from v2.admission_certificate import SCHEMA, collect_certificate, replay_certificate
from v2.admission_proxy import AdmissionProxy
from v2.model import expected_native_calls, pilot_item
from v2.test_native_completion import item, request_row
from v2.workload import model_request

BASE = {'source_id': 'base-test', 'git_base': '1' * 40}
STUDY = {'source_id': 'study-test', 'git_base': '1' * 40}


class AdmissionCertificateTests(unittest.IsolatedAsyncioTestCase):
    async def evidence(self, mode='global', outcome='success', active=False):
        proxy = AdmissionProxy(BASE, STUDY)
        async def successful(*args, **kwargs): return 200, {'done': True, 'response': '{"label":"ok"}'}
        with patch('v2.inference_proxy.request', side_effect=successful):
            for index in range(4):
                await proxy.handle('POST', '/api/generate', model_request(pilot_item(index)))
        proxy.configure('POST', {'mode': mode, 'capacity': 1, 'trial_id': 'admission-fixture', 'study_source_id': STUDY['source_id']})
        before = copy.deepcopy(proxy.admission_state('GET', '/study-admission/state?records=1', None)[1])
        entered, release = asyncio.Event(), asyncio.Event()
        async def native(*args, **kwargs):
            entered.set(); await release.wait()
            if outcome == 'timeout': raise TimeoutError('Synthetic unknown native outcome')
            return await successful()
        cfg = {'trial_id': 'admission-fixture', 'direct_ollama': True,
               'provider': {'ollama_url': 'http://fixture'}, 'trace': [item('a'), item('b')],
               'shared_admission': {'mode': mode, 'capacity': 1, 'study_source_id': STUDY['source_id'], 'git_base': STUDY['git_base']}}
        result = {'requests': [request_row('a'), request_row('b')]}
        with patch('v2.inference_proxy.request', side_effect=native):
            first = asyncio.create_task(proxy.handle('POST', '/api/generate', model_request(item('a'))))
            await entered.wait()
            if mode == 'global':
                await proxy.handle('POST', '/api/generate', model_request(item('b')))
                second = None
            else:
                second = asyncio.create_task(proxy.handle('POST', '/api/generate', model_request(item('b'))))
                await asyncio.sleep(0)
            mid = copy.deepcopy(proxy.admission_state('GET', '/study-admission/state?records=1', None)[1])
            release.set()
            await asyncio.gather(*[task for task in (first, second) if task], return_exceptions=True)
        terminal = mid if active else proxy.admission_state('GET', '/study-admission/state?records=1', None)[1]
        study = {**STUDY, 'admission_proxy_sha256': proxy.admission_proxy_sha256}
        base = {**BASE, 'proxy_sha256': proxy.proxy_sha256}
        certificate = {'schema': SCHEMA, 'pre_native_state': before['native_state'], 'pre_admission_state': before,
            'terminal_native_state': terminal['native_state'], 'terminal_admission_state': terminal,
            'expected_api': expected_native_calls(cfg, result), 'driver_began': 1, 'driver_deadline': 901,
            'driver_finished': 3, 'budget_seconds': 900,
            'driver_clock': {'clock': 'time.monotonic', 'scope': 'requesting_VM_process', 'pid': 1, 'clock_domain': 'fixture:1'},
            'polls': [{'driver_at': 2, 'native': terminal['native_state'], 'admission': terminal}]}
        return cfg, result, certificate, study, base

    async def test_global_rejections_are_covered_without_becoming_native_calls(self):
        cfg, result, cert, study, base = await self.evidence()
        verdict = replay_certificate(cfg, result, cert, study, base)
        self.assertTrue(verdict['coverage_valid'], verdict)
        self.assertTrue(verdict['native_complete'], verdict)
        self.assertEqual((verdict['counts']['api_attempts'], verdict['counts']['admitted'], verdict['counts']['rejected']), (2, 1, 1))

    async def test_bypass_can_hold_more_than_k_and_duplicate_payloads_keep_multiplicity(self):
        args = await self.evidence(mode='bypass')
        verdict = replay_certificate(*args)
        self.assertTrue(verdict['coverage_valid'], verdict)
        self.assertTrue(verdict['native_complete'], verdict)
        self.assertEqual(verdict['counts']['admitted'], 2)

    async def test_quarantined_and_still_active_native_work_censor_resources_only(self):
        for outcome, active in [('timeout', False), ('success', True)]:
            args = await self.evidence(outcome=outcome, active=active)
            verdict = replay_certificate(*args)
            self.assertTrue(verdict['coverage_valid'], verdict)
            self.assertFalse(verdict['native_complete'], verdict)
            self.assertEqual(verdict['execution_issues'], [])
            self.assertEqual(verdict['native_issues'], ['native_lifetime_not_certified'])

    async def test_source_epoch_payload_sequence_and_early_release_corruption_fail_closed(self):
        cfg, result, original, study, base = await self.evidence()
        mutations = [
            lambda c: c['terminal_admission_state'].update(source_id='wrong'),
            lambda c: c['terminal_admission_state'].update(clock_domain='other-epoch'),
            lambda c: c['terminal_admission_state']['records'][-1].update(request_sha256='f' * 64),
            lambda c: c['terminal_admission_state']['records'][-1].update(native_sequence=999),
            lambda c: c['terminal_admission_state']['records'][-1].update(finished=0),
            lambda c: c['terminal_admission_state'].update(occupied=2),
            lambda c: c['terminal_admission_state'].update(records=c['terminal_admission_state']['records'][:-1]),
        ]
        for mutate in mutations:
            cert = copy.deepcopy(original); mutate(cert)
            # Align aliases after deliberately mutating the single atomic snapshot.
            cert['polls'][0]['admission'] = cert['terminal_admission_state']
            cert['polls'][0]['native'] = cert['terminal_admission_state']['native_state']
            cert['terminal_native_state'] = cert['terminal_admission_state']['native_state']
            verdict = replay_certificate(cfg, result, cert, study, base)
            self.assertFalse(verdict['coverage_valid'], verdict)

    async def test_failed_collect_cannot_be_promoted_by_replay_of_an_earlier_snapshot(self):
        cfg, result, cert, study, base = await self.evidence()
        cert['collection_error'] = {'type': 'TimeoutError', 'detail': 'synthetic'}
        verdict = replay_certificate(cfg, result, cert, study, base)
        self.assertFalse(verdict['coverage_valid'])

    async def test_offline_replay_never_uses_http_and_ignores_claimed_success(self):
        cfg, result, cert, study, base = await self.evidence(outcome='timeout')
        cert.update(coverage_valid=True, native_complete=True)
        with patch('v2.admission_certificate.json_http', side_effect=AssertionError('No HTTP allowed')):
            verdict = replay_certificate(cfg, result, cert, study, base)
        self.assertTrue(verdict['coverage_valid'], verdict)
        self.assertFalse(verdict['native_complete'])

    async def test_collector_keeps_polling_after_uncertainty_until_other_callbacks_finish(self):
        cfg, result, cert, study, base = await self.evidence(mode='bypass', active=True)
        _, _, done, _, _ = await self.evidence(mode='bypass', outcome='timeout')
        # Build same-epoch observations from the completed fixture, then expose one callback as active.
        final = done['terminal_admission_state']; before = done['pre_admission_state']
        intermediate = copy.deepcopy(final)
        last = intermediate['records'].pop()
        last_native = intermediate['native_state']['records'].pop()
        intermediate['finished_total'] -= 1; intermediate['quarantined'] -= 1
        intermediate['active'] = [{key: value for key, value in last.items() if key not in ('finished', 'occupied_after')}]
        intermediate['active'][0].update(status=None, error=None, quarantined=False)
        intermediate['native_state']['finished_total'] -= 1; intermediate['native_state']['uncertain_total'] -= 1
        intermediate['native_state']['active'] = [{key: value for key, value in last_native.items() if key in ('sequence', 'request_sha256', 'started')}]
        class Clock:
            now = 2
            def monotonic(self): return self.now
            def sleep(self, value): self.now += value
        clock = Clock()
        with patch('v2.admission_certificate.json_http', side_effect=[intermediate, final]) as http, patch('v2.admission_certificate.time.monotonic', side_effect=clock.monotonic), patch('v2.admission_certificate.time.sleep', side_effect=clock.sleep):
            observed = collect_certificate(cfg, result, before['native_state'], before, study, base, 901, 1)
        self.assertEqual(http.call_count, 2)
        self.assertTrue(observed['coverage_valid'], observed)
        self.assertFalse(observed['native_complete'])


if __name__ == '__main__': unittest.main()
