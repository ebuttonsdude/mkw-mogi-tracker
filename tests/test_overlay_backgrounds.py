"""Runtime Lounge uploads use disposable data and synthetic images only."""
import base64
from io import BytesIO
from http.client import HTTPConnection
import json
import random
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mkw_tracker.overlay_backgrounds import OverlayBackgrounds
from mkw_tracker.overlay_profiles import OverlayProfiles
from mkw_tracker.server import Application, make_server
from mkw_tracker.settings import DEFAULT_SETTINGS
from mkw_tracker.themes import resolve_theme

try:
    from PIL import Image, PngImagePlugin
except ImportError:
    Image = None


def encoded_image(color='red', size=(64, 32), format='PNG'):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('private', 'must not survive')
    Image.new('RGB', size, color).save(output, format=format, pnginfo=info)
    return base64.b64encode(output.getvalue()).decode('ascii')


@unittest.skipUnless(Image, 'Pillow not installed')
class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = OverlayProfiles(self.root, resolve_theme)
        self.profiles.create('cards', 'Cards', DEFAULT_SETTINGS)

    def save(self, key='cards', encoded=None):
        png = self.profiles.backgrounds.prepare(encoded or encoded_image())
        return self.profiles.save_background(key, png, self.profiles.background_token(key))

    def test_normalizes_wide_background_and_preserves_existing_icon_size(self):
        from mkw_tracker.theme_library import _image
        encoded = encoded_image(size=(3000, 900))
        png = self.profiles.backgrounds.prepare(encoded)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (1920, 576))
            self.assertEqual(image.info, {})
            self.assertEqual(image.mode, 'RGBA')
        with Image.open(BytesIO(_image(encoded)[0])) as icon:
            self.assertEqual(icon.size, (512, 154))
        for format in ('JPEG', 'WEBP'):
            with self.subTest(format=format):
                self.assertTrue(self.profiles.backgrounds.prepare(encoded_image(format=format)).startswith(b'\x89PNG'))

    def test_persistence_public_url_only_independent_presets_and_source_isolation(self):
        saved = self.save()
        self.assertRegex(saved['image_url'], r'^/api/overlay-profiles/cards/background.png\?v=[a-f0-9]{16}$')
        self.assertIsNone(self.profiles.appearance('default')['image_url'])
        self.assertEqual(self.profiles.get('cards')['lounge']['appearance']['background'], 'aurora')
        for background in ('custom', 'hud', 'carbon'):
            self.profiles.update('cards', {'lounge': {'appearance': {'background': background, 'border': 'hud'}}})
            self.assertEqual(self.profiles.appearance('cards')['image_url'], saved['image_url'])
        restored = OverlayProfiles(self.root, resolve_theme)
        self.assertEqual(restored.appearance('cards')['image_url'], saved['image_url'])
        public = json.dumps(restored.public(DEFAULT_SETTINGS))
        self.assertNotIn('image_base64', public)
        self.assertNotIn(base64.b64encode(restored.backgrounds.image('cards')).decode(), public)
        with self.assertRaises(ValueError):
            restored.update('cards', {'lounge': {'appearance': {'image_url': 'https://example.org/a.png'}}})
        restored.remove_background('cards')
        self.assertIsNone(OverlayProfiles(self.root, resolve_theme).appearance('cards')['image_url'])

    def test_normalized_size_cap_and_cached_public_urls(self):
        with patch('mkw_tracker.overlay_backgrounds._image', return_value=(b'x' * 17, None)), \
                patch('mkw_tracker.overlay_backgrounds.MAX_IMAGE_BYTES', 16), \
                self.assertRaisesRegex(ValueError, 'Normalized background'):
            self.profiles.backgrounds.prepare('unused')
        self.save()
        with patch('mkw_tracker.overlay_backgrounds.hashlib.sha256', side_effect=AssertionError('poll must not hash pixels')):
            for _ in range(3):
                self.profiles.public(DEFAULT_SETTINGS)
                self.profiles.appearance('cards')

    def test_bad_inputs_and_failed_commit_retain_last_good(self):
        self.save()
        before = self.profiles.backgrounds.path.read_bytes()
        invalid = ('', '!!!', base64.b64encode(b'<svg/>').decode(), base64.b64encode(b'not image').decode(),
                   encoded_image(size=(4097, 1)), 'a' * (6 * 1024 * 1024))
        for value in invalid:
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                self.profiles.backgrounds.prepare(value)
            self.assertEqual(self.profiles.backgrounds.path.read_bytes(), before)
        animation = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(animation, format='PNG', save_all=True,
            append_images=[Image.new('RGB', (8, 8), 'blue')], duration=100, loop=0)
        with self.assertRaisesRegex(ValueError, 'animation'):
            self.profiles.backgrounds.prepare(base64.b64encode(animation.getvalue()).decode())
        with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')), self.assertRaises(OSError):
            self.save(encoded=encoded_image('blue'))
        self.assertEqual(self.profiles.backgrounds.path.read_bytes(), before)
        self.assertEqual(self.profiles.backgrounds.image('cards'), OverlayProfiles(self.root, resolve_theme).backgrounds.image('cards'))
        self.assertFalse(list(self.root.glob('.overlay-backgrounds-*.tmp')))
        with patch('mkw_tracker.overlay_backgrounds.MAX_LIBRARY_BYTES', 1), self.assertRaisesRegex(ValueError, 'full'):
            self.save(encoded=encoded_image('blue'))
        self.assertEqual(self.profiles.backgrounds.path.read_bytes(), before)

    def test_delete_recreate_and_newer_background_operations_fence_upload(self):
        self.save()
        pending = self.profiles.background_token('cards')
        png = self.profiles.backgrounds.prepare(encoded_image('blue'))
        self.profiles.remove_background('cards')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.profiles.save_background('cards', png, pending)
        pending = self.profiles.background_token('cards')
        self.save()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.profiles.save_background('cards', png, pending)
        self.profiles.delete('cards')
        self.assertNotIn('cards', self.profiles.backgrounds.images)
        self.profiles.create('cards', 'New cards', DEFAULT_SETTINGS)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.profiles.save_background('cards', png, pending)
        self.assertIsNone(self.profiles.appearance('cards')['image_url'])

    def test_cleanup_failure_never_exposes_old_image_to_recreated_source(self):
        self.save()
        original = self.profiles.backgrounds._commit
        with patch.object(self.profiles.backgrounds, '_commit', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.profiles.delete('cards')
            self.assertNotIn('cards', self.profiles.profiles)
            with self.assertRaises(OSError):
                self.profiles.create('cards', 'New cards', DEFAULT_SETTINGS)
        self.assertNotIn('cards', self.profiles.profiles)
        self.profiles.backgrounds._commit = original
        self.profiles.create('cards', 'New cards', DEFAULT_SETTINGS)
        self.assertIsNone(self.profiles.appearance('cards')['image_url'])

    def test_invalid_saved_library_is_rejected(self):
        path = self.root / 'overlay-backgrounds.json'
        for records in ([], {'../x': encoded_image()}, {'cards': 'invalid'}):
            path.write_text(json.dumps(records))
            with self.assertRaises(ValueError):
                OverlayBackgrounds(self.root)
        path.unlink()
        target = self.root / 'elsewhere.json'
        target.write_text('{}')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            OverlayBackgrounds(self.root)


@unittest.skipUnless(Image, 'Pillow not installed')
class BackgroundApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'scores.sqlite', self.root)
        self.app.overlay_profiles.create('cards', 'Cards', self.app.settings)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.path = '/api/overlay-profiles/cards/background'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.app.close()
        self.temporary.cleanup()

    def request(self, path, method='GET', data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=body, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_upload_replace_remove_and_snapshot_contract(self):
        status, result = self.request(self.path, 'POST', {'image_base64': encoded_image()})
        self.assertEqual(status, 200, result)
        self.assertTrue(self.request(result['image_url'])[1].startswith(b'\x89PNG'))
        self.assertEqual(self.request('/api/overlay/cards')[1]['lounge']['appearance']['image_url'], result['image_url'])
        self.assertIsNone(self.request('/api/overlay')[1]['lounge']['appearance']['image_url'])
        public = self.request('/api/overlay-profiles')[1]
        self.assertEqual(public['profiles'][1]['lounge']['appearance']['image_url'], result['image_url'])
        replacement = self.request(self.path, 'POST', {'image_base64': encoded_image('blue')})[1]
        self.assertNotEqual(replacement['image_url'], result['image_url'])
        self.assertEqual(self.request(self.path, 'DELETE', {})[0], 200)
        self.assertEqual(self.request(result['image_url'])[0], 404)
        self.assertIsNone(self.request('/api/overlay/cards')[1]['lounge']['appearance']['image_url'])

    def test_upload_body_allowance_is_larger_than_normal_json_routes(self):
        pixels = random.Random(42).randbytes(384 * 384 * 3)
        image = Image.frombytes('RGB', (384, 384), pixels)
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode()
        self.assertGreater(len(encoded), 262144)
        status, result = self.request(self.path, 'POST', {'image_base64': encoded})
        self.assertEqual(status, 200, result)
        self.assertTrue(self.request(result['image_url'])[1].startswith(b'\x89PNG'))

    def test_bad_origin_path_mode_schema_and_body_limits(self):
        data = {'image_base64': encoded_image()}
        self.assertEqual(self.request(self.path, 'POST', data, {'Origin': 'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request(self.path, 'POST', dict(data, expected_tracker_mode='ww'))[0], 400)
        self.assertEqual(self.request(self.path, 'POST', dict(data, path='/tmp/file'))[0], 400)
        self.assertEqual(self.request(self.path, 'PATCH', data)[0], 400)
        self.assertEqual(self.request('/api/overlay-profiles/missing/background', 'POST', data)[0], 404)
        for path, method, length in ((self.path, 'POST', 6 * 1024 * 1024 + 1),
                                     ('/api/overlay-profiles/cards', 'PATCH', 262145)):
            # Send oversized headers without transmitting a body the server rejects unread.
            connection = HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=3)
            try:
                connection.putrequest(method, path)
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', str(length))
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                response.close()
            finally:
                connection.close()
        self.assertEqual(self.request('/api/overlay-profiles/cards', 'PATCH', {'lounge': {'appearance': {'image_url': '/tmp/x'}}})[0], 400)
        self.assertFalse(self.app.overlay_profiles.backgrounds.images)

    def test_decode_outside_lock_rechecks_source_identity_and_mode(self):
        prepare = self.app.overlay_profiles.backgrounds.prepare
        def recreate(encoded):
            self.assertFalse(self.app.lock._is_owned())
            with self.app.lock:
                self.app.overlay_profiles.delete('cards')
                self.app.overlay_profiles.create('cards', 'Replacement', self.app.settings)
            return prepare(encoded)
        with patch.object(self.app.overlay_profiles.backgrounds, 'prepare', side_effect=recreate):
            self.assertEqual(self.request(self.path, 'POST', {'image_base64': encoded_image()})[0], 400)
        self.assertIsNone(self.app.overlay_profiles.appearance('cards')['image_url'])
        def switch(encoded):
            with self.app.lock:
                self.app.tracker_mode = 'ww'
            return prepare(encoded)
        with patch.object(self.app.overlay_profiles.backgrounds, 'prepare', side_effect=switch):
            self.assertEqual(self.request(self.path, 'POST', {'image_base64': encoded_image()})[0], 400)
        self.app.tracker_mode = 'mogi'
        self.assertIsNone(self.app.overlay_profiles.appearance('cards')['image_url'])


if __name__ == '__main__':
    unittest.main()
