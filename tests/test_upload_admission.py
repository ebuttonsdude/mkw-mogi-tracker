"""Concurrent upload admission without real video decoding or capture devices."""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

from mkw_tracker.server import Application, make_server
from mkw_tracker.videos import _WindowsDirectory


class UploadAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Application(':memory:', self.directory.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.directory.cleanup()

    def upload(self, connection, payload=b'video', length=None):
        connection.putrequest('POST', '/api/imports/upload')
        connection.putheader('X-Filename', 'sample.mp4')
        connection.putheader('Content-Length', str(len(payload) if length is None else length))
        connection.endheaders(payload)

    def test_partial_upload_excludes_other_upload_import_and_obs_until_handoff(self):
        opened = threading.Event()
        original_upload = self.app.videos._upload

        @contextmanager
        def open_file(descriptor, name):
            with original_upload(descriptor, name) as output:
                opened.set()
                yield output

        local = Path(self.directory.name) / 'local.mp4'
        local.write_bytes(b'local')
        # An invalid mock video ends the job before any recognizer is created.
        self.app.imports.video_factory = lambda _: Mock(isOpened=lambda: False)
        first = HTTPConnection('127.0.0.1', self.port, timeout=5)
        second = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            with patch.object(self.app.videos, '_upload', side_effect=open_file):
                self.upload(first, b'first', length=10)
                self.assertTrue(opened.wait(3))
                with self.assertRaisesRegex(ValueError, 'upload'):
                    self.app.imports.start(str(local))
                with self.assertRaisesRegex(ValueError, 'imports/uploads'):
                    self.app.cameras.start(capture_mode='obs', obs_source='Game')
                self.upload(second)
                response = second.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn('upload', json.loads(response.read())['error'])
                # No app lock is held while the first request waits for bytes.
                acquired = self.app.lock.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    self.app.lock.release()
                first.send(b'final')
                response = first.getresponse()
                self.assertEqual(response.status, 200)
                job = json.loads(response.read())
                self.app.imports.thread.join(3)
                self.assertEqual(Path(job['path']).read_bytes(), b'firstfinal')
                self.assertEqual(self.app.active_uploads, set())
        finally:
            first.close()
            second.close()

    def test_failed_import_cleanup_rejects_upload_before_creating_file(self):
        self.app.imports.cleanup_failed = True
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            self.upload(connection)
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn('restart', json.loads(response.read())['error'])
            self.assertEqual(list(self.app.videos.root.iterdir()), [])
            self.assertEqual(self.app.active_uploads, set())
        finally:
            connection.close()
            self.app.imports.cleanup_failed = False

    def test_windows_special_filenames_are_rejected_on_all_platforms(self):
        for name in ('stream:payload.mp4', 'bad?.mp4', 'bad*.mp4', 'bad|.mp4', 'bad<.mp4'):
            with self.subTest(name=name):
                connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
                try:
                    connection.request('POST', '/api/imports/upload', body=b'video', headers={'X-Filename': name})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertIn('plain video filename', json.loads(response.read())['error'])
                finally:
                    connection.close()
        self.assertFalse(self.app.videos.root.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX permission regression')
    def test_directory_permission_error_does_not_enable_path_fallback(self):
        self.app.videos.root.mkdir()
        with patch('mkw_tracker.videos.os.open', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.app.videos.snapshot()

    @unittest.skipUnless(os.name == 'nt', 'Win32 handle regression')
    def test_windows_upload_pins_parent_and_file_until_handoff(self):
        root = self.app.videos.root
        root.mkdir()
        with self.app.videos._directory() as directory:
            self.assertIsInstance(directory, _WindowsDirectory)
            with self.app.videos._upload(directory, 'sample.mp4') as output:
                output.write(b'video')
                output.flush()
                with self.assertRaises(PermissionError):
                    root.rename(root.with_name('exchanged'))
                with self.assertRaises(PermissionError):
                    (root / 'sample.mp4').rename(root / 'exchanged.mp4')
                self.assertEqual((root / 'sample.mp4').read_bytes(), b'video')
        result = self.app.videos.delete(self.app.videos._id('sample.mp4'))
        self.assertEqual(result['deleted_files'], 1)
        self.assertFalse((root / 'sample.mp4').exists())

    @unittest.skipUnless(os.name == 'nt', 'Win32 handle regression')
    def test_windows_failed_upload_deletes_opened_file(self):
        root = self.app.videos.root
        root.mkdir()
        with self.app.videos._directory() as directory:
            with self.assertRaisesRegex(ValueError, 'interrupted'):
                with self.app.videos._upload(directory, 'sample.mp4') as output:
                    output.write(b'partial')
                    raise ValueError('interrupted')
        self.assertEqual(list(root.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Win32 handle identity regression')
    def test_windows_failed_upload_never_deletes_exchanged_replacement(self):
        root = self.app.videos.root
        root.mkdir()
        with self.app.videos._directory() as directory:
            original_open = directory.open

            def exchange_before_cleanup(name, **options):
                if options.get('delete'):
                    (root / name).rename(root / 'saved-partial.mp4')
                    (root / name).write_bytes(b'replacement')
                return original_open(name, **options)

            with patch.object(directory, 'open', side_effect=exchange_before_cleanup):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    with self.app.videos._upload(directory, 'sample.mp4') as output:
                        output.write(b'partial')
                        raise RuntimeError('interrupted')
        self.assertEqual((root / 'sample.mp4').read_bytes(), b'replacement')
        self.assertEqual((root / 'saved-partial.mp4').read_bytes(), b'partial')

    @unittest.skipUnless(os.name == 'nt', 'Win32 change-time regression')
    def test_windows_same_size_change_with_restored_mtime_is_protected(self):
        root = self.app.videos.root
        root.mkdir()
        path = root / 'sample.mp4'
        path.write_bytes(b'video')
        with self.app.videos._directory() as directory:
            entry, expected = self.app.videos._entries(directory)[0]
            # NTFS timestamps have 100 ns units, but ChangeTime comes from the
            # system clock: two writes within one clock tick can be identical.
            time.sleep(.05)
            path.write_bytes(b'other')
            os.utime(path, ns=(path.stat().st_atime_ns, expected.st_mtime_ns))
            handle = directory.open(path.name)
            try:
                changed = directory.fstat(handle)
            finally:
                os.close(handle)
            self.assertEqual((changed.st_dev, changed.st_ino, changed.st_size, changed.st_mtime_ns),
                             (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns))
            self.assertGreater(changed.st_ctime_ns, expected.st_ctime_ns,
                               'The mutation must advance NTFS ChangeTime before testing the fingerprint')
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.app.videos._unlink(directory, entry, expected)
        self.assertEqual(path.read_bytes(), b'other')

    @unittest.skipUnless(os.name == 'nt', 'Win32 junction regression')
    def test_windows_junction_upload_root_is_refused(self):
        outside = Path(self.directory.name) / 'outside'
        outside.mkdir()
        original = outside / 'original.mp4'
        original.write_bytes(b'original')
        root = self.app.videos.root
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root), str(outside)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises((ValueError, OSError)):
                self.app.videos.snapshot()
            with self.assertRaises((ValueError, OSError)):
                with _WindowsDirectory(root):
                    self.fail('Opened a junction as a managed directory')
            self.assertEqual(original.read_bytes(), b'original')
        finally:
            root.rmdir()  # Remove only the junction, never its target.
