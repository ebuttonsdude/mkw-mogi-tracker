"""Guided setup is verified without launching processes or downloading anything."""

from contextlib import ExitStack
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('guided_setup', Path(__file__).parents[1] / 'tools/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / 'Tracker 日本語'
        self.root.mkdir()
        self.stack.enter_context(patch.object(setup, 'ROOT', self.root))
        self.stack.enter_context(patch.object(setup.sys, 'platform', 'linux'))
        self.stack.enter_context(patch.object(setup.sys, 'version_info', (3, 12, 10)))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')))
        self.stack.enter_context(patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected download')))
        self.run = self.stack.enter_context(patch.object(setup.subprocess, 'run'))
        self.tesseract = self.stack.enter_context(patch.object(setup, 'tesseract_executable', return_value='/mock/tesseract'))
        self.which = self.stack.enter_context(patch.object(setup.shutil, 'which', side_effect=lambda name: '/mock/' + name))
        self.output = self.stack.enter_context(patch.object(setup.sys, 'stdout', new_callable=io.StringIO))
        self.errors = self.stack.enter_context(patch.object(setup.sys, 'stderr', new_callable=io.StringIO))
        self.stack.enter_context(patch.object(setup.sys.stdin, 'isatty', return_value=False))

    def commands(self):
        return [call.args[0] for call in self.run.call_args_list]

    def test_each_reader_uses_explicit_existing_engine(self):
        for choice in [*setup.READERS, *setup.READER_ALIASES]:
            engine = setup.READERS[setup.READER_ALIASES.get(choice, choice)]
            if engine is None:
                continue
            with self.subTest(choice=choice):
                self.run.reset_mock()
                self.assertEqual(setup.main(['--reader', choice]), 0)
                expected_neural = [str(self.root / '.venv/bin/python'), str(self.root / 'tools/setup_neural.py'), '--engine', engine]
                if engine == setup.GGUF_PROFILE['engine']:
                    expected_neural += ['--gguf-backend', 'cpu']
                self.assertEqual(self.commands(), [
                    [setup.sys.executable, str(self.root / 'tools/setup_local.py')],
                    expected_neural,
                ])
                for call in self.run.call_args_list:
                    self.assertEqual(call.kwargs, {'cwd': self.root, 'check': True})
        self.assertIn('Automatic does not select', self.output.getvalue())
        self.assertFalse((self.root / '.local').exists())

    def test_skip_still_installs_capture_and_reports(self):
        self.assertEqual(setup.main(['--reader', 'skip']), 0)
        self.assertEqual(self.commands(), [[setup.sys.executable, str(self.root / 'tools/setup_local.py')]])
        self.assertIn('Tesseract', self.output.getvalue())

    def test_missing_tesseract_stops_before_any_install(self):
        self.tesseract.return_value = None
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.run.assert_not_called()
        self.assertIn('Tesseract', self.errors.getvalue())
        self.assertIn('PATH', self.errors.getvalue())

    def test_missing_uv_stops_neural_setup_before_capture_install(self):
        self.which.side_effect = lambda name: None if name == 'uv' else '/mock/' + name
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.run.assert_not_called()
        self.assertIn('uv', self.errors.getvalue())

    def test_skip_works_without_uv(self):
        self.which.side_effect = lambda name: None if name == 'uv' else '/mock/' + name
        self.assertEqual(setup.main(['--reader', 'skip']), 0)
        self.assertEqual(len(self.commands()), 1)

    def test_nvfp4_rejects_non_linux_before_any_install(self):
        for platform in ('win32', 'darwin'):
            for choice in ('nvfp4', 'qwen-nvfp4', 'qwen36-nvfp4'):
                with self.subTest(platform=platform, choice=choice), patch.object(setup.sys, 'platform', platform):
                    self.assertEqual(setup.main(['--reader', choice]), 1)
                    self.run.assert_not_called()
        self.assertIn('Linux', self.errors.getvalue())

    def test_windows_commands_use_scripts_python(self):
        with patch.object(setup.sys, 'platform', 'win32'):
            self.assertEqual(setup.main(['--reader', 'cpu']), 0)
        self.assertEqual(self.commands()[1][0], str(self.root / '.venv/Scripts/python.exe'))
        self.assertIn(r'.\.venv\Scripts\python.exe -m mkw_tracker', self.output.getvalue())

    def test_dry_run_never_runs_commands_or_requires_installed_prerequisites(self):
        self.which.side_effect = None
        self.which.return_value = None
        self.assertEqual(setup.main(['--reader', '8gb', '--dry-run']), 0)
        self.run.assert_not_called()
        self.assertIn('6.5 GiB', self.output.getvalue())
        self.assertIn('8.88 GB', self.output.getvalue())
        self.assertIn('Needed before installation', self.output.getvalue())
        self.assertFalse((self.root / '.venv').exists())

    def test_noninteractive_requires_explicit_reader(self):
        with self.assertRaises(SystemExit) as error:
            setup.main([])
        self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_help_is_available_without_prerequisites_or_processes(self):
        self.which.side_effect = AssertionError('Help should not inspect prerequisites')
        with self.assertRaises(SystemExit) as error:
            setup.main(['--help'])
        self.assertEqual(error.exception.code, 0)
        self.run.assert_not_called()
        self.assertIn('--dry-run', self.output.getvalue())

    def test_interactive_invalid_and_blank_choices_do_not_start_downloads(self):
        def choose(_prompt):
            return next(answers)
        answers = iter(['1', '', 'bad', '2', 'n'])
        with patch.object(setup.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=choose):
            self.assertEqual(setup.main([]), 0)
        self.assertEqual(self.commands()[-1][-2:], ['--engine', 'qwen-4b-nf4'])
        output = self.output.getvalue()
        for text in ('optional', 'highly recommended', '81 MB', '8.88 GB', '17.5 GB', '11 GiB', 'Skip'):
            self.assertIn(text, output)

    def test_menu_includes_every_installable_reader_once_and_preserves_numbers(self):
        self.assertEqual(list(setup.READERS)[:4], ['cpu', '8gb', 'nvfp4', 'skip'])
        self.assertEqual(set(setup.READERS.values()), {None, 'paddle', *setup.QWEN_PROFILES, setup.GGUF_PROFILE['engine']})
        self.assertEqual(len(setup.READERS), len(set(setup.READERS.values())))
        for number, choice in enumerate(setup.READERS, 1):
            with self.subTest(number=number), patch('builtins.input', return_value=str(number)):
                self.assertEqual(setup.choose_reader(), choice)
        self.run.assert_not_called()

    def test_menu_accepts_canonical_reader_ids(self):
        for alias, choice in setup.READER_ALIASES.items():
            with self.subTest(alias=alias), patch('builtins.input', return_value=alias):
                self.assertEqual(setup.choose_reader(), choice)
        self.run.assert_not_called()

    def test_gguf_is_recommended_without_changing_numbers_or_auto_selecting(self):
        with patch('builtins.input', side_effect=['', '8']):
            self.assertEqual(setup.choose_reader(), 'qwen-4b-gguf')
        output = self.output.getvalue()
        self.assertIn('Recommended Qwen starting point: 8. Qwen3-VL-4B GGUF Q4_K_M', output)
        self.assertIn('No choice is selected automatically', output)
        self.assertIn('Enter a number', output)
        self.assertIn('3.33 GB', setup.reader_description('qwen-4b-gguf'))
        self.assertIn('BF16 source weights', setup.reader_description('8gb'))
        self.run.assert_not_called()

    def test_descriptions_use_profile_labels_and_memory_limits(self):
        for choice, engine in setup.READERS.items():
            if engine not in setup.QWEN_PROFILES:
                continue
            profile = setup.QWEN_PROFILES[engine]
            description = setup.reader_description(choice)
            self.assertIn(profile['label'], description)
            self.assertIn(f"{profile['min_free_gib']} GiB FREE VRAM", description)
            if profile['requires_nvfp4']:
                with patch.object(setup.sys, 'platform', 'win32'):
                    self.assertIn('Unavailable on this platform', setup.reader_description(choice))
                self.assertIn('Linux only', description)

    def test_tesseract_alias_installs_only_capture_and_reports(self):
        self.assertEqual(setup.main(['--reader', 'tesseract']), 0)
        self.assertEqual(self.commands(), [[setup.sys.executable, str(self.root / 'tools/setup_local.py')]])

    def test_gguf_interactive_backend_and_explicit_default(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['1', '8', '', 'bad', '1', 'n']):
            self.assertEqual(setup.main([]), 0)
        self.assertEqual(self.commands()[-1][-4:], ['--engine', 'qwen-4b-gguf', '--gguf-backend', 'vulkan'])
        self.run.reset_mock()
        self.which.side_effect = None
        self.which.return_value = None
        self.assertEqual(setup.main(['--reader', 'qwen-4b-gguf']), 0)
        self.assertEqual(self.commands()[-1][-2:], ['--gguf-backend', 'cpu'])

    def test_gguf_backend_cancelled_before_install(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['1', '8', KeyboardInterrupt]):
            self.assertEqual(setup.main([]), 130)
        self.run.assert_not_called()

    def test_gguf_backend_flag_rejects_other_readers(self):
        for args in (['--reader', 'cpu'], ['--manual-only']):
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                setup.main([*args, '--gguf-backend', 'vulkan'])
            self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_cancelled_menu_makes_no_changes(self):
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error), patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', side_effect=error):
                self.assertEqual(setup.main([]), 130)
                self.run.assert_not_called()

    def test_failed_capture_install_does_not_start_neural_install(self):
        self.run.side_effect = subprocess.CalledProcessError(1, ['mock-setup'])
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.assertEqual(len(self.commands()), 1)
        self.assertNotIn('Setup complete', self.output.getvalue())
        self.assertIn('rerun', self.errors.getvalue())

    def test_missing_executable_reports_the_actual_failure(self):
        self.run.side_effect = FileNotFoundError('missing environment Python')
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.assertEqual(len(self.commands()), 1)
        self.assertIn('missing environment Python', self.errors.getvalue())

    def test_manual_only_does_not_require_system_dependencies(self):
        self.which.side_effect = None
        self.which.return_value = None
        self.assertEqual(setup.main(['--manual-only']), 0)
        self.assertEqual(self.commands(), [[setup.sys.executable, '-m', 'venv', str(self.root / '.venv')]])

    def test_manual_only_reuses_existing_environment(self):
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').touch()
        self.assertEqual(setup.main(['--manual-only']), 0)
        self.run.assert_not_called()

    def test_manual_only_rejects_reader_flag(self):
        with self.assertRaises(SystemExit) as error:
            setup.main(['--manual-only', '--reader', 'cpu'])
        self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_unsupported_python_stops_before_install(self):
        with patch.object(setup.sys, 'version_info', (3, 10, 9)):
            self.assertEqual(setup.main(['--reader', 'skip']), 1)
        self.run.assert_not_called()
        self.assertIn('Python 3.11', self.errors.getvalue())

    def test_system_prerequisites_require_explicit_opt_in(self):
        self.tesseract.return_value = None
        with patch.object(setup.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu'}):
            self.assertEqual(setup.main(['--reader', 'skip', '--dry-run', '--install-prerequisites']), 0)
            self.run.assert_not_called()
            self.assertIn('sudo apt install', self.output.getvalue())
            self.assertEqual(setup.main(['--reader', 'skip']), 1)
            self.run.assert_not_called()

    def test_default_action_accepts_tesseract_and_auto(self):
        for engine in ('tesseract', 'auto'):
            self.assertEqual(setup.main(['--action', 'default', '--reader', engine]), 0)
            self.assertEqual(setup.reader_setup.read(self.root)['default'], engine)
        self.run.assert_not_called()

    def test_corrupt_preferences_stop_install_before_commands(self):
        (self.root / '.local').mkdir()
        (self.root / '.local/reader-setup.json').write_text('broken')
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.run.assert_not_called()


    def test_failed_reinstall_keeps_removed_marker(self):
        setup.reader_setup.write(self.root, {'default': 'auto', 'removed': ['paddle']})
        self.run.side_effect = subprocess.CalledProcessError(1, 'mock')
        self.assertEqual(setup.main(['--reader', 'cpu']), 1)
        self.assertIn('paddle', setup.reader_setup.read(self.root)['removed'])


    def test_tesseract_install_can_set_default(self):
        self.assertEqual(setup.main(['--reader', 'tesseract', '--set-default']), 0)
        self.assertEqual(setup.reader_setup.read(self.root)['default'], 'tesseract')

    def test_recommended_bundle_dry_run_is_nonmutating(self):
        self.assertEqual(setup.main(['--reader', 'recommended', '--gguf-backend', 'vulkan',
                                     '--set-default', '--dry-run']), 0)
        self.run.assert_not_called()
        self.assertFalse((self.root / '.local').exists())
        self.assertIn('--engine paddle', self.output.getvalue())
        self.assertIn('--engine qwen-4b-gguf --gguf-backend vulkan', self.output.getvalue())

    def test_recommended_bundle_sets_only_gguf_default_after_both_successes(self):
        def installed(root, engine):
            self.assertEqual(len(self.commands()), 3)
            return True
        with patch.object(setup.reader_setup, 'installed', side_effect=installed):
            self.assertEqual(setup.main(['--reader', 'recommended', '--set-default']), 0)
        self.assertEqual(self.commands()[1][-2:], ['--engine', 'paddle'])
        self.assertEqual(self.commands()[2][-4:], ['--engine', 'qwen-4b-gguf', '--gguf-backend', 'cpu'])
        self.assertEqual(setup.reader_setup.read(self.root)['default'], 'qwen-4b-gguf')

    def test_recommended_partial_failure_preserves_default(self):
        setup.reader_setup.set_default(self.root, 'tesseract')
        self.run.side_effect = [None, None, subprocess.CalledProcessError(1, 'mock')]
        self.assertEqual(setup.main(['--reader', 'recommended', '--set-default']), 1)
        self.assertEqual(setup.reader_setup.read(self.root)['default'], 'tesseract')

    def test_recommended_interactive_offers_backend_and_default_without_changing_it(self):
        setup.reader_setup.set_default(self.root, 'tesseract')
        with patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['1', '9', '1', 'n']):
            self.assertEqual(setup.main([]), 0)
        self.assertEqual(self.commands()[-1][-2:], ['--gguf-backend', 'vulkan'])
        self.assertEqual(setup.reader_setup.read(self.root)['default'], 'tesseract')

    def test_recommended_is_not_a_single_management_engine(self):
        for action in ('default', 'remove'):
            with self.subTest(action=action), self.assertRaises(SystemExit):
                setup.main(['--reader', 'recommended', '--action', action])
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
