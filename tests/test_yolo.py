"""Opt-in Mogi scoring with synthetic frames and temporary stores only."""
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from mkw_tracker.capture import CaptureCoordinator
from mkw_tracker.recognition import ResultReader
from mkw_tracker.server import Application
from tests.test_imports import FakeVideo
from tests.test_recognition import word


def reader(rows=None):
    result = Mock()
    result.read_prerace.return_value = None
    result.is_prerace_layout.return_value = False
    result.read.return_value = {'phase': 'finishing_results', 'rows': rows or [
        {'position': 3, 'name': '日本語'}, {'position': 8, 'name': ''}]}
    return result


class YoloTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = Application(self.path / 'db.sqlite3', self.path)
        self.addCleanup(self.app.close)
        self.capture = self.app.capture
        self.capture.reader = reader()
        self.frame = np.full((180, 320, 3), 100, dtype=np.uint8)

    def enable(self, value=True):
        return self.app.update_settings({'yolo_mode': value}, confirm_yolo=value,
                                       expected_session_id=self.app.store.active_session()['id'])

    def race(self):
        return self.app.store.snapshot()['races'][0]

    def test_default_partial_rejected_full_pending_and_enable_not_retroactive(self):
        self.assertFalse(self.app.settings['yolo_mode'])
        self.capture.observe(self.frame)
        self.assertEqual(self.app.store.snapshot()['races'], [])
        self.capture.reader = reader([{'position': p, 'name': str(p)} for p in range(1, 13)])
        self.capture.observe(self.frame)
        self.assertEqual(self.race()['status'], 'pending')
        self.assertEqual(self.race()['auto_confirmed'], 0)
        self.enable()
        self.capture.observe(self.frame)
        self.assertEqual(self.race()['status'], 'pending')
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_consent_boolean_validation_and_session_ownership(self):
        for values, kwargs in [({'yolo_mode': True}, {}),
                               ({'yolo_mode': True}, {'confirm_yolo': True}),
                               ({'yolo_mode': True}, {'confirm_yolo': 1}),
                               ({'yolo_mode': 1}, {'confirm_yolo': True})]:
            with self.assertRaises(ValueError):
                self.app.update_settings(values, **kwargs)
        self.assertFalse(self.app.settings['yolo_mode'])
        self.enable()
        self.assertTrue(self.capture.yolo_policy[0])
        self.enable(False)
        self.assertFalse(self.capture.yolo_policy[0])

    def test_enable_requires_idle_but_disable_is_immediate(self):
        self.enable()
        with patch.object(self.app.cameras, 'thread', Mock(is_alive=lambda: True)):
            with self.assertRaisesRegex(ValueError, 'Stop capture'):
                self.enable()
            self.enable(False)
        self.assertFalse(self.capture.yolo_policy[0])

    def test_sparse_positions_placeholders_evidence_correction_and_restart(self):
        self.enable()
        self.capture.observe(self.frame)
        race = self.race()
        self.assertEqual(race['auto_confirmed'], 1)
        self.assertEqual(race['status'], 'scored')
        self.assertEqual(race['placements'][2]['player_name'], '日本語')
        self.assertEqual(len({p['player_id'] for p in race['placements']}), 12)
        self.assertEqual(sum(p['player_name'] == 'not-detected-fixme' for p in race['placements']), 11)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 82)
        self.assertTrue(race['evidence'])
        self.assertEqual(self.capture.snapshot()['pending'], [])
        order = [p['player_id'] for p in race['placements']]
        corrected = self.app.store.correct_result(race['id'], list(reversed(order)))
        self.assertEqual(corrected['auto_confirmed'], 1)
        self.assertEqual(corrected['revision'], 2)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 82)
        self.assertEqual(len(self.app.store._rows('SELECT * FROM placements WHERE revision=1')), 12)
        resumed = CaptureCoordinator(self.app.store, self.app.lock, self.path, self.capture.reader)
        resumed.set_yolo(True)
        resumed.observe(self.frame)
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)
        self.assertEqual(self.race()['revision'], 2)

    def test_held_results_next_race_and_placeholder_names_never_merge(self):
        self.enable()
        for _ in range(3):
            self.capture.observe(self.frame)
        first = self.race()
        self.capture.reader.is_prerace_layout.return_value = True
        self.capture.observe(self.frame)
        self.capture.reader.is_prerace_layout.return_value = False
        self.capture.observe(self.frame)
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)
        self.capture.reader.read_prerace.return_value = {'course': 'Crown City'}
        for _ in range(3):
            self.capture.observe(self.frame)
        self.capture.reader.read_prerace.return_value = None
        for _ in range(3):
            self.capture.observe(self.frame)
        races = self.app.store.snapshot()['races']
        self.assertEqual(len(races), 2)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 164)
        self.assertEqual(first['placements'][2]['player_id'], races[1]['placements'][2]['player_id'])
        first_ids = {p['player_id'] for p in first['placements'] if p['player_name'] == 'not-detected-fixme'}
        second_ids = {p['player_id'] for p in races[1]['placements'] if p['player_name'] == 'not-detected-fixme'}
        self.assertFalse(first_ids & second_ids)

    def test_only_unique_exact_names_match_never_fuzzy_or_duplicate_identity(self):
        store = self.app.store
        exact = store.add_player('日本語')
        store.add_player('Duplicate')
        store.add_player('Duplicate')
        store.add_player('AApple')
        placeholder = store.add_player('not-detected-fixme')
        self.capture.reader = reader([{'position': i, 'name': name} for i, name in enumerate(
            ['日本語', '日本語', 'Duplicate', 'A Apple', 'not-detected-fixme'], 1)])
        self.enable()
        self.capture.observe(self.frame)
        placements = self.race()['placements']
        self.assertEqual(placements[0]['player_id'], exact['id'])
        self.assertNotEqual(placements[1]['player_id'], exact['id'])
        self.assertEqual(placements[3]['player_name'], 'A Apple')
        self.assertNotIn(placeholder['id'], [p['player_id'] for p in placements])
        self.assertEqual(len({p['player_id'] for p in placements}), 12)
        self.assertEqual(store.snapshot()['teams'], [])

    def test_no_phase_no_positions_or_invalid_positions_cannot_score(self):
        self.enable()
        for phase, rows in [('standings', [{'position': 1}]), ('unknown', [{'position': 1}]),
                            ('finishing_results', []), ('finishing_results', [{'position': True}]),
                            ('finishing_results', [{'position': 13}]),
                            ('finishing_results', [{'position': 1}, {'position': 1}])]:
            self.capture.reader.read.return_value = {'phase': phase, 'rows': rows}
            self.capture.observe(self.frame)
            self.assertEqual(self.app.store.snapshot()['races'], [])

    def test_disabling_during_recognition_invalidates_inflight_then_strict_review(self):
        self.enable()
        rows = [{'position': p, 'name': str(p)} for p in range(1, 13)]
        def recognize(_frame, **_kwargs):
            self.enable(False)
            return {'phase': 'finishing_results', 'rows': rows}
        self.capture.reader.read.side_effect = recognize
        self.capture.observe(self.frame)
        self.assertEqual(self.app.store.snapshot()['races'], [])
        self.capture.reader.read.side_effect = None
        self.capture.reader.read.return_value = {'phase': 'finishing_results', 'rows': rows}
        self.capture.observe(self.frame)
        self.assertEqual(self.race()['status'], 'pending')
        self.assertEqual(self.race()['auto_confirmed'], 0)

    def test_session_switch_during_recognition_drops_auto_result(self):
        self.enable()
        old = self.app.store.active_session()['id']
        def recognize(_frame, **_kwargs):
            self.app.store.create_session('New', old)
            return {'phase': 'finishing_results', 'rows': [{'position': 1, 'name': 'One'}]}
        self.capture.reader.read.side_effect = recognize
        self.capture.observe(self.frame)
        self.assertEqual(self.app.store._races(), [])

    def test_latched_old_session_remains_manual_not_redirected(self):
        self.enable()
        old = self.app.store.active_session()['id']
        self.capture.reader.read_prerace.return_value = {'course': 'Crown City'}
        self.capture.observe(self.frame)
        self.capture.reader.read_prerace.return_value = None
        self.app.store.create_session('New', old)
        self.capture.observe(self.frame)
        pending = self.capture.snapshot()['pending'][0]
        race = self.app.store.get_race(pending['race_id'])
        self.assertEqual(race['session_id'], old)
        self.assertEqual(race['status'], 'pending')
        self.assertEqual(len(pending['rows']), 12)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_mode_contexts_ffa_autoscores_and_ww_is_unchanged(self):
        self.enable()
        self.app.switch_tracker_mode('ffa', self.app.store.active_session()['id'])
        self.app.capture.reader = reader()
        self.app.capture.observe(self.frame)
        self.assertEqual(self.race()['auto_confirmed'], 1)
        self.assertEqual(self.app.store.snapshot()['teams'], [])
        self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
        self.assertFalse(self.app.capture.yolo_policy[0])
        with self.assertRaisesRegex(ValueError, 'Mogi'):
            self.enable()
        self.app.switch_tracker_mode('mogi', self.app.store.active_session()['id'])
        self.assertEqual(self.app.store.snapshot()['races'], [])
        self.assertTrue(self.app.capture.yolo_policy[0])

    def test_mock_video_import_scores_original_destination_only(self):
        self.enable()
        target = self.app.store.active_session()
        self.app.store.create_session('Other', target['id'])
        video = self.path / 'synthetic.mkv'
        video.write_bytes(b'fake video adapter only')
        self.app.imports.video_factory = lambda _: FakeVideo()
        self.app.imports.reader_factory = lambda _: reader()
        self.app.imports.start(str(video), folder_id=target['folder_id'])
        self.app.imports.thread.join(5)
        self.assertFalse(self.app.imports.thread.is_alive())
        job = self.app.imports.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'completed', job['error'])
        races = self.app.store.snapshot(session_id=target['id'])['races']
        self.assertEqual(len(races), 1)
        self.assertEqual(races[0]['auto_confirmed'], 1)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)
        self.assertEqual(self.capture.snapshot()['pending'], [])
        self.assertTrue(video.exists())

    def test_interrupted_latch_save_after_commit_never_scores_twice(self):
        self.enable()
        save = self.capture._save
        calls = 0
        def interrupted_save():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated interrupted latch save')
            save()
        with patch.object(self.capture, '_save', side_effect=interrupted_save):
            with self.assertRaises(OSError):
                self.capture.observe(self.frame)
        self.assertEqual(self.race()['revision'], 1)
        resumed = CaptureCoordinator(self.app.store, self.app.lock, self.path, reader())
        resumed.set_yolo(True)
        resumed.observe(self.frame)
        self.assertEqual(len(self.app.store.snapshot()['races']), 1)
        self.assertEqual(self.app.store.snapshot()['room']['points'], 82)
        self.assertEqual(self.race()['revision'], 1)
        self.assertFalse(resumed.state['armed'])

    def test_auto_commit_failure_rolls_back_identities_and_provenance(self):
        self.enable()
        write = self.app.store._write
        def fail(sql, values=()):
            if sql.startswith('UPDATE races SET revision=1'):
                raise RuntimeError('simulated transaction failure')
            return write(sql, values)
        with patch.object(self.app.store, '_write', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.capture.observe(self.frame)
        self.assertEqual(self.race()['auto_confirmed'], 0)
        self.assertEqual(self.race()['revision'], 0)
        self.assertEqual(self.app.store.snapshot()['players'], [])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)
        self.assertEqual(self.app.store._rows('SELECT * FROM result_revisions'), [])
        self.capture.observe(self.frame)
        self.assertEqual(self.race()['auto_confirmed'], 1)

    def test_import_shares_disable_guard_even_if_reenabled_during_recognition(self):
        self.enable()
        imported = CaptureCoordinator(self.app.store, self.app.lock, self.path, reader(),
                                      session_id=self.app.store.active_session()['id'],
                                      pending_owner=self.capture)
        def recognize(_frame, **_kwargs):
            self.enable(False)
            self.enable()
            return {'phase': 'finishing_results', 'rows': [{'position': 1, 'name': 'One'}]}
        imported.reader.read.side_effect = recognize
        imported.observe(self.frame)
        self.assertEqual(self.app.store.snapshot()['races'], [])

    def test_enabled_preference_survives_restart_without_changing_existing_scores(self):
        self.enable()
        self.app.close()
        self.app = Application(self.path / 'db.sqlite3', self.path)
        self.addCleanup(self.app.close)
        self.assertTrue(self.app.settings['yolo_mode'])
        self.assertTrue(self.app.capture.yolo_policy[0])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_schema_upgrade_defaults_existing_race_to_manual(self):
        # SQLite supports DROP COLUMN here; recreate the precise pre-feature shape.
        self.app.store.create_race('old', 'Course')
        self.app.close()
        import sqlite3
        with closing(sqlite3.connect(self.path / 'db.sqlite3')) as db:
            db.execute('ALTER TABLE races DROP COLUMN auto_confirmed')
        self.app = Application(self.path / 'db.sqlite3', self.path)
        self.addCleanup(self.app.close)
        self.assertEqual(self.race()['auto_confirmed'], 0)


class PartialRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((1152, 2048, 3), dtype=np.uint8)
        self.reader = ResultReader()
        self.names = [word('Third', 2), word('Ninth', 8)]
        self.plus = [word('+1', i) for i in range(3)]

    def test_partial_path_keeps_sparse_ranks_ordinary_rejects(self):
        with patch.object(self.reader, '_ocr', return_value=self.names):
            self.assertEqual(self.reader.read(self.frame)['rows'], [])
        with patch.object(self.reader, '_rank_matches', side_effect=lambda _, rank: rank in (3, 9)), \
             patch.object(self.reader, '_ocr', side_effect=[self.names, self.plus] + [[]] * 10):
            result = self.reader.read(self.frame, allow_partial=True)
        self.assertEqual(result['phase'], 'finishing_results')
        self.assertEqual([r['position'] for r in result['rows']], [3, 9])
        self.assertEqual([r['name'] for r in result['rows']], ['Third', 'Ninth'])

    def test_no_phase_or_no_rank_cannot_invent_race(self):
        for plus in ([], self.plus):
            with patch.object(self.reader, '_rank_matches', return_value=False), \
                 patch.object(self.reader, '_ocr', side_effect=[self.names, plus] + [[]] * 12):
                result = self.reader.read(self.frame, allow_partial=True)
            self.assertEqual(result['rows'], [])
            self.assertNotEqual(result['phase'], 'finishing_results')

    def test_neural_receives_only_proven_rows_even_with_no_baseline_names(self):
        neural = Mock(engine='paddle')
        neural.recognize.return_value = [{'name': '九', 'confidence': .8}]
        self.reader = ResultReader(name_reader=neural)
        with patch.object(self.reader, '_rank_matches', side_effect=lambda _, rank: rank == 9), \
             patch.object(self.reader, '_ocr', side_effect=[[], self.plus] + [[]] * 11):
            result = self.reader.read(self.frame, allow_partial=True)
        self.assertEqual(len(neural.recognize.call_args.args[0]), 1)
        self.assertEqual(result['rows'][0]['position'], 9)
        self.assertEqual(result['rows'][0]['name'], '九')


if __name__ == '__main__':
    unittest.main()
