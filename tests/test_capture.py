import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
except ImportError:
    np = None

from mkw_tracker.capture import CaptureCoordinator, RecordingClock
from mkw_tracker.store import TrackerStore


def status(duration=0, active=True, paused=False):
    return {'outputActive': active, 'outputPaused': paused, 'outputDuration': duration}


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.store = TrackerStore(':memory:')
        self.clock = RecordingClock(self.store)

    def tearDown(self):
        self.store.close()

    def test_start_pause_reset_and_recording_restart(self):
        self.assertEqual(self.clock.update(status(active=False)), (None, None))
        recording, offset = self.clock.update(status(1234.5))
        self.assertEqual(offset, 1234)
        self.store.reset(self.store.snapshot()['session']['id'])
        self.assertEqual(self.clock.update(status(2000)), (recording, 2000))
        self.assertEqual(self.clock.update(status(2000, paused=True)), (recording, None))
        self.assertEqual(self.clock.update(status(3000)), (recording, 3000))
        self.clock.update(status(active=False))
        second, offset = self.clock.update(status(100))
        self.assertNotEqual(second, recording)
        self.assertEqual(offset, 100)

    def test_reconnect_and_file_split_require_review(self):
        first, offset = self.clock.update(status(1000))
        self.assertIsNone(offset)
        self.clock.update(status(active=False))
        second, _ = self.clock.update(status(10))
        self.clock.disconnected()
        self.assertEqual(self.clock.update(status(2000)), (second, None))
        third, offset = self.clock.update(status(3000), [{'eventType': 'RecordFileChanged'}])
        self.assertNotEqual(third, first)
        self.assertIsNone(offset)
        self.assertIsNone(self.clock.update(status(4000))[1])

    def test_started_event_catches_restart_between_polls(self):
        first, _ = self.clock.update(status(100))
        second, offset = self.clock.update(status(200), [{'eventType': 'RecordStateChanged', 'eventData': {'outputState': 'OBS_WEBSOCKET_OUTPUT_STARTED'}}])
        self.assertNotEqual(first, second)
        self.assertEqual(offset, 200)

    def test_recording_rate_stays_with_its_own_file(self):
        first, _ = self.clock.update({**status(100), 'recordingFps': '60000/1001'})
        second, _ = self.clock.update(status(200), [{'eventType': 'RecordFileChanged'}])
        self.assertEqual(self.store.get_recording(first)['fps'], '60000/1001')
        self.assertIsNone(self.store.get_recording(second)['fps'])

    def test_invalid_clocks_cannot_create_offsets(self):
        for duration in [True, -1, float('nan'), '100']:
            self.assertIsNone(self.clock.update(status(duration))[1])

    def test_resume_with_unverified_clock_does_not_claim_recording_still_paused(self):
        self.clock.update(status(100, paused=True))
        self.assertIn('paused', self.clock.message)
        self.clock.update(status(200))
        self.assertNotIn('paused', self.clock.message)
        self.assertIn('verify', self.clock.message)


@unittest.skipIf(np is None, 'Capture requires NumPy/OpenCV')
class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = self.directory.name + '/db.sqlite3'
        self.store = TrackerStore(self.database)
        self.reader = Mock()
        self.reader.read_prerace.return_value = None
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        self.frame = np.full((180, 320, 3), 100, dtype=np.uint8)

    def tearDown(self):
        self.capture.close()
        self.store.close()
        self.directory.cleanup()

    def prerace(self, course='Course'):
        self.reader.read_prerace.return_value = {'course': course, 'confidence': .9}
        self.capture.observe(self.frame)
        self.reader.read_prerace.return_value = None

    def result(self):
        self.reader.read.return_value = {'phase': 'finishing_results', 'rows': [
            {'position': i + 1, 'name': f'Player {i}', 'confidence': .8} for i in range(12)], 'reasons': []}
        self.capture.observe(self.frame)

    def test_delete_race_during_result_ocr_discards_in_flight_frame(self):
        self.prerace()
        self.capture.observe(self.frame)
        race = self.store.snapshot()['races'][0]
        entered, resume = threading.Event(), threading.Event()
        rows = [{'position': i, 'name': str(i)} for i in range(1, 13)]

        def recognize(_frame):
            entered.set()
            resume.wait(3)
            return {'phase': 'finishing_results', 'rows': rows}

        self.reader.read.side_effect = recognize
        worker = threading.Thread(target=self.capture.observe, args=(self.frame,))
        worker.start()
        self.assertTrue(entered.wait(3))
        try:
            with self.capture.lock:
                self.store.delete_race(race['id'])
                self.capture.prune_deleted_races()
        finally:
            resume.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.store.snapshot()['races'], [])
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.capture.observe(self.frame)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.prerace('Next race')
        self.capture.observe(self.frame)
        self.assertEqual(self.store.snapshot()['races'][0]['course'], 'Next race')

    def test_capture_snapshot_exposes_selected_name_engine(self):
        self.reader.recognition_status = {'name_engine': 'paddle', 'name_engine_status': 'selected'}
        self.assertEqual(self.capture.snapshot()['name_engine'], 'paddle')
        self.assertEqual(self.capture.snapshot()['name_engine_status'], 'selected')

    def test_capture_and_review_use_no_full_history_snapshots(self):
        with patch.object(self.store, 'snapshot', side_effect=AssertionError('full history read')):
            self.capture.close()
            self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
            self.prerace()
            self.capture.observe(self.frame)
            self.reader.read.return_value = {'phase': 'unknown', 'rows': [], 'suspected_results': True}
            self.capture.observe(self.frame)
            self.capture.prune_deleted_races()
            self.result()
            candidate = self.capture.snapshot()['pending'][0]
            result = self.capture.review(candidate['id'], [{'name': str(i)} for i in range(12)])
        self.assertEqual(result['status'], 'scored')
        self.assertEqual(sum(row['points'] for row in result['placements']), 82)

    def test_pruning_checks_only_referenced_races_and_sessions(self):
        self.prerace()
        self.capture.observe(self.frame)
        self.result()
        race_id = self.capture.state['race_id']
        session_id = self.capture.state['session_id']
        with patch.object(self.store, 'snapshot', side_effect=AssertionError('full history read')), \
             patch.object(self.store, 'race_exists', wraps=self.store.race_exists) as race_exists, \
             patch.object(self.store, 'session_exists', wraps=self.store.session_exists) as session_exists:
            self.capture.prune_deleted_races()
        self.assertEqual({call.args[0] for call in race_exists.call_args_list}, {race_id})
        session_exists.assert_called_once_with(session_id)
        self.assertEqual(len(self.capture.snapshot()['pending']), 1)

    def test_pending_capture_does_not_score_and_hold_survives_restart_reset(self):
        self.prerace()
        self.capture.observe(self.frame)
        self.result()
        pending = self.capture.snapshot()['pending']
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.store.snapshot()['players'], [])
        self.store.reset(self.store.snapshot()['session']['id'])
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        self.result()
        self.assertEqual(len(self.capture.snapshot()['pending']), 1)
        self.capture.review(pending[0]['id'], [{'name': str(i)} for i in range(12)])
        self.assertEqual(sum(p['points'] for p in self.store.snapshot()['players']), 0)
        self.assertEqual(len(self.capture.snapshot()['pending']), 0)
        self.result()
        self.assertEqual(len(self.capture.snapshot()['pending']), 0)

    def test_new_prerace_rearms_even_same_course_and_dismiss_does_not(self):
        self.result()
        first = self.capture.snapshot()['pending'][0]
        self.capture.dismiss(first['id'])
        self.result()
        self.assertEqual(len(self.capture.snapshot()['pending']), 0)
        self.prerace()
        self.result()
        self.assertEqual(len(self.capture.snapshot()['pending']), 1)

    def test_partial_results_and_roster_ocr_miss_cannot_create_bad_race(self):
        self.prerace()
        self.reader.is_prerace_layout.return_value = True
        self.capture.observe(self.frame)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'finishing_results', 'rows': [{'position': 1, 'name': 'One'}]}
        self.capture.observe(self.frame)
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.assertEqual(self.store.snapshot()['races'][0]['status'], 'pending')

    def test_same_course_after_missed_result_starts_a_distinct_race(self):
        self.prerace('Same course')
        self.capture.observe(self.frame)
        first_id = self.store.snapshot()['races'][0]['id']
        self.prerace('Same course')
        self.prerace('Same course')  # Held roster must not create extra races.
        self.capture.observe(self.frame)
        self.result()
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 2)
        self.assertNotEqual(races[1]['id'], first_id)
        self.assertEqual(self.capture.snapshot()['pending'][0]['race_id'], races[1]['id'])
        self.assertEqual(races[0]['status'], 'pending')

    def test_false_roster_during_gameplay_does_not_split_course_and_results(self):
        from pathlib import Path

        import cv2

        from mkw_tracker.recognition import ResultReader

        self.prerace('Great ? Block Ruins')
        self.capture.observe(self.frame)
        race_id = self.capture.state['race_id']
        fixture = Path(__file__).parent / 'fixtures/mogi-2026-10-19/gameplay-ruins.webp'
        if not fixture.is_file():
            self.skipTest('Optional gameplay image is not distributed in this project')
        frame = cv2.imread(str(fixture))
        self.reader.is_prerace_layout.side_effect = ResultReader().is_prerace_layout
        self.capture.observe(frame)
        self.assertEqual(self.capture.state['race_id'], race_id)
        self.reader.is_prerace_layout.side_effect = None
        self.result()
        self.assertEqual(len(self.store.snapshot()['races']), 1)
        self.assertEqual(self.capture.snapshot()['pending'][0]['race_id'], race_id)
        self.assertEqual(self.capture.snapshot()['pending'][0]['course'], 'Great ? Block Ruins')
        self.assertEqual(self.store.snapshot()['players'], [])

    def test_review_validation_is_atomic_and_queue_retained_on_error(self):
        self.result()
        candidate = self.capture.snapshot()['pending'][0]
        with self.assertRaises(KeyError):
            self.capture.review(candidate['id'], [{'name': str(i)} for i in range(11)] + [{'player_id': 'missing'}])
        self.assertEqual(len(self.capture.snapshot()['pending']), 1)
        self.assertEqual(self.store.snapshot()['players'], [])
        self.capture.review(candidate['id'], [{'name': str(i)} for i in range(12)])
        self.assertEqual(sum(p['points'] for p in self.store.snapshot()['players']), 82)

    def test_course_and_results_evidence_survive_confirmation_and_restart(self):
        self.prerace('Crown City')
        self.capture.observe(self.frame)
        self.result()
        race = self.store.snapshot()['races'][0]
        self.assertEqual({item['kind'] for item in race['evidence']}, {'prerace', 'results'})
        for proof in race['evidence']:
            from pathlib import Path
            self.assertTrue((Path(self.directory.name) / proof['url'].lstrip('/')).is_file())
        self.capture.review(self.capture.snapshot()['pending'][0]['id'], [{'name': str(i)} for i in range(12)])
        self.capture.close()
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.assertEqual(self.store.snapshot()['races'][0]['evidence'], race['evidence'])

    def test_suspected_results_keep_one_screenshot_without_queueing_or_scoring(self):
        self.prerace('SNES Ghost Valley 2')
        self.capture.observe(self.frame)
        self.reader.read.return_value = {'phase': 'unknown', 'rows': [], 'suspected_results': True}
        self.capture.observe(self.frame, offset_ms=1234)
        race = self.store.snapshot()['races'][0]
        suspected = [item for item in race['evidence'] if item['kind'] == 'suspected_results']
        self.assertEqual(len(suspected), 1)
        self.assertEqual(suspected[0]['offset_ms'], 1234)
        self.assertEqual(race['status'], 'pending')
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.assertEqual(self.store.snapshot()['players'], [])
        self.assertTrue(self.capture.state['armed'])
        self.capture.close()
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        self.capture.observe(self.frame, offset_ms=2000)
        self.assertEqual(self.store.snapshot()['races'][0]['evidence'], race['evidence'])
        self.result()
        race = self.store.snapshot()['races'][0]
        self.assertEqual(race['evidence'][0]['kind'], 'results')
        self.assertEqual(len(self.capture.snapshot()['pending']), 1)

    def test_suspected_results_do_not_create_unknown_races(self):
        self.reader.read.return_value = {'phase': 'unknown', 'rows': [], 'suspected_results': True}
        self.capture.observe(self.frame)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.assertEqual(list(self.capture.evidence_dir.iterdir()), [])

    def test_stop_during_suspected_evidence_write_removes_new_image(self):
        import cv2

        self.prerace()
        self.capture.observe(self.frame)
        before = set(self.capture.evidence_dir.iterdir())
        self.reader.read.return_value = {'phase': 'unknown', 'rows': [], 'suspected_results': True}
        imwrite = cv2.imwrite

        def stop_after_write(*args):
            saved = imwrite(*args)
            self.capture.stop_event.set()
            return saved

        with patch('cv2.imwrite', side_effect=stop_after_write):
            self.capture.observe(self.frame)
        self.assertEqual(set(self.capture.evidence_dir.iterdir()), before)
        self.assertEqual([item['kind'] for item in self.store.snapshot()['races'][0]['evidence']], ['prerace'])

    def test_suspected_results_do_not_attach_to_manually_scored_race(self):
        self.prerace()
        self.capture.observe(self.frame)
        race = self.store.snapshot()['races'][0]
        self.store.review_result(race['id'], [{'name': str(i)} for i in range(12)])
        self.reader.read.return_value = {'phase': 'unknown', 'rows': [], 'suspected_results': True}
        self.capture.observe(self.frame)
        self.assertEqual([item['kind'] for item in self.store.snapshot()['races'][0]['evidence']], ['prerace'])

    def test_suspected_results_after_deletion_do_not_leave_orphan_image(self):
        self.prerace()
        self.capture.observe(self.frame)
        race = self.store.snapshot()['races'][0]
        before = set(self.capture.evidence_dir.iterdir())

        def read(_frame):
            with self.capture.lock:
                self.store.delete_race(race['id'])
                self.capture.prune_deleted_races()
            return {'phase': 'unknown', 'rows': [], 'suspected_results': True}

        self.reader.read.side_effect = read
        self.capture.observe(self.frame)
        self.assertEqual(set(self.capture.evidence_dir.iterdir()), before)
        self.assertEqual(self.store.snapshot()['races'], [])

    def test_pending_legacy_evidence_backfills_without_inventing_old_mappings(self):
        self.result()
        original = self.store.snapshot()['races'][0]['evidence']
        with self.store._transaction():
            self.store._write('DELETE FROM race_evidence')
        self.capture.close()
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        restored = self.store.snapshot()['races'][0]['evidence']
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['url'], original[0]['url'])

    def test_startup_prunes_deleted_session_reviews_and_does_not_recapture_held_result(self):
        self.result()
        old = self.store.snapshot()['session']
        self.store.create_session('Next', old['id'])
        self.store.delete_folder(old['folder_id'])
        self.capture.close()
        self.capture = CaptureCoordinator(self.store, threading.RLock(), self.directory.name, self.reader)
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.result()
        self.assertEqual(self.store.snapshot()['races'], [])

    def live_client(self):
        client = Mock()
        client.record_status.return_value = status(200)
        client.screenshot.return_value = self.frame
        client.drain_events.return_value = []
        return client

    def test_ocr_does_not_block_reset_and_keeps_original_session(self):
        for known_race in (False, True):
            with self.subTest(known_race=known_race):
                if known_race:
                    recording = self.store.create_recording("Known recording")
                    self.prerace()
                    self.capture.observe(self.frame, recording["id"], 1200)
                old_session = self.store.snapshot()['session']['id']
                entered, release = threading.Event(), threading.Event()

                def read(_frame):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test did not release OCR')
                    return {'phase': 'finishing_results', 'rows': [
                        {'position': i + 1, 'name': str(i)} for i in range(12)]}

                self.reader.read.side_effect = read
                with patch.object(self.capture.stop_event, 'wait',
                                  side_effect=lambda _delay: self.capture.stop_event.set()):
                    self.capture.start(self.live_client(), 'Capture card')
                    try:
                        self.assertTrue(entered.wait(3))
                        acquired = self.capture.lock.acquire(timeout=.5)
                        try:
                            self.assertTrue(acquired, 'OCR held the application lock')
                            self.capture.snapshot()
                            self.store.reset(old_session)
                        finally:
                            if acquired:
                                self.capture.lock.release()
                    finally:
                        release.set()
                        self.capture.thread.join(3)
                self.assertFalse(self.capture.thread.is_alive())
                candidate = self.capture.snapshot()['pending'][-1]
                race = next(r for r in self.store.snapshot()['races']
                            if r['id'] == candidate['race_id'])
                self.assertEqual(race['session_id'], old_session)
                if known_race:
                    self.assertEqual((race['recording_id'], race['offset_ms']),
                                     (recording['id'], 1200))
                self.capture.review(candidate['id'], [{'name': str(i)} for i in range(12)])
                self.assertEqual(self.store.snapshot()['players'], [])
                self.capture.close()
                self.capture = CaptureCoordinator(self.store, threading.RLock(),
                                                  self.directory.name, self.reader)
                self.capture.state.update(armed=True, course=None, race_id=None)
                self.reader.read.side_effect = None

    def test_reset_during_roster_ocr_keeps_intro_in_original_session(self):
        old_session = self.store.snapshot()['session']
        entered, release = threading.Event(), threading.Event()

        def read_prerace(_frame):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test did not release roster OCR')
            return {'course': 'Course'}

        self.reader.read_prerace.side_effect = read_prerace
        with patch.object(self.capture.stop_event, 'wait',
                          side_effect=lambda _delay: self.capture.stop_event.set()):
            self.capture.start(self.live_client(), 'Capture card')
            try:
                self.assertTrue(entered.wait(3))
                acquired = self.capture.lock.acquire(timeout=.5)
                try:
                    self.assertTrue(acquired, 'Roster OCR held the application lock')
                    self.store.reset(old_session['id'])
                finally:
                    if acquired:
                        self.capture.lock.release()
            finally:
                release.set()
                self.capture.thread.join(3)
        self.reader.read_prerace.side_effect = None
        self.capture.stop_event.clear()
        self.capture.observe(self.frame)
        race = self.store.snapshot()['races'][0]
        self.assertEqual(race['session_id'], old_session['id'])
        self.assertEqual(race['folder_id'], old_session['folder_id'])

    def test_stop_during_ocr_discards_result(self):
        entered, release = threading.Event(), threading.Event()

        def read(_frame):
            entered.set()
            release.wait(3)
            return {'phase': 'finishing_results', 'rows': [
                {'position': i + 1, 'name': str(i)} for i in range(12)]}

        self.reader.read.side_effect = read
        self.capture.start(self.live_client(), 'Capture card')
        try:
            self.assertTrue(entered.wait(3))
            self.capture.stop_event.set()
        finally:
            release.set()
            self.capture.thread.join(3)
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.assertEqual(self.store.snapshot()['races'], [])

    def test_capture_interval_subtracts_processing_time_without_catchup(self):
        for elapsed, expected_wait in ((.2, .3), (.7, 0)):
            with self.subTest(elapsed=elapsed):
                self.capture.stop_event.clear()
                self.capture.observe = Mock()
                with patch('mkw_tracker.capture.time.monotonic', side_effect=[10, 10 + .1, 10 + elapsed]), \
                     patch.object(self.capture.stop_event, 'wait',
                                  side_effect=lambda _delay: self.capture.stop_event.set()) as wait:
                    self.capture.start(self.live_client(), 'Capture card', interval=.5)
                    self.capture.thread.join(3)
                wait.assert_called_once()
                self.assertAlmostEqual(wait.call_args.args[0], expected_wait)
                timing = self.capture.snapshot()['sample_timing']
                self.assertEqual(timing['requested_interval_ms'], 500)
                self.assertEqual(timing['acquisition_ms'], 100)
                self.assertAlmostEqual(timing['recognition_ms'], (elapsed - .1) * 1000)
                self.assertAlmostEqual(timing['cycle_ms'], max(.5, elapsed) * 1000)

    def test_capture_interval_rejects_invalid_values(self):
        for value in (0, -1, True, float('nan'), float('inf'), '0.5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.capture.start(self.live_client(), 'Capture card', interval=value)

    def test_default_live_interval_is_quarter_second_without_catchup(self):
        for elapsed, expected_wait in ((.1, .15), (.4, 0)):
            with self.subTest(elapsed=elapsed):
                self.capture.stop_event.clear()
                self.capture.observe = Mock()
                with patch('mkw_tracker.capture.time.monotonic', side_effect=[10, 10.05, 10 + elapsed]), \
                     patch.object(self.capture.stop_event, 'wait',
                                  side_effect=lambda _delay: self.capture.stop_event.set()) as wait:
                    self.capture.start(self.live_client(), 'Capture card')
                    self.capture.thread.join(3)
                self.assertFalse(self.capture.thread.is_alive())
                wait.assert_called_once()
                self.assertAlmostEqual(wait.call_args.args[0], expected_wait)
                timing = self.capture.snapshot()['sample_timing']
                self.assertEqual(timing['requested_interval_ms'], 250)
                self.assertAlmostEqual(timing['cycle_ms'], max(.25, elapsed) * 1000)

    def test_recording_restart_across_screenshot_invalidates_frame_offset(self):
        client = Mock()
        client.record_status.side_effect = [status(100), status(200)]
        client.screenshot.return_value = self.frame
        client.drain_events.return_value = [{
            'eventType': 'RecordStateChanged',
            'eventData': {'outputState': 'OBS_WEBSOCKET_OUTPUT_STARTED'}}]
        self.capture.observe = Mock(side_effect=lambda *_args: self.capture.stop_event.set())
        self.capture.start(client, 'Capture card', interval=.5)
        self.capture.thread.join(timeout=3)
        self.assertFalse(self.capture.thread.is_alive())
        self.capture.observe.assert_called_once()
        _, recording_id, offset = self.capture.observe.call_args.args
        self.assertIsNotNone(recording_id)
        self.assertIsNone(offset)


if __name__ == '__main__':
    unittest.main()
