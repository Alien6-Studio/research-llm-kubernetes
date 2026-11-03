"""Replay must distinguish source contents and preserve local transport clocks."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from v2 import identity, verify
from v2.model_service import ModelApplication
from v2.network_probe import observe


class SourceMetadataReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'v2').mkdir()
        self.names = ['service.py', 'transport.py', 'quota.py', 'workload.py', 'network_policy.py']
        self.files = ['v2/' + name for name in self.names]
        for name in self.files:
            (self.root / name).write_bytes((name + '\n').encode())
        self.base = '1' * 40
        self.snapshot = identity.capture(self.root, self.files, git_base=self.base)
        identity.register(self.root, self.snapshot)
        self.instruments = json.loads((self.root / 'instruments/manifest.json').read_text())['sources']
        self.metadata = {**self.snapshot.metadata(), 'source_commit': self.base,
                         'code_sha256': self.code_hash(self.names)}

    def code_hash(self, names):
        digest = hashlib.sha256()
        for name in names:
            digest.update(name.encode() + b'\0' + (self.root / 'v2' / name).read_bytes())
        return digest.hexdigest()

    def replay(self, metadata=None):
        with patch.object(verify, 'ROOT', self.root):
            return verify.verify_source_metadata(metadata or self.metadata, self.instruments)

    def test_content_identity_takes_precedence_over_git_registry_key(self):
        self.instruments[self.base] = {'files': {}}
        self.assertEqual(self.replay(), self.instruments[self.snapshot.source_id]['files'])

    def test_new_five_file_hash_cannot_be_replaced_by_historical_four_file_hash(self):
        self.replay()
        with self.assertRaisesRegex(AssertionError, 'Executed source identity'):
            self.replay({**self.metadata, 'code_sha256': self.code_hash(self.names[:-1])})

    def test_actual_git_base_must_remain_separate_and_consistent(self):
        for field, value in [('git_base', '2' * 40), ('source_commit', self.snapshot.source_id)]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Git base separately'):
                self.replay({**self.metadata, field: value})
        missing = dict(self.metadata)
        del missing['git_base']
        with self.assertRaisesRegex(ValueError, 'Git base separately'):
            self.replay(missing)

    def test_diagnostic_content_identity_can_retain_git_base_without_a_legacy_alias(self):
        diagnostic = {key: self.metadata[key] for key in ('source_id', 'git_base')}
        self.replay(diagnostic)
        self.assertNotIn('source_commit', diagnostic)
        measured = dict(self.metadata)
        del measured['source_commit']
        with self.assertRaisesRegex(ValueError, 'Git base separately'):
            self.replay(measured)

    def test_altered_snapshot_fails_even_when_code_hash_field_is_unchanged(self):
        record = self.instruments[self.snapshot.source_id]['files']['v2/network_policy.py']
        (self.root / record['path']).write_bytes(b'altered archived source')
        with self.assertRaisesRegex(ValueError, 'Registered source bytes differ'):
            self.replay()

    def test_content_entry_is_verified_without_a_code_hash_for_transport_controls(self):
        data = dict(self.metadata)
        del data['code_sha256']
        self.replay(data)
        data['source_id'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'Invalid content source'):
            self.replay(data)

    def test_measured_driver_controller_and_monitor_hashes_match_the_content_snapshot(self):
        extra = {'driver_sha256': 'v2/model.py', 'model_controller_sha256': 'v2/model_service.py',
                 'monitor_sha256': 'v2/inference_monitor.py'}
        for name in extra.values():
            (self.root / name).write_text(name + '\n')
        snapshot = identity.capture(self.root, self.files + list(extra.values()), git_base=self.base)
        identity.register(self.root, snapshot)
        self.instruments = json.loads((self.root / 'instruments/manifest.json').read_text())['sources']
        data = {**self.metadata, **snapshot.metadata()}
        for field, name in extra.items():
            data[field] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        self.replay(data)
        for field in extra:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'measured component hash'):
                self.replay({**data, field: '0' * 64})

    def test_legacy_git_key_still_replays_only_its_original_four_file_hash(self):
        self.instruments[self.base] = self.instruments[self.snapshot.source_id]
        historical = {'source_commit': self.base, 'code_sha256': self.code_hash(self.names[:-1])}
        self.replay(historical)
        with self.assertRaisesRegex(AssertionError, 'Executed source identity'):
            self.replay({**historical, 'code_sha256': self.code_hash(self.names)})

    def test_observed_pods_must_match_content_identity_and_git_base(self):
        role = {key: self.metadata[key] for key in ('source_id', 'source_commit', 'git_base', 'code_sha256')}
        verify.verify_role_identity(role, self.metadata)
        for key, value in [('source_id', 'sha256:' + '0' * 64), ('source_commit', '2' * 40), ('git_base', '2' * 40)]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                verify.verify_role_identity({**role, key: value}, self.metadata)
        missing = dict(role)
        del missing['source_id']
        with self.assertRaisesRegex(AssertionError, 'pod source identity'):
            verify.verify_role_identity(missing, self.metadata)

    def test_explicitly_unavailable_samples_are_not_filled_with_invented_identity(self):
        verify.verify_role_identity({'unavailable': 'ConnectionError'}, self.metadata)
        with self.assertRaisesRegex(AssertionError, 'pod source identity'):
            verify.verify_role_identity({'unavailable': 'ConnectionError', 'source_id': 'wrong'}, self.metadata)


class TransportPhaseReplayTests(unittest.TestCase):
    def complete(self):
        phases = {'clock': 'time.monotonic', 'clock_scope': 'requesting_process',
                  'clock_domain': 'client-domain'}
        phases.update({name: float(index + 10) for index, name in enumerate(verify.PHASE_ORDER)})
        return {'clock_domain': 'client-domain', 'scheduled': 8.0, 'launched': 9.0,
                'emitted': 17.01, 'finished': 24.0, 'status': 200, 'transport_phases': phases}

    def test_complete_shared_transport_and_smaller_network_probe_both_replay(self):
        row = self.complete()
        verify.verify_transport_phases(row)
        for name in ('serialization_started', 'serialization_finished', 'write_queued', 'clock_domain'):
            del row['transport_phases'][name]
        verify.verify_transport_phases(row)

    def test_partial_connection_failure_retains_only_reached_stages(self):
        row = self.complete()
        row.update(status=0, emitted=None, finished=12.0, error='ConnectionRefusedError')
        row['transport_phases'] = {key: value for key, value in row['transport_phases'].items()
                                   if key in ('clock', 'clock_scope', 'clock_domain', *verify.CORE_PHASES[:2])}
        verify.verify_transport_phases(row)

    def test_partial_timeout_and_decode_failure_allow_cleanup_after_last_reached_stage(self):
        for last, error in [('headers_received', 'TimeoutError'), ('body_received', 'JSONDecodeError')]:
            with self.subTest(last=last):
                row = self.complete()
                row.update(error=error)
                index = verify.PHASE_ORDER.index(last)
                allowed = {'clock', 'clock_scope', 'clock_domain', *verify.PHASE_ORDER[:index + 1],
                           'close_started', 'close_complete'}
                row['transport_phases'] = {key: value for key, value in row['transport_phases'].items() if key in allowed}
                verify.verify_transport_phases(row)

    def test_late_model_response_may_drain_after_client_finished(self):
        row = self.complete()
        row.update(status=0, reason='client_deadline', finished=19.0, model_call_drained_at=24.0,
                   late_response={'status': 200, 'correct': True})
        verify.verify_transport_phases(row)
        del row['model_call_drained_at']
        with self.assertRaisesRegex(ValueError, 'recorded late drain'):
            verify.verify_transport_phases(row)

    def test_late_emission_can_be_observed_after_client_expiry_before_drain(self):
        row = self.complete()
        row.update(status=0, reason='client_deadline', finished=16.5, model_call_drained_at=24.0)
        verify.verify_transport_phases(row)
        row['model_call_drained_at'] = 16.0
        with self.assertRaisesRegex(ValueError, 'drain precedes'):
            verify.verify_transport_phases(row)

    def test_clock_domains_are_not_combined(self):
        row = self.complete()
        row['transport_phases']['clock_domain'] = 'another-host'
        with self.assertRaisesRegex(ValueError, 'different clock domain'):
            verify.verify_transport_phases(row)
        row = self.complete()
        del row['clock_domain']
        row.update(launched=10000.0, finished=1.0, emitted=9000.0)
        # Only the declared phase-domain order is comparable in this record.
        verify.verify_transport_phases(row)

    def test_out_of_order_or_skipped_stages_fail(self):
        row = self.complete()
        row['transport_phases']['body_received'] = 16.0
        with self.assertRaisesRegex(ValueError, 'out of order'):
            verify.verify_transport_phases(row)
        row = self.complete()
        del row['transport_phases']['connection_established']
        with self.assertRaisesRegex(ValueError, 'preceding stage'):
            verify.verify_transport_phases(row)

    def test_callback_outside_its_local_emission_stage_fails(self):
        for emission in (16.9, 18.1):
            row = self.complete()
            row['emitted'] = emission
            with self.subTest(emission=emission), self.assertRaisesRegex(ValueError, 'callback'):
                verify.verify_transport_phases(row)

    def test_unknown_clock_or_nonfinite_times_fail(self):
        for field, value in [('clock', 'time.time'), ('clock_scope', 'remote_process'),
                             ('connection_started', float('nan')), ('connection_started', True)]:
            row = self.complete()
            row['transport_phases'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify.verify_transport_phases(row)
        row = self.complete()
        row['finished'] = float('inf')
        with self.assertRaisesRegex(ValueError, 'not finite'):
            verify.verify_transport_phases(row)

    def test_not_emitted_record_can_have_only_clock_metadata(self):
        row = {'clock_domain': 'client-domain', 'emitted': None, 'status': 0,
               'transport_phases': {'clock': 'time.monotonic', 'clock_domain': 'client-domain'}}
        verify.verify_transport_phases(row)
        row['emitted'] = 1.0
        with self.assertRaisesRegex(ValueError, 'Recorded emission differs'):
            verify.verify_transport_phases(row)

    def test_historical_rows_without_optional_phases_keep_their_original_meaning(self):
        verify.verify_transport_phases({'status': 200, 'finished': 1.0})

    def test_client_outcome_cannot_precede_launch_even_without_transport_events(self):
        row = {'clock_domain': 'client-domain', 'launched': 10., 'finished': 9., 'emitted': None,
               'status': 0, 'transport_phases': {'clock': 'time.monotonic', 'clock_domain': 'client-domain'}}
        with self.assertRaisesRegex(ValueError, 'precedes the request launch'):
            verify.verify_transport_phases(row)

    def test_normal_response_cannot_hide_a_late_body_behind_a_drain_timestamp(self):
        row = self.complete()
        row.update(finished=22., model_call_drained_at=24.)
        with self.assertRaisesRegex(ValueError, 'recorded late drain'):
            verify.verify_transport_phases(row)

    def test_http_error_response_and_late_success_both_need_complete_response_phases(self):
        for late in (False, True):
            row = self.complete()
            row['status'] = 500
            if late:
                row.update(status=0, reason='client_deadline', finished=19., model_call_drained_at=24.,
                           late_response={'status': 200, 'correct': True})
            verify.verify_transport_phases(row)
            del row['transport_phases']['body_decoded']
            with self.subTest(late=late), self.assertRaisesRegex(ValueError, 'complete response phases'):
                verify.verify_transport_phases(row)

    def test_started_serialization_must_finish_before_writing(self):
        row = self.complete()
        del row['transport_phases']['serialization_finished']
        with self.assertRaisesRegex(ValueError, 'completion of serialization'):
            verify.verify_transport_phases(row)


class ProducedPhaseReplayTests(unittest.IsolatedAsyncioTestCase):
    class Writer:
        def write(self, _raw):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    def stream(self, body, *, delayed=False):
        raw = json.dumps(body).encode()
        reader = asyncio.StreamReader()
        reader.feed_data(f'HTTP/1.1 200 OK\r\nContent-Length: {len(raw)}\r\n\r\n'.encode())
        if not delayed:
            reader.feed_data(raw)
            reader.feed_eof()
        return reader, self.Writer(), raw

    async def test_current_generator_record_replays_without_network_or_ollama(self):
        reader, writer, _raw = self.stream({'response': '{"label":"ok"}'})
        item = {'id': 'replay', 'offset': 0, 'task_status': 200, 'prompt_text': ''}
        config = {'direct_ollama': True, 'deadline_seconds': 1, 'trial_id': 'replay',
                  'provider': {'ollama_url': 'http://local'}}
        with patch('v2.transport.asyncio.open_connection', new=AsyncMock(return_value=(reader, writer))):
            row = await ModelApplication('controller').invoke(item, config, time.monotonic())
        self.assertEqual(row['status'], 200)
        verify.verify_transport_phases(row)

    async def test_current_late_generation_record_replays_its_later_drain(self):
        reader, writer, raw = self.stream({'response': '{"label":"ok"}'}, delayed=True)

        async def finish_response():
            await asyncio.sleep(.04)
            reader.feed_data(raw)
            reader.feed_eof()

        delayed = asyncio.create_task(finish_response())
        item = {'id': 'late-replay', 'offset': 0, 'task_status': 200, 'prompt_text': ''}
        config = {'direct_ollama': True, 'deadline_seconds': .01, 'trial_id': 'late-replay',
                  'provider': {'ollama_url': 'http://local'}}
        with patch('v2.transport.asyncio.open_connection', new=AsyncMock(return_value=(reader, writer))):
            row = await ModelApplication('controller').invoke(item, config, time.monotonic())
        await delayed
        self.assertEqual(row['reason'], 'client_deadline')
        self.assertGreater(row['transport_phases']['body_received'], row['finished'])
        verify.verify_transport_phases(row)

    async def test_current_network_probe_record_replays_its_smaller_phase_set(self):
        reader, writer, _raw = self.stream({'model_info': {'test': True}})
        item = {'id': 'probe-replay', 'offset': 0, 'prompt_text': ''}
        with patch('v2.network_probe.asyncio.open_connection', new=AsyncMock(return_value=(reader, writer))):
            row = await observe('http://local/api/show', item, time.monotonic())
        self.assertEqual(row['status'], 200)
        self.assertNotIn('write_queued', row['transport_phases'])
        verify.verify_transport_phases(row)
