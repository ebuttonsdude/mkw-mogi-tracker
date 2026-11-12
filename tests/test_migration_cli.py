"""Interactive and scripted migration use synthetic, disposable project data only."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from mkw_tracker import migrate as migration
from tests import test_transfers as fixtures


class MigrationCliTests(unittest.TestCase):
    setUp = fixtures.MigrationTests.setUp
    source_project = fixtures.MigrationTests.source_project

    def run_cli(self, project, *flags, answers=(), tty=True):
        output = StringIO()
        replies = iter(answers)

        def answer(prompt=''):
            output.write(prompt)
            try:
                reply = next(replies)
            except StopIteration:
                raise EOFError from None
            # Every menu, review and confirmation is read-only until explicit yes.
            self.assertFalse(self.target.exists(), 'Target written before explicit confirmation')
            if isinstance(reply, BaseException):
                raise reply
            return reply

        argv = ['mkw_tracker.migrate', str(project), '--data-dir', str(self.target), '--theme-conflict', 'rename', *flags]
        with patch('sys.argv', argv), patch('sys.stdin.isatty', return_value=tty), \
                patch('builtins.input', side_effect=answer) as prompts, \
                redirect_stdout(output), redirect_stderr(output):
            migration.main()
        return output.getvalue(), prompts.call_count

    def assert_no_sessions(self):
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])
        self.assertFalse((self.target / 'ww').exists())
        self.assertFalse((self.target / 'ffa').exists())

    def test_terminal_defaults_show_inventory_and_cancel_without_writes(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, answers=['q'])
        self.assertGreater(prompts, 0)
        for name in ('sessions', 'evidence', 'pictures', 'fonts', 'videos', 'models'):
            self.assertIn(name, output.lower())
        self.assertRegex(output.lower(), r'\b(?:bytes|kib|mib|gib)\b')
        self.assertFalse(self.target.exists())

    def test_terminal_defaults_apply_sessions_evidence_pictures_fonts_only(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['m', 'yes'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue(list(self.target.rglob('evidence/**/*.png')))
        self.assertTrue((self.target / 'fonts/RacersDelight.otf').exists())
        self.assertTrue((self.target / 'imported-assets/track-icons/Course.png').exists())
        self.assertFalse((self.target / 'imported-videos').exists())
        self.assertFalse((self.root / 'models').exists())

    def test_everything_menu_copies_optional_data_after_confirmation(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['a', 'm', 'yes'])
        self.assertTrue((self.target / 'imported-videos/mogi/recording.MP4').exists())
        self.assertTrue((self.root / 'models/qwen/model.safetensors').exists())
        self.assertFalse((self.root / 'models/qwen/code.py').exists())

    def test_select_none_then_fonts_does_not_initialize_databases(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['n', '4', 'm', 'yes'])
        self.assertTrue((self.target / 'fonts/RacersDelight.otf').exists())
        self.assert_no_sessions()
        self.assertFalse((self.target / 'imported-assets').exists())

    def test_deselect_sessions_also_deselects_evidence(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['1', 'm', 'yes'])
        self.assert_no_sessions()
        self.assertEqual(list(self.target.rglob('evidence')), [])
        self.assertTrue((self.target / 'fonts/RacersDelight.otf').exists())

    def test_select_evidence_automatically_selects_sessions(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['n', '2', 'm', 'yes'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue(list(self.target.rglob('evidence/**/*.png')))
        self.assertFalse((self.target / 'fonts').exists())
        self.assertFalse((self.target / 'imported-assets').exists())

    def test_invalid_menu_command_does_not_apply_or_exit(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, answers=['invalid', '7', 'q'])
        self.assertGreaterEqual(prompts, 3)
        self.assertRegex(output.lower(), r'invalid|unknown|choose|command')
        self.assertFalse(self.target.exists())

    def test_preview_selected_returns_to_menu_without_writing(self):
        project, _ = self.source_project()
        _, prompts = self.run_cli(project, answers=['p', 'q'])
        self.assertGreaterEqual(prompts, 2)
        self.assertFalse(self.target.exists())

    def test_menu_eof_cancels_without_writes(self):
        project, _ = self.source_project()
        self.run_cli(project)
        self.assertFalse(self.target.exists())

    def test_confirmation_eof_cancels_without_writes(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['m'])
        self.assertFalse(self.target.exists())

    def test_keyboard_interrupt_at_menu_and_confirmation_cancels(self):
        project, _ = self.source_project()
        for answers in ([KeyboardInterrupt()], ['m', KeyboardInterrupt()]):
            with self.subTest(stage='confirmation' if len(answers) == 2 else 'menu'):
                self.run_cli(project, answers=answers)
                self.assertFalse(self.target.exists())

    def test_confirmation_rejects_ambiguous_y(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['m', 'y', 'q'])
        self.assertFalse(self.target.exists())

    def test_redirected_stdin_defaults_to_json_preview_without_prompt(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, tty=False)
        self.assertEqual(prompts, 0)
        self.assertFalse(json.loads(output)['applied'])
        self.assertFalse(self.target.exists())

    def test_explicit_preview_never_prompts_even_in_terminal(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, '--preview', '--everything')
        self.assertEqual(prompts, 0)
        self.assertFalse(json.loads(output)['applied'])
        self.assertFalse(self.target.exists())

    def test_non_interactive_disables_menu_and_implicit_writes(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, '--non-interactive')
        self.assertEqual(prompts, 0)
        self.assertFalse(json.loads(output)['applied'])
        self.assertFalse(self.target.exists())

    def test_explicit_apply_keeps_script_mode_without_confirmation(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, '--apply', tty=False)
        self.assertEqual(prompts, 0)
        self.assertTrue(json.loads(output)['applied'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())

    def test_explicit_apply_in_terminal_preserves_conflict_prompt(self):
        project, _ = self.source_project()
        migration.migrate(project, self.target, apply=True, theme_conflict='rename')
        store = fixtures.TrackerStore(self.target / 'tracker.sqlite3')
        try:
            before = store.snapshot()
        finally:
            store.close()
        output = StringIO()
        argv = ['mkw_tracker.migrate', str(project), '--data-dir', str(self.target), '--apply']
        with patch('sys.argv', argv), patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value='s') as prompts, redirect_stdout(output):
            migration.main()
        self.assertEqual(prompts.call_count, 2)
        self.assertTrue(json.loads(output.getvalue()[output.getvalue().index('{'):])['applied'])
        store = fixtures.TrackerStore(self.target / 'tracker.sqlite3')
        try:
            self.assertEqual(store.snapshot(), before)
        finally:
            store.close()

    def test_only_fonts_script_excludes_sessions_and_other_assets(self):
        project, _ = self.source_project()
        output, prompts = self.run_cli(project, '--only', 'fonts', '--apply', tty=False)
        self.assertEqual(prompts, 0)
        self.assertTrue(json.loads(output)['applied'])
        self.assertTrue((self.target / 'fonts/RacersDelight.otf').exists())
        self.assert_no_sessions()
        self.assertFalse((self.target / 'imported-assets').exists())

    def test_everything_script_includes_optional_files(self):
        project, _ = self.source_project()
        self.run_cli(project, '--everything', '--apply', tty=False)
        self.assertTrue((self.target / 'imported-videos/mogi/recording.MP4').exists())
        self.assertTrue((self.root / 'models/qwen/model.safetensors').exists())

    def test_select_none_cannot_migrate_without_selection(self):
        project, _ = self.source_project()
        self.run_cli(project, answers=['n', 'm', 'q'])
        self.assertFalse(self.target.exists())

    def test_only_evidence_script_includes_required_sessions(self):
        project, _ = self.source_project()
        self.run_cli(project, '--only', 'evidence', '--apply', tty=False)
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue(list(self.target.rglob('evidence/**/*.png')))
        self.assertFalse((self.target / 'fonts').exists())
        self.assertFalse((self.target / 'imported-assets').exists())

    def test_legacy_optional_flags_remain_supported(self):
        project, _ = self.source_project()
        self.run_cli(project, '--include-videos', '--include-models', '--apply', tty=False)
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue((self.target / 'imported-videos/mogi/recording.MP4').exists())
        self.assertTrue((self.root / 'models/qwen/model.safetensors').exists())

    def test_missing_evidence_does_not_block_menu_font_selection(self):
        project, data = self.source_project()
        for image in data.rglob('evidence/**/*.png'):
            image.unlink()
        output, _ = self.run_cli(project, answers=['n', '4', 'm', 'yes'])
        self.assertIn('unavailable', output.lower())
        self.assertTrue((self.target / 'fonts/RacersDelight.otf').exists())
        self.assert_no_sessions()

    def test_missing_evidence_does_not_block_menu_sessions_without_evidence(self):
        project, data = self.source_project()
        for image in data.rglob('evidence/**/*.png'):
            image.unlink()
        self.run_cli(project, answers=['n', '1', 'm', 'yes'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertEqual(list(self.target.rglob('evidence')), [])
        self.assertFalse((self.target / 'fonts').exists())

    def test_conflicting_script_modes_and_selections_are_rejected(self):
        project, _ = self.source_project()
        for flags in (['--apply', '--preview'], ['--everything', '--only', 'fonts']):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                self.run_cli(project, *flags, tty=False)
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(self.target.exists())

    def test_unknown_only_category_fails_without_writes(self):
        project, _ = self.source_project()
        with self.assertRaises(SystemExit) as error:
            self.run_cli(project, '--only', 'passwords', '--apply', tty=False)
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.target.exists())


class MigrationCategoryTests(unittest.TestCase):
    setUp = fixtures.MigrationTests.setUp
    source_project = fixtures.MigrationTests.source_project

    def test_sessions_only_excludes_evidence_and_assets(self):
        project, _ = self.source_project()
        result = migration.migrate(project, self.target, categories={'sessions'}, apply=True, theme_conflict='rename')
        self.assertTrue(result['applied'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertEqual(list(self.target.rglob('evidence')), [])
        self.assertFalse((self.target / 'fonts').exists())
        self.assertFalse((self.target / 'imported-assets').exists())

    def test_pictures_only_imports_images_without_database_or_fonts(self):
        project, _ = self.source_project()
        migration.migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='rename')
        self.assertTrue((self.target / 'imported-assets/track-icons/Course.png').exists())
        self.assertTrue((self.target / 'custom-themes.json').exists())
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])
        self.assertFalse((self.target / 'fonts').exists())

    def test_empty_category_selection_is_read_only(self):
        project, _ = self.source_project()
        with self.assertRaises(ValueError):
            migration.migrate(project, self.target, categories=set(), apply=True, theme_conflict='rename')
        self.assertFalse(self.target.exists())

    def test_video_only_does_not_read_unselected_invalid_fonts(self):
        project, _ = self.source_project()
        (project / 'mkw_tracker/static/fonts/RacersDelight.otf').write_bytes(b'invalid')
        migration.migrate(project, self.target, categories={'videos'}, apply=True, theme_conflict='rename')
        self.assertTrue((self.target / 'imported-videos/mogi/recording.MP4').exists())
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])

    def test_picture_only_source_does_not_require_database(self):
        project, data = self.source_project()
        (data / 'tracker.sqlite3').unlink()
        migration.migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='rename')
        self.assertTrue((self.target / 'imported-assets/track-icons/Course.png').exists())
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])

    def test_inventory_counts_include_evidence_bytes_without_target_writes(self):
        project, data = self.source_project()
        before = {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()}
        inventory = migration.inspect_source(project)
        self.assertEqual(before, {p.relative_to(project): p.read_bytes()
                                  for p in project.rglob('*') if p.is_file()})
        categories = inventory['categories']
        self.assertEqual(set(categories), {'sessions', 'evidence', 'pictures', 'fonts', 'videos', 'models'})
        self.assertEqual(categories['sessions']['count'], 1)
        self.assertEqual(categories['pictures']['count'], 2)
        self.assertEqual(categories['fonts']['count'], 1)
        self.assertEqual(categories['videos']['count'], 1)
        self.assertEqual(categories['models']['count'], 1)
        evidence = list(data.rglob('evidence/**/*.png'))
        self.assertEqual(categories['evidence']['count'], len(evidence))
        self.assertGreater(categories['evidence']['count'], 0)
        self.assertEqual(categories['evidence']['bytes'], sum(path.stat().st_size for path in evidence))
        self.assertGreater(inventory['database_bytes'], 0)
        self.assertGreater(inventory['races'], 0)
        self.assertFalse(self.target.exists())

    def test_missing_evidence_is_reported_and_selected_import_rejected_without_writes(self):
        project, data = self.source_project()
        for image in data.rglob('evidence/**/*.png'):
            image.unlink()
        inventory = migration.inspect_source(project)
        self.assertTrue(inventory['unavailable']['evidence'])
        self.assertEqual(inventory['categories']['evidence']['count'], 1)
        self.assertEqual(inventory['categories']['evidence']['bytes'], 0)
        with self.assertRaises((OSError, ValueError)):
            migration.migrate(project, self.target, categories={'sessions', 'evidence'}, apply=True, theme_conflict='rename')
        self.assertFalse(self.target.exists())

    def test_malformed_source_preferences_and_themes_do_not_block_sessions_or_videos(self):
        project, data = self.source_project()
        (data / 'settings.json').write_text('not valid json')
        (data / 'custom-themes.json').write_text('not valid json either')
        result = migration.migrate(project, self.target, categories={'sessions', 'videos'}, apply=True, theme_conflict='rename')
        self.assertTrue(result['applied'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue((self.target / 'imported-videos/mogi/recording.MP4').exists())
        self.assertFalse((self.target / 'custom-themes.json').exists())
        self.assertFalse((self.target / 'settings.json').exists())

    def test_selected_malformed_source_theme_library_is_retained_with_warning(self):
        project, data = self.source_project()
        original = b'{invalid theme data retained exactly'
        (data / 'custom-themes.json').write_bytes(original)
        result = migration.migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='rename')
        self.assertEqual((self.target / 'imported-assets/custom-themes.json').read_bytes(), original)
        self.assertTrue(any('custom-theme' in warning and 'retained' in warning
                            for warning in result['warnings']))
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])

    def test_sessions_only_preserves_unselected_malformed_target_theme_library(self):
        project, _ = self.source_project()
        self.target.mkdir()
        original = b'malformed user library must remain untouched'
        (self.target / 'custom-themes.json').write_bytes(original)
        result = migration.migrate(project, self.target, categories={'sessions'}, apply=True, theme_conflict='rename')
        self.assertTrue(result['applied'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertEqual((self.target / 'custom-themes.json').read_bytes(), original)

    def test_preview_skip_and_rename_do_not_fingerprint_existing_session_contents(self):
        project, _ = self.source_project()
        migration.migrate(project, self.target, categories={'sessions'}, apply=True, theme_conflict='rename')
        for policy in (None, 'skip', 'rename'):
            with self.subTest(policy=policy), patch(
                    'mkw_tracker.migrate.session_fingerprint',
                    side_effect=AssertionError('Only overwrite needs a session fingerprint')) as fingerprint:
                result = migration.migrate(project, self.target, categories={'sessions'},
                                           apply=policy is not None, conflict=policy)
                self.assertTrue(any(item['kind'] == 'session' for item in result['conflicts']))
                fingerprint.assert_not_called()

    def test_theme_picker_byte_budget_retains_original_picture_and_existing_library(self):
        project, _ = self.source_project()
        library = migration.ThemeLibrary(self.target)
        theme = migration.all_themes()[0]
        library.save({key: theme[key] for key in ('name', 'tokens', 'team_colors')} |
                     {'name': 'Existing user theme'})
        original_library = library.path.read_bytes()
        with patch('mkw_tracker.theme_library.MAX_LIBRARY_BYTES', len(original_library) + 1):
            result = migration.migrate(project, self.target, categories={'pictures'}, apply=True, theme_conflict='rename')
        self.assertEqual(library.path.read_bytes(), original_library)
        original_picture = project / 'mkw_tracker/static/character-icons/Character_Mario.png'
        self.assertEqual((self.target / 'imported-assets/character-icons/Character_Mario.png').read_bytes(),
                         original_picture.read_bytes())
        self.assertTrue(any('size limit' in warning.lower() for warning in result['warnings']))
        self.assertEqual(list(self.target.rglob('tracker.sqlite3')), [])
