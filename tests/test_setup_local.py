"""Executable discovery for tools/setup_local.py and the neural reader follows platform layouts."""
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mkw_tracker import neural_ocr
from mkw_tracker.name_engines import QWEN_PROFILES

spec = importlib.util.spec_from_file_location('setup_local', Path(__file__).parents[1] / 'tools/setup_local.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupLocalTests(unittest.TestCase):
    def setUp(self):
        guard = patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch'))
        guard.start()
        self.addCleanup(guard.stop)

    def _run_main(self, root, platform='linux', uv='/usr/bin/uv', tesseract='/usr/bin/tesseract'):
        output = io.StringIO()
        with patch.object(setup.sys, 'platform', platform), \
                patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                patch.object(setup, 'tesseract_executable', return_value=tesseract), \
                patch.object(setup.shutil, 'which', side_effect=lambda name: uv if name == 'uv' else '/usr/bin/' + name), \
                patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                patch.object(setup, 'MODELS', {}), \
                patch.object(setup.sys, 'stdout', output):
            setup.main()
        commands = [call.args[0] for call in run.call_args_list]
        return commands, output.getvalue()

    def test_linux_uses_bin_python_for_environment_and_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands, output = self._run_main(root)
        python = str(root / '.venv' / 'bin' / 'python')
        self.assertEqual(commands[0], ['/usr/bin/uv', 'venv', '--python', sys.executable,
                                       '--system-site-packages', str(root / '.venv')])
        self.assertEqual(commands[1], [python, '-c', 'import cv2, numpy'])
        self.assertEqual(commands[2], [python, '-c', 'import PIL'])
        self.assertEqual(commands[-1], ['/usr/bin/uv', 'pip', 'install', '--python', python,
                                        'websocket-client==1.9.2'])
        self.assertIn(f'Run: {Path(".venv") / "bin" / "python"} -m mkw_tracker', output)

    def test_windows_uses_scripts_python_exe_for_environment_and_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands, output = self._run_main(root, platform='win32')
        python = str(root / '.venv' / 'Scripts' / 'python.exe')
        self.assertEqual(commands[0], ['/usr/bin/uv', 'venv', '--python', sys.executable,
                                       '--system-site-packages', str(root / '.venv')])
        self.assertEqual(commands[1], [python, '-c', 'import cv2, numpy'])
        self.assertEqual(commands[2], [python, '-c', 'import PIL'])
        self.assertEqual(commands[-1], ['/usr/bin/uv', 'pip', 'install', '--python', python,
                                        'websocket-client==1.9.2'])
        self.assertIn(f'Run: .venv{os.sep}Scripts{os.sep}python.exe -m mkw_tracker', output)

    def test_windows_without_uv_falls_back_to_venv_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands, _ = self._run_main(root, platform='win32', uv=None)
        python = str(root / '.venv' / 'Scripts' / 'python.exe')
        self.assertEqual(commands[0], [sys.executable, '-m', 'venv', '--system-site-packages', str(root / '.venv')])
        self.assertEqual(commands[-1][:4], [python, '-m', 'pip', 'install'])

    def test_paths_with_spaces_and_unicode_survive_command_lines(self):
        for platform in ('linux', 'win32'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as base:
                root = Path(base) / 'My Project 日本語'
                root.mkdir()
                commands, _ = self._run_main(root, platform=platform)
            python = str(root / '.venv' / ('Scripts/python.exe' if platform == 'win32' else 'bin/python'))
            self.assertIn('My Project 日本語', python)
            self.assertEqual(commands[0][-1], str(root / '.venv'))
            self.assertEqual(commands[1], [python, '-c', 'import cv2, numpy'])
            self.assertEqual(commands[-1][4], python)

    def test_windows_setup_accepts_discovered_tesseract_outside_path(self):
        with tempfile.TemporaryDirectory() as directory:
            commands, output = self._run_main(
                Path(directory), platform='win32', tesseract='C:/Program Files 日本語/Tesseract-OCR/tesseract.exe')
        self.assertIn('Capture dependencies and English/Japanese OCR data ready', output)
        self.assertTrue(commands)

    def test_missing_tesseract_explains_default_or_custom_install(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SystemExit) as error:
            self._run_main(Path(directory), platform='win32', tesseract=None)
        self.assertIn('default installation folder', str(error.exception))
        self.assertIn('PATH', str(error.exception))


class NeuralReaderDiscoveryTests(unittest.TestCase):
    def _root(self, name=''):
        base = Path(tempfile.mkdtemp(prefix='mkw-reader-'))
        self.addCleanup(shutil.rmtree, base)
        root = base / name if name else base
        if name:
            root.mkdir()
        return root

    def test_reader_python_paths_follow_platform_layout(self):
        for platform in ('linux', 'win32'):
            for suffix in ('', 'My Project 日本語'):
                with self.subTest(platform=platform, suffix=suffix), \
                        patch.object(neural_ocr.sys, 'platform', platform):
                    root = self._root(suffix)
                    for engine in (*QWEN_PROFILES, 'paddle'):
                        with patch('mkw_tracker.neural_ocr.Path.cwd', return_value=root):
                            reader = neural_ocr.make_name_reader(engine)
                        self.addCleanup(reader.close)
                        environment = 'paddle-venv' if engine == 'paddle' else QWEN_PROFILES[engine]['environment']
                        expected = root / '.local' / environment / (
                            'Scripts/python.exe' if platform == 'win32' else 'bin/python')
                        self.assertEqual(reader.python_path, expected)

    def test_auto_discovers_windows_scripts_layout_and_keeps_priority(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            for filename in ('.local/vlm-venv/Scripts/python.exe', '.local/models/qwen3-vl-8b/config.json',
                             '.local/paddle-venv/Scripts/python.exe', '.local/models/paddle-ppocrv5/inference.json'):
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            for free_memory, expected in (('24576\n', 'qwen'), ('8192\n', 'paddle')):
                with self.subTest(free_memory=free_memory), \
                        patch.object(neural_ocr.sys, 'platform', 'win32'), \
                        patch('mkw_tracker.neural_ocr.Path.cwd', return_value=root), \
                        patch('mkw_tracker.neural_ocr.subprocess.run',
                              return_value=SimpleNamespace(stdout=free_memory)):
                    reader = neural_ocr.make_name_reader('auto')
                self.addCleanup(reader.close)
                self.assertEqual(reader.engine, expected)
            for filename in ('.local/vlm-venv/Scripts/python.exe', '.local/paddle-venv/Scripts/python.exe'):
                (root / filename).unlink()
            with patch.object(neural_ocr.sys, 'platform', 'win32'), \
                    patch('mkw_tracker.neural_ocr.Path.cwd', return_value=root), \
                    patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
                self.assertIsNone(neural_ocr.make_name_reader('auto'))
            probe.assert_not_called()

    def test_explicit_python_path_wins_over_platform_layout(self):
        with tempfile.TemporaryDirectory() as base, \
                patch.object(neural_ocr.sys, 'platform', 'win32'), \
                patch('mkw_tracker.neural_ocr.Path.cwd', return_value=Path(base)):
            reader = neural_ocr.NeuralNameReader('paddle', python_path='C:/My Project 日本語/python.exe')
        self.addCleanup(reader.close)
        self.assertEqual(reader.python_path, Path('C:/My Project 日本語/python.exe'))


if __name__ == '__main__':
    unittest.main()
