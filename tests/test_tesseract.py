"""Tesseract discovery uses fake files and never runs an executable."""

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker import tesseract


class TesseractDiscoveryTests(unittest.TestCase):
    def setUp(self):
        guard = patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_path_install_wins_over_default_windows_folders(self):
        with patch.object(tesseract.sys, 'platform', 'win32'), \
                patch.object(tesseract.shutil, 'which', return_value='custom/tesseract.exe') as which, \
                patch.object(Path, 'is_file') as is_file:
            self.assertEqual(tesseract.tesseract_executable(), 'custom/tesseract.exe')
        which.assert_called_once_with('tesseract')
        is_file.assert_not_called()

    def test_windows_defaults_with_spaces_and_unicode(self):
        for variable, suffix in (('LOCALAPPDATA', 'Programs/Tesseract-OCR'),
                                 ('ProgramW6432', 'Tesseract-OCR'),
                                 ('ProgramFiles', 'Tesseract-OCR')):
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'User 日本語 with spaces'
                executable = root / suffix / 'tesseract.exe'
                executable.parent.mkdir(parents=True)
                executable.touch()
                environment = {variable: str(root)}
                with patch.object(tesseract.sys, 'platform', 'win32'), \
                        patch.object(tesseract.shutil, 'which', return_value=None), \
                        patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(tesseract.tesseract_executable(), str(executable))
                    self.assertEqual(dict(os.environ), environment)

    def test_current_user_install_precedes_machine_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user = root / 'user/Programs/Tesseract-OCR/tesseract.exe'
            machine = root / 'machine/Tesseract-OCR/tesseract.exe'
            for executable in (user, machine):
                executable.parent.mkdir(parents=True)
                executable.touch()
            with patch.object(tesseract.sys, 'platform', 'win32'), \
                    patch.object(tesseract.shutil, 'which', return_value=None), \
                    patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'user'),
                                            'ProgramFiles': str(root / 'machine')}, clear=True):
                self.assertEqual(tesseract.tesseract_executable(), str(user))

    def test_non_windows_only_uses_path(self):
        with patch.object(tesseract.sys, 'platform', 'linux'), \
                patch.object(tesseract.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_file') as is_file:
            self.assertIsNone(tesseract.tesseract_executable())
        is_file.assert_not_called()

    def test_unset_variables_do_not_search_relative_directories(self):
        with patch.object(tesseract.sys, 'platform', 'win32'), \
                patch.object(tesseract.shutil, 'which', return_value=None), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(Path, 'is_file') as is_file:
            self.assertIsNone(tesseract.tesseract_executable())
        is_file.assert_not_called()

    def test_directory_named_tesseract_exe_is_not_an_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'Tesseract-OCR/tesseract.exe').mkdir(parents=True)
            with patch.object(tesseract.sys, 'platform', 'win32'), \
                    patch.object(tesseract.shutil, 'which', return_value=None), \
                    patch.dict(os.environ, {'ProgramFiles': directory}, clear=True):
                self.assertIsNone(tesseract.tesseract_executable())


class TesseractRuntimeTests(unittest.TestCase):
    def test_ocr_uses_discovered_executable_as_single_argument(self):
        try:
            from mkw_tracker import recognition
        except ImportError:
            self.skipTest('capture dependencies required')
        executable = 'C:/Program Files 日本語/Tesseract-OCR/tesseract.exe'
        result = SimpleNamespace(returncode=0, stdout=b'level\ttext\tconf\ttop\theight\n', stderr=b'')
        with patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')), \
                patch.object(recognition, 'tesseract_executable', return_value=executable), \
                patch.object(recognition.subprocess, 'run', return_value=result) as run:
            reader = recognition.ResultReader()
            self.assertEqual(reader._ocr(recognition.np.zeros((20, 20), dtype=recognition.np.uint8)), [])
        self.assertEqual(run.call_args.args[0][0], executable)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_ocr_keeps_missing_executable_failure_when_not_discovered(self):
        try:
            from mkw_tracker import recognition
        except ImportError:
            self.skipTest('capture dependencies required')
        with patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')), \
                patch.object(recognition, 'tesseract_executable', return_value=None), \
                patch.object(recognition.subprocess, 'run', side_effect=FileNotFoundError) as run:
            with self.assertRaises(FileNotFoundError):
                recognition.ResultReader()._ocr(recognition.np.zeros((20, 20), dtype=recognition.np.uint8))
        self.assertEqual(run.call_args.args[0][0], 'tesseract')


if __name__ == '__main__':
    unittest.main()
