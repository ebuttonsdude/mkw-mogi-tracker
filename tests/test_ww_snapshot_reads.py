"""WW bulk/scoped reads preserve live derivation and cross-session statistics."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.ww_store import WWStore, ww_course_stats, ww_summary


class WWSnapshotReadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = WWStore(Path(self.directory.name) / 'ww.sqlite3')
        self.addCleanup(self.store.close)

    def race(self, identifier, course='Crown City', *, session=None, **result):
        owner = {} if session is None else {'session_id': session['id'], 'folder_id': session['folder_id']}
        self.store.create_race(identifier, course, **owner)
        if result:
            self.store.set_ww_result(identifier, **result)

    def mixed_history(self):
        first = self.store.active_session()
        self.store.set_player_name('最初のプレイヤー', first['id'])
        self.race('first', position=1, vr_before=8000, vr_after=8050,
                  source='capture', derive_from_previous=True)
        self.race('pending')
        self.race('import', 'Rainbow Road', position=8, vr_after=8040, source='import')
        second = self.store.create_session('Second', first['id'])
        self.store.set_player_name('Second player', second['id'])
        self.race('second-first', position=4, vr_after=9000, source='capture', derive_from_previous=True)
        # Session histories are interleaved in the database, not contiguous.
        self.race('old-live', 'Rainbow Road', session=first, position=2, vr_after=8090,
                  source='capture', derive_from_previous=True)
        self.race('second-live', position=3, vr_before=9020, vr_after=9030,
                  source='capture', derive_from_previous=True)
        self.race('old-manual', session=first, position=5, vr_before=8085, vr_after=8100)
        self.race('legacy-capture', session=first, position=6, vr_after=8110, source='capture')
        return first, second

    def test_bulk_matches_single_race_reads_and_remains_fresh(self):
        first, second = self.mixed_history()

        def assert_equivalent():
            ids = [row['id'] for row in self.store._rows('SELECT id FROM races ORDER BY rowid')]
            expected = [self.store._race(identifier) for identifier in ids]
            with patch.object(self.store, '_race', side_effect=AssertionError('per-race hydration')):
                self.assertEqual(self.store._races(), expected)
                for owner in (first, second):
                    self.assertEqual(self.store._races(owner['id']),
                                     [race for race in expected if race['session_id'] == owner['id']])

        assert_equivalent()
        self.store.set_ww_result('import', position=7, vr_after=8030, source='import')
        self.assertEqual(self.store._race('old-live')['ww_result']['delta'], 60)
        assert_equivalent()
        self.store.delete_race('import')
        self.assertEqual(self.store._race('old-live')['ww_result']['delta'], 40)
        assert_equivalent()
        # Finishing a previously pending race changes its later live dependents,
        # regardless of the order in which the results were confirmed.
        self.store.set_ww_result('pending', position=4, vr_after=8020, source='import')
        self.assertEqual(self.store._race('old-live')['ww_result']['delta'], 70)
        self.assertIsNone(self.store._race('legacy-capture')['ww_result']['delta'])
        assert_equivalent()
        self.store.set_ww_result('old-live', position=2, vr_before=8080, vr_after=8090)
        self.store.set_ww_result('first', position=1, vr_after=8000)
        self.assertEqual(self.store._race('old-live')['ww_result']['delta'], 10)
        assert_equivalent()

    def test_default_snapshot_preserves_all_history_and_statistics(self):
        _, current = self.mixed_history()
        races = [self.store._race(row['id']) for row in self.store._rows('SELECT id FROM races ORDER BY rowid')]
        expected = ww_summary([race for race in races if race['session_id'] == current['id']], 'Second player')
        expected['all_courses'] = ww_course_stats(races)
        with patch.object(self.store, '_race', side_effect=AssertionError('per-race hydration')):
            snapshot = self.store.snapshot()
        self.assertEqual(snapshot['races'], races)
        self.assertEqual(snapshot['ww'], expected)
        self.assertEqual(snapshot['ww_player_names'], {
            snapshot['sessions'][0]['id']: '最初のプレイヤー', current['id']: 'Second player'})
        self.assertIsNone(snapshot['room']['maximum_points'])
        self.assertIsNone(snapshot['room']['expected_races'])

    def test_scoped_archive_read_keeps_correct_name_and_optional_all_course_stats(self):
        archived, current = self.mixed_history()
        full = self.store.snapshot()
        scoped = self.store.snapshot(session_id=archived['id'], include_history=False)
        self.assertEqual(scoped['session'], archived)
        self.assertEqual(self.store.active_session(), current)
        self.assertEqual(scoped['races'], [race for race in full['races'] if race['session_id'] == archived['id']])
        self.assertEqual(scoped['ww']['player_name'], '最初のプレイヤー')
        self.assertEqual(scoped['ww']['all_courses'], full['ww']['all_courses'])
        with patch.object(self.store, '_ww_races', wraps=self.store._ww_races) as reads:
            narrow = self.store.snapshot(session_id=archived['id'], include_history=False, include_all_courses=False)
        reads.assert_called_once_with(archived['id'])
        self.assertNotIn('all_courses', narrow['ww'])
        self.assertEqual(narrow['ww'], {key: value for key, value in scoped['ww'].items() if key != 'all_courses'})

    def test_scoped_all_courses_tracks_archived_corrections_deletions_and_course_edits(self):
        archived, _ = self.mixed_history()
        for mutation in (
                lambda: self.store.set_ww_result('first', position=9, vr_after=8060),
                lambda: self.store.update_race('old-live', 'Mario Circuit'),
                lambda: self.store.delete_race('import'),
                lambda: self.store.delete_folder(archived['folder_id'])):
            mutation()
            expected = self.store.snapshot()['ww']['all_courses']
            self.assertEqual(self.store.snapshot(include_history=False)['ww']['all_courses'], expected)

    def test_bulk_query_count_does_not_grow_with_history(self):
        archived = self.store.active_session()
        self.race('old', position=2, vr_after=8000)
        self.store.create_session('Current', archived['id'])
        self.race('current', position=1, vr_after=9000)

        def count_queries(**kwargs):
            queries = []
            self.store._db.set_trace_callback(queries.append)
            try:
                snapshot = self.store.snapshot(**kwargs)
            finally:
                self.store._db.set_trace_callback(None)
            return len(queries), snapshot

        before_full, _ = count_queries()
        before_scoped, _ = count_queries(include_history=False, include_all_courses=False)
        for index in range(100):
            self.race(f'archive-{index}', session=archived, position=2, vr_after=8001 + index,
                      source='capture', derive_from_previous=True)
        after_full, full = count_queries()
        after_scoped, scoped = count_queries(include_history=False, include_all_courses=False)
        self.assertEqual(after_full, before_full)
        self.assertEqual(after_scoped, before_scoped)
        self.assertEqual(len(full['races']), 102)
        self.assertEqual([race['id'] for race in scoped['races']], ['current'])


if __name__ == '__main__':
    unittest.main()
