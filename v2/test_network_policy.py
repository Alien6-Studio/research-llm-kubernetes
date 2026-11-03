"""HTTP configuration cannot grant a caller a new outbound destination."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from v2.network_policy import allowed_url, normalized_config, NetworkPolicyError
from v2.service import Application
import service


class PolicyTests(unittest.TestCase):
    def test_rejects_arbitrary_inference_and_worker_hosts_recursively(self):
        for url in ['http://169.254.169.254:80', 'file:///etc/passwd',
                    'http://provider:8080@evil.test:80', 'http://provider:8080?x=1']:
            with self.subTest(url=url):
                for payload in [{'provider': {'ollama_url': url}}, {'addresses': [url]}, {'worker_url': url}]:
                    with self.assertRaises(NetworkPolicyError): normalized_config(payload)
                with self.assertRaises(NetworkPolicyError): allowed_url(url)

    def test_targets_are_selected_from_operator_configuration(self):
        with patch.dict(os.environ, {'STUDY_WORKER_ORIGINS': '["http://127.0.0.1:19001"]',
                                     'STUDY_INFERENCE_ORIGIN': 'http://127.0.0.1:19002'}):
            config = {'provider': {'ollama_url': 'http://127.0.0.1:19002'},
                      'worker_addresses': ['http://127.0.0.1:19001']}
            self.assertEqual(normalized_config(config), config)
            self.assertEqual(allowed_url('http://127.0.0.1:19001/stats'), 'http://127.0.0.1:19001/stats')
            with self.assertRaises(NetworkPolicyError): allowed_url('http://127.0.0.1:19001/stats?url=x')

    def test_worker_discovery_uses_server_owned_headless_service(self):
        with patch.dict(os.environ, {}, clear=True), patch('v2.network_policy.socket.getaddrinfo',
                return_value=[(None,None,None,None,('10.1.2.3',8080))]) as lookup:
            self.assertEqual(normalized_config({'addresses': ['http://10.1.2.3:8080']}),
                             {'addresses': ['http://10.1.2.3:8080']})
            self.assertEqual(lookup.call_args.args[0], 'workers-observe')

    def test_legacy_http_configuration_rejects_unapproved_hosts(self):
        server=service.Server(('127.0.0.1',0),service.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        before=dict(service.CONFIG)
        try:
            with patch.dict(os.environ, {'PROVIDER_URL':origin}), patch.object(service,'ROLE','provider'):
                status,body=service.http(origin+'/configure',{'ollama_url':'http://169.254.169.254:80'})
                self.assertEqual(status,400)
                self.assertEqual(body['reason'],'unapproved_network_target')
                self.assertEqual(service.CONFIG,before)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_legacy_http_never_follows_redirects(self):
        calls = []
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *_): pass
        server = HTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            origin='http://127.0.0.1:'+str(server.server_port)
            with patch.dict(os.environ, {'PROVIDER_URL': origin}):
                self.assertEqual(service.http(origin+'/stats')[0],302)
            self.assertEqual(calls, ['/stats'])
        finally:
            server.shutdown();server.server_close();thread.join()


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_rejects_targets_before_state_changes_or_network_calls(self):
        app = Application('controller')
        for path,payload in [('/configure',{'ollama_url':'http://evil.test:80'}),
                             ('/run',{'worker_url':'http://evil.test:80'}),
                             ('/prepare',{'provider':{'ollama_url':'http://evil.test:80'}})]:
            with patch('v2.service.request') as network:
                status, body = await app.handle('POST',path,payload)
                self.assertEqual(status,400)
                self.assertEqual(body['reason'],'unapproved_network_target')
                network.assert_not_called()
