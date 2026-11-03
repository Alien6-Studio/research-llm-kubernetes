"""Byte accounting for the metadata-only transport control."""
import json
import time
import unittest
from v2.network_probe import observe
from v2.transport import serve


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_records_payload_bytes_and_metadata_without_generation(self):
        seen = []
        async def handler(method, path, payload):
            seen.append((method, path, payload))
            return 200, {'model_info': {'architecture': 'unit-fixture'}}
        server = await serve(handler, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            row = await observe(f'http://127.0.0.1:{port}/api/show', {'id': 'metadata', 'offset': 0, 'prompt_text': 'x' * 4096}, time.monotonic())
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(seen[0][1], '/api/show')
        self.assertEqual(row['status'], 200)
        self.assertTrue(row['model_metadata_returned'])
        self.assertGreater(row['request_body_bytes'], 4096)
        self.assertGreater(row['response_body_bytes'], 0)
        self.assertIsNotNone(row['emitted'])
