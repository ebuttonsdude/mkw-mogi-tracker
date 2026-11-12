"""Simulated native-Windows startup checks.

Native Windows lacks fcntl and the POSIX-only os attributes O_NOFOLLOW,
O_DIRECTORY, O_CLOEXEC, and os.fchmod. These tests remove those attributes in
an isolated interpreter, then exercise application startup, private OBS
credential persistence, and managed upload-directory handling. They prove the
code paths do not require the missing attributes; final verification still
requires a native Windows host (see .agent/tasks.md T-010).
"""

import subprocess
import sys
import unittest
from pathlib import Path


class SimulatedWindowsStartupTests(unittest.TestCase):
    def _run(self, code):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', code], cwd=root,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, f'stdout={result.stdout!r} stderr={result.stderr!r}')

    def _windows_prelude(self):
        return (
            "import os, sys\n"
            "sys.modules['fcntl'] = None\n"
            "for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC'):\n"
            "    if hasattr(os, name):\n"
            "        delattr(os, name)\n"
            "if hasattr(os, 'fchmod'):\n"
            "    delattr(os, 'fchmod')\n")

    def test_manual_startup_without_posix_hardening(self):
        self._run(
            self._windows_prelude() +
            "import pathlib, tempfile\n"
            "from mkw_tracker.server import Application, make_server\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    app = Application(pathlib.Path(directory) / 'tracker.sqlite3', pathlib.Path(directory), mode='manual')\n"
            "    try:\n"
            "        server = make_server(app, 0)\n"
            "    finally:\n"
            "        server.server_close()\n"
            "        app.close()\n")

    def test_credentials_roundtrip_and_forget_wins_over_stale_save(self):
        self._run(
            self._windows_prelude() +
            "import pathlib, tempfile\n"
            "from mkw_tracker.credentials import OBSConnection\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    connection = OBSConnection(pathlib.Path(directory))\n"
            "    values, generation = connection.prepare(url='ws://127.0.0.1:4455', password='secret')\n"
            "    self_saved = connection.save(values)\n"
            "    assert self_saved is True\n"
            "    public = connection.public()\n"
            "    assert set(public) == {'url', 'password_saved', 'error'}, public\n"
            "    assert public['password_saved'] is True and 'secret' not in str(public)\n"
            "    reloaded = OBSConnection(pathlib.Path(directory))\n"
            "    assert reloaded.resolve()['password'] == 'secret'\n"
            "    connection.forget_password()\n"
            "    stale = connection.save(values, expected_generation=generation)\n"
            "    assert stale is False, 'stale save must not resurrect a forgotten password'\n"
            "    assert connection.public()['password_saved'] is False\n"
            "    fresh = connection.save({'url': 'ws://127.0.0.1:4455', 'password': 'again'})\n"
            "    assert fresh is True and OBSConnection(pathlib.Path(directory)).resolve()['password'] == 'again'\n")

    def test_basic_startup_without_cv2_or_onnxruntime(self):
        # The optional neural OCR path runs in isolated worker venvs; basic
        # startup must not import cv2 or onnxruntime at all.
        self._run(
            "import sys\n"
            "sys.modules['cv2'] = None\n"
            "sys.modules['onnxruntime'] = None\n"
            "import pathlib, tempfile\n"
            "from mkw_tracker.server import Application, make_server\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    app = Application(pathlib.Path(directory) / 'tracker.sqlite3', pathlib.Path(directory), mode='manual')\n"
            "    try:\n"
            "        server = make_server(app, 0)\n"
            "    finally:\n"
            "        server.server_close()\n"
            "        app.close()\n")

    def test_upload_directory_listing_and_delete_without_posix_flags(self):
        self._run(
            self._windows_prelude() +
            "import pathlib, tempfile\n"
            "from mkw_tracker.server import Application\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    app = Application(pathlib.Path(directory) / 'tracker.sqlite3', pathlib.Path(directory), mode='manual')\n"
            "    try:\n"
            "        app.videos.root.mkdir(parents=True, exist_ok=True)\n"
            "        (app.videos.root / 'clip.mp4').write_bytes(b'x')\n"
            "        (app.videos.root / 'notes.txt').write_bytes(b'y')\n"
            "        snapshot = app.videos.snapshot()\n"
            "        assert snapshot['total_files'] == 1, snapshot\n"
            "        video_id = snapshot['files'][0]['id']\n"
            "        deleted = app.videos.delete(video_id)\n"
            "        assert deleted['deleted_files'] == 1 and not (app.videos.root / 'clip.mp4').exists()\n"
            "    finally:\n"
            "        app.close()\n")


if __name__ == '__main__':
    unittest.main()
