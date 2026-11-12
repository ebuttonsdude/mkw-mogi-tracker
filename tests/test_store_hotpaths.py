"""Narrow capture reads without full-history snapshots."""

from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_store import WWStore


class StoreHotpathTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = TrackerStore(Path(self.directory.name) / 'tracker.sqlite3')
        self.addCleanup(self.store.close)

    def test_narrow_reads_are_fresh_without_a_snapshot(self):
        store = self.store
        initial = store.snapshot()['session']
        recording = store.create_recording('Video')
        with patch.object(store, 'snapshot', side_effect=AssertionError('full history read')):
            self.assertEqual(store.active_session(), initial)
            self.assertTrue(store.session_exists(initial['id']))
            self.assertFalse(store.session_exists('missing'))
            self.assertFalse(store.race_exists(None))
            self.assertFalse(store.race_exists('missing'))
            with self.assertRaises(KeyError):
                store.get_race('missing')
            with self.assertRaises(KeyError):
                store.get_folder('missing')
            self.assertEqual(store.count_recording_races(recording['id']), 0)
            store.create_race('race', 'Crown City', recording_id=recording['id'])
            self.assertTrue(store.race_exists('race'))
            self.assertEqual(store.count_recording_races(recording['id']), 1)
            store.update_race('race', 'Rainbow Road', recording_id=recording['id'], offset_ms=1000)
            store.add_evidence('race', '00000000-0000-0000-0000-000000000001.png', kind='results', label='Finishing results')
            players = [store.add_player(f'Player {i}')['id'] for i in range(12)]
            store.submit_result('race', players)
            store.correct_result('race', players[::-1])
            penalty = store.add_penalty('race', players[-1], 2, 'Penalty')
            current = store.get_race('race')
            self.assertEqual(current['course'], 'Rainbow Road')
            self.assertEqual(current['offset_ms'], 1000)
            self.assertEqual(current['revision'], 2)
            self.assertEqual(current['placements'][0]['player_id'], players[-1])
            self.assertEqual(current['placements'][0]['net_points'], 13)
            self.assertEqual(current['evidence'][0]['url'], '/evidence/00000000-0000-0000-0000-000000000001.png')
            store.remove_penalty(penalty['id'])
            self.assertEqual(store.get_race('race')['placements'][0]['net_points'], 15)
            store.rename_folder(initial['folder_id'], 'Renamed')
            self.assertEqual(store.get_folder(initial['folder_id'])['name'], 'Renamed')
            self.assertEqual(store.get_folder(initial['folder_id'])['session_id'], initial['id'])
            replacement = store.reset(initial['id'])
            self.assertEqual(store.active_session(), replacement)
            store.delete_race('race')
            self.assertFalse(store.race_exists('race'))
            self.assertEqual(store.count_recording_races(recording['id']), 0)
            store.delete_folder(initial['folder_id'])
            self.assertFalse(store.session_exists(initial['id']))

    def test_read_query_count_is_independent_of_history(self):
        store = self.store
        session = store.snapshot()['session']
        recording = store.create_recording('Video')
        with store._transaction():
            store._db.executemany(
                'INSERT INTO races(id,session_id,course,recording_id,folder_id,created_at) VALUES(?,?,?,?,?,?)',
                [(str(i), session['id'], 'Crown City', recording['id'], session['folder_id'], session['started_at'])
                 for i in range(2400)]).close()
        statements = []
        store._db.set_trace_callback(statements.append)
        try:
            store.active_session()
            store.get_race('1200')
            store.race_exists('1200')
            store.session_exists(session['id'])
            store.get_folder(session['folder_id'])
            self.assertEqual(store.count_recording_races(recording['id']), 2400)
        finally:
            store._db.set_trace_callback(None)
        self.assertLessEqual(len(statements), 13)
        self.assertFalse(any('BEGIN' in sql.upper() for sql in statements))
        self.assertFalse(any('SELECT id FROM races ORDER BY rowid' in sql for sql in statements))
        plan = store._rows('EXPLAIN QUERY PLAN SELECT COUNT(*) FROM races WHERE recording_id=?', (recording['id'],))
        self.assertIn('races_recording', ' '.join(row['detail'] for row in plan))

    def test_single_ww_race_keeps_derived_result_fields(self):
        with closing(WWStore(Path(self.directory.name) / 'ww.sqlite3')) as store:
            store.create_race('ww', 'Crown City')
            store.set_ww_result('ww', position=1, vr_before=8000, vr_after=8050)
            with patch.object(store, 'snapshot', side_effect=AssertionError('full history read')):
                race = store.get_race('ww')
            self.assertEqual(race['status'], 'recorded')
            self.assertEqual(race['ww_result']['delta'], 50)
