"""The clean distribution has palettes, not bundled character artwork."""
from pathlib import Path
import unittest

from mkw_tracker.themes import all_themes, character_icon_path, resolve_theme


class CharacterIconTests(unittest.TestCase):
    def test_all_presets_have_no_bundled_icon(self):
        themes = all_themes()
        self.assertEqual(len(themes), 50)
        for theme in themes:
            with self.subTest(theme=theme['id']):
                self.assertIsNone(theme['icon_url'])
                self.assertFalse(theme.get('icon_fallback', False))
                self.assertEqual(resolve_theme(theme['id'])['tokens'], theme['tokens'])

    def test_old_character_paths_are_never_available(self):
        for name in ('Character_Toad.png', '../themes.py', '/tmp/icon.png', '', None):
            self.assertIsNone(character_icon_path(name))

    def test_no_decorative_game_image_or_display_font_files(self):
        root = Path(__file__).resolve().parents[1]
        for directory in ('mkw-character-icons', 'mkw-track-icons',
                          'mkw_tracker/static/character-icons', 'mkw_tracker/static/track-icons',
                          'mkw_tracker/static/fonts'):
            self.assertFalse([path for path in (root / directory).rglob('*') if path.is_file()], directory)

    def test_docs_images_are_only_reviewed_synthetic_examples(self):
        directory = Path(__file__).resolve().parents[1] / 'docs/images'
        allowed = {'standings.png', 'overlay.png', 'name-review.png', 'ww-standings.png', 'ww-overlay.png', 'README.md'}
        self.assertFalse([path for path in directory.rglob('*')
                          if path.is_file() and path.relative_to(directory).as_posix() not in allowed])
        for name in ('standings.png', 'overlay.png', 'name-review.png', 'ww-standings.png', 'ww-overlay.png'):
            path = directory / name
            if path.exists():  # Screenshots are deliberately excluded from app distributions.
                self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
                self.assertIn('fictional', (directory / 'README.md').read_text())
