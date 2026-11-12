"""Still-image and conservative-boundary checks for the separate WW reader."""

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from mkw_tracker.ww_recognition import WWReader

FIXTURES = Path(__file__).parent / 'fixtures' / 'ww'
REFERENCES = json.loads((FIXTURES / 'reference.json').read_text())
HAS_OCR = bool(shutil.which('tesseract') and Path('.local/tessdata/eng.traineddata').is_file())


def frame_for(reference):
    if not (FIXTURES / reference['file']).is_file():
        raise unittest.SkipTest('Optional gameplay image is not distributed in this project')
    width, height = reference['frame_size']
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    left, top, right, bottom = reference['crop']
    frame[top:bottom, left:right] = cv2.imread(str(FIXTURES / reference['file']))
    return frame


def fixture(name):
    return frame_for(next(row for row in REFERENCES if row['file'] == name))


class WWRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.reader = WWReader()

    def tearDown(self):
        self.reader.close()

    def test_name_engine_selection_never_loads_a_name_model(self):
        for requested in ('auto', 'qwen', 'qwen-4b-nf4', 'qwen36-nvfp4', 'paddle'):
            with self.subTest(requested=requested):
                reader = WWReader(requested)
                self.assertIsNone(reader._ocr_reader.name_reader)
                self.assertEqual(reader.recognition_status['name_engine'], 'ww-digits')
                reader.warmup()
                reader.close()

    def test_rejects_non_16_by_9_and_invalid_images(self):
        for frame in (None, np.zeros((30, 30), dtype=np.uint8), np.zeros((480, 640, 3), dtype=np.uint8)):
            with self.subTest(shape=getattr(frame, 'shape', None)):
                with self.assertRaises(ValueError):
                    self.reader.read_results(frame)

    def test_black_gameplay_and_prerace_do_not_launch_digit_ocr(self):
        for frame in (np.zeros((720, 1280, 3), dtype=np.uint8), fixture('hardname-60.webp'),
                      fixture('easyname-330.webp')):
            with patch.object(self.reader._ocr_reader, '_ocr') as ocr:
                self.assertFalse(self.reader.is_results_layout(frame))
                self.assertIsNone(self.reader.read_results(frame))
                ocr.assert_not_called()

    def test_table_stays_visible_without_local_highlight(self):
        frame = fixture('hardname-98.webp')
        frame[230:283, 670:1250] = 0
        self.assertTrue(self.reader.is_results_layout(frame))
        with patch.object(self.reader._ocr_reader, '_ocr') as ocr:
            self.assertIsNone(self.reader.read_results(frame))
            ocr.assert_not_called()

    def test_a_single_gold_row_is_not_table_proof(self):
        frame = fixture('hardname-98.webp')
        frame[:230] = 0
        frame[283:] = 0
        self.assertFalse(self.reader.is_results_layout(frame))
        self.assertIsNone(self.reader.read_results(frame))

    def test_partial_colored_podium_glyph_cannot_fall_back_to_wrong_white_digit(self):
        frame = fixture('hardname-389.webp')
        self.assertTrue(self.reader.is_results_layout(frame))
        with patch.object(self.reader, '_digits') as digits:
            self.assertIsNone(self.reader.read_results(frame))
            digits.assert_not_called()

    def test_unreadable_delta_is_not_mistaken_for_final_rating(self):
        frame = fixture('hardname-96.webp')
        with patch.object(self.reader, '_digits', side_effect=[5, 8576, None]):
            self.assertIsNone(self.reader.read_results(frame))

    def test_rejects_out_of_range_position_or_rating(self):
        frame = fixture('hardname-98.webp')
        for position, rating in ((25, 8628), (0, 8628), (5, 0), (5, 100000)):
            with self.subTest(position=position, rating=rating):
                with patch.object(self.reader, '_rank_number', return_value=position):
                    with patch.object(self.reader, '_rating', return_value=rating):
                        self.assertIsNone(self.reader.read_results(frame))

    def test_delta_requires_an_explicit_sign(self):
        mask = np.zeros((40, 60), dtype=np.uint8)
        for text, expected in [('52', None), ('+52', 52), ('-46', -46), ('+0', 0), ('-0', 0), ('+', None)]:
            with self.subTest(text=text):
                with patch.object(self.reader._ocr_reader, '_ocr', return_value=[{'text': text, 'confidence': .9}]):
                    self.assertEqual(self.reader._digits(mask, signed=True), expected)

    def test_rank_fallback_does_not_invent_an_unreadable_digit(self):
        from mkw_tracker.ww_recognition import _ink

        frame = fixture('easyname-1330.webp')
        center = self.reader._highlight(frame)
        mask = _ink(frame[center - 22:center + 22, 712:757])
        with patch.object(self.reader, '_digits', side_effect=[1, None]):
            self.assertIsNone(self.reader._rank_number(mask))

    def test_rating_glyph_count_rejects_inserted_ocr_digit(self):
        frame = fixture('hardname-96.webp')
        center = self.reader._highlight(frame)
        crop = frame[center - 22:center + 22, 1148:1240]
        with patch.object(self.reader, '_digits', return_value=85776):
            self.assertIsNone(self.reader._rating(crop))
        with patch.object(self.reader, '_digits', side_effect=[85776, 8576]):
            self.assertEqual(self.reader._rating(crop), 8576)

    def test_low_confidence_digits_are_not_automatic_observations(self):
        mask = np.zeros((40, 60), dtype=np.uint8)
        with patch.object(self.reader._ocr_reader, '_ocr',
                          return_value=[{'text': '8759', 'confidence': .1}]):
            self.assertIsNone(self.reader._digits(mask))

    @unittest.skipUnless(HAS_OCR, 'Tesseract and local English data required')
    def test_sample_results_distinguish_change_and_final_without_names(self):
        for reference in REFERENCES:
            if reference['result'] is None:
                continue
            with self.subTest(file=reference['file']):
                frame = frame_for(reference)
                self.assertTrue(self.reader.is_results_layout(frame))
                self.assertEqual(self.reader.read_results(frame), {**reference['result'], 'lobby_size': None})

    @unittest.skipUnless(HAS_OCR, 'Tesseract and local English data required')
    def test_two_visible_rows_are_sufficient_and_do_not_invent_lobby_count(self):
        # Simulated two-row table using real first/second-rank glyphs. This is
        # not a claim of validation on an actual two-player recording.
        frame = fixture('hardname-388.webp')
        frame[130:] = 0
        self.assertTrue(self.reader.is_results_layout(frame))
        result = self.reader.read_results(frame)
        self.assertEqual(result['position'], 1)
        self.assertEqual(result['vr'], 8644)
        self.assertIsNone(result['lobby_size'])

    @unittest.skipUnless(HAS_OCR, 'Tesseract and local English data required')
    def test_course_labels_at_upper_and_lower_map_positions(self):
        for reference in REFERENCES:
            if not reference['course']:
                continue
            with self.subTest(file=reference['file']):
                result = self.reader.read_prerace(frame_for(reference))
                self.assertEqual(result, {'course': reference['course'], 'lobby_size': None})

    def test_course_ocr_is_gated_and_ambiguous_vocabulary_is_rejected(self):
        with patch.object(self.reader._ocr_reader, '_ocr') as ocr:
            self.assertIsNone(self.reader.read_prerace(np.zeros((720, 1280, 3), dtype=np.uint8)))
            ocr.assert_not_called()
        frame = fixture('easyname-330.webp')
        words = [{'text': 'SNES Choco Island', 'confidence': .9, 'top': 100, 'height': 20}]
        with patch.object(self.reader._ocr_reader, '_ocr', return_value=words):
            self.assertIsNone(self.reader.read_prerace(frame))


if __name__ == '__main__':
    unittest.main()
