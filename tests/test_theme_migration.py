"""Explicit theme replacement uses only disposable libraries and synthetic icons."""
import hashlib
import json
import unittest
from unittest.mock import patch

from mkw_tracker.migrate import migrate, _interactive_resolver
from mkw_tracker.theme_library import ThemeLibrary
from mkw_tracker.themes import all_themes
from tests.test_custom_themes import draft, icon
from tests import test_transfers as fixtures


class ThemeMigrationTests(unittest.TestCase):
    setUp = fixtures.MigrationTests.setUp
    source_project = fixtures.MigrationTests.source_project
    def test_fresh_same_name_requires_choice_and_overwrite_keeps_builtin_id(self):
        project, _ = self.source_project()
        preview = migrate(project, self.target, categories={'pictures'})
        self.assertEqual(preview['conflicts'][0]['kind'], 'theme')
        with self.assertRaisesRegex(ValueError, 'Unresolved conflict'):
            migrate(project, self.target, categories={'pictures'}, apply=True)
        self.assertFalse(self.target.exists())
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        library = ThemeLibrary(self.target)
        self.assertEqual(len(library.themes()), 50)
        self.assertTrue(library.resolve('mario')['overridden'])
        self.assertTrue(library.icon('mario').startswith(b'\x89PNG'))
        library.save(draft(id='mario', name='Mario', icon_base64=None))
        self.assertIsNone(library.resolve('mario')['icon_url'])
        library.delete('mario')
        self.assertFalse(library.resolve('mario')['custom'])

    def test_all_builtin_duplicates_repaired_selection_remapped_idempotent(self):
        project, _ = self.source_project()
        folder = project / 'mkw_tracker/static/character-icons'
        self.target.mkdir()
        library = ThemeLibrary(self.target)
        records = {}
        for theme in all_themes():
            (folder / ('Character_' + theme['name'] + '.png')).write_bytes(fixtures.png())
            record = library.prepare({k: theme[k] for k in ('name', 'tokens', 'team_colors')} |
                                     {'icon_base64': icon()})
            record['id'] = 'custom-' + hashlib.sha256(json.dumps(
                {k: v for k, v in record.items() if k != 'id'}, sort_keys=True).encode()).hexdigest()[:32]
            records[record['id']] = record
        selected = next(iter(records))
        library._write(records)
        (self.target / 'settings.json').write_text(json.dumps({'theme': selected, 'other': 'keep'}))
        before = {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()}
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        library = ThemeLibrary(self.target)
        self.assertEqual(set(library.records), {t['id'] for t in all_themes()})
        self.assertEqual(len(library.themes()), 50)
        settings = json.loads((self.target / 'settings.json').read_text())
        self.assertEqual(settings['theme'], all_themes()[0]['id'])
        self.assertEqual(settings['other'], 'keep')
        library.save(draft(name='Unrelated'))  # Overrides do not consume custom slots.
        result = migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        self.assertEqual(result['themes'], 0)
        self.assertEqual(len(ThemeLibrary(self.target).themes()), 51)
        self.assertEqual(before, {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()})
        self.assertFalse(list(self.target.rglob('tracker.sqlite3')))

    def test_skip_and_other_names_preserved(self):
        project, _ = self.source_project()
        library = ThemeLibrary(self.target)
        custom = library.save(draft(name='Mario', icon_base64=icon()))
        unrelated = library.save(draft(name='My Mario'))
        before = library.path.read_bytes()
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='skip')
        self.assertEqual(library.path.read_bytes(), before)
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        library = ThemeLibrary(self.target)
        self.assertNotIn(custom['id'], library.records)
        self.assertIn(unrelated['id'], library.records)

    def test_failed_settings_write_restores_library_and_selected_alias(self):
        project, _ = self.source_project()
        library = ThemeLibrary(self.target)
        saved = library.save(draft(name='Mario', icon_base64=icon()))
        settings = self.target / 'settings.json'
        settings.write_text(json.dumps({'theme': saved['id']}))
        before = library.path.read_bytes(), settings.read_bytes()
        from pathlib import Path
        original = Path.write_text

        def fail(path, *args, **kwargs):
            if path.name.startswith('.migration-'):
                raise OSError('synthetic disk failure')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'write_text', fail), self.assertRaises(OSError):
            migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        self.assertEqual((library.path.read_bytes(), settings.read_bytes()), before)

    def test_overwrite_all_is_scoped_to_themes(self):
        resolver = _interactive_resolver()
        item = {'kind': 'theme', 'name': 'Mario', 'can_overwrite': True, 'matching_ids': ['mario']}
        with patch('builtins.input', side_effect=['w', 's']) as prompt:
            self.assertEqual(resolver(item), ('overwrite', None))
            self.assertEqual(resolver({**item, 'name': 'Luigi'}), ('overwrite', None))
            self.assertEqual(resolver({**item, 'kind': 'asset'}), ('skip', None))
            self.assertEqual(prompt.call_count, 2)

    def test_full_custom_budget_allows_fifty_overrides_and_retains_byte_limit(self):
        project, _ = self.source_project()
        library = ThemeLibrary(self.target)
        for index in range(50):
            library.save(draft(name=f'Custom {index}'))
        folder = project / 'mkw_tracker/static/character-icons'
        for theme in all_themes():
            (folder / ('Character_' + theme['name'] + '.png')).write_bytes(fixtures.png())
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        library = ThemeLibrary(self.target)
        self.assertEqual(len(library.records), 100)
        self.assertEqual(len(library.themes()), 100)
        with self.assertRaisesRegex(ValueError, 'fifty'):
            library.save(draft(name='Too many'))
        before = library.path.read_bytes()
        with patch('mkw_tracker.theme_library.MAX_LIBRARY_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'full'):
                library.save(draft(id='mario', name='Mario'))
        self.assertEqual(library.path.read_bytes(), before)

    def test_cli_accept_all_and_theme_policy_do_not_overwrite_assets(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from mkw_tracker.migrate import main
        project, _ = self.source_project()
        folder = project / 'mkw_tracker/static/character-icons'
        (folder / 'Character_Luigi.png').write_bytes(fixtures.png())
        argv = ['migrate', str(project), '--data-dir', str(self.target), '--only', 'pictures', '--apply']
        with patch('sys.argv', argv), patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value='w') as prompt, redirect_stdout(StringIO()):
            main()
        self.assertEqual(prompt.call_count, 1)
        library = ThemeLibrary(self.target)
        self.assertEqual(set(library.records), {'mario', 'luigi'})
        picture = self.target / 'imported-assets/character-icons/Character_Mario.png'
        picture.write_bytes(b'keep different retained asset')
        with patch('sys.argv', argv + ['--theme-conflict', 'overwrite', '--conflict', 'skip']), \
                patch('sys.stdin.isatty', return_value=False), redirect_stdout(StringIO()):
            main()
        self.assertEqual(picture.read_bytes(), b'keep different retained asset')
        self.assertTrue(ThemeLibrary(self.target).icon('mario').startswith(b'\x89PNG'))

    def test_source_saved_override_palette_and_icon_take_priority(self):
        project, source_data = self.source_project()
        source = ThemeLibrary(source_data)
        record = source.prepare(draft(name='Mario', icon_base64=icon((1, 2, 3, 255))))
        record['id'] = 'mario'
        record['tokens']['accent'] = '#010203'
        source._write({'mario': record})
        migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='overwrite')
        self.assertEqual(ThemeLibrary(self.target).records['mario'], source.records['mario'])
