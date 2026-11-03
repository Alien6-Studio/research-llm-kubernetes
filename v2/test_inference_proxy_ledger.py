"""Native completion evidence must survive a disconnected downstream client."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from v2 import inference_proxy, transport
from v2.inference_proxy import InferenceProxy


IDENTITY = {'source_id': 'sha256:' + 'a' * 64, 'git_base': 'b' * 40}


class MemoryWriter:
    """The real HTTP transport, with streams in memory and no bound socket."""

    def __init__(self, peer, closed=None):
        self.peer = peer
        self.closed = closed or asyncio.Event()
        self.disconnect = None

    def write(self, data):
        if self.disconnect is not None and self.disconnect.is_set():
            raise ConnectionResetError('Downstream connection is closed')
        self.peer.feed_data(data)

    async def drain(self):
        pass

    def close(self):
        self.closed.set()
        self.peer.feed_eof()

    async def wait_closed(self):
        pass


class NativeLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def state(self, proxy, query=''):
        status, state = await proxy.handle('GET', '/study-state' + query, None)
        self.assertEqual(status, 200)
        self.assertEqual(state['started_total'], state['finished_total'] + len(state['active']))
        return state

    async def test_success_preserves_payload_options_timeout_response_and_log(self):
        proxy = InferenceProxy(IDENTITY)
        payload = {'model': 'pinned-model', 'prompt': 'Café', 'stream': False,
                   'options': {'seed': 17, 'temperature': 0, 'num_ctx': 8192}}
        raw = {'done': True, 'response': '{"label":"ok"}', 'eval_count': 5,
               'total_duration': 123, 'context': [4, 5]}
        result = (200, raw)
        with patch.object(inference_proxy, 'request', new=AsyncMock(return_value=result)) as upstream:
            with patch('builtins.print') as log:
                returned = await proxy.handle('POST', '/api/generate', payload)
        self.assertIs(returned, result)
        self.assertIs(returned[1], raw)
        upstream.assert_awaited_once_with('http://127.0.0.1:11435/api/generate', payload, timeout=180)
        self.assertIs(upstream.call_args.args[1], payload)
        logged = json.loads(log.call_args.args[0])
        self.assertEqual(set(logged), {'at', 'finished', 'path', 'status', 'response_json_bytes'})
        self.assertEqual(logged['status'], 200)
        self.assertEqual(logged['path'], '/api/generate')
        state = await self.state(proxy, '?after=0&records=1')
        self.assertEqual({key: state[key] for key in IDENTITY}, IDENTITY)
        self.assertEqual(state['proxy_sha256'], hashlib.sha256(Path(inference_proxy.__file__).read_bytes()).hexdigest())
        record, = state['records']
        expected = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        self.assertEqual(record['request_sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(record['response'], raw)
        self.assertEqual(record['sequence'], 1)
        self.assertLessEqual(record['started'], record['finished'])
        self.assertTrue(record['done'])
        self.assertFalse(record['uncertain'])
        self.assertIsNone(record['error'])
        self.assertEqual(state['uncertain_total'], 0)
        self.assertEqual((await self.state(proxy))['clock_domain'], state['clock_domain'])
        self.assertNotEqual(InferenceProxy(IDENTITY).clock_domain, state['clock_domain'])
        self.assertNotIn('records', await self.state(proxy))
        self.assertEqual((await self.state(proxy, '?after=1&records=1'))['records'], [])

    async def test_unknown_or_failed_native_outcome_remains_uncertain(self):
        for outcome in (TimeoutError('native timeout'), asyncio.CancelledError(),
                        (200, {'done': False}), (200, {'response': 'no done flag'}),
                        (500, {'done': True, 'error': 'upstream failure'})):
            with self.subTest(outcome=outcome):
                proxy = InferenceProxy(IDENTITY)
                upstream = AsyncMock(side_effect=outcome) if isinstance(outcome, BaseException) else AsyncMock(return_value=outcome)
                with patch.object(inference_proxy, 'request', new=upstream), patch('builtins.print'):
                    if isinstance(outcome, BaseException):
                        with self.assertRaises(type(outcome)):
                            await proxy.handle('POST', '/api/generate', {'prompt': 'test'})
                    else:
                        self.assertIs(await proxy.handle('POST', '/api/generate', {'prompt': 'test'}), outcome)
                state = await self.state(proxy, '?records=1')
                self.assertEqual(state['active'], [])
                self.assertEqual(state['finished_total'], 1)
                self.assertEqual(state['uncertain_total'], 1)
                self.assertTrue(state['records'][0]['uncertain'])
                if isinstance(outcome, BaseException):
                    self.assertEqual(state['records'][0]['error'], type(outcome).__name__)
                    self.assertIsNone(state['records'][0]['status'])
                    self.assertIsNone(state['records'][0]['response'])
                with patch.object(inference_proxy, 'request', new=AsyncMock(return_value=(200, {'done': True}))), patch('builtins.print'):
                    await proxy.handle('POST', '/api/generate', {'prompt': 'later success'})
                self.assertEqual((await self.state(proxy))['uncertain_total'], 1)

    async def test_concurrent_completions_keep_unique_sequences_and_all_terminal_records(self):
        proxy = InferenceProxy(IDENTITY)
        release = [asyncio.Event(), asyncio.Event()]

        async def native(url, payload, timeout):
            await release[payload['index']].wait()
            return 200, {'done': True, 'index': payload['index']}

        with patch.object(inference_proxy, 'request', new=native), patch('builtins.print'):
            calls = [asyncio.create_task(proxy.handle('POST', '/api/generate?controlled=1', {'index': index}))
                     for index in range(2)]
            try:
                await asyncio.sleep(0)
                self.assertEqual([row['sequence'] for row in (await self.state(proxy))['active']], [1, 2])
                release[1].set()
                await calls[1]
                state = await self.state(proxy, '?records=1&after=0')
                self.assertEqual([row['sequence'] for row in state['active']], [1])
                self.assertEqual([row['sequence'] for row in state['records']], [2])
                release[0].set()
                await calls[0]
            finally:
                for event in release:
                    event.set()
                await asyncio.gather(*calls)
        state = await self.state(proxy, '?records=1&after=0')
        self.assertEqual({row['sequence'] for row in state['records']}, {1, 2})
        self.assertEqual(state['finished_total'], 2)
        self.assertEqual(state['active'], [])
        self.assertEqual(state['uncertain_total'], 0)
        self.assertEqual([row['sequence'] for row in (await self.state(proxy, '?after=1&records=1'))['records']], [2])

    async def test_real_handler_survives_downstream_timeout_and_reports_native_active(self):
        proxy = InferenceProxy(IDENTITY)
        entered, release = asyncio.Event(), asyncio.Event()
        callbacks, handlers = [], []
        client_reader, server_reader = asyncio.StreamReader(), asyncio.StreamReader()
        client_writer = MemoryWriter(server_reader)
        server_writer = MemoryWriter(client_reader)
        server_writer.disconnect = client_writer.closed

        async def native(url, payload, timeout):
            self.assertEqual(timeout, 180)
            entered.set()
            await release.wait()
            return 200, {'done': True, 'response': 'finished after client left'}

        async def fake_start_server(callback, *args, **kwargs):
            callbacks.append(callback)
            return object()

        async def connect(*args, **kwargs):
            handlers.append(asyncio.create_task(callbacks[0](server_reader, server_writer)))
            return client_reader, client_writer

        with patch.object(inference_proxy, 'request', new=native), patch('builtins.print'):
            with patch.object(transport.asyncio, 'start_server', new=fake_start_server):
                await transport.serve(proxy.handle)
            with patch.object(transport.asyncio, 'open_connection', new=connect):
                try:
                    with self.assertRaises(TimeoutError):
                        await transport.request('http://memory-only/api/generate', {'prompt': 'test'}, timeout=.02)
                    await asyncio.wait_for(entered.wait(), 1)
                    self.assertTrue(client_writer.closed.is_set())
                    state = await self.state(proxy, '?records=1')
                    self.assertEqual(state['started_total'], 1)
                    self.assertEqual(state['finished_total'], 0)
                    self.assertEqual([row['sequence'] for row in state['active']], [1])
                    self.assertEqual(state['records'], [])
                    self.assertFalse(handlers[0].done())
                finally:
                    release.set()
                    await asyncio.wait_for(asyncio.gather(*handlers), 1)
        state = await self.state(proxy, '?records=1')
        self.assertEqual(state['active'], [])
        self.assertEqual(state['finished_total'], 1)
        self.assertEqual(state['uncertain_total'], 0)
        self.assertTrue(state['records'][0]['done'])

    async def test_state_parameters_are_strict_and_cannot_select_foreign_sequences(self):
        proxy = InferenceProxy(IDENTITY)
        bad = ('?after=-1', '?after=1', '?after=01', '?after=', '?after=1.0',
               '?after=0&after=0', '?records=2', '?records=true', '?records=',
               '?records=1&records=1', '?reset=1', '?after=0&records=1&x=1',
               '?after=0&', '?after=0#fragment', '?after')
        for query in bad:
            with self.subTest(query=query):
                self.assertEqual((await proxy.handle('GET', '/study-state' + query, None))[0], 400)
        self.assertEqual((await proxy.handle('POST', '/study-state', None))[0], 400)
        self.assertEqual((await proxy.handle('GET', '/study-state', {}))[0], 400)
        self.assertEqual((await InferenceProxy().handle('GET', '/study-state', None))[0], 503)
        self.assertEqual((await self.state(proxy, '?after=0&records=0'))['started_total'], 0)

    async def test_only_native_generation_enters_ledger(self):
        proxy = InferenceProxy(IDENTITY)
        with patch.object(inference_proxy, 'request', new=AsyncMock(return_value=(200, {'version': 'pinned'}))) as upstream:
            with patch('builtins.print'):
                await proxy.handle('POST', '/transport-control', {'prompt': 'fixture'})
                upstream.assert_not_awaited()
                await proxy.handle('GET', '/api/version', None)
            upstream.assert_awaited_once_with('http://127.0.0.1:11435/api/version', None, timeout=180)
        self.assertEqual((await self.state(proxy))['started_total'], 0)

    async def test_main_verifies_actual_source_before_listening(self):
        snapshot = SimpleNamespace(source_id=IDENTITY['source_id'], metadata=lambda: IDENTITY)
        server = AsyncMock()
        server.serve_forever = AsyncMock()
        with patch.object(inference_proxy, 'capture', return_value=snapshot) as capture:
            with patch.object(inference_proxy, 'verify_worktree') as verify:
                with patch.object(inference_proxy, 'serve', new=AsyncMock(return_value=server)) as serve:
                    await inference_proxy.main()
        capture.assert_called_once_with(Path(inference_proxy.__file__).resolve().parents[1])
        verify.assert_called_once_with(capture.call_args.args[0], IDENTITY['source_id'])
        bound = serve.call_args.args[0].__self__
        self.assertEqual(bound.identity, IDENTITY)


if __name__ == '__main__':
    unittest.main()
