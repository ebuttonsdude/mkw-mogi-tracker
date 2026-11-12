import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.credentials import OBSConnection
from mkw_tracker.videos import _WindowsDirectory


class OBSCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.connection = OBSConnection(self.root)

    def test_password_persists_private_and_only_metadata_is_public(self):
        self.assertFalse(self.connection.path.exists())
        values = self.connection.resolve(url='ws://localhost:4456', password='private☆password')
        self.connection.save(values)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.connection.path.stat().st_mode), 0o600)
        self.assertNotIn('private☆password', json.dumps(self.connection.public()))
        self.assertTrue(self.connection.public()['password_saved'])
        if os.name != 'nt':
            self.connection.path.chmod(0o644)
        restored = OBSConnection(self.root)
        self.assertEqual(restored.resolve(), values)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(restored.path.stat().st_mode), 0o600)
        restored.forget_password()
        self.assertFalse(OBSConnection(self.root).public()['password_saved'])
        self.assertEqual(OBSConnection(self.root).resolve()['password'], '')
        self.assertEqual(restored.public()['url'], 'ws://localhost:4456')

    def test_bad_endpoints_password_types_and_unencodable_text_are_rejected(self):
        for url in ('https://localhost:4455', 'ws://example.com:4455', 'ws://127.0.0.1',
                    'ws://user:password@localhost:4455', 'ws://localhost:4455?secret=1',
                    'ws://localhost:4455/other', 'ws://localhost:0', None, {}):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.connection.resolve(url=url)
        for url in (' ' * 40000 + 'ws://localhost:4455', '\nws://localhost:4455',
                    'ws://localhost:4455 ', 'ws://localhost:\t4455'):
            with self.assertRaises(ValueError):
                self.connection.resolve(url=url)
        for password in (None, 4, True, [], 'x' * 4097, 'private-token-\ud800'):
            with self.assertRaises(ValueError) as context:
                self.connection.resolve(password=password)
            self.assertNotIn('private-token', str(context.exception))
        self.assertFalse(self.connection.path.exists())

    def test_failed_write_preserves_previous_credentials_and_has_safe_error(self):
        previous = self.connection.resolve(password='old-private-token')
        self.connection.save(previous)
        changed = self.connection.resolve(password='new-private-token')
        target = _WindowsDirectory if os.name == 'nt' else Path
        with patch.object(target, 'replace', side_effect=OSError('new-private-token')):
            with self.assertRaises(OSError):
                self.connection.save(changed)
        self.assertEqual(self.connection.resolve(), previous)
        self.assertEqual(OBSConnection(self.root).resolve(), previous)
        self.assertNotIn('private-token', json.dumps(self.connection.public()))
        self.assertIsNotNone(self.connection.public()['error'])
        self.assertEqual(list(self.root.glob('.obs-connection-*.tmp')), [])

    def test_unsafe_private_file_and_malformed_settings_fail_without_exposing_payload(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"url":"ws://localhost:4455","password":"outside-token"}')
        try:
            self.connection.path.symlink_to(outside)
        except OSError:
            if os.name != 'nt':
                raise
        else:
            with self.assertRaises(ValueError):
                OBSConnection(self.root)
            with self.assertRaises(OSError):
                self.connection.save(self.connection.resolve(password='new-token'))
            self.assertIn('outside-token', outside.read_text())
            self.connection.path.unlink()
        for value in ('not-json', '[]', '{"password":"private-token"}',
                      '{"url":"ws://example.com:4455","password":"private-token"}'):
            self.connection.path.write_text(value)
            with self.assertRaises(ValueError) as context:
                OBSConnection(self.root)
            self.assertNotIn('private-token', str(context.exception))

    def test_forget_during_start_cannot_restore_stale_password(self):
        self.connection.save(self.connection.resolve(password='old-private-token'))
        prepared, generation = self.connection.prepare()
        self.connection.forget_password()
        self.assertFalse(self.connection.save(prepared, expected_generation=generation))
        self.assertFalse(self.connection.public()['password_saved'])
        self.assertEqual(OBSConnection(self.root).resolve()['password'], '')

    @unittest.skipUnless(os.name == 'nt', 'Win32 handle and ACL regression')
    def test_windows_credentials_have_protected_owner_only_dacl(self):
        import ctypes
        from ctypes import wintypes

        self.connection.save(self.connection.resolve(password='private-token'))
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        advapi.GetFileSecurityW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        advapi.GetFileSecurityW.restype = wintypes.BOOL
        convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD))
        convert.restype = wintypes.BOOL
        needed = wintypes.DWORD()
        advapi.GetFileSecurityW(str(self.connection.path), 4, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        self.assertTrue(advapi.GetFileSecurityW(str(self.connection.path), 4, buffer,
                                              len(buffer), ctypes.byref(needed)))
        text = wintypes.LPWSTR()
        self.assertTrue(convert(buffer, 1, 4, ctypes.byref(text), None))
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            self.assertEqual(text.value, 'D:P(A;;FA;;;OW)')
        finally:
            kernel.LocalFree(text)

    @unittest.skipUnless(os.name == 'nt', 'Win32 handle regression')
    def test_windows_read_handle_cannot_be_exchanged_after_open(self):
        self.connection.save(self.connection.resolve(password='private-token'))
        original_open = _WindowsDirectory._open

        def open_and_exchange(filesystem, path, *args, **kwargs):
            handle = original_open(filesystem, path, *args, **kwargs)
            if path == self.connection.path:
                with self.assertRaises(PermissionError):
                    path.rename(path.with_suffix('.exchanged'))
            return handle

        with patch.object(_WindowsDirectory, '_open', open_and_exchange):
            self.assertEqual(OBSConnection(self.root).resolve()['password'], 'private-token')
