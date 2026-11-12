"""Exercise the real module entrypoint without capture hardware or user data."""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen


class CLIStartupTests(unittest.TestCase):
    def test_help_without_optional_dependencies(self):
        result = subprocess.run(
            [sys.executable, '-m', 'mkw_tracker', '--help'],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--data-dir', result.stdout)
        self.assertIn('--verbose', result.stdout)
        self.assertIn('logs/', result.stdout)

    def _start(self, demo, encoding='utf-8'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'tracker data \u65e5\u672c\u8a9e'
            environment = {**os.environ, 'PYTHONIOENCODING': encoding}
            process = subprocess.Popen(
                [sys.executable, '-m', 'mkw_tracker', '--port', '0', '--data-dir', str(root),
                 *(['--demo'] if demo else [])],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            )
            output = queue.Queue()
            reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
            reader.start()
            try:
                first = output.get(timeout=15).decode('utf-8')
                self.assertTrue(first.startswith('Race tracker: http://127.0.0.1:'), first)
                base = first.strip().split('Race tracker: ', 1)[1]
                with urlopen(base + '/api/state', timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(len(state['players']), 12 if demo else 0)
                with urlopen(base + '/overlay', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                if not demo:
                    self.assertTrue((root / 'tracker.sqlite3').is_file())
                    self.assertIn('Dashboard listener ready', (root / 'logs/http.log').read_text())
                    self.assertIn('Tracker started', (root / 'logs/app.log').read_text())
            finally:
                process.terminate()
                process.communicate(timeout=10)
                reader.join(timeout=5)

    def test_manual_startup_with_unicode_data_path(self):
        self._start(demo=False)

    def test_temporary_demo_startup(self):
        self._start(demo=True)

    def test_unicode_data_path_with_legacy_redirected_stdout(self):
        self._start(demo=False, encoding='cp1252')
