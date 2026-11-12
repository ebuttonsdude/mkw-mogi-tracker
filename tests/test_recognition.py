import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from mkw_tracker.recognition import ResultReader, _rank_templates


def read_fixture(path):
    if not Path(path).is_file():
        raise unittest.SkipTest('Optional gameplay image is not distributed in this project')
    return cv2.imread(path)


def word(text, row, confidence=.9):
    return {'text': text, 'top': 23 + row * 82, 'height': 26, 'confidence': confidence}


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.reader = ResultReader()
        self.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    def test_status_reports_selection_warmup_and_failure(self):
        from unittest.mock import Mock
        self.assertEqual(self.reader.recognition_status['name_engine'], 'tesseract')
        neural = Mock(engine='paddle')
        reader = ResultReader(name_reader=neural)
        self.assertEqual(reader.recognition_status['name_engine_status'], 'selected')
        reader.warmup()
        self.assertEqual(reader.recognition_status['name_engine_status'], 'ready')
        neural.recognize.side_effect = RuntimeError('missing model')
        with self.assertRaisesRegex(RuntimeError, 'missing model'):
            reader.warmup()
        self.assertEqual(reader.recognition_status['name_engine_status'], 'error')
        self.assertEqual(reader.recognition_status['name_engine_error'], 'missing model')

    def test_complete_results_are_review_candidates_and_ignore_point_values(self):
        names = [word(f'Player {i + 1}', i) for i in range(12)]
        plus = [word('+8', 0), word('+3', 1), word('+1', 2)]
        ranks = [[word(str(i + 1), 0)] for i in range(12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, plus, *ranks]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['phase'], 'finishing_results')
        self.assertEqual([r['position'] for r in result['rows']], list(range(1, 13)))
        self.assertTrue(result['needs_review'])
        self.assertNotIn('points', result['rows'][0])

    def test_standings_are_never_returned_as_finishing_rows(self):
        names = [word(f'Player {i}', i) for i in range(12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, []]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['phase'], 'standings')
        self.assertEqual(result['rows'], [])

    def test_rejected_table_can_be_evidence_without_finishing_rows(self):
        names = [word(f'Player {i}', i) for i in range(12)]
        plus = [word('+8', i) for i in range(3)]
        ranks = [[]] + [[word(str(i + 1), 0)] for i in range(1, 12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, plus, *ranks]) as ocr:
            result = self.reader.read(self.frame)
        self.assertEqual(ocr.call_count, 3)
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(result['rows'], [])
        self.assertIs(result['suspected_results'], True)
        self.assertEqual(result['suspected_results_details'],
                         {'name_rows': 12, 'plus_rows': 3, 'rank_rows': 0})
        self.assertIn('unverified', result['suspected_results_reason'])

    def test_standings_evidence_requires_rank_shapes_not_just_text(self):
        names = [word(f'Player {i}', i) for i in range(12)]
        for rank_matches, suspected in ((False, False), (True, True)):
            with self.subTest(rank_matches=rank_matches):
                with patch.object(self.reader, '_ocr', side_effect=[names, []]) as ocr:
                    with patch.object(self.reader, '_rank_matches', return_value=rank_matches):
                        result = self.reader.read(self.frame)
                self.assertEqual(result['phase'], 'standings')
                self.assertEqual(result['rows'], [])
                self.assertEqual(bool(result.get('suspected_results')), suspected)
                self.assertEqual(ocr.call_count, 2)
                if suspected:
                    self.assertIn('cumulative', result['suspected_results_reason'])

    def test_sparse_gameplay_text_does_not_trigger_evidence_or_extra_ocr(self):
        with patch.object(self.reader, '_ocr', return_value=[word('Player', 11)]) as ocr:
            with patch.object(self.reader, '_rank_matches') as rank:
                result = self.reader.read(self.frame)
        self.assertFalse(result.get('suspected_results'))
        ocr.assert_called_once()
        rank.assert_not_called()

    def test_quote_in_tesseract_name_cannot_consume_following_tsv_rows(self):
        from types import SimpleNamespace
        output = ('level\tconf\ttop\theight\ttext\n'
                  '5\t90\t23\t26\t"Player\n'
                  '5\t91\t105\t26\tNext player\n').encode()
        with patch('mkw_tracker.recognition.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=output)):
            words = self.reader._ocr(np.zeros((70, 200), dtype=np.uint8))
        self.assertEqual([entry['text'] for entry in words], ['"Player', 'Next player'])

    def test_complete_names_cannot_bypass_a_missing_position(self):
        names = [word(f'Player {i}', i) for i in range(12)]
        ranks = [[]] + [[word(str(i + 1), 0)] for i in range(1, 12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, [word('+8', i) for i in range(3)], *ranks]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(result['rows'], [])

    def test_neural_names_preserve_row_positions_and_require_review(self):
        names = [word(f'Player {i + 1}', i) for i in range(12)]
        plus = [word('+8', i) for i in range(3)]
        ranks = [[word(str(i + 1), 0)] for i in range(12)]
        from unittest.mock import Mock
        from mkw_tracker.name_engines import GENERATIVE_NAME_ENGINES
        for engine in GENERATIVE_NAME_ENGINES:
            with self.subTest(engine=engine):
                neural = Mock(engine=engine)
                neural.recognize.return_value = [{'name': 'てがすべってdc☆' if i == 0 else f'名前 {i}',
                                                'confidence': 0.0} for i in range(12)]
                self.reader.name_reader = neural
                with patch.object(self.reader, '_ocr', side_effect=[names, plus, *ranks]):
                    result = self.reader.read(self.frame)
                self.assertEqual(result['rows'][0]['name'], 'てがすべってdc☆')
                self.assertEqual([row['position'] for row in result['rows']], list(range(1, 13)))
                self.assertFalse(result['rows'][0]['confidence_available'])
                self.assertTrue(result['needs_review'])
                self.assertEqual(result['name_engine'], engine)
                self.assertEqual(self.reader.recognition_status['name_engine'], engine)
                self.assertEqual(self.reader.recognition_status['name_engine_status'], 'ready')
                self.assertEqual(len(neural.recognize.call_args.args[0]), 12)

    def test_neural_failure_remains_visible_and_does_not_create_confident_results(self):
        from unittest.mock import Mock
        self.reader.name_reader = Mock(engine='qwen')
        self.reader.name_reader.recognize.side_effect = RuntimeError('model unavailable')
        names = [word(f'Player {i + 1}', i) for i in range(12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, [word('+8', i) for i in range(3)],
                                                          *[[word(str(i + 1), 0)] for i in range(12)]]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['rows'][0]['name'], 'Player 1')
        self.assertEqual(result['name_engine'], 'tesseract')
        self.assertEqual(self.reader.recognition_status['name_engine'], 'tesseract')
        self.assertEqual(self.reader.recognition_status['selected_name_engine'], 'qwen')
        self.assertEqual(self.reader.recognition_status['name_engine_status'], 'fallback')
        self.assertTrue(result['needs_review'])
        self.assertTrue(any('Neural name recognition failed' in reason for reason in result['reasons']))

    def test_unreadable_baseline_name_can_reach_neural_model_when_all_positions_proven(self):
        from unittest.mock import Mock
        neural = Mock(engine='qwen')
        neural.recognize.return_value = [{'name': f'選手☆{i}', 'confidence': 0.0} for i in range(12)]
        self.reader.name_reader = neural
        names = [word(f'Player {i}', i) for i in range(12) if i != 7]
        with patch.object(self.reader, '_ocr', side_effect=[names, [word('+8', i) for i in range(3)],
                                                          *[[word(str(i + 1), 0)] for i in range(12)]]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['phase'], 'finishing_results')
        self.assertEqual(result['rows'][7]['name'], '選手☆7')
        neural.recognize.assert_called_once()

    def test_unreadable_name_with_partial_rank_table_cannot_reach_neural_model(self):
        from unittest.mock import Mock
        neural = Mock(engine='qwen')
        self.reader.name_reader = neural
        names = [word(f'Player {i}', i) for i in range(12) if i != 0]
        ranks = [[]] + [[word(str(i + 1), 0)] for i in range(1, 12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, [word('+8', i) for i in range(3)], *ranks]):
            result = self.reader.read(self.frame)
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(result['rows'], [])
        neural.recognize.assert_not_called()

    def test_proven_rank_shapes_skip_per_row_tesseract_processes(self):
        frame = np.zeros((1152, 2048, 3), dtype=np.uint8)
        for rank, template in _rank_templates().items():
            top = 45 + (rank - 1) * 82
            frame[top:top + 70, 1191:1255] = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
        names = [word(f'Player {i}', i) for i in range(12)]
        with patch.object(self.reader, '_ocr', side_effect=[names, [word('+8', i) for i in range(3)]]) as ocr:
            result = self.reader.read(frame)
        self.assertEqual(result['phase'], 'finishing_results')
        self.assertEqual(len(result['rows']), 12)
        self.assertEqual(ocr.call_count, 2)

    def test_rank_shape_fallback_requires_the_expected_glyph(self):
        templates = _rank_templates()
        for rank, template in templates.items():
            with self.subTest(rank=rank):
                crop = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
                self.assertTrue(self.reader._rank_matches(crop, rank))
                self.assertFalse(self.reader._rank_matches(crop, rank % 12 + 1))
                self.assertFalse(self.reader._rank_matches(np.zeros_like(crop), rank))
                self.assertFalse(self.reader._rank_matches(np.full_like(crop, 255), rank))
                self.assertFalse(self.reader._rank_matches(crop[:50], rank))

    def test_highlighted_bronze_rank_keeps_colored_ink_not_gold_panel(self):
        frame = read_fixture(str(Path(__file__).parent / 'fixtures/mogi-2026-10-19/finishing-burnout.webp'))
        crop = frame[209:279, 1191:1255]
        self.assertTrue(self.reader._rank_matches(crop, 3))
        for rank in range(1, 13):
            if rank != 3:
                self.assertFalse(self.reader._rank_matches(crop, rank))
        self.assertFalse(self.reader._rank_matches(np.full_like(crop, (0, 190, 255)), 3))

    def test_highlighted_gold_rank_keeps_gold_face_without_accepting_panel(self):
        frame = read_fixture(str(Path(__file__).parent / 'fixtures/mogi-2026-10-19/finishing-wario.webp'))
        crop = frame[45:115, 1191:1255]
        self.assertTrue(self.reader._rank_matches(crop, 1))
        for rank in range(2, 13):
            self.assertFalse(self.reader._rank_matches(crop, rank))
        self.assertFalse(self.reader._rank_matches(np.full_like(crop, (0, 190, 255)), 1))

    def test_gameplay_and_standings_backgrounds_are_not_team_rosters(self):
        for name in ('standings-railings', 'gameplay-ruins'):
            with self.subTest(frame=name):
                frame = read_fixture(str(Path(__file__).parent / f'fixtures/mogi-2026-10-19/{name}.webp'))
                self.assertFalse(self.reader.is_prerace_layout(frame))

    @unittest.skipUnless(Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_burnout_complete_results_with_highlighted_bronze_rank(self):
        frame = read_fixture(str(Path(__file__).parent / 'fixtures/mogi-2026-10-19/finishing-burnout.webp'))
        result = self.reader.read(frame)
        self.assertEqual(result['phase'], 'finishing_results')
        self.assertEqual([row['position'] for row in result['rows']], list(range(1, 13)))
        self.assertTrue(result['needs_review'])

    @unittest.skipUnless(Path('.local/tessdata/eng.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_rainbow_road_uses_higher_map_label(self):
        frame = read_fixture(str(Path(__file__).parent / 'fixtures/mogi-2026-10-19/prerace-rainbow.webp'))
        result = self.reader.read_prerace(frame)
        self.assertIsNotNone(result)
        self.assertEqual(result['course'], 'Rainbow Road')
        self.assertTrue(result['matched_course'])

    @unittest.skipUnless(Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_ghost_results_window_and_suspected_evidence_do_not_relax_phase(self):
        from unittest.mock import Mock
        fixtures = Path(__file__).parent / 'fixtures/mogi-2026-10-19'
        for name, phase, suspected in [('partial-ghost', 'unknown', True),
                                        ('finishing-ghost', 'finishing_results', False),
                                        ('points-consumed-ghost', 'standings', True),
                                        ('partial-wario', 'unknown', True),
                                        ('finishing-wario', 'finishing_results', False)]:
            with self.subTest(frame=name):
                neural = Mock(engine='qwen')
                neural.recognize.return_value = [{'name': f'Mocked {i}', 'confidence': 0.0}
                                                 for i in range(12)]
                reader = ResultReader(name_reader=neural)
                result = reader.read(read_fixture(str(fixtures / f'{name}.webp')))
                self.assertEqual(result['phase'], phase)
                self.assertEqual(bool(result.get('suspected_results')), suspected)
                self.assertEqual(len(result['rows']), 12 if phase == 'finishing_results' else 0)
                if suspected:
                    neural.recognize.assert_not_called()
                else:
                    neural.recognize.assert_called_once()

    @unittest.skipUnless(Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_jpeg95_preserves_strict_fixture_phase_boundaries(self):
        from unittest.mock import Mock
        fixtures = Path(__file__).parent / 'fixtures'
        cases = [('mogi-2026-10-19', 'partial-ghost', 'unknown'),
                 ('mogi-2026-10-19', 'finishing-ghost', 'finishing_results'),
                 ('mogi-2026-10-19', 'points-consumed-ghost', 'standings'),
                 ('mogi-2026-10-19', 'finishing-burnout', 'finishing_results'),
                 ('mogi-2026-10-19', 'partial-wario', 'unknown'),
                 ('mogi-2026-10-19', 'finishing-wario', 'finishing_results'),
                 ('mogi-2026-10-19', 'standings-railings', 'standings'),
                 ('mogi-2026-10-19', 'gameplay-ruins', 'unknown'),
                 ('team-event', 'finishing-sundae', 'finishing_results'),
                 ('team-event', 'finishing-oasis', 'finishing_results'),
                 ('team-event', 'points-consumed', 'standings'),
                 ('team-event', 'reordered-oasis', 'standings'),
                 ('team-event', 'partial-oasis', 'unknown'),
                 ('team-event', 'team-summary', 'unknown'),
                 ('team-event', 'gameplay', 'unknown')]
        for folder, name, phase in cases:
            with self.subTest(frame=name):
                frame = read_fixture(str(fixtures / folder / f'{name}.webp'))
                encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])[1]
                decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                neural = Mock(engine='qwen')
                neural.recognize.return_value = [{'name': f'Mocked {i}', 'confidence': 0.0}
                                                 for i in range(12)]
                result = ResultReader(name_reader=neural).read(decoded)
                self.assertEqual(result['phase'], phase)
                self.assertEqual(len(result['rows']), 12 if phase == 'finishing_results' else 0)
                if phase != 'finishing_results':
                    neural.recognize.assert_not_called()
                if name in ('partial-ghost', 'points-consumed-ghost', 'partial-wario'):
                    self.assertTrue(result.get('suspected_results'))
                if name in ('gameplay', 'gameplay-ruins', 'team-summary'):
                    self.assertFalse(result.get('suspected_results'))

    def test_higher_course_crop_requires_known_course_and_preserves_primary_read(self):
        with patch.object(self.reader, 'is_prerace_layout', return_value=True):
            with patch.object(self.reader, '_ocr', side_effect=[[], [word('Arbitrary artwork', 0)]]) as ocr:
                self.assertIsNone(self.reader.read_prerace(self.frame))
                self.assertEqual(ocr.call_count, 2)
            with patch.object(self.reader, '_ocr', return_value=[word('Crown City', 0)]) as ocr:
                self.assertEqual(self.reader.read_prerace(self.frame)['course'], 'Crown City')
                ocr.assert_called_once()

    def test_partial_entering_table_is_unknown(self):
        with patch.object(self.reader, '_ocr', return_value=[word('Player', 11)]):
            self.assertEqual(self.reader.read(self.frame)['phase'], 'unknown')

    def test_missing_rows_cannot_look_complete(self):
        with patch.object(self.reader, '_ocr', return_value=[word('Player', i) for i in range(10)]):
            result = self.reader.read(self.frame)
        self.assertTrue(result['needs_review'])
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(result['rows'], [])

    def test_prerace_layout_survives_course_ocr_failure(self):
        frame = np.zeros((1152, 2048, 3), dtype=np.uint8)
        for x in (420, 950):
            for y in (130, 212, 294, 376, 458, 540):
                frame[y - 8:y + 8, x - 8:x + 8] = 70
        with patch.object(self.reader, '_ocr', return_value=[]):
            self.assertIsNone(self.reader.read_prerace(frame))
        self.assertTrue(self.reader.is_prerace_layout(frame))

    def test_colored_cinematic_cannot_pass_as_grey_roster(self):
        frame = np.zeros((1152, 2048, 3), dtype=np.uint8)
        frame[:] = (110, 120, 130)
        self.assertFalse(self.reader.is_prerace_layout(frame))

    def test_wrong_aspect_ratio_is_rejected(self):
        with self.assertRaises(ValueError):
            self.reader.read(np.zeros((100, 100, 3), dtype=np.uint8))

    def test_team_rosters_use_panel_geometry_not_neutral_colors(self):
        fixtures = Path(__file__).parent / 'fixtures/team-event'
        for name in ('prerace-sundae', 'prerace-oasis'):
            with self.subTest(frame=name):
                self.assertTrue(self.reader.is_prerace_layout(read_fixture(str(fixtures / f'{name}.webp'))))
        for name in ('finishing-sundae', 'partial-oasis', 'team-summary', 'gameplay'):
            with self.subTest(frame=name):
                self.assertFalse(self.reader.is_prerace_layout(read_fixture(str(fixtures / f'{name}.webp'))))

    def test_team_rank_shape_fallback_proves_dark_glyph_not_arbitrary_color(self):
        fixtures = Path(__file__).parent / 'fixtures/team-event'
        for name in ('finishing-sundae', 'finishing-oasis'):
            frame = read_fixture(str(fixtures / f'{name}.webp'))
            for rank in range(5, 13):
                with self.subTest(frame=name, rank=rank):
                    crop = frame[45 + (rank - 1) * 82:115 + (rank - 1) * 82, 1191:1255]
                    self.assertTrue(self.reader._rank_matches(crop, rank))
                    self.assertFalse(self.reader._rank_matches(crop, rank % 12 + 1))

    @unittest.skipUnless(Path('.local/tessdata/eng.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_team_prerace_course_labels(self):
        fixtures = Path(__file__).parent / 'fixtures/team-event'
        for name, course in [('prerace-sundae', 'Sky-High Sundae'), ('prerace-oasis', 'Faraway Oasis')]:
            with self.subTest(frame=name):
                result = self.reader.read_prerace(read_fixture(str(fixtures / f'{name}.webp')))
                self.assertEqual(result['course'], course)
                self.assertTrue(result['matched_course'])

    @unittest.skipUnless(Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_real_team_finishing_and_negative_phase_boundaries(self):
        from unittest.mock import Mock
        fixtures = Path(__file__).parent / 'fixtures/team-event'
        for name, phase in [('finishing-sundae', 'finishing_results'),
                            ('finishing-oasis', 'finishing_results'),
                            ('points-consumed', 'standings'), ('reordered-oasis', 'standings'),
                            ('partial-oasis', 'unknown'), ('team-summary', 'unknown'),
                            ('gameplay', 'unknown')]:
            with self.subTest(frame=name):
                neural = Mock(engine='qwen')
                neural.recognize.return_value = [{'name': f'選手☆{i}', 'confidence': 0.0}
                                                 for i in range(12)]
                result = ResultReader(name_reader=neural).read(read_fixture(str(fixtures / f'{name}.webp')))
                self.assertEqual(result['phase'], phase)
                self.assertEqual(len(result['rows']), 12 if phase == 'finishing_results' else 0)
                self.assertTrue(result['needs_review'])
                if phase == 'finishing_results':
                    neural.recognize.assert_called_once()
                    self.assertEqual([row['position'] for row in result['rows']], list(range(1, 13)))
                    self.assertTrue(all('points' not in row for row in result['rows']))
                else:
                    neural.recognize.assert_not_called()
                    if name in ('gameplay', 'team-summary'):
                        self.assertFalse(result.get('suspected_results'))

    @unittest.skipUnless(Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local OCR data required')
    def test_team_fixture_replay_opens_pending_races_and_never_counts_unreviewed_points(self):
        import tempfile
        import threading
        from unittest.mock import Mock

        from mkw_tracker.capture import CaptureCoordinator
        from mkw_tracker.store import TrackerStore

        fixtures = Path(__file__).parent / 'fixtures/team-event'
        neural = Mock(engine='qwen')
        neural.recognize.return_value = [{'name': f'選手☆{i}', 'confidence': 0.0} for i in range(12)]
        with tempfile.TemporaryDirectory() as data:
            store = TrackerStore(str(Path(data) / 'tracker.sqlite3'))
            capture = CaptureCoordinator(store, threading.RLock(), data, ResultReader(name_reader=neural))
            try:
                for name, races, reviews in [('prerace-sundae', 0, 0), ('gameplay', 1, 0),
                                             ('finishing-sundae', 1, 1), ('points-consumed', 1, 1),
                                             ('prerace-oasis', 1, 1), ('gameplay', 2, 1),
                                             ('finishing-oasis', 2, 2), ('reordered-oasis', 2, 2)]:
                    capture.observe(read_fixture(str(fixtures / f'{name}.webp')))
                    self.assertEqual(len(store.snapshot()['races']), races)
                    self.assertEqual(len(capture.snapshot()['pending']), reviews)
                    self.assertEqual(store.snapshot()['players'], [])
                self.assertEqual([race['course'] for race in store.snapshot()['races']],
                                 ['Sky-High Sundae', 'Faraway Oasis'])
                for race in store.snapshot()['races']:
                    self.assertEqual(race['status'], 'pending')
                    self.assertEqual({evidence['kind'] for evidence in race['evidence']}, {'prerace', 'results'})
            finally:
                capture.close()
                store.close()

    def test_gameplay_without_roster_is_not_a_prerace_screen(self):
        with patch.object(self.reader, '_ocr') as ocr:
            self.assertIsNone(self.reader.read_prerace(self.frame))
            ocr.assert_not_called()

    @unittest.skipUnless(Path('.omx/evidence/frame-098.png').exists()
                         and Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local sample and OCR data required')
    def test_real_sample_phase_boundaries(self):
        for stamp, phase in [('098', 'finishing_results'), ('104', 'standings'),
                             ('210', 'unknown'), ('212', 'finishing_results'),
                             ('214', 'standings')]:
            with self.subTest(stamp=stamp):
                result = self.reader.read(cv2.imread(f'.omx/evidence/frame-{stamp}.png'))
                self.assertEqual(result['phase'], phase)
                self.assertTrue(result['needs_review'])

    @unittest.skipUnless(Path('.omx/evidence/frame-098.png').exists()
                         and Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local sample and OCR data required')
    def test_real_complete_table_with_unreadable_baseline_name_reaches_neural_model(self):
        from unittest.mock import Mock
        for stamp in ('098', '212'):
            with self.subTest(stamp=stamp):
                neural = Mock(engine='qwen')
                neural.recognize.return_value = [
                    {'name': f'選手☆{i}', 'confidence': 0.0} for i in range(12)]
                reader = ResultReader(name_reader=neural)
                original_ocr = reader._ocr

                def drop_name(image, **kwargs):
                    words = original_ocr(image, **kwargs)
                    if kwargs.get('language', 'eng+jpn') == 'eng+jpn':
                        words = [w for w in words
                                 if round((w['top'] + w['height'] / 2 - 36) / 82) != 7]
                    return words

                with patch.object(reader, '_ocr', side_effect=drop_name):
                    result = reader.read(cv2.imread(f'.omx/evidence/frame-{stamp}.png'))
                self.assertEqual(result['phase'], 'finishing_results')
                self.assertEqual(result['rows'][7]['name'], '選手☆7')
                self.assertTrue(result['needs_review'])
                neural.recognize.assert_called_once()

    @unittest.skipUnless(Path('.omx/evidence/frame-210.png').exists()
                         and Path('.omx/evidence/full-toad-1484.png').exists()
                         and Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local samples and OCR data required')
    def test_neural_model_cannot_bypass_real_partial_or_reordered_tables(self):
        from unittest.mock import Mock
        for filename, phase in [('frame-210', 'unknown'), ('full-toad-1484', 'unknown'),
                                ('frame-104', 'standings'), ('frame-214', 'standings')]:
            with self.subTest(frame=filename):
                neural = Mock(engine='qwen')
                result = ResultReader(name_reader=neural).read(
                    cv2.imread(f'.omx/evidence/{filename}.png'))
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['rows'], [])
                neural.recognize.assert_not_called()

    @unittest.skipUnless(Path('.omx/evidence/full-toad-1485.png').exists()
                         and Path('.local/tessdata/jpn.traineddata').exists()
                         and shutil.which('tesseract'), 'local long sample and OCR data required')
    def test_real_long_sample_short_finishing_window(self):
        # The entering table at 1484 lacks four rows. At 1486 the points
        # animation has consumed most +values, so only 1485 is a safe sample.
        # A two-second polling interval skips this race's complete +table.
        for stamp, phase in [(1484, 'unknown'), (1485, 'finishing_results'),
                             (1486, 'standings')]:
            with self.subTest(stamp=stamp):
                result = self.reader.read(cv2.imread(f'.omx/evidence/full-toad-{stamp}.png'))
                self.assertEqual(result['phase'], phase)
                self.assertTrue(result['needs_review'])
                self.assertEqual(len(result['rows']), 12 if stamp == 1485 else 0)


if __name__ == '__main__':
    unittest.main()
