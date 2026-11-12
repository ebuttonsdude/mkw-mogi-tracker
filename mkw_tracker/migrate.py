"""Read-only upstream migration. Run ``python -m mkw_tracker.migrate --help``."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from uuid import uuid4

from .settings import DEFAULT_SETTINGS
from . import course_images
from .course_images import CourseImageLibrary, course_from_filename
from .store import TrackerStore
from . import theme_library
from .theme_library import ThemeLibrary, _image
from .themes import all_themes
from .transfers import (FORMAT, MODES, PNG_NAME, VERSION, collect, merge, mode_dir, plain_file,
                        evidence_dir, local_evidence, safe_path, session_fingerprint, summary, validate)
from .ww_store import WWStore

MODEL_NAMES = {'config.json', 'generation_config.json', 'preprocessor_config.json',
               'video_preprocessor_config.json', 'tokenizer_config.json', 'tokenizer.json',
               'special_tokens_map.json', 'vocab.json', 'merges.txt', 'chat_template.json',
               'model.safetensors.index.json', 'inference.json', 'inference.pdiparams', 'inference.yml'}
VIDEO_SUFFIXES = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.ts', '.mts', '.m2ts'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
CATEGORIES = ('sessions', 'evidence', 'pictures', 'fonts', 'videos', 'models')
DEFAULT_CATEGORIES = frozenset(CATEGORIES[:4])
CATEGORY_LABELS = {'sessions': 'Sessions, results and corrections (all modes)',
                   'evidence': 'Saved race evidence images', 'pictures': 'Pictures and custom themes',
                   'fonts': 'Report fonts', 'videos': 'Uploaded recordings', 'models': 'Portable model files'}


def _source_paths(source, source_data=None):
    source = Path(source).absolute()
    data = Path(source_data).absolute() if source_data else source / '.local' / 'tracker'
    for directory in (source, data):
        safe_path(directory, directory)
        if not directory.is_dir():
            raise ValueError(f'Source directory does not exist: {directory}')
    return source, data


def _closed_database(path, root):
    plain_file(path, root)
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Close the original tracker before migration (active SQLite journal)')


def _source_assets(source, data, errors):
    """Discover portable data only; inventory never decodes media or loads models."""
    static = source / 'mkw_tracker' / 'static'
    assets = {key: [] for key in CATEGORIES[2:]}
    def scan(category, directory):
        try:
            return _files(directory)
        except (OSError, ValueError) as error:
            errors[category].append(str(error))
            return []
    for kind in ('character-icons', 'track-icons'):
        assets['pictures'].extend(p for p in scan('pictures', static / kind) if p.suffix.lower() in IMAGE_SUFFIXES)
    assets['pictures'].extend(p for p in scan('pictures', data / 'imported-assets' / 'track-icons')
                              if p.suffix.lower() in IMAGE_SUFFIXES)
    for name in ('custom-themes.json', 'course-images.json'):
        library = data / name
        if library.exists():
            try:
                assets['pictures'].append(plain_file(library, data))
            except ValueError as error:
                errors['pictures'].append(str(error))
    for directory in (static / 'fonts', data / 'fonts'):
        assets['fonts'].extend(p for p in scan('fonts', directory) if p.suffix.lower() in ('.ttf', '.otf'))
    for mode in MODES:
        assets['videos'].extend(p for p in scan('videos', mode_dir(data, mode) / 'uploads')
                                if p.suffix.lower() in VIDEO_SUFFIXES)
    assets['models'] = [p for p in scan('models', source / '.local' / 'models')
                        if p.name in MODEL_NAMES or p.suffix.lower() in ('.safetensors', '.gguf')]
    return assets


def inspect_source(source, *, source_data=None):
    """Stat-only media inventory and read-only SQL counts, without transfer caps."""
    source, data = _source_paths(source, source_data)
    categories = {key: {'count': 0, 'bytes': 0, 'unit': 'files'} for key in CATEGORIES}
    errors = {key: [] for key in CATEGORIES}
    categories['sessions']['unit'] = 'sessions'
    modes, races, database_bytes = {}, 0, 0
    for mode in MODES:
        path = mode_dir(data, mode) / 'tracker.sqlite3'
        if not path.exists():
            continue
        _closed_database(path, data)
        before = path.stat()
        # immutable avoids journal/lock-file writes; both trackers must be closed.
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            count = db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            race_count = db.execute('SELECT COUNT(*) FROM races').fetchone()[0]
            modes[mode] = {'sessions': count, 'races': race_count}
            categories['sessions']['count'] += count
            races += race_count
            tables_present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'race_evidence' in tables_present:
                for name, in db.execute('SELECT DISTINCT filename FROM race_evidence'):
                    categories['evidence']['count'] += 1
                    try:
                        if not isinstance(name, str) or not PNG_NAME.fullmatch(name):
                            raise ValueError('Invalid evidence filename')
                        image = plain_file(evidence_dir(data, mode) / name, data)
                        categories['evidence']['bytes'] += image.stat().st_size
                    except (OSError, ValueError) as error:
                        errors['evidence'].append(str(error))
        finally:
            db.close()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Source changed during inventory; close the original tracker')
        database_bytes += before.st_size
    categories['sessions']['bytes'] = database_bytes
    for category, paths in _source_assets(source, data, errors).items():
        categories[category]['count'] = len(paths)
        categories[category]['bytes'] = sum(p.stat().st_size for p in paths)
    return {'categories': categories, 'races': races, 'database_bytes': database_bytes, 'modes': modes,
            'unavailable': {key: values for key, values in errors.items() if values}}


def _files(directory):
    if not directory.exists():
        return []
    return [plain_file(p, directory) for p in sorted(directory.rglob('*')) if p.is_file() or p.is_symlink()]


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _destination(source, directory):
    return safe_path(directory / source.name, directory)


def _choice(conflict, policy, resolver):
    if policy:
        action, name = policy, None
    elif resolver:
        action, name = resolver(conflict)
    else:
        raise ValueError('Unresolved conflict: ' + conflict['name'] +
                         '. Use an interactive terminal or --conflict rename, skip or overwrite.')
    if action not in ('rename', 'skip', 'overwrite'):
        raise ValueError('Migration cancelled; no data imported')
    if action == 'overwrite' and not conflict['can_overwrite']:
        raise ValueError('This name matches multiple sessions or a planned item; rename or skip instead')
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 180
                             or any(ord(c) < 32 for c in name)):
        raise ValueError('Rename must be 1–180 characters without control characters')
    return action, name.strip() if name is not None else None


def _prune(rows, selected, mode):
    # Reuse the same selection code against a validated, disposable store.
    store = WWStore(':memory:') if mode == 'ww' else TrackerStore(':memory:')
    try:
        from .transfers import _insert
        with store._db:
            store._db.execute('DELETE FROM current_session')
            store._db.execute('DELETE FROM sessions')
            store._db.execute('DELETE FROM history_folders')
            _insert(store._db, 'main', rows)
        return collect(store, selected, mode, archive_limits=False)
    finally:
        store.close()


def _session_conflicts(target, payload, *, apply, policy, resolver):
    conflicts, replacements = [], {}
    for mode, rows in list(payload['modes'].items()):
        existing = {}
        path = mode_dir(target, mode) / 'tracker.sqlite3'
        if path.exists():
            plain_file(path, target)
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            try:
                for row in db.execute('SELECT s.id,f.name FROM sessions s JOIN history_folders f ON f.id=s.folder_id'):
                    existing.setdefault(row['name'], []).append({'id': row['id']})
            finally:
                db.close()
        keep = []
        folders = {f['id']: f for f in rows['history_folders']}
        for session in rows['sessions']:
            folder = folders[session['folder_id']]
            name = folder['name']
            matches = existing.get(name, [])
            if matches:
                conflict = {'kind': 'session', 'mode': mode, 'name': name,
                            'can_overwrite': len(matches) == 1 and matches[0] is not None}
                conflicts.append(conflict)
                if apply:
                    action, renamed = _choice(conflict, policy, resolver)
                    if action == 'skip':
                        continue
                    if action == 'overwrite':
                        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                        db.row_factory = sqlite3.Row
                        try:
                            fingerprint = session_fingerprint(db, 'main', mode, matches[0]['id'], archive_limits=False)
                        finally:
                            db.close()
                        replacements.setdefault(mode, {})[session['id']] = matches[0] | {'fingerprint': fingerprint}
                    else:
                        if renamed is None:
                            index = 2
                            renamed = name[:160] + f' (imported {index})'
                            while renamed in existing:
                                index += 1
                                renamed = name[:160] + f' (imported {index})'
                        if renamed in existing:
                            raise ValueError('Renamed session already exists; choose another name')
                        folder['name'] = renamed
            keep.append(session['id'])
            existing.setdefault(folder['name'], []).append(None)
        if apply:
            if keep and len(keep) != len(rows['sessions']):
                payload['modes'][mode] = _prune(rows, keep, mode)
            elif not keep:
                del payload['modes'][mode]
    return conflicts, replacements


def _asset_conflicts(assets, *, apply, policy, resolver, model_dir):
    conflicts, planned, result = [], {}, []
    for original, destination in assets:
        safe_path(destination, destination.parent)
        previous = planned.get(destination)
        present = destination.exists()
        if present:
            plain_file(destination, destination.parent)
        other = previous or (destination if present else None)
        if other and _digest(other) == _digest(original):
            result.append((original, destination, 'reuse'))
            continue
        fingerprint = None
        if other:
            conflict = {'kind': 'asset', 'name': str(destination), 'can_overwrite': previous is None}
            conflicts.append(conflict)
            if apply:
                action, renamed = _choice(conflict, policy, resolver)
                if action == 'skip':
                    continue
                if action == 'overwrite':
                    fingerprint = _digest(destination)
                else:
                    if destination.is_relative_to(model_dir):
                        raise ValueError('Model filenames cannot be renamed; choose a separate --model-dir, skip or overwrite')
                    renamed = renamed or f'{destination.stem}-{_digest(original)[:16]}{destination.suffix}'
                    if (Path(renamed).name != renamed or '/' in renamed or "\\" in renamed
                            or renamed in ('.', '..') or ':' in renamed
                            or Path(renamed).suffix.lower() != destination.suffix.lower()):
                        raise ValueError('Asset rename must be a basename with the same extension')
                    destination = safe_path(destination.with_name(renamed), destination.parent)
                    if destination.exists() or destination in planned:
                        raise ValueError('Renamed asset already exists; choose another name')
        planned[destination] = original
        result.append((original, destination, fingerprint))
    return conflicts, result


def _read_settings(directory):
    path = directory / 'settings.json'
    if not path.exists():
        return {}
    plain_file(path, directory)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Settings file is too large')
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values, dict):
        raise ValueError('Settings must be an object')
    return values


def _course_plan(source_data, target, pictures, warnings, *, apply, policy, resolver):
    """Activate bounded display copies independently of raw-asset conflict choices."""
    library = CourseImageLibrary(target)
    records = dict(library.records)
    sources = []
    path = source_data / 'course-images.json'
    if path.exists():
        try:
            plain_file(path, source_data)
            if path.stat().st_size > course_images.MAX_LIBRARY_BYTES:
                raise ValueError('course-image library exceeds the display size limit')
            values = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(values, dict):
                raise ValueError('course-image library must be an object')
            for value in values.values():
                if not isinstance(value, dict):
                    warnings.append('Invalid course-image record; original library retained unless skipped.')
                    continue
                sources.append((value.get('course'), value.get('icon_base64'), path.name))
        except (ValueError, UnicodeError) as error:
            warnings.append(f'{path.name}: original retained unless skipped; not activated ({error}).')
    seen, conflicts, changed = set(), [], 0

    def candidates():
        # Runtime associations take priority, then static files, then retained files.
        yield from sources
        for path in pictures:
            course = course_from_filename(path.name)
            if not course:
                warnings.append(f'{path.name}: original retained unless skipped; no exact known course filename match.')
                continue
            if path.stat().st_size > 4 * 1024 * 1024:
                warnings.append(f'{path.name}: original retained unless skipped; exceeds the course image size limit.')
                continue
            yield course, base64.b64encode(path.read_bytes()).decode(), path.name

    for course, encoded, label in candidates():
        try:
            record = library.prepare(course, encoded)
        except ValueError as error:
            warnings.append(f'{label}: original retained unless skipped; not activated ({error}).')
            continue
        course = record['course']
        if record['id'] in seen:
            continue
        seen.add(record['id'])
        matches = [records[record['id']]] if record['id'] in records else []
        if matches and matches[0]['icon_base64'] == record['icon_base64']:
            continue
        if matches:
            item = {'kind': 'course-image', 'name': course, 'can_overwrite': True}
            conflicts.append(item)
            if apply:
                action, _ = _choice(item, policy, resolver)
                if action != 'overwrite':
                    if action == 'rename':
                        warnings.append(f'{course}: existing course image kept; course associations cannot be renamed. '
                                        'Original asset is retained under the asset conflict policy.')
                    continue
            record['id'] = matches[0]['id']
        candidate = records | {record['id']: record}
        if (len(candidate) > course_images.MAX_RECORDS or
                len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > course_images.MAX_LIBRARY_BYTES):
            warnings.append(f'{course}: course display library limit; original retained unless skipped.')
            continue
        records = candidate
        changed += 1
    return library, records, conflicts, changed


def migrate(source, target, *, source_data=None, videos=False, models=False, model_dir=None,
            apply=False, conflict=None, theme_conflict=None, course_image_conflict=None, resolver=None, categories=None):
    """Preview by default; never open the source with a writable SQLite connection.

    The original tracker and target tracker must be closed for application. DB,
    WAL and schema upgrades are handled only in disposable copies. Importing the
    same project twice intentionally adds another independent copy of sessions.
    """
    selected = (set(DEFAULT_CATEGORIES) | ({'videos'} if videos else set()) |
                ({'models'} if models else set())) if categories is None else set(categories)
    if not selected or not selected <= set(CATEGORIES):
        raise ValueError('Select at least one valid migration category')
    if 'evidence' in selected and 'sessions' not in selected:
        raise ValueError('Evidence requires sessions')
    videos, models = 'videos' in selected, 'models' in selected
    source, source_data = _source_paths(source, source_data)
    target = Path(target).absolute()
    if (target.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(target.resolve())
            or target.resolve().is_relative_to(source_data.resolve())
            or source_data.resolve().is_relative_to(target.resolve())):
        raise ValueError('Source and target directories must be separate')
    for directory in (source, source_data, target):
        for part in (directory, *directory.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError('Source and target directories cannot contain links')
    inventory = inspect_source(source, source_data=source_data)
    payload = {'format': FORMAT, 'version': VERSION, 'modes': {}}
    with tempfile.TemporaryDirectory(prefix='mkw-migrate-') as temporary:
        for mode in MODES if 'sessions' in selected else ():
            original = mode_dir(source_data, mode) / 'tracker.sqlite3'
            if not original.exists():
                continue
            _closed_database(original, source_data)
            # Avoid torn copies of databases actively being written. No checkpoint,
            # migration, journal cleanup or SQLite connection touches the original.
            before = original.stat()
            copied = Path(temporary) / f'{mode}.sqlite3'
            shutil.copyfile(original, copied)
            after = original.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Source changed during migration; close the original tracker')
            store = WWStore(copied) if mode == 'ww' else TrackerStore(copied)
            try:
                payload['modes'][mode] = collect(store, mode=mode, archive_limits=False)
                if 'evidence' not in selected:
                    payload['modes'][mode]['race_evidence'] = []
            finally:
                store.close()
    if 'sessions' in selected and not payload['modes']:
        raise ValueError('No tracker.sqlite3 found; specify --source-data-dir for a custom location')
    if payload['modes']:
        validate(payload, archive_limits=False)
    session_conflicts, replacements = _session_conflicts(target, payload, apply=apply, policy=conflict, resolver=resolver)
    evidence = local_evidence(source_data, payload)
    assets = []
    warnings = []
    theme_pictures = set()
    course_pictures = []
    static = source / 'mkw_tracker' / 'static'
    for kind in ('character-icons', 'track-icons') if 'pictures' in selected else ():
        for path in _files(static / kind):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                assets.append((path, _destination(path, target / 'imported-assets' / kind)))
                # Keep every original picture. Picker conversion is a separate,
                # bounded display concern and must not abort an entire history.
                if kind == 'track-icons':
                    course_pictures.append(path)
                if kind == 'character-icons':
                    if path.stat().st_size > 4 * 1024 * 1024:
                        warnings.append(f'{path.name}: original retained; exceeds the theme picker image size limit.')
                    else:
                        try:
                            _image(base64.b64encode(path.read_bytes()).decode())
                            theme_pictures.add(path)
                        except ValueError as error:
                            warnings.append(f'{path.name}: original retained; not added to theme picker ({error}).')
    if 'pictures' in selected:
        retained = source_data / 'imported-assets' / 'track-icons'
        for path in _files(retained):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                destination = target / 'imported-assets' / 'track-icons' / 'retained' / path.relative_to(retained).parent
                assets.append((path, _destination(path, destination)))
                course_pictures.append(path)
        path = source_data / 'course-images.json'
        if path.exists():
            assets.append((plain_file(path, source_data), _destination(path, target / 'imported-assets')))
    for directory in (static / 'fonts', source_data / 'fonts') if 'fonts' in selected else ():
        for path in _files(directory):
            if path.suffix.lower() in ('.ttf', '.otf'):
                with path.open('rb') as stream:
                    if stream.read(4) not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
                        raise ValueError('Invalid TTF/OTF font')
                assets.append((path, _destination(path, target / 'fonts')))
                if path.stat().st_size > 64 * 1024 * 1024:
                    warnings.append(f'{path.name}: original retained; too large for the current font selector.')
    if videos:
        for mode in MODES:
            for path in _files(mode_dir(source_data, mode) / 'uploads'):
                if path.suffix.lower() in VIDEO_SUFFIXES:
                    assets.append((path, _destination(path, target / 'imported-videos' / mode)))
    model_dir = Path(model_dir).absolute() if model_dir else target.parent / 'models'
    if models:
        if any(model_dir.resolve().is_relative_to(original.resolve()) or original.resolve().is_relative_to(model_dir.resolve())
               for original in (source, source_data)):
            raise ValueError('Model destination must be separate from the original project')
        safe_path(model_dir, model_dir)
        model_root = source / '.local' / 'models'
        for path in _files(model_root):
            if path.name in MODEL_NAMES or path.suffix.lower() in ('.safetensors', '.gguf'):
                # Models are portable data only. Never copy venvs, binaries, Python,
                # pickles, .env, caches, credentials, or executable path preferences.
                relative = path.relative_to(model_root)
                destination = _destination(path, model_dir / relative.parent)
                assets.append((path, destination))
    source_library = None
    if 'pictures' in selected:
        library_source = source_data / 'custom-themes.json'
        if library_source.exists():
            plain_file(library_source, source_data)
            assets.append((library_source, _destination(library_source, target / 'imported-assets')))
        try:
            source_library = ThemeLibrary(source_data)
        except ValueError as error:
            warnings.append(f'Original custom-theme library retained as an asset; not added to picker ({error}).')
    asset_conflicts, asset_plan = _asset_conflicts(assets, apply=apply, policy=conflict, resolver=resolver, model_dir=model_dir)
    assets = [(src, dst) for src, dst, _ in asset_plan]
    # Prepare custom theme records using existing image/palette validation.
    library = ThemeLibrary(target) if 'pictures' in selected else None
    records = dict(library.records) if library else {}
    preferences_selected = bool(selected & {'pictures', 'fonts'})
    source_settings = {}
    if preferences_selected:
        try:
            source_settings = _read_settings(source_data)
        except ValueError as error:
            warnings.append(f'Source preferences could not be read; existing target choices kept ({error}).')
    settings = _read_settings(target) if preferences_selected else {}
    selected_theme = None
    theme_sources = list(source_library.records.values()) if source_library else []
    def key(value):
        return ''.join(c for c in value.lower() if c.isalnum())
    pictures = {key(p.stem.removeprefix('Character_')): p for p in sorted(theme_pictures)}
    for theme in all_themes():
        picture = pictures.get(key(theme['name']))
        if picture and not (source_library and theme['id'] in source_library.records):
            theme_sources.append({k: theme[k] for k in ('id', 'name', 'tokens', 'team_colors')} |
                                 {'icon_base64': base64.b64encode(picture.read_bytes()).decode()})
    theme_conflicts = []
    changed_themes = 0
    builtins = {theme['id']: theme for theme in all_themes()}
    theme_sources.sort(key=lambda t: t['id'] != source_settings.get('theme', 'mario'))
    for theme in theme_sources:
        old_id = theme['id']
        record = library.prepare({k: v for k, v in theme.items() if k != 'id'})
        # Keep the previous deterministic ID recognizable for repair and reruns.
        record['id'] = 'custom-' + hashlib.sha256(json.dumps(
            {k: v for k, v in record.items() if k != 'id'}, sort_keys=True).encode()).hexdigest()[:32]
        effective = builtins | records
        matches = [r for r in effective.values() if r['name'] == record['name']]
        builtin_match = next((r for r in matches if r['id'] in builtins), None)
        # Already effective imports need no confirmation, unless duplicate names
        # remain from an older migration and explicitly need repair.
        equivalent = [r for r in matches if {k: v for k, v in r.items() if k != 'id'} ==
                      {k: v for k, v in record.items() if k != 'id'}]
        if len(matches) == 1 and equivalent:
            if old_id == source_settings.get('theme', 'mario'):
                selected_theme = equivalent[0]['id']
            continue
        removed = []
        if matches:
            item = {'kind': 'theme', 'name': record['name'], 'can_overwrite': True,
                    'matching_ids': [r['id'] for r in matches]}
            theme_conflicts.append(item)
            if apply:
                action, renamed = _choice(item, theme_conflict or conflict, resolver)
                if action == 'skip':
                    continue
                if action == 'overwrite':
                    # The explicit name-conflict choice replaces all displayed
                    # same-name records, never unrelated/custom-renamed themes.
                    record['id'] = (builtin_match or matches[0])['id']
                    removed = [r['id'] for r in matches if r['id'] != record['id'] and r['id'] in records]
                else:
                    if renamed is None:
                        base = record['name'][:60] + ' (imported)'
                        renamed = base
                        number = 2
                        while any(r['name'] == renamed for r in effective.values()):
                            renamed = f'{base} {number}'
                            number += 1
                    if len(renamed) > 80 or any(r['name'] == renamed for r in effective.values()):
                        raise ValueError('Theme rename must be a unique name of at most 80 characters')
                    record['name'] = renamed
                    record['id'] = 'custom-' + uuid4().hex
        candidate = {k: v for k, v in records.items() if k not in removed} | {record['id']: record}
        if sum(identifier not in builtins for identifier in candidate) > 50:
            warnings.append('Theme limit (50): ' + record['name'] +
                            ' not added to the picker; pictures are retained as imported assets unless skipped.')
            continue
        if len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > theme_library.MAX_LIBRARY_BYTES:
            warnings.append('Theme picker size limit: ' + record['name'] +
                            ' not added to the picker; original theme assets are retained unless skipped.')
            continue
        records = candidate
        changed_themes += 1
        if settings.get('theme') in removed:
            settings['theme'] = record['id']
        if old_id == source_settings.get('theme', 'mario'):
            selected_theme = record['id']
    course_library, course_records, course_conflicts, changed_courses = None, {}, [], 0
    if 'pictures' in selected:
        course_library, course_records, course_conflicts, changed_courses = _course_plan(
            source_data, target, course_pictures, warnings, apply=apply,
            policy=course_image_conflict or conflict, resolver=resolver)
    if selected_theme and 'theme' not in settings:
        settings['theme'] = selected_theme
    preferred_font = source_settings.get('report_font') or 'RacersDelight.otf'
    preferred_source = None
    if isinstance(preferred_font, str) and Path(preferred_font).name == preferred_font:
        preferred_source = source_data / 'fonts' / preferred_font
        if not preferred_source.exists():
            preferred_source = static / 'fonts' / preferred_font
    font = next((dest.name for path, dest in assets if dest.parent == target / 'fonts'
                 and path == preferred_source), None)
    # The font feature owns validation/resolution. Older builds still preserve
    # imported font assets without adding a setting they cannot yet read.
    if font and 'report_font' not in settings and 'report_font' in DEFAULT_SETTINGS:
        settings['report_font'] = font
    result = summary(payload) | {'policy': 'Import new identities; replace a target object only after an explicit Overwrite conflict choice.', 'files': len(assets), 'bytes': sum(p.stat().st_size for p, _ in assets),
                                 'themes': changed_themes, 'course_images': changed_courses, 'conflicts': session_conflicts + asset_conflicts + theme_conflicts + course_conflicts, 'warnings': warnings,
                                 'font': font, 'model_dir': str(model_dir) if models else None, 'videos': videos, 'models': models, 'applied': False,
                                 'categories': [c for c in CATEGORIES if c in selected], 'inventory': inventory,
                                 'evidence_files': len(evidence), 'evidence_bytes': sum(item.size for item in evidence.values())}
    if not apply:
        return result
    # Preflight before scores. Explicit overwrites retain same-filesystem backups
    # so rollback restores old inodes atomically, even when no disk space remains.
    target.mkdir(parents=True, exist_ok=True)
    created = []
    replaced = []
    rollback = Path(tempfile.mkdtemp(prefix='.migration-rollback-', dir=target))
    preferences = {}
    completed = restored = False
    try:
        preference_files = (['course-images.json'] if course_library else []) + (['custom-themes.json'] if library else []) + (['settings.json'] if preferences_selected else [])
        for name in preference_files:
            path = target / name
            backup = rollback / name if path.exists() else None
            if backup is not None:
                shutil.copyfile(path, backup)
            preferences[name] = backup
        for original, destination, fingerprint in asset_plan:
            safe_path(destination, destination.parent)
            destination.parent.mkdir(parents=True, exist_ok=True)
            for parent in (destination.parent, *destination.parents):
                if parent.is_symlink():
                    raise ValueError('Target asset directory cannot be a link')
            if fingerprint == 'reuse':
                continue
            if fingerprint is not None:
                if not destination.exists() or _digest(destination) != fingerprint:
                    raise ValueError('Target asset changed since conflict resolution; preview again')
                backup = destination.with_name('.migration-backup-' + uuid4().hex)
                temporary = destination.with_name('.migrate-' + uuid4().hex)
                try:
                    with original.open('rb') as src, temporary.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    replaced.append((destination, backup))
                    destination.replace(backup)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                with original.open('rb') as src, destination.open('xb') as dst:
                    created.append(destination)
                    shutil.copyfileobj(src, dst)
        if course_library and course_records != course_library.records:
            course_library._write(course_records)
        if library and records != library.records:
            library._write(records)
        if preferences_selected and settings != _read_settings(target):
            temporary = target / f'.migration-{uuid4().hex}.tmp'
            try:
                temporary.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
                temporary.replace(target / 'settings.json')
            finally:
                temporary.unlink(missing_ok=True)
        if payload['modes']:
            merge(target, payload, evidence, replacements=replacements, archive_limits=False)
        completed = True
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        for destination, backup in reversed(replaced):
            if backup.exists():
                backup.replace(destination)
        for name, backup in preferences.items():
            path = target / name
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                backup.replace(path)
        restored = True
        raise
    finally:
        # If rollback itself fails, retain exact backups for recovery rather than
        # deleting the only surviving copy of a user's original target asset.
        if completed or restored:
            try:
                for _, backup in replaced:
                    backup.unlink(missing_ok=True)
                shutil.rmtree(rollback)
            except OSError:
                if completed:
                    warnings.append('Import completed; some temporary rollback copies could not be removed.')
                # A cleanup failure must not report committed scores as a failed import.
    result['applied'] = True
    return result


def _interactive(conflict):
    print(f"Conflict ({conflict['kind']}): {conflict['name']}")
    choices = '[r]ename, [s]kip, [o]verwrite, [a]bort' if conflict['can_overwrite'] else '[r]ename, [s]kip, [a]bort'
    if conflict['kind'] == 'course-image':
        choices = '[s]kip, [o]verwrite, [a]bort'
    if conflict['kind'] == 'theme' and conflict['can_overwrite']:
        print('Overwrite replaces every target theme with this exact name (' + str(len(conflict.get('matching_ids', []))) + ' matches).')
        choices += ', [w] overwrite all remaining theme conflicts only'
    try:
        choice = input(choices + ': ').strip().lower()
        action = {'r': 'rename', 's': 'skip', 'o': 'overwrite', 'w': 'overwrite-themes'}.get(choice, choice)
        if conflict['kind'] == 'course-image' and action == 'rename':
            return 'abort', None
        name = input('New name (blank for an automatic name): ').strip() or None if action == 'rename' else None
        return action, name
    except (EOFError, KeyboardInterrupt):
        return 'abort', None


def _interactive_resolver():
    overwrite_themes = False

    def resolve(item):
        nonlocal overwrite_themes
        if overwrite_themes and item['kind'] == 'theme':
            return 'overwrite', None
        action, name = _interactive(item)
        if action == 'overwrite-themes' and item['kind'] == 'theme':
            overwrite_themes = True
            return 'overwrite', None
        return action, name

    return resolve


def _size(value):
    for unit in ('bytes', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:,.1f} {unit}' if unit != 'bytes' else f'{value:,} bytes'
        value /= 1024


def _show_inventory(inventory, selected, target, model_dir):
    print('\nLocal migration — select what to copy')
    for index, category in enumerate(CATEGORIES, 1):
        item = inventory['categories'][category]
        print(f" {index}. [{'x' if category in selected else ' '}] {category}: "
              f"{CATEGORY_LABELS[category]} — {item['count']:,} {item['unit']}, {_size(item['bytes'])}")
        unavailable = inventory.get('unavailable', {}).get(category, [])
        if unavailable:
            print(f'    Incomplete estimate: {len(unavailable):,} unavailable item(s): {unavailable[0]}')
    print(f"Saved races: {inventory['races']:,}")
    total = sum(inventory['categories'][c]['bytes'] for c in selected)
    print(f'Selected source data: approximately {_size(total)}')
    if 'sessions' in selected:
        print(f"Temporary database copies: approximately {_size(inventory['database_bytes'])} "
              f"in {tempfile.gettempdir()}")
    destinations = [('Tracker destination', Path(target).absolute())]
    if 'models' in selected:
        destinations.append(('Model destination', Path(model_dir).absolute() if model_dir else Path(target).absolute().parent / 'models'))
    for label, destination in destinations:
        existing = destination
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        print(f'{label}: {destination} (filesystem free: {_size(shutil.disk_usage(existing).free)})')
    print('Sizes are estimates before conflict choices; database journals, theme copies and rollback need extra disk space.')
    print('Evidence/video/model files are copied incrementally; record metadata uses memory proportional to history size.')
    print('Everything includes uploaded videos and portable model data, not external recording originals, credentials or environments.')
    print('Evidence requires sessions; toggles keep that dependency selected.')
    print('1–6 toggle | a = select everything | n = select none | p = preview | m = migrate | q = cancel')


def _show_preview(result):
    print(f"\nPreview: {len(result['sessions']):,} sessions, {result['evidence_files']:,} evidence images "
          f"({_size(result['evidence_bytes'])}), {result['files']:,} other files ({_size(result['bytes'])}), {result['course_images']:,} course images.")
    print(f"Conflicts requiring choices: {len(result['conflicts']):,}")
    for item in result['conflicts'][:20]:
        print(f"  {item['kind']}: {item['name']}")
    if len(result['conflicts']) > 20:
        print('  Additional conflicts will be shown individually during migration.')
    for warning in result['warnings']:
        print('Warning: ' + warning)


def _terminal(source, target, selected, options):
    print('Close both trackers before continuing. Scanning local files; no data is being imported...')
    inventory = inspect_source(source, source_data=options.get('source_data'))
    while True:
        _show_inventory(inventory, selected, target, options.get('model_dir'))
        try:
            command = input('Selection: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            command = 'q'
        if command == 'q':
            print('Cancelled; no data imported.')
            return
        if command in ('a', 'n'):
            selected = set(CATEGORIES) if command == 'a' else set()
        elif command in tuple(str(i) for i in range(1, 7)):
            category = CATEGORIES[int(command) - 1]
            selected.symmetric_difference_update({category})
            if category == 'sessions' and category not in selected:
                selected.discard('evidence')
            if category == 'evidence' and category in selected:
                selected.add('sessions')
        elif command in ('p', 'm'):
            if not selected:
                print('Choose at least one category.')
                continue
            print('Preparing selected migration preview...')
            try:
                result = migrate(source, target, categories=selected, **options)
            except (OSError, ValueError, sqlite3.Error) as error:
                print(f'Cannot preview selected data: {error}. Adjust the selection or cancel.')
                continue
            _show_preview(result)
            if command == 'p':
                continue
            try:
                confirmed = input('Type yes to migrate the selected data (anything else cancels): ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                print('Cancelled; no data imported.')
                return
            if confirmed != 'yes':
                print('Not confirmed; no data imported.')
                continue
            print('Migrating selected data. Large histories and model files may take time...')
            result = migrate(source, target, categories=selected, apply=True, **options)
            print(f"Migration complete: {len(result['sessions']):,} sessions, "
                  f"{result['evidence_files']:,} evidence images, {result['files']:,} other files, {result['course_images']:,} course images.")
            for warning in result['warnings']:
                print('Warning: ' + warning)
            return
        else:
            print('Unknown command; choose 1–6, a, n, p, m or q.')


def main():
    parser = argparse.ArgumentParser(description='Copy mkw-livesplit-vos sessions and assets without changing the original')
    parser.add_argument('source', type=Path, help='Original project folder')
    parser.add_argument('--data-dir', type=Path, default=Path('.local/tracker'), help='Target tracker data directory')
    parser.add_argument('--source-data-dir', type=Path, help='Original custom data directory')
    parser.add_argument('--include-videos', action='store_true', help='Copy managed uploads (not external path recordings)')
    parser.add_argument('--include-models', action='store_true', help='Copy portable model files, never runtimes')
    parser.add_argument('--model-dir', type=Path, help='Target model directory (default: data-dir parent/models; normally .local/models)')
    parser.add_argument('--conflict', choices=('rename', 'skip', 'overwrite'), help='Explicit policy for all conflicts; otherwise ask in an interactive terminal')
    parser.add_argument('--theme-conflict', choices=('rename', 'skip', 'overwrite'),
                        help='Policy only for same-name themes; overrides --conflict for themes')
    parser.add_argument('--course-image-conflict', choices=('skip', 'overwrite'),
                        help='Policy only for existing course pictures; overrides --conflict for course associations')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--everything', action='store_true', help='Select all categories, including uploaded videos and portable models')
    selection.add_argument('--only', help='Select comma-separated categories: ' + ','.join(CATEGORIES))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Apply selected categories without the menu/final prompt; both trackers must be closed')
    mode.add_argument('--preview', action='store_true', help='Print a JSON preview without prompts or target writes')
    parser.add_argument('--non-interactive', action='store_true', help='Disable the terminal menu and conflict prompts; without --apply, preview only')
    args = parser.parse_args()
    selected = set(CATEGORIES) if args.everything else set(DEFAULT_CATEGORIES)
    if args.only is not None:
        selected = {part.strip() for part in args.only.split(',')}
        if not selected or not selected <= set(CATEGORIES):
            parser.error('--only requires valid comma-separated categories: ' + ','.join(CATEGORIES))
        if 'evidence' in selected:
            selected.add('sessions')
    if args.include_videos:
        selected.add('videos')
    if args.include_models:
        selected.add('models')
    terminal = sys.stdin.isatty() and not args.non_interactive
    options = {'source_data': args.source_data_dir, 'model_dir': args.model_dir,
               'conflict': args.conflict, 'theme_conflict': args.theme_conflict,
               'course_image_conflict': args.course_image_conflict,
               'resolver': _interactive_resolver() if terminal and not args.preview else None}
    try:
        if terminal and not (args.apply or args.preview):
            _terminal(args.source, args.data_dir, selected, options)
            return
        result = migrate(args.source, args.data_dir, categories=selected, apply=args.apply, **options)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
