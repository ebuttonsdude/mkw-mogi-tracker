import errno
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from mkw_tracker.cameras import BACKEND_UNAVAILABLE_MESSAGE, CameraClient, CameraManager, enumerate_cameras
from mkw_tracker.capture import CaptureCoordinator, RecordingClock
from mkw_tracker.store import TrackerStore


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Worker did not reach the expected state')
        time.sleep(.01)


@unittest.skipUnless(sys.platform == 'linux', 'V4L2 enumeration requires Linux ioctl and open flags')
class EnumerationTests(unittest.TestCase):
    def test_effective_caps_exclude_output_metadata_and_never_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(4):
                entry = root / f'video{index}'
                entry.mkdir()
                (entry / 'name').write_text(f'Camera {index}')
            opened, closed = [], []

            def open_device(path, flags):
                self.assertTrue(flags & __import__('os').O_NOFOLLOW)
                index = int(path.name[5:])
                opened.append(index)
                return index + 10

            def query(fd, request, data, mutate):
                self.assertEqual(request, 0x80685600)
                caps = [1, 2, 0x80000001, 0x80000000][fd - 10]
                device_caps = [0, 0, 0x00800000, 0x1000][fd - 10]
                struct.pack_into('III', data, 80, 1, caps, device_caps)

            with patch('mkw_tracker.cameras.os.open', side_effect=open_device), \
                    patch('mkw_tracker.cameras.os.fstat', return_value=SimpleNamespace(st_mode=0o020000)), \
                    patch('mkw_tracker.cameras.os.close', side_effect=closed.append), \
                    patch('mkw_tracker.cameras.fcntl.ioctl', side_effect=query):
                devices = enumerate_cameras(root)
            self.assertEqual([item['id'] for item in devices], ['/dev/video0', '/dev/video3'])
            self.assertTrue(all(item['available'] for item in devices))
            self.assertEqual(opened, [0, 1, 2, 3])
            self.assertEqual(closed, [10, 11, 12, 13])

    def test_permission_or_symlink_failure_is_visible_but_not_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'video10'
            entry.mkdir()
            (entry / 'name').write_text('OBS Virtual Camera')
            with patch('mkw_tracker.cameras.os.open', side_effect=OSError(errno.ELOOP, 'symlink')):
                devices = enumerate_cameras(directory)
            self.assertEqual(devices[0]['name'], 'OBS Virtual Camera')
            self.assertFalse(devices[0]['available'])
            self.assertTrue(devices[0]['error'])

    def test_non_character_file_is_not_opened_as_a_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'video1').mkdir()
            with patch('mkw_tracker.cameras.os.open', return_value=4), \
                    patch('mkw_tracker.cameras.os.close'), \
                    patch('mkw_tracker.cameras.os.fstat', return_value=SimpleNamespace(st_mode=0o100000)), \
                    patch('mkw_tracker.cameras.fcntl.ioctl') as query:
                self.assertFalse(enumerate_cameras(directory)[0]['available'])
                query.assert_not_called()


class FakeCamera:
    def __init__(self, gate=None):
        self.gate = gate
        self.released = False
        self.read_thread = self.release_thread = None
        self.count = 0

    def read(self):
        self.read_thread = threading.get_ident()
        if self.gate:
            self.gate.wait()
        time.sleep(.005)
        self.count += 1
        return True, np.full((2, 2, 3), self.count % 255, dtype=np.uint8)

    def release(self):
        self.release_thread = threading.get_ident()
        self.released = True


class ClientTests(unittest.TestCase):
    def test_drainer_returns_fresh_bgr_frames_and_owns_release(self):
        camera = FakeCamera()
        client = CameraClient('/dev/video10', camera_factory=lambda _: camera)
        client.connect()
        first = client.screenshot('ignored')
        time.sleep(.03)
        second = client.screenshot('ignored')
        client.close()
        self.assertEqual(first.shape, (2, 2, 3))
        self.assertGreater(int(second[0, 0, 0]), int(first[0, 0, 0]))
        self.assertTrue(camera.released)
        self.assertEqual(camera.read_thread, camera.release_thread)
        self.assertTrue(client.record_status()['clockUnavailable'])

    def test_blocked_read_never_releases_on_the_calling_thread(self):
        gate = threading.Event()
        camera = FakeCamera(gate)
        client = CameraClient('/dev/video10', camera_factory=lambda _: camera)
        client.connect()
        wait_for(lambda: camera.read_thread is not None)
        client.close()
        self.assertTrue(client.thread.is_alive())
        self.assertFalse(camera.released)
        with self.assertRaisesRegex(RuntimeError, 'still stopping'):
            client.connect()
        gate.set()
        client.thread.join(1)
        self.assertTrue(camera.released)
        self.assertEqual(camera.read_thread, camera.release_thread)

    def test_missing_obs_clock_is_not_a_verified_inactive_recording(self):
        store = TrackerStore(':memory:')
        try:
            clock = RecordingClock(store)
            self.assertEqual(clock.update(CameraClient('/dev/video10').record_status()), (None, None))
            self.assertIsNone(clock.update({'outputActive': True, 'outputDuration': 2000})[1])
            clock.update({'outputActive': False})
            self.assertEqual(clock.update({'outputActive': True, 'outputDuration': 3000})[1], 3000)
            clock.update({'clockUnavailable': True})
            self.assertIsNone(clock.update({'outputActive': True, 'outputDuration': 4000})[1])
        finally:
            store.close()

    def test_optional_obs_failure_recovers_without_exposing_password(self):
        obs = Mock()
        obs.connect.side_effect = [RuntimeError('secret'), None]
        obs.record_status.return_value = {'outputActive': True, 'outputPaused': False, 'outputDuration': 123}
        camera = FakeCamera()
        client = CameraClient('/dev/video10', camera_factory=lambda _: camera, clock=obs)
        client.connect()
        self.assertEqual(client.record_status()['outputDuration'], 123)
        self.assertTrue(client.clock_connected)
        client.close()

    def test_failed_release_blocks_reconnect_even_after_reader_thread_exits(self):
        camera = Mock()
        camera.read.side_effect = RuntimeError('device read failed')
        camera.release.side_effect = OSError('device resource leaked')
        factory = Mock(return_value=camera)
        client = CameraClient('/dev/video10', camera_factory=factory)
        try:
            client.connect()
        except RuntimeError:
            pass
        client.thread.join(1)
        self.assertFalse(client.thread.is_alive())
        self.assertTrue(client.release_failed)
        with self.assertRaisesRegex(RuntimeError, 'resource could not be released'):
            client.connect()
        factory.assert_called_once_with('/dev/video10')
        client.close()

    def test_open_and_read_failures_report_safe_stage_and_exception_type(self):
        for stage in ['camera open', 'camera read']:
            with self.subTest(stage=stage):
                camera = Mock()
                failure = OSError('password=do-not-display')
                if stage == 'camera open':
                    factory = Mock(side_effect=failure)
                else:
                    camera.read.side_effect = failure
                    factory = Mock(return_value=camera)
                client = CameraClient('/dev/video10', camera_factory=factory)
                try:
                    client.connect()
                except RuntimeError:
                    pass
                client.thread.join(1)
                self.assertIn(f'{stage} (OSError)', client.error)
                self.assertNotIn('do-not-display', client.error)
                client.close()

    @unittest.skipUnless(sys.platform == 'linux', 'Pinned V4L2 devices require Linux open flags and procfs')
    def test_constructor_cleanup_failure_closes_pin_and_blocks_reconnect(self):
        video = Mock()
        video.isOpened.return_value = False
        video.release.side_effect = OSError('private release failure')
        cv2 = SimpleNamespace(VideoCapture=Mock(return_value=video), CAP_V4L2=200,
                              CAP_PROP_BUFFERSIZE=38)
        with patch.dict('sys.modules', {'cv2': cv2}), \
                patch('mkw_tracker.cameras.os.open', return_value=42) as opened, \
                patch('mkw_tracker.cameras.os.fstat', return_value=SimpleNamespace(st_mode=0o020000)), \
                patch('mkw_tracker.cameras._capture_capabilities', return_value=True), \
                patch('mkw_tracker.cameras.os.close') as closed:
            client = CameraClient('/dev/video10')
            with self.assertRaises(RuntimeError):
                client.connect()
            client.thread.join(1)
            self.assertTrue(client.release_failed)
            closed.assert_called_once_with(42)
            with self.assertRaisesRegex(RuntimeError, 'resource could not be released'):
                client.connect()
            self.assertEqual(opened.call_count, 1)
            cv2.VideoCapture.assert_called_once()
            self.assertNotIn('private', client.error)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        backend = patch('mkw_tracker.cameras.CAMERA_BACKEND_AVAILABLE', True)
        backend.start()
        self.addCleanup(backend.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.store = TrackerStore(':memory:')
        self.app = SimpleNamespace(store=self.store, lock=threading.RLock(),
                                   data_dir=Path(self.directory.name), active_uploads=set())
        self.app.capture = CaptureCoordinator(self.store, self.app.lock, self.app.data_dir, None)
        self.reader = Mock()
        self.reader.read_prerace.return_value = None
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.app.imports = SimpleNamespace(thread=None, snapshot=lambda: {'active': None},
                                           reader_factory=lambda _: self.reader)
        self.camera = FakeCamera()
        self.enumerator = Mock(return_value=[{'id': '/dev/video10', 'path': '/dev/video10',
                                             'name': 'OBS Camera', 'available': True, 'error': None}])
        self.manager = CameraManager(self.app, enumerator=self.enumerator,
                                     client_factory=lambda device, **kwargs: CameraClient(
                                         device, camera_factory=lambda _: self.camera, **kwargs))

    def tearDown(self):
        self.manager.close()
        self.store.close()
        self.directory.cleanup()

    def test_initial_capture_input_is_obs_without_starting_resources(self):
        status = self.manager.snapshot()['status']
        self.assertEqual(status['capture_mode'], 'obs')
        self.assertEqual(status['state'], 'stopped')
        self.assertFalse(status['busy'])
        self.assertIsNone(status['device_id'])
        self.assertIsNone(status['obs_source'])
        self.assertIn('OBS source or scene', status['message'])
        self.assertIsNone(self.manager.thread)
        self.assertIsNone(self.app.capture.thread)
        self.enumerator.assert_not_called()
        self.reader.read.assert_not_called()
        self.assertEqual(self.camera.count, 0)

    def test_each_qwen_profile_reaches_explicit_capture_reader(self):
        from mkw_tracker.name_engines import GENERATIVE_NAME_ENGINES
        factory = Mock(return_value=self.reader)
        self.manager.reader_factory = factory
        obs = Mock()
        obs.record_status.return_value = {'outputActive': False, 'outputPaused': False, 'outputDuration': 0}
        obs.screenshot.return_value = np.zeros((2, 2, 3), dtype=np.uint8)
        obs.drain_events.return_value = []
        for engine in GENERATIVE_NAME_ENGINES:
            with self.subTest(engine=engine), patch('mkw_tracker.obs.ObsClient', return_value=obs):
                self.manager.start(capture_mode='obs', obs_source='Fixture', name_engine=engine)
                wait_for(lambda: factory.call_args is not None and factory.call_args.args == (engine,))
                factory.assert_called_with(engine)
                self.manager.stop()
                self.manager.thread.join(3)
                self.assertFalse(self.manager.thread.is_alive())
        self.enumerator.assert_not_called()
        self.assertEqual(self.camera.count, 0)

    def test_obs_start_stop_restart_uses_source_and_never_opens_camera(self):
        obs = Mock()
        obs.record_status.return_value = {'outputActive': False, 'outputPaused': False,
                                          'outputDuration': 0}
        obs.screenshot.return_value = np.zeros((2, 2, 3), dtype=np.uint8)
        obs.drain_events.return_value = []
        camera_factory = Mock()
        self.manager.client_factory = camera_factory
        self.app.capture.state['pending'].append({'id': 'held-review'})
        with patch('mkw_tracker.obs.ObsClient', return_value=obs) as factory:
            status = self.manager.start(capture_mode='obs', obs_source='Desktop',
                                        obs_password='private-token')
            self.assertEqual(status['capture_mode'], 'obs')
            self.assertEqual(status['obs_source'], 'Desktop')
            self.assertIsNone(status['device_id'])
            wait_for(lambda: obs.screenshot.call_count > 0)
            obs.screenshot.assert_called_with('Desktop')
            factory.assert_called_with('ws://127.0.0.1:4455', 'private-token')
            with self.assertRaisesRegex(ValueError, 'Stop capture'):
                self.manager.start(capture_mode='obs', obs_source='Desktop')
            self.assertEqual(self.manager.stop()['state'], 'stopping')
            self.manager.thread.join(3)
            self.assertFalse(self.manager.thread.is_alive())
            obs.close.assert_called()
            self.assertEqual(self.manager.snapshot()['status']['state'], 'stopped')
            self.assertEqual(self.app.capture.state['pending'], [{'id': 'held-review'}])
            self.assertEqual(self.store.snapshot()['races'], [])
            self.manager.start(capture_mode='obs', obs_source='In-Game Stack (Light)')
            wait_for(lambda: obs.connect.call_count == 2)
        self.enumerator.assert_not_called()
        camera_factory.assert_not_called()

    def test_obs_invalid_options_and_busy_import_never_probe_cameras(self):
        for options in [{'capture_mode': 'wrong'}, {'capture_mode': []},
                        {'capture_mode': 'obs'}, {'capture_mode': 'obs', 'obs_source': '  '},
                        {'capture_mode': 'obs', 'obs_source': 123},
                        {'capture_mode': 'obs', 'obs_source': 'x' * 201},
                        {'capture_mode': 'obs', 'obs_source': '\ud800'},
                        {'capture_mode': 'obs', 'obs_source': 'Desktop', 'name_engine': 'bad'},
                        {'capture_mode': 'obs', 'obs_source': 'Desktop', 'obs_url': 'ws://remote:4455'}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.manager.start(**options)
        self.app.active_uploads.add(Path('/tmp/upload'))
        with self.assertRaisesRegex(ValueError, 'imports/uploads'):
            self.manager.start(capture_mode='obs', obs_source='Desktop')
        self.enumerator.assert_not_called()
        self.assertEqual(self.camera.count, 0)

    def test_failed_import_cleanup_blocks_camera_and_obs_admission(self):
        self.app.imports.cleanup_failed = True
        for options in ({'device_id': '/dev/video10'},
                        {'capture_mode': 'obs', 'obs_source': 'Game'}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'imports/uploads'):
                self.manager.start(**options)
        self.assertIsNone(self.manager.thread)
        self.assertEqual(self.camera.count, 0)

    def test_obs_close_failure_retains_busy_lifetime(self):
        obs = Mock()
        obs.record_status.return_value = {'outputActive': False, 'outputPaused': False,
                                          'outputDuration': 0}
        obs.screenshot.return_value = np.zeros((2, 2, 3), dtype=np.uint8)
        obs.drain_events.return_value = []
        def close():
            if threading.current_thread() is self.manager.thread:
                raise RuntimeError('private-token')
        obs.close.side_effect = close
        with patch('mkw_tracker.obs.ObsClient', return_value=obs):
            self.manager.start(capture_mode='obs', obs_source='Desktop')
            wait_for(lambda: obs.screenshot.call_count > 0)
            self.manager.stop()
            wait_for(lambda: self.manager.cleanup_failed)
            self.assertTrue(self.app.capture.thread.is_alive())
            status = self.manager.snapshot()['status']
            self.assertTrue(status['busy'])
            self.assertNotIn('private-token', status['message'])
            with self.assertRaisesRegex(ValueError, 'Stop capture'):
                self.manager.start(capture_mode='obs', obs_source='Desktop')
            obs.close.side_effect = None
            self.manager.cleanup_failed = False
            self.manager.thread.join(3)
            self.assertFalse(self.manager.thread.is_alive())

    def test_obs_connection_failure_is_safe_and_stop_allows_retry(self):
        from mkw_tracker.obs import ObsClient
        connection = Mock(side_effect=OSError('private-token'))
        client = ObsClient(password='private-token', connection_factory=connection)
        with patch('mkw_tracker.obs.ObsClient', return_value=client):
            self.manager.start(capture_mode='obs', obs_source='Desktop')
            wait_for(lambda: self.app.capture.status == 'error')
            status = self.manager.snapshot()['status']
            self.assertEqual(status['state'], 'error')
            self.assertTrue(status['busy'])
            self.assertNotIn('private-token', status['message'])
            begin = time.monotonic()
            self.manager.stop()
            self.assertLess(time.monotonic() - begin, .2)
            self.manager.thread.join(3)
            self.assertFalse(self.manager.thread.is_alive())
            self.manager.start(capture_mode='obs', obs_source='Desktop')
            wait_for(lambda: connection.call_count >= 2)
        self.enumerator.assert_not_called()

    def test_obs_recognizer_failure_is_safe_and_releases_busy_state(self):
        self.manager.reader_factory = Mock(side_effect=RuntimeError('private-token'))
        self.manager.start(capture_mode='obs', obs_source='Desktop')
        self.manager.thread.join(2)
        status = self.manager.snapshot()['status']
        self.assertEqual(status['state'], 'error')
        self.assertFalse(status['busy'])
        self.assertIn('recognizer setup (RuntimeError)', status['message'])
        self.assertNotIn('private-token', status['message'])
        self.assertFalse(self.app.capture.thread.is_alive())
        self.enumerator.assert_not_called()

    def test_cached_snapshot_does_not_probe_under_application_lock(self):
        self.assertEqual(self.manager.snapshot()['devices'], [])
        self.assertFalse(self.manager.snapshot()['status']['busy'])
        self.enumerator.assert_not_called()
        self.assertEqual(self.manager.snapshot(refresh=True)['devices'][0]['id'], '/dev/video10')
        self.manager.snapshot()
        self.enumerator.assert_called_once()

    def test_explicit_start_stop_retains_reviews_and_never_assigns_clock(self):
        original = self.app.capture
        original.state['pending'].append({'id': 'held-review'})
        self.assertEqual(self.manager.start('/dev/video10')['state'], 'starting')
        wait_for(lambda: self.camera.count > 2)
        self.assertIs(self.app.capture, original)
        self.assertEqual(original.state['pending'], [{'id': 'held-review'}])
        self.assertFalse(original.clock.valid)
        self.assertIn('unknown', original.clock.message)
        self.assertEqual(self.manager.stop()['state'], 'stopping')
        self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())
        self.assertEqual(self.manager.snapshot()['status']['state'], 'stopped')
        self.assertTrue(self.camera.released)
        self.assertEqual(original.state['pending'], [{'id': 'held-review'}])
        self.assertEqual(self.store.snapshot()['races'], [])
        self.manager.start('/dev/video10')
        wait_for(lambda: self.reader.warmup.call_count >= 2)

    def test_bad_device_engine_and_clock_flag_never_open_stream(self):
        for device in ['/dev/video10/../video0', 'http://camera', '/tmp/video0', '/dev/video01', None]:
            with self.assertRaises(ValueError):
                self.manager.start(device)
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10', name_engine='bad')
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10', use_obs_clock='yes')
        self.assertEqual(self.camera.count, 0)

    def test_unavailable_input_and_busy_work_reject_before_loading(self):
        factory = Mock(return_value=self.reader)
        self.manager.reader_factory = factory
        self.enumerator.return_value[0]['available'] = False
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.manager.start('/dev/video10')
        self.enumerator.return_value[0]['available'] = True
        self.app.active_uploads.add(Path('/tmp/upload'))
        with self.assertRaisesRegex(ValueError, 'imports/uploads'):
            self.manager.start('/dev/video10')
        self.app.active_uploads.clear()
        self.app.imports.thread = Mock(is_alive=lambda: True)
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        factory.assert_not_called()
        self.app.imports.thread = None

    def test_slow_factory_stop_is_prompt_and_keeps_capture_busy(self):
        gate, entered = threading.Event(), threading.Event()

        def factory(_engine):
            entered.set()
            gate.wait(3)
            return self.reader

        self.manager.reader_factory = factory
        self.manager.start('/dev/video10')
        self.assertTrue(entered.wait(1))
        begin = time.monotonic()
        self.manager.stop()
        self.assertLess(time.monotonic() - begin, .2)
        self.assertTrue(self.app.capture.thread.is_alive())
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        gate.set()
        self.manager.thread.join(2)
        self.assertFalse(self.manager.thread.is_alive())
        self.reader.close.assert_called()
        self.assertEqual(self.camera.count, 0)

    def test_blocked_read_keeps_busy_until_owning_worker_releases(self):
        gate = threading.Event()
        self.camera = FakeCamera(gate)
        self.manager.start('/dev/video10')
        wait_for(lambda: self.camera.read_thread is not None)
        self.manager.stop()
        time.sleep(.3)
        self.assertTrue(self.app.capture.thread.is_alive())
        self.assertFalse(self.camera.released)
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        gate.set()
        self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())
        self.assertTrue(self.camera.released)

    def test_factory_failure_clears_busy_and_uses_safe_error(self):
        self.manager.reader_factory = Mock(side_effect=RuntimeError('secret credentials'))
        self.manager.start('/dev/video10')
        self.manager.thread.join(2)
        status = self.manager.snapshot()['status']
        self.assertEqual(status['state'], 'error')
        self.assertNotIn('secret', status['message'])
        self.assertIn('recognizer setup (RuntimeError)', status['message'])
        self.assertFalse(self.app.capture.thread.is_alive())

    def test_cli_stop_keeps_busy_until_reader_and_capture_have_closed(self):
        stopped = threading.Event()
        close_gate = threading.Event()
        capture = self.app.capture
        capture.thread = threading.Thread(target=lambda: stopped.wait(3), daemon=True)
        capture.thread.start()
        capture.client = Mock(close=lambda: stopped.set())
        capture.reader = Mock(close=lambda: close_gate.wait(3))
        self.assertEqual(self.manager.snapshot()['status']['state'], 'running')
        self.assertTrue(self.manager.snapshot()['status']['busy'])
        self.assertEqual(self.manager.stop()['state'], 'stopping')
        wait_for(stopped.is_set)
        self.assertTrue(capture.thread.is_alive())
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        close_gate.set()
        self.manager.thread.join(2)
        self.assertFalse(capture.thread.is_alive())
        self.assertEqual(self.manager.snapshot()['status']['state'], 'stopped')

    def test_release_failure_keeps_busy_instead_of_starting_another_capture(self):
        original_release = self.camera.release
        self.camera.release = Mock(side_effect=OSError('device close failed'))
        self.manager.start('/dev/video10')
        wait_for(lambda: self.camera.count > 2)
        self.manager.stop()
        wait_for(lambda: self.manager.client and self.manager.client.release_failed)
        self.assertTrue(self.app.capture.thread.is_alive())
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        # Recover the mock resource explicitly so this test does not leave a worker.
        original_release()
        self.manager.client.release_failed = False
        self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())

    def test_failed_recognizer_close_retains_busy_lifetime(self):
        self.reader.close.side_effect = RuntimeError('worker is still alive')
        self.manager.start('/dev/video10')
        wait_for(lambda: self.camera.count > 2)
        self.manager.stop()
        wait_for(lambda: self.manager.cleanup_failed)
        self.assertTrue(self.app.capture.thread.is_alive())
        with self.assertRaises(ValueError):
            self.manager.start('/dev/video10')
        self.reader.close.side_effect = None
        self.manager.cleanup_failed = False
        self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())

    def test_client_setup_failure_reports_stage_and_type_without_payload(self):
        self.manager.client_factory = Mock(side_effect=ValueError('password=do-not-display'))
        self.manager.start('/dev/video10')
        self.manager.thread.join(2)
        message = self.manager.snapshot()['status']['message']
        self.assertIn('camera client setup (ValueError)', message)
        self.assertNotIn('do-not-display', message)
        self.assertFalse(self.app.capture.thread.is_alive())

    def test_unavailable_backend_is_explicit_and_never_probes_devices(self):
        manager = CameraManager(self.app)
        try:
            with patch('mkw_tracker.cameras.CAMERA_BACKEND_AVAILABLE', False), \
                    patch('mkw_tracker.cameras.os.open', side_effect=AssertionError('must not probe devices')):
                snapshot = manager.snapshot(refresh=True)
                self.assertEqual(snapshot['devices'], [])
                # Default OBS mode keeps the source prompt; the Linux-only limitation
                # is only reported once camera capture is actually selected.
                self.assertEqual(
                    snapshot['status']['message'],
                    'Enter the exact OBS source or scene name, then start capture.')
                manager.status['capture_mode'] = 'camera'
                snapshot = manager.snapshot(refresh=True)
                self.assertEqual(snapshot['status']['message'], BACKEND_UNAVAILABLE_MESSAGE)
                factory = Mock(return_value=self.reader)
                manager.reader_factory = factory
                with self.assertRaisesRegex(ValueError, 'Linux V4L2'):
                    manager.start(capture_mode='camera', device_id='/dev/video0')
                self.assertIsNone(manager.thread)
                factory.assert_not_called()
        finally:
            manager.close()


class PlatformStartupTests(unittest.TestCase):
    """Simulate a platform without the fcntl module, such as native Windows."""

    def _run_without_fcntl(self, code):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', code], cwd=root,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, f'stdout={result.stdout!r} stderr={result.stderr!r}')

    def test_help_exits_without_importing_fcntl(self):
        self._run_without_fcntl(
            "import sys\n"
            "sys.modules['fcntl'] = None\n"
            "from mkw_tracker.__main__ import main\n"
            "sys.argv = ['mkw-tracker', '--help']\n"
            "try:\n"
            "    main()\n"
            "except SystemExit as exc:\n"
            "    assert exc.code == 0, exc.code\n")

    def test_manual_application_and_server_start_without_fcntl(self):
        self._run_without_fcntl(
            "import pathlib, sys, tempfile\n"
            "sys.modules['fcntl'] = None\n"
            "from mkw_tracker.server import Application, make_server\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    app = Application(pathlib.Path(directory) / 'tracker.sqlite3', pathlib.Path(directory), mode='manual')\n"
            "    try:\n"
            "        server = make_server(app, 0)\n"
            "    finally:\n"
            "        server.server_close()\n"
            "        app.close()\n")
