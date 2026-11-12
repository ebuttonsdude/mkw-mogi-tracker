"""Course migrations use generated pixels and disposable directories only."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.course_images import CourseImageLibrary, course_from_filename
from mkw_tracker.courses import COURSES
from mkw_tracker.migrate import inspect_source, migrate, _interactive_resolver


# Legacy track_icons.py associations; text only, never upstream artwork.
LEGACY_COURSE_FILES = dict(line.split("|") for line in """track_011MBC.png|Mario Bros. Circuit
track_012CC.png|Crown City
track_013WS.png|Whistlestop Summit
track_014DKS.png|DK Spaceport
track_021rDH.png|Desert Hills
track_022rSGB.png|Shy Guy Bazaar
track_023rWS.png|Wario Stadium
track_024rAF.png|Airship Fortress
track_031rDKP.png|DK Pass
track_032SP.png|Starview Peak
track_033rSHS.png|Sky-High Sundae
track_034rWSh.png|Wario's Shipyard
track_041rKTB.png|Koopa Troopa Beach
track_042FO.png|Faraway Oasis
track_051rPB.png|Peach Beach
track_052SSS.png|Salty Salty Speedway
track_053rDDJ.png|Dino Dino Jungle
track_054GBR.png|Great ? Block Ruins
track_061CCF.png|Cheep Cheep Falls
track_062DD.png|Dandelion Depths
track_063BCi.png|Boo Cinema
track_064DBB.png|Dry Bones Burnout
track_071rMMM.png|Moo Moo Meadows
track_072rCM.png|Choco Mountain
track_073rTF.png|Toad's Factory
track_074BC.png|Bowser's Castle
track_081AH.png|Acorn Heights
track_082rMC.png|Mario Circuit
track_083PS.png|Peach Stadium
track_084RR.png|Rainbow Road
track_rCI1.png|SNES Choco Island 1
track_rCI2.png|SNES Choco Island 2
track_rGV1.png|SNES Ghost Valley 1
track_rGV2.png|SNES Ghost Valley 2
track_rGV3.png|SNES Ghost Valley 3
track_rKB1.png|SNES Koopa Beach 1
track_rMC1.png|SNES Mario Circuit 1
track_rMC2.png|SNES Mario Circuit 2
track_rMC3.png|SNES Mario Circuit 3
track_rVL1.png|SNES Vanilla Lake 1""".splitlines())


def png(color='blue'):
    from PIL import Image
    stream = BytesIO()
    Image.new('RGB', (4, 4), color).save(stream, 'PNG')
    return stream.getvalue()


class CourseMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source, self.target = root / 'source', root / 'target'
        self.data = self.source / '.local/tracker'
        self.static = self.source / 'mkw_tracker/static/track-icons'
        self.data.mkdir(parents=True)
        self.static.mkdir(parents=True)

    def run_migration(self, **kwargs):
        return migrate(self.source, self.target, categories={'pictures'}, apply=True, **kwargs)

    def saved(self, directory, course='Mario Circuit', color='red'):
        library = CourseImageLibrary(directory)
        record = library.prepare(course, base64.b64encode(png(color)).decode())
        library._write(library.records | {record['id']: record})
        return library

    def test_all_catalog_images_activate_without_sessions_and_rerun_is_idempotent(self):
        for course in COURSES:
            (self.static / f'MKW_Track_{course}_icon.png').write_bytes(png())
        before = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        inventory = inspect_source(self.source)['categories']['pictures']
        self.assertEqual(inventory['count'], 40)
        result = self.run_migration()
        self.assertEqual(result['course_images'], 40)
        self.assertEqual(len(CourseImageLibrary(self.target).records), 40)
        self.assertFalse(list(self.target.rglob('tracker.sqlite3')))
        self.assertEqual(self.run_migration(conflict='skip')['course_images'], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()})

    def test_legacy_filename_associations_are_exact_and_complete(self):
        self.assertEqual(len(LEGACY_COURSE_FILES), 40)
        self.assertEqual(set(LEGACY_COURSE_FILES.values()), set(COURSES))
        for filename, course in LEGACY_COURSE_FILES.items():
            with self.subTest(filename=filename):
                self.assertEqual(course_from_filename(filename), course)
                self.assertEqual(course_from_filename(filename.upper()), course)
        for filename in ('track_011.png', '011MBC.png', 'track_rMC.png', 'track_rMC4.png',
                         'track_999MBC.png', 'track_011MBC-copy.png', '../track_011MBC.png'):
            with self.subTest(filename=filename):
                self.assertIsNone(course_from_filename(filename))

    def test_legacy_files_repair_all_associations_with_raw_conflicts_skipped(self):
        raw = self.target / 'imported-assets/track-icons'
        raw.mkdir(parents=True)
        original = png()
        for filename in LEGACY_COURSE_FILES:
            (self.static / filename).write_bytes(original)
            (raw / filename).write_bytes(original)
        before = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        preview = migrate(self.source, self.target, categories={'pictures'}, conflict='skip')
        self.assertEqual(preview['course_images'], 40)
        self.assertFalse((self.target / 'course-images.json').exists())
        result = self.run_migration(conflict='skip', theme_conflict='skip')
        self.assertEqual(result['course_images'], 40)
        self.assertEqual(result['sessions'], [])
        self.assertEqual(result['warnings'], [])
        library = CourseImageLibrary(self.target)
        self.assertEqual({record['course'] for record in library.public()}, set(COURSES))
        for course in COURSES:
            self.assertTrue(library.icon_url(course))
        self.assertEqual(self.run_migration(conflict='skip')['course_images'], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.assertTrue(all((raw / name).read_bytes() == original for name in LEGACY_COURSE_FILES))
        self.assertFalse(list(self.target.rglob('tracker.sqlite3')))

    def test_retained_legacy_files_activate_without_static_originals(self):
        retained = self.data / 'imported-assets/track-icons'
        retained.mkdir(parents=True)
        for filename in LEGACY_COURSE_FILES:
            (retained / filename).write_bytes(png())
        self.assertEqual(self.run_migration(conflict='skip')['course_images'], 40)
        self.assertEqual(len(CourseImageLibrary(self.target).public()), 40)

    def test_unknown_ambiguous_invalid_and_oversize_are_retained(self):
        files = {'Mystery.png': png(), 'SNES_Mario_Circuit.png': png(),
                 'Mario_Circuit.png': b'invalid', 'DK_Pass.png': b'x' * (4 * 1024 * 1024 + 1)}
        for name, data in files.items():
            (self.static / name).write_bytes(data)
        result = self.run_migration()
        self.assertEqual(result['course_images'], 0)
        self.assertEqual(len(result['warnings']), 4)
        for name, data in files.items():
            self.assertEqual((self.target / 'imported-assets/track-icons' / name).read_bytes(), data)

    def test_runtime_then_static_then_retained_priority_and_inventory(self):
        self.saved(self.data)
        (self.static / 'Mario_Circuit.png').write_bytes(png('blue'))
        retained = self.data / 'imported-assets/track-icons'
        retained.mkdir(parents=True)
        (retained / 'Mario_Circuit.png').write_bytes(png('green'))
        (retained / 'DK_Pass.png').write_bytes(png())
        self.assertEqual(inspect_source(self.source)['categories']['pictures']['count'], 4)
        result = self.run_migration()
        self.assertEqual(result['course_images'], 2)
        expected = CourseImageLibrary(self.data).records
        actual = CourseImageLibrary(self.target).records
        for identifier, record in expected.items():
            self.assertEqual(actual[identifier], record)
        self.assertTrue((self.target / 'imported-assets/course-images.json').exists())

    def test_conflicts_require_choice_and_scoped_overwrite_wins_over_raw_skip(self):
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        library = self.saved(self.target)
        before = library.path.read_bytes()
        raw = self.target / 'imported-assets/track-icons/Mario_Circuit.png'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(png('red'))
        with self.assertRaisesRegex(ValueError, 'Unresolved conflict'):
            self.run_migration()
        self.assertEqual(self.run_migration(conflict='skip')['course_images'], 0)
        self.assertEqual(library.path.read_bytes(), before)
        result = self.run_migration(conflict='skip', course_image_conflict='overwrite')
        self.assertEqual(result['course_images'], 1)
        self.assertEqual(raw.read_bytes(), png('red'))
        self.assertNotEqual(library.path.read_bytes(), before)

    def test_rename_preserves_existing_association_and_warns(self):
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        library = self.saved(self.target)
        before = library.path.read_bytes()
        raw = self.target / 'imported-assets/track-icons/Mario_Circuit.png'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(png('red'))
        result = self.run_migration(conflict='rename')
        self.assertEqual(result['course_images'], 0)
        self.assertEqual(library.path.read_bytes(), before)
        self.assertTrue(any('cannot be renamed' in warning for warning in result['warnings']))
        self.assertEqual(len(list(raw.parent.glob('*.png'))), 2)

    def test_raw_reuse_does_not_prevent_missing_association_repair(self):
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        raw = self.target / 'imported-assets/track-icons/Mario_Circuit.png'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(png())
        self.assertEqual(self.run_migration(conflict='skip')['course_images'], 1)

    def test_display_limits_and_bad_json_preserve_originals(self):
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        source_json = self.data / 'course-images.json'
        source_json.write_text('{bad json')
        with patch('mkw_tracker.course_images.MAX_RECORDS', 0):
            result = self.run_migration()
        self.assertEqual(result['course_images'], 0)
        self.assertEqual((self.target / 'imported-assets/course-images.json').read_bytes(), source_json.read_bytes())
        self.assertEqual(len(result['warnings']), 2)

    def test_failure_restores_overwritten_assets_libraries_and_source_bytes(self):
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        (self.data / 'settings.json').write_text(json.dumps({'theme': 'mario'}))
        library = self.saved(self.target)
        before_library = library.path.read_bytes()
        raw = self.target / 'imported-assets/track-icons/Mario_Circuit.png'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(png('red'))
        before = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        original = CourseImageLibrary._write

        def fail(instance, records):
            original(instance, records)
            raise OSError('synthetic post-write failure')

        with patch.object(CourseImageLibrary, '_write', fail), self.assertRaises(OSError):
            self.run_migration(conflict='overwrite')
        self.assertEqual(library.path.read_bytes(), before_library)
        self.assertEqual(raw.read_bytes(), png('red'))
        self.assertEqual(before, {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.assertFalse(list(self.target.glob('.migration-*')))

    def test_course_display_quota_does_not_abort_selected_sessions(self):
        from tests.test_transfers import populate
        store = populate(self.data)
        store.close()
        (self.static / 'Mario_Circuit.png').write_bytes(png())
        with patch('mkw_tracker.course_images.MAX_RECORDS', 0):
            result = migrate(self.source, self.target, categories={'sessions', 'pictures'}, apply=True)
        self.assertEqual(result['course_images'], 0)
        self.assertTrue(result['sessions'])
        self.assertTrue((self.target / 'tracker.sqlite3').exists())
        self.assertTrue((self.target / 'imported-assets/track-icons/Mario_Circuit.png').exists())

    def test_explicit_custom_course_case_variant_still_requires_overwrite(self):
        self.saved(self.data, course='Custom route', color='blue')
        library = self.saved(self.target, course='CUSTOM ROUTE', color='red')
        before = library.path.read_bytes()
        result = self.run_migration(conflict='skip')
        self.assertEqual(result['course_images'], 0)
        self.assertTrue(any(item['kind'] == 'course-image' for item in result['conflicts']))
        self.assertEqual(library.path.read_bytes(), before)

    def test_interactive_theme_overwrite_all_does_not_overwrite_courses(self):
        resolver = _interactive_resolver()
        with patch('builtins.input', side_effect=['w', 's']):
            self.assertEqual(resolver({'kind': 'theme', 'name': 'Mario', 'can_overwrite': True})[0], 'overwrite')
            self.assertEqual(resolver({'kind': 'course-image', 'name': 'Mario Circuit', 'can_overwrite': True})[0], 'skip')
