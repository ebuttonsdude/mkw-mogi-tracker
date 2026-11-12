"""Application-facing backup selection, bounded preview and explicit confirmation."""
from contextlib import contextmanager
from pathlib import Path
import secrets
import sqlite3
import time
from types import SimpleNamespace

from .transfers import (FORMAT, MODES, VERSION, collect, decode_archive, encode_archive,
                        merge, mode_dir, plain_file, read_evidence, summary)


class SessionBackups:
    def __init__(self, app):
        self.app = app
        self.preview = None

    def _idle(self):
        app = self.app
        if (app.active_uploads or app.cameras.cleanup_failed
                or app.cameras.status.get('state') in ('starting', 'running', 'stopping')
                or (app.cameras.thread and app.cameras.thread.is_alive())):
            raise ValueError('Stop capture and finish uploads before transferring sessions')
        for _, capture, imports in app._contexts.values():
            if (imports.cleanup_failed or imports.snapshot()['active']
                    or any(t and t.is_alive() for t in (capture.thread, imports.thread))):
                raise ValueError('Stop capture and finish imports before transferring sessions')

    @contextmanager
    def _store(self, mode):
        if mode in self.app._contexts:
            yield self.app._contexts[mode][0]
            return
        path = mode_dir(self.app.data_dir, mode) / 'tracker.sqlite3'
        if not path.exists():
            yield None
            return
        plain_file(path, self.app.data_dir)
        db = sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            yield SimpleNamespace(_db=db)
        finally:
            db.close()

    def catalog(self):
        with self.app.lock:
            sessions = []
            for mode in MODES:
                with self._store(mode) as store:
                    if store:
                        sessions.extend(dict(row) | {'mode': mode} for row in store._db.execute(
                            'SELECT s.id,f.name,(SELECT count(*) FROM races r WHERE r.session_id=s.id) AS races '
                            'FROM sessions s JOIN history_folders f ON f.id=s.folder_id ORDER BY s.rowid'))
            return {'sessions': sessions}

    def export(self, selection):
        if not isinstance(selection, list) or not selection or len(selection) > 1000:
            raise ValueError('Choose 1–1000 sessions to export')
        chosen = {}
        for item in selection:
            if (not isinstance(item, dict) or set(item) != {'mode', 'id'} or item['mode'] not in MODES
                    or not isinstance(item['id'], str) or not 0 < len(item['id']) <= 200):
                raise ValueError('Invalid session selection')
            chosen.setdefault(item['mode'], []).append(item['id'])
        with self.app.lock:
            self._idle()
            payload = {'format': FORMAT, 'version': VERSION, 'modes': {}}
            for mode, selected in chosen.items():
                with self._store(mode) as store:
                    if store is None:
                        raise ValueError('Selected mode no longer exists')
                    payload['modes'][mode] = collect(store, selected, mode)
            evidence = read_evidence(self.app.data_dir, payload)
        return encode_archive(payload, evidence)

    def prepare(self, data):
        payload, evidence = decode_archive(data)
        with self.app.lock:
            self._idle()
            token = secrets.token_urlsafe(32)
            self.preview = (token, time.monotonic() + 600, payload, evidence)
            return summary(payload) | {'token': token, 'evidence_files': len(evidence),
                                       'expires_in_seconds': 600}

    def confirm(self, token):
        with self.app.lock:
            self._idle()
            if (not isinstance(token, str) or self.preview is None
                    or not secrets.compare_digest(token, self.preview[0]) or time.monotonic() > self.preview[1]):
                raise ValueError('Import preview expired or changed; preview the archive again')
            _, _, payload, evidence = self.preview
            paths = {}
            for mode, (store, _, _) in self.app._contexts.items():
                path = store._db.execute('PRAGMA database_list').fetchone()[2]
                if not path and mode in payload['modes']:
                    raise ValueError('Session import requires a persistent database')
                if path:
                    paths[mode] = path
            result = merge(self.app.data_dir, payload, evidence, database_paths=paths)
            self.preview = None
            self.app.sync_outputs()
            return result | {'imported': len(result['sessions'])}
