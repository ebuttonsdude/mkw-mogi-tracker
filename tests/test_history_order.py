"""Manual display order is independent of scoring and recording chronology."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_store import WWStore


class HistoryOrderTests(unittest.TestCase):
    def test_order_is_scoped_persistent_and_conflict_checked(self):
        for cls in (TrackerStore, WWStore):
            with self.subTest(store=cls), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'history.sqlite'
                store = cls(path)
                try:
                    session = store.active_session()
                    folder = session['folder_id']
                    for key in ('a', 'b'):
                        store.create_race(key, key)
                    store.create_session('Other', session['id'])
                    store.create_race('foreign', 'Foreign')
                    store.reorder_history(folder, ['b', 'a'], ['a', 'b'])
                    for invalid in (['a', 'a'], ['a'], ['a', 'foreign'], [None, {}]):
                        with self.assertRaises(ValueError):
                            store.reorder_history(folder, invalid, ['b', 'a'])
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        store.reorder_history(folder, ['a', 'b'], ['a', 'b'])
                    store.create_race('new', 'New', folder_id=folder)
                finally:
                    store.close()
                store = cls(path)
                try:
                    races = store.snapshot()['races']
                    self.assertEqual([(r['id'], r['history_order']) for r in races],
                                     [('a', 2), ('b', 1), ('foreign', 1), ('new', 3)])
                finally:
                    store.close()

    def test_upgrade_old_database_preserves_insertion_order(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'history.sqlite'
            store = TrackerStore(path)
            try:
                for key in ('z', 'a'):
                    store.create_race(key, key)
            finally:
                store.close()
            db = sqlite3.connect(path)
            try:
                db.execute('ALTER TABLE races DROP COLUMN history_order')
            finally:
                db.close()
            store = TrackerStore(path)
            try:
                self.assertEqual([(r['id'], r['history_order']) for r in store.snapshot()['races']],
                                 [('z', 1), ('a', 2)])
            finally:
                store.close()
