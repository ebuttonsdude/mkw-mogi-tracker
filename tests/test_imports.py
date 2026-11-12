import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import numpy as np
except ImportError:
    np = None

from mkw_tracker.capture import CaptureCoordinator
from mkw_tracker.imports import ImportManager, VIDEO_EXTENSIONS
from mkw_tracker.name_engines import GENERATIVE_NAME_ENGINES
from mkw_tracker.store import TrackerStore


class FakeVideo:
    def __init__(self, frames=4, opened=True, fps=1):
        self.frames, self.opened, self.fps = frames, opened, fps
        self.offset = 0
        self.released = False

    def isOpened(self):
        return self.opened

    def get(self, key):
        return self.fps if key == 5 else self.frames

    def set(self, _key, value):
        self.offset = value / 1000
        return True

    def read(self):
        return True, np.full((180, 320, 3), int(self.offset) + 100, dtype=np.uint8)

    def release(self):
        self.released = True


@unittest.skipIf(np is None, 'Video recognition requires NumPy/OpenCV')
class ImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.video_path = self.path / 'recording.mkv'
        self.video_path.write_bytes(b'video fixture')
        self.store = TrackerStore(self.path / 'db.sqlite3')
        self.lock = threading.RLock()
        self.capture = CaptureCoordinator(self.store, self.lock, self.path, None)
        self.app = SimpleNamespace(store=self.store, lock=self.lock, data_dir=self.path, capture=self.capture)
        self.reader = Mock()
        self.reader.read_prerace.side_effect = lambda frame: {'course': 'Crown City'} if frame[0, 0, 0] == 100 else None
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'finishing_results', 'rows': [
            {'position': index, 'name': f'Player {index}'} for index in range(1, 13)]}
        self.video = FakeVideo()
        self.manager = ImportManager(self.app, reader_factory=lambda _: self.reader,
                                     video_factory=lambda _: self.video)

    def tearDown(self):
        self.manager.close()
        self.capture.close()
        self.store.close()
        self.directory.cleanup()

    def wait(self):
        self.manager.thread.join(5)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.snapshot()['jobs'][0]

    def test_video_open_decode_and_release_have_one_worker_owner(self):
        owners = []

        def factory(_path):
            owners.append(threading.get_ident())
            return self.video

        for name in ('isOpened', 'get', 'set', 'read', 'release'):
            original = getattr(self.video, name)

            def tracked(*args, method=original):
                owners.append(threading.get_ident())
                return method(*args)

            setattr(self.video, name, tracked)
        self.manager.video_factory = factory
        self.manager.start(str(self.video_path))
        self.assertEqual(self.wait()['status'], 'completed')
        self.assertEqual(len(set(owners)), 1)
        self.assertNotEqual(owners[0], threading.get_ident())
        self.assertTrue(self.video.released)

    def test_real_decoder_accepts_unicode_path_and_preserves_original(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('Real video decoding requires optional OpenCV')
        path = self.path / 'recordings \u65e5\u672c\u8a9e' / 'tiny clip.avi'
        path.parent.mkdir()
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (320, 180))
        try:
            if not writer.isOpened():
                self.skipTest('MJPG encoder is unavailable')
            for _ in range(10):
                writer.write(np.zeros((180, 320, 3), dtype=np.uint8))
        finally:
            writer.release()
        original = path.read_bytes()
        self.manager.video_factory = lambda name: cv2.VideoCapture(str(name))
        self.reader.read_prerace.side_effect = None
        self.reader.read_prerace.return_value = None
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.manager.start(str(path), name_engine='tesseract')
        job = self.wait()
        self.assertEqual(job['status'], 'completed', job['error'])
        self.assertGreater(job['duration_seconds'], 0)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.assertEqual(path.read_bytes(), original)

    def test_decode_overlaps_ocr_with_only_one_sample_ahead(self):
        entered, resume, prefetched = threading.Event(), threading.Event(), threading.Event()
        original_read = self.video.read
        decoded, observed = [], []

        def decode():
            decoded.append(self.video.offset)
            if self.video.offset == .5:
                prefetched.set()
            return original_read()

        def recognize(frame):
            observed.append(int(frame[0, 0, 0]))
            if len(observed) == 1:
                entered.set()
                resume.wait(3)
            return None

        self.video.read = decode
        self.reader.read_prerace.side_effect = recognize
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.manager.start(str(self.video_path))
        self.assertTrue(entered.wait(3))
        try:
            self.assertTrue(prefetched.wait(3))
            self.assertEqual(decoded, [0, .5])
        finally:
            resume.set()
        self.assertEqual(self.wait()['status'], 'completed')
        self.assertEqual(decoded, [i * .5 for i in range(8)])
        self.assertEqual(observed, [100, 100, 101, 101, 102, 102, 103, 103])

    def test_import_engine_status_updates_while_ocr_is_loading(self):
        entered, resume = threading.Event(), threading.Event()
        self.reader.recognition_status = {'name_engine': 'qwen', 'selected_name_engine': 'qwen',
                                          'name_engine_status': 'selected', 'name_engine_error': None}

        def recognize(_frame):
            self.reader.recognition_status['name_engine_status'] = 'loading'
            entered.set()
            resume.wait(3)
            self.reader.recognition_status.update(name_engine='tesseract', name_engine_status='fallback',
                                                   name_engine_error='model unavailable')
            return None

        self.reader.read_prerace.side_effect = recognize
        self.manager.start(str(self.video_path))
        self.assertTrue(entered.wait(3))
        try:
            active = self.manager.snapshot()['active']
            self.assertEqual(active['name_engine'], 'auto')
            self.assertEqual(active['resolved_name_engine'], 'qwen')
            self.assertEqual(active['name_engine_status'], 'loading')
        finally:
            resume.set()
        final = self.wait()
        self.assertEqual(final['resolved_name_engine'], 'tesseract')
        self.assertEqual(final['selected_name_engine'], 'qwen')
        self.assertEqual(final['name_engine_status'], 'fallback')

    def test_delete_import_race_during_ocr_keeps_job_running(self):
        entered, resume = threading.Event(), threading.Event()
        original = self.reader.read.side_effect

        def recognize(_frame):
            entered.set()
            resume.wait(3)
            return self.reader.read.return_value

        self.reader.read.side_effect = recognize
        self.manager.start(str(self.video_path))
        self.assertTrue(entered.wait(3))
        try:
            with self.lock:
                race = self.store.snapshot()['races'][0]
                self.store.delete_race(race['id'])
                self.capture.prune_deleted_races()
                self.manager.prune_deleted_races()
        finally:
            resume.set()
            self.reader.read.side_effect = original
        final = self.wait()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['observed_results'], 0)
        self.assertEqual(final['observed_races'], 0)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.assertEqual(self.capture.state['pending'], [])

    def test_import_ownership_and_counters_do_not_load_history(self):
        counts = []
        coordinator = SimpleNamespace(state={'armed': True, 'race_id': None})

        def observe(_frame, recording_id, offset_ms):
            with self.lock:
                if offset_ms == 0:
                    self.store.create_race('observed', 'Crown City', recording_id=recording_id)
                elif offset_ms == 500:
                    self.store.delete_race('observed')
                elif offset_ms == 1000:
                    self.store.create_race('replacement', 'Rainbow Road', recording_id=recording_id)

        coordinator.observe = observe
        self.manager.coordinator_factory = lambda *args, **kwargs: coordinator
        original_count = self.store.count_recording_races

        def count(recording_id):
            value = original_count(recording_id)
            counts.append(value)
            return value

        with patch.object(self.store, 'snapshot', side_effect=AssertionError('full history read')), \
                patch.object(self.store, 'count_recording_races', side_effect=count):
            job = self.manager.start(str(self.video_path))
            final = self.wait()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(counts, [1, 0, 1, 1, 1, 1, 1, 1])
        self.assertEqual(final['observed_races'], 1)
        self.assertEqual(job['session_id'], self.store.active_session()['id'])

    def test_import_preserves_live_latch_pending_and_original_offsets(self):
        self.capture.state.update(armed=False, course='Live course', race_id=None)
        self.capture.state['pending'].append({'id': 'existing'})
        self.capture._save()
        before = dict(self.capture.state)
        job = self.manager.start(str(self.video_path))
        final = self.wait()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['observed_races'], 1)
        self.assertEqual(final['observed_results'], 1)
        self.assertEqual(final['percent'], 100)
        self.assertTrue(self.video.released)
        self.reader.close.assert_called()
        snapshot = self.store.snapshot()
        race = snapshot['races'][0]
        self.assertEqual(race['offset_ms'], 1000)
        self.assertEqual(race['recording_id'], job['recording_id'])
        self.assertEqual(race['status'], 'pending')
        self.assertEqual(self.capture.state['course'], before['course'])
        self.assertFalse(self.capture.state['armed'])
        persisted = json.loads((self.path / 'capture.json').read_text())
        self.assertEqual([row['id'] for row in persisted['pending']][:1], ['existing'])
        self.assertEqual(len(persisted['pending']), 2)
        self.assertTrue((self.path / 'evidence' / (persisted['pending'][1]['id'] + '.png')).is_file())

    def test_import_session_and_folder_are_fixed_across_reset(self):
        entered, resume = threading.Event(), threading.Event()
        original = self.reader.read_prerace.side_effect

        def blocked(frame):
            if frame[0, 0, 0] == 100:
                entered.set()
                resume.wait(3)
            return original(frame)

        self.reader.read_prerace.side_effect = blocked
        folder = self.store.create_folder('Imported room')
        job = self.manager.start(str(self.video_path), folder['id'])
        self.assertTrue(entered.wait(3))
        with self.lock:
            selected = self.store.snapshot()['session']['id']
            self.assertNotEqual(job['session_id'], selected)
            self.store.create_session('Another session', selected)
        resume.set()
        self.assertEqual(self.wait()['status'], 'completed')
        race = self.store.snapshot()['races'][0]
        self.assertEqual(race['session_id'], job['session_id'])
        self.assertEqual(race['folder_id'], folder['id'])
        self.assertEqual(self.store.snapshot()['players'], [])

    def test_cancel_interrupts_reader_and_keeps_partial_results(self):
        entered, resume = threading.Event(), threading.Event()
        original = self.reader.read_prerace.side_effect

        def blocked(frame):
            if frame[0, 0, 0] == 102:
                entered.set()
                resume.wait(3)
            return original(frame)

        self.reader.read_prerace.side_effect = blocked
        self.reader.close.side_effect = resume.set
        job = self.manager.start(str(self.video_path))
        self.assertTrue(entered.wait(3))
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.manager.start(str(self.video_path))
        self.manager.cancel(job['id'])
        self.assertEqual(self.wait()['status'], 'cancelled')
        self.assertEqual(len(self.capture.state['pending']), 1)
        self.assertTrue(self.video.released)

    def test_dismissed_cards_are_not_resurrected_and_imports_have_new_recordings(self):
        entered, resume = threading.Event(), threading.Event()
        original = self.reader.read_prerace.side_effect

        def blocked(frame):
            if frame[0, 0, 0] == 102:
                entered.set()
                resume.wait(3)
            return original(frame)

        self.reader.read_prerace.side_effect = blocked
        first = self.manager.start(str(self.video_path))
        self.assertTrue(entered.wait(3))
        with self.lock:
            self.capture.dismiss(self.capture.state['pending'][0]['id'])
        resume.set()
        self.wait()
        self.assertEqual(self.capture.state['pending'], [])
        self.assertEqual(json.loads((self.path / 'capture.json').read_text())['pending'], [])
        second = self.manager.start(str(self.video_path))
        self.wait()
        self.assertNotEqual(first['recording_id'], second['recording_id'])
        self.assertEqual(len(self.capture.state['pending']), 1)

    def test_mid_race_import_has_recording_reference_without_inventing_intro(self):
        self.reader.read_prerace.side_effect = None
        self.reader.read_prerace.return_value = None
        job = self.manager.start(str(self.video_path))
        self.assertEqual(self.wait()['status'], 'completed')
        race = self.store.snapshot()['races'][0]
        self.assertEqual(race['recording_id'], job['recording_id'])
        self.assertIsNone(race['offset_ms'])
        self.assertEqual(len(self.capture.state['pending']), 1)

    def test_decode_errors_preserve_prior_review_and_release_resources(self):
        original = self.video.read

        def read():
            return (False, None) if self.video.offset == 2 else original()

        self.video.read = read
        self.manager.start(str(self.video_path))
        job = self.wait()
        self.assertEqual(job['status'], 'failed')
        self.assertIn('decoded at 2.0', job['error'])
        self.assertTrue(self.video.released)
        self.assertEqual(len(self.capture.state['pending']), 1)

    def test_invalid_inputs_and_live_capture_rejected(self):
        for path in ['https://example.com/a.mp4', '/dev/null', str(self.path), '', '   ', 123,
                     str(self.path / 'missing.mp4')]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.start(path)
        for stride in [True, 0, .09, 11, float('nan'), float('inf')]:
            with self.subTest(stride=stride), self.assertRaises(ValueError):
                self.manager.start(str(self.video_path), stride=stride)
        for engine in ['cloud', None, []]:
            with self.assertRaises(ValueError):
                self.manager.start(str(self.video_path), name_engine=engine)
        with self.assertRaises(ValueError):
            self.manager.start(str(self.video_path), folder_id='missing')
        self.capture.thread = Mock()
        self.capture.thread.is_alive.return_value = True
        with self.assertRaisesRegex(ValueError, 'live capture'):
            self.manager.start(str(self.video_path))
        self.capture.thread = None

    def test_each_qwen_profile_reaches_import_reader_without_scoring(self):
        factory = Mock(return_value=self.reader)
        self.manager.reader_factory = factory
        for engine in GENERATIVE_NAME_ENGINES:
            with self.subTest(engine=engine):
                self.video = FakeVideo()
                self.manager.start(str(self.video_path), name_engine=engine)
                job = self.wait()
                self.assertEqual(job['status'], 'completed')
                self.assertEqual(job['name_engine'], engine)
                factory.assert_called_with(engine)
                self.assertEqual(self.store.snapshot()['room']['points'], 0)

    def test_failed_cleanup_retains_resources_and_blocks_admission(self):
        for resource in ('reader', 'video'):
            with self.subTest(resource=resource):
                failing = self.reader.close if resource == 'reader' else Mock(side_effect=RuntimeError('busy'))
                if resource == 'reader':
                    failing.side_effect = RuntimeError('busy')
                else:
                    self.video.release = failing
                self.manager.start(str(self.video_path))
                self.assertEqual(self.wait()['status'], 'failed')
                self.assertTrue(self.manager.cleanup_failed)
                self.assertIs(self.manager.reader, self.reader)
                self.assertIs(self.manager.video, self.video)
                with self.assertRaisesRegex(ValueError, 'restart'):
                    self.manager.start(str(self.video_path))
                with self.assertRaisesRegex(RuntimeError, 'restart'):
                    self.manager.close()
                # Simulate a restart, not an unsafe in-process latch reset.
                self.reader.close.side_effect = None
                self.video.release = lambda: None
                self.manager = ImportManager(self.app, reader_factory=lambda _: self.reader,
                                             video_factory=lambda _: self.video)

    def test_upload_reservation_blocks_import_except_explicit_handoff(self):
        self.app.active_uploads = {self.video_path}
        with self.assertRaisesRegex(ValueError, 'upload'):
            self.manager.start(str(self.video_path))
        with self.assertRaisesRegex(ValueError, 'upload'):
            self.manager.start(str(self.video_path), upload_path=self.path / 'other.mp4')
        self.manager.start(str(self.video_path), upload_path=self.video_path)
        self.assertEqual(self.wait()['status'], 'completed')

    def test_terminal_status_waits_for_reader_and_video_cleanup(self):
        closing, release = threading.Event(), threading.Event()

        def close_reader():
            closing.set()
            release.wait(3)

        self.reader.close.side_effect = close_reader
        job = self.manager.start(str(self.video_path))
        self.assertTrue(closing.wait(3))
        try:
            snapshot = self.manager.snapshot()
            self.assertEqual(snapshot['active']['id'], job['id'])
            self.assertEqual(snapshot['jobs'][0]['status'], 'running')
            self.assertIsNone(snapshot['jobs'][0]['finished_at'])
            self.assertFalse(self.video.released)
            persisted = json.loads((self.path / 'imports.json').read_text())
            self.assertEqual(persisted[0]['status'], 'running')
            self.assertIsNone(persisted[0]['finished_at'])
        finally:
            release.set()
        finished = self.wait()
        self.assertEqual(finished['status'], 'completed')
        self.assertIsNotNone(finished['finished_at'])
        self.assertIsNone(self.manager.snapshot()['active'])
        self.assertTrue(self.video.released)

    def test_supported_extensions_and_long_recording_names(self):
        self.assertTrue({'.mts', '.m2ts', '.mpeg', '.mpg'} <= VIDEO_EXTENSIONS)
        for extension in VIDEO_EXTENSIONS:
            path = self.path / ('x' * 210 + extension)
            path.write_bytes(b'fixture')
            job = self.manager.start(str(path), stride=.1)
            self.assertEqual(self.wait()['status'], 'completed')
            self.assertEqual(job['path'], str(path))
            recording = next(row for row in self.store.snapshot()['recordings']
                             if row['id'] == job['recording_id'])
            self.assertLessEqual(len(recording['label']), 200)

    def test_bad_video_and_invalid_duration_fail_cleanly(self):
        self.video.opened = False
        self.manager.start(str(self.video_path))
        self.assertEqual(self.wait()['status'], 'failed')
        self.assertTrue(self.video.released)
        self.video.opened, self.video.fps = True, float('nan')
        self.manager.start(str(self.video_path))
        self.assertIn('duration', self.wait()['error'])

    def test_interrupted_jobs_persist_without_silent_resume(self):
        self.manager.start(str(self.video_path))
        self.wait()
        self.manager.close()
        jobs = json.loads((self.path / 'imports.json').read_text())
        jobs[0]['status'] = 'running'
        (self.path / 'imports.json').write_text(json.dumps(jobs))
        self.manager = ImportManager(self.app)
        self.assertIsNone(self.manager.snapshot()['active'])
        self.assertEqual(self.manager.snapshot()['jobs'][0]['status'], 'interrupted')
        self.assertIsNone(self.manager.thread)
        with self.assertRaises(KeyError):
            self.manager.cancel('missing')
