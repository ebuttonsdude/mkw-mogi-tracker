"""Bounded local theme records; uploaded artwork is never an executable or path."""

import base64
import binascii
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import warnings
from uuid import uuid4

from .themes import all_themes, resolve_theme, _palette

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_LIBRARY_BYTES = 32 * 1024 * 1024
TOKENS = frozenset(resolve_theme()['tokens'])
CUSTOM_ID = re.compile(r'custom-[0-9a-f]{32}')
BUILTIN_IDS = frozenset(theme['id'] for theme in all_themes())


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Colors must use #rrggbb format.')
    return value.lower()


def _image(encoded, *, max_size=(512, 512)):
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError('Choose an image no larger than 4 MiB.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('Invalid base64 image.') from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Choose an image no larger than 4 MiB.')
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ValueError('Image uploads require the optional Pillow report dependency.') from error
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in ('PNG', 'JPEG', 'WEBP'):
                    raise ValueError('Choose a PNG, JPEG or WebP image, not SVG or animation.')
                if getattr(original, 'n_frames', 1) != 1:
                    raise ValueError('Choose a still image, not animation.')
                if max(original.size) > 4096 or original.width * original.height > 16_000_000:
                    raise ValueError('Image dimensions must be at most 4096 px and 16 megapixels.')
                original.load()
                image = ImageOps.exif_transpose(original).convert('RGBA')
                image.thumbnail(max_size)
                # Fresh pixels drop metadata, profiles and original container payloads.
                clean = Image.new('RGBA', image.size)
                clean.putdata(list(image.getdata()))
                output = BytesIO()
                clean.save(output, format='PNG')
                return output.getvalue(), clean
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('The image is invalid or exceeds safe decoding limits.') from error


def suggest_colors(encoded):
    _, image = _image(encoded)
    image.thumbnail((64, 64))
    pixels = [pixel[:3] for pixel in image.getdata() if pixel[3] >= 128]
    if not pixels:
        raise ValueError('The image has no visible colors to suggest.')
    from PIL import Image
    sample = Image.new('RGB', (len(pixels), 1))
    sample.putdata(pixels)
    reduced = sample.quantize(colors=6)
    palette = reduced.getpalette()
    colors = ['#' + ''.join(f'{c:02x}' for c in palette[index * 3:index * 3 + 3])
              for _, index in sorted(reduced.getcolors(), reverse=True)]
    theme = _palette('suggestion', 'Suggested colors', colors[0])
    channels = [int(colors[0][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    luminance = sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    theme['tokens']['accent_text'] = '#000000' if luminance > .179 else '#ffffff'
    return {'tokens': theme['tokens'], 'team_colors': theme['team_colors'], 'palette': colors}


class ThemeLibrary:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'custom-themes.json'
        self.records = {}
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError('Invalid custom theme library.')
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or sum(key not in BUILTIN_IDS for key in payload) > 50:
                raise ValueError('Invalid custom theme library.')
            for identifier, values in payload.items():
                if identifier not in BUILTIN_IDS and not CUSTOM_ID.fullmatch(identifier):
                    raise ValueError('Invalid custom theme identifier.')
                self.records[identifier] = self._validate(values, identifier)

    def _validate(self, values, identifier):
        if not isinstance(values, dict) or set(values) - {'id', 'name', 'tokens', 'team_colors', 'icon_base64'}:
            raise ValueError('Unknown custom theme field.')
        name = values.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('Theme name must be 1–80 characters without controls.')
        name.encode('utf-8')
        tokens = values.get('tokens')
        colors = values.get('team_colors')
        if not isinstance(tokens, dict) or set(tokens) != TOKENS:
            raise ValueError('Provide every theme color token.')
        if not isinstance(colors, list) or len(colors) != 12:
            raise ValueError('Provide exactly twelve standings colors.')
        result = {'id': identifier, 'name': name.strip(),
                  'tokens': {key: _color(value) for key, value in tokens.items()},
                  'team_colors': [_color(value) for value in colors]}
        encoded = values.get('icon_base64')
        if encoded is not None:
            data, _ = _image(encoded)
            result['icon_base64'] = base64.b64encode(data).decode('ascii')
        return result

    def _write(self, records):
        data = json.dumps(records, ensure_ascii=False).encode('utf-8')
        if len(data) > MAX_LIBRARY_BYTES:
            raise ValueError('Theme library is full; remove unused themes first.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.themes-{uuid4().hex}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.records = records

    def prepare(self, values):
        """Decode untrusted image data before acquiring the scoring/application lock."""
        if not isinstance(values, dict):
            raise ValueError('Provide a custom theme.')
        identifier = values.get('id')
        if identifier is not None and (
                not isinstance(identifier, str) or not (CUSTOM_ID.fullmatch(identifier)
                or identifier in BUILTIN_IDS and identifier in self.records)):
            raise ValueError('Built-in themes cannot be overwritten; save a new copy.')
        record = self._validate(values, identifier or 'custom-' + uuid4().hex)
        if 'icon_base64' in values and values['icon_base64'] is None:
            record['icon_base64'] = None
        return record

    def save(self, values):
        return self.save_prepared(self.prepare(values), creating=values.get('id') is None)

    def save_prepared(self, values, *, creating):
        """Commit a record produced by prepare(), under the application lock."""
        identifier = values['id']
        if not creating and identifier not in self.records:
            raise ValueError('Choose an existing custom theme to update.')
        if creating and sum(key not in BUILTIN_IDS for key in self.records) >= 50:
            raise ValueError('Save at most fifty custom themes.')
        record = deepcopy(values)
        if 'icon_base64' not in record and identifier in self.records:
            record['icon_base64'] = self.records[identifier].get('icon_base64')
        records = deepcopy(self.records)
        records[identifier] = record
        self._write(records)
        return self.resolve(identifier)

    def delete(self, identifier):
        if identifier not in self.records:
            raise ValueError('Choose a saved custom theme to delete.')
        records = deepcopy(self.records)
        del records[identifier]
        self._write(records)

    def resolve(self, identifier, *, internal=False):
        if not isinstance(identifier, str):
            raise ValueError('Choose an available theme.')
        if identifier not in self.records:
            return resolve_theme(identifier)
        theme = deepcopy(self.records[identifier])
        encoded = theme.pop('icon_base64', None)
        icon = base64.b64decode(encoded) if encoded else None
        theme.update(custom=True, icon_name=theme['name'] if icon else None, icon_fallback=False,
                     icon_url=f'/api/themes/{identifier}/icon.png?v={hashlib.sha256(icon).hexdigest()[:16]}' if icon else None)
        if identifier in BUILTIN_IDS:
            theme['overridden'] = True
        if internal and icon:
            theme['icon_png'] = icon
        return theme

    def themes(self):
        return [self.resolve(theme['id']) for theme in all_themes()] + [
            self.resolve(identifier) for identifier in self.records if identifier not in BUILTIN_IDS]

    def icon(self, identifier):
        record = self.records.get(identifier)
        if record is None or not record.get('icon_base64'):
            raise KeyError('theme icon')
        return base64.b64decode(record['icon_base64'])
