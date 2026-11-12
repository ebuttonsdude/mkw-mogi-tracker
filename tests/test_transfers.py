"""Only temporary sessions, generated images and fake secrets cross transfers."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from mkw_tracker import transfers
from mkw_tracker.migrate import migrate
from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_store import WWStore


def png():
    from PIL import Image
    stream = BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(stream, 'PNG')
    return stream.getvalue()


def populate(root, mode='mogi'):
    directory = transfers.mode_dir(root, mode)
    directory.mkdir(parents=True, exist_ok=True)
    store = WWStore(directory / 'tracker.sqlite3') if mode == 'ww' else TrackerStore(directory / 'tracker.sqlite3')
    session = store.active_session()['id']
    recording = store.create_recording('Recording 選手')
    store.create_race('same-race-id', 'Course', recording['id'], 1200)
    if mode == 'ww':
        store.set_player_name('選手', session)
        store.set_ww_result('same-race-id', position=2, vr_after=9000, expected_session_id=session)
        store.set_ww_result('same-race-id', position=1, vr_after=9001, expected_session_id=session)
    else:
        players = [store.add_player(f'選手 {i}')['id'] for i in range(12)]
        team = store.create_team('Team')['id']
        store.update_player(players[0], '選手 0', team)
        store.submit_result('same-race-id', players)
        store.correct_result('same-race-id', players[::-1])
        store.add_penalty('same-race-id', players[0], 2, 'test')
    store.create_race('pending', 'Pending')
    name = str(uuid4()) + '.png'
    directory = transfers.evidence_dir(root, mode)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(png())
    store.add_evidence('same-race-id', name)
    return store


class TransfersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'

    def payload(self, modes=('mogi',)):
        payload = {'format': transfers.FORMAT, 'version': 1, 'modes': {}}
        for mode in modes:
            store = populate(self.source, mode)
            try:
                payload['modes'][mode] = transfers.collect(store, mode=mode)
            finally:
                store.close()
        return payload

    def test_roundtrip_all_modes_preserves_revisions_pending_and_existing(self):
        payload = self.payload(transfers.MODES)
        evidence = transfers.read_evidence(self.source, payload)
        archive = transfers.encode_archive(payload, evidence)
        restored, images = transfers.decode_archive(archive)
        for mode in transfers.MODES:
            store = populate(self.target, mode)
            store.close()
        transfers.merge(self.target, restored, images)
        transfers.merge(self.target, restored, images)
        for mode in transfers.MODES:
            cls = WWStore if mode == 'ww' else TrackerStore
            store = cls(transfers.mode_dir(self.target, mode) / 'tracker.sqlite3')
            try:
                self.assertEqual(len(store._rows('SELECT * FROM sessions')), 3)
                self.assertEqual(len(store._rows('SELECT * FROM races WHERE revision=0')), 3)
                self.assertEqual(len(store._rows('SELECT * FROM race_evidence')), 3)
                self.assertEqual(len(store._rows('SELECT * FROM ww_results' if mode == 'ww'
                                                else 'SELECT * FROM result_revisions')), 6)
                self.assertEqual(store._rows('SELECT * FROM races WHERE id="same-race-id"')[0]['revision'], 2)
                self.assertFalse(store._rows('PRAGMA foreign_key_check'))
            finally:
                store.close()

    def test_schema_relationships_and_scores_rejected(self):
        original = self.payload()
        for mutate in (
                lambda p: p['modes']['mogi']['placements'].pop(),
                lambda p: p['modes']['mogi']['sessions'][0].update(format='bad'),
                lambda p: p['modes']['mogi']['races'][0].update(folder_id='missing'),
                lambda p: p['modes']['mogi']['players'][0].update(password='secret'),
                lambda p: p.update(version=2)):
            payload = deepcopy(original)
            mutate(payload)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                transfers.validate(payload)

    def test_archive_paths_links_duplicates_and_unknown_files_rejected(self):
        payload = self.payload()
        manifest = json.dumps(payload)
        for name, mode in (('../escape', 0), ('/abs', 0), ('obs-connection.json', 0),
                           ('evidence/mogi/' + str(uuid4()) + '.png', 0o120777)):
            stream = BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('manifest.json', manifest)
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, b'fake-secret')
            with self.subTest(name=name), self.assertRaises(ValueError):
                transfers.decode_archive(stream.getvalue())

    def test_selected_sessions_and_credentials_exclusion(self):
        store = populate(self.source)
        try:
            selected = store.active_session()['id']
            store.create_session('Excluded', selected)
            rows = transfers.collect(store, [selected])
            self.assertEqual(len(rows['sessions']), 1)
            self.assertEqual(len(rows['races']), 2)
            store._db.execute('CREATE TABLE credentials(password TEXT)')
            store._db.execute('INSERT INTO credentials VALUES("fake-secret")')
            store._db.commit()
            (self.source / 'obs-connection.json').write_text('{"password":"fake-secret"}')
            payload = {'format': transfers.FORMAT, 'version': 1, 'modes': {'mogi': rows}}
            archive = transfers.encode_archive(payload, transfers.read_evidence(self.source, payload))
            with zipfile.ZipFile(BytesIO(archive)) as z:
                self.assertNotIn(b'fake-secret', b''.join(z.read(n) for n in z.namelist()))
        finally:
            store.close()

    def test_failure_rolls_back_all_mode_rows_and_new_evidence(self):
        payload = self.payload(('mogi', 'ffa'))
        evidence = transfers.read_evidence(self.source, payload)
        existing = populate(self.target)
        before = existing.snapshot()
        existing.close()
        original_insert = transfers._insert
        def fail(db, schema, rows):
            original_insert(db, schema, rows)
            if schema == 'ffa':
                raise OSError('simulated disk error')
        with patch.object(transfers, '_insert', side_effect=fail), self.assertRaises(OSError):
            transfers.merge(self.target, payload, evidence)
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(store.snapshot(), before)
            self.assertEqual(len(list((self.target / 'evidence').glob('*.png'))), 1)
        finally:
            store.close()

    def test_rejects_linked_evidence_ancestors_and_broken_links(self):
        payload = self.payload()
        evidence = transfers.read_evidence(self.source, payload)
        outside = self.root / 'outside'
        outside.mkdir()
        self.target.mkdir()
        (self.target / 'evidence').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            transfers.merge(self.target, payload, evidence)
        self.assertEqual(list(outside.iterdir()), [])
        (self.target / 'evidence').unlink()
        (self.target / 'evidence').symlink_to(outside / 'missing', target_is_directory=True)
        with self.assertRaises(ValueError):
            transfers.merge(self.target, payload, evidence)


class MigrationTests(unittest.TestCase):
    setUp = TransfersTests.setUp
    def source_project(self):
        project = self.root / 'upstream'
        data = project / '.local' / 'tracker'
        store = populate(data)
        store.close()
        (data / 'obs-connection.json').write_text('{"password":"fake-secret"}')
        (data / 'settings.json').write_text('{"theme":"mario"}')
        static = project / 'mkw_tracker' / 'static'
        for kind, name, content in (
                ('character-icons', 'Character_Mario.png', png()),
                ('track-icons', 'Course.png', png()),
                ('fonts', 'RacersDelight.otf', b'OTTO' + bytes(64))):
            folder = static / kind
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(content)
        models = project / '.local' / 'models' / 'qwen'
        models.mkdir(parents=True)
        (models / 'model.safetensors').write_bytes(b'fake weights never loaded')
        (models / 'runtime.json').write_text('{"executable":"/bad/absolute/path"}')
        (models / 'code.py').write_text('raise RuntimeError()')
        (data / 'uploads').mkdir()
        (data / 'uploads' / 'recording.MP4').write_bytes(b'fake video never decoded')
        return project, data

    def test_preview_then_apply_source_unchanged_assets_and_optional_data(self):
        project, data = self.source_project()
        before = {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()}
        preview = migrate(project, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(preview['applied'])
        result = migrate(project, self.target, apply=True, theme_conflict='rename')
        self.assertTrue(result['applied'])
        self.assertTrue((self.target / 'fonts' / 'RacersDelight.otf').exists())
        self.assertTrue((self.target / 'custom-themes.json').exists())
        self.assertFalse((self.target / 'obs-connection.json').exists())
        self.assertFalse((self.target / 'imported-videos').exists())
        self.assertFalse((self.root / 'models').exists())
        migrate(project, self.target, videos=True, models=True, apply=True, theme_conflict='rename', conflict='rename')
        self.assertTrue((self.root / 'models' / 'qwen' / 'model.safetensors').exists())
        self.assertFalse((self.root / 'models' / 'qwen' / 'runtime.json').exists())
        self.assertFalse((self.root / 'models' / 'qwen' / 'code.py').exists())
        self.assertTrue((self.target / 'imported-videos' / 'mogi' / 'recording.MP4').exists())
        self.assertEqual(before, {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()})

    def test_font_preference_preserves_explicit_default_and_collisions(self):
        project, _ = self.source_project()
        self.target.mkdir()
        (self.target / 'settings.json').write_text('{"report_font":""}')
        with patch.dict('mkw_tracker.migrate.DEFAULT_SETTINGS', {'report_font': ''}):
            migrate(project, self.target, apply=True, theme_conflict='rename', conflict='rename')
        self.assertEqual(json.loads((self.target / 'settings.json').read_text())['report_font'], '')
        (self.target / 'settings.json').write_text('{}')
        with patch.dict('mkw_tracker.migrate.DEFAULT_SETTINGS', {'report_font': ''}):
            migrate(project, self.target, apply=True, theme_conflict='rename', conflict='rename')
        self.assertEqual(json.loads((self.target / 'settings.json').read_text())['report_font'], 'RacersDelight.otf')

    def test_migration_failure_preserves_existing_preferences_and_sessions(self):
        project, _ = self.source_project()
        store = populate(self.target)
        before = store.snapshot()
        store.close()
        with patch('mkw_tracker.migrate.merge', side_effect=OSError('disk full')), self.assertRaises(OSError):
            migrate(project, self.target, apply=True, theme_conflict='rename')
        self.assertFalse((self.target / 'custom-themes.json').exists())
        self.assertFalse((self.target / 'fonts' / 'RacersDelight.otf').exists())
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(store.snapshot(), before)
        finally:
            store.close()

    def test_overlapping_target_and_source_links_rejected(self):
        project, data = self.source_project()
        with self.assertRaises(ValueError):
            migrate(project, data / 'copy', apply=True, theme_conflict='rename')
        (project / 'mkw_tracker' / 'static' / 'fonts' / 'linked.ttf').symlink_to(data / 'obs-connection.json')
        with self.assertRaises(ValueError):
            migrate(project, self.target, apply=True, theme_conflict='rename')

    def test_session_conflicts_rename_skip_overwrite_and_abort(self):
        project, _ = self.source_project()
        migrate(project, self.target, apply=True, theme_conflict='rename')
        with self.assertRaisesRegex(ValueError, 'Unresolved conflict'):
            migrate(project, self.target, apply=True, theme_conflict='rename')
        preview = migrate(project, self.target)
        self.assertEqual(preview['conflicts'][0]['kind'], 'session')
        migrate(project, self.target, apply=True, theme_conflict='rename', conflict='skip')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        count = len(store._rows('SELECT * FROM sessions'))
        before = store.snapshot()
        store.close()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            migrate(project, self.target, apply=True, theme_conflict='rename', resolver=lambda _: ('abort', None))
        migrate(project, self.target, apply=True, theme_conflict='rename', resolver=lambda _: ('rename', 'Renamed room'))
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(len(store._rows('SELECT * FROM sessions')), count + 1)
            self.assertTrue(store._rows('SELECT * FROM history_folders WHERE name="Renamed room"'))
        finally:
            store.close()
        migrate(project, self.target, apply=True, theme_conflict='rename', conflict='overwrite')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(len(store._rows('SELECT * FROM sessions')), count + 1)
            self.assertEqual(store.active_session()['id'], before['session']['id'])
        finally:
            store.close()

    def test_explicit_overwrite_rollback_and_asset_rename_validation(self):
        project, _ = self.source_project()
        migrate(project, self.target, apply=True, theme_conflict='rename')
        font = self.target / 'fonts' / 'RacersDelight.otf'
        font.write_bytes(b'OTTOtarget contents')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        before = store.snapshot()
        store.close()
        with patch('mkw_tracker.migrate.merge', side_effect=OSError('fail')), self.assertRaises(OSError):
            migrate(project, self.target, apply=True, theme_conflict='rename', conflict='overwrite')
        self.assertEqual(font.read_bytes(), b'OTTOtarget contents')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(before, store.snapshot())
        finally:
            store.close()
        with self.assertRaises(ValueError):
            migrate(project, self.target, apply=True, theme_conflict='rename',
                    resolver=lambda c: ('skip', None) if c['kind'] == 'session' else ('rename', '../bad.otf'))
        migrate(project, self.target, apply=True, theme_conflict='rename', conflict='overwrite')
        self.assertEqual(font.read_bytes(), b'OTTO' + bytes(64))

    def test_session_overwrite_transaction_rolls_back_after_delete(self):
        project, _ = self.source_project()
        migrate(project, self.target, apply=True, theme_conflict='rename')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        before = transfers.collect(store)
        store.close()
        real_insert = transfers._insert
        def fail(db, schema, rows):
            real_insert(db, schema, rows)
            if schema == 'mogi':
                raise OSError('fail after replacement insert')
        with patch.object(transfers, '_insert', side_effect=fail), self.assertRaises(OSError):
            migrate(project, self.target, apply=True, theme_conflict='rename', conflict='overwrite')
        store = TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(before, transfers.collect(store))
        finally:
            store.close()

    def test_full_theme_library_keeps_sessions_and_preserves_extra_pictures(self):
        from mkw_tracker.theme_library import ThemeLibrary
        from mkw_tracker.themes import all_themes
        project, _ = self.source_project()
        self.target.mkdir()
        library = ThemeLibrary(self.target)
        for index in range(50):
            theme = all_themes()[0]
            library.save({k: theme[k] for k in ('tokens', 'team_colors')} | {'name': f'Existing {index}'})
        result = migrate(project, self.target, apply=True, theme_conflict='rename')
        self.assertTrue(result['applied'])
        self.assertTrue(result['warnings'])
        self.assertEqual(len(ThemeLibrary(self.target).records), 50)
        self.assertTrue((self.target / 'imported-assets/character-icons/Character_Mario.png').is_file())

    def test_skipped_font_does_not_become_selected(self):
        project, _ = self.source_project()
        (self.target / 'fonts').mkdir(parents=True)
        (self.target / 'fonts/RacersDelight.otf').write_bytes(b'OTTOexisting')
        with patch.dict('mkw_tracker.migrate.DEFAULT_SETTINGS', {'report_font': ''}):
            migrate(project, self.target, apply=True, theme_conflict='rename', conflict='skip')
        self.assertNotIn('report_font', json.loads((self.target / 'settings.json').read_text()))

    def test_terminal_entrypoint_and_noninteractive_conflict_refusal(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from mkw_tracker.migrate import main, _interactive
        project, _ = self.source_project()
        args = ['mkw_tracker.migrate', str(project), '--data-dir', str(self.target), '--apply', '--theme-conflict', 'rename']
        with patch('sys.argv', args), patch('sys.stdin.isatty', return_value=False), redirect_stdout(StringIO()):
            main()
        with patch('sys.argv', args), patch('sys.stdin.isatty', return_value=False), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
        with patch('sys.argv', args + ['--conflict', 'skip']), patch('sys.stdin.isatty', return_value=False), redirect_stdout(StringIO()):
            main()
        with patch('builtins.input', side_effect=['r', 'Chosen name']), redirect_stdout(StringIO()):
            self.assertEqual(_interactive({'kind': 'session', 'name': 'Original', 'can_overwrite': True}),
                             ('rename', 'Chosen name'))
