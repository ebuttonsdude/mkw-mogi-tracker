"""Private external vision configuration; separate from public settings and backups."""

import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from uuid import uuid4

from .external_vision import destination


def _validate(url, model, api_key, acknowledged):
    if not isinstance(url, str):
        raise ValueError('External endpoint URL must be text.')
    if url:
        url = destination(url)[0].geturl()
    if (not isinstance(model, str) or len(model) > 200 or (url and not model.strip())
            or any(ord(c) < 32 or ord(c) > 126 for c in model)):
        raise ValueError('Provide a model ID of at most 200 ASCII characters.')
    if (not isinstance(api_key, str) or len(api_key) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
        raise ValueError('API key must be at most 4096 printable ASCII characters without spaces.')
    if not isinstance(acknowledged, bool):
        raise ValueError('Privacy/cost acknowledgement must be a boolean.')
    return dict(url=url, model=model, api_key=api_key, acknowledged=acknowledged)


class ExternalVisionSettings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'external-vision.json'
        self.lock = threading.RLock()
        self._values = {'url': '', 'model': '', 'api_key': '', 'acknowledged': False}
        self.error = None
        self._generation = uuid4().hex
        if self.path.exists() or self.path.is_symlink():
            self._read()

    def _read(self):
        try:
            if os.name == 'nt':
                from .videos import _WindowsDirectory
                with _WindowsDirectory(self.path.parent) as directory:
                    descriptor = directory.open(self.path.name, private=True)
            else:
                flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
                         | getattr(os, 'O_NONBLOCK', 0))
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
            if not isinstance(values, dict) or set(values) != set(self._values):
                raise ValueError('Invalid private connection settings')
            self._values = _validate(**values)
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('Could not read private external vision settings. Check external-vision.json.') from error

    def prepare(self):
        with self.lock:
            return dict(self._values), self._generation

    def is_current(self, generation):
        # Immutable token reads must not wait behind a slow credential-file fsync;
        # transport cancellation/deadline watchers cannot acquire the storage lock.
        return self._generation == generation

    def configure(self, fields):
        allowed = {'url', 'model', 'api_key', 'acknowledged', 'generation'}
        if (not isinstance(fields, dict) or not set(fields) <= allowed
                or not {'url', 'model', 'acknowledged', 'generation'} <= set(fields)
                or not isinstance(fields['generation'], str)):
            raise ValueError('Provide URL, model, acknowledgement and current configuration generation.')
        with self.lock:
            if fields['generation'] != self._generation:
                raise ValueError('External configuration changed; reload the page before saving.')
            changed = (fields['url'], fields['model']) != (self._values['url'], self._values['model'])
            # A key must not silently follow a new destination/model.
            key = fields.get('api_key', '' if changed else self._values['api_key'])
            values = _validate(fields['url'], fields['model'], key, fields['acknowledged'])
            self.save(values, expected_generation=fields['generation'])
            return self.public()

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
                    self._generation = uuid4().hex
                    self.error = None
                    return True
                if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
                    raise OSError('Unsafe private settings file')
                descriptor, name = tempfile.mkstemp(prefix='.external-vision-', suffix='.tmp', dir=self.path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                    json.dump(values, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(self.path)
                self._values = dict(values)
                self._generation = uuid4().hex
                self.error = None
                return True
            except OSError:
                self.error = 'Could not save private external vision settings. Check data-directory permissions and free space.'
                raise OSError(self.error) from None
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
            name = f'.external-vision-{uuid4()}.tmp'
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

    def forget_key(self):
        with self.lock:
            # Revoke consent as well, interrupting already-created readers/tests.
            self.save(dict(self._values, api_key='', acknowledged=False))
            return self.public()

    def public(self):
        with self.lock:
            return {'url': self._values['url'], 'model': self._values['model'],
                    'acknowledged': self._values['acknowledged'],
                    'api_key_saved': bool(self._values['api_key']),
                    'generation': self._generation, 'error': self.error}
