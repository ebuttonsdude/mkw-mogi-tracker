"""Runtime built-in overrides use generated icons and temporary application data."""
import unittest

from mkw_tracker.theme_library import ThemeLibrary
from mkw_tracker.themes import resolve_theme
from tests import test_custom_themes as fixtures


class ThemeOverrideAPITests(unittest.TestCase):
    setUp = fixtures.ThemeAPITests.setUp
    tearDown = fixtures.ThemeAPITests.tearDown
    request = fixtures.ThemeAPITests.request

    def seed_override(self):
        record = self.app.theme_library.prepare(fixtures.draft(name='Mario', icon_base64=fixtures.icon()))
        record['id'] = 'mario'
        self.app.theme_library._write({'mario': record})

    def test_effective_builtin_icon_overlay_and_png_use_runtime_override(self):
        self.seed_override()
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        theme = state['theme']
        self.assertTrue(theme['overridden'])
        self.assertTrue(theme['custom'])
        self.assertEqual(theme['id'], 'mario')
        self.assertEqual(len(state['themes']), 50)
        self.assertEqual(sum(t['id'] == 'mario' for t in state['themes']), 1)
        self.assertEqual(state['display_standings']['theme'], theme)
        status, data = self.request(theme['icon_url'])
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b'\x89PNG'))
        status, data = self.request('/api/folders/' + state['session']['folder_id'] + '/report.png')
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_edit_and_restore_active_builtin_keeps_selection_and_sessions(self):
        self.seed_override()
        before = self.request('/api/state')[1]['sessions']
        status, theme = self.request('/api/themes', 'POST', fixtures.draft(id='mario', name='Mario', icon_base64=fixtures.icon()))
        self.assertEqual(status, 200)
        self.assertTrue(theme['overridden'])
        self.assertTrue(ThemeLibrary(self.temp.name).resolve('mario')['overridden'])
        self.assertEqual(self.request('/api/themes/mario', 'DELETE', {})[0], 200)
        state = self.request('/api/state')[1]
        self.assertEqual(state['settings']['theme'], 'mario')
        self.assertEqual(state['theme'], resolve_theme('mario'))
        self.assertEqual(state['sessions'], before)
        self.assertEqual(self.request(theme['icon_url'])[0], 404)
        self.assertEqual(len(state['themes']), 50)

    def test_unoverridden_builtin_and_bad_routes_cannot_be_mutated(self):
        self.assertEqual(self.request('/api/themes', 'POST', fixtures.draft(id='mario'))[0], 400)
        self.assertEqual(self.request('/api/themes/mario', 'DELETE', {})[0], 400)
        self.assertEqual(self.request('/api/themes/mario/icon.png')[0], 404)
        self.assertEqual(self.request('/api/themes/../../settings.json')[0], 404)

    def test_renderer_override_metadata_is_strict(self):
        self.seed_override()
        theme = self.app.theme_library.resolve('mario', internal=True)
        self.assertEqual(resolve_theme(theme), theme)
        for updates in ({'overridden': 1}, {'overridden': False}, {'custom': False},
                        {'id': 'custom-' + 'a' * 32}, {'icon_url': 'https://invalid.test/icon.png'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                resolve_theme(theme | updates)
