"""Operator-selected private listeners retain loopback defaults without sockets."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from v2 import inference_monitor, inference_proxy


class ListenerBindingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def server():
        server = MagicMock()
        server.__aenter__.return_value = server
        server.serve_forever = AsyncMock(side_effect=asyncio.CancelledError)
        return server

    async def test_proxy_keeps_loopback_default_and_supports_operator_private_binding(self):
        identity = {'source_id': 'sha256:' + 'a' * 64, 'git_base': 'b' * 40}
        snapshot = SimpleNamespace(source_id=identity['source_id'], metadata=lambda: dict(identity))
        for environment, host, port in [({}, '127.0.0.1', 11437),
                                        ({'STUDY_PROXY_BIND': '172.16.0.244', 'STUDY_PROXY_PORT': '11437'}, '172.16.0.244', 11437)]:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(inference_proxy, 'capture', return_value=snapshot) as capture, \
                    patch.object(inference_proxy, 'verify_worktree') as verify, \
                    patch.object(inference_proxy, 'serve', new=AsyncMock(return_value=self.server())) as serve:
                with self.assertRaises(asyncio.CancelledError):
                    await inference_proxy.main()
                root = Path(inference_proxy.__file__).resolve().parents[1]
                capture.assert_called_once_with(root)
                verify.assert_called_once_with(root, identity['source_id'])
                serve.assert_awaited_once()
                handler, bound_host, bound_port = serve.call_args.args
                self.assertEqual((bound_host, bound_port), (host, port))
                self.assertEqual(serve.call_args.kwargs, {})
                self.assertIsInstance(handler.__self__, inference_proxy.InferenceProxy)
                self.assertIs(handler.__func__, inference_proxy.InferenceProxy.handle)
                status, state = await handler('GET', '/study-state', None)
                self.assertEqual(status, 200)
                self.assertEqual({key: state[key] for key in identity}, identity)
                self.assertTrue(state['proxy_sha256'])
                self.assertTrue(state['clock_domain'])

    async def test_proxy_rejects_unverified_source_before_binding(self):
        snapshot = SimpleNamespace(source_id='sha256:' + 'a' * 64)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(inference_proxy, 'capture', return_value=snapshot), \
                patch.object(inference_proxy, 'verify_worktree', side_effect=ValueError('Source bytes changed')), \
                patch.object(inference_proxy, 'serve', new=AsyncMock()) as serve:
            with self.assertRaisesRegex(ValueError, 'Source bytes changed'):
                await inference_proxy.main()
            serve.assert_not_awaited()

    async def test_invalid_proxy_port_fails_before_binding(self):
        for port in ('0', '65536', 'invalid'):
            with self.subTest(port=port), patch.dict(os.environ, {'STUDY_PROXY_PORT': port}, clear=True), \
                    patch.object(inference_proxy, 'serve', new=AsyncMock()) as serve:
                with self.assertRaises(ValueError):
                    await inference_proxy.main()
                serve.assert_not_awaited()

    async def test_monitor_keeps_loopback_default_and_supports_operator_private_binding(self):
        for environment, host in [({}, '127.0.0.1'), ({'STUDY_MONITOR_BIND': '172.16.0.244'}, '172.16.0.244')]:
            monitor = MagicMock()
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(inference_monitor, 'Monitor', return_value=monitor), \
                    patch.object(inference_monitor, 'serve', new=AsyncMock(return_value=self.server())) as serve:
                with self.assertRaises(asyncio.CancelledError):
                    await inference_monitor.main()
                serve.assert_awaited_once_with(monitor.handle, host, 11436)


if __name__ == '__main__':
    unittest.main()
