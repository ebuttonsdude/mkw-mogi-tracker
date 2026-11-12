"""Bounded runtime-only, per-source Lounge backgrounds; never user paths or URLs."""
import base64
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .theme_library import MAX_IMAGE_BYTES, _image

MAX_LIBRARY_BYTES = 32 * 1024 * 1024


class OverlayBackgrounds:
    def __init__(self, directory):
        self.path = Path(directory) / 'overlay-backgrounds.json'
        self.images = {}
        self.urls = {}
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError('Invalid overlay background library.')
            records = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(records, dict) or len(records) > 16:
                raise ValueError('Invalid overlay background library.')
            for key, encoded in records.items():
                if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', key) or len(key) > 48:
                    raise ValueError('Invalid overlay background source.')
                # Revalidate persisted pixels too, without trusting the container.
                png = self.prepare(encoded)
                self.images[key] = png
                self.urls[key] = self._url(key, png)

    @staticmethod
    def prepare(encoded):
        png = _image(encoded, max_size=(1920, 1080))[0]
        if len(png) > MAX_IMAGE_BYTES:
            raise ValueError('Normalized background exceeds 4 MiB; choose a smaller image.')
        return png

    def _commit(self, images):
        records = {key: base64.b64encode(png).decode('ascii') for key, png in images.items()}
        data = json.dumps(records).encode('utf-8')
        if len(records) > 16 or len(data) > MAX_LIBRARY_BYTES:
            raise ValueError('Overlay background library is full; remove a background first.')
        urls = {key: self.urls[key] if self.images.get(key) == png else self._url(key, png)
                for key, png in images.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.overlay-backgrounds-{uuid4().hex}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.images, self.urls = images, urls

    def save_prepared(self, key, png):
        self._commit(dict(self.images, **{key: png}))

    def remove(self, key):
        if key in self.images:
            self._commit({name: png for name, png in self.images.items() if name != key})

    @staticmethod
    def _url(key, png):
        digest = hashlib.sha256(png).hexdigest()[:16]
        return f'/api/overlay-profiles/{key}/background.png?v={digest}'

    def url(self, key):
        return self.urls.get(key)

    def image(self, key):
        return self.images[key]
