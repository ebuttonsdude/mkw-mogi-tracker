from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import UUID

from mkw_tracker.store import POINTS, TrackerStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'scores.sqlite'
        self.store = TrackerStore(self.path)
        self.ids = [self.store.add_player(f'Player {i}')['id'] for i in range(12)]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def race(self, race_id='race'):
        return self.store.create_race(race_id, 'Course')

    def totals(self):
        return {p['id']: p['points'] for p in self.store.snapshot()['players']}

    def test_fixed_points_and_pending_race(self):
        self.assertEqual(POINTS, (15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1))
        self.assertEqual(self.race()['status'], 'pending')
        self.assertEqual(sum(self.totals().values()), 0)
        result = self.store.submit_result('race', self.ids)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(result['status'], 'scored')
        self.assertEqual([p['position'] for p in result['placements']], list(range(1, 13)))
        self.assertEqual(list(self.totals().values()), list(POINTS))
        self.assertEqual(sum(self.totals().values()), 82)
        self.assertTrue(all(p['races'] == 1 for p in self.store.snapshot()['players']))

    def test_internal_capture_session_override_is_validated_and_idempotent(self):
        old = self.store.snapshot()['session']
        self.store.reset(old['id'])
        race = self.store.create_race('late-frame', 'Course', session_id=old['id'])
        self.assertEqual(race['session_id'], old['id'])
        self.assertEqual(race['folder_id'], old['folder_id'])
        self.assertEqual(race, self.store.create_race('late-frame', 'Course', session_id=old['id']))
        current = self.store.snapshot()['session']['id']
        with self.assertRaises(ValueError):
            self.store.create_race('late-frame', 'Course', session_id=current)
        for invalid, error in (('missing', KeyError), ('', ValueError), (False, ValueError)):
            with self.subTest(session_id=invalid), self.assertRaises(error):
                self.store.create_race('invalid', 'Course', session_id=invalid)
        self.assertEqual(self.store.create_race('default', 'Course')['session_id'], current)

    def test_sample_reference(self):
        # Keep the fixture portable; .omx evidence is deliberately not tracked.
        names = [f'p{i:02}' for i in range(1, 13)]
        lookup = dict(zip(names, self.ids))
        second = ['p05', 'p08', 'p12', 'p03', 'p04', 'p07', 'p10', 'p02', 'p06', 'p01', 'p11', 'p09']
        expected = [18, 17, 19, 17, 23, 11, 13, 17, 5, 9, 4, 11]
        for race_id, order in [('first', names), ('second', second)]:
            self.race(race_id)
            self.store.submit_result(race_id, [lookup[p] for p in order])
        self.race('third')
        self.assertEqual(list(self.totals().values()), expected)
        self.assertEqual(sum(self.totals().values()), 164)
        self.assertEqual(self.totals()[lookup['p09']], 5)
        self.assertEqual(self.store.snapshot()['races'][-1]['status'], 'pending')

    def test_restart_persistence_and_idempotent_retries(self):
        team = self.store.create_team('Team')
        self.store.update_player(self.ids[0], '選手☆', team['id'])
        self.race()
        first = self.store.submit_result('race', self.ids)
        state = self.store.snapshot()
        self.store.close()
        self.store = TrackerStore(self.path)
        self.assertEqual(state, self.store.snapshot())
        self.assertEqual(first, self.store.submit_result('race', self.ids))
        self.assertEqual(first, self.race())
        self.assertEqual(sum(self.totals().values()), 82)
        with self.assertRaises(ValueError):
            self.store.submit_result('race', list(reversed(self.ids)))
        self.assertEqual(state, self.store.snapshot())

    def test_team_reassignment_uses_existing_points(self):
        self.race()
        self.store.submit_result('race', self.ids)
        first = self.store.create_team('First')
        second = self.store.create_team('Second')
        self.store.update_player(self.ids[0], 'Renamed', first['id'])
        self.store.update_player(self.ids[1], 'Player 1', first['id'])
        self.assertEqual([t['points'] for t in self.store.snapshot()['teams']], [27, 0])
        self.store.update_player(self.ids[0], 'Renamed', second['id'])
        self.assertEqual([t['points'] for t in self.store.snapshot()['teams']], [12, 15])
        self.store.update_player(self.ids[0], 'Renamed', None)
        self.assertEqual([t['points'] for t in self.store.snapshot()['teams']], [12, 0])
        self.assertEqual(sum(self.totals().values()), 82)

    def test_reset_preserves_clock_history_and_pending_ownership(self):
        recording = self.store.create_recording('OBS recording')
        team = self.store.create_team('Team')
        self.store.update_player(self.ids[0], 'Player 0', team['id'])
        old = self.store.snapshot()['session']
        self.store.create_race('race', 'Course', recording['id'], 123456)
        scored = self.store.submit_result('race', self.ids)
        self.store.create_race('pending', 'Next', recording['id'], 234567)
        new = self.store.reset(old['id'])
        self.assertNotEqual(new['id'], old['id'])
        self.assertEqual(sum(self.totals().values()), 0)
        archived = self.store.submit_result('race', self.ids)
        self.assertEqual(archived['revision'], scored['revision'])
        self.assertEqual([p['player_id'] for p in archived['placements']], self.ids)
        self.assertTrue(all(p['player_active'] for p in archived['placements']))
        self.assertEqual(archived, self.store.create_race('race', 'Course', recording['id'], 123456))
        self.store.submit_result('pending', self.ids)
        self.store.correct_result('race', list(reversed(self.ids)))
        state = self.store.snapshot()
        self.assertEqual(sum(self.totals().values()), 0)
        self.assertEqual(state['recordings'], [recording])
        self.assertEqual(state['sessions'], [old, new])
        self.assertEqual([r['offset_ms'] for r in state['races']], [123456, 234567])
        self.assertTrue(all(r['session_id'] == old['id'] for r in state['races']))
        self.assertEqual(state['players'], [])
        self.assertEqual(state['teams'], [])
        self.assertEqual(state['archived_players'][0]['team_id'], team['id'])
        self.assertEqual(len(state['archived_players']), 12)
        self.assertEqual(len(state['folders']), 2)
        with self.assertRaises(ValueError):
            self.store.reset(old['id'])
        self.assertEqual(state, self.store.snapshot())
        self.race('new')
        with self.assertRaises(ValueError):
            self.store.submit_result('new', self.ids)
        fresh_ids = [self.store.add_player(f'Player {i}')['id'] for i in range(12)]
        self.store.submit_result('new', fresh_ids)
        self.assertEqual(sum(self.totals().values()), 82)

    def test_correction_retains_audit_and_replaces_not_adds(self):
        self.race()
        with self.assertRaises(ValueError):
            self.store.correct_result('race', self.ids)
        original = self.store.submit_result('race', self.ids)
        corrected = self.store.correct_result('race', list(reversed(self.ids)))
        self.assertEqual(corrected['revision'], 2)
        self.assertEqual(list(self.totals().values()), list(reversed(POINTS)))
        self.assertEqual(sum(self.totals().values()), 82)
        self.assertEqual(corrected, self.store.correct_result('race', list(reversed(self.ids))))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM result_revisions').fetchone()[0], 2)
            rows = connection.execute('SELECT player_id FROM placements WHERE revision=1 ORDER BY position').fetchall()
        self.assertEqual([r[0] for r in rows], [p['player_id'] for p in original['placements']])

    def test_result_validation_is_atomic(self):
        self.race()
        state = self.store.snapshot()
        invalid = [[], self.ids[:-1], self.ids + [self.ids[0]], [self.ids[0]] * 12,
                   'not a list', [None] + self.ids[1:], [{}] + self.ids[1:]]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.store.submit_result('race', result)
            self.assertEqual(state, self.store.snapshot())
        with self.assertRaises(KeyError):
            self.store.submit_result('race', self.ids[:-1] + ['missing'])
        with self.assertRaises(KeyError):
            self.store.submit_result('missing-race', self.ids)
        self.assertEqual(state, self.store.snapshot())
        self.store.submit_result('race', self.ids)
        state = self.store.snapshot()
        with self.assertRaises(KeyError):
            self.store.correct_result('race', ['unknown'] + self.ids[1:])
        self.assertEqual(state, self.store.snapshot())

    def test_database_failure_rolls_back_whole_result(self):
        self.race()
        before = self.store.snapshot()
        real_write = self.store._write
        def fail_mid_result(sql, values=()):
            if sql.startswith('INSERT INTO placements') and values[-1] == 6:
                raise sqlite3.OperationalError('simulated write failure')
            return real_write(sql, values)
        with patch.object(self.store, '_write', side_effect=fail_mid_result):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.submit_result('race', self.ids)
        self.assertEqual(before, self.store.snapshot())
        self.assertEqual(self.store._rows('SELECT * FROM result_revisions'), [])
        self.assertEqual(self.store._rows('SELECT * FROM placements'), [])
        self.store.submit_result('race', self.ids)
        self.assertEqual(sum(self.totals().values()), 82)

    def test_unicode_duplicate_names_and_ids(self):
        a = self.store.add_player('日本語 ☆')
        b = self.store.add_player('日本語 ☆')
        self.assertNotEqual(a['id'], b['id'])
        UUID(a['id'])
        self.assertEqual(a['name'], '日本語 ☆')
        with self.assertRaises(ValueError):
            self.store.add_player('Different', a['id'])
        self.assertEqual(self.store.snapshot()['players'][-2]['name'], '日本語 ☆')

    def test_invalid_names_and_missing_team_do_not_mutate(self):
        state = self.store.snapshot()
        for name in [None, 42, '', '  ', 'x' * 201, 'line\nbreak']:
            for operation in [self.store.add_player, self.store.create_team, self.store.create_recording]:
                with self.subTest(name=name, operation=operation), self.assertRaises(ValueError):
                    operation(name)
        with self.assertRaises(KeyError):
            self.store.update_player(self.ids[0], 'Changed', 'unknown')
        with self.assertRaises(KeyError):
            self.store.update_player('unknown', 'Changed', None)
        self.assertEqual(state, self.store.snapshot())

    def test_race_metadata_validation_and_unknown_clock(self):
        recording = self.store.create_recording('OBS', 'recording')
        self.assertEqual(recording, self.store.create_recording('OBS', 'recording'))
        with self.assertRaises(ValueError):
            self.store.create_recording('Other', 'recording')
        for offset in [-1, True, 2.5, '123', 2**63]:
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.store.create_race('invalid', 'Course', 'recording', offset)
        with self.assertRaises(ValueError):
            self.store.create_race('invalid', 'Course', offset_ms=0)
        with self.assertRaises(KeyError):
            self.store.create_race('invalid', 'Course', 'unknown', 0)
        self.assertEqual(self.store.snapshot()['races'], [])
        self.assertIsNone(self.race()['offset_ms'])
        with self.assertRaises(ValueError):
            self.store.create_race('race', 'Different')
        self.assertIsNone(self.store.create_race('unknown-clock', 'Course', 'recording')['offset_ms'])
        self.assertEqual(self.store.create_race('zero', 'Course', 'recording', 0)['offset_ms'], 0)

    def test_connection_can_be_used_by_serialized_request_threads(self):
        errors = []
        def request():
            try:
                self.race()
                self.store.submit_result('race', self.ids)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=request)
        thread.start()
        thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(self.totals().values()), 82)

    def test_foreign_keys_and_database_integrity(self):
        self.assertEqual(self.store._rows('PRAGMA foreign_keys')[0]['foreign_keys'], 1)
        self.race()
        self.store.submit_result('race', self.ids)
        self.assertEqual(self.store._rows('PRAGMA integrity_check'), [{'integrity_check': 'ok'}])
        self.assertEqual(self.store._rows('PRAGMA foreign_key_check'), [])


if __name__ == '__main__':
    unittest.main()
