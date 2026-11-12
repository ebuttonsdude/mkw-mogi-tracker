"""List and remove only app-managed uploaded video copies."""

import errno
import hashlib
import os
import stat
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .imports import TERMINAL, VIDEO_EXTENSIONS


class _WindowsDirectory:
    """Pin every path component against rename and reject all reparse points.

    CRT directory descriptors/dir_fd are unavailable on Windows. Win32 handles
    without FILE_SHARE_DELETE keep paths stable for the lifetime of this scope.
    File deletion and replacement use the opened handle, never a checked path.
    """

    def __init__(self, path):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.types = wintypes
        self.path = Path(os.path.abspath(path))
        self.stack = ExitStack()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                            ctypes.c_void_p, wintypes.DWORD)
        self.kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.kernel.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                            ctypes.c_void_p, wintypes.DWORD)
        self.kernel.SetFileInformationByHandle.restype = wintypes.BOOL

    def _open(self, path, access, creation, *, directory=False, security=None, sharing=3):
        c = self.ctypes
        handle = self.kernel.CreateFileW(str(path), access, sharing, security, creation,
                                         0x02200000, None)  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            attributes = (self.types.DWORD * 2)()
            if not self.kernel.GetFileInformationByHandleEx(handle, 9, c.byref(attributes), c.sizeof(attributes)):
                raise c.WinError(c.get_last_error())
            if attributes[0] & 0x400 or bool(attributes[0] & 0x10) != directory:
                raise OSError(errno.ELOOP, 'Reparse points and non-regular managed paths are not allowed')
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def __enter__(self):
        try:
            for path in (*reversed(self.path.parents), self.path):
                # READ_ATTRIBUTES alone does not participate in Windows sharing
                # checks. LIST_DIRECTORY is required to deny rename/delete.
                handle = self._open(path, 0x81, 3, directory=True)
                self.stack.callback(self.kernel.CloseHandle, handle)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def open(self, name, *, create=False, private=False, delete=False):
        import msvcrt

        if Path(name).name != name or any(char in name for char in '/\\:'):
            raise ValueError('Expected a plain managed filename')
        access = (0x80000000 | (0x40000000 if create else 0) | (0x10000 if delete else 0)
                  | (0x40000 if private else 0))  # WRITE_DAC for owner-only credentials
        c = self.ctypes
        with ExitStack() as stack:
            security = None
            if private:
                # Protected DACL: only the file owner receives access, regardless
                # of inherited directory permissions. OW is the Owner Rights SID.
                advapi = c.WinDLL('advapi32', use_last_error=True)
                convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
                convert.argtypes = (self.types.LPCWSTR, self.types.DWORD,
                                    c.POINTER(c.c_void_p), c.POINTER(self.types.DWORD))
                convert.restype = self.types.BOOL
                descriptor = c.c_void_p()
                if not convert('D:P(A;;FA;;;OW)', 1, c.byref(descriptor), None):
                    raise c.WinError(c.get_last_error())
                self.kernel.LocalFree.argtypes = (c.c_void_p,)
                self.kernel.LocalFree.restype = c.c_void_p
                stack.callback(self.kernel.LocalFree, descriptor)

                class SecurityAttributes(c.Structure):
                    _fields_ = [('length', self.types.DWORD), ('descriptor', c.c_void_p),
                                ('inherit', self.types.BOOL)]

                attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
                security = c.byref(attributes)
            handle = self._open(self.path / name, access, 1 if create else 3, security=security,
                                sharing=1 if private or delete else 3)
            if private:
                advapi.SetKernelObjectSecurity.argtypes = (self.types.HANDLE, self.types.DWORD, c.c_void_p)
                advapi.SetKernelObjectSecurity.restype = self.types.BOOL
                if not advapi.SetKernelObjectSecurity(handle, 0x80000004, descriptor):
                    error = c.WinError(c.get_last_error())
                    self.kernel.CloseHandle(handle)
                    raise error
        try:
            return msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY))
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def _set(self, descriptor, kind, value):
        import msvcrt

        c = self.ctypes
        if not self.kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(descriptor), kind,
                                                     c.byref(value), c.sizeof(value)):
            raise c.WinError(c.get_last_error())

    def fstat(self, descriptor):
        import msvcrt

        c = self.ctypes
        identity = (c.c_ulonglong * 3)()  # FILE_ID_INFO: volume serial + 128-bit ID
        if not self.kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(descriptor), 18,
                                                        c.byref(identity), c.sizeof(identity)):
            raise c.WinError(c.get_last_error())

        # Python's Windows st_ctime is creation time, not metadata-change time.
        class BasicInfo(c.Structure):
            _fields_ = [('created', c.c_longlong), ('accessed', c.c_longlong),
                        ('written', c.c_longlong), ('changed', c.c_longlong), ('attributes', self.types.DWORD)]

        basic = BasicInfo()
        if not self.kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(descriptor), 0,
                                                        c.byref(basic), c.sizeof(basic)):
            raise c.WinError(c.get_last_error())
        info = os.fstat(descriptor)
        return SimpleNamespace(st_dev=identity[0], st_ino=int.from_bytes(bytes(identity)[8:], 'little'),
                               st_mode=info.st_mode, st_size=info.st_size, st_mtime=info.st_mtime,
                               st_mtime_ns=info.st_mtime_ns, st_ctime_ns=basic.changed * 100)

    def unlink(self, descriptor):
        self._set(descriptor, 4, self.ctypes.c_ubyte(True))  # FileDispositionInfo

    def replace(self, descriptor, name):
        c = self.ctypes
        target = str(self.path / name)
        length = len(target.encode('utf-16-le'))

        class RenameInfo(c.Structure):
            _fields_ = [('replace', self.types.BOOL), ('root', self.types.HANDLE),
                        ('length', self.types.DWORD), ('name', c.c_wchar * (length // 2 + 1))]

        info = RenameInfo(True, None, length, target)
        self._set(descriptor, 3, info)  # FileRenameInfo


class VideoLibrary:
    def __init__(self, app):
        self.app = app
        self.root = Path(app.data_dir).resolve() / 'uploads'

    @contextmanager
    def _upload_directory(self):
        with ExitStack() as stack:
            if os.name == 'nt':
                stack.enter_context(_WindowsDirectory(self.root.parent))
            self.root.mkdir(parents=os.name != 'nt', exist_ok=True)
            yield stack.enter_context(self._directory())

    @contextmanager
    def _directory(self):
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            yield None
            return
        if not stat.S_ISDIR(info.st_mode) or self.root.resolve() != self.root:
            raise ValueError('The managed uploads directory must not be a symlink.')
        if os.name == 'nt':
            with _WindowsDirectory(self.root) as directory:
                yield directory
            return
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(self.root, flags)
        try:
            opened = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError('The managed uploads directory changed. Try again.')
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _id(name):
        return hashlib.sha256(os.fsencode(name)).hexdigest()

    @staticmethod
    def _fingerprint(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _activity(self, path):
        imports = getattr(self.app, 'imports', None)
        jobs = getattr(imports, 'jobs', [])
        thread = getattr(imports, 'thread', None)
        alive = (thread is not None and thread.is_alive()) or getattr(imports, 'cleanup_failed', False)
        ids, active = [], False
        for index, job in enumerate(jobs):
            try:
                matches = Path(job['path']).expanduser().resolve() == path
            except (KeyError, OSError, RuntimeError, ValueError):
                continue
            if matches:
                ids.append(job['id'])
                active |= job.get('status') not in TERMINAL or (alive and index == len(jobs) - 1)
        for uploading in getattr(self.app, 'active_uploads', set()):
            if Path(uploading).resolve() == path:
                active = True
        return ids, active

    def _entries(self, descriptor):
        if descriptor is None:
            return []
        entries = []
        for name in os.listdir(self.root if isinstance(descriptor, _WindowsDirectory) else descriptor):
            if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                if isinstance(descriptor, _WindowsDirectory):
                    info = os.lstat(self.root / name)
                    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        continue
                    # Use the same handle-based metadata for both snapshots and
                    # deletion; Windows path-stat and CRT fstat differ in Python.
                    handle = descriptor.open(name)
                    try:
                        info = descriptor.fstat(handle)
                    finally:
                        os.close(handle)
                else:
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                continue
            path = self.root / name
            ids, active = self._activity(path)
            entries.append(({'id': self._id(name), 'name': name, 'path': str(path),
                             'size_bytes': info.st_size,
                             'modified_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                             'active': active, 'can_delete': not active, 'import_ids': ids}, info))
        return sorted(entries, key=lambda entry: (entry[0]['modified_at'], entry[0]['name']), reverse=True)

    def snapshot(self):
        with self.app.lock, self._directory() as descriptor:
            files = [entry for entry, _ in self._entries(descriptor)]
            return {'files': files, 'managed_bytes': sum(entry['size_bytes'] for entry in files),
                    'total_files': len(files)}

    def _unlink(self, descriptor, entry, expected):
        # Recheck both the directory and entry immediately before unlinking. The
        # directory descriptor prevents an exchanged parent path redirecting it.
        full = self.root / entry['name']
        if isinstance(descriptor, _WindowsDirectory):
            if self._activity(full)[1]:
                raise ValueError('This video is uploading or importing. Wait for it to finish.')
            try:
                handle = descriptor.open(entry['name'], delete=True)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ValueError('The uploaded video changed. Refresh before deleting it.') from error
                raise
            try:
                current = descriptor.fstat(handle)
                if self._fingerprint(current) != self._fingerprint(expected):
                    raise ValueError('The uploaded video changed. Refresh before deleting it.')
                if self._activity(full)[1]:
                    raise ValueError('This video is uploading or importing. Wait for it to finish.')
                descriptor.unlink(handle)
            finally:
                os.close(handle)
            return
        self._check_directory(descriptor)
        current = os.stat(entry['name'], dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or self._fingerprint(current) != self._fingerprint(expected):
            raise ValueError('The uploaded video changed. Refresh before deleting it.')
        if self._activity(full)[1]:
            raise ValueError('This video is uploading or importing. Wait for it to finish.')
        os.unlink(entry['name'], dir_fd=descriptor)

    def _check_directory(self, descriptor):
        if isinstance(descriptor, _WindowsDirectory):
            return  # All ancestors remain pinned without FILE_SHARE_DELETE.
        root_info, opened = self.root.lstat(), os.fstat(descriptor)
        if (not stat.S_ISDIR(root_info.st_mode)
                or (root_info.st_dev, root_info.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError('The managed uploads directory changed. Try again.')

    @contextmanager
    def _upload(self, descriptor, name):
        windows = isinstance(descriptor, _WindowsDirectory)
        # Do not request DELETE access while the importer opens its own reader:
        # ordinary Windows readers do not grant FILE_SHARE_DELETE.
        handle = (descriptor.open(name, create=True) if windows else
                  os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                          0o600, dir_fd=descriptor))
        try:
            identity = descriptor.fstat(handle) if windows else os.fstat(handle)
        except BaseException:
            os.close(handle)
            raise
        try:
            with os.fdopen(handle, 'wb') as output:
                yield output
        except BaseException:
            if windows:
                # Reopen for deletion after closing the writer, then compare the
                # identity. Never remove an exchanged replacement on failure.
                cleanup = descriptor.open(name, delete=True)
                try:
                    current = descriptor.fstat(cleanup)
                    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                        raise ValueError('The uploaded video changed. Refresh before deleting it.')
                    descriptor.unlink(cleanup)
                finally:
                    os.close(cleanup)
            else:
                os.unlink(name, dir_fd=descriptor)
            raise

    def delete(self, video_id):
        with self.app.lock, self._directory() as descriptor:
            match = next((item for item in self._entries(descriptor) if item[0]['id'] == video_id), None)
            if match is None:
                raise KeyError('Uploaded video not found.')
            entry, info = match
            self._unlink(descriptor, entry, info)
            return {'deleted_files': 1, 'deleted_bytes': entry['size_bytes'], 'name': entry['name']}

    def cleanup(self, older_than_days=30):
        if type(older_than_days) is not int or not 0 <= older_than_days <= 36500:
            raise ValueError('Video age must be a whole number from 0 to 36500 days.')
        cutoff = time.time() - older_than_days * 86400
        result = {'deleted_files': 0, 'deleted_bytes': 0, 'skipped': 0, 'errors': []}
        with self.app.lock, self._directory() as descriptor:
            for entry, info in self._entries(descriptor):
                if entry['active'] or (older_than_days and info.st_mtime >= cutoff):
                    result['skipped'] += 1
                    continue
                try:
                    self._unlink(descriptor, entry, info)
                except (OSError, ValueError) as error:
                    result['errors'].append({'id': entry['id'], 'name': entry['name'], 'error': str(error)})
                    continue
                result['deleted_files'] += 1
                result['deleted_bytes'] += entry['size_bytes']
        return result
