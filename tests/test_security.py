import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from token_tv import live
from token_tv.http_server import LocalHTTPServer
from token_tv.sources import json_get
from token_tv.state import UsageStore


@contextmanager
def serve(handler, server_class=LocalHTTPServer):
    server = server_class(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class DashboardSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.json'
        self.config = {'accounts': [{'key': 'test', 'alias': 'TEST', 'provider': 'codex',
                                    'email': 'test@example.invalid'}], 'display_style': 'pixel'}
        self.path.write_text(json.dumps(self.config))
        self.preferences = live.DisplayPreferences(self.path, self.config)
        self.handler = live.handler(UsageStore(self.config['accounts']), self.preferences)

    def request(self, server, method='GET', path='/snapshot', headers=(), body=None):
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            client.putrequest(method, path, skip_host=True)
            for key, value in headers:
                client.putheader(key, value)
            if body is not None:
                client.putheader('Content-Type', 'application/json')
                client.putheader('Content-Length', str(len(body)))
            client.endheaders(body)
            response = client.getresponse()
            return response.status, response.read(), response.headers
        finally:
            client.close()

    def test_rebinding_and_cross_origin_requests_cannot_read_or_write(self):
        before = self.path.read_bytes()
        with serve(self.handler) as server:
            host = f'127.0.0.1:{server.server_port}'
            evil = f'attacker.example:{server.server_port}'
            cases = [
                [('Host', evil), ('Origin', 'http://' + evil)],
                [('Host', evil)],
                [('Host', host), ('Origin', 'http://attacker.example')],
                [('Host', host), ('Origin', 'https://' + host)],
                [('Host', host), ('Origin', 'null')],
                [('Host', host), ('Origin', 'http://' + host), ('Origin', 'http://' + host)],
                [('Host', host), ('Sec-Fetch-Site', 'cross-site')],
                [('Host', host), ('Host', evil)],
                [('Host', 'user@' + host)],
                [('Host', host + '.attacker.example')],
                [('Host', '127.0.0.1:1')],
                [],
            ]
            for headers in cases:
                for method, path, body in [('GET', '/snapshot', None),
                                            ('POST', '/display/style', b'{"style":"digital"}')]:
                    with self.subTest(headers=headers, method=method):
                        status, response, _ = self.request(server, method, path, headers, body)
                        self.assertEqual(status, 403)
                        self.assertNotIn(b'TEST', response)
            for path in ('http://' + evil + '/snapshot', '//attacker.example/snapshot'):
                self.assertEqual(self.request(server, path=path, headers=[('Host', host)])[0], 403)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.preferences.changed.is_set())

    def test_local_browser_and_non_browser_clients_still_work(self):
        with serve(self.handler) as server:
            for name in ('localhost', '127.0.0.1'):
                host = f'{name}:{server.server_port}'
                status, body, headers = self.request(server, headers=[('Host', host)])
                self.assertEqual(status, 200)
                self.assertIn('test', json.loads(body)['accounts'])
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertEqual(headers['Content-Security-Policy'], "frame-ancestors 'none'")
                self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
                for origin in ([], [('Origin', 'http://' + host)]):
                    status, _, _ = self.request(server, 'POST', '/display/style',
                                                [('Host', host)] + origin, b'{"style":"digital"}')
                    self.assertEqual(status, 200)
            self.assertEqual(json.loads(self.path.read_text())['display_style'], 'digital')

    def test_non_loopback_bind_is_rejected_before_accounts_or_clock_are_touched(self):
        for host in ('0.0.0.0', '192.168.0.20', '::', 'example.com', '::ffff:127.0.0.1'):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    LocalHTTPServer((host, 0), self.handler)
                with patch('sys.argv', ['token-tv', '--config', '/unused', '--host', host]), \
                        patch('token_tv.live.load_config') as config, \
                        patch('sys.stderr'), self.assertRaises(SystemExit) as error:
                    live.main()
                self.assertEqual(error.exception.code, 2)
                config.assert_not_called()

    def test_localhost_bind_works(self):
        with LocalHTTPServer(('localhost', 0), self.handler) as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')


class RequestLimitsTests(unittest.TestCase):
    def test_incomplete_body_times_out_without_changing_style(self):
        class FastTimeout(LocalHTTPServer):
            read_timeout = 0.1

        preferences = Mock()
        with serve(live.handler(UsageStore([]), preferences), FastTimeout) as server:
            with socket.create_connection(server.server_address, timeout=3) as client:
                client.sendall((f'POST /display/style HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                                'Content-Type: application/json\r\nContent-Length: 19\r\n\r\n{').encode())
                self.assertIn(b'408', client.recv(4096))
            preferences.set_style.assert_not_called()

    def test_connection_cap_and_total_deadline_release_capacity(self):
        entered = threading.Event()
        class SlowReader(BaseHTTPRequestHandler):
            def handle(self):
                entered.set()
                while self.rfile.read(1):
                    pass
        class Limited(LocalHTTPServer):
            max_connections = 1
            read_timeout = 5
            request_deadline = 0.5

        with serve(SlowReader, Limited) as server:
            with socket.create_connection(server.server_address, timeout=3) as first:
                self.assertTrue(entered.wait(2))
                with socket.create_connection(server.server_address, timeout=3) as excess:
                    self.assertEqual(excess.recv(1), b'')
                # The total deadline must close this well before the five-second read timeout.
                self.assertEqual(first.recv(1), b'')
            deadline = time.monotonic() + 2
            while not server._slots.acquire(blocking=False):
                self.assertLess(time.monotonic(), deadline, 'Connection slot was not released')
                time.sleep(0.01)
            server._slots.release()
            entered.clear()
            with socket.create_connection(server.server_address, timeout=3):
                self.assertTrue(entered.wait(2), 'New connections must work after expiration')


class RedirectSecurityTests(unittest.TestCase):
    def test_authenticated_redirects_never_reach_the_destination(self):
        hits = []
        class Destination(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                hits.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        with serve(Destination, ThreadingHTTPServer) as sink:
            class Redirect(Destination):
                def do_GET(self):
                    if self.path == '/direct':
                        return super().do_GET()
                    self.send_response(int(self.path.strip('/')))
                    self.send_header('Location', f'http://localhost:{sink.server_port}/target')
                    self.end_headers()
            with serve(Redirect, ThreadingHTTPServer) as source:
                root = f'http://127.0.0.1:{source.server_port}'
                for code in (301, 302, 303, 307, 308):
                    with self.subTest(code=code), self.assertRaises(HTTPError) as error:
                        json_get(root + f'/{code}', {'authorization': 'Bearer FAKE-TEST-TOKEN'})
                    self.assertEqual(error.exception.code, code)
                self.assertEqual(hits, [])
                self.assertEqual(json_get(root + '/direct', {'Authorization': 'Bearer FAKE-TEST-TOKEN'}), {'ok': True})
                self.assertEqual(hits, ['Bearer FAKE-TEST-TOKEN'])
                # Credential-free CodexBar URLs can still follow ordinary redirects.
                self.assertEqual(json_get(root + '/302'), {'ok': True})
                self.assertEqual(hits, ['Bearer FAKE-TEST-TOKEN', None])


if __name__ == '__main__':
    unittest.main()
