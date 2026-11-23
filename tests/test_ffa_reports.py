"""Player-only report regressions independent of the active dashboard format."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.reports import (cumulative_player_series, folder_report, render_folder_png,
                                 team_standings_markdown, team_standings_text)
from mkw_tracker.store import TrackerStore


class FFAReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TrackerStore(Path(self.temp.name) / 'scores.sqlite')
        self.addCleanup(self.store.close)
        self.ids = [self.store.add_player(f'選手 Ω {i}')['id'] for i in range(12)]
        self.store.create_race('first', 'Rainbow Road')
        self.store.submit_result('first', self.ids)
        self.folder = self.store.snapshot()['session']['folder_id']

    def snapshot(self):
        snapshot = self.store.snapshot()
        snapshot['format'] = snapshot['session']['format'] = 'ffa'
        for folder in snapshot['folders']:
            folder['format'] = 'ffa'
        return snapshot

    def test_individual_totals_corrections_penalties_and_pending_prefix(self):
        self.store.correct_result('first', list(reversed(self.ids)))
        self.store.add_penalty('first', self.ids[-1], 20)
        self.store.create_race('pending', 'Acorn Heights')
        self.store.create_race('later', 'Rainbow Road')
        self.store.submit_result('later', self.ids)
        report = folder_report(self.snapshot(), self.folder)
        self.assertEqual(report['format'], 'ffa')
        self.assertEqual(report['teams'], [])
        self.assertTrue(all(p['team_name'] is None and p['team_id'] is None for p in report['players']))
        self.assertIsNone(report['membership_note'])
        self.assertEqual((report['gross_points'], report['net_points']), (164, 144))
        player = next(p for p in report['players'] if p['id'] == self.ids[-1])
        self.assertEqual(player['races'][0]['net_points'], -5)
        series = next(p for p in cumulative_player_series(report) if p['id'] == self.ids[-1])
        self.assertEqual(series['values'], [-5, None, None])
        relative = {p['id']: p['values'] for p in cumulative_player_series(report, relative=True)}
        self.assertAlmostEqual(relative[self.ids[-1]][0], -5 - 82 / 12)
        self.assertEqual(relative[self.ids[-1]][1:], [None, None])
        self.store.reorder_history(self.folder, ['later', 'first', 'pending'], ['first', 'pending', 'later'])
        report = folder_report(self.snapshot(), self.folder)
        series = next(p for p in cumulative_player_series(report) if p['id'] == self.ids[-1])
        self.assertEqual([r['id'] for r in report['races']], ['later', 'first', 'pending'])
        self.assertEqual(series['values'], [1, -4, None])
        relative = {p['id']: p['values'] for p in cumulative_player_series(report, relative=True)}
        self.assertAlmostEqual(relative[self.ids[-1]][0], 1 - 82 / 12)
        self.assertAlmostEqual(relative[self.ids[-1]][1], -4 - 164 / 12)
        self.assertIsNone(relative[self.ids[-1]][2])

    def test_relative_series_keeps_twelve_player_pace_with_a_substitute(self):
        sub = self.store.add_player('Sub')['id']
        self.store.create_race('second', 'Rainbow Road')
        self.store.submit_result('second', [sub, *self.ids[1:]])
        report = folder_report(self.snapshot(), self.folder)
        relative = {p['id']: p['values'] for p in cumulative_player_series(report, relative=True)}
        self.assertAlmostEqual(relative[sub][0], -82 / 12)
        self.assertAlmostEqual(relative[self.ids[0]][0], 15 - 82 / 12)
        self.assertAlmostEqual(relative[sub][1], 15 - 164 / 12)
        self.assertEqual(relative[sub][1], relative[self.ids[0]][1])

    def test_text_and_markdown_are_player_only_and_escape_names(self):
        self.store.update_player(self.ids[0], '選手 | <Ω>', None)
        state = self.snapshot()
        text = team_standings_text(state)
        markdown = team_standings_markdown(state)
        self.assertIn('FFA PLAYER STANDINGS', text)
        self.assertIn('選手 | <Ω> | 15 pts', text)
        self.assertIn('Player', markdown)
        self.assertIn('選手 \\| &lt;Ω&gt;', markdown)
        for output in (text, markdown):
            self.assertNotIn('Unassigned', output)
            self.assertNotIn('No teams', output)
            self.assertIn('82/984', output)

    def test_png_uses_players_unicode_fonts_and_scoped_highlight(self):
        from PIL import Image
        state = self.snapshot()
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw:
            data = render_folder_png(state, self.folder, highlight_player_id=self.ids[0],
                                     highlight_session_id=state['session']['id'])
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertIn('Cumulative player net points', labels)
        self.assertIn('FFA player standings · Net points', labels)
        self.assertNotIn('Unassigned', labels)
        self.assertIn('Place 1', labels)
        with patch('mkw_tracker.reports._draw_text', autospec=True) as relative_draw:
            render_folder_png(state, self.folder, highlight_player_id=self.ids[0],
                              highlight_session_id=state['session']['id'], mogi_png_graph='relative')
        relative_labels = [call.args[2] for call in relative_draw.call_args_list]
        self.assertIn('Player points above/below average', relative_labels)
        self.assertIn('0 · average', relative_labels)
        self.assertNotIn('Cumulative player net points', relative_labels)
        standings = 'FFA player standings · Net points'
        self.assertEqual(labels[labels.index(standings):], relative_labels[relative_labels.index(standings):])
        for call in draw.call_args_list:
            if '選手' in call.args[2]:
                self.assertNotEqual(call.kwargs['font'].getname()[0], 'Racers Delight')
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.width, 1100)
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw:
            render_folder_png(state, self.folder, highlight_player_id=self.ids[0],
                              highlight_session_id='another-session')
        self.assertNotIn('Place 1', [call.args[2] for call in draw.call_args_list])

    def test_unloaded_folder_format_wins_over_active_session(self):
        snapshot = self.snapshot()
        snapshot['format'] = snapshot['session']['format'] = 'teams'
        self.assertEqual(folder_report(snapshot, self.folder)['format'], 'ffa')
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw:
            render_folder_png(snapshot, self.folder)
        self.assertIn('Cumulative player net points', [call.args[2] for call in draw.call_args_list])
        snapshot['folders'][0]['format'] = 'teams'
        snapshot['format'] = snapshot['session']['format'] = 'ffa'
        self.assertEqual(folder_report(snapshot, self.folder)['format'], 'teams')
