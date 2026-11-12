"""Temporary neutral fonts only; no downloads, user preferences or hardware."""
from copy import deepcopy
from http.client import HTTPConnection
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageFont

from mkw_tracker.report_fonts import FontLibrary, draw_text, text_clusters
from mkw_tracker.reports import _font, render_folder_png
from mkw_tracker.server import Application, make_server
from mkw_tracker.settings import DEFAULT_SETTINGS, merge_settings
from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_reports import _wrapped_lines, render_ww_folder_png
from mkw_tracker.ww_store import WWStore


class FontSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'fonts'
        self.directory.mkdir()
        self.latin = self.directory / 'Neutral Latin.ttf'
        self.latin.write_bytes(ImageFont.load_default(size=20).path.getvalue())
        self.system = self.root / 'system'
        self.system.mkdir()
        # Copy a locally installed neutral font; never commit font binaries.
        self.cjk = self.system / 'NotoSansCJK-Regular.ttc'
        self.cjk.write_bytes(Path(_font(ImageFont, 20).path).read_bytes())
        self.library = FontLibrary(self.root, roots=[self.system])

    def test_portable_setting_validates_without_requiring_present_file(self):
        for identifier in ('', self.latin.name, 'missing.otf', 'system-123'):
            with self.subTest(identifier=identifier):
                saved = merge_settings(deepcopy(DEFAULT_SETTINGS), {'report_font': identifier})
                self.assertEqual(saved['report_font'], identifier)
        for identifier in ('../secret', '/etc/passwd', 'C:\\font.ttf', '\\font.ttf', '..', 'x\x00', '\ud800', None, 3):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                merge_settings(DEFAULT_SETTINGS, {'report_font': identifier})

    def test_registry_lists_runtime_basenames_without_paths_and_rejects_escapes(self):
        self.assertIn(self.latin.name, {item['id'] for item in self.library.choices()})
        self.assertNotIn(str(self.root), json.dumps(self.library.browser(self.latin.name)))
        outside = self.root / 'private.ttf'
        outside.write_bytes(self.latin.read_bytes())
        link = self.directory / 'escape.ttf'
        link.symlink_to(outside)
        self.library.refresh()
        for identifier in ('../private.ttf', str(outside), 'escape.ttf'):
            with self.subTest(identifier=identifier), self.assertRaises(KeyError):
                self.library.read(identifier)
        # A formerly registered file cannot become a link after discovery.
        self.latin.unlink()
        self.latin.symlink_to(outside)
        with self.assertRaises(KeyError):
            self.library.read(self.latin.name)
        self.assertTrue(self.library.browser(self.latin.name)['missing'])

    def test_registry_rejects_symlink_directory_and_non_fonts(self):
        self.latin.write_bytes(b'not a font' * 4)
        with self.assertRaises(KeyError):
            self.library.read(self.latin.name)
        self.latin.unlink()
        self.directory.rmdir()
        self.directory.symlink_to(self.system, target_is_directory=True)
        self.assertFalse(FontLibrary(self.root, roots=[]).choices())

    def test_png_selects_primary_then_cjk_fallback_and_preserves_clusters(self):
        font = self.library.font(ImageFont, 20, self.latin.name)
        latin = font._select('A')
        japanese = font._select('日')
        self.assertEqual(latin.getname(), ImageFont.load_default(size=20).getname())
        self.assertNotEqual(latin.getname(), japanese.getname())
        text = 'Latin 日本語 Ω ☆ 葛\U000e0100 ♥\ufe0f A\u200dB 👩\u200d💻 e\u0301 🇯🇵 \U0010ffff'
        runs = list(font.runs(text))
        self.assertEqual(''.join(value for _, value in runs), text)
        self.assertEqual(font.getlength(text), sum(selected.getlength(value) for selected, value in runs))
        clusters = list(text_clusters(text))
        for sequence in ('葛\U000e0100', '♥\ufe0f', 'A\u200dB', '👩\u200d💻', 'e\u0301', '🇯🇵'):
            self.assertIn(sequence, clusters)
            self.assertIn(sequence, _wrapped_lines(sequence, font, 1))
        draw = Mock()
        draw_text(draw, (10, 10), text, font=font, fill='white')
        self.assertEqual(''.join(call.args[1] for call in draw.text.call_args_list), text)
        self.assertTrue(all(call.kwargs['anchor'] == 'ls' for call in draw.text.call_args_list))

    def test_missing_corrupt_or_no_fonts_fall_back_without_losing_text(self):
        for preference in (self.latin.name, 'removed.otf'):
            for data in (b'invalid font data', None):
                if data is None:
                    self.latin.unlink(missing_ok=True)
                else:
                    self.latin.write_bytes(data)
                font = self.library.font(ImageFont, 20, preference)
                self.assertGreater(font.getlength('Player 日本語 ☆ \U0010ffff'), 0)
        empty = FontLibrary(roots=[]).font(ImageFont, 20)
        value = 'Player 日本語 ♥\ufe0f A\u200dB \U0010ffff'
        self.assertEqual(''.join(run for _, run in empty.runs(value)), value)
        image = Image.new('RGB', (500, 50))
        from PIL import ImageDraw
        draw_text(ImageDraw.Draw(image), (0, 0), value, font=empty, fill='white')
        self.assertIsNotNone(image.getbbox())

    def test_mogi_ffa_and_ww_pngs_use_selection_and_keep_unknown_characters(self):
        for mode in ('mogi', 'ffa', 'ww'):
            store = WWStore(':memory:') if mode == 'ww' else TrackerStore(':memory:', default_format=mode if mode == 'ffa' else 'teams')
            try:
                name = 'Player 日本語 ☆ ♥\ufe0f A\u200dB \U0010ffff'
                if mode == 'ww':
                    store.set_player_name(name, store.snapshot()['session']['id'])
                else:
                    store.add_player(name)
                state = store.snapshot()
                renderer = render_ww_folder_png if mode == 'ww' else render_folder_png
                with self.subTest(mode=mode):
                    content = renderer(state, state['session']['folder_id'], font_library=self.library,
                                       report_font=self.latin.name)
                    with Image.open(BytesIO(content)) as image:
                        image.verify()
            finally:
                store.close()

    def test_api_serves_same_font_persists_selection_and_no_paths(self):
        app = Application(self.root / 'scores.sqlite', self.root)
        app.font_library = self.library
        self.addCleanup(app.close)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def close_server():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(close_server)
        def request(path, method='GET', data=None):
            connection = HTTPConnection(*server.server_address, timeout=5)
            try:
                body = json.dumps(data) if data is not None else None
                connection.request(method, path, body, {'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        code, content = request('/api/fonts')
        self.assertEqual(code, 200)
        face = next(font for font in json.loads(content)['fonts'] if font['id'] == self.latin.name)
        self.assertEqual(request(face['url']), (200, self.latin.read_bytes()))
        code, _ = request('/api/settings', 'PATCH', {'report_font': self.latin.name})
        self.assertEqual(code, 200)
        overlay = json.loads(request('/api/overlay')[1])
        self.assertEqual(overlay['font']['faces'][0]['id'], self.latin.name)
        self.assertNotIn(str(self.root), json.dumps(overlay['font']))
        self.assertEqual(json.loads(app.settings_path.read_text())['report_font'], self.latin.name)
        state = app.store.snapshot()
        with patch('mkw_tracker.server.render_folder_png', return_value=b'PNG') as render:
            self.assertEqual(request(f"/api/folders/{state['session']['folder_id']}/report.png"), (200, b'PNG'))
        self.assertEqual(render.call_args.kwargs['report_font'], self.latin.name)
        self.assertIs(render.call_args.kwargs['font_library'], self.library)
        for path in ('/api/fonts/..%2Fprivate.ttf', '/api/fonts/%2Fetc%2Fpasswd', '/api/fonts/missing.ttf'):
            self.assertEqual(request(path)[0], 404)
        app.close()
        reopened = Application(self.root / 'scores.sqlite', self.root)
        try:
            self.assertEqual(reopened.settings['report_font'], self.latin.name)
        finally:
            reopened.close()
