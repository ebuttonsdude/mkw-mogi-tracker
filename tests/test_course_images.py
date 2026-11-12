"""Synthetic pixels only: runtime course artwork and its public boundary."""

import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from mkw_tracker.course_images import CourseImageLibrary, course_from_filename
from tests import test_custom_themes
from tests.test_custom_themes import icon


class CourseImageLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = CourseImageLibrary(self.temp.name)

    def test_empty_save_restart_explicit_replace_delete(self):
        self.assertEqual(self.library.public(), [])
        self.assertIsNone(self.library.icon_url('Mario Circuit'))
        record = self.library.prepare('mario_circuit', icon())
        public = self.library.save_prepared(record)
        self.assertEqual(public['course'], 'Mario Circuit')
        self.assertNotIn('base64', json.dumps(public))
        self.assertEqual(self.library.save_prepared(record), public)
        replacement = self.library.prepare('Mario Circuit', icon((0, 0, 0, 255)))
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.library.save_prepared(replacement)
        self.assertEqual(self.library.public(), [public])
        changed = self.library.save_prepared(replacement, overwrite=True)
        self.assertNotEqual(public['icon_url'], changed['icon_url'])
        loaded = CourseImageLibrary(self.temp.name)
        self.assertEqual(loaded.public(), [changed])
        self.assertEqual(loaded.pngs_for(['MARIO CIRCUIT', 'Unknown']),
                         {'MARIO CIRCUIT': loaded.icon(record['id'])})
        loaded.delete(record['id'])
        self.assertEqual(CourseImageLibrary(self.temp.name).public(), [])

    def test_failed_atomic_replace_keeps_last_good_library(self):
        original = self.library.prepare('Mario Circuit', icon())
        public = self.library.save_prepared(original)
        replacement = self.library.prepare('Mario Circuit', icon((1, 2, 3, 255)))
        with patch('pathlib.Path.replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.library.save_prepared(replacement, overwrite=True)
        self.assertEqual(self.library.records, {original['id']: original})
        self.assertEqual(self.library.public(), [public])
        self.assertEqual(CourseImageLibrary(self.temp.name).records, self.library.records)
        self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])

    def test_public_polling_uses_metadata_and_returns_copies(self):
        record = self.library.prepare('Mario Circuit', icon())
        public = self.library.save_prepared(record)
        loaded = CourseImageLibrary(self.temp.name)
        for library in (self.library, loaded):
            with patch.object(library, 'icon', side_effect=AssertionError('decoded PNG')), \
                    patch('mkw_tracker.course_images.base64.b64decode', side_effect=AssertionError('base64 decode')), \
                    patch('mkw_tracker.course_images.hashlib.sha256', side_effect=AssertionError('image hash')):
                for _ in range(10):
                    self.assertEqual(library.icon_url('MARIO CIRCUIT'), public['icon_url'])
                    self.assertEqual(library.public(), [public])
                exposed = library.public()
                exposed[0]['icon_url'] = 'changed'
                self.assertEqual(library.public(), [public])
        with patch.object(loaded, 'icon', wraps=loaded.icon) as read:
            result = loaded.pngs_for(['Mario Circuit'] * 1000)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(set(result), {'Mario Circuit'})
        changed = self.library.save_prepared(
            self.library.prepare('Mario Circuit', icon((0, 0, 0, 255))), overwrite=True)
        self.assertNotEqual(changed['icon_url'], public['icon_url'])
        self.assertEqual(self.library.icon_url('Mario Circuit'), changed['icon_url'])
        self.library._write({record['id']: record})
        self.assertEqual(self.library.public(), [public])
        with patch('mkw_tracker.course_images.base64.b64decode', side_effect=AssertionError('unchanged image decode')):
            self.assertEqual(self.library.save_prepared(record), public)
        self.library.delete(record['id'])
        self.assertIsNone(self.library.icon_url('Mario Circuit'))

    def test_exact_filename_matching_only(self):
        for name in ('Mario Circuit.png', 'course_Mario_Circuit_icon.png',
                     'MKW_Mario_Circuit_course_icon.WEBP', 'track_Mario_Circuit_thumbnail.jpg'):
            self.assertEqual(course_from_filename(name), 'Mario Circuit', name)
        self.assertEqual(course_from_filename('SNES_Mario_Circuit_2.png'), 'SNES Mario Circuit 2')
        for name in ('SNES_Mario_Circuit.png', 'Mario Circut.png', '../Mario Circuit.png',
                     'Mario Circuit.py', 'notknown.png', 'Mario Circuit.png.exe'):
            self.assertIsNone(course_from_filename(name), name)

    def test_unicode_custom_names_and_invalid_records(self):
        result = self.library.prepare('私のコース ☆', icon())
        self.library.save_prepared(result)
        self.assertTrue(self.library.icon_url('私のコース ☆'))
        for course in ('', '\nCourse', 'x' * 161, '\ud800'):
            with self.assertRaises(ValueError):
                self.library.prepare(course, icon())
        for identifier in ('../file', '/etc/passwd', 'a' * 32):
            with self.assertRaises(KeyError):
                self.library.icon(identifier)
        with self.assertRaises(ValueError):
            self.library._write({'a' * 32: result})
        with patch('mkw_tracker.course_images.MAX_RECORDS', 0), self.assertRaises(ValueError):
            self.library._write(self.library.records)
        with patch('mkw_tracker.course_images.MAX_LIBRARY_BYTES', 1), self.assertRaises(ValueError):
            self.library._write(self.library.records)
        self.assertEqual(CourseImageLibrary(self.temp.name).records, self.library.records)

    def test_normalization_removes_metadata_and_limits_dimensions(self):
        output = BytesIO()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('private', 'secret')
        Image.new('RGB', (1024, 800), 'red').save(output, 'PNG', pnginfo=metadata)
        record = self.library.prepare('Mario Circuit', base64.b64encode(output.getvalue()).decode())
        with Image.open(BytesIO(base64.b64decode(record['icon_base64']))) as clean:
            self.assertEqual(clean.size, (512, 400))
            self.assertEqual(clean.info, {})
        for encoded in ('bad!', 'A' * (6 * 1024 * 1024), base64.b64encode(b'<svg/>').decode()):
            with self.assertRaises(ValueError):
                self.library.prepare('Mario Circuit', encoded)
        for images in ([Image.new('RGB', (4097, 1))],
                       [Image.new('RGBA', (8, 8), 'red'), Image.new('RGBA', (8, 8), 'blue')]):
            output = BytesIO()
            images[0].save(output, 'PNG', save_all=True, append_images=images[1:])
            with self.assertRaises(ValueError):
                self.library.prepare('Mario Circuit', base64.b64encode(output.getvalue()).decode())


class CourseImageAPITests(unittest.TestCase):
    setUp = test_custom_themes.ThemeAPITests.setUp
    tearDown = test_custom_themes.ThemeAPITests.tearDown
    request = test_custom_themes.ThemeAPITests.request

    def upload(self, **extra):
        return self.request('/api/course-images', 'POST',
                            {'course': 'Mario Circuit', 'image_base64': icon(), **extra})

    def test_crud_snapshot_and_report_snapshot(self):
        self.assertEqual(self.request('/api/course-images')[1]['images'], [])
        original_prepare = self.app.course_images.prepare
        def prepare(*args):
            self.assertFalse(self.app.lock._is_owned())
            return original_prepare(*args)
        with patch.object(self.app.course_images, 'prepare', side_effect=prepare):
            status, public = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(self.request(public['icon_url'])[0], 200)
        self.assertEqual(self.request('/api/course-images')[1]['images'], [public])
        with self.app.lock:
            self.app.store.create_race('r', 'Mario Circuit')
        state = self.request('/api/state')[1]
        self.assertEqual(state['races'][0]['icon_url'], public['icon_url'])
        self.assertEqual(state['capture']['current_race']['icon_url'], public['icon_url'])
        self.assertNotIn('icon_base64', json.dumps(state))
        folder = state['session']['folder_id']
        with patch('mkw_tracker.server.render_folder_png', return_value=b'PNG') as render:
            self.assertEqual(self.request(f'/api/folders/{folder}/preview.png')[0], 200)
            self.assertEqual(render.call_args.kwargs['course_images'],
                             {'Mario Circuit': self.app.course_images.icon(public['id'])})
        self.assertEqual(self.upload(image_base64=icon((0, 0, 0, 255)))[0], 400)
        self.assertEqual(self.upload(image_base64=icon((0, 0, 0, 255)), overwrite=True)[0], 200)
        self.assertEqual(self.request('/api/course-images/' + public['id'], 'DELETE')[0], 200)
        self.assertEqual(self.request(public['icon_url'])[0], 404)
        self.assertIsNone(self.request('/api/state')[1]['races'][0]['icon_url'])

    def test_repeated_race_snapshot_looks_up_each_course_once(self):
        public = self.upload()[1]
        snapshot = {'races': [{'course': 'Mario Circuit'} for _ in range(1000)],
                    'ww': {'courses': [{'course': 'Mario Circuit'}],
                           'all_courses': [{'course': 'Mario Circuit'}]}}
        with patch.object(self.app.course_images, 'icon_url', wraps=self.app.course_images.icon_url) as lookup, \
                patch.object(self.app.course_images, 'icon', side_effect=AssertionError('decoded PNG')):
            self.app._with_course_images(snapshot)
            self.assertEqual(lookup.call_count, 1)
        self.assertTrue(all(race['icon_url'] == public['icon_url'] for race in snapshot['races']))

    def test_authoritative_resolution_matches_upload_identity(self):
        for name, aliases in (('Mario Circuit', ('mario_circuit', 'MARIO CIRCUIT', 'Mario-Circuit')),
                              ('私のコース ☆', ('私のコース', '私のコース！'))):
            status, saved = self.request('/api/course-images', 'POST',
                                         {'course': name, 'image_base64': icon()})
            self.assertEqual(status, 200)
            with patch('mkw_tracker.course_images._image', side_effect=AssertionError('image decode')):
                for alias in aliases:
                    status, result = self.request('/api/course-images/resolve', 'POST', {'course': alias})
                    self.assertEqual(status, 200)
                    self.assertEqual(result['id'], saved['id'])
                    self.assertTrue(result['exists'])
                    if name == 'Mario Circuit':
                        self.assertEqual(result['course'], name)
        status, fresh = self.request('/api/course-images/resolve', 'POST', {'course': 'Unknown Track'})
        self.assertEqual(status, 200)
        self.assertFalse(fresh['exists'])
        self.assertEqual(fresh['id'], self.app.course_images.prepare('Unknown Track', icon())['id'])
        for body in ({}, {'course': None}, {'course': ''}, {'course': 'A', 'path': '/tmp/x'}):
            self.assertEqual(self.request('/api/course-images/resolve', 'POST', body)[0], 400)
        self.assertEqual(self.request('/api/course-images/resolve', 'POST',
                                     {'course': 'Mario Circuit'}, 'https://foreign.invalid')[0], 403)
        self.assertEqual(self.request('/api/course-images/resolve', 'POST',
                                     {'course': 'Mario Circuit', 'expected_tracker_mode': 'ww'})[0], 400)

    def test_upload_matches_all_legacy_asset_codes(self):
        from tests.test_course_migration import LEGACY_COURSE_FILES
        status, body = self.request('/api/course-images/match', 'POST',
                                    {'filenames': list(LEGACY_COURSE_FILES)})
        self.assertEqual(status, 200)
        self.assertEqual(body['matches'], [{'filename': filename, 'course': course}
                                          for filename, course in LEGACY_COURSE_FILES.items()])

    def test_match_origin_paths_types_and_modes(self):
        response = self.request('/api/course-images/match', 'POST',
                                {'filenames': ['Mario_Circuit.png', 'bad.png']})
        self.assertEqual(response[1]['matches'], [{'filename': 'Mario_Circuit.png', 'course': 'Mario Circuit'},
                                                {'filename': 'bad.png', 'course': None}])
        self.assertEqual(self.upload(overwrite='yes')[0], 400)
        self.assertEqual(self.request('/api/course-images', 'POST',
                         {'course': 'Mario Circuit', 'image_base64': icon()}, 'https://foreign.invalid')[0], 403)
        self.assertEqual(self.request('/api/course-images/../../settings.json')[0], 404)
        self.assertEqual(self.request('/api/course-images/match', 'POST', {'filenames': ['a'] * 257})[0], 400)
        public = self.upload()[1]
        with self.app.lock:
            self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
            self.app.store.create_race('ww-r', 'Mario Circuit')
        self.assertEqual(self.request('/api/state')[1]['races'][0]['icon_url'], public['icon_url'])
        self.assertEqual(self.upload()[0], 400)
        self.assertEqual(self.upload(expected_tracker_mode='ww')[0], 200)
        folder = self.request('/api/state')[1]['session']['folder_id']
        with patch('mkw_tracker.ww_reports.render_ww_folder_png', return_value=b'PNG') as render:
            self.assertEqual(self.request(f'/api/folders/{folder}/preview.png')[0], 200)
            self.assertIn('Mario Circuit', render.call_args.kwargs['course_images'])
        with self.app.lock:
            self.app.store.set_ww_result('ww-r', position=1, vr_before=1000, vr_after=1010)
        state = self.request('/api/state')[1]
        for key in ('courses', 'all_courses'):
            self.assertEqual(state['ww'][key][0]['icon_url'], public['icon_url'])
        with self.app.lock:
            self.app.switch_tracker_mode('ffa', self.app.store.active_session()['id'])
            self.app.store.create_race('ffa-r', 'Mario Circuit')
        self.assertEqual(self.request('/api/state')[1]['races'][0]['icon_url'], public['icon_url'])
        self.assertTrue((Path(self.temp.name) / 'course-images.json').exists())
