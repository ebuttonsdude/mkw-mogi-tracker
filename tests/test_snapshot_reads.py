"""Full-history compatibility and bounded session reads for reports/outputs."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mkw_tracker.store import TrackerStore


class SnapshotReadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = TrackerStore(Path(self.directory.name) / 'tracker.sqlite3')
        self.addCleanup(self.store.close)

    def populate_session(self, name):
        store = self.store
        session = store.active_session()
        team = store.create_team(name)
        players = [store.add_player(f'{name} 選手 {i}')['id'] for i in range(12)]
        for player in players:
            store.update_player(player, store.get_player(player)['name'], team['id'])
        recording = store.create_recording(name)
        first = store.create_race(name + '-first', 'Crown City', recording['id'], 2000)
        second = store.create_race(name + '-second', 'Rainbow Road', recording['id'], 1000)
        store.submit_result(second['id'], players)
        store.submit_result(first['id'], players[::-1])
        store.correct_result(second['id'], players[::-1])
        store.add_penalty(first['id'], players[0], 2, 'First penalty')
        store.add_penalty(first['id'], players[0], 3, 'Second penalty')
        for number, kind in enumerate(('prerace', 'suspected_results', 'results')):
            store.add_evidence(first['id'], f'00000000-0000-0000-0000-{number:012d}.png', kind=kind)
        store.delete_player(players[1])
        store.create_race(name + '-pending', 'Mario Circuit')
        return session, team, players, recording

    def test_batch_hydration_matches_individual_races_exactly(self):
        first = self.populate_session('Old')
        self.store.create_session('Current', first[0]['id'])
        self.populate_session('Current')
        expected = [self.store._race(row['id']) for row in self.store._rows('SELECT id FROM races ORDER BY rowid')]
        with patch.object(self.store, '_race', side_effect=AssertionError('per-race hydration')):
            actual = self.store.snapshot()
        self.assertEqual(actual['races'], expected)
        self.assertEqual(actual['room']['points'], 164)
        self.assertEqual(actual['room']['penalty_points'], 5)
        self.assertEqual(actual['room']['net_points'], 159)
        self.assertEqual(sum(p['points'] for p in actual['players']), 155)
        self.assertEqual(actual['teams'][0]['points'], 155)

    def test_scoped_snapshot_preserves_deleted_roster_and_archive_ownership(self):
        old, team, players, recording = self.populate_session('Old')
        current = self.store.create_session('Current', old['id'])
        self.populate_session('Current')
        with patch.object(self.store, '_race', side_effect=AssertionError('per-race hydration')):
            selected = self.store.snapshot(session_id=old['id'], include_history=False)
        self.assertEqual(self.store.active_session()['id'], current['id'])
        self.assertEqual(selected['session'], old)
        self.assertEqual(selected['sessions'], [old])
        self.assertEqual([f['id'] for f in selected['folders']], [old['folder_id']])
        self.assertEqual(selected['folders'][0]['race_count'], 3)
        self.assertEqual(selected['recordings'], [recording])
        self.assertEqual({r['session_id'] for r in selected['races']}, {old['id']})
        self.assertEqual([p['id'] for p in selected['archived_players']], [players[1]])
        self.assertEqual(selected['archived_teams'], [])
        self.assertEqual(selected['teams'][0]['id'], team['id'])
        self.assertEqual(selected['teams'][0]['points'], 155)
        with self.assertRaises(KeyError):
            self.store.snapshot(session_id='missing', include_history=False)

    def test_scoped_reads_are_fresh_after_correction_penalty_and_roster_changes(self):
        _, team, players, _ = self.populate_session('Current')

        def read():
            return self.store.snapshot(include_history=False)

        self.assertEqual(read()['teams'][0]['points'], 155)
        self.store.correct_result('Current-first', players)
        self.assertEqual(read()['races'][0]['placements'][0]['player_id'], players[0])
        penalty = self.store.add_penalty('Current-second', players[-1], 7, 'New penalty')
        self.assertEqual(read()['room']['penalty_points'], 12)
        self.store.remove_penalty(penalty['id'])
        self.assertEqual(read()['room']['penalty_points'], 5)
        self.store.update_player(players[0], 'Renamed 選手', None)
        snapshot = read()
        self.assertEqual(snapshot['races'][0]['placements'][0]['player_name'], 'Renamed 選手')
        self.assertIsNone(next(p for p in snapshot['players'] if p['id'] == players[0])['team_id'])
        self.store.update_team(team['id'], name='Renamed team', color='#abcdef')
        self.assertEqual(read()['teams'][0]['color'], '#abcdef')
        self.store.delete_race('Current-second')
        self.assertEqual(read()['room']['scored_races'], 1)
        self.assertEqual(read()['folders'][0]['race_count'], 2)

    def test_query_count_is_constant_and_scoped_read_uses_no_write_transaction(self):
        store = self.store
        old = store.active_session()
        with store._transaction():
            store._db.executemany(
                'INSERT INTO races(id,session_id,course,folder_id,created_at) VALUES(?,?,?,?,?)',
                [(str(i), old['id'], 'Crown City', old['folder_id'], old['started_at']) for i in range(2400)]).close()
        store.create_session('Current', old['id'])
        store.create_race('current', 'Rainbow Road')
        statements = []
        store._db.set_trace_callback(statements.append)
        try:
            snapshot = store.snapshot(include_history=False)
            scoped_count = len(statements)
            statements.clear()
            full = store.snapshot()
        finally:
            store._db.set_trace_callback(None)
        self.assertEqual([r['id'] for r in snapshot['races']], ['current'])
        self.assertEqual(len(full['races']), 2401)
        self.assertLessEqual(scoped_count, 20)
        self.assertLessEqual(len(statements), 20)
        self.assertNotIn('BEGIN IMMEDIATE', statements)

    def test_chapter_reads_and_active_identity_lookup_do_not_load_history(self):
        session, team, players, recording = self.populate_session('Current')
        with patch.object(self.store, 'snapshot', side_effect=AssertionError('full snapshot')), \
             patch.object(self.store, '_race', side_effect=AssertionError('hydrated race')):
            self.assertEqual(self.store.get_player(players[0])['id'], players[0])
            self.assertEqual(self.store.get_team(team['id'])['id'], team['id'])
            self.assertTrue(self.store.folder_exists(session['folder_id']))
            self.assertFalse(self.store.folder_exists('missing'))
            chapters = self.store.recording_chapters(recording['id'])
            self.assertEqual([(r['course'], r['offset_ms']) for r in chapters],
                             [('Rainbow Road', 1000), ('Crown City', 2000)])
            for missing in ('missing', None, ''):
                with self.subTest(recording=missing), self.assertRaises(KeyError):
                    self.store.recording_chapters(missing)
            with self.assertRaises(ValueError):
                self.store.get_player(players[1])

    def test_commit_notification_is_after_commit_and_skips_reads_and_rollback(self):
        observed = []

        def committed():
            self.assertFalse(self.store._db.in_transaction)
            observed.append(self.store.snapshot(include_history=False)['teams'])

        self.store.on_commit = committed
        team = self.store.create_team('Committed')
        self.assertEqual([[row['id'] for row in rows] for rows in observed], [[team['id']]])
        self.store.snapshot()
        self.assertEqual(len(observed), 1)
        with self.assertRaises(RuntimeError):
            with self.store._transaction():
                self.store._write('UPDATE teams SET name=? WHERE id=?', ('Rolled back', team['id']))
                raise RuntimeError('Abort')
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.store.get_team(team['id'])['name'], 'Committed')
        with self.store._transaction():
            self.store._rows('SELECT id FROM teams')
        self.assertEqual(len(observed), 1)


if __name__ == '__main__':
    unittest.main()
