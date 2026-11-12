"""Review-only partial observations with synthetic frames and temporary stores."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from mkw_tracker.capture import CaptureCoordinator
from mkw_tracker.server import Application


def rows(positions):
    return [{'position': p, 'name': f'Player {p}', 'confidence': .9} for p in positions]


class PartialCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root / 'scores.sqlite', self.root)
        self.addCleanup(self.app.close)
        self.capture = self.app.capture
        self.reader = Mock()
        self.reader.read_prerace.return_value = None
        self.reader.is_prerace_layout.return_value = False
        self.reader.read.return_value = {'phase': 'unknown', 'rows': []}
        self.reader.read_partial.return_value = {'phase': 'unknown', 'rows': []}
        self.capture.reader = self.reader
        self.frame = np.full((100, 160, 3), 100, dtype=np.uint8)

    def partial(self, positions, capture=None):
        self.reader.read_partial.return_value = {'phase': 'partial_results', 'rows': rows(positions)}
        (capture or self.capture).observe(self.frame)

    def complete(self, capture=None):
        self.reader.read.return_value = {'phase': 'finishing_results', 'rows': rows(range(1, 13))}
        (capture or self.capture).observe(self.frame)

    def candidate(self):
        return self.capture.snapshot()['pending'][0]

    def test_shifted_rows_queue_only_actual_positions_and_no_scores(self):
        self.partial([9, 10, 11])
        candidate = self.candidate()
        self.assertTrue(candidate['partial'])
        self.assertEqual([r['position'] for r in candidate['rows']], [9, 10, 11])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)
        self.assertEqual(self.app.store.snapshot()['players'], [])
        self.assertIsNone(self.app.store.get_race(candidate['race_id'])['offset_ms'])
        self.assertTrue(self.capture.state['armed'])
        with self.assertRaises(ValueError):
            self.capture.review(candidate['id'], [{'name': 'Only one'}])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_better_single_frame_upgrades_same_candidate_then_full_latches(self):
        self.partial([9, 10, 11])
        original = self.candidate()
        self.partial(range(3, 12))
        better = self.candidate()
        self.assertEqual(better['id'], original['id'])
        self.assertNotEqual(better['screenshot_url'], original['screenshot_url'])
        self.assertEqual(len(better['rows']), 9)
        self.assertEqual(len(self.capture.state['pending']), 1)
        self.assertEqual(self.reader.read_partial.call_args.kwargs, {'minimum_rows': 3})
        files = sorted(self.capture.evidence_dir.iterdir())
        self.partial([9, 10, 11])
        self.partial(range(3, 12))
        self.assertEqual(self.candidate(), better)
        self.assertEqual(sorted(self.capture.evidence_dir.iterdir()), files)
        self.complete()
        full = self.candidate()
        self.assertFalse(full['partial'])
        self.assertEqual(full['id'], original['id'])
        self.assertEqual(len(full['rows']), 12)
        self.assertFalse(self.capture.state['armed'])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_eleven_observed_players_leave_twelfth_unobserved(self):
        self.partial(range(1, 12))
        self.assertEqual(len(self.candidate()['rows']), 11)
        self.assertFalse(any(r['position'] == 12 for r in self.candidate()['rows']))
        self.capture.review(self.candidate()['id'], [{'name': f'Human reviewed {p}'} for p in range(12)])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 82)
        self.partial(range(1, 12))
        self.assertEqual(self.capture.state['pending'], [])
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)

    def test_dismiss_and_restart_do_not_recreate_partial_review(self):
        self.partial([9, 10, 11])
        self.capture.dismiss(self.candidate()['id'])
        restarted = CaptureCoordinator(self.app.store, self.app.lock, self.root, self.reader)
        restarted.observe(self.frame)
        self.assertEqual(restarted.state['pending'], [])
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)
        self.reader.read_prerace.return_value = {'course': 'Rainbow Road'}
        restarted.observe(self.frame)
        self.reader.read_prerace.return_value = None
        restarted.observe(self.frame)
        self.assertEqual(len(restarted.state['pending']), 1)
        self.assertEqual(len(self.app.store.snapshot()['races']), 2)

    def test_restart_retains_partial_candidate_for_fuller_frame(self):
        self.partial([9, 10, 11])
        original = self.candidate()
        restarted = CaptureCoordinator(self.app.store, self.app.lock, self.root, self.reader)
        self.partial(range(1, 12), restarted)
        self.assertEqual(restarted.state['pending'][0]['id'], original['id'])
        self.assertEqual(len(restarted.state['pending'][0]['rows']), 11)

    def test_yolo_never_retroactively_scores_partial_review(self):
        self.partial([9, 10, 11])
        self.capture.set_yolo(True)
        self.complete()
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)
        self.assertEqual(len(self.capture.state['pending']), 1)
        self.assertEqual(self.reader.read.call_args.kwargs, {})

    def test_invalid_and_wrong_phase_partial_outputs_never_queue(self):
        for phase, positions in [('standings', [9, 10, 11]), ('partial_results', [1, 1, 2]),
                                 ('partial_results', [True, 2, 3]), ('partial_results', [1, 2, 13]),
                                 ('partial_results', []), ('partial_results', [3])]:
            self.reader.read_partial.return_value = {'phase': phase, 'rows': rows(positions)}
            self.capture.observe(self.frame)
            self.assertEqual(self.app.store.snapshot()['races'], [])

    def test_import_partial_review_dismissal_fences_inflight_upgrade(self):
        imported = CaptureCoordinator(self.app.store, self.app.lock, self.root, self.reader,
            session_id=self.app.store.active_session()['id'], pending_owner=self.capture)
        self.partial([9, 10, 11], imported)
        candidate = self.candidate()
        def dismissed(*_args, **_kwargs):
            self.capture.dismiss(candidate['id'])
            return {'phase': 'partial_results', 'rows': rows(range(1, 12))}
        self.reader.read_partial.side_effect = dismissed
        imported.observe(self.frame)
        self.assertEqual(self.capture.state['pending'], [])
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)
        self.assertIsNone(self.capture.overlay_event)
        self.assertIsNone(imported.overlay_event)

    def test_manual_score_during_image_save_wins(self):
        self.partial([9, 10, 11])
        candidate = self.candidate()
        original = cv2.imwrite
        def confirm(path, frame):
            self.capture.review(candidate['id'], [{'name': f'Player {p}'} for p in range(1, 13)])
            return original(path, frame)
        with patch('cv2.imwrite', side_effect=confirm):
            self.partial(range(1, 12))
        self.assertEqual(self.capture.state['pending'], [])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 82)

    def test_partial_end_emits_once_and_upgrade_preserves_manual_hide(self):
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        self.partial([9, 10, 11])
        self.assertTrue(self.app.overlay_snapshot()['visible'])
        event = self.capture.overlay_event
        self.app.overlay_visible = False
        self.partial(range(1, 12))
        self.complete()
        self.assertEqual(self.capture.overlay_event, event)
        self.assertFalse(self.app.overlay_snapshot()['visible'])
