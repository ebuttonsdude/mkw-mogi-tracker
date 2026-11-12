"""Bound font residency using neutral temporary bytes; no fonts are bundled."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import gc
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import weakref

from PIL import ImageFont

from mkw_tracker import report_fonts


class FontResourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fonts = self.root / 'fonts'
        fonts.mkdir()
        self.neutral = ImageFont.load_default(size=20).path.getvalue()
        for number in range(40):
            (fonts / f'neutral-{number:02}.ttf').write_bytes(self.neutral + bytes([number]))
        self.library = report_fonts.FontLibrary(self.root, roots=[])

    def test_missing_glyph_checks_coverage_without_constructing_every_font(self):
        stack = self.library.font(ImageFont, 20, 'neutral-00.ttf')
        with patch.object(ImageFont, 'truetype', wraps=ImageFont.truetype) as construct:
            chosen = stack._select('\U0010ffff')
        self.assertIsNotNone(chosen)
        self.assertLessEqual(construct.call_count, 1)
        self.assertLessEqual(len(stack.loaded), 1)
        value = 'A 日本語 ♥\ufe0f A\u200dB \U0010ffff'
        self.assertEqual(''.join(text for _, text in stack.runs(value)), value)

    def test_resolved_clusters_do_not_pin_evicted_rendering_fonts(self):
        stack = self.library.font(ImageFont, 20, 'neutral-00.ttf')
        references = []
        construct = ImageFont.truetype

        def tracked(*args, **kwargs):
            font = construct(*args, **kwargs)
            references.append(weakref.ref(font))
            return font

        with patch.object(report_fonts, '_data_codepoints', side_effect=lambda data: frozenset({65 + data[-1]})), \
                patch.object(ImageFont, 'truetype', side_effect=tracked):
            for number in range(40):
                stack._select(chr(65 + number))
            gc.collect()
            self.assertLessEqual(sum(ref() is not None for ref in references), 8)
            self.assertLessEqual(len(stack.loaded), 8)
            # Revisit an evicted first choice: selection, not default, survives.
            self.assertEqual(stack._select('A').getname(), ImageFont.load_default(size=20).getname())

    def test_coverage_cache_does_not_retain_raw_font_blobs(self):
        if hasattr(report_fonts._data_codepoints, 'cache_clear'):
            report_fonts._data_codepoints.cache_clear()
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            with patch.object(report_fonts, '_parse_codepoints', return_value=frozenset({65})):
                for index in range(32):
                    report_fonts._data_codepoints(b'padding' + bytes([index]) * (256 * 1024))
            gc.collect()
            retained = tracemalloc.get_traced_memory()[0] - before
            self.assertLess(retained, 1024 * 1024)
        finally:
            tracemalloc.stop()

    def test_resident_source_and_coverage_bytes_obey_budget(self):
        stack = self.library.font(ImageFont, 20, 'neutral-00.ttf')
        with patch.object(report_fonts, 'MAX_RESIDENT_FONT_BYTES', len(self.neutral) * 3, create=True), \
                patch.object(report_fonts, '_data_codepoints', side_effect=lambda data: frozenset({65 + data[-1]})):
            for number in range(40):
                stack._select(chr(65 + number))
            self.assertLessEqual(len(stack.loaded), 2)
            self.assertLessEqual(stack.loaded_bytes, report_fonts.MAX_RESIDENT_FONT_BYTES)

    def test_oversized_selected_face_is_transient_but_keeps_shaping_runs(self):
        stack = self.library.font(ImageFont, 20, 'neutral-00.ttf')
        with patch.object(report_fonts, 'MAX_RESIDENT_FONT_BYTES', 1):
            runs = list(stack.runs('AVATAR'))
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0][1], 'AVATAR')
            self.assertEqual(runs[0][0].getname(), ImageFont.load_default(size=20).getname())
            self.assertFalse(stack.loaded)
            self.assertEqual(stack.loaded_bytes, 0)
            self.assertTrue(all(isinstance(value, str) or value is None for value in stack.resolved.values()))

    def test_resolution_cache_is_bounded_and_has_no_font_references(self):
        stack = self.library.font(ImageFont, 20, 'neutral-00.ttf')
        with patch.object(report_fonts, 'MAX_RESOLVED_CLUSTERS', 3):
            for char in 'ABCDE':
                stack._select(char)
            self.assertEqual(len(stack.resolved), 3)
            self.assertTrue(all(isinstance(value, str) or value is None for value in stack.resolved.values()))

    def test_coverage_cache_weight_and_parallel_updates_are_bounded(self):
        # Small limit also proves an individually oversized cmap is not retained.
        with patch.object(report_fonts, '_COVERAGE_CACHE', OrderedDict(), create=True), \
                patch.object(report_fonts, '_COVERAGE_BYTES', 0, create=True), \
                patch.object(report_fonts, 'MAX_COVERAGE_CACHE_BYTES', 4096, create=True), \
                patch.object(report_fonts, '_parse_codepoints', side_effect=lambda data, index: frozenset(range(data[0]))):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(report_fonts._data_codepoints, [bytes([n]) for n in range(128)]))
            self.assertEqual(results[-1], frozenset(range(127)))
            self.assertLessEqual(report_fonts._COVERAGE_BYTES, 4096)
            self.assertTrue(report_fonts._COVERAGE_CACHE)
            self.assertEqual(report_fonts._COVERAGE_BYTES,
                             sum(cost for _, cost in report_fonts._COVERAGE_CACHE.values()))
            self.assertLessEqual(len(report_fonts._COVERAGE_CACHE), report_fonts.MAX_COVERAGE_CACHE_ENTRIES)
            self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in report_fonts._COVERAGE_CACHE))
