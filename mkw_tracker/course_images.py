"""User-owned course artwork, normalized to bounded metadata-free PNGs."""

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from .courses import COURSES
from .theme_library import _image

MAX_LIBRARY_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 256
IDENTIFIER = re.compile(r'[0-9a-f]{32}')

# Textual compatibility associations from mkw-livesplit-vos track_icons.py.
# These are exact legacy asset IDs, not fuzzy abbreviations or bundled images.
_LEGACY_COURSE_STEMS = {
    'track_011mbc': 'Mario Bros. Circuit',
    'track_012cc': 'Crown City',
    'track_013ws': 'Whistlestop Summit',
    'track_014dks': 'DK Spaceport',
    'track_021rdh': 'Desert Hills',
    'track_022rsgb': 'Shy Guy Bazaar',
    'track_023rws': 'Wario Stadium',
    'track_024raf': 'Airship Fortress',
    'track_031rdkp': 'DK Pass',
    'track_032sp': 'Starview Peak',
    'track_033rshs': 'Sky-High Sundae',
    'track_034rwsh': "Wario's Shipyard",
    'track_041rktb': 'Koopa Troopa Beach',
    'track_042fo': 'Faraway Oasis',
    'track_051rpb': 'Peach Beach',
    'track_052sss': 'Salty Salty Speedway',
    'track_053rddj': 'Dino Dino Jungle',
    'track_054gbr': 'Great ? Block Ruins',
    'track_061ccf': 'Cheep Cheep Falls',
    'track_062dd': 'Dandelion Depths',
    'track_063bci': 'Boo Cinema',
    'track_064dbb': 'Dry Bones Burnout',
    'track_071rmmm': 'Moo Moo Meadows',
    'track_072rcm': 'Choco Mountain',
    'track_073rtf': "Toad's Factory",
    'track_074bc': "Bowser's Castle",
    'track_081ah': 'Acorn Heights',
    'track_082rmc': 'Mario Circuit',
    'track_083ps': 'Peach Stadium',
    'track_084rr': 'Rainbow Road',
    'track_rci1': 'SNES Choco Island 1',
    'track_rci2': 'SNES Choco Island 2',
    'track_rgv1': 'SNES Ghost Valley 1',
    'track_rgv2': 'SNES Ghost Valley 2',
    'track_rgv3': 'SNES Ghost Valley 3',
    'track_rkb1': 'SNES Koopa Beach 1',
    'track_rmc1': 'SNES Mario Circuit 1',
    'track_rmc2': 'SNES Mario Circuit 2',
    'track_rmc3': 'SNES Mario Circuit 3',
    'track_rvl1': 'SNES Vanilla Lake 1',
}


def _key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def canonical_course(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('Course must be 1–160 characters without controls.')
    value = unicodedata.normalize('NFC', value.strip())
    key = _key(value)
    if not key:
        raise ValueError('Provide a course name containing letters or numbers.')
    exact = [course for course in COURSES if _key(course) == key]
    return exact[0] if len(exact) == 1 else value


def course_from_filename(name):
    """Match an exact legacy asset ID or complete name, never guess track numbers."""
    if (not isinstance(name, str) or len(name) > 255 or '/' in name or '\\' in name
            or any(unicodedata.category(c).startswith('C') for c in name)):
        return None
    stem, dot, extension = name.rpartition('.')
    if not dot or extension.casefold() not in ('png', 'jpg', 'jpeg', 'webp'):
        return None
    legacy = _LEGACY_COURSE_STEMS.get(stem.casefold())
    if legacy is not None:
        return legacy
    candidates = {stem}
    # Prefix/suffix tokens are deliberately narrow, not fuzzy OCR matching.
    for _ in range(3):
        for candidate in tuple(candidates):
            candidates.add(re.sub(r'^(?:course|track|mkw)[_ -]+', '', candidate, flags=re.I))
            candidates.add(re.sub(r'[_ -]+(?:course[_ -]+icon|icon|thumbnail)$', '', candidate, flags=re.I))
    matches = {course for course in COURSES for candidate in candidates if _key(course) == _key(candidate)}
    return next(iter(matches)) if len(matches) == 1 else None


class CourseImageLibrary:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'course-images.json'
        self.records = {}
        self._public_records = {}
        self._course_ids = {}
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError('Invalid course image library.')
            self.records = self._validated(json.loads(self.path.read_text(encoding='utf-8')))
        self._public_records, self._course_ids = self._metadata(self.records)

    def resolve_course(self, course):
        course = canonical_course(course)
        identifier = hashlib.sha256(_key(course).encode('utf-8')).hexdigest()[:32]
        return {'id': identifier, 'course': course, 'exists': identifier in self.records}

    def prepare(self, course, image_base64):
        resolved = self.resolve_course(course)
        png, _ = _image(image_base64)
        return {'id': resolved['id'], 'course': resolved['course'],
                'icon_base64': base64.b64encode(png).decode('ascii')}

    def _validated(self, records):
        if not isinstance(records, dict) or len(records) > MAX_RECORDS:
            raise ValueError('Save at most 256 course images.')
        result = {}
        for identifier, record in records.items():
            if (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
                    or not isinstance(record, dict) or set(record) != {'id', 'course', 'icon_base64'}):
                raise ValueError('Invalid course image record.')
            prepared = self.prepare(record['course'], record['icon_base64'])
            if prepared['id'] != identifier or record['id'] != identifier:
                raise ValueError('Invalid course image identifier.')
            result[identifier] = prepared
        return result

    def _write(self, records):
        records = self._validated(records)
        self._commit(records)

    def _metadata(self, records):
        public, course_ids = {}, {}
        for identifier, record in records.items():
            if identifier in self._public_records and self.records.get(identifier) == record:
                public[identifier] = self._public_records[identifier]
            else:
                digest = hashlib.sha256(base64.b64decode(record['icon_base64'])).hexdigest()[:16]
                public[identifier] = {'id': identifier, 'course': record['course'],
                                     'icon_url': f'/api/course-images/{identifier}.png?v={digest}'}
            course_ids[_key(record['course'])] = identifier
        return public, course_ids

    def _commit(self, records):
        data = json.dumps(records, ensure_ascii=False).encode('utf-8')
        if len(records) > MAX_RECORDS or len(data) > MAX_LIBRARY_BYTES:
            raise ValueError('Course image library is full; remove unused images first.')
        public, course_ids = self._metadata(records)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.course-images-{uuid4().hex}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.records = records
        self._public_records, self._course_ids = public, course_ids

    def save_prepared(self, record, overwrite=False):
        """Commit prepare() output under the app lock, without image decoding again."""
        if type(overwrite) is not bool:
            raise ValueError('Overwrite must be true or false.')
        identifier = record['id']
        old = self.records.get(identifier)
        if old and old['icon_base64'] != record['icon_base64'] and not overwrite:
            raise ValueError('This course already has an image; explicitly choose overwrite.')
        records = deepcopy(self.records)
        records[identifier] = deepcopy(record)
        self._commit(records)
        return self._public_record(identifier)

    def delete(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier not in self.records:
            raise ValueError('Choose a saved course image to delete.')
        records = deepcopy(self.records)
        del records[identifier]
        self._commit(records)

    def icon(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise KeyError('course image')
        return base64.b64decode(self.records[identifier]['icon_base64'])

    def _public_record(self, identifier):
        return dict(self._public_records[identifier])

    def public(self):
        return [self._public_record(identifier) for identifier in sorted(
            self.records, key=lambda key: self.records[key]['course'].casefold())]

    def icon_url(self, course):
        try:
            identifier = self._course_ids.get(_key(canonical_course(course)))
        except ValueError:
            return None
        return self._public_records[identifier]['icon_url'] if identifier is not None else None

    def pngs_for(self, courses):
        result, decoded = {}, {}
        for course in dict.fromkeys(courses):
            if self.icon_url(course):
                identifier = self._course_ids[_key(canonical_course(course))]
                if identifier not in decoded:
                    decoded[identifier] = self.icon(identifier)
                result[course] = decoded[identifier]
        return result
