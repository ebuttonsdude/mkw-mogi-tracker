"""Whole-project migration regressions using tiny generated local evidence."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from mkw_tracker.migrate import migrate
from tests.test_transfers import populate, png


class MigrationScaleTests(unittest.TestCase):
    def test_whole_migration_streams_more_than_4095_images_without_manual_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, target = root / 'source', root / 'target'
            data = project / '.local/tracker'
            store = populate(data)
            try:
                picture = png()
                with store._db:
                    for _ in range(4096):
                        name = str(uuid4()) + '.png'
                        (data / 'evidence' / name).write_bytes(picture)
                        store._db.execute('INSERT INTO race_evidence VALUES (?,?,?,?,?,?)',
                                          (str(uuid4()), 'same-race-id', name, 'results', None, ''))
            finally:
                store.close()
            database = data / 'tracker.sqlite3'
            before = hashlib.sha256(database.read_bytes()).digest()
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('Evidence must not accumulate in memory')):
                preview = migrate(project, target, categories={'sessions', 'evidence'})
                self.assertFalse(target.exists())
                self.assertEqual(preview['evidence_files'], 4097)
                self.assertEqual(preview['evidence_bytes'], len(picture) * 4097)
                result = migrate(project, target, categories={'sessions', 'evidence'}, apply=True)
            self.assertTrue(result['applied'])
            self.assertEqual(len(list((target / 'evidence').glob('*.png'))), 4097)
            with sqlite3.connect(target / 'tracker.sqlite3') as check:
                self.assertEqual(check.execute('SELECT COUNT(*) FROM race_evidence').fetchone()[0], 4097)
                self.assertEqual(check.execute('SELECT COUNT(*) FROM placements').fetchone()[0], 24)
                self.assertEqual(check.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(hashlib.sha256(database.read_bytes()).digest(), before)
