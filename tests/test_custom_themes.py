"""Local custom themes use generated artwork only, never game assets."""

import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from mkw_tracker.server import Application, make_server
from mkw_tracker.theme_library import ThemeLibrary, suggest_colors
from mkw_tracker.themes import resolve_theme


def icon(color=(220, 50, 80, 255)):
    output = BytesIO()
    Image.new('RGBA', (24, 16), color).save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode('ascii')


def draft(**overrides):
    theme = resolve_theme()
    return {'name': 'Custom 日本', 'tokens': theme['tokens'], 'team_colors': theme['team_colors'], **overrides}


class ThemeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = ThemeLibrary(self.temp.name)

    def test_save_reload_update_and_remove_icon_atomically(self):
        theme = self.library.save(draft(icon_base64=icon()))
        self.assertTrue(theme['custom'])
        self.assertNotIn('icon_base64', theme)
        self.assertNotIn('icon_png', theme)
        self.assertNotIn(self.temp.name, json.dumps(theme))
        identifier = theme['id']
        self.assertTrue(self.library.icon(identifier).startswith(b'\x89PNG'))
        loaded = ThemeLibrary(self.temp.name)
        self.assertEqual(loaded.resolve(identifier), theme)
        updated = loaded.save(draft(id=identifier, name='Updated'))
        self.assertEqual(updated['icon_url'], theme['icon_url'])
        cleared = loaded.save(draft(id=identifier, icon_base64=None))
        self.assertIsNone(cleared['icon_url'])
        with self.assertRaises(KeyError):
            loaded.icon(identifier)
        loaded.delete(identifier)
        self.assertEqual(len(loaded.themes()), 50)

    def test_builtins_and_identifiers_are_not_paths_or_overwritable(self):
        for identifier in ['mario', '../other', '/etc/passwd', 'custom-unknown']:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.library.save(draft(id=identifier))
        self.assertTrue(all(theme['icon_url'] is None for theme in self.library.themes()))

    def test_resolved_renderer_themes_require_complete_safe_shape(self):
        theme = self.library.save(draft(icon_base64=icon()))
        internal = self.library.resolve(theme['id'], internal=True)
        self.assertEqual(resolve_theme(internal), internal)
        self.assertEqual(resolve_theme(resolve_theme('cow')), resolve_theme('cow'))
        for malformed in ({}, {**internal, 'icon_path': '/etc/passwd'},
                          {**internal, 'icon_url': 'https://example.test/icon.png'},
                          {**internal, 'icon_png': b'not PNG'},
                          {**internal, 'tokens': {}}, {**internal, 'team_colors': []}):
            with self.subTest(keys=list(malformed)), self.assertRaises(ValueError):
                resolve_theme(malformed)

    def test_validate_tokens_names_and_palette(self):
        for data in [draft(name=''), draft(name='bad\nname'), draft(tokens={}),
                     draft(team_colors=['#ffffff']), draft(icon_path='/etc/passwd'),
                     draft(tokens={**draft()['tokens'], 'bg': 'url(javascript:alert(1))'})]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.library.save(data)
        self.assertFalse(self.library.path.exists())

    def test_reject_invalid_oversized_svg_and_animation_images(self):
        for encoded in ['not base64!', base64.b64encode(b'<svg></svg>').decode(),
                        'a' * (6 * 1024 * 1024)]:
            with self.subTest(length=len(encoded)), self.assertRaises(ValueError):
                self.library.save(draft(icon_base64=encoded))
        output = BytesIO()
        Image.new('RGB', (4097, 1)).save(output, 'PNG')
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            self.library.save(draft(icon_base64=base64.b64encode(output.getvalue()).decode()))
        output = BytesIO()
        Image.new('RGB', (2, 2), 'red').save(output, 'PNG', save_all=True,
                                           append_images=[Image.new('RGB', (2, 2), 'blue')])
        with self.assertRaisesRegex(ValueError, 'animation'):
            self.library.save(draft(icon_base64=base64.b64encode(output.getvalue()).decode()))

    def test_failed_write_preserves_last_good_records(self):
        original = self.library.save(draft())
        saved = self.library.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.library.save(draft(id=original['id'], name='Uncommitted'))
        self.assertEqual(self.library.path.read_bytes(), saved)
        self.assertEqual(self.library.resolve(original['id']), original)
        self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])

    def test_suggested_accent_foreground_is_readable_on_dark_and_light_images(self):
        self.assertEqual(suggest_colors(icon((0, 0, 0, 255)))['tokens']['accent_text'], '#ffffff')
        self.assertEqual(suggest_colors(icon((255, 255, 255, 255)))['tokens']['accent_text'], '#000000')

    def test_suggestions_are_explicit_nonmutating_and_ignore_transparency(self):
        result = suggest_colors(icon())
        self.assertEqual(result['palette'][0], '#dc3250')
        self.assertEqual(result['tokens']['accent'], '#dc3250')
        self.assertEqual(len(result['team_colors']), 12)
        self.assertFalse(self.library.path.exists())
        with self.assertRaisesRegex(ValueError, 'visible'):
            suggest_colors(icon((0, 0, 0, 0)))


class ThemeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = Application(root / 'scores.sqlite', root)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = Request(self.base + path, method=method, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            with urlopen(request, timeout=10) as response:
                content = response.read()
                return response.status, json.loads(content) if response.headers['Content-Type'].startswith('application/json') else content
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_crud_icon_settings_and_restart(self):
        status, theme = self.request('/api/themes', 'POST', draft(icon_base64=icon()))
        self.assertEqual(status, 200)
        identifier = theme['id']
        self.assertEqual(self.request(theme['icon_url'])[0], 200)
        self.assertEqual(self.request('/api/settings', 'PATCH', {'theme': identifier})[0], 200)
        state = self.request('/api/state')[1]
        self.assertEqual(state['theme'], theme)
        self.assertEqual(state['display_standings']['theme'], theme)
        self.assertEqual(self.request('/api/themes/' + identifier, 'DELETE', {})[0], 400)
        self.app.close()
        root = Path(self.temp.name)
        reopened = Application(root / 'scores.sqlite', root)
        try:
            self.assertEqual(reopened.snapshot()['theme'], theme)
        finally:
            reopened.close()
        self.assertEqual(self.request('/api/settings', 'PATCH', {'theme': 'mario'})[0], 200)
        self.assertEqual(self.request('/api/themes/' + identifier, 'DELETE', {})[0], 200)
        self.assertEqual(self.request(theme['icon_url'])[0], 404)

    def test_origin_unknown_routes_and_suggestion_do_not_mutate(self):
        self.assertEqual(self.request('/api/themes', 'POST', draft(), 'http://evil.invalid')[0], 403)
        self.assertEqual(self.request('/api/themes/../../settings.json')[0], 404)
        result = self.request('/api/themes/suggest', 'POST', {'image_base64': icon()})
        self.assertEqual(result[0], 200)
        self.assertEqual(len(self.request('/api/themes')[1]['themes']), 50)
        self.assertEqual(self.request('/api/themes/suggest', 'POST', {'path': '/etc/passwd'})[0], 400)
