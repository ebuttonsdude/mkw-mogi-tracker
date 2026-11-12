"""Synthetic endpoint tests: no model, real capture, subprocess or external traffic."""

import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import stat
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from mkw_tracker.external_settings import ExternalVisionSettings
from mkw_tracker.external_vision import ExternalVisionReader, destination, test_connection

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


def completion(name='A☆あ'):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': name}}]}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = ExternalVisionSettings(self.root)

    def configure(self, **changes):
        values = dict(url='http://127.0.0.1:8080/v1/chat/completions', model='vision',
                      acknowledged=False, generation=self.settings.public()['generation'])
        values.update(changes)
        return self.settings.configure(values)

    def test_url_policy_without_dns_or_network(self):
        for url in ('http://127.0.0.1:80/v1/chat/completions', 'http://localhost:8080/v1/chat/completions',
                    'http://[::1]:8080/v1/chat/completions', 'http://192.168.1.20:8080/v1/chat/completions',
                    'https://1.1.1.1/v1/chat/completions', 'http://[fd00::2]/v1/chat/completions'):
            with self.subTest(url=url), patch('socket.getaddrinfo', side_effect=AssertionError('DNS')):
                destination(url)
        for url in ('ftp://127.0.0.1/v1/chat/completions', 'http://example.com/v1/chat/completions',
                    'http://169.254.169.254/v1/chat/completions', 'http://0.0.0.0/v1/chat/completions',
                    'http://224.0.0.1/v1/chat/completions', 'http://8.8.8.8/v1/chat/completions',
                    'http://[::ffff:127.0.0.1]/v1/chat/completions', 'http://[fe80::1%eth0]/v1/chat/completions',
                    'http://127.0.0.1:0/v1/chat/completions', 'http://127.0.0.1/v1/chat/completions?',
                    'http://127.0.0.1/v1/chat/completions#', 'http://user:secret@127.0.0.1/v1/chat/completions',
                    ' http://127.0.0.1/v1/chat/completions', 'http://127.0.0.1/\nchat/completions',
                    'http://127.0.0.1/other', 'http://127.0.0.1:bad/v1/chat/completions'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                destination(url)

    def test_base_url_and_hostname_require_consent_before_dns(self):
        self.assertEqual(destination('http://127.0.0.1:8080/v1/')[0].path, '/v1/chat/completions')
        self.assertEqual(destination('https://vision.example/v1')[0].path, '/v1/chat/completions')
        self.assertEqual(destination('https://vision.example')[0].path, '/v1/chat/completions')
        self.configure(url='https://vision.example/v1')
        self.assertEqual(self.settings.public()['url'], 'https://vision.example/v1/chat/completions')
        with patch('socket.getaddrinfo', side_effect=AssertionError('DNS before consent')):
            with self.assertRaises(ValueError):
                ExternalVisionReader(self.settings)

    def test_private_roundtrip_no_key_in_public_or_stale_save_after_forget(self):
        public = self.configure(api_key='secret-key', acknowledged=True)
        self.assertNotIn('secret-key', json.dumps(public))
        self.assertTrue(public['api_key_saved'])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        restored = ExternalVisionSettings(self.root)
        self.assertEqual(restored.prepare()[0]['api_key'], 'secret-key')
        self.assertNotEqual(restored.public()['generation'], public['generation'])
        pending, generation = self.settings.prepare()
        self.settings.forget_key()
        self.assertFalse(self.settings.save(pending, expected_generation=generation))
        with self.assertRaises(ValueError):
            self.configure(generation=generation, api_key='secret-key')
        self.assertEqual(ExternalVisionSettings(self.root).prepare()[0]['api_key'], '')
        self.assertFalse(self.settings.public()['acknowledged'])

    def test_destination_change_drops_key_and_consent_is_not_inherited(self):
        self.configure(api_key='secret', acknowledged=True)
        self.configure(url='http://192.168.1.20/v1/chat/completions')
        self.assertFalse(self.settings.public()['api_key_saved'])
        with patch('socket.socket', side_effect=AssertionError('network')), self.assertRaises(ValueError):
            ExternalVisionReader(self.settings)
        with self.assertRaises(ValueError):
            self.settings.configure({'url': 'http://192.168.1.21/v1/chat/completions', 'model': 'x',
                                     'generation': self.settings.public()['generation']})
        self.configure(url='http://192.168.1.20/v1/chat/completions', acknowledged=True)
        reader = ExternalVisionReader(self.settings)
        self.settings.forget_key()
        self.assertFalse(reader._valid())

    def test_bad_key_and_failed_write_are_sanitized(self):
        for key in ('secret\nheader', 'secret\ud800', 3, 'x' * 4097):
            with self.assertRaises(ValueError) as raised:
                self.configure(api_key=key)
            self.assertNotIn('secret', str(raised.exception))
        self.configure(api_key='old')
        with patch.object(Path, 'replace', side_effect=OSError('secret raw failure')):
            with self.assertRaises(OSError) as raised:
                self.configure(api_key='secret')
        self.assertNotIn('secret', str(raised.exception))
        self.assertEqual(self.settings.prepare()[0]['api_key'], 'old')
        self.assertEqual(list(self.root.glob('.external-vision-*.tmp')), [])

    def test_symlink_and_malformed_private_files_rejected(self):
        self.settings.path.write_text('{"api_key":"secret"}')
        with self.assertRaises(ValueError) as raised:
            ExternalVisionSettings(self.root)
        self.assertNotIn('secret', str(raised.exception))
        self.settings.path.unlink()
        outside = self.root / 'other'
        outside.write_text('unchanged')
        try:
            self.settings.path.symlink_to(outside)
        except OSError:
            self.skipTest('symlink not available')
        with self.assertRaises(ValueError):
            ExternalVisionSettings(self.root)
        with self.assertRaises(OSError):
            self.configure(api_key='secret')
        self.assertEqual(outside.read_text(), 'unchanged')


@unittest.skipUnless(cv2 is not None, 'optional capture dependencies unavailable')
class TransportTests(SettingsTests):
    def setUp(self):
        super().setUp()
        owner = self
        self.requests = []
        self.mode = 'normal'
        self.reply = completion()
        self.received = threading.Event()
        self.release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                owner.requests.append((self.path, dict(self.headers), json.loads(raw), len(raw)))
                owner.received.set()
                if owner.mode == 'wait':
                    owner.release.wait(3)
                body = json.dumps(owner.reply).encode() if not isinstance(owner.reply, bytes) else owner.reply
                try:
                    if owner.mode == 'redirect':
                        self.send_response(307)
                        self.send_header('Location', 'http://192.168.1.99/v1/chat/completions')
                    else:
                        self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    if owner.mode == 'drip':
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(.02)
                    else:
                        self.wfile.write(body)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'
        self.configure(url=self.url, api_key='secret-key')
        self.crop = np.full((70, 398, 3), 255, dtype=np.uint8)

    def stop_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def reader(self):
        reader = ExternalVisionReader(self.settings)
        self.addCleanup(reader.close)
        return reader

    def test_external_diagnostics_record_safe_events_without_payloads(self):
        from mkw_tracker.diagnostics import configure_logging

        stdout = io.StringIO()
        logs = configure_logging(self.root, verbose=True, stdout=stdout)
        try:
            reader = self.reader()
            with patch.object(reader, '_request', return_value={'name': 'PrivatePlayer', 'confidence': 0.0}):
                reader.recognize([self.crop])
            reader.close()
            failed = self.reader()
            with patch.object(failed, '_request', side_effect=RuntimeError('secret-key PrivatePlayer')):
                with self.assertRaises(RuntimeError):
                    failed.recognize([self.crop])
            text = (self.root / 'logs/recognition.log').read_text() + stdout.getvalue()
            for event in ('External vision reader selected', 'row_count=1',
                          'External vision request failed', 'External vision reader closed'):
                self.assertIn(event, text)
            for private in ('secret-key', 'PrivatePlayer', self.url, 'data:image', 'Bearer'):
                self.assertNotIn(private, text)
        finally:
            logs.close()

    def test_crop_only_payload_unicode_no_warmup_and_no_processes(self):
        reader = self.reader()
        with patch('subprocess.Popen', side_effect=AssertionError('process launch')):
            reader.warmup()
            self.assertEqual(self.requests, [])
            rows = reader.recognize([self.crop] * 12)
            reader.close()
        self.assertEqual(rows, [{'name': 'A☆あ', 'confidence': 0.0}] * 12)
        self.assertEqual(len(self.requests), 12)
        for path, headers, payload, size in self.requests:
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(headers['Authorization'], 'Bearer secret-key')
            self.assertLess(size, 351 * 1024)
            self.assertEqual(set(payload), {'model', 'temperature', 'max_tokens', 'stream', 'messages'})
            content = payload['messages'][0]['content']
            png = base64.b64decode(content[0]['image_url']['url'].split(',')[1])
            image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(image, self.crop)
            self.assertEqual(payload['max_tokens'], 96)
            self.assertFalse(payload['stream'])

    def test_downscale_and_preflight_bounds(self):
        self.reader().recognize([np.zeros((256, 1024, 3), np.uint8)])
        encoded = self.requests[0][2]['messages'][0]['content'][0]['image_url']['url'].split(',')[1]
        image = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (128, 512, 3))
        for images in ([], [self.crop] * 13, [self.crop, np.zeros((1080, 1920, 3), np.uint8)],
                       [np.zeros((1, 2, 3), np.float32)]):
            with self.assertRaises(ValueError):
                self.reader().recognize(images)
        self.assertEqual(len(self.requests), 1)

    def test_malformed_and_unfinished_fail_closed_without_retry_or_raw_error(self):
        for reply in (b'not-json-secret-key', b'x' * 16385, [], {}, {'choices': []},
                      completion(''), completion('secret-key'), completion('secret\nreply'), completion('x' * 201),
                      {'choices': [{'finish_reason': 'length', 'message': {'content': 'secret'}}]}):
            self.reply = reply
            reader = self.reader()
            count = len(self.requests)
            with self.assertRaises(RuntimeError) as raised:
                reader.recognize([self.crop] * 12)
            self.assertNotIn('secret', str(raised.exception))
            with self.assertRaises(RuntimeError):
                reader.recognize([self.crop])
            self.assertEqual(len(self.requests), count + 1)

    def test_redirect_not_followed_and_proxy_environment_ignored(self):
        self.mode = 'redirect'
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://192.168.1.99:1', 'HTTPS_PROXY': 'http://192.168.1.99:1'}):
            with self.assertRaises(RuntimeError):
                self.reader().recognize([self.crop])
        self.assertEqual(len(self.requests), 1)

    def test_absolute_slow_body_and_header_deadlines(self):
        for mode in ('wait', 'drip'):
            self.mode = mode
            with patch('mkw_tracker.external_vision.REQUEST_TIMEOUT', .12):
                started = time.monotonic()
                with self.assertRaises(RuntimeError):
                    self.reader().recognize([self.crop])
                self.assertLess(time.monotonic() - started, 1)
        self.release.set()

    def test_close_forget_and_edit_interrupt_inflight_without_followup_requests(self):
        self.mode = 'wait'
        for action in ('close', 'forget', 'edit'):
            self.received.clear()
            reader = self.reader()
            errors = []

            def recognize():
                try:
                    reader.recognize([self.crop] * 12)
                except RuntimeError as error:
                    errors.append(str(error))

            worker = threading.Thread(target=recognize)
            worker.start()
            self.assertTrue(self.received.wait(2))
            if action == 'close':
                reader.close()
            elif action == 'forget':
                self.settings.forget_key()
            else:
                self.configure(url=self.url, model='different')
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertTrue(errors)
        self.assertEqual(len(self.requests), 3)
        self.release.set()

    def test_synthetic_test_checks_unknown_image_word_not_any_text(self):
        self.reply = completion('V12ABCD')
        with patch('mkw_tracker.external_vision.secrets.token_hex', return_value='12abcd'):
            result = test_connection(self.settings)
        self.assertTrue(result['ok'])
        content = self.requests[-1][2]['messages'][0]['content']
        self.assertNotIn('V12ABCD', content[1]['text'])
        self.assertNotEqual(content[0]['image_url']['url'], '')
        self.reply = completion('generic acknowledgement')
        self.assertFalse(test_connection(self.settings)['ok'])

    def test_hostname_tls_pins_checked_peer_and_retains_sni(self):
        self.configure(url='https://vision.example/v1', acknowledged=True)
        peer = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        raw_socket = MagicMock()
        tls_socket = MagicMock()
        response = MagicMock(status=200)
        response.read.return_value = json.dumps(completion()).encode()
        with patch('socket.getaddrinfo', return_value=[peer]) as dns, \
                patch('socket.socket', return_value=raw_socket), \
                patch('ssl.create_default_context', return_value=context), \
                patch.object(context, 'wrap_socket', return_value=tls_socket) as wrap, \
                patch('http.client.HTTPConnection') as http:
            http.return_value.getresponse.return_value = response
            result = self.reader().recognize([self.crop])
            self.assertEqual(result[0]['name'], 'A☆あ')
            dns.assert_called_once_with('vision.example', 443, type=socket.SOCK_STREAM)
            raw_socket.connect.assert_called_once_with(peer[4])
            wrap.assert_called_once_with(raw_socket, server_hostname='vision.example', do_handshake_on_connect=False)
            tls_socket.do_handshake.assert_called_once()
            http.assert_called_once_with('vision.example', 443)
            http.return_value.request.assert_called_once()
            self.assertEqual(http.return_value.request.call_args.args[:2], ('POST', '/v1/chat/completions'))

    def test_localhost_tls_uses_hostname_identity_with_numeric_loopback_pin(self):
        self.configure(url='https://localhost:8443/v1')
        raw_socket = MagicMock()
        context = MagicMock()
        response = MagicMock(status=200)
        response.read.return_value = json.dumps(completion()).encode()
        with patch('socket.getaddrinfo', side_effect=AssertionError('localhost DNS')), \
                patch('socket.socket', return_value=raw_socket), \
                patch('ssl.create_default_context', return_value=context), \
                patch('http.client.HTTPConnection') as http:
            http.return_value.getresponse.return_value = response
            self.reader().recognize([self.crop])
            raw_socket.connect.assert_called_once_with(('127.0.0.1', 8443))
            context.wrap_socket.assert_called_once_with(raw_socket, server_hostname='localhost',
                                                       do_handshake_on_connect=False)
            http.assert_called_once_with('localhost', 8443)

    def test_hostname_dns_rebinding_unsafe_or_mixed_answers_send_nothing(self):
        self.configure(url='https://vision.example/v1', acknowledged=True)
        safe = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        for ip in ('127.0.0.1', '169.254.169.254', '0.0.0.0', '224.0.0.1', '100.64.0.1'):
            unsafe = (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))
            with patch('socket.getaddrinfo', return_value=[safe, unsafe]), \
                    patch('socket.socket', side_effect=AssertionError('unsafe connection')):
                with self.assertRaisesRegex(RuntimeError, 'unsafe peer'):
                    self.reader().recognize([self.crop])
        self.assertEqual(self.requests, [])

    def test_dns_deadline_cancellation_and_single_outstanding_resolver(self):
        self.configure(url='https://vision.example/v1', acknowledged=True)
        from mkw_tracker.external_vision import _DNS_GATE
        entered, release = threading.Event(), threading.Event()

        def slow_dns(*args, **kwargs):
            entered.set()
            release.wait(3)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]

        with patch('socket.getaddrinfo', side_effect=slow_dns) as dns, \
                patch('socket.socket', side_effect=AssertionError('unexpected socket')), \
                patch('mkw_tracker.external_vision.REQUEST_TIMEOUT', .1):
            try:
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'deadline'):
                    self.reader().recognize([self.crop])
                self.assertLess(time.monotonic() - started, .5)
                self.assertTrue(entered.is_set())
                with self.assertRaisesRegex(RuntimeError, 'DNS is still busy'):
                    self.reader().recognize([self.crop])
                dns.assert_called_once()
            finally:
                release.set()
                self.assertTrue(_DNS_GATE.acquire(timeout=2))
                _DNS_GATE.release()

    def test_cancel_during_dns_never_connects_after_resolver_finishes(self):
        from mkw_tracker.external_vision import _DNS_GATE
        self.configure(url='https://vision.example/v1', acknowledged=True)
        reader = self.reader()
        entered, release = threading.Event(), threading.Event()
        errors = []

        def resolve(*args, **kwargs):
            entered.set()
            release.wait(3)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]

        def recognize():
            try:
                reader.recognize([self.crop])
            except RuntimeError as error:
                errors.append(str(error))

        with patch('socket.getaddrinfo', side_effect=resolve), \
                patch('socket.socket', side_effect=AssertionError('cancelled DNS connected')):
            worker = threading.Thread(target=recognize)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                reader.close()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertTrue(errors and 'cancelled' in errors[0])
            finally:
                release.set()
                self.assertTrue(_DNS_GATE.acquire(timeout=2))
                _DNS_GATE.release()
                worker.join(2)

    def test_attempt_deadline_and_https_certificate_failure_never_send_body(self):
        self.configure(url='https://vision.example/v1', acknowledged=True)
        peer = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError('secret-key certificate detail')
        with patch('socket.getaddrinfo', return_value=[peer]), patch('socket.socket'), \
                patch('ssl.create_default_context', return_value=context), patch('http.client.HTTPConnection') as http:
            with self.assertRaises(RuntimeError) as raised:
                self.reader().recognize([self.crop])
            self.assertNotIn('secret-key', str(raised.exception))
            http.return_value.request.assert_not_called()
        self.configure(url=self.url)
        self.mode = 'wait'
        with patch('mkw_tracker.external_vision.ATTEMPT_TIMEOUT', .1):
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                self.reader().recognize([self.crop] * 12)
            self.assertLess(time.monotonic() - started, .5)
        self.release.set()

    def test_live_import_factory_and_warmup_use_external_reader(self):
        from mkw_tracker.server import Application
        from mkw_tracker.reader_setup import installed, remove
        with patch('subprocess.Popen', side_effect=AssertionError('process launch')):
            app = Application(self.root / 'tracker.sqlite3', self.root)
            try:
                reader = app.imports.reader_factory('external-vision')
                self.addCleanup(reader.close)
                reader.warmup()
                self.assertEqual(self.requests, [])
                self.assertEqual(reader.name_reader.engine, 'external-vision')
                # CameraManager uses this exact import factory unless explicitly injected.
                self.assertIsNone(app.cameras.reader_factory)
                self.assertFalse(installed(self.root, 'external-vision'))
                with self.assertRaises(ValueError):
                    remove(self.root, 'external-vision')
            finally:
                app.close()


@unittest.skipUnless(cv2 is not None, 'optional capture dependencies unavailable')
class APITests(unittest.TestCase):
    def test_private_config_test_consent_and_forget_api(self):
        from mkw_tracker.server import Application, make_server
        with tempfile.TemporaryDirectory() as temporary:
            app = Application(Path(temporary) / 'tracker.sqlite3', temporary)
            server = make_server(app, 0)
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                def request(method, path, body=None):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    try:
                        conn.request(method, path, json.dumps(body) if body is not None else None,
                                     {'Content-Type': 'application/json'})
                        response = conn.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        conn.close()

                config = app.external_vision.public()
                body = dict(url='http://192.168.1.20/v1/chat/completions', model='vision',
                            api_key='never-public-key', acknowledged=False, generation=config['generation'])
                status, config = request('PATCH', '/api/external-vision', body)
                self.assertEqual(status, 200)
                self.assertNotIn('never-public-key', json.dumps(config))
                with patch('socket.create_connection', side_effect=AssertionError('unexpected transmission')):
                    # Call direct: patching outbound connections would also block the test's API client.
                    with self.assertRaises(ValueError):
                        test_connection(app.external_vision)
                status, value = request('GET', '/api/state')
                self.assertNotIn('never-public-key', json.dumps(value))
                self.assertEqual(status, 200)
                status, value = request('POST', '/api/external-vision/test', {'test_id': 'a' * 32, 'generation': config['generation']})
                self.assertEqual(status, 400)
                self.assertNotIn('never-public-key', json.dumps(value))
                self.assertEqual(request('DELETE', '/api/external-vision/key', {})[0], 200)
                self.assertEqual(request('PATCH', '/api/external-vision', body)[0], 400)
                self.assertEqual(request('POST', '/api/external-vision/test/cancel', {'test_id': 'b' * 32})[0], 200)
                self.assertIn('b' * 32, app.external_cancelled_tests)
                config = app.external_vision.public()
                body.update(url='http://127.0.0.1:9/v1', generation=config['generation'])
                status, config = request('PATCH', '/api/external-vision', body)
                # Cancellation arriving before the test request must win.
                status, value = request('POST', '/api/external-vision/test',
                                        {'test_id': 'b' * 32, 'generation': config['generation']})
                self.assertEqual(status, 400)
                self.assertIn('cancelled', value['error'])
                self.assertNotIn('api_key', app.external_vision.public())
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
                app.close()
