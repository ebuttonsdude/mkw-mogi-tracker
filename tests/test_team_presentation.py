import tempfile
from pathlib import Path
import unittest

from mkw_tracker.store import TrackerStore
from mkw_tracker.themes import resolve_theme, team_color_map


class TeamPresentationTests(unittest.TestCase):
    def test_custom_color_is_scoped_persistent_and_resettable_without_score_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'scores.sqlite'
            store = TrackerStore(database)
            try:
                ids = [store.add_player(f'Player {i}')['id'] for i in range(12)]
                team = store.create_team('My team')
                store.update_player(ids[0], 'Player 0', team['id'])
                store.create_race('r', 'Rainbow Road')
                store.submit_result('r', ids)
                old = store.snapshot()
                self.assertEqual(store.update_team(team['id'], color='#A1B2C3')['color'], '#a1b2c3')
                self.assertEqual(store.rename_team(team['id'], 'Renamed')['color'], '#a1b2c3')
                state = store.snapshot()
                self.assertEqual(state['room'], old['room'])
                self.assertEqual(state['races'], old['races'])
                colors = team_color_map(state, state['session']['id'], resolve_theme('peach'))
                self.assertEqual(colors[team['id']], '#a1b2c3')
                first_session = state['session']
                new = store.create_session('Next', first_session['id'])
                with self.assertRaises(ValueError):
                    store.update_team(team['id'], color='#000000')
                store.load_session(first_session['folder_id'], new['id'])
                self.assertEqual(store.snapshot()['teams'][0]['color'], '#a1b2c3')
            finally:
                store.close()
            store = TrackerStore(database)
            try:
                self.assertEqual(store.snapshot()['teams'][0]['color'], '#a1b2c3')
                store.update_team(team['id'], color=None)
                state = store.snapshot()
                colors = team_color_map(state, state['session']['id'], resolve_theme())
                self.assertEqual(colors[team['id']], resolve_theme()['team_colors'][0])
                self.assertEqual(store._rows('PRAGMA foreign_key_check'), [])
            finally:
                store.close()

    def test_invalid_color_updates_are_atomic(self):
        store = TrackerStore(':memory:')
        try:
            team = store.create_team('Name')
            before = store.snapshot()
            for value in ('red', '#abc', '#gg0000', 'url(https://example.com)', True, [], {}, 0):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.update_team(team['id'], name='Should roll back', color=value)
                self.assertEqual(store.snapshot(), before)
            with self.assertRaises(ValueError):
                store.update_team(team['id'])
        finally:
            store.close()

    def test_existing_schema_adds_color_without_modifying_results(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'scores.sqlite'
            store = TrackerStore(database)
            try:
                store.create_team('Old team')
                before = store.snapshot()
                store._write('ALTER TABLE teams DROP COLUMN color')
                store._db.commit()
            finally:
                store.close()
            store = TrackerStore(database)
            try:
                self.assertEqual(store.snapshot(), before)
            finally:
                store.close()
