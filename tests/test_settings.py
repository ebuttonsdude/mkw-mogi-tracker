from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.server import Application
from mkw_tracker.settings import DEFAULT_SETTINGS, merge_settings
from mkw_tracker.themes import all_themes, resolve_theme, team_color_map


class ThemeSettingsTests(unittest.TestCase):
    def test_ww_overlay_toggles_are_independent_booleans_and_persist(self):
        keys = ('ww_show_graph', 'ww_show_current', 'ww_show_peak', 'ww_show_lowest',
                'ww_show_last_10', 'ww_show_character')
        for key in keys:
            self.assertTrue(DEFAULT_SETTINGS['overlay'][key])
            for value in (None, 0, 1, 'false', [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    merge_settings(DEFAULT_SETTINGS, {'overlay': {key: value}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / 'store.sqlite', root)
            try:
                original_layout = app.settings['overlay']['layout']
                app.update_settings({'overlay': {'ww_show_graph': False, 'ww_show_lowest': False,
                                                  'ww_show_last_10': False, 'ww_show_character': False}})
                self.assertTrue(app.settings['overlay']['ww_show_current'])
                self.assertTrue(app.settings['overlay']['ww_show_peak'])
                self.assertTrue(app.settings['overlay']['show_header'])
                app.update_settings({'overlay': {'show_header': False, 'ww_show_character': True}})
                self.assertTrue(app.settings['overlay']['ww_show_character'])
                self.assertFalse(app.settings['overlay']['show_header'])
                app.update_settings({'overlay': {'show_header': True, 'ww_show_character': False}})
                self.assertEqual(app.settings['overlay']['layout'], original_layout)
            finally:
                app.close()
            app = Application(root / 'store.sqlite', root)
            try:
                self.assertFalse(app.settings['overlay']['ww_show_graph'])
                self.assertFalse(app.settings['overlay']['ww_show_lowest'])
                self.assertFalse(app.settings['overlay']['ww_show_last_10'])
                self.assertFalse(app.settings['overlay']['ww_show_character'])
                self.assertTrue(app.settings['overlay']['show_header'])
                self.assertTrue(app.settings['overlay']['ww_show_current'])
            finally:
                app.close()

    def test_all_fifty_themes_are_ordered_unique_and_safe(self):
        themes = all_themes()
        self.assertEqual(len(themes), 50)
        self.assertEqual((themes[0]['name'], themes[-1]['name']), ('Mario', "Chargin' Chuck"))
        self.assertEqual(len({t['id'] for t in themes}), 50)
        self.assertEqual(len({t['tokens']['accent'] for t in themes}), 50)
        for theme in themes:
            self.assertRegex(theme['id'], r'^[a-z]+(?:-[a-z]+)*$')
            self.assertEqual(len(theme['team_colors']), 12)
            self.assertEqual(len(set(theme['team_colors'])), 12)
            for color in [*theme['tokens'].values(), *theme['team_colors']]:
                self.assertIsNotNone(re.fullmatch(r'#[0-9a-f]{6}', color))
        themes[0]['tokens']['accent'] = 'unsafe'
        self.assertNotEqual(resolve_theme()['tokens']['accent'], 'unsafe')
        for invalid in ('unknown', '<script>', None, {}, []):
            with self.assertRaises(ValueError):
                resolve_theme(invalid)

    def test_partial_merges_are_independent_and_do_not_mutate_input(self):
        before = deepcopy(DEFAULT_SETTINGS)
        settings = merge_settings(before, {'theme': 'cow', 'overlay': {'width': 800, 'show_gaps': False}})
        self.assertEqual(before, DEFAULT_SETTINGS)
        self.assertEqual(settings['standings_format'], 'txt')
        self.assertEqual(settings['overlay']['height'], 600)
        settings = merge_settings(settings, {'standings_format': 'md'})
        self.assertEqual(settings['theme'], 'cow')
        self.assertFalse(settings['overlay']['show_gaps'])
        self.assertEqual(settings['overlay']['width'], 800)

    def test_theme_text_and_controls_have_aa_contrast(self):
        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
            return sum(v * factor for v, factor in zip(linear, (.2126, .7152, .0722)))
        def contrast(first, second):
            a, b = sorted((luminance(first), luminance(second)))
            return (b + .05) / (a + .05)
        for theme in all_themes():
            tokens = theme['tokens']
            for foreground in ('text', 'muted', 'accent'):
                for background in ('bg', 'panel', 'input'):
                    self.assertGreaterEqual(contrast(tokens[foreground], tokens[background]), 4.5,
                                            (theme['name'], foreground, background))
            self.assertGreaterEqual(contrast(tokens['accent_text'], tokens['accent']), 4.5, theme['name'])

    def test_team_colors_do_not_follow_rank_and_include_unscored_archived_roster(self):
        snapshot = {'teams': [{'id': 'b', 'name': 'B', 'session_id': 's', 'points': 0},
                              {'id': 'a', 'name': 'A', 'session_id': 's', 'points': 100}],
                    'archived_teams': [{'id': 'z', 'name': 'Z', 'session_id': 's'},
                                       {'id': 'other', 'name': '0', 'session_id': 'outside'}]}
        theme = resolve_theme('peach')
        before = team_color_map(snapshot, 's', theme)
        self.assertEqual(before['a'], theme['team_colors'][0])
        self.assertEqual(before['b'], theme['team_colors'][1])
        self.assertEqual(before['z'], theme['team_colors'][2])
        self.assertEqual(before[None], theme['team_colors'][3])
        snapshot['teams'].reverse()
        snapshot['teams'][0]['points'] = 1000
        self.assertEqual(team_color_map(snapshot, 's', theme), before)
        self.assertNotIn('other', before)

    def test_settings_validation_rejects_unknown_keys_wrong_types_and_ranges(self):
        bad = [{}, None, [], {'other': 1}, {'theme': 'unknown'}, {'standings_format': 'browser'},
               {'overlay': {}}, {'overlay': []}, {'overlay': {'other': True}}]
        for key, values in {
            'width': (True, 239, 1921, 500.5, '560'), 'height': (39, 2161, None),
            'font_size': (11, 49, True), 'scroll_speed': (4, 121, 1.5),
            'opacity': (-.01, 1.01, float('nan'), float('inf'), True, 10 ** 400),
            'layout': ('unknown', [], None), 'title': ('x' * 101, 'bad\nline', 3),
            'show_header': (0, 'true', None), 'show_players': (1,), 'show_gaps': ('false',),
            'animate': ([],), 'scroll': ({},),
            'highlight_team_id': (True, 0, '', 'x' * 201, 'line\nbreak'),
            'alternating_backgrounds': (0, 'yes', None),
            'slide_direction': ('up', [], None),
            'slide_duration': (-1, 5001, True, 1.5, '400'),
        }.items():
            bad.extend({'overlay': {key: value}} for value in values)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_settings(DEFAULT_SETTINGS, value)

    def test_settings_reject_unpaired_surrogates_without_mutation(self):
        before = deepcopy(DEFAULT_SETTINGS)
        for key in ('title', 'highlight_team_id'):
            for invalid in ('\ud800', '\udfff', 'text\ud800end'):
                with self.subTest(key=key, invalid=repr(invalid)), self.assertRaises(ValueError):
                    merge_settings(before, {'overlay': {key: invalid}})
        self.assertEqual(before, DEFAULT_SETTINGS)

    def test_invalid_unicode_does_not_poison_saved_settings_or_public_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / 'store.sqlite', root)
            try:
                app.update_settings({'overlay': {'title': '選手 🏁'}})
                saved = deepcopy(app.settings)
                disk = app.settings_path.read_bytes()
                with self.assertRaises(ValueError):
                    app.update_settings({'theme': 'cow', 'overlay': {'title': '\ud800'}})
                self.assertEqual(app.settings, saved)
                self.assertEqual(app.settings_path.read_bytes(), disk)
                for state in (app.snapshot(), app.overlay_snapshot()):
                    json.dumps(state, ensure_ascii=False).encode('utf-8')
            finally:
                app.close()
            app = Application(root / 'store.sqlite', root)
            try:
                self.assertEqual(app.settings, saved)
                for state in (app.snapshot(), app.overlay_snapshot()):
                    json.dumps(state, ensure_ascii=False).encode('utf-8')
            finally:
                app.close()

    def test_slide_preferences_default_validate_and_persist(self):
        self.assertEqual(DEFAULT_SETTINGS['overlay']['slide_direction'], 'left')
        self.assertEqual(DEFAULT_SETTINGS['overlay']['slide_duration'], 400)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / 'store.sqlite', root)
            try:
                app.update_settings({'overlay': {'slide_direction': 'right', 'slide_duration': 5000}})
                self.assertEqual(app.settings['overlay']['slide_direction'], 'right')
                app.update_settings({'overlay': {'slide_duration': 0}})
            finally:
                app.close()
            app = Application(root / 'store.sqlite', root)
            try:
                self.assertEqual(app.settings['overlay']['slide_direction'], 'right')
                self.assertEqual(app.settings['overlay']['slide_duration'], 0)
            finally:
                app.close()

    def test_legacy_format_migrates_and_theme_overlay_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings.json').write_text('{"standings_format":"md"}')
            app = Application(root / 'store.sqlite', root)
            try:
                self.assertEqual(app.settings['standings_format'], 'md')
                self.assertEqual(app.settings['theme'], 'mario')
                old = app.store.snapshot()
                app.update_settings({'theme': 'rosalina', 'overlay': {'opacity': 0, 'layout': 'players'}})
                self.assertEqual(app.store.snapshot(), old)
                saved = deepcopy(app.settings)
                with patch.object(Path, 'replace', side_effect=OSError('test disk failure')):
                    with self.assertRaises(OSError):
                        app.update_settings({'theme': 'bowser'})
                self.assertEqual(app.settings, saved)
                self.assertEqual(json.loads((root / 'settings.json').read_text()), saved)
            finally:
                app.close()
            app = Application(root / 'store.sqlite', root)
            try:
                self.assertEqual(app.settings, saved)
                self.assertTrue(app.obs_text_path.name.endswith('.md'))
            finally:
                app.close()
