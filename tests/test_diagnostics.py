"""Safe component logging without real OBS, cameras, readers or user data."""

import errno
import http.client
import io
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mkw_tracker.__main__ import main
from mkw_tracker import diagnostics
from mkw_tracker.diagnostics import configure_logging, log_event
from mkw_tracker.server import Application, make_server


SECRET = 'private-password-token /home/private/recording.mp4 Authorization: Bearer hidden data:image/png;base64,AAAA'


class DiagnosticHelpers:

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.stdout = io.StringIO()

    def configure(self, **kwargs):
        session = configure_logging(self.root, stdout=self.stdout, **kwargs)
        self.addCleanup(session.close)
        return session

    def read(self, component):
        return (self.root / 'logs' / f'{component}.log').read_text(encoding='utf-8')


class DiagnosticTests(DiagnosticHelpers, unittest.TestCase):
    def test_default_levels_route_events_to_each_component_and_warning_stdout(self):
        self.configure()
        events = {'app': 'started', 'http': 'listening', 'capture': 'ready',
                  'obs': 'connected', 'recognition': 'started', 'imports': 'started'}
        for component, event in events.items():
            log_event(component, event)
        log_event('http', 'response', status=200)
        log_event('http', 'rejected', status=403)
        log_event('obs', 'failed', error=RuntimeError(SECRET))
        for component in events:
            text = self.read(component)
            self.assertRegex(text, rf'^\d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ INFO \[{component}\]')
            for other in events.keys() - {component}:
                self.assertNotIn(f'[{other}]', text)
            self.assertNotIn(SECRET, text)
        self.assertNotIn('HTTP response', self.read('http'))
        console = self.stdout.getvalue()
        self.assertIn('WARNING [http]', console)
        self.assertIn('status=403', console)
        self.assertIn('ERROR [obs]', console)
        self.assertIn('check the WebSocket server and password', console)
        self.assertNotIn('INFO', console)
        self.assertNotIn(SECRET, console)

    def test_verbose_adds_debug_metrics_to_files_not_stdout(self):
        self.configure(verbose=True)
        log_event('http', 'response', status=200)
        log_event('capture', 'sample', cycle_ms=251.2, recognition_ms=120)
        log_event('imports', 'progress', percent=50, observed_results=2)
        self.assertIn('DEBUG [http] HTTP response status=200', self.read('http'))
        self.assertIn('recognition_ms=120', self.read('capture'))
        self.assertIn('percent=50', self.read('imports'))
        self.assertEqual(self.stdout.getvalue(), '')

    def test_only_safe_metrics_and_error_categories_are_logged(self):
        self.configure(verbose=True)
        class PrivateError(RuntimeError):
            def __str__(self):
                raise AssertionError('Exception text must not be read')
        log_event('imports', 'failed', error=PrivateError(SECRET), path=SECRET,
                  token=SECRET, percent=SECRET, observed_races=True,
                  status=float('nan'), cycle_ms=float('inf'), port=10**1000)
        text = self.read('imports')
        self.assertIn('reason=operation-error', text)
        for unsafe in (SECRET, 'PrivateError', 'percent=', 'status=', 'path=', 'port=', 'True', 'Traceback'):
            self.assertNotIn(unsafe, text + self.stdout.getvalue())
        cases = [(PermissionError(SECRET), 'permission-denied'),
                 (OSError(errno.EADDRINUSE, SECRET), 'port-in-use'),
                 (FileNotFoundError(SECRET), 'file-not-found'),
                 (TimeoutError(SECRET), 'timeout'), (ConnectionError(SECRET), 'connection-failed'),
                 (ValueError(SECRET), 'invalid-input'), (OSError(SECRET), 'io-error')]
        for error, kind in cases:
            log_event('app', 'failed', error=error)
            self.assertIn(f'reason={kind}', self.read('app'))
        self.assertNotIn(SECRET, self.read('app'))

    def test_raw_logging_and_tracebacks_cannot_enter_component_handlers(self):
        self.configure(verbose=True)
        logger = logging.getLogger('mkw_tracker.diagnostics.http')
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            logger.exception(SECRET, stack_info=True)
            # Even an explicitly marked record renders only the fixed event.
            logger.error(SECRET, exc_info=True, stack_info=True,
                         extra={'diagnostic_event': 'failed', 'diagnostic_metrics': {},
                                'diagnostic_error': SECRET})
        self.assertIn('HTTP operation failed', self.read('http'))
        for text in (self.read('http'), self.stdout.getvalue()):
            self.assertNotIn(SECRET, text)
            self.assertNotIn('Traceback', text)
            self.assertNotIn('test_diagnostics.py', text)

    def test_rotation_is_bounded_and_preserves_recent_events(self):
        with patch.object(diagnostics, 'MAX_BYTES', 180), patch.object(diagnostics, 'BACKUP_COUNT', 2):
            session = self.configure(verbose=True)
        for status in range(100, 130):
            log_event('http', 'response', status=status)
        session.close()
        files = sorted((self.root / 'logs').glob('http.log*'))
        self.assertEqual({path.name for path in files}, {'http.log', 'http.log.1', 'http.log.2'})
        self.assertTrue(all(path.stat().st_size <= 180 for path in files))
        self.assertIn('status=129', self.read('http'))
        self.assertEqual((diagnostics.MAX_BYTES, diagnostics.BACKUP_COUNT), (2 * 1024 * 1024, 3))

    def test_close_restores_loggers_and_configuration_does_not_duplicate_events(self):
        logger = logging.getLogger('mkw_tracker.diagnostics.app')
        previous = (logger.level, logger.propagate, logger.disabled, logger.handlers[:])
        root = logging.getLogger()
        root_state = (root.level, root.handlers[:])
        session = self.configure()
        handlers = tuple(logger.handlers)
        log_event('app', 'started')
        session.close()
        session.close()
        self.assertTrue(all(getattr(handler, 'stream', None) is None
                            for handler in handlers if isinstance(handler, logging.FileHandler)))
        self.assertEqual((logger.level, logger.propagate, logger.disabled, logger.handlers), previous)
        self.assertEqual((root.level, root.handlers), root_state)
        self.configure()
        log_event('app', 'started')
        self.assertEqual(self.read('app').count('Tracker started'), 2)

    def test_partial_configuration_failure_closes_owned_handlers(self):
        factory = diagnostics._RotatingHandler
        created = []
        def make(*args, **kwargs):
            if created:
                raise PermissionError(SECRET)
            handler = factory(*args, **kwargs)
            created.append(handler)
            return handler
        logger = logging.getLogger('mkw_tracker.diagnostics.app')
        previous = logger.handlers[:]
        with patch.object(diagnostics, '_RotatingHandler', side_effect=make):
            with self.assertRaises(PermissionError):
                self.configure()
        self.assertEqual(logger.handlers, previous)
        self.assertIsNone(created[0].stream)

    def test_file_write_failure_has_safe_once_only_console_fallback(self):
        self.configure()
        logger = logging.getLogger('mkw_tracker.diagnostics.app')
        handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        with patch.object(handler, 'shouldRollover', side_effect=OSError(SECRET)), \
                patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                patch('sys.stderr', new_callable=io.StringIO) as stderr:
            log_event('app', 'started')
            log_event('app', 'started')
        self.assertEqual(stdout.getvalue().count('Cannot write diagnostic log'), 1)
        self.assertNotIn(SECRET, stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')

    def test_logs_ignored_even_under_custom_data_directory(self):
        # CI runs a source archive without .git; test the shipped rules in a
        # disposable repository, not the caller's checkout or global excludes.
        (self.root / '.gitignore').write_bytes(
            (Path(__file__).resolve().parents[1] / '.gitignore').read_bytes())
        subprocess.run(['git', 'init', '--quiet', str(self.root)], check=True,
                       capture_output=True)
        excludes = self.root / 'empty-excludes'
        excludes.write_text('')
        result = subprocess.run(['git', '-c', f'core.excludesFile={excludes}', 'check-ignore', '--stdin'],
                                cwd=self.root,
                                input='logs/app.log\ncustom-data/logs/http.log.3\nsource.py\n',
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ['logs/app.log', 'custom-data/logs/http.log.3'])


class DiagnosticIntegrationTests(DiagnosticHelpers, unittest.TestCase):
    def run_cli(self, *options, application_error=None, bind_error=None, cleanup_error=None):
        with patch('sys.argv', ['mkw_tracker', '--data-dir', str(self.root), *options]), \
                patch('sys.stdout', self.stdout), patch('sys.stderr', new_callable=io.StringIO) as stderr, \
                patch('mkw_tracker.__main__.Application') as application, \
                patch('mkw_tracker.__main__.make_server') as server:
            server.return_value.server_address = ('127.0.0.1', 8765)
            server.return_value.serve_forever.side_effect = lambda **_: log_event('http', 'response', status=200)
            application.side_effect = application_error
            server.side_effect = bind_error
            application.return_value.close.side_effect = cleanup_error
            if any((application_error, bind_error, cleanup_error)):
                with self.assertRaises(SystemExit) as exit_error:
                    main()
                self.assertNotEqual(exit_error.exception.code, 0)
            else:
                main()
            return application, server, stderr.getvalue()

    def test_cli_verbose_reaches_real_logging_configuration(self):
        self.run_cli('--verbose')
        self.assertIn('DEBUG [http] HTTP response status=200', self.read('http'))
        self.assertIn('Tracker started', self.read('app'))
        self.assertIn('Tracker stopped', self.read('app'))
        self.assertNotIn('DEBUG', self.stdout.getvalue())

    def test_cli_normal_hides_debug(self):
        self.run_cli()
        self.assertNotIn('DEBUG', self.read('http'))
        self.assertIn('Race tracker:', self.stdout.getvalue())

    def test_cli_startup_bind_and_cleanup_errors_are_concise_and_safe(self):
        for kwargs in ({'application_error': RuntimeError(SECRET)},
                       {'bind_error': OSError(errno.EADDRINUSE, SECRET)},
                       {'cleanup_error': RuntimeError(SECRET)}):
            with self.subTest(kwargs=tuple(kwargs)):
                _, _, stderr = self.run_cli(**kwargs)
                text = self.stdout.getvalue() + stderr
                self.assertIn('ERROR', text)
                self.assertNotIn(SECRET, text)
                self.assertNotIn('Traceback', text)
        self.assertIn('reason=port-in-use', self.read('http'))

    def test_cli_log_directory_failure_does_not_print_exception(self):
        with patch('sys.argv', ['mkw_tracker', '--data-dir', str(self.root)]), \
                patch('mkw_tracker.__main__.configure_logging', side_effect=PermissionError(SECRET)), \
                patch('sys.stdout', self.stdout):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 1)
        self.assertIn('check folder access', self.stdout.getvalue())
        self.assertNotIn(SECRET, self.stdout.getvalue())

    def test_cli_demo_cleanup_error_does_not_print_private_paths(self):
        temporary = SimpleNamespace(name=str(self.root), cleanup=Mock(side_effect=OSError(SECRET)))
        with patch('mkw_tracker.__main__.tempfile.TemporaryDirectory', return_value=temporary):
            # The helper already expects an error when cleanup_error is set;
            # this extra failure exercises final cleanup after service cleanup.
            self.run_cli('--demo', cleanup_error=RuntimeError(SECRET))
        self.assertIn('shutdown incomplete', self.read('app'))
        self.assertNotIn(SECRET, self.stdout.getvalue())

    def test_demo_closes_log_files_before_removing_temporary_directory(self):
        def cleanup():
            for component in diagnostics.EVENTS:
                logger = logging.getLogger(f'mkw_tracker.diagnostics.{component}')
                self.assertFalse(any(isinstance(h, logging.FileHandler) for h in logger.handlers))
        temporary = SimpleNamespace(name=str(self.root), cleanup=Mock(side_effect=cleanup))
        with patch('mkw_tracker.__main__.tempfile.TemporaryDirectory', return_value=temporary):
            self.run_cli('--demo')
        temporary.cleanup.assert_called_once()

    def test_capture_success_records_sample_timings(self):
        from mkw_tracker.capture import CaptureCoordinator
        self.configure(verbose=True)
        capture = CaptureCoordinator(Mock(), threading.RLock(), self.root, Mock())
        self.addCleanup(capture.close)
        client = Mock()
        client.record_status.return_value = {'outputActive': False, 'outputPaused': False, 'outputDuration': 0}
        client.drain_events.return_value = []
        capture.clock.update = Mock(return_value=(None, None))
        capture.observe = Mock(side_effect=lambda *args: capture.stop_event.set())
        capture.start(client, SECRET)
        capture.thread.join(5)
        self.assertFalse(capture.thread.is_alive())
        text = self.read('capture')
        self.assertIn('Capture connected; recognizer ready', text)
        self.assertIn('Capture sample processed sample_count=1', text)
        self.assertIn('recognition_ms=', text)
        self.assertNotIn(SECRET, text)

    def test_browser_favicon_is_themed_and_quiet_but_still_checks_host(self):
        self.configure()
        app = Application(':memory:', self.root)
        self.addCleanup(app.close)
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        worker.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                for target in ('/favicon.ico', '/favicon.ico?token=private-icon-query'):
                    connection.request('GET', target)
                    response = connection.getresponse()
                    with response:
                        body = response.read()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader('Content-Type'), 'image/svg+xml')
                        self.assertIn(b'<svg', body)
                        self.assertIn(app.theme_library.resolve(app.settings['theme'])['tokens']['accent'].encode(), body)
                self.assertEqual(self.stdout.getvalue(), '')
                self.assertEqual(self.read('http'), '')
                connection.request('GET', '/favicon.ico', headers={'Host': 'untrusted.invalid'})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()
        self.assertIn('HTTP request rejected', self.stdout.getvalue())
        self.assertIn('status=403', self.stdout.getvalue())
        self.assertNotIn('private-icon-query', self.read('http'))

    def test_http_status_and_internal_failure_do_not_log_request_payloads(self):
        self.configure(verbose=True)
        app = Application(':memory:', self.root)
        self.addCleanup(app.close)
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        worker.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request('GET', '/private-recording?token=secret-query',
                                   headers={'Authorization': 'Bearer private-header'})
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                with patch.object(app, 'snapshot', side_effect=RuntimeError(SECRET)):
                    connection.request('GET', '/api/state')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 500)
                    response.read()
                # Also cover socketserver's outer fallback without raw tracebacks.
                with patch('sys.stderr', new_callable=io.StringIO) as stderr:
                    server.handle_error(None, ('private-client', 1234))
                self.assertEqual(stderr.getvalue(), '')
            finally:
                connection.close()
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()
        text = self.read('http') + self.stdout.getvalue()
        self.assertIn('status=404', text)
        self.assertIn('HTTP resource not found', text)
        self.assertNotIn('HTTP request rejected', text)
        self.assertIn('status=500', text)
        self.assertIn('HTTP operation failed', text)
        for private in (SECRET, 'secret-query', 'private-recording', 'private-header', 'private-client'):
            self.assertNotIn(private, text)

    def test_capture_failure_emits_safe_event_without_hardware(self):
        from mkw_tracker.capture import CaptureCoordinator
        self.configure(verbose=True)
        capture = CaptureCoordinator(Mock(), threading.RLock(), self.root, Mock())
        self.addCleanup(capture.close)
        client = Mock()
        def fail():
            raise RuntimeError(SECRET)
        client.connect.side_effect = fail
        # End after one failed attempt; never wait or touch a real source.
        capture.stop_event.wait = lambda _: capture.stop_event.set()
        capture.start(client, SECRET)
        capture.thread.join(5)
        self.assertFalse(capture.thread.is_alive())
        text = self.read('capture') + self.stdout.getvalue()
        self.assertIn('Starting capture', text)
        self.assertIn('Capture unavailable', text)
        self.assertIn('Capture stopped', text)
        self.assertNotIn(SECRET, text)

    def test_obs_connection_events_do_not_include_password_or_transport_errors(self):
        try:
            from mkw_tracker.obs import ObsClient, ObsError
        except ImportError:
            self.skipTest('OBS adapter requires optional capture dependencies')
        self.configure(verbose=True)
        connection = Mock()
        connection.recv.side_effect = ['{"op":0,"d":{"rpcVersion":1}}',
                                       '{"op":2,"d":{"negotiatedRpcVersion":1}}']
        client = ObsClient(password=SECRET, connection_factory=lambda *a, **k: connection)
        client.connect()
        client.close()
        client = ObsClient(password=SECRET, connection_factory=Mock(side_effect=RuntimeError(SECRET)))
        with self.assertRaises(ObsError):
            client.connect()
        text = self.read('obs') + self.stdout.getvalue()
        self.assertIn('Connecting to local OBS', text)
        self.assertIn('OBS WebSocket connected', text)
        self.assertIn('OBS connection failed', text)
        self.assertNotIn(SECRET, text)

    def test_import_lifecycle_emits_events_with_no_recording_path(self):
        from mkw_tracker.imports import ImportManager
        self.configure(verbose=True)
        app = Application(':memory:', self.root)
        self.addCleanup(app.close)
        video_path = self.root / 'private-recording.mkv'
        video_path.write_bytes(b'not real video')
        reader = Mock()
        video = Mock()
        video.get.side_effect = lambda key: 1
        video.set.return_value = True
        video.read.return_value = (True, object())
        coordinator = SimpleNamespace(state={'armed': False, 'race_id': None}, observe=Mock())
        manager = ImportManager(app, reader_factory=lambda _: reader, video_factory=lambda _: video,
                                coordinator_factory=lambda *a, **k: coordinator)
        self.addCleanup(manager.close)
        for failed in (False, True):
            if failed:
                manager.video_factory = Mock(side_effect=RuntimeError(SECRET))
            manager.start(str(video_path))
            manager.thread.join(5)
            self.assertFalse(manager.thread.is_alive())
        text = self.read('imports') + self.stdout.getvalue()
        for expected in ('Video import started', 'Video import recognizer ready', 'Video import progress',
                         'Video import completed', 'Video import failed'):
            self.assertIn(expected, text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn('private-recording', text)
        self.assertNotIn(str(self.root), text)
