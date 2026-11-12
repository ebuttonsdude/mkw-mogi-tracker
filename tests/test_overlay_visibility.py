"""Live race visibility uses temporary stores and synthetic frames, not hardware."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mkw_tracker.capture import CaptureCoordinator
from mkw_tracker.server import Application
from mkw_tracker.settings import DEFAULT_SETTINGS, merge_settings

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


class OverlayMotionSettingsTests(unittest.TestCase):
    def test_defaults_and_all_transition_choices(self):
        self.assertFalse(DEFAULT_SETTINGS['overlay']['auto_visibility'])
        for style in ('slide', 'fade', 'checkered', 'dissolve', 'zoom', 'flip', 'iris', 'wipe'):
            settings = merge_settings(DEFAULT_SETTINGS, {'overlay': {
                'auto_visibility': True, 'transition_style': style}})
            self.assertEqual(settings['overlay']['transition_style'], style)
            self.assertTrue(settings['overlay']['auto_visibility'])
        self.assertEqual(DEFAULT_SETTINGS['overlay']['transition_style'], 'slide')

    def test_invalid_values_do_not_mutate_settings(self):
        for key, value in [('auto_visibility', 1), ('auto_visibility', 'yes'),
                           ('transition_style', 'random'), ('transition_style', None)]:
            settings = deepcopy(DEFAULT_SETTINGS)
            with self.assertRaises(ValueError):
                merge_settings(settings, {'overlay': {key: value}})
            self.assertEqual(settings, DEFAULT_SETTINGS)


@unittest.skipIf(cv2 is None, 'optional capture dependencies unavailable')
class AutomaticOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root / 'db.sqlite3', self.root)
        self.addCleanup(self.app.close)
        self.frame = np.full((100, 160, 3), 100, dtype=np.uint8)
        self.reader = Mock()
        self.reader.read_prerace.return_value = None
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.app.capture.reader = self.reader
        self.app.update_settings({'overlay': {'auto_visibility': True}})

    def intro(self, capture=None):
        capture = capture or self.app.capture
        self.reader.read_prerace.return_value = {'course': 'Rainbow Road'}
        capture.observe(self.frame)
        self.reader.read_prerace.return_value = None

    def result(self, capture=None):
        capture = capture or self.app.capture
        self.reader.read.return_value = {'phase': 'finishing_results', 'rows': [
            {'position': position, 'name': f'Player {position}'} for position in range(1, 13)]}
        capture.observe(self.frame)

    def visible(self):
        return self.app.overlay_snapshot()['visible']

    def test_intro_hides_results_show_without_confirming_scores(self):
        self.assertTrue(self.visible())
        self.intro()
        self.assertFalse(self.visible())
        self.app.capture.observe(self.frame)
        self.result()
        self.assertTrue(self.visible())
        snapshot = self.app.store.snapshot()
        self.assertEqual(snapshot['room']['points'], 0)
        self.assertEqual(snapshot['room']['scored_races'], 0)
        self.assertEqual(len(self.app.capture.state['pending']), 1)
        self.assertEqual(snapshot['races'][0]['status'], 'pending')

    def test_repeated_intro_and_results_do_not_undo_manual_override(self):
        self.intro()
        self.assertFalse(self.visible())
        self.app.overlay_visible = True
        event = self.app.capture.overlay_event
        self.intro()
        self.assertEqual(self.app.capture.overlay_event, event)
        self.assertTrue(self.visible())
        self.result()
        self.assertTrue(self.visible())
        self.app.overlay_visible = False
        self.result()
        self.assertFalse(self.visible())
        self.intro()
        self.assertFalse(self.visible())
        self.result()
        self.assertTrue(self.visible())

    def test_default_disabled_and_enabling_waits_for_next_event(self):
        self.app.update_settings({'overlay': {'auto_visibility': False}})
        self.intro()
        self.assertTrue(self.visible())
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        self.assertTrue(self.visible())
        self.result()
        self.assertTrue(self.visible())
        self.intro()
        self.assertFalse(self.visible())

    def test_import_coordinator_never_emits_live_visibility_events(self):
        capture = CaptureCoordinator(self.app.store, self.app.lock, self.root, self.reader,
                                     session_id=self.app.store.active_session()['id'],
                                     pending_owner=self.app.capture)
        self.intro(capture)
        self.result(capture)
        self.assertIsNone(capture.overlay_event)
        self.assertIsNone(self.app.capture.overlay_event)
        self.assertTrue(self.visible())

    def test_enabling_without_a_poll_does_not_replay_old_intro(self):
        self.app.update_settings({'overlay': {'auto_visibility': False}})
        self.intro()
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        self.assertTrue(self.visible())

    def test_foreign_session_event_and_layout_only_frames_are_ignored(self):
        self.reader.is_prerace_layout.return_value = True
        self.app.capture.observe(self.frame)
        self.assertTrue(self.visible())
        self.assertIsNone(self.app.capture.overlay_event)
        self.reader.is_prerace_layout.return_value = False
        self.app.capture._emit_overlay_event(False, 'another-session')
        self.assertTrue(self.visible())

    def test_partial_or_suspected_results_do_not_show(self):
        self.intro()
        self.app.capture.observe(self.frame)
        self.reader.read.return_value = {'phase': 'finishing_results', 'suspected_results': True,
                                         'rows': [{'position': 1, 'name': 'Only one'}]}
        self.app.capture.observe(self.frame)
        self.assertFalse(self.visible())
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_restart_does_not_replay_persisted_capture_state(self):
        self.intro()
        self.assertFalse(self.visible())
        self.app.close()
        self.app = Application(self.root / 'db.sqlite3', self.root)
        self.addCleanup(self.app.close)
        self.assertTrue(self.app.settings['overlay']['auto_visibility'])
        self.assertIsNone(self.app.capture.overlay_event)
        self.assertTrue(self.visible())

    def test_ffa_and_ww_use_their_own_live_events(self):
        for mode in ('ffa', 'ww'):
            self.app.switch_tracker_mode(mode, self.app.store.active_session()['id'])
            self.app.capture.reader = self.reader
            self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
            self.reader.read_results.return_value = None
            self.reader.is_results_layout.return_value = False
            self.intro()
            self.assertFalse(self.visible(), mode)
            if mode == 'ww':
                self.reader.read_results.return_value = {'phase': 'final', 'position': 1,
                                                        'vr': 9000, 'delta': None}
                self.app.capture.observe(self.frame)
                self.app.capture.observe(self.frame)
            else:
                self.result()
            self.assertTrue(self.visible(), mode)

    def test_ww_manual_edit_during_result_image_save_still_shows(self):
        self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
        self.app.capture.reader = self.reader
        self.reader.read_results.return_value = None
        self.reader.is_results_layout.return_value = False
        self.intro()
        self.assertFalse(self.visible())
        self.reader.read_results.return_value = {'phase': 'final', 'position': 2,
                                                'vr': 9000, 'delta': None}
        self.app.capture.observe(self.frame)
        original = cv2.imwrite

        def manual_edit(path, frame):
            self.app.store.set_ww_result(self.app.capture.state['race_id'], position=1, vr_after=9100)
            return original(path, frame)

        with patch('cv2.imwrite', side_effect=manual_edit):
            self.app.capture.observe(self.frame)
        self.assertTrue(self.visible())
        self.assertFalse(self.app.capture.state['armed'])
        self.assertEqual(self.app.store.snapshot()['ww']['current_vr'], 9100)
