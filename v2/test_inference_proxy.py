"""Keep the transport fixture distinct from actual model generation."""
import unittest
from unittest.mock import AsyncMock, patch
from v2.inference_proxy import handle


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_never_calls_inference(self):
        with patch('v2.inference_proxy.request', new_callable=AsyncMock) as upstream:
            status, body = await handle('POST', '/transport-control', {'prompt': 'fixture'})
            upstream.assert_not_awaited()
        self.assertEqual(status, 200)
        self.assertEqual(len(body['padding']), 8192)

    async def test_model_response_and_request_pass_through(self):
        payload = {'prompt': 'classification', 'stream': False}
        raw = {'response': '{"label":"ok"}', 'eval_count': 5}
        with patch('v2.inference_proxy.request', new=AsyncMock(return_value=(200, raw))) as upstream:
            status, returned = await handle('POST', '/api/generate', payload)
            upstream.assert_awaited_once_with('http://127.0.0.1:11435/api/generate', payload, timeout=180)
        self.assertIs(returned, raw)
        self.assertEqual(status, 200)
