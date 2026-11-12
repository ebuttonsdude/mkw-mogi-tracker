"""Backup HTTP and application integration with temporary databases only."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import zipfile

from mkw_tracker.server import Application, make_server
from tests.test_transfers import populate


class BackupAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for mode in ('mogi', 'ffa', 'ww'):
            store = populate(self.root, mode)
            store.close()
        (self.root / 'obs-connection.json').write_text('{"password":"fake-secret-not-valid-private-file"}')
        # Private persistence has its own schema; do not load a bogus connection.
        (self.root / 'obs-connection.json').unlink()
        self.app = Application(self.root / 'tracker.sqlite3', self.root)
        self.addCleanup(self.app.close)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path, body=None, *, headers=None):
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
            headers = {'Content-Type': 'application/json'} | (headers or {})
        return urlopen(Request(self.url + path, data=body, headers=headers or {}), timeout=10)

    def test_export_preview_confirm_and_token_consumption(self):
        with self.request('/api/backups/sessions') as response:
            sessions = json.load(response)['sessions']
        chosen = [{'mode': s['mode'], 'id': s['id']} for s in sessions]
        (self.root / 'obs-connection.json').write_text('fake-secret')
        (self.root / 'api-token.txt').write_text('fake-token')
        with self.request('/api/backups/export', {'sessions': chosen}) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/zip')
            archive = response.read()
        with zipfile.ZipFile(BytesIO(archive)) as z:
            contents = b''.join(z.read(name) for name in z.namelist())
            self.assertNotIn(b'fake-secret', contents)
            self.assertNotIn(b'fake-token', contents)
        with self.request('/api/backups/preview', archive, headers={'Content-Type': 'application/zip'}) as response:
            preview = json.load(response)
        self.assertEqual(len(preview['sessions']), 3)
        with self.request('/api/backups/sessions') as response:
            self.assertEqual(len(json.load(response)['sessions']), 3)
        with self.request('/api/backups/import', {'token': preview['token']}) as response:
            self.assertEqual(json.load(response)['imported'], 3)
        with self.request('/api/backups/sessions') as response:
            self.assertEqual(len(json.load(response)['sessions']), 6)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/backups/import', {'token': preview['token']})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_bad_origin_archive_and_selection_rejected(self):
        for path, body, headers, code in (
                ('/api/backups/export', {'sessions': []}, {}, 400),
                ('/api/backups/preview', b'not zip', {'Content-Type': 'application/zip'}, 400),
                ('/api/backups/preview', b'not zip', {'Content-Type': 'application/zip', 'Origin': 'https://evil.example'}, 403)):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, body, headers=headers)
            self.assertEqual(error.exception.code, code)
            error.exception.close()

    def test_active_capture_failed_import_and_expired_preview_preserve_sessions(self):
        sessions = self.app.backups.catalog()['sessions']
        selected = [{'mode': s['mode'], 'id': s['id']} for s in sessions]
        self.app.cameras.status['state'] = 'starting'
        with self.assertRaises(ValueError):
            self.app.backups.export(selected)
        self.app.cameras.status['state'] = 'stopped'
        archive = self.app.backups.export(selected)
        preview = self.app.backups.prepare(archive)
        with patch('mkw_tracker.backups.merge', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.app.backups.confirm(preview['token'])
        self.assertEqual(self.app.backups.catalog()['sessions'], sessions)
        with patch('mkw_tracker.backups.time.monotonic', return_value=float('inf')), self.assertRaises(ValueError):
            self.app.backups.confirm(preview['token'])

    def test_nonstandard_application_database_is_import_target(self):
        other_root = self.root / 'custom'
        other_root.mkdir()
        app = Application(other_root / 'custom.sqlite3', other_root)
        try:
            session = app.store.active_session()['id']
            archive = app.backups.export([{'mode': 'mogi', 'id': session}])
            token = app.backups.prepare(archive)['token']
            app.backups.confirm(token)
            self.assertEqual(len(app.store._rows('SELECT * FROM sessions')), 2)
            self.assertFalse((other_root / 'tracker.sqlite3').exists())
        finally:
            app.close()

    def test_new_preview_invalidates_previous_token(self):
        selected = [{'mode': s['mode'], 'id': s['id']} for s in self.app.backups.catalog()['sessions']]
        archive = self.app.backups.export(selected)
        first = self.app.backups.prepare(archive)['token']
        second = self.app.backups.prepare(archive)['token']
        with self.assertRaises(ValueError):
            self.app.backups.confirm(first)
        self.app.backups.confirm(second)

    def test_yolo_provenance_and_legacy_archive_default(self):
        from copy import deepcopy
        from mkw_tracker import transfers
        store = self.app.store
        if 'auto_confirmed' not in {r[1] for r in store._db.execute('PRAGMA table_info(races)')}:
            store._db.execute('ALTER TABLE races ADD COLUMN auto_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(auto_confirmed IN (0,1))')
        store._db.execute('UPDATE races SET auto_confirmed=1 WHERE id="same-race-id"')
        store._db.commit()
        selected = [{'mode': 'mogi', 'id': store.active_session()['id']}]
        archive = self.app.backups.export(selected)
        payload, _ = transfers.decode_archive(archive)
        self.assertEqual(payload['modes']['mogi']['races'][0]['auto_confirmed'], 1)
        self.app.backups.confirm(self.app.backups.prepare(archive)['token'])
        self.assertEqual(len(store._rows('SELECT * FROM races WHERE auto_confirmed=1')), 2)
        legacy = deepcopy(payload)
        for race in legacy['modes']['mogi']['races']:
            race.pop('auto_confirmed')
        transfers.validate(legacy)
        self.assertTrue(all(r['auto_confirmed'] == 0 for r in legacy['modes']['mogi']['races']))
        for value in (2, -1, True, '1'):
            invalid = deepcopy(payload)
            invalid['modes']['mogi']['races'][0]['auto_confirmed'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                transfers.validate(invalid)

    def test_malformed_manifest_values_are_controlled_http_errors(self):
        selected = [{'mode': 'mogi', 'id': self.app.store.active_session()['id']}]
        archive = self.app.backups.export(selected)
        with zipfile.ZipFile(BytesIO(archive)) as source:
            manifest = json.loads(source.read('manifest.json'))
        for value in (10**100, True, [], {'bad': 'value'}):
            manifest['modes']['mogi']['races'][0]['revision'] = value
            stream = BytesIO()
            with zipfile.ZipFile(stream, 'w') as target:
                target.writestr('manifest.json', json.dumps(manifest))
            with self.subTest(value=type(value)), self.assertRaises(HTTPError) as error:
                self.request('/api/backups/preview', stream.getvalue(), headers={'Content-Type': 'application/zip'})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.assertEqual(len(self.app.backups.catalog()['sessions']), 3)

    def test_export_limits_checked_before_image_reads(self):
        from mkw_tracker import transfers
        payload = {'format': transfers.FORMAT, 'version': 1,
                   'modes': {'mogi': transfers.collect(self.app.store)}}
        manifest_size = len(json.dumps(payload, ensure_ascii=False).encode())
        for limits in ({'MAX_ARCHIVE': manifest_size + 1}, {'MAX_FILES': 1}, {'MAX_JSON': 1}):
            with patch.multiple(transfers, **limits), patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read')):
                with self.assertRaises(ValueError):
                    transfers.read_evidence(self.root, payload)
        with patch.object(transfers, 'MAX_JSON', 1), self.assertRaises(ValueError):
            transfers.collect(self.app.store)

    def test_zip_upload_has_absolute_deadline_even_with_progress(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        handler = object.__new__(self.server.RequestHandlerClass)
        handler.headers = {'Content-Type': 'application/zip', 'Content-Length': '10'}
        handler._check_write_origin = Mock()
        handler.connection = Mock()
        handler.rfile = Mock()
        handler.rfile.read1.return_value = b'x'
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 121]))
        with patch('mkw_tracker.server.time', clock), self.assertRaisesRegex(ValueError, 'deadline'):
            handler._backup_upload()
        handler.rfile.read1.assert_called_once()
        self.assertIsNone(self.app.backups.preview)

    def test_duplicate_archive_members_are_rejected(self):
        import warnings
        stream = BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('manifest.json', '{}')
                archive.writestr('manifest.json', '{}')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/backups/preview', stream.getvalue(), headers={'Content-Type': 'application/zip'})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
