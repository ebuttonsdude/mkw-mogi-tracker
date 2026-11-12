from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.store import TrackerStore


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'scores.sqlite'
        self.store = TrackerStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scored_race(self):
        ids = [self.store.add_player(f'Player {i}')['id'] for i in range(12)]
        self.store.create_race('race', 'Course')
        self.store.submit_result('race', ids)
        return ids

    def test_suspected_results_are_evidence_only_and_follow_verified_results(self):
        self.store.create_race('suspected', 'SNES Ghost Valley 2')
        files = ['00000000-0000-0000-0000-00000000000' + str(i) + '.png' for i in range(3)]
        self.store.add_evidence('suspected', files[0], kind='prerace')
        self.store.add_evidence('suspected', files[1], kind='suspected_results',
                                label='Suspected results; finishing order unverified')
        state = self.store.snapshot()
        self.assertEqual(state['room']['points'], 0)
        self.assertEqual(state['players'], [])
        self.assertEqual(state['races'][0]['status'], 'pending')
        self.assertEqual(state['races'][0]['evidence'][0]['kind'], 'suspected_results')
        self.store.add_evidence('suspected', files[2], kind='results')
        self.assertEqual([e['kind'] for e in self.store.snapshot()['races'][0]['evidence']],
                         ['results', 'suspected_results', 'prerace'])
        self.store.close()
        self.store = TrackerStore(self.path)
        self.assertEqual(self.store.snapshot()['races'][0]['evidence'][1]['kind'], 'suspected_results')

    def test_reset_keeps_history_readable_and_late_review_roster_empty(self):
        ids = self.scored_race()
        team = self.store.create_team('Pair')
        self.store.update_player(ids[0], '選手☆', team['id'])
        self.store.create_race('late', 'Next')
        old = self.store.snapshot()['session']
        self.store.reset(old['id'])
        self.store.review_result('late', [{'name': f'後 {i}'} for i in range(12)])
        state = self.store.snapshot()
        self.assertEqual(state['players'], [])
        self.assertEqual(state['teams'], [])
        self.assertEqual(len(state['archived_players']), 24)
        self.assertEqual(state['races'][0]['placements'][0]['player_name'], '選手☆')
        self.assertTrue(state['races'][0]['placements'][0]['player_active'])
        self.assertEqual([f['race_count'] for f in state['folders']], [2, 0])
        self.assertEqual(self.store.submit_result('race', ids)['revision'], 1)

    def test_delete_player_preserves_identity_and_does_not_transfer_totals(self):
        ids = self.scored_race()
        self.store.delete_player(ids[0])
        replacement = self.store.add_player('Player 0')
        state = self.store.snapshot()
        self.assertNotEqual(replacement['id'], ids[0])
        self.assertEqual(state['players'][-1]['points'], 0)
        self.assertEqual(state['archived_players'][0]['id'], ids[0])
        self.assertEqual(state['races'][0]['placements'][0]['player_name'], 'Player 0')
        self.assertEqual(state['races'][0]['placements'][0]['points'], 15)
        self.assertEqual(sum(p['points'] for p in state['players']), 67)
        self.assertEqual(self.store.submit_result('race', ids)['revision'], 1)

    def test_delete_team_unassigns_active_players_and_rename(self):
        ids = self.scored_race()
        team = self.store.create_team('Typo')
        self.assertEqual(self.store.rename_team(team['id'], 'Pair')['name'], 'Pair')
        self.store.update_player(ids[0], 'Player 0', team['id'])
        self.store.delete_team(team['id'])
        state = self.store.snapshot()
        self.assertEqual(state['teams'], [])
        self.assertIsNone(state['players'][0]['team_id'])
        self.assertEqual(state['players'][0]['points'], 15)
        self.assertEqual(state['archived_teams'][0]['name'], 'Pair')

    def test_folders_move_and_reset_preserve_scoring_ownership(self):
        self.scored_race()
        old = self.store.snapshot()['session']
        folder = self.store.create_folder('Match')
        self.assertEqual(self.store.rename_folder(folder['id'], '2v2')['name'], '2v2')
        with self.assertRaises(ValueError):
            self.store.move_race('race', folder['id'])
        self.assertEqual(self.store.move_race('race', old['folder_id'])['session_id'], old['id'])
        self.assertEqual(sum(p['points'] for p in self.store.snapshot()['players']), 82)
        self.store.update_race('race', 'Correct course')
        self.store.reset(old['id'])
        state = self.store.snapshot()
        self.assertEqual(state['races'][0]['folder_id'], old['folder_id'])
        self.assertEqual(state['races'][0]['course'], 'Correct course')
        self.assertEqual([f['race_count'] for f in state['folders']], [1, 0, 0])
        new = self.store.create_race('new', 'Next')
        self.assertEqual(new['folder_id'], state['session']['folder_id'])
        self.assertTrue(new['created_at'])

    def test_invalid_actions_are_atomic(self):
        ids = self.scored_race()
        team = self.store.create_team('Pair')
        folder = self.store.create_folder('Match')
        self.store.delete_player(ids[0])
        self.store.delete_team(team['id'])
        before = self.store.snapshot()
        actions = [lambda: self.store.delete_player('missing'),
                   lambda: self.store.delete_player(ids[0]),
                   lambda: self.store.delete_team(team['id']),
                   lambda: self.store.rename_team(team['id'], 'New'),
                   lambda: self.store.rename_folder(folder['id'], ''),
                   lambda: self.store.move_race('race', 'missing'),
                   lambda: self.store.move_race('missing', folder['id']),
                   lambda: self.store.update_player(ids[1], 'Name', team['id']),
                   lambda: self.store.reset('wrong')]
        for action in actions:
            with self.assertRaises((KeyError, ValueError)):
                action()
            self.assertEqual(before, self.store.snapshot())

    def test_reset_write_failure_rolls_back_archiving_and_folder(self):
        self.scored_race()
        self.store.create_team('Pair')
        before = self.store.snapshot()
        original = self.store._write

        def fail_new_session(sql, values=()):
            if sql.startswith('INSERT INTO sessions'):
                raise sqlite3.OperationalError('simulated failure')
            return original(sql, values)

        with patch.object(self.store, '_write', side_effect=fail_new_session):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.reset(before['session']['id'])
        self.assertEqual(before, self.store.snapshot())

    def test_current_results_reject_unrelated_archived_identity(self):
        archived = self.store.add_player('Old')
        self.store.delete_player(archived['id'])
        ids = self.scored_race()
        before = self.store.snapshot()
        with self.assertRaises(ValueError):
            self.store.correct_result('race', [archived['id']] + ids[1:])
        self.assertEqual(before, self.store.snapshot())
        self.store.delete_player(ids[0])
        # Explicit corrections may retain an identity already in this race.
        self.assertEqual(self.store.correct_result('race', list(reversed(ids)))['revision'], 2)

    def test_existing_schema_migrates_once_without_losing_results(self):
        self.store.close()
        self.path.unlink()
        # Exact pre-folder schema, including completed and pending results.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''
                CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at TEXT NOT NULL);
                CREATE TABLE current_session(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    session_id TEXT NOT NULL REFERENCES sessions(id));
                CREATE TABLE teams(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE players(id TEXT PRIMARY KEY, name TEXT NOT NULL, team_id TEXT REFERENCES teams(id));
                CREATE TABLE recordings(id TEXT PRIMARY KEY, label TEXT NOT NULL, started_at TEXT NOT NULL);
                CREATE TABLE races(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    course TEXT NOT NULL, recording_id TEXT REFERENCES recordings(id),
                    offset_ms INTEGER CHECK(offset_ms >= 0), revision INTEGER NOT NULL DEFAULT 0,
                    CHECK(offset_ms IS NULL OR recording_id IS NOT NULL));
                CREATE TABLE result_revisions(race_id TEXT NOT NULL REFERENCES races(id),
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(race_id, revision));
                CREATE TABLE placements(race_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    player_id TEXT NOT NULL REFERENCES players(id),
                    position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 12),
                    PRIMARY KEY(race_id, revision, position), UNIQUE(race_id, revision, player_id),
                    FOREIGN KEY(race_id, revision) REFERENCES result_revisions(race_id, revision));
                INSERT INTO sessions VALUES ('old', '2026-01-01');
                INSERT INTO sessions VALUES ('current', '2026-01-02');
                INSERT INTO current_session VALUES (1, 'current');
                INSERT INTO teams VALUES ('t', 'Team');
                INSERT INTO recordings VALUES ('rec', 'OBS', '2026-01-01');
                INSERT INTO races VALUES ('completed', 'old', 'Course', 'rec', 12000, 1);
                INSERT INTO races VALUES ('pending', 'current', 'Next', 'rec', 20000, 0);
                INSERT INTO result_revisions VALUES ('completed', 1, '2026-01-01');
            ''')
            for i in range(12):
                db.execute('INSERT INTO players VALUES (?, ?, ?)', (f'p{i}', f'選手 {i}', 't'))
                db.execute('INSERT INTO placements VALUES (?, ?, ?, ?)', ('completed', 1, f'p{i}', i+1))
        self.store = TrackerStore(self.path)
        state = self.store.snapshot()
        self.assertEqual(len(state['folders']), 2)
        self.assertEqual([f['race_count'] for f in state['folders']], [1, 1])
        self.assertEqual(state['session']['id'], 'current')
        self.assertEqual(state['races'][0]['offset_ms'], 12000)
        self.assertEqual(state['races'][0]['placements'][0]['player_name'], '選手 0')
        self.assertEqual(sum(p['points'] for p in state['players']), 0)
        self.assertEqual(self.store._rows('PRAGMA foreign_key_check'), [])
        self.store.close()
        self.store = TrackerStore(self.path)
        self.assertEqual(state, self.store.snapshot())


if __name__ == '__main__':
    unittest.main()
