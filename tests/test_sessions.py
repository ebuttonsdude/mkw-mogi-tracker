import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from mkw_tracker.store import TrackerStore


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TrackerStore(Path(self.temp.name) / 'scores.sqlite')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scored(self):
        ids = [self.store.add_player(str(i))['id'] for i in range(12)]
        self.store.create_race('r', 'Course')
        self.store.submit_result('r', ids)
        return ids

    def test_creates_reject_stale_context_without_mutation(self):
        old = self.store.snapshot()['session']
        current = self.store.create_session('Other tab', old['id'])
        actions = (
            lambda expected: self.store.add_player('Draft player', expected_session_id=expected),
            lambda expected: self.store.create_team('Draft team', expected_session_id=expected),
            lambda expected: self.store.create_race('draft', 'Course', expected_session_id=expected),
        )
        before = self.store.snapshot()
        for action in actions:
            for expected in (None, old['id'], '', 42):
                with self.subTest(action=action, expected=expected), self.assertRaisesRegex(ValueError, 'session changed'):
                    action(expected)
                self.assertEqual(self.store.snapshot(), before)
            self.assertEqual(action(current['id'])['session_id'], current['id'])
            before = self.store.snapshot()

    def test_race_destination_and_request_context_are_independent(self):
        old = self.store.snapshot()['session']
        current = self.store.create_session('Current', old['id'])
        with self.assertRaisesRegex(ValueError, 'session changed'):
            self.store.create_race('stale', 'Course', folder_id=old['folder_id'],
                                   expected_session_id=old['id'])
        race = self.store.create_race('historical', 'Course', folder_id=old['folder_id'],
                                      expected_session_id=current['id'])
        self.assertEqual(race['session_id'], old['id'])
        # Internal capture callers need no new session lookup or context argument.
        from unittest.mock import patch
        with patch.object(self.store, '_expect_session', side_effect=AssertionError('unexpected check')):
            self.store.create_race('capture', 'Course', session_id=old['id'])
            self.store.add_player('Internal player')
            self.store.create_team('Internal team')

    def test_folder_has_exactly_one_database_owner_after_reopen(self):
        import sqlite3
        path = Path(self.temp.name) / 'scores.sqlite'
        for reopened in (False, True):
            if reopened:
                self.store.close()
                self.store = TrackerStore(path)
            state = self.store.snapshot()
            with self.assertRaises(sqlite3.IntegrityError), self.store._transaction():
                self.store._write('INSERT INTO sessions(id,started_at,folder_id) VALUES(?,?,?)',
                                  (str(uuid4()), 'now', state['session']['folder_id']))
            self.assertEqual(state, self.store.snapshot())
            self.assertEqual(self.store._rows('PRAGMA foreign_key_check'), [])

    def test_sessions_restore_roster_and_reject_cross_session_move(self):
        ids = self.scored()
        old = self.store.snapshot()['session']
        new = self.store.create_session('New', old['id'])
        self.assertEqual(self.store.snapshot()['players'], [])
        with self.assertRaises(ValueError):
            self.store.move_race('r', new['folder_id'])
        self.store.load_session(old['folder_id'], new['id'])
        self.assertEqual([p['id'] for p in self.store.snapshot()['players']], ids)
        self.assertEqual(self.store.snapshot()['room']['points'], 82)

    def test_penalties_deduct_without_changing_room_gross_or_base_points(self):
        ids = self.scored()
        penalty = self.store.add_penalty('r', ids[0], 4, 'Rule')
        state = self.store.snapshot()
        self.assertEqual(state['players'][0]['points'], 11)
        self.assertEqual(state['room']['points'], 82)
        self.assertEqual(state['room']['net_points'], 78)
        self.assertEqual(state['room']['logged_races'], 1)
        self.assertEqual(state['races'][0]['placements'][0]['points'], 15)
        self.assertEqual(state['races'][0]['placements'][0]['net_points'], 11)
        extra = self.store.add_player('other')['id']
        with self.assertRaises(ValueError):
            self.store.correct_result('r', [extra] + ids[1:])
        for invalid in (0, -1, True, 1.5, 1000001):
            with self.assertRaises(ValueError):
                self.store.add_penalty('r', ids[0], invalid)
        self.store.remove_penalty(penalty['id'])
        self.assertEqual(self.store.snapshot()['players'][0]['points'], 15)

    def test_evidence_and_folder_deletion_preserve_recordings(self):
        ids = self.scored()
        recording = self.store.create_recording('Video')
        filename = str(uuid4()) + '.png'
        evidence = self.store.add_evidence('r', filename, 'results', 100)
        self.assertEqual(evidence, self.store.add_evidence('r', filename, 'results', 100))
        self.assertEqual(self.store.snapshot()['races'][0]['evidence_url'], '/evidence/' + filename)
        self.store.add_penalty('r', ids[0], 1)
        old = self.store.snapshot()['session']
        deleted = self.store.delete_folder(old['folder_id'], old['id'])
        self.assertEqual(deleted['deleted_race_ids'], ['r'])
        state = self.store.snapshot()
        self.assertEqual(state['players'], [])
        self.assertEqual(state['races'], [])
        self.assertEqual(state['recordings'], [recording])
        self.assertEqual(self.store._rows('PRAGMA foreign_key_check'), [])

    def test_delete_race_removes_revisions_penalties_and_proof_but_keeps_roster(self):
        ids = self.scored()
        self.store.correct_result('r', list(reversed(ids)))
        self.store.add_penalty('r', ids[0], 4, 'Linked deduction')
        self.store.add_evidence('r', str(uuid4()) + '.png')
        recording = self.store.create_recording('Keep this original recording')
        self.store.update_race('r', 'Course', recording['id'], 0)
        self.store.create_race('keep', 'Rainbow Road', recording['id'], 1000)
        self.store.submit_result('keep', ids)
        self.store.create_race('pending', 'Crown City')
        old_session = self.store.snapshot()['session']
        deleted = self.store.delete_race('r')
        self.assertEqual(deleted['deleted_race_ids'], ['r'])
        self.assertEqual(len(deleted['deleted_evidence_files']), 1)
        state = self.store.snapshot()
        self.assertEqual(state['session'], old_session)
        self.assertEqual([p['id'] for p in state['players']], ids)
        self.assertEqual(state['recordings'], [recording])
        self.assertEqual(state['room']['points'], 82)
        self.assertEqual(state['room']['penalty_points'], 0)
        self.assertEqual(state['room']['logged_races'], 2)
        for table in ('placements', 'result_revisions', 'penalties', 'race_evidence'):
            self.assertEqual(self.store._rows(f'SELECT * FROM {table} WHERE race_id=?', ('r',)), [])
        self.assertEqual(self.store._rows('PRAGMA foreign_key_check'), [])
        self.store.delete_race('pending')
        self.assertEqual(self.store.snapshot()['room']['logged_races'], 1)
        before = self.store.snapshot()
        with self.assertRaises(KeyError):
            self.store.delete_race('missing')
        self.assertEqual(self.store.snapshot(), before)

    def test_delete_race_failure_rolls_back_all_dependent_records(self):
        import sqlite3
        from unittest.mock import patch
        ids = self.scored()
        self.store.correct_result('r', list(reversed(ids)))
        self.store.add_penalty('r', ids[0], 1)
        self.store.add_evidence('r', str(uuid4()) + '.png')
        before = self.store.snapshot()
        original = self.store._write

        def fail(sql, values=()):
            if sql.startswith('DELETE FROM races'):
                raise sqlite3.OperationalError('simulated final delete failure')
            return original(sql, values)

        with patch.object(self.store, '_write', side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            self.store.delete_race('r')
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(self.store._rows('SELECT * FROM result_revisions WHERE race_id=?', ('r',))), 2)

    def test_cross_session_identities_and_race_folder_mismatch_rejected(self):
        ids = self.scored()
        old = self.store.snapshot()['session']
        old_team = self.store.create_team('Team')
        new = self.store.create_session('New', old['id'])
        fresh = [self.store.add_player('new ' + str(i))['id'] for i in range(12)]
        self.store.create_race('new', 'Course')
        before = self.store.snapshot()
        for action in (
            lambda: self.store.submit_result('new', ids),
            lambda: self.store.review_result('new', [{'player_id': value} for value in ids]),
            lambda: self.store.correct_result('r', fresh),
            lambda: self.store.update_player(fresh[0], 'new', old_team['id']),
            lambda: self.store.delete_player(ids[0]),
            lambda: self.store.create_race('mismatch', 'Course', folder_id=old['folder_id'], session_id=new['id']),
        ):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(self.store.snapshot(), before)
        race = self.store.create_race('folder-only', 'Course', folder_id=old['folder_id'])
        self.assertEqual(race['session_id'], old['id'])

    def test_historical_review_roster_available_when_loaded(self):
        old = self.store.snapshot()['session']
        self.store.create_race('pending', 'Course')
        current = self.store.create_session('New', old['id'])
        self.store.review_result('pending', [{'name': str(i)} for i in range(12)])
        self.assertEqual(self.store.snapshot()['players'], [])
        self.store.load_session(old['folder_id'], current['id'])
        self.assertEqual(len(self.store.snapshot()['players']), 12)
        self.assertEqual(self.store.snapshot()['room']['points'], 82)

    def test_deleted_player_penalty_still_affects_room_and_evidence_validation(self):
        ids = self.scored()
        self.store.add_penalty('r', ids[0], 20)
        self.store.delete_player(ids[0])
        state = self.store.snapshot()
        self.assertEqual(state['room']['net_points'], 62)
        self.assertEqual(state['room']['penalty_points'], 20)
        for filename in ('../bad.png', 'bad.png', str(uuid4()) + '.jpg', 'a' * 36 + '.png'):
            with self.assertRaises(ValueError):
                self.store.add_evidence('r', filename)
        self.assertEqual(state, self.store.snapshot())
        self.store.add_evidence('r', str(uuid4()) + '.png', 'prerace')
        result = self.store.add_evidence('r', str(uuid4()) + '.png', 'results')
        self.assertEqual(self.store.snapshot()['races'][0]['evidence_url'], result['url'])


class MigrationTests(unittest.TestCase):
    def legacy(self, path):
        import sqlite3
        from contextlib import closing
        # Build the immediately preceding folder schema, including shared IDs,
        # two revisions, a moved race, an empty folder, and an unscored roster.
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''
                CREATE TABLE history_folders(id TEXT PRIMARY KEY,name TEXT,created_at TEXT,lounge_id TEXT);
                CREATE TABLE sessions(id TEXT PRIMARY KEY,started_at TEXT,folder_id TEXT REFERENCES history_folders(id));
                CREATE TABLE current_session(singleton INTEGER PRIMARY KEY,session_id TEXT REFERENCES sessions(id));
                CREATE TABLE teams(id TEXT PRIMARY KEY,name TEXT,active INTEGER DEFAULT 1);
                CREATE TABLE players(id TEXT PRIMARY KEY,name TEXT,team_id TEXT REFERENCES teams(id),active INTEGER DEFAULT 1);
                CREATE TABLE recordings(id TEXT PRIMARY KEY,label TEXT,started_at TEXT);
                CREATE TABLE races(id TEXT PRIMARY KEY,session_id TEXT REFERENCES sessions(id),course TEXT,
                    recording_id TEXT,offset_ms INTEGER,revision INTEGER,folder_id TEXT REFERENCES history_folders(id),created_at TEXT);
                CREATE TABLE result_revisions(race_id TEXT REFERENCES races(id),revision INTEGER,created_at TEXT,PRIMARY KEY(race_id,revision));
                CREATE TABLE placements(race_id TEXT,revision INTEGER,player_id TEXT REFERENCES players(id),position INTEGER,
                    PRIMARY KEY(race_id,revision,position),UNIQUE(race_id,revision,player_id),
                    FOREIGN KEY(race_id,revision) REFERENCES result_revisions(race_id,revision));
                INSERT INTO history_folders VALUES('oldfolder','Old','1',NULL),('currentfolder','Current','2',NULL),('movedfolder','Moved','3','1146183');
                INSERT INTO sessions VALUES('old','1','oldfolder'),('current','2','currentfolder');
                INSERT INTO current_session VALUES(1,'current');
                INSERT INTO teams VALUES('team','Team',1),('emptyteam','Empty team',1);
                INSERT INTO players VALUES('unscored','Unscored',NULL,1),('deleted','Deleted',NULL,0);
                INSERT INTO races VALUES('oldrace','old','A',NULL,NULL,2,'oldfolder','1'),
                    ('currentrace','current','B',NULL,NULL,1,'currentfolder','2'),
                    ('movedrace','current','C',NULL,NULL,1,'movedfolder','3');
                INSERT INTO result_revisions VALUES('oldrace',1,'1'),('oldrace',2,'2'),('currentrace',1,'2'),('movedrace',1,'3');
            ''')
            for i in range(12):
                db.execute('INSERT INTO players VALUES(?,?,?,?)', (f'p{i}', f'Player{i}', 'team', int(i != 11)))
                for race, revision in [('oldrace', 1), ('oldrace', 2), ('currentrace', 1), ('movedrace', 1)]:
                    db.execute('INSERT INTO placements VALUES(?,?,?,?)', (race, revision, f'p{i}', i+1))

    def test_all_revisions_folder_ownership_clones_backup_and_restart(self):
        from contextlib import closing
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.sqlite'
            self.legacy(path)
            store = TrackerStore(path)
            try:
                state = store.snapshot()
                self.assertEqual(len(state['sessions']), 3)
                self.assertTrue(state['migration_notice'])
                self.assertEqual(len(store._rows('SELECT * FROM placements')), 48)
                self.assertEqual(len(state['players']), 12)  # 11 active scored + unscored
                self.assertEqual(state['players'][0]['id'], 'unscored')
                self.assertEqual(state['teams'][0]['id'], 'team')
                self.assertEqual(state['teams'][1]['id'], 'emptyteam')
                for race in state['races']:
                    for entry in race['placements']:
                        player = store._get('players', entry['player_id'])
                        self.assertEqual(player['session_id'], race['session_id'])
                        if player['team_id']:
                            self.assertEqual(store._get('teams', player['team_id'])['session_id'], race['session_id'])
                current_ids = [e['player_id'] for e in state['races'][1]['placements']]
                self.assertEqual(current_ids, [f'p{i}' for i in range(12)])
                self.assertEqual(store._rows('PRAGMA foreign_key_check'), [])
                backup = Path(str(path) + '.pre-session-migration.bak')
                self.assertTrue(backup.exists())
                original_backup = backup.read_bytes()
                with closing(sqlite3.connect(backup)) as db:
                    self.assertNotIn('session_id', [r[1] for r in db.execute('PRAGMA table_info(players)')])
                store.close()
                store = TrackerStore(path)
                self.assertEqual(state, store.snapshot())
                self.assertEqual(backup.read_bytes(), original_backup)
                store.load_session('oldfolder', 'current')
                self.assertEqual(len(store.snapshot()['players']), 12)  # historical deleted ambiguity restored
            finally:
                store.close()

    def test_failure_rolls_back_schema_and_rows_and_preserves_backup(self):
        from contextlib import closing
        import sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.sqlite'
            self.legacy(path)
            original = TrackerStore._write
            def fail(store, sql, values=()):
                if sql.startswith('UPDATE placements'):
                    raise sqlite3.OperationalError('injected clone failure')
                return original(store, sql, values)
            with patch.object(TrackerStore, '_write', fail), self.assertRaises(sqlite3.OperationalError):
                TrackerStore(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertNotIn('session_id', [r[1] for r in db.execute('PRAGMA table_info(players)')])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM placements').fetchone()[0], 48)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 2)
            store = TrackerStore(path)
            store.close()
