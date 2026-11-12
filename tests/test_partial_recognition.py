"""Synthetic rank-mask coverage; no gameplay assets or OCR processes."""
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from mkw_tracker.recognition import ResultReader, _rank_templates


def table(ranks, top=45, scale=1):
    frame = np.zeros((1152, 2048, 3), dtype=np.uint8)
    additions = []
    for index, rank in enumerate(ranks):
        y = top + index * 82
        frame[y:y + 70, 1191:1255] = cv2.cvtColor(_rank_templates()[rank], cv2.COLOR_GRAY2BGR)
        frame[y:y + 70, 1357:1755] = (20 + index * 10, 30, 40)
        additions.append({'text': '+1', 'top': y + 23, 'height': 26, 'confidence': .9})
    if scale != 1:
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
    return frame, additions


class PartialRecognitionTests(unittest.TestCase):
    def reader(self, additions, count, name_reader=None):
        reader = ResultReader(name_reader=name_reader)
        names = [{'text': f'Player{i}', 'top': i * 82 + 23, 'height': 26, 'confidence': .8}
                 for i in range(count)]
        reader._ocr = Mock(side_effect=[additions, names])
        return reader

    def test_shifted_and_eleven_player_tables(self):
        for ranks, top in [(list(range(1, 12)), 45), (list(range(3, 12)), 86),
                           (list(range(6, 12)), 24), ([9, 10, 11], 44)]:
            with self.subTest(ranks=ranks):
                frame, additions = table(ranks, top)
                reader = self.reader(additions, len(ranks))
                result = reader.read_partial(frame)
                self.assertEqual(result['phase'], 'partial_results')
                self.assertEqual([row['position'] for row in result['rows']], ranks)
                self.assertEqual(result['rows'][0]['name'], 'Player0')
                self.assertTrue(result['needs_review'])

    def test_blank_gameplay_does_not_ocr(self):
        reader = self.reader([], 0)
        self.assertEqual(reader.read_partial(np.zeros((1152, 2048, 3), np.uint8))['rows'], [])
        reader._ocr.assert_not_called()

    def test_missing_plus_phase_never_reads_names(self):
        frame, additions = table([9, 10, 11])
        for evidence in [[], additions[:2], [dict(w, text='80') for w in additions]]:
            reader = self.reader(evidence, 3)
            self.assertEqual(reader.read_partial(frame)['rows'], [])
            self.assertEqual(reader._ocr.call_count, 1)

    def test_duplicate_reordered_and_invalid_ranks_rejected(self):
        for ranks in [[9, 9, 11], [9, 11, 10], [3, 5, 6]]:
            frame, additions = table(ranks)
            reader = self.reader(additions, 3)
            self.assertEqual(reader.read_partial(frame)['rows'], [])
            self.assertEqual(reader._ocr.call_count, 1)

    def test_unproven_middle_glyph_stays_absent(self):
        frame, additions = table([7, 8, 9, 10, 11])
        frame[45 + 2 * 82:45 + 2 * 82 + 40, 1191:1255] = 0
        reader = self.reader(additions, 4)
        self.assertEqual([r['position'] for r in reader.read_partial(frame)['rows']], [7, 8, 10, 11])

    def test_minimum_rows_skips_names_and_neural(self):
        frame, additions = table([9, 10, 11])
        neural = Mock(engine='paddleocr')
        reader = self.reader(additions, 3, neural)
        self.assertEqual(reader.read_partial(frame, minimum_rows=3)['rows'], [])
        self.assertEqual(reader._ocr.call_count, 1)
        neural.recognize.assert_not_called()

    def test_native_resolution_neural_alignment_and_blank_names(self):
        frame, additions = table([9, 10, 11], top=44, scale=2)
        neural = Mock(engine='external-vision')
        neural.recognize.return_value = [dict(name='A', confidence=.7, position=1),
                                        dict(name='', confidence=0), dict(name='C', confidence=.8)]
        reader = self.reader(additions, 3, neural)
        result = reader.read_partial(frame)
        self.assertEqual([r['position'] for r in result['rows']], [9, 10, 11])
        self.assertEqual([r['name'] for r in result['rows']], ['A', '', 'C'])
        self.assertFalse(result['rows'][0]['confidence_available'])
        crops = neural.recognize.call_args.args[0]
        self.assertEqual(crops[0].shape, (140, 796, 3))
        self.assertTrue(np.all(crops[0][20, 20] == (20, 30, 40)))
        self.assertTrue(np.all(crops[2][20, 20] == (40, 30, 40)))

    def test_neural_failure_retains_baseline_and_status(self):
        frame, additions = table([9, 10, 11])
        neural = Mock(engine='external-vision')
        neural.recognize.side_effect = RuntimeError('offline')
        reader = self.reader(additions, 3, neural)
        result = reader.read_partial(frame)
        self.assertEqual(result['rows'][0]['name'], 'Player0')
        self.assertEqual(result['name_engine'], 'tesseract')
        self.assertEqual(reader.recognition_status['name_engine_status'], 'fallback')

    def test_malformed_neural_batch_preserves_all_baseline_names(self):
        frame, additions = table([9, 10, 11])
        for response in [[{'name': 'Wrong'}], [{'name': 'Wrong'}, None, {'name': 'Wrong'}]]:
            neural = Mock(engine='paddle')
            neural.recognize.return_value = response
            reader = self.reader(additions, 3, neural)
            result = reader.read_partial(frame)
            self.assertEqual([r['name'] for r in result['rows']], ['Player0', 'Player1', 'Player2'])
            self.assertEqual(result['name_engine'], 'tesseract')

    def test_missing_baseline_name_does_not_shift_later_names(self):
        frame, additions = table([9, 10, 11])
        reader = self.reader(additions, 3)
        reader._ocr.side_effect = [additions, [{'text': 'Last', 'top': 187, 'height': 26, 'confidence': .8}]]
        self.assertEqual([r['name'] for r in reader.read_partial(frame)['rows']], ['', '', 'Last'])


if __name__ == '__main__':
    unittest.main()
