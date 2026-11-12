"""Local font registry and cluster-preserving PNG fallback; no downloads or assets."""

from collections import OrderedDict
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import stat
from struct import error as StructError, unpack_from
import sys
import threading
import unicodedata
from urllib.parse import quote


def _font_codepoints(path, index, modified_ns, file_size):
    """Read the selected OpenType Unicode cmap, not the font's filename or family.

    The stat arguments remain compatible with existing callers; content hashes
    invalidate coverage when bytes change. Only BMP/full-Unicode cmaps are used.
    """
    return _data_codepoints(Path(path).read_bytes(), index)


def _parse_codepoints(data, index):
    offset = unpack_from('>I', data, 12 + index * 4)[0] if data[:4] == b'ttcf' else 0
    tables = unpack_from('>H', data, offset + 4)[0]
    cmap = next(unpack_from('>I', data, position + 8)[0]
                for position in range(offset + 12, offset + 12 + tables * 16, 16)
                if data[position:position + 4] == b'cmap')
    subtables = []
    for position in range(cmap + 4, cmap + 4 + unpack_from('>H', data, cmap + 2)[0] * 8, 8):
        platform, encoding, relative = unpack_from('>HHI', data, position)
        subtable = cmap + relative
        form = unpack_from('>H', data, subtable)[0]
        if form in (4, 12) and (platform == 0 or (platform, encoding) in ((3, 1), (3, 10))):
            priority = 3 if (platform, encoding) == (3, 10) else 2 if platform == 0 else 1
            subtables.append((priority, encoding, subtable, form))
    _, _, subtable, form = max(subtables)
    supported = set()
    if form == 12:
        groups = unpack_from('>I', data, subtable + 12)[0]
        for position in range(subtable + 16, subtable + 16 + groups * 12, 12):
            start, end, glyph = unpack_from('>III', data, position)
            if start > end or end > 0x10ffff:
                raise ValueError('Invalid Unicode font range')
            supported.update(range(start + (glyph == 0), end + 1))
    else:
        segments = unpack_from('>H', data, subtable + 6)[0] // 2
        ends = subtable + 14
        starts = ends + segments * 2 + 2
        deltas = starts + segments * 2
        ranges = deltas + segments * 2
        for segment in range(segments):
            end = unpack_from('>H', data, ends + segment * 2)[0]
            start = unpack_from('>H', data, starts + segment * 2)[0]
            delta = unpack_from('>h', data, deltas + segment * 2)[0]
            location = ranges + segment * 2
            relative = unpack_from('>H', data, location)[0]
            for codepoint in range(start, end + 1):
                glyph = (unpack_from('>H', data, location + relative + (codepoint - start) * 2)[0]
                         if relative else codepoint)
                if (not relative or glyph) and (glyph + delta) % 65536:
                    supported.add(codepoint)
    return frozenset(supported)



def valid_font_id(value):
    return (isinstance(value, str) and len(value) <= 200
            and not any(c in value for c in '/\\:')
            and value not in ('.', '..')
            and not any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in value))


def system_font_roots():
    roots = [Path('/usr/share/fonts'), Path('/usr/local/share/fonts'),
             Path.home() / '.local/share/fonts', Path.home() / '.fonts']
    if sys.platform == 'win32':
        roots += [Path(os.environ.get('SystemRoot') or os.environ.get('WINDIR') or 'C:/Windows') / 'Fonts',
                  Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Microsoft/Windows/Fonts']
    if sys.platform == 'darwin':
        roots += [Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home() / 'Library/Fonts']
    return roots


def _priority(path):
    name = path.name.lower()
    return (0 if any(part in name for part in ('notosanscjk', 'notosansjp', 'yugoth', 'meiryo', 'msgothic')) else
            1 if 'notosans' in name else 2,
            0 if 'regular' in name or name == 'notosans[wght].ttf' else 1, name)


def _safe_path(path, root):
    # No symlink/junction escapes, including the fonts directory itself.
    if (root.is_symlink() or path.is_symlink()
            or getattr(root, 'is_junction', lambda: False)()
            or getattr(path, 'is_junction', lambda: False)()):
        return False
    try:
        return path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError):
        return False


class FontLibrary:
    """Runtime IDs are basenames; system IDs are opaque, never absolute paths.

    Scan on startup or explicit refresh, not on every scoreboard poll. Files are
    rechecked when read so removal or replacement safely falls back.
    """

    def __init__(self, data_dir=None, *, roots=None):
        self.directory = Path(data_dir) / 'fonts' if data_dir is not None else None
        self.roots = system_font_roots() if roots is None else list(roots)
        self.entries = {}
        self.explicit = Path(os.environ['MKW_REPORT_FONT']).expanduser() if roots is None and os.environ.get('MKW_REPORT_FONT') else None
        self.refresh()

    def refresh(self):
        entries = {}
        for root in self.roots + ([self.directory] if self.directory else []):
            if not root.is_dir() or root.is_symlink():
                continue
            runtime = root == self.directory
            try:
                paths = sorted(root.iterdir() if runtime else root.rglob('*'), key=_priority)
            except OSError:
                continue
            for path in paths:
                if path.suffix.lower() not in (('.ttf', '.otf') if runtime else ('.ttf', '.otf', '.ttc')):
                    continue
                if not _safe_path(path, root) or not valid_font_id(path.name):
                    continue
                identifier = path.name if runtime else 'system-' + sha256(str(path).encode()).hexdigest()[:24]
                entries[identifier] = (path, root, runtime)
        if self.explicit and self.explicit.is_file():
            entries['environment-font'] = (self.explicit, self.explicit.parent, False)
        self.entries = entries

    def read(self, identifier):
        entry = self.entries.get(identifier)
        if entry is None:
            raise KeyError('font')
        path, root, _ = entry
        if not _safe_path(path, root):
            raise KeyError('font')
        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(path, flags), 'rb') as stream:
                info = os.fstat(stream.fileno())
                current = path.stat()
                if not _safe_path(path, root) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise KeyError('font')
                if not stat.S_ISREG(info.st_mode) or not 12 <= info.st_size <= 64 * 1024 * 1024:
                    raise KeyError('font')
                data = stream.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024 or data[:4] not in (b'OTTO', b'\x00\x01\x00\x00', b'ttcf', b'true'):
                raise KeyError('font')
            return data
        except OSError:
            raise KeyError('font') from None

    def choices(self):
        choices = []
        for identifier, (path, root, runtime) in self.entries.items():
            # Collections are usable by Pillow; browsers use their local CJK
            # families rather than a collection URL (not portable across CEF).
            if path.suffix.lower() == '.ttc' or not _safe_path(path, root):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or not 12 <= info.st_size <= 64 * 1024 * 1024:
                continue
            choices.append({'id': identifier, 'name': path.stem, 'source': 'User' if runtime else 'System',
                            'url': '/api/fonts/' + quote(identifier, safe='') + f'?v={info.st_mtime_ns:x}-{info.st_size:x}'})
        return choices

    def browser(self, preference=''):
        choices = self.choices()
        effective = preference or ('environment-font' if self.explicit else '')
        selected = next((item for item in choices if item['id'] == effective), None)
        # The browser also retains its OS Unicode fallback families. Prefer
        # regular Unicode fonts over italic/decorative fallback candidates.
        fallback = sorted((item for item in choices if item != selected),
                          key=lambda item: _priority(self.entries[item['id']][0]))
        local_fallback = [item for index, item in enumerate(fallback)
                          if index < 8 or self.entries[item['id']][2]]
        return {'id': preference, 'missing': bool(preference and not selected),
                'faces': ([selected] if selected else []) + local_fallback}

    def font(self, ImageFont, size, preference=''):
        return FontStack(self, ImageFont, size, preference)


def _modifier(char):
    cp = ord(char)
    return (unicodedata.category(char).startswith('M') or 0xfe00 <= cp <= 0xfe0f
            or 0xe0100 <= cp <= 0xe01ef or 0x1f3fb <= cp <= 0x1f3ff)


def text_clusters(text):
    """Keep marks, selectors, emoji modifiers, joiners and flag pairs intact."""
    cluster = ''
    for char in str(text):
        regional_pair = (len(cluster) == 1 and 0x1f1e6 <= ord(cluster) <= 0x1f1ff
                         and 0x1f1e6 <= ord(char) <= 0x1f1ff)
        if cluster and not (_modifier(char) or char in '\u200c\u200d'
                            or cluster[-1] in '\u200c\u200d' or regional_pair):
            yield cluster
            cluster = ''
        cluster += char
    if cluster:
        yield cluster


def _required(text):
    return {ord(c) for c in text if c not in '\u200c\u200d'
            and not (0xfe00 <= ord(c) <= 0xfe0f or 0xe0100 <= ord(c) <= 0xe01ef)}


class FontStack:
    def __init__(self, library, ImageFont, size, preference):
        self.size = size
        self.library = library
        self.ImageFont = ImageFont
        self.identifiers = sorted(library.entries, key=lambda key: _priority(library.entries[key][0]))
        if not preference and 'environment-font' in self.identifiers:
            preference = 'environment-font'
        if preference in self.identifiers:
            self.identifiers.remove(preference)
            self.identifiers.insert(0, preference)
        self.loaded = OrderedDict()
        self.loaded_bytes = 0
        self.resolved = OrderedDict()
        self.default = ImageFont.load_default(size=size)

    def _load(self, identifier, required=None):
        font = self.loaded.get(identifier)
        if font is not None:
            if required is not None and not required <= font._report_codepoints:
                return None
            self.loaded.move_to_end(identifier)
            return font
        try:
            data = self.library.read(identifier)
            coverage = _data_codepoints(data)
            # Missing glyphs may scan every cmap, but must not instantiate and
            # retain a native FreeType face for every installed font.
            if required is not None and not required <= coverage:
                return None
            cost = sys.getsizeof(data) + _coverage_weight(coverage)
            # Evict before construction, not after the peak allocation. The
            # default face and the current generator run are outside this LRU.
            while self.loaded and (len(self.loaded) >= MAX_RESIDENT_FONTS
                                   or self.loaded_bytes + cost > MAX_RESIDENT_FONT_BYTES):
                _, previous = self.loaded.popitem(last=False)
                self.loaded_bytes -= previous._report_cost
            with BytesIO(data) as source:
                font = self.ImageFont.truetype(source, self.size)
            # Pillow owns font_bytes; closing the input releases its duplicate
            # BytesIO buffer. No open stream or path is needed for these runs.
            font._report_codepoints = coverage
            font._report_identifier = identifier
            font._report_cost = cost
            if cost <= MAX_RESIDENT_FONT_BYTES:
                self.loaded[identifier] = font
                self.loaded_bytes += cost
            # A valid single font larger than the cache budget is still usable,
            # but is never retained by the stack or its cluster-resolution map.
            return font
        except (KeyError, OSError, ValueError, StructError, StopIteration):
            return None

    def _select(self, cluster):
        if cluster in self.resolved:
            identifier = self.resolved[cluster]
            self.resolved.move_to_end(cluster)
            font = self._load(identifier) if identifier is not None else self.default
            if font is not None:
                return font
            del self.resolved[cluster]
        required = _required(cluster)
        chosen, font = None, None
        for identifier in self.identifiers:
            font = self._load(identifier, required)
            if font is not None:
                chosen = identifier
                break
        else:
            # Keep original text and a visible missing-glyph marker. Construct
            # only the first usable fallback, not all unsupported faces.
            for identifier in self.identifiers:
                font = self._load(identifier)
                if font is not None:
                    chosen = identifier
                    break
        # IDs, never font references: cached clusters cannot pin evicted faces.
        self.resolved[cluster] = chosen
        if len(self.resolved) > MAX_RESOLVED_CLUSTERS:
            self.resolved.popitem(last=False)
        return font if font is not None else self.default

    def runs(self, text):
        previous, value = None, ''
        for cluster in text_clusters(text):
            font = self._select(cluster)
            if (previous is not None and font is not previous
                    and getattr(font, '_report_identifier', None) == getattr(previous, '_report_identifier', None)):
                # Uncached oversized faces still shape adjacent clusters as one
                # run instead of losing kerning/ligatures at each character.
                font = previous
            if previous is not None and previous is not font:
                yield previous, value
                value = ''
            previous, value = font, value + cluster
        if previous is not None:
            yield previous, value

    def getlength(self, text):
        return sum(font.getlength(value) for font, value in self.runs(text))

    def getname(self):
        return self._select('A').getname()

    def getbbox(self, text):
        bounds, x = [], 0
        for selected, value in self.runs(text):
            left, top, right, bottom = selected.getbbox(value, anchor='ls')
            bounds.append((x + left, self.size + top, x + right, self.size + bottom))
            x += selected.getlength(value)
        if not bounds:
            return (0, 0, 0, 0)
        return (min(b[0] for b in bounds), min(b[1] for b in bounds),
                max(b[2] for b in bounds), max(b[3] for b in bounds))


# Per-size rendering cache: bounded face count and retained font-source/cmap
# bytes. Oversized individual faces remain transient rather than losing text.
MAX_RESIDENT_FONTS = 8
MAX_RESIDENT_FONT_BYTES = 64 * 1024 * 1024
MAX_RESOLVED_CLUSTERS = 1024
MAX_COVERAGE_CACHE_BYTES = 8 * 1024 * 1024
MAX_COVERAGE_CACHE_ENTRIES = 32
_COVERAGE_CACHE = OrderedDict()
_COVERAGE_BYTES = 0
_COVERAGE_LOCK = threading.Lock()


def _coverage_weight(coverage):
    # Conservatively count each integer even when Python shares its allocation.
    return sys.getsizeof(coverage) + len(coverage) * sys.getsizeof(0x10ffff)


def _data_codepoints(data, index=0):
    global _COVERAGE_BYTES
    digest = sha256(index.to_bytes(4, 'big'))
    digest.update(data)
    key = digest.digest()
    # Serializing parsing also avoids concurrent duplicate large cmap expansion.
    # Cache keys contain only a fingerprint, never the input font byte blob.
    with _COVERAGE_LOCK:
        if key in _COVERAGE_CACHE:
            _COVERAGE_CACHE.move_to_end(key)
            return _COVERAGE_CACHE[key][0]
        coverage = _parse_codepoints(data, index)
        cost = _coverage_weight(coverage) + 256  # Digest, tuple and LRU bookkeeping.
        if cost <= MAX_COVERAGE_CACHE_BYTES:
            while _COVERAGE_CACHE and (len(_COVERAGE_CACHE) >= MAX_COVERAGE_CACHE_ENTRIES
                                      or _COVERAGE_BYTES + cost > MAX_COVERAGE_CACHE_BYTES):
                _, (_, previous_cost) = _COVERAGE_CACHE.popitem(last=False)
                _COVERAGE_BYTES -= previous_cost
            _COVERAGE_CACHE[key] = (coverage, cost)
            _COVERAGE_BYTES += cost
        return coverage


def draw_text(draw, point, label, *, font, fill):
    if not isinstance(font, FontStack):
        draw.text(point, label, font=font, fill=fill)
        return
    x, y = point
    for selected, value in font.runs(label):
        draw.text((x, y + font.size), value, font=selected, fill=fill, anchor='ls')
        x += selected.getlength(value)
