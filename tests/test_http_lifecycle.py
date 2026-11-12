"""Isolated HTTP regressions; never connect to OBS or open capture hardware."""

from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from mkw_tracker.server import Application, make_server


class HTTPLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.directory.name) / 'scores.sqlite', self.directory.name)
        self.server = make_server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.worker.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.app.close()
        self.directory.cleanup()

    def connect(self):
        client = socket.socket()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client.settimeout(2)
        client.connect(self.server.server_address)
        self.clients.append(client)
        return client

    def test_windows_connection_abort_during_headers_is_quiet(self):
        request = object.__new__(self.server.RequestHandlerClass)
        request.handle_one_request = Mock(side_effect=ConnectionAbortedError('client disconnected'))
        request.handle()
        self.assertTrue(request.close_connection)

    def test_windows_connection_abort_during_response_cannot_send_an_error(self):
        request = object.__new__(self.server.RequestHandlerClass)
        request.connection = Mock()
        request.send_response = Mock()
        request.send_header = Mock()
        request.end_headers = Mock()
        request.wfile = Mock()
        request.wfile.write.side_effect = ConnectionAbortedError('client disconnected')
        request._send(200, b'test')
        self.assertTrue(request.close_connection)
        with patch.object(request, '_send') as send:
            request._error(ConnectionAbortedError('client disconnected'))
            send.assert_not_called()

    def test_origin_rejection_does_not_wait_for_an_untrusted_body(self):
        client = self.connect()
        host, port = self.server.server_address
        request = (f'POST /api/overlay/hide HTTP/1.0\r\nHost: {host}:{port}\r\n'
                   'Origin: null\r\nContent-Type: application/json\r\n'
                   'Content-Length: 262144\r\n\r\n')
        started = time.monotonic()
        client.sendall(request.encode('ascii'))
        response = client.recv(4096)
        self.assertIn(b'403', response.split(b'\r\n', 1)[0])
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(self.app.overlay_visible)

    def test_origin_rejection_has_deadline_even_with_trickling_body(self):
        client = self.connect()
        host, port = self.server.server_address
        stop = threading.Event()
        def trickle():
            while not stop.wait(.01):
                try:
                    client.sendall(b'x')
                except OSError:
                    return
        client.sendall((f'POST /api/overlay/hide HTTP/1.0\r\nHost: {host}:{port}\r\n'
                        'Origin: null\r\nContent-Length: 262144\r\n\r\n').encode())
        sender = threading.Thread(target=trickle)
        sender.start()
        try:
            client.settimeout(.5)
            response = client.recv(4096)
            self.assertIn(b'403', response.split(b'\r\n', 1)[0])
            self.assertTrue(self.app.overlay_visible)
        finally:
            stop.set()
            sender.join(1)

    def test_idle_connection_expires_before_orderly_shutdown(self):
        handler = self.server.RequestHandlerClass
        self.assertIsNotNone(handler.timeout)
        self.assertGreater(handler.timeout, 0)
        self.assertLessEqual(handler.timeout, 5)
        entered = threading.Event()
        setup = handler.setup

        def tracking_setup(request):
            setup(request)
            entered.set()

        with patch.object(handler, 'timeout', .1, create=True), patch.object(handler, 'setup', tracking_setup):
            client = self.connect()
            self.assertTrue(entered.wait(1))
            self.assertEqual(client.recv(1), b'')
        self.server.shutdown()
        closer = threading.Thread(target=self.server.server_close)
        closer.start()
        closer.join(1)
        self.assertFalse(closer.is_alive(), 'Idle client held shutdown')

    def test_slow_state_receiver_does_not_block_obs_capture_progress(self):
        handler = self.server.RequestHandlerClass
        entered = threading.Event()
        send = handler._send

        def small_send_buffer(request, *args, **kwargs):
            request.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            entered.set()
            return send(request, *args, **kwargs)

        reader = Mock()
        reader.read_prerace.return_value = None
        reader.is_prerace_layout.return_value = False
        reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.app.capture.reader = reader
        obs = Mock()
        obs.record_status.return_value = {
            'outputActive': False, 'outputPaused': False, 'outputDuration': 0,
        }
        obs.drain_events.return_value = []
        with patch.object(self.app, 'snapshot', return_value={'padding': 'x' * (8 * 1024**2)}), \
                patch.object(handler, '_send', small_send_buffer):
            client = self.connect()
            host, port = self.server.server_address
            client.sendall(f'GET /api/state HTTP/1.0\r\nHost: {host}:{port}\r\n\r\n'.encode())
            self.assertTrue(entered.wait(1))
            self.app.capture.start(obs, 'mock source', interval=.01)
            deadline = time.monotonic() + 2
            while obs.screenshot.call_count < 5 and time.monotonic() < deadline:
                time.sleep(.01)
            count = obs.screenshot.call_count
            client.close()
            self.assertGreaterEqual(count, 5, 'A stalled HTTP receiver blocked screenshot capture')

    def test_state_serialization_and_transmission_happen_outside_application_lock(self):
        handler = self.server.RequestHandlerClass
        observed = []
        send = handler._send

        def check_lock(request, *args, **kwargs):
            acquired = []

            def acquire():
                if self.app.lock.acquire(timeout=.2):
                    acquired.append(True)
                    self.app.lock.release()

            probe = threading.Thread(target=acquire)
            probe.start()
            probe.join(1)
            observed.append(bool(acquired))
            return send(request, *args, **kwargs)

        from urllib.request import urlopen
        host, port = self.server.server_address
        with patch.object(handler, '_send', check_lock):
            with urlopen(f'http://{host}:{port}/api/state', timeout=3) as response:
                self.assertEqual(response.status, 200)
                response.read()
        self.assertEqual(observed, [True])

    def test_nonreading_response_and_incomplete_body_have_bounded_waits(self):
        handler = self.server.RequestHandlerClass
        finished = threading.Event()
        send = handler._send

        def bounded_send(request, *args, **kwargs):
            request.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            try:
                return send(request, *args, **kwargs)
            finally:
                finished.set()

        host, port = self.server.server_address
        with patch.object(handler, 'timeout', .1), patch.object(handler, '_send', bounded_send), \
                patch.object(self.app, 'snapshot', return_value={'padding': 'x' * (8 * 1024**2)}):
            receiver = self.connect()
            receiver.sendall(f'GET /api/state HTTP/1.0\r\nHost: {host}:{port}\r\n\r\n'.encode())
            self.assertTrue(finished.wait(2), 'Response write did not time out')
            sender = self.connect()
            sender.sendall((f'POST /api/overlay/show HTTP/1.0\r\nHost: {host}:{port}\r\n'
                            'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{').encode())
            self.assertEqual(sender.recv(1), b'')

    def test_shutdown_interrupts_a_continuously_trickling_header(self):
        stopped, closed, accepted = threading.Event(), threading.Event(), threading.Event()
        handler = self.server.RequestHandlerClass
        setup = handler.setup

        def track_setup(request):
            setup(request)
            accepted.set()

        def trickle(client):
            while not stopped.wait(.02):
                try:
                    client.sendall(b'x')
                except OSError:
                    break

        def close_server():
            self.server.server_close()
            closed.set()

        with patch.object(handler, 'timeout', .1), patch.object(handler, 'setup', track_setup):
            client = self.connect()
            client.sendall(b'GET /api/state HTTP/1.0\r\nX-Slow: ')
            self.assertTrue(accepted.wait(1))
            sender = threading.Thread(target=trickle, args=(client,))
            sender.start()
            self.server.shutdown()
            closer = threading.Thread(target=close_server)
            closer.start()
            try:
                self.assertTrue(closed.wait(1), 'Trickling headers held orderly shutdown')
            finally:
                stopped.set()
                sender.join(1)
                client.close()
                closer.join(2)
