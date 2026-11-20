"""Versioned, credential-free session records and additive transactional imports.

Only the tables/columns below cross this boundary. Never export whole databases,
settings, capture state, private connections, or arbitrary data-directory files.
Callers serialize access and stop capture/imports before applying a transfer.
"""

from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from uuid import uuid4
import zipfile
import zlib

from .store import TrackerStore
from .ww_store import WWStore

FORMAT = 'mkw-session-backup'
VERSION = 1
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MAX_IMAGE = 16 * 1024 * 1024
MAX_FILES = 4096
MODES = ('mogi', 'ffa', 'ww')
TABLES = {
    'history_folders': 'id name created_at lounge_id',
    'sessions': 'id started_at folder_id format',
    'teams': 'id name active color session_id',
    'players': 'id name team_id active session_id',
    'recordings': 'id label started_at',
    'races': 'id session_id course recording_id offset_ms revision folder_id created_at auto_confirmed history_order',
    'result_revisions': 'race_id revision created_at',
    'placements': 'race_id revision player_id position',
    'penalties': 'id race_id player_id points reason created_at',
    'race_evidence': 'id race_id filename kind offset_ms label',
}
WW_TABLES = {
    'ww_session_players': 'session_id name',
    'ww_results': 'race_id revision position lobby_size vr_before vr_after delta source created_at derive_from_previous',
}
REFERENCES = {'session_id': 'sessions', 'folder_id': 'history_folders',
              'team_id': 'teams', 'player_id': 'players', 'race_id': 'races',
              'recording_id': 'recordings'}
PNG_NAME = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\.png')


def tables(mode):
    return TABLES | (WW_TABLES if mode == 'ww' else {})


def mode_dir(root, mode):
    return Path(root) if mode == 'mogi' else Path(root) / mode


def evidence_dir(root, mode):
    return mode_dir(root, 'ffa' if mode == 'ffa' else 'mogi') / 'evidence'


def safe_path(path, root):
    """Check every existing component even when the final path does not exist."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('File is outside its allowed directory')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Symbolic links and junctions are not imported')
    return path


def plain_file(path, root):
    """Reject links at every component, including Windows junctions."""
    path = safe_path(path, root)
    if not path.is_file():
        raise ValueError(f'Missing regular file: {path.name}')
    return path


def collect(store, selected=None, mode='mogi', schema='main', *, archive_limits=True):
    """Select complete session subgraphs; local transfers have no archive quotas."""
    if schema not in ('main', *MODES):
        raise ValueError('Invalid database scope')
    all_sessions = selected is None
    if all_sessions:
        limit = ' LIMIT 1001' if archive_limits else ''
        selected = {r[0] for r in store._db.execute(f'SELECT id FROM {schema}.sessions{limit}')}
    else:
        selected = set(selected)
    if not selected or (archive_limits and len(selected) > 1000):
        raise ValueError('Choose 1–1000 sessions per transfer' if archive_limits
                         else 'Choose at least one session')
    selection = None
    values = []
    if all_sessions:
        session_query = f'SELECT id FROM {schema}.sessions'
    elif len(selected) <= store._db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER):
        values = sorted(selected)
        session_query = f'SELECT id FROM {schema}.sessions WHERE id IN ({",".join("?" for _ in values)})'
    else:
        selection = 'transfer_selection_' + uuid4().hex
        store._db.execute(f'SAVEPOINT {selection}')
        store._db.execute(f'CREATE TEMP TABLE {selection} (id TEXT PRIMARY KEY)')
        store._db.executemany(f'INSERT INTO {selection} VALUES (?)', ((v,) for v in selected))
        session_query = f'SELECT id FROM temp.{selection}'
    try:
        race_query = f'SELECT id FROM {schema}.races WHERE session_id IN ({session_query})'
        conditions = {
            'sessions': f'id IN ({session_query})',
            'history_folders': f'id IN (SELECT folder_id FROM {schema}.sessions WHERE id IN ({session_query}))',
            'recordings': f'id IN (SELECT recording_id FROM {schema}.races WHERE session_id IN ({session_query}))',
            **{t: f'session_id IN ({session_query})' for t in ('teams', 'players', 'races', 'ww_session_players')},
        }
        rows, total_bytes, count = {}, 0, 0
        race_columns = {row[1] for row in store._db.execute(f'PRAGMA {schema}.table_info(races)')}
        for table, columns in tables(mode).items():
            fallback = {'auto_confirmed': '0 AS auto_confirmed', 'history_order': 'rowid AS history_order'}
            selected_columns = [fallback.get(c, c) if table == 'races' and c not in race_columns else c
                                for c in columns.split()]
            condition = conditions.get(table, f'race_id IN ({race_query})')
            cursor = store._db.execute(
                f'SELECT {",".join(selected_columns)} FROM {schema}.{table} WHERE {condition} ORDER BY rowid', values)
            rows[table] = []
            try:
                for value in cursor:
                    row = dict(value)
                    if archive_limits:
                        total_bytes += len(json.dumps(row, ensure_ascii=False).encode()) + 2
                        count += 1
                        if total_bytes > MAX_JSON or count > 100000:
                            raise ValueError('Selected records exceed archive limits; choose fewer sessions')
                    rows[table].append(row)
            finally:
                cursor.close()
        if {r['id'] for r in rows['sessions']} != selected:
            raise ValueError('Choose existing sessions to export')
        return rows
    finally:
        if selection:
            store._db.execute(f'ROLLBACK TO SAVEPOINT {selection}')
            store._db.execute(f'RELEASE SAVEPOINT {selection}')


def _insert(db, schema, rows):
    for table, records in rows.items():
        columns = tables('ww')[table].split()
        if table == 'races' and 'auto_confirmed' not in {
                r[1] for r in db.execute(f'PRAGMA {schema}.table_info(races)')}:
            if any(r.get('auto_confirmed', 0) for r in records):
                raise ValueError('This archive contains YOLO provenance; update the tracker before importing it')
            columns.remove('auto_confirmed')
        db.executemany(f'INSERT INTO {schema}.{table} ({",".join(columns)}) '
                       f'VALUES ({",".join("?" for _ in columns)})',
                       ([r[c] for c in columns] for r in records))


def validate(payload, *, archive_limits=True):
    """Validate an untrusted document against fresh application schemas, not SQL from it."""
    if (not isinstance(payload, dict) or set(payload) != {'format', 'version', 'modes'}
            or payload['format'] != FORMAT or type(payload['version']) is not int
            or payload['version'] != VERSION or not isinstance(payload['modes'], dict)
            or not payload['modes'] or not set(payload['modes']) <= set(MODES)):
        raise ValueError('Unsupported session archive format/version')
    for mode, rows in payload['modes'].items():
        if not isinstance(rows, dict) or set(rows) != set(tables(mode)):
            raise ValueError('Unexpected session tables')
        if archive_limits and sum(len(v) for v in rows.values() if isinstance(v, list)) > 100000:
            raise ValueError('Archive contains too many records')
        for table, columns in tables(mode).items():
            if not isinstance(rows[table], list) or (archive_limits and len(rows[table]) > 100000):
                raise ValueError('Invalid or oversized session table')
            for index, row in enumerate(rows[table], start=1):
                if table == 'races' and isinstance(row, dict):
                    row.setdefault('history_order', index)  # Older archives retain insertion order.
                    row.setdefault('auto_confirmed', 0)  # Version 1 archives before YOLO provenance.
                if not isinstance(row, dict) or set(row) != set(columns.split()):
                    raise ValueError('Unexpected session fields')
                for key, value in row.items():
                    integer = key in {'active', 'offset_ms', 'revision', 'position', 'points',
                                      'lobby_size', 'vr_before', 'vr_after', 'delta', 'derive_from_previous', 'auto_confirmed', 'history_order'}
                    optional = key in {'team_id', 'recording_id', 'offset_ms', 'color', 'lounge_id',
                                       'lobby_size', 'vr_before', 'delta'}
                    if value is None and not optional:
                        raise ValueError('Missing required session value')
                    if value is not None and type(value) is not (int if integer else str):
                        raise ValueError('Invalid session field type')
                    if integer and value is not None and not -(2**63) < value < 2**63:
                        raise ValueError('Session integer is out of range')
                    if key in ('active', 'derive_from_previous', 'auto_confirmed') and value not in (0, 1):
                        raise ValueError('Invalid boolean session value')
                    if value is not None and type(value) not in (str, int):
                        raise ValueError('Invalid session value type')
                    if isinstance(value, str) and (len(value) > 4096 or '\x00' in value):
                        raise ValueError('Invalid session text')
                    if key == 'id' or key in REFERENCES:
                        if value is not None and (not isinstance(value, str) or not value or len(value) > 200):
                            raise ValueError('Invalid identity')
        if not rows['sessions']:
            raise ValueError('Archive mode has no sessions')
        with ExitStack() as validation_stack:
            database = ':memory:' if archive_limits else str(Path(validation_stack.enter_context(
                tempfile.TemporaryDirectory(prefix='mkw-validation-'))) / 'validation.sqlite3')
            check = WWStore(database) if mode == 'ww' else TrackerStore(database)
            validation_stack.callback(check.close)
            _validate_rows(check, mode, rows)
    return payload


def _validate_rows(check, mode, rows):
    try:
        db = check._db
        if 'auto_confirmed' not in {r[1] for r in db.execute('PRAGMA table_info(races)')}:
            db.execute('ALTER TABLE races ADD COLUMN auto_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(auto_confirmed IN (0,1))')
        with db:
            db.execute('DELETE FROM current_session')
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM history_folders')
            _insert(db, 'main', rows)
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Broken session relationships')
        sessions = {r['id']: r for r in rows['sessions']}
        races = {r['id']: r for r in rows['races']}
        players = {r['id']: r for r in rows['players']}
        teams = {r['id']: r for r in rows['teams']}
        if len({s['folder_id'] for s in sessions.values()}) != len(rows['history_folders']):
            raise ValueError('Unowned session folder')
        for session in sessions.values():
            if session['format'] not in ('teams', 'ffa'):
                raise ValueError('Invalid session format')
        for row in [*players.values(), *teams.values(), *races.values()]:
            if row['session_id'] not in sessions:
                raise ValueError('Unowned record')
        for player in players.values():
            if player['team_id'] and teams[player['team_id']]['session_id'] != player['session_id']:
                raise ValueError('Cross-session team assignment')
        revisions_by_race = {}
        for revision in rows['ww_results' if mode == 'ww' else 'result_revisions']:
            if revision['revision'] < 1:
                raise ValueError('Result revisions start at one')
            revisions_by_race.setdefault(revision['race_id'], []).append(revision['revision'])
        seen_order = set()
        for race in races.values():
            slot = (race['session_id'], race['history_order'])
            if not 0 < race['history_order'] < 2**63 - 1 or slot in seen_order:
                raise ValueError('Invalid race history order')
            seen_order.add(slot)
            if race['folder_id'] != sessions[race['session_id']]['folder_id']:
                raise ValueError('Cross-session race folder')
            revisions = revisions_by_race.get(race['id'], [])
            if race['auto_confirmed'] and (mode == 'ww' or not race['revision']):
                raise ValueError('Invalid automatic-confirmation provenance')
            if type(race['revision']) is not int or race['revision'] != max(revisions, default=0):
                raise ValueError('Invalid current result revision')
        for row in [*rows['placements'], *rows['penalties']]:
            if players[row['player_id']]['session_id'] != races[row['race_id']]['session_id']:
                raise ValueError('Cross-session result identity')
        counts = Counter((r['race_id'], r['revision']) for r in rows['placements'])
        for revision in rows['result_revisions'] if mode != 'ww' else []:
            if counts[(revision['race_id'], revision['revision'])] != 12:
                raise ValueError('Scored results require twelve distinct placements')
        if mode == 'ww':
            if rows['placements'] or rows['penalties']:
                raise ValueError('WW cannot contain Mogi placements or penalties')
            if {(r['race_id'], r['revision']) for r in rows['result_revisions']} != {
                    (r['race_id'], r['revision']) for r in rows['ww_results']}:
                raise ValueError('Missing WW revision')
            for row in rows['ww_results']:
                if row['source'] not in ('manual', 'capture', 'import', 'automatic'):
                    raise ValueError('Invalid WW source')
                if row['derive_from_previous'] and row['source'] != 'capture':
                    raise ValueError('Invalid derived WW result')
        for row in rows['race_evidence']:
            if (not isinstance(row['filename'], str) or not PNG_NAME.fullmatch(row['filename'])
                    or row['kind'] not in ('results', 'suspected_results', 'prerace')):
                raise ValueError('Invalid evidence reference')
    except (sqlite3.Error, KeyError) as error:
        raise ValueError('Invalid session schema or relationships') from error

def summary(payload):
    sessions = []
    for mode, rows in payload['modes'].items():
        folders = {row['id']: row['name'] for row in rows['history_folders']}
        counts = Counter(row['session_id'] for row in rows['races'])
        sessions.extend({'mode': mode, 'id': row['id'], 'name': folders[row['folder_id']],
                         'races': counts[row['id']]} for row in rows['sessions'])
    return {'sessions': sessions,
            'policy': 'Add as new sessions; existing sessions and active selections are never replaced.'}


def read_evidence(root, payload):
    result = {}
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    if size > MAX_JSON:
        raise ValueError('Selected manifest exceeds 16 MiB; choose fewer sessions')
    for mode, rows in payload['modes'].items():
        for row in rows['race_evidence']:
            name = row['filename']
            if not PNG_NAME.fullmatch(name):
                raise ValueError('Invalid evidence filename')
            path = plain_file(evidence_dir(root, mode) / name, root)
            if path.stat().st_size > MAX_IMAGE:
                raise ValueError('Evidence image exceeds 16 MiB')
            key = f'{mode}/{name}'
            if key in result:
                continue
            size += path.stat().st_size
            if size > MAX_ARCHIVE or len(result) >= MAX_FILES - 1:
                raise ValueError('Selected evidence exceeds archive limits; choose fewer sessions')
            result[key] = path.read_bytes()
    return result


@dataclass(frozen=True)
class LocalEvidence:
    """A validated source identity, not an in-memory image or an archive member."""

    path: Path
    root: Path
    identity: tuple

    @property
    def size(self):
        return self.identity[2]


def _file_identity(info):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Evidence must be a regular file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_local_evidence(source):
    path = plain_file(source.path, source.root)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        if _file_identity(os.fstat(descriptor)) != source.identity:
            raise ValueError('Source evidence changed since preview; preview again')
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def _check_local_identity(source, stream):
    if (_file_identity(os.fstat(stream.fileno())) != source.identity
            or _file_identity(plain_file(source.path, source.root).stat()) != source.identity):
        raise ValueError('Source evidence changed since preview; preview again')


def local_evidence(root, payload):
    """Validate local PNGs one at a time, retaining only their paths and identities."""
    root = Path(root).absolute()
    result = {}
    for mode, rows in payload['modes'].items():
        for row in rows['race_evidence']:
            name = row['filename']
            if not PNG_NAME.fullmatch(name):
                raise ValueError('Invalid evidence filename')
            key = f'{mode}/{name}'
            if key in result:
                continue
            path = plain_file(evidence_dir(root, mode) / name, root)
            source = LocalEvidence(path, root, _file_identity(path.stat()))
            with _open_local_evidence(source) as stream:
                _check_image(stream)
                _check_local_identity(source, stream)
            result[key] = source
    return result


def _copy_local_evidence(source, output):
    with _open_local_evidence(source) as stream:
        remaining = source.size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError('Source evidence changed since preview; preview again')
            output.write(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise ValueError('Source evidence changed since preview; preview again')
        _check_local_identity(source, stream)


def _check_image(data):
    # Decode before accepting image containers; keep original evidence pixels/metadata.
    try:
        from PIL import Image
        with Image.open(BytesIO(data) if isinstance(data, bytes) else data) as picture:
            if (picture.format != 'PNG' or picture.width * picture.height > 16_000_000
                    or max(picture.size) > 8192 or getattr(picture, 'n_frames', 1) != 1):
                raise ValueError('Evidence must be a bounded still PNG')
            picture.verify()
    except ImportError as error:
        raise ValueError('Evidence transfers require the optional Pillow report dependency') from error
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('Invalid evidence PNG') from error


def session_fingerprint(db, schema, mode, session_id, *, archive_limits=True):
    class View:
        _db = db
    rows = collect(View(), [session_id], mode, schema, archive_limits=archive_limits)
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def _delete_session(db, schema, session_id):
    """Delete only the explicitly chosen session; shared recording references stay."""
    folder = db.execute(f'SELECT folder_id FROM {schema}.sessions WHERE id=?', (session_id,)).fetchone()[0]
    for table in ('placements', 'penalties', 'race_evidence', 'ww_results', 'result_revisions'):
        if table == 'ww_results' and schema != 'ww':
            continue
        db.execute(f'DELETE FROM {schema}.{table} WHERE race_id IN '
                   f'(SELECT id FROM {schema}.races WHERE session_id=?)', (session_id,))
    for table in ('races', 'players', 'teams'):
        db.execute(f'DELETE FROM {schema}.{table} WHERE session_id=?', (session_id,))
    if schema == 'ww':
        db.execute(f'DELETE FROM {schema}.ww_session_players WHERE session_id=?', (session_id,))
    db.execute(f'DELETE FROM {schema}.current_session WHERE session_id=?', (session_id,))
    db.execute(f'DELETE FROM {schema}.sessions WHERE id=?', (session_id,))
    db.execute(f'DELETE FROM {schema}.history_folders WHERE id=?', (folder,))


def merge(root, payload, evidence, *, replacements=None, database_paths=None, archive_limits=True):
    """Add all modes in one attached-database transaction; never update existing rows."""
    validate(payload, archive_limits=archive_limits)
    replacements = replacements or {}
    expected = {f'{mode}/{r["filename"]}' for mode, rows in payload['modes'].items()
                for r in rows['race_evidence']}
    if set(evidence) != expected:
        raise ValueError('Missing or unexpected evidence files')
    for data in evidence.values():
        if not archive_limits:
            if not isinstance(data, LocalEvidence):
                raise ValueError('Local transfers require file-backed evidence')
            continue
        if not isinstance(data, bytes) or len(data) > MAX_IMAGE:
            raise ValueError('Invalid evidence size')
        _check_image(data)
    root = Path(root)
    paths = {mode: Path((database_paths or {}).get(mode, mode_dir(root, mode) / 'tracker.sqlite3'))
             for mode in payload['modes']}
    # Initialize only target schemas; existing active sessions remain selected.
    for mode in payload['modes']:
        path = safe_path(paths[mode], paths[mode].parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            plain_file(path, path.parent)
        store = WWStore(path) if mode == 'ww' else TrackerStore(path, default_format='ffa' if mode == 'ffa' else 'teams')
        store.close()
    created = []
    result = summary(payload)
    with ExitStack() as stack:
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='mkw-transfer-', ignore_cleanup_errors=True))
        db = sqlite3.connect(Path(temporary) / 'coordinator.sqlite3')
        db.execute('CREATE TABLE coordinator (id INTEGER)')
        db.row_factory = sqlite3.Row
        stack.callback(db.close)
        db.execute('PRAGMA foreign_keys=ON')
        for mode in payload['modes']:
            db.execute(f'ATTACH DATABASE ? AS {mode}', (str(paths[mode]),))
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                for mode, original in payload['modes'].items():
                    rows = deepcopy(original)
                    ids = {table: {r['id']: str(uuid4()) for r in records}
                           for table, records in rows.items() if 'id' in tables(mode)[table].split()}
                    active_replacement = None
                    for incoming, target in replacements.get(mode, {}).items():
                        if incoming not in ids['sessions']:
                            raise ValueError('Replacement refers to a missing incoming session')
                        if session_fingerprint(db, mode, mode, target['id'], archive_limits=archive_limits) != target['fingerprint']:
                            raise ValueError('Target session changed since preview; preview again')
                        current = db.execute(f'SELECT session_id FROM {mode}.current_session').fetchone()
                        if current and current[0] == target['id']:
                            active_replacement = ids['sessions'][incoming]
                        _delete_session(db, mode, target['id'])
                    filenames = {}
                    for row in rows['race_evidence']:
                        old = row['filename']
                        if old not in filenames:
                            new = str(uuid4()) + '.png'
                            path = safe_path(evidence_dir(root, mode) / new, root)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            with path.open('xb') as output:
                                created.append(path)
                                source = evidence[f'{mode}/{old}']
                                if archive_limits:
                                    output.write(source)
                                else:
                                    _copy_local_evidence(source, output)
                            if not archive_limits:
                                with path.open('rb') as copied:
                                    _check_image(copied)
                            filenames[old] = new
                        row['filename'] = filenames[old]
                    for table, records in rows.items():
                        for row in records:
                            for column, value in list(row.items()):
                                target = table if column == 'id' else REFERENCES.get(column)
                                if target and value is not None:
                                    row[column] = ids[target][value]
                    _insert(db, mode, rows)
                    if active_replacement:
                        db.execute(f'INSERT INTO {mode}.current_session VALUES(1,?)', (active_replacement,))
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
    return result


def encode_archive(payload, evidence):
    validate(payload)
    manifest = json.dumps(payload, ensure_ascii=False).encode()
    if (len(manifest) > MAX_JSON or len(manifest) + sum(map(len, evidence.values())) > MAX_ARCHIVE
            or len(evidence) + 1 > MAX_FILES):
        raise ValueError('Selected sessions exceed archive limits; choose fewer sessions')
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', manifest)
        for name, data in evidence.items():
            archive.writestr('evidence/' + name, data)
    data = output.getvalue()
    # Apply the same limits to exports so every downloadable archive is importable.
    decode_archive(data)
    return data


def decode_archive(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ARCHIVE:
        raise ValueError('Choose a session ZIP no larger than 128 MiB')
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if (len(members) > MAX_FILES or len(set(names)) != len(names)
                    or sum(m.file_size for m in members) > MAX_ARCHIVE):
                raise ValueError('Archive is duplicated or exceeds expanded limits')
            for member in members:
                name = member.filename
                allowed = name == 'manifest.json' or re.fullmatch(
                    r'evidence/(mogi|ffa|ww)/[0-9a-fA-F-]{36}\.png', name)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (not allowed or member.orig_filename != name or kind not in (0, stat.S_IFREG) or member.flag_bits & 1
                        or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or member.file_size > (MAX_JSON if name == 'manifest.json' else MAX_IMAGE)):
                    raise ValueError('Unsafe or unexpected archive member')
            if 'manifest.json' not in names:
                raise ValueError('Archive has no manifest')
            payload = validate(json.loads(archive.read('manifest.json')))
            evidence = {name.removeprefix('evidence/'): archive.read(name)
                        for name in names if name != 'manifest.json'}
            expected = {f'{mode}/{r["filename"]}' for mode, rows in payload['modes'].items()
                        for r in rows['race_evidence']}
            if set(evidence) != expected:
                raise ValueError('Missing or unexpected evidence files')
            for image in evidence.values():
                _check_image(image)
            return payload, evidence
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, RecursionError, NotImplementedError, EOFError, zlib.error) as error:
        raise ValueError('Invalid session archive') from error
