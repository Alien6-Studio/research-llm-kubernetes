"""Local transport phases must not change requests, deadlines or late evidence."""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from v2.model_service import ModelApplication
from v2.service import Application
from v2.transport import request


PHASE_ORDER = (
    'request_task_started', 'connection_started', 'connection_established',
    'serialization_started', 'serialization_finished', 'write_started',
    'write_queued', 'emitted', 'status_received', 'headers_received',
    'body_received', 'body_decoded', 'close_started', 'close_complete',
)


class Writer:
    def __init__(self):
        self.written = bytearray()
        self.closed = False

    def write(self, value):
        self.written.extend(value)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def response(body, framing='length'):
    raw = json.dumps(body, separators=(',', ':')).encode()
    first = b'HTTP/1.1 200 OK\r\n'
    if framing == 'chunked':
        middle = len(raw) // 2
        chunks = b''.join(f'{len(part):x}\r\n'.encode() + part + b'\r\n'
                          for part in (raw[:middle], raw[middle:]))
        return first + b'Transfer-Encoding: chunked\r\n\r\n' + chunks + b'0\r\n\r\n'
    if framing == 'eof':
        return first + b'\r\n' + raw
    return first + f'Content-Length: {len(raw)}\r\n\r\n'.encode() + raw


def connection(raw, eof=True):
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    if eof:
        reader.feed_eof()
    return reader, Writer()


class TransportPhaseTests(unittest.IsolatedAsyncioTestCase):
    def assert_ordered(self, phases, expected=PHASE_ORDER):
        self.assertEqual(phases['clock'], 'time.monotonic')
        self.assertEqual(phases['clock_scope'], 'requesting_process')
        values = [phases[name] for name in expected]
        self.assertEqual(values, sorted(values))

    async def test_optional_capture_preserves_wire_bytes_result_and_callback(self):
        for framing in ('length', 'chunked', 'eof'):
            for payload in (None, {'prompt': 'A precise request', 'stream': False}):
                with self.subTest(framing=framing, payload=payload):
                    wires = []
                    results = []
                    for enabled in (False, True):
                        reader, writer = connection(response({'correct': True}, framing))
                        phases = {'clock_domain': 'test-client'}
                        callbacks = []

                        def sent():
                            callbacks.append(time.monotonic())
                            if enabled:
                                self.assertIn('emitted', phases)
                                self.assertNotIn('status_received', phases)

                        with patch('v2.transport.asyncio.open_connection',
                                   new=AsyncMock(return_value=(reader, writer))):
                            if enabled:
                                result = await request('http://local:8080/api/generate', payload,
                                                       sent=sent, phases=phases)
                            else:
                                result = await request('http://local:8080/api/generate', payload, 10, sent)
                        self.assertEqual(len(callbacks), 1)
                        self.assertTrue(writer.closed)
                        self.assertEqual(result, (200, {'correct': True}))
                        self.assertIsInstance(result, tuple)
                        self.assertEqual(len(result), 2)
                        wires.append(bytes(writer.written))
                        results.append(result)
                        if enabled:
                            self.assertEqual(phases['clock_domain'], 'test-client')
                            self.assert_ordered(phases)
                            self.assertLessEqual(phases['emitted'], callbacks[0])
                            self.assertLessEqual(callbacks[0], phases['status_received'])
                    self.assertEqual(wires[0], wires[1])
                    self.assertEqual(results[0], results[1])
                    body = b'' if payload is None else json.dumps(payload, separators=(',', ':')).encode()
                    method = 'GET' if payload is None else 'POST'
                    expected = (f'{method} /api/generate HTTP/1.1\r\nHost: local\r\n'
                                'Connection: close\r\nContent-Type: application/json\r\n'
                                f'Content-Length: {len(body)}\r\n\r\n').encode() + body
                    self.assertEqual(wires[0], expected)

    async def test_timeout_keeps_partial_phases_and_closes_the_connection(self):
        reader, writer = connection(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{', eof=False)
        phases = {}
        with patch('v2.transport.asyncio.open_connection',
                   new=AsyncMock(return_value=(reader, writer))):
            with self.assertRaises(TimeoutError):
                await request('http://local:8080/api/generate', {}, timeout=.01, phases=phases)
        self.assert_ordered(phases, PHASE_ORDER[:10] + ('close_started', 'close_complete'))
        self.assertNotIn('body_received', phases)
        self.assertNotIn('body_decoded', phases)
        self.assertTrue(writer.closed)

    async def test_connection_failure_retains_only_phases_that_were_reached(self):
        phases = {}
        sent = []
        with patch('v2.transport.asyncio.open_connection',
                   new=AsyncMock(side_effect=ConnectionRefusedError('test refusal'))):
            with self.assertRaises(ConnectionRefusedError):
                await request('http://local:8080/', sent=lambda: sent.append(True), phases=phases)
        self.assert_ordered(phases, PHASE_ORDER[:2])
        self.assertEqual(sent, [])
        for name in PHASE_ORDER[2:]:
            self.assertNotIn(name, phases)

    async def test_invalid_response_keeps_received_and_close_times(self):
        reader, writer = connection(b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nbad')
        phases = {}
        with patch('v2.transport.asyncio.open_connection',
                   new=AsyncMock(return_value=(reader, writer))):
            with self.assertRaises(json.JSONDecodeError):
                await request('http://local:8080/', phases=phases)
        self.assert_ordered(phases, PHASE_ORDER[:11] + ('close_started', 'close_complete'))
        self.assertNotIn('body_decoded', phases)

    async def test_generator_records_phases_in_worker_and_direct_paths(self):
        item = {'id': 'phases', 'offset': 0, 'task_status': 200, 'prompt_text': '', 'tokens': 1000}
        for mode in ('workers', 'direct_provider', 'direct_ollama'):
            with self.subTest(mode=mode):
                app = Application('controller', 'http://local:8080')
                config = {'trial_id': 'phases', 'deadline_seconds': 1, 'policy': 'none',
                          'worker_url': 'http://local:8080', 'provider': {'ollama_url': 'http://local:8080'}}
                if mode != 'workers':
                    config[mode] = True
                body = {'response': '{"label":"ok"}'} if mode == 'direct_ollama' else {'correct': True}
                reader, writer = connection(response(body))
                with patch('v2.transport.asyncio.open_connection',
                           new=AsyncMock(return_value=(reader, writer))):
                    row = await app.invoke(item, config, time.monotonic())
                self.assertTrue(row['correct'])
                self.assertEqual(row['status'], 200)
                phases = row['transport_phases']
                self.assertEqual(phases['clock_domain'], row['clock_domain'])
                self.assert_ordered(phases)
                self.assertLessEqual(row['launched'], phases['request_task_started'])
                self.assertLessEqual(phases['emitted'], row['emitted'])
                self.assertLessEqual(row['emitted'], phases['status_received'])
                self.assertLessEqual(phases['close_complete'], row['finished'])

    async def test_late_ollama_response_retains_complete_transport_phases(self):
        item = {'id': 'late-phases', 'offset': 0, 'task_status': 200, 'prompt_text': ''}
        config = {'direct_ollama': True, 'trial_id': 'late-phases', 'deadline_seconds': .015,
                  'provider': {'ollama_url': 'http://local:8080'}}
        reader, writer = connection(b'', eof=False)

        async def finish_later():
            await asyncio.sleep(.06)
            reader.feed_data(response({'response': '{"label":"ok"}', 'eval_count': 6}))
            reader.feed_eof()

        feeding = asyncio.create_task(finish_later())
        with patch('v2.transport.asyncio.open_connection',
                   new=AsyncMock(return_value=(reader, writer))):
            row = await ModelApplication('controller').invoke(item, config, time.monotonic())
        await feeding
        self.assertEqual(row['reason'], 'client_deadline')
        self.assertFalse(row['correct'])
        self.assertEqual(row['status'], 0)
        self.assertTrue(row['late_response']['correct'])
        self.assertEqual(row['late_response']['inference']['eval_count'], 6)
        phases = row['transport_phases']
        self.assertEqual(phases['clock_domain'], row['clock_domain'])
        self.assert_ordered(phases)
        self.assertLess(row['finished'], phases['status_received'])
        self.assertLessEqual(phases['close_complete'], row['model_call_drained_at'])

    async def test_unemitted_expired_rows_do_not_invent_transport_events(self):
        item = {'id': 'not-emitted', 'offset': 0}
        config = {'trial_id': 'expired', 'deadline_seconds': .01, 'direct_ollama': True}
        for app in (Application('controller'), ModelApplication('controller')):
            row = await app.invoke(item, config, time.monotonic() - 1)
            self.assertEqual(row['reason'], 'generator_not_emitted_deadline')
            self.assertIsNone(row['emitted'])
            self.assertEqual(row['transport_phases'], {
                'clock_domain': row['clock_domain'], 'clock': 'time.monotonic',
            })


if __name__ == '__main__':
    unittest.main()
