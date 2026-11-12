from contextlib import contextmanager
from http.client import HTTPConnection
from io import BytesIO
import os
from pathlib import Path
from struct import pack
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageFont

from mkw_tracker.report_fonts import draw_text
from mkw_tracker.reports import _check_font_text, _font, _font_codepoints, render_folder_png
from mkw_tracker.server import Application, make_server
from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_reports import render_ww_folder_png
from mkw_tracker.ww_store import WWStore


class LocalFontTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_font = Path(_font(ImageFont, 20).path).read_bytes()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.system = self.root / 'Windows path \u65e5\u672c\u8a9e'
        self.local = self.root / 'Local AppData \u03a9'

    @contextmanager
    def environment(self, platform='win32', **extra):
        rglob = Path.rglob
        environment = {'SystemRoot': str(self.system), 'LOCALAPPDATA': str(self.local),
                       'MKW_REPORT_FONT': '', **extra}
        # Exclude real system fonts while exercising the real discovery paths.
        with patch('mkw_tracker.reports.sys', SimpleNamespace(platform=platform)), \
                patch.dict(os.environ, environment), \
                patch.object(Path, 'home', return_value=self.home), \
                patch.object(Path, 'rglob', autospec=True,
                             side_effect=lambda root, pattern: rglob(root, pattern)
                             if root.is_relative_to(self.root) else iter(())):
            yield

    def install_font(self, path, data=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.source_font if data is None else data)
        return path


class LocalFontDiscoveryTests(LocalFontTestCase):
    def test_windows_system_and_per_user_exact_candidate_paths(self):
        # The fixture bytes are a real Noto font, not licensed Windows font bytes.
        # Each discovered file still undergoes the same loaded-file cmap check.
        for folder in (self.system / 'Fonts', self.local / 'Microsoft/Windows/Fonts'):
            for filename in ('NotoSansCJK-Regular.ttc', 'NotoSansJP-Regular.ttf',
                             'NotoSansCJKjp-Regular.otf', 'TakaoGothic.ttf',
                             'YuGothR.ttc', 'meiryo.ttc', 'msgothic.ttc'):
                with self.subTest(folder=folder, filename=filename):
                    path = self.install_font(folder / filename)
                    try:
                        with self.environment():
                            font = _font(ImageFont, 23)
                        self.assertEqual(Path(font.path), path)
                        self.assertEqual(font.size, 23)
                        _check_font_text(font, 'Latin \u00e9 \u65e5\u672c\u8a9e \u0391\u03bb\u03c6\u03b1 \u2606')
                    finally:
                        path.unlink()

    def test_windows_windir_and_default_per_user_location(self):
        path = self.install_font(self.home / 'AppData/Local/Microsoft/Windows/Fonts/meiryo.ttc')
        with self.environment(SystemRoot='', WINDIR=str(self.system), LOCALAPPDATA=''):
            self.assertEqual(Path(_font(ImageFont, 20).path), path)
        path.unlink()
        path = self.install_font(self.system / 'Fonts/YuGothR.ttc')
        with self.environment(SystemRoot='', WINDIR=str(self.system)):
            self.assertEqual(Path(_font(ImageFont, 20).path), path)

    def test_explicit_path_with_spaces_and_unicode_on_all_platforms(self):
        path = self.install_font(self.root / 'manual fonts \u65e5\u672c\u8a9e/custom font.otf')
        for platform in ('win32', 'linux', 'darwin'):
            with self.subTest(platform=platform), self.environment(platform, MKW_REPORT_FONT=str(path)):
                self.assertEqual(Path(_font(ImageFont, 20).path), path)

    def test_linux_and_macos_manual_noto_directory_still_works(self):
        path = self.install_font(self.home / '.local/share/fonts/NotoSansCJKjp-Regular.otf')
        for platform in ('linux', 'darwin'):
            with self.subTest(platform=platform), self.environment(platform):
                self.assertEqual(Path(_font(ImageFont, 20).path), path)

    def test_invalid_or_latin_only_font_does_not_hide_valid_next_candidate(self):
        first = self.system / 'Fonts/NotoSansCJK-Regular.ttc'
        valid = self.install_font(self.system / 'Fonts/meiryo.ttc')
        for data in (b'not a font', ImageFont.load_default(size=20).path.getvalue()):
            with self.subTest(corrupt=data == b'not a font'):
                self.install_font(first, data)
                with self.environment():
                    self.assertEqual(Path(_font(ImageFont, 20).path), valid)

    def test_explicit_bad_path_does_not_silently_choose_another_font(self):
        self.install_font(self.system / 'Fonts/meiryo.ttc')
        invalid = self.root / 'bad.ttf'
        for data in (None, b'not a font', ImageFont.load_default(size=20).path.getvalue()):
            if data is not None:
                self.install_font(invalid, data)
            with self.subTest(data=data is None), self.environment(MKW_REPORT_FONT=str(invalid)):
                with self.assertRaisesRegex(RuntimeError, 'MKW_REPORT_FONT could not load'):
                    _font(ImageFont, 20)

    def test_missing_font_has_windows_specific_offline_remediation(self):
        with self.environment(), self.assertRaises(RuntimeError) as error:
            _font(ImageFont, 20)
        for text in ('Japanese-capable font', 'Windows', 'Optional features', 'Noto Sans CJK JP',
                     'per-user', 'MKW_REPORT_FONT', 'never downloaded'):
            self.assertIn(text, str(error.exception))

    def test_coverage_is_invalidated_after_font_replacement(self):
        path = self.install_font(self.root / 'font.otf')
        with self.environment(MKW_REPORT_FONT=str(path)):
            _font(ImageFont, 20)
            path.write_bytes(ImageFont.load_default(size=20).path.getvalue())
            with self.assertRaisesRegex(RuntimeError, 'MKW_REPORT_FONT could not load'):
                _font(ImageFont, 20)

    def test_selected_font_cmap_rejects_unmapped_glyphs(self):
        font = _font(ImageFont, 20)
        with self.assertRaisesRegex(RuntimeError, r'U\+10FFFF'):
            _check_font_text(font, '\U0010ffff')
        with self.assertRaisesRegex(RuntimeError, r'U\+10FFFF'):
            _check_font_text(font, 'Mario \U0010ffff')

    def test_variation_selectors_are_not_standalone_glyphs(self):
        font = _font(ImageFont, 20)
        for text in ('葛\U000e0100', '♥\ufe0f', 'A\u200dB'):
            with self.subTest(text=text):
                _check_font_text(font, text)
        with self.assertRaisesRegex(RuntimeError, r'U\+10FFFF'):
            _check_font_text(font, '\U0010ffff\ufe0f')

    def test_cmap_formats_and_zero_glyph_mapping(self):
        # Minimal SFNTs exercise both direct-delta and glyph-array BMP segments,
        # plus full-Unicode ranges whose initial glyph is .notdef (zero).
        bmp = (pack('>7H', 4, 44, 0, 6, 0, 0, 0)
               + pack('>4H', 66, 68, 65535, 0) + pack('>3H', 65, 67, 65535)
               + pack('>3h', -65, 1, 1) + pack('>3H', 0, 4, 0) + pack('>2H', 0, 8))
        full = pack('>HHIII', 12, 0, 28, 0, 1) + pack('>III', 0x1f600, 0x1f602, 0)
        for form, mapping, expected in ((4, bmp, {66, 68}), (12, full, {0x1f601, 0x1f602})):
            cmap = pack('>HHHHI', 0, 1, 3, 10 if form == 12 else 1, 12) + mapping
            data = pack('>I4H', 0x10000, 1, 0, 0, 0) + pack('>4sIII', b'cmap', 0, 28, len(cmap)) + cmap
            path = self.install_font(self.root / f'format{form}.ttf', data)
            with self.subTest(form=form):
                self.assertEqual(_font_codepoints(str(path), 0, 0, len(data)), expected)


class UnicodeReportExportTests(LocalFontTestCase):
    def test_mogi_and_ww_preview_and_export_keep_mixed_script_names(self):
        path = self.install_font(self.system / 'Fonts/meiryo.ttc')
        name = 'Latin \u00e9 \u65e5\u672c\u8a9e \u0391\u03bb\u03c6\u03b1 \u2606 葛\U000e0100 ♥\ufe0f'
        app = Application(':memory:', self.root / 'app')
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with self.environment(), patch('urllib.request.urlopen', side_effect=AssertionError('no font fetching')):
                ids = [app.store.add_player(f'{name} {index}')['id'] for index in range(12)]
                team = app.store.create_team(name)
                for index, identifier in enumerate(ids):
                    app.store.update_player(identifier, f'{name} {index}', team['id'])
                app.store.create_race('mogi', name)
                app.store.submit_result('mogi', ids)
                state = app.store.snapshot()
                folder = state['session']['folder_id']
                for mode in ('mogi', 'ww'):
                    if mode == 'ww':
                        app.switch_tracker_mode('ww', state['session']['id'])
                        ww_session = app.store.snapshot()['session']
                        app.store.set_player_name(name, ww_session['id'])
                        app.store.create_race('ww', name)
                        app.store.set_ww_result('ww', position=3, vr_before=8000, vr_after=8050)
                        folder = ww_session['folder_id']
                    for endpoint in ('preview.png', 'report.png'):
                        with self.subTest(mode=mode, endpoint=endpoint):
                            connection = HTTPConnection(*server.server_address, timeout=5)
                            try:
                                connection.request('GET', f'/api/folders/{folder}/{endpoint}')
                                response = connection.getresponse()
                                content = response.read()
                                self.assertEqual(response.status, 200, content)
                                self.assertEqual(response.getheader('Content-Type'), 'image/png')
                                self.assertEqual('attachment' in (response.getheader('Content-Disposition') or ''),
                                                 endpoint == 'report.png')
                                with Image.open(BytesIO(content)) as image:
                                    image.verify()
                            finally:
                                connection.close()
                self.assertEqual(Path(_font(ImageFont, 20).path), path)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            app.close()

    def test_both_renderers_keep_exporting_with_unavailable_glyphs(self):
        mogi = TrackerStore(':memory:')
        ww = WWStore(':memory:')
        try:
            mogi.add_player('Missing \U0010ffff')
            ww.set_player_name('Missing \U0010ffff', ww.snapshot()['session']['id'])
            for store, render in ((mogi, render_folder_png), (ww, render_ww_folder_png)):
                state = store.snapshot()
                with self.subTest(renderer=render.__name__):
                    content = render(state, state['session']['folder_id'])
                    with Image.open(BytesIO(content)) as image:
                        image.verify()
        finally:
            mogi.close()
            ww.close()


class ReportFontTests(unittest.TestCase):
    def test_no_display_font_is_bundled(self):
        directory = Path(__file__).resolve().parents[1] / 'mkw_tracker/static/fonts'
        self.assertFalse(list(directory.glob('*.otf')))

    def test_neutral_unicode_text_uses_measured_font_directly(self):
        font = _font(ImageFont, 22)
        value = 'Player 日本語 / 123'
        _check_font_text(font, value)
        self.assertGreater(font.getlength(value), 10)
        self.assertGreater(font.getbbox(value)[2], 10)
        draw = Mock()
        draw_text(draw, (20, 40), value, font=font, fill='white')
        draw.text.assert_called_once_with((20, 40), value, font=font, fill='white')

    def test_player_names_and_labels_use_neutral_unicode_font(self):
        store = TrackerStore(':memory:')
        try:
            ids = [store.add_player(f'Racer {index}')['id'] for index in range(12)]
            team = store.create_team('Racing team')
            for player in ids:
                store.update_player(player, next(p['name'] for p in store.snapshot()['players'] if p['id'] == player), team['id'])
            store.create_race('r', 'Rainbow Road')
            store.submit_result('r', ids)
            state = store.snapshot()
            with patch('mkw_tracker.reports._draw_text', autospec=True) as render:
                render_folder_png(state, state['session']['folder_id'])
            player_calls = [call for call in render.call_args_list if call.args[2].startswith('Racer ')]
            self.assertEqual(len(player_calls), 12)
            self.assertTrue(all(call.kwargs['font'].getname()[0] != 'Racers Delight' for call in player_calls))
            score_call = next(call for call in render.call_args_list if call.args[2] == '82 pts')
            self.assertEqual(score_call.kwargs['font'].getname(), player_calls[0].kwargs['font'].getname())
            title_call = next(call for call in render.call_args_list if call.args[2] == 'Cumulative team net points')
            self.assertEqual(title_call.kwargs['font'].getname(), player_calls[0].kwargs['font'].getname())
        finally:
            store.close()
