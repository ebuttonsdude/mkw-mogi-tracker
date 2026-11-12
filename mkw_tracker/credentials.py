"""Private, local-only OBS connection preferences; never serialize the password."""

import ipaddress
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import uuid4

_UNSET = object()


def _validate(url, password):
    if (not isinstance(url, str) or len(url) > 2048
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)):
        raise ValueError('OBS endpoint must be a bounded loopback WebSocket URL without whitespace')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        valid = (parsed.scheme in ('ws', 'wss') and local and parsed.username is None
                 and parsed.password is None and not parsed.query and not parsed.fragment
                 and parsed.path in ('', '/') and parsed.port is not None and 0 < parsed.port <= 65535)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('OBS endpoint must be a loopback WebSocket URL with a port')
    if not isinstance(password, str) or len(password) > 4096:
        raise ValueError('OBS password must be text of at most 4096 characters')
    try:
        password.encode('utf-8')
    except UnicodeError:
        raise ValueError('OBS password must be valid UTF-8 text') from None
    return {'url': url, 'password': password}


class OBSConnection:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'obs-connection.json'
        self.lock = threading.RLock()
        self._values = {'url': 'ws://127.0.0.1:4455', 'password': ''}
        self.error = None
        self._generation = 0
        if self.path.exists() or self.path.is_symlink():
            self._read()

    def _read(self):
        try:
            if os.name == 'nt':
                from .videos import _WindowsDirectory
                with _WindowsDirectory(self.path.parent) as directory:
                    descriptor = directory.open(self.path.name, private=True)
            else:
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
                descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError('Not a private regular file')
                if hasattr(os, 'fchmod'):
                    os.fchmod(source.fileno(), 0o600)
                raw = source.read(32769)
            if len(raw) > 32768:
                raise ValueError('Oversized credentials file')
            values = json.loads(raw)
            if not isinstance(values, dict) or set(values) != {'url', 'password'}:
                raise ValueError('Invalid private connection settings')
            self._values = _validate(**values)
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('Could not read private OBS connection settings. Check obs-connection.json.') from error

    def resolve(self, *, url=_UNSET, password=_UNSET):
        with self.lock:
            return _validate(self._values['url'] if url is _UNSET else url,
                             self._values['password'] if password is _UNSET else password)

    def prepare(self, **fields):
        with self.lock:
            return self.resolve(**fields), self._generation

    def save(self, values, *, expected_generation=None):
        values = _validate(**values)
        temporary = None
        with self.lock:
            if expected_generation is not None and expected_generation != self._generation:
                return False  # An intervening Forget action must not be undone.
            try:
                if os.name == 'nt':
                    self._save_windows(values)
                    self._values = dict(values)
                    self._generation += 1
                    self.error = None
                    return True
                if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
                    raise OSError('Unsafe private settings file')
                descriptor, name = tempfile.mkstemp(prefix='.obs-connection-', suffix='.tmp', dir=self.path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                    json.dump(values, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(self.path)
                self._values = dict(values)
                self._generation += 1
                self.error = None
                return True
            except OSError:
                self.error = 'Could not save the OBS password locally. Check data-directory permissions and free space.'
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _save_windows(self, values):
        from .videos import _WindowsDirectory

        with _WindowsDirectory(self.path.parent) as directory:
            try:
                existing = directory.open(self.path.name)
            except FileNotFoundError:
                pass
            else:
                os.close(existing)
            name = f'.obs-connection-{uuid4()}.tmp'
            descriptor = directory.open(name, create=True, private=True, delete=True)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                try:
                    json.dump(values, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                    directory.replace(output.fileno(), self.path.name)
                except BaseException:
                    directory.unlink(output.fileno())
                    raise

    def forget_password(self):
        with self.lock:
            self.save(dict(self._values, password=''))
            return self.public()

    def public(self):
        with self.lock:
            return {'url': self._values['url'], 'password_saved': bool(self._values['password']), 'error': self.error}
