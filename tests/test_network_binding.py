"""Network binding remains opt-in and preserves HTTP request boundaries."""

from contextlib import contextmanager
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from mkw_tracker.__main__ import main
from mkw_tracker.server import Application, make_server


class NetworkBindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = Application(':memory:', self.directory.name)
        self.addCleanup(self.app.close)

    @contextmanager
    def running_server(self, host=None):
        server = make_server(self.app, 0) if host is None else make_server(self.app, 0, host=host)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            self.assertFalse(thread.is_alive())

    def request(self, server, *, address=None, host=None, headers=(), method='GET', path='/api/state'):
        address = address or ('::1' if server.address_family == socket.AF_INET6 else '127.0.0.1')
        port = server.server_address[1]
        authority = f'[{address}]:{port}' if ':' in address else f'{address}:{port}'
        connection = http.client.HTTPConnection(address, port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            connection.putheader('Host', authority if host is None else host)
            for key, value in headers:
                connection.putheader(key, value)
            body = b'{}' if method == 'POST' else None
            if body is not None:
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_default_binding_is_ipv4_loopback(self):
        with self.running_server() as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            self.assertEqual(self.request(server)[0], 200)

    def test_explicit_loopback_and_localhost_alias_are_readable(self):
        with self.running_server('127.0.0.1') as server:
            self.assertEqual(self.request(server)[0], 200)
            self.assertEqual(self.request(server, host=f'localhost:{server.server_address[1]}')[0], 200)

    def test_hostname_binding_accepts_configured_hostname(self):
        with self.running_server('localhost') as server:
            self.assertEqual(self.request(server, host=f'localhost:{server.server_address[1]}')[0], 200)

    def test_wildcard_accepts_actual_destination_address(self):
        with self.running_server('0.0.0.0') as server:
            self.assertEqual(server.server_address[0], '0.0.0.0')
            self.assertEqual(self.request(server)[0], 200)

    def require_loopback_alias(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(('127.0.0.2', 0))
        except OSError as error:
            self.skipTest(f'Loopback alias unavailable: {error}')

    def test_explicit_loopback_alias_is_reachable(self):
        self.require_loopback_alias()
        with self.running_server('127.0.0.2') as server:
            self.assertEqual(server.server_address[0], '127.0.0.2')
            self.assertEqual(self.request(server, address='127.0.0.2')[0], 200)

    def test_wildcard_checks_connection_destination_not_fixed_loopback(self):
        self.require_loopback_alias()
        with self.running_server('0.0.0.0') as server:
            self.assertEqual(self.request(server, address='127.0.0.2')[0], 200)
            self.assertEqual(self.request(server, address='127.0.0.2',
                                          host=f'127.0.0.1:{server.server_address[1]}')[0], 403)

    def test_wildcard_allows_same_origin_write(self):
        with self.running_server('0.0.0.0') as server:
            origin = f'http://127.0.0.1:{server.server_address[1]}'
            status, result = self.request(server, method='POST', path='/api/overlay/hide',
                                          headers=[('Origin', origin)])
            self.assertEqual((status, result), (200, {'visible': False}))

    def test_non_browser_write_without_origin_remains_allowed(self):
        with self.running_server('0.0.0.0') as server:
            self.assertEqual(self.request(server, method='POST', path='/api/overlay/hide'),
                             (200, {'visible': False}))

    def test_wildcard_rejects_arbitrary_host_and_wrong_port(self):
        with self.running_server('0.0.0.0') as server:
            port = server.server_address[1]
            for host in (f'attacker.example:{port}', '127.0.0.1', f'127.0.0.1:{port + 1}',
                         f'192.0.2.123:{port}'):
                with self.subTest(host=host):
                    self.assertEqual(self.request(server, host=host)[0], 403)

    def test_invalid_host_cannot_mutate_state(self):
        with self.running_server('0.0.0.0') as server:
            self.assertEqual(self.request(server, host='attacker.example', method='POST',
                                          path='/api/overlay/hide')[0], 403)
            self.assertTrue(self.app.overlay_visible)

    def test_cross_origin_write_rejected_even_for_loopback_alias(self):
        with self.running_server('0.0.0.0') as server:
            port = server.server_address[1]
            for origin in (f'http://localhost:{port}', f'https://127.0.0.1:{port}',
                           'http://attacker.example', 'null'):
                with self.subTest(origin=origin):
                    self.assertEqual(self.request(server, method='POST', path='/api/overlay/hide',
                                                  headers=[('Origin', origin)])[0], 403)
                    self.assertTrue(self.app.overlay_visible)

    def test_localhost_alias_write_requires_matching_origin(self):
        with self.running_server('127.0.0.1') as server:
            authority = f'localhost:{server.server_address[1]}'
            self.assertEqual(self.request(server, host=authority, method='POST',
                                          path='/api/overlay/hide',
                                          headers=[('Origin', f'http://{authority}')]),
                             (200, {'visible': False}))

    def test_cross_site_without_origin_cannot_mutate_state(self):
        with self.running_server('0.0.0.0') as server:
            self.assertEqual(self.request(server, method='POST', path='/api/overlay/hide',
                                          headers=[('Sec-Fetch-Site', 'cross-site')])[0], 403)
            self.assertTrue(self.app.overlay_visible)

    def test_duplicate_host_is_rejected_even_when_identical(self):
        with self.running_server('0.0.0.0') as server:
            authority = f'127.0.0.1:{server.server_address[1]}'
            self.assertEqual(self.request(server, headers=[('Host', authority)])[0], 403)

    def test_duplicate_origin_cannot_mutate_state(self):
        with self.running_server('0.0.0.0') as server:
            origin = f'http://127.0.0.1:{server.server_address[1]}'
            self.assertEqual(self.request(server, method='POST', path='/api/overlay/hide',
                                          headers=[('Origin', origin), ('Origin', origin)])[0], 403)
            self.assertTrue(self.app.overlay_visible)

    def test_saved_obs_password_is_not_exposed_on_wildcard_listener(self):
        secret = 'network-test-private-password'
        self.app.obs_connection.save(self.app.obs_connection.resolve(password=secret))
        with self.running_server('0.0.0.0') as server:
            status, state = self.request(server)
            self.assertEqual(status, 200)
            self.assertTrue(state['obs_connection']['password_saved'])
            self.assertNotIn(secret, json.dumps(state))
            for path in ('/api/obs/credentials', '/obs-connection.json'):
                with self.subTest(path=path):
                    status, body = self.request(server, path=path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(secret, json.dumps(body))

    def test_ipv6_loopback_accepts_bracketed_authority_and_origin(self):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
                probe.bind(('::1', 0))
        except OSError as error:
            self.skipTest(f'IPv6 loopback unavailable: {error}')
        with self.running_server('::1') as server:
            self.assertEqual(self.request(server)[0], 200)
            origin = f'http://[::1]:{server.server_address[1]}'
            self.assertEqual(self.request(server, method='POST', path='/api/overlay/hide',
                                          headers=[('Origin', origin)]),
                             (200, {'visible': False}))

    def test_ipv6_wildcard_accepts_actual_destination_address(self):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
                probe.bind(('::', 0))
        except OSError as error:
            self.skipTest(f'IPv6 wildcard unavailable: {error}')
        with self.running_server('::') as server:
            self.assertEqual(self.request(server)[0], 200)
            self.assertEqual(self.request(server, host=f'attacker.example:{server.server_address[1]}')[0], 403)

    def test_malformed_bind_address_fails_without_a_server(self):
        for host in ('', 'http://127.0.0.1', '127.0.0.1/path', 'bad host'):
            with self.subTest(host=host):
                with self.assertRaises((ValueError, OSError)):
                    server = make_server(self.app, 0, host=host)
                    server.server_close()


class NetworkBindingCLITests(unittest.TestCase):
    def run_cli(self, arguments, bind_error=None, *, bound_host='127.0.0.1'):
        with tempfile.TemporaryDirectory() as directory, \
                patch('sys.argv', ['mkw_tracker', '--data-dir', directory, *arguments]), \
                patch('mkw_tracker.__main__.Application') as application, \
                patch('mkw_tracker.__main__.make_server') as factory, \
                patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                patch('sys.stderr', new_callable=io.StringIO) as stderr:
            factory.return_value.server_address = (bound_host, 18765)
            if bind_error:
                factory.side_effect = bind_error
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn('cannot listen', stderr.getvalue().lower())
            else:
                main()
                factory.return_value.server_close.assert_called_once()
            application.assert_called_once_with(Path(directory) / 'tracker.sqlite3',
                                                Path(directory), mode='manual')
            application.return_value.close.assert_called_once()
            return factory.call_args, application.return_value, stdout.getvalue(), stderr.getvalue()

    def test_cli_default_stays_on_loopback(self):
        call, app, _, _ = self.run_cli([])
        self.assertEqual(call.args[:2], (app, 8765))
        self.assertEqual(call.kwargs.get('host', '127.0.0.1'), '127.0.0.1')

    def test_cli_forwards_explicit_bind_host(self):
        call, app, _, _ = self.run_cli(['--host', '0.0.0.0', '--port', '0'], bound_host='0.0.0.0')
        self.assertEqual(call.args[:2], (app, 0))
        self.assertEqual(call.kwargs['host'], '0.0.0.0')

    def test_cli_warns_network_clients_can_reuse_saved_obs_credentials(self):
        for host in ('0.0.0.0', '192.0.2.10', '::'):
            with self.subTest(host=host):
                _, _, _, stderr = self.run_cli(['--host', host], bound_host=host)
                self.assertIn('no login or TLS', stderr)
                self.assertIn('start capture using saved OBS credentials', stderr)
                self.assertIn('never expose it to the public Internet', stderr)

    def test_cli_loopback_has_no_network_warning(self):
        for host in ('127.0.0.1', '127.0.0.2', '::1'):
            with self.subTest(host=host):
                _, _, _, stderr = self.run_cli(['--host', host], bound_host=host)
                self.assertEqual(stderr, '')

    def test_cli_wildcard_prints_usable_local_url_and_actual_listener(self):
        for host, browser_host in (('0.0.0.0', '127.0.0.1'), ('::', '[::1]')):
            with self.subTest(host=host):
                _, _, stdout, _ = self.run_cli(['--host', host, '--port', '0'], bound_host=host)
                self.assertIn(f'Race tracker: http://{browser_host}:18765', stdout)
                self.assertIn(f'Listening on {host} port 18765', stdout)
                self.assertIn("remote clients use this machine's network address", stdout)

    def test_cli_ipv6_url_has_bracketed_address(self):
        _, _, stdout, _ = self.run_cli(['--host', '::1'], bound_host='::1')
        self.assertIn('Race tracker: http://[::1]:18765', stdout)

    def test_cli_bind_failure_does_not_start_requested_obs_capture(self):
        with patch('mkw_tracker.capture.CaptureCoordinator') as capture, \
                patch('mkw_tracker.obs.ObsClient') as obs, \
                patch('mkw_tracker.recognition.ResultReader') as reader, \
                patch('mkw_tracker.neural_ocr.make_name_reader') as name_reader:
            self.run_cli(['--host', '192.0.2.123', '--obs-source', 'Test source'],
                         OSError('Cannot bind requested address'))
            capture.assert_not_called()
            obs.assert_not_called()
            reader.assert_not_called()
            name_reader.assert_not_called()

    def test_cli_bind_failure_is_clear_and_closes_application(self):
        self.run_cli(['--host', '192.0.2.123'], OSError('Cannot bind requested address'))


if __name__ == '__main__':
    unittest.main()
