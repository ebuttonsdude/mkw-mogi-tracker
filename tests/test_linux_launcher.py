"""Exercise only a temporary fake interpreter, never the running tracker."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'POSIX shell required')
class LinuxLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / 'tracker folder with spaces'
        self.checkout.mkdir()
        self.launcher = self.checkout / 'startLinux.sh'
        shutil.copyfile(ROOT / 'startLinux.sh', self.launcher)
        self.launcher.chmod(0o755)

    def fake_python(self, exit_code=0):
        python = self.checkout / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n' + f'exit {exit_code}\n')
        python.chmod(0o755)
        return python

    def test_anchors_checkout_and_forwards_arguments_without_splitting(self):
        self.fake_python()
        result = subprocess.run(['sh', str(self.launcher), '--obs-source', 'Capture with spaces',
                                 '--data-dir', 'relative data', '*literal*'], cwd=self.root,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(),
                         [str(self.checkout), '-m', 'mkw_tracker', '--obs-source', 'Capture with spaces',
                          '--data-dir', 'relative data', '*literal*'])

    def test_direct_execution_preserves_interpreter_exit_code(self):
        self.fake_python(7)
        result = subprocess.run([str(self.launcher), '--verbose'], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertIn('--verbose', result.stdout)

    def test_missing_environment_reports_setup_without_starting_anything(self):
        result = subprocess.run(['sh', str(self.launcher)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('sh setupLinux.sh', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse((self.checkout / '.venv').exists())

    def test_nonexecutable_interpreter_reports_setup(self):
        self.fake_python().chmod(0o644)
        result = subprocess.run(['sh', str(self.launcher)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('sh setupLinux.sh', result.stderr)

    def test_launcher_is_executable_and_replaces_shell_without_backgrounding(self):
        source = ROOT / 'startLinux.sh'
        self.assertTrue(source.stat().st_mode & 0o111)
        self.assertIn('exec .venv/bin/python -m mkw_tracker "$@"', source.read_text())
        self.assertIn('startLinux.sh', (ROOT / 'MANIFEST.in').read_text())
