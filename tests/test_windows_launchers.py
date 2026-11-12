"""Launcher contracts plus native Windows checks using a disposable stub app.

The native checks never start the real tracker or touch user data/hardware.
They do not terminate processes; the stub exits normally with its requested code.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


ROOT = Path(__file__).resolve().parents[1]


class WindowsLauncherContractTests(unittest.TestCase):
    def test_both_launchers_use_their_own_folder_and_restore_it(self):
        for name in ('startWindows.bat', 'setupWindows.bat'):
            with self.subTest(name=name):
                script = (ROOT / name).read_text()
                self.assertIn('setlocal EnableExtensions DisableDelayedExpansion', script)
                self.assertIn('pushd "%~dp0"\nif errorlevel 1 goto directory_error', script)
                self.assertIn('popd\nexit /b', script)
                self.assertIn(':directory_error\necho Could not open the tracker folder.\npause\nexit /b 1', script)

    def test_start_uses_local_python_and_preserves_failure(self):
        script = (ROOT / 'startWindows.bat').read_text()
        self.assertIn('if not exist ".venv\\Scripts\\python.exe" goto missing_python', script)
        self.assertIn('".venv\\Scripts\\python.exe" -m mkw_tracker %*\nset "tracker_exit=%errorlevel%"', script)
        self.assertIn('exit /b %tracker_exit%', script)
        self.assertIn('Run setupWindows.bat', script)
        self.assertNotIn('pip install', script)
        self.assertNotIn('uv.exe run', script)

    def test_setup_reuses_guided_installer_and_preserves_failure(self):
        script = (ROOT / 'setupWindows.bat').read_text()
        self.assertIn('uv.exe run --no-project --python 3.12 python tools/setup.py %*\n'
                      'set "setup_exit=%errorlevel%"', script)
        self.assertIn('if errorlevel 1 goto missing_uv', script)
        self.assertIn('pause\npopd\nexit /b %setup_exit%', script)
        # The wrapper explains prerequisites; it never elevates or installs system packages.
        self.assertNotIn('\nwinget install', script)
        self.assertNotIn('ExecutionPolicy', script)

    def test_uv_fallback_is_available_to_child_setup_processes(self):
        script = (ROOT / 'setupWindows.bat').read_text()
        self.assertIn('set "PATH=%USERPROFILE%\\.local\\bin;%PATH%"', script)
        self.assertIn('set "PATH=%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"', script)

    def test_source_distribution_includes_launchers(self):
        manifest = (ROOT / 'MANIFEST.in').read_text()
        self.assertIn('include startWindows.bat setupWindows.bat', manifest)


@unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows cmd.exe; not emulated on Linux.')
class NativeWindowsLauncherTests(unittest.TestCase):
    def test_start_from_another_directory_with_spaces_unicode_and_forwarded_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Tracker 日本語 ! with spaces'
            root.mkdir()
            script = root / 'startWindows.bat'
            shutil.copyfile(ROOT / script.name, script)
            venv.EnvBuilder(with_pip=False).create(root / '.venv')
            package = root / 'mkw_tracker'
            package.mkdir()
            (package / '__init__.py').write_text('')
            (package / '__main__.py').write_text(
                'import json, os, pathlib, sys\n'
                'pathlib.Path("invocation.json").write_text(json.dumps([os.getcwd(), sys.argv[1:]]))\n'
                'raise SystemExit(int(sys.argv[1]))\n'
            )
            env = {key: value for key, value in os.environ.items()
                   if key.upper() not in ('PYTHONPATH', 'PYTHONHOME')}
            for code in (0, 7):
                result = subprocess.run(
                    f'cmd.exe /d /s /c ""{script}" {code} "two words""',
                    cwd=directory, env=env, input='\n', text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                folder, arguments = json.loads((root / 'invocation.json').read_text())
                self.assertEqual(Path(folder), root)
                self.assertEqual(arguments, [str(code), 'two words'])

    def test_missing_python_explains_setup_and_returns_error(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'startWindows.bat'
            shutil.copyfile(ROOT / script.name, script)
            result = subprocess.run(f'cmd.exe /d /s /c ""{script}""', cwd=ROOT,
                                    input='\n', text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('setupWindows.bat', result.stdout)


if __name__ == '__main__':
    unittest.main()
