"""File-backed local transfers use generated PNGs and temporary databases only."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from mkw_tracker import transfers
from mkw_tracker.store import TrackerStore
from tests.test_transfers import populate, png


class LocalTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.store = populate(self.source)
        self.addCleanup(self.store.close)

    def payload(self):
        return {'format': transfers.FORMAT, 'version': 1,
                'modes': {'mogi': transfers.collect(self.store, archive_limits=False)}}

    def add_images(self, count):
        data = png()
        with self.store._db:
            for _ in range(count):
                name = str(uuid4()) + '.png'
                (self.source / 'evidence' / name).write_bytes(data)
                self.store._db.execute('INSERT INTO race_evidence VALUES (?,?,?,?,?,?)',
                                       (str(uuid4()), 'same-race-id', name, 'results', None, ''))

    def test_more_than_archive_file_count_roundtrips_without_read_bytes(self):
        self.add_images(4095)
        payload = self.payload()
        source_database = self.source / 'tracker.sqlite3'
        before = hashlib.sha256(source_database.read_bytes()).hexdigest()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Evidence must stream')):
            evidence = transfers.local_evidence(self.source, payload)
            self.assertEqual(len(evidence), 4096)
            transfers.merge(self.target, payload, evidence, archive_limits=False)
        with sqlite3.connect(self.target / 'tracker.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM race_evidence').fetchone()[0], 4096)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM result_revisions').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM placements').fetchone()[0], 24)
        self.assertEqual(hashlib.sha256(source_database.read_bytes()).hexdigest(), before)
        with self.assertRaisesRegex(ValueError, 'archive limits'):
            transfers.read_evidence(self.source, payload)

    def test_local_ignores_archive_quotas_but_zip_keeps_them(self):
        payload = self.payload()
        image_bytes = transfers.read_evidence(self.source, payload)
        with patch.multiple(transfers, MAX_FILES=1, MAX_JSON=1, MAX_IMAGE=1, MAX_ARCHIVE=1):
            rows = transfers.collect(self.store, archive_limits=False)
            self.assertEqual(rows, payload['modes']['mogi'])
            transfers.validate(payload, archive_limits=False)
            transfers.merge(self.target, payload, transfers.local_evidence(self.source, payload), archive_limits=False)
            with self.assertRaises(ValueError):
                transfers.collect(self.store)
            with self.assertRaises(ValueError):
                transfers.encode_archive(payload, image_bytes)
            with self.assertRaises(ValueError):
                transfers.decode_archive(b'zip')

    def test_more_than_thousand_sessions_and_low_sql_variable_limit(self):
        with self.store._db:
            for index in range(1001):
                identity = f'extra-{index}'
                self.store._db.execute('INSERT INTO history_folders VALUES (?,?,?,?)',
                                       (identity, identity, '2026-01-01', None))
                self.store._db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                                       (identity, '2026-01-01', identity, 'teams'))
        self.store._db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 20)
        payload = self.payload()
        self.assertEqual(len(payload['modes']['mogi']['sessions']), 1002)
        selected = [row['id'] for row in payload['modes']['mogi']['sessions']][1:]
        cursor = self.store._db.execute('SELECT * FROM sessions')
        try:
            cursor.fetchone()
            self.assertEqual(len(transfers.collect(self.store, selected, archive_limits=False)['sessions']), 1001)
        finally:
            cursor.close()
        transfers.merge(self.target, payload, transfers.local_evidence(self.source, payload), archive_limits=False)
        with sqlite3.connect(self.target / 'tracker.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1003)
        with self.assertRaises(ValueError):
            transfers.collect(self.store)
        self.assertFalse(self.store._db.in_transaction)

    def test_copy_failure_rolls_back_overwrite_rows_and_all_new_files(self):
        self.add_images(1)
        payload = self.payload()
        target = populate(self.target)
        try:
            session = target.active_session()['id']
            fingerprint = transfers.session_fingerprint(target._db, 'main', 'mogi', session, archive_limits=False)
            before = transfers.collect(target, archive_limits=False)
        finally:
            target.close()
        files = set((self.target / 'evidence').iterdir())
        evidence = transfers.local_evidence(self.source, payload)
        original = transfers._copy_local_evidence
        calls = 0
        def fail(source, output):
            nonlocal calls
            calls += 1
            original(source, output)
            if calls == 2:
                raise OSError('simulated mid-copy failure')
        incoming = payload['modes']['mogi']['sessions'][0]['id']
        with patch.object(transfers, '_copy_local_evidence', side_effect=fail), self.assertRaises(OSError):
            transfers.merge(self.target, payload, evidence, archive_limits=False,
                            replacements={'mogi': {incoming: {'id': session, 'fingerprint': fingerprint}}})
        self.assertEqual(set((self.target / 'evidence').iterdir()), files)
        target = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(transfers.collect(target, archive_limits=False), before)
        finally:
            target.close()

    def test_invalid_changed_and_symlink_evidence_rejected(self):
        payload = self.payload()
        evidence = transfers.local_evidence(self.source, payload)
        source = next(iter(evidence.values()))
        source.path.write_bytes(b'not a PNG')
        with self.assertRaises(ValueError):
            transfers.local_evidence(self.source, payload)
        with self.assertRaisesRegex(ValueError, 'changed'):
            transfers.merge(self.target, payload, evidence, archive_limits=False)
        self.assertFalse(list((self.target / 'evidence').glob('*.png')))
        source.path.unlink()
        other = self.root / 'other.png'
        other.write_bytes(png())
        source.path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'links'):
            transfers.local_evidence(self.source, payload)

    def test_local_validation_has_no_record_cap_and_uses_disk_schema(self):
        payload = self.payload()
        rows = payload['modes']['mogi']
        first = rows['race_evidence'][0]
        rows['race_evidence'] = [dict(first, id=f'evidence-{index}', filename=str(uuid4()) + '.png') for index in range(100001)]
        with self.assertRaisesRegex(ValueError, 'too many'):
            transfers.validate(deepcopy(payload))
        actual = transfers.TrackerStore
        paths = []
        def track(path):
            paths.append(path)
            return actual(path)
        with patch.object(transfers, 'TrackerStore', side_effect=track):
            transfers.validate(payload, archive_limits=False)
        self.assertTrue(paths)
        self.assertNotIn(':memory:', paths)
        self.assertTrue(all(not Path(path).exists() for path in paths))

    def test_local_all_modes_preserve_revisions_provenance_and_image_bytes(self):
        payload = self.payload()
        for mode in ('ffa', 'ww'):
            store = populate(self.source, mode)
            try:
                payload['modes'][mode] = transfers.collect(store, mode=mode, archive_limits=False)
            finally:
                store.close()
        for mode in ('mogi', 'ffa'):
            payload['modes'][mode]['races'][0]['auto_confirmed'] = 1
        evidence = transfers.local_evidence(self.source, payload)
        before = {key: hashlib.sha256(source.path.read_bytes()).hexdigest() for key, source in evidence.items()}
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Evidence must stream')):
            transfers.merge(self.target, payload, evidence, archive_limits=False)
        for mode in transfers.MODES:
            with sqlite3.connect(transfers.mode_dir(self.target, mode) / 'tracker.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM result_revisions').fetchone()[0], 2)
                if mode != 'ww':
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM races WHERE auto_confirmed=1').fetchone()[0], 1)
                filename = db.execute('SELECT filename FROM race_evidence').fetchone()[0]
                copied = transfers.evidence_dir(self.target, mode) / filename
                original = next(value for key, value in before.items() if key.startswith(mode + '/'))
                self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), original)
        self.assertEqual({key: hashlib.sha256(source.path.read_bytes()).hexdigest()
                          for key, source in evidence.items()}, before)
