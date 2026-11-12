"""WW temporal/ownership checks without hardware or name models."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from mkw_tracker.ww_capture import WWCaptureCoordinator
from mkw_tracker.ww_store import WWStore

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


class Reader:
    prerace = None
    result = None
    table = False
    prerace_layout = False

    def read_prerace(self, frame):
        return self.prerace

    def is_prerace_layout(self, frame):
        return bool(self.prerace) or self.prerace_layout

    def read_results(self, frame):
        return self.result

    def is_results_layout(self, frame):
        return self.table


@unittest.skipIf(cv2 is None, 'optional capture dependencies are not installed')
class WWCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = WWStore(self.root / 'ww.sqlite3')
        self.lock = threading.RLock()
        self.reader = Reader()
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader)
        self.frame = np.full((100, 100, 3), 80, dtype=np.uint8)
        self.recording = self.store.create_recording('Edited file')['id']
        self.offset = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def observe(self, count=1):
        for _ in range(count):
            self.capture.observe(self.frame, self.recording, self.offset)
            self.offset += 500

    def intro(self, course='Koopa Troopa Beach', size=None):
        self.reader.prerace = {'course': course, 'lobby_size': size}
        self.reader.result = None
        self.observe(2)
        self.reader.prerace = None
        self.observe()

    def result(self, position=18, vr=8759, delta=None, phase='final', count=3):
        self.reader.result = {'position': position, 'vr': vr, 'delta': delta,
                              'phase': phase, 'lobby_size': None}
        self.observe(count)

    def test_complete_change_is_automatic_and_duplicate_pages_do_not_rescore(self):
        self.intro(size=23)
        self.result(vr=8805, delta=-46, phase='change', count=2)
        self.assertIsNone(self.store.snapshot()['ww']['current_vr'])
        self.result(count=1)
        snapshot = self.store.snapshot()
        race = snapshot['races'][0]
        self.assertEqual(race['ww_result']['delta'], -46)
        self.assertEqual(race['ww_result']['vr_before'], 8805)
        self.assertEqual(race['ww_result']['lobby_size'], 23)
        self.assertEqual(race['revision'], 1)
        self.assertEqual(race['offset_ms'], 1000)
        self.assertEqual({entry['kind'] for entry in race['evidence']}, {'prerace', 'results'})
        self.reader.result, self.reader.table = None, True
        self.observe(12)  # Local row scrolls out of sight, then returns pinned.
        self.result(count=10)
        self.assertEqual(len(self.store.snapshot()['races']), 1)
        self.assertEqual(self.store.snapshot()['races'][0]['revision'], 1)
        self.assertEqual(self.capture.snapshot()['pending'], [])

    def test_import_cut_final_saves_known_values_without_prior_race_inference(self):
        session = self.store.snapshot()['session']
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader,
                                             session_id=session['id'], folder_id=session['folder_id'])
        self.result()
        first = self.store.snapshot()['races'][0]
        self.assertIsNone(first['offset_ms'])
        self.assertIsNone(first['ww_result']['delta'])
        self.assertIsNone(first['ww_result']['vr_before'])
        self.reader.result = None
        self.observe(5)
        self.result(position=16, vr=8736)
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot['races']), 2)
        self.assertIsNone(snapshot['races'][1]['ww_result']['delta'])
        self.assertFalse(snapshot['races'][1]['ww_result']['derive_from_previous'])
        self.assertEqual(snapshot['ww']['current_vr'], 8736)
        self.assertIsNone(snapshot['ww']['total_change'])

    def test_live_final_changes_use_previous_result_with_positive_negative_and_zero_changes(self):
        for final in (8000, 8050, 8030, 8030):
            self.intro()
            self.result(position=5, vr=final)
        results = [race['ww_result'] for race in self.store.snapshot()['races']]
        self.assertEqual([r['vr_before'] for r in results], [None, 8000, 8050, 8030])
        self.assertEqual([r['delta'] for r in results], [None, 50, -20, 0])
        self.assertTrue(all(r['derive_from_previous'] for r in results))
        self.assertTrue(all(r['source'] == 'capture' for r in results))

    def test_live_owner_baseline_does_not_follow_selected_session(self):
        owner = self.store.snapshot()['session']
        self.result(vr=8000)
        self.intro()
        self.store.create_session('Another session', owner['id'])
        self.store.create_race('other', 'Rainbow Road')
        self.store.set_ww_result('other', position=1, vr_after=9000)
        self.result(vr=8050)
        owned = [r for r in self.store.snapshot()['races'] if r['session_id'] == owner['id']]
        self.assertEqual(owned[-1]['ww_result']['delta'], 50)
        self.assertEqual(self.store.snapshot()['ww']['current_vr'], 9000)

    def test_two_clear_final_samples_before_a_cut_are_sufficient(self):
        self.result(position=21, vr=8566, count=2)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 1)
        self.assertEqual(races[0]['ww_result']['vr_after'], 8566)
        self.assertIsNone(races[0]['ww_result']['delta'])

    def test_brief_final_is_corroborated_by_same_window_change_targets(self):
        self.result(position=12, vr=8754, delta=-31, phase='change', count=1)
        self.result(position=12, vr=8750, delta=-27, phase='change', count=1)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.result(position=12, vr=8723, count=1)
        result = self.store.snapshot()['races'][0]['ww_result']
        self.assertEqual(result['vr_after'], 8723)
        # Only the final total is corroborated, not an original before/delta.
        self.assertIsNone(result['vr_before'])
        self.assertIsNone(result['delta'])

    def test_change_target_does_not_commit_without_a_settled_final(self):
        self.result(position=12, vr=8754, delta=-31, phase='change', count=4)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.result(position=12, vr=8724, count=1)
        self.assertEqual(self.store.snapshot()['races'], [])

    def test_animation_and_inconsistent_change_do_not_invent_baseline(self):
        self.result(vr=8790, delta=-46, phase='change', count=2)
        for vr in (8780, 8770, 8760):
            self.result(vr=vr, count=1)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.result()
        result = self.store.snapshot()['races'][0]['ww_result']
        self.assertIsNone(result['vr_before'])
        self.assertIsNone(result['delta'])

    def test_first_stable_change_not_later_animation_remainder_is_used(self):
        self.result(position=5, vr=8576, delta=52, phase='change', count=2)
        self.result(position=5, vr=8600, delta=28, phase='change', count=2)
        self.result(position=5, vr=8628)
        self.assertEqual(self.store.snapshot()['races'][0]['ww_result']['delta'], 52)

    def test_cut_before_final_does_not_reuse_previous_race_or_delta(self):
        self.intro('Race A')
        self.result(position=5, vr=8000, delta=100, phase='change', count=2)
        self.reader.result = None
        self.observe(12)
        self.result(position=5, vr=8100)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 2)
        self.assertEqual(races[0]['course'], 'Race A')
        self.assertIsNone(races[0]['ww_result'])
        self.assertIsNone(races[1]['offset_ms'])
        self.assertIsNone(races[1]['ww_result']['delta'])
        self.assertIsNone(races[1]['ww_result']['vr_before'])

    def test_offscreen_highlight_during_unfinished_table_keeps_same_race(self):
        self.intro('Race A')
        self.result(position=5, vr=8000, delta=100, phase='change', count=2)
        self.reader.result, self.reader.table = None, True
        self.observe(12)
        self.result(position=5, vr=8100)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 1)
        self.assertEqual(races[0]['ww_result']['delta'], 100)

    def test_table_like_scenery_does_not_detach_pending_course(self):
        self.intro('Dandelion Depths')
        self.reader.result, self.reader.table = None, True
        self.observe()
        self.reader.table = False
        self.observe(8)
        self.result(position=1, vr=8644)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 1)
        self.assertEqual(races[0]['course'], 'Dandelion Depths')
        self.assertEqual(races[0]['ww_result']['vr_after'], 8644)

    def test_roster_like_scenery_does_not_start_new_intro_without_course(self):
        self.intro('Moo Moo Meadows')
        self.reader.prerace_layout = True
        self.observe()
        self.reader.prerace_layout = False
        self.observe(6)
        self.result(position=4, vr=8689)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 1)
        self.assertEqual(races[0]['course'], 'Moo Moo Meadows')
        self.assertEqual(races[0]['offset_ms'], 1000)
        self.assertEqual(races[0]['ww_result']['vr_after'], 8689)

    def test_same_course_and_same_result_can_be_two_distinct_races(self):
        for _ in range(2):
            self.intro()
            self.result()
        self.assertEqual(len(self.store.snapshot()['races']), 2)

    def test_intro_owns_result_even_if_selected_session_changes(self):
        owner = self.store.snapshot()['session']
        self.intro()
        self.store.create_session('Another session', owner['id'])
        self.result()
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot['races'][0]['session_id'], owner['id'])
        self.assertIsNone(snapshot['ww']['current_vr'])

    def test_manual_result_during_evidence_write_wins(self):
        self.intro()
        race = self.store.snapshot()['races'][0]
        self.result(count=1)

        def correct(path, frame):
            self.store.set_ww_result(race['id'], position=1, vr_after=9000)
            return True

        with patch('cv2.imwrite', side_effect=correct):
            self.observe()
        saved = self.store.snapshot()['races'][0]
        self.assertEqual(saved['ww_result']['vr_after'], 9000)
        self.assertFalse(saved['ww_result']['derive_from_previous'])
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(any(e['kind'] == 'results' for e in saved['evidence']))

    def test_deletion_invalidates_inflight_read(self):
        self.intro()
        race = self.store.snapshot()['races'][0]
        self.result(count=1)

        def deleted(frame):
            self.store.delete_race(race['id'])
            self.capture.prune_deleted_races()
            return self.reader.result

        with patch.object(self.reader, 'read_results', side_effect=deleted):
            self.observe()
        self.assertEqual(self.store.snapshot()['races'], [])

    def test_restart_preserves_completed_result_latch(self):
        self.result()
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader)
        self.observe(8)
        self.assertEqual(len(self.store.snapshot()['races']), 1)

    def test_restart_during_gameplay_rearms_for_next_cut_only_result(self):
        self.result()
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader)
        self.reader.result = None
        self.observe(6)
        self.result(position=16, vr=8736)
        self.assertEqual(len(self.store.snapshot()['races']), 2)
        self.assertEqual(self.store.snapshot()['ww']['current_vr'], 8736)
        self.assertEqual(self.store.snapshot()['races'][1]['ww_result']['delta'], -23)

    def test_restart_during_unfinished_result_keeps_boundary_not_delta(self):
        self.intro('Race A')
        self.result(position=5, vr=8000, delta=100, phase='change', count=2)
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader)
        self.reader.result = None
        self.observe(6)
        self.result(position=5, vr=8100)
        races = self.store.snapshot()['races']
        self.assertEqual(len(races), 2)
        self.assertIsNone(races[0]['ww_result'])
        self.assertIsNone(races[1]['ww_result']['delta'])

    def test_import_fixed_owner_and_live_latch_are_independent(self):
        live = self.capture
        session = self.store.snapshot()['session']
        self.capture = WWCaptureCoordinator(self.store, self.lock, self.root / 'ww', self.reader,
                                             session_id=session['id'], folder_id=session['folder_id'],
                                             pending_owner=live)
        self.store.create_session('Other', session['id'])
        self.result()
        race = self.store.snapshot()['races'][0]
        self.assertEqual(race['session_id'], session['id'])
        self.assertEqual(race['ww_result']['source'], 'import')
        self.assertIsNone(live.state['race_id'])

    def test_no_recording_does_not_use_capture_elapsed_time(self):
        self.reader.prerace = {'course': 'Wario Stadium'}
        self.capture.observe(self.frame)
        self.reader.prerace = None
        self.capture.observe(self.frame)
        self.assertIsNone(self.store.snapshot()['races'][0]['recording_id'])
        self.assertIsNone(self.store.snapshot()['races'][0]['offset_ms'])


if __name__ == '__main__':
    unittest.main()
