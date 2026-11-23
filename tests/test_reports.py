from contextlib import closing
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.courses import COURSES
from mkw_tracker.reports import (folder_report, render_folder_png, team_standings_text,
                                 team_standings_markdown, cumulative_team_series, scaled_bounds)
from mkw_tracker.store import TrackerStore
from mkw_tracker.track_icons import COURSE_ICONS, icon_filename


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'store.sqlite'
        self.store = TrackerStore(self.path)
        self.ids = [self.store.add_player(f'選手☆ {i}')['id'] for i in range(12)]
        self.folder = self.store.snapshot()['session']['folder_id']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def score(self, race='race', course='Acorn Heights'):
        self.store.create_race(race, course)
        self.store.submit_result(race, self.ids)

    def test_lounge_validation_clear_and_rename_preservation(self):
        value = self.store.update_folder(self.folder, lounge_id='1146183')
        self.assertEqual(value['lounge_id'], '1146183')
        self.assertEqual(self.store.rename_folder(self.folder, 'Room')['lounge_id'], '1146183')
        self.assertEqual(self.store.update_folder(self.folder, lounge_id=None)['name'], 'Room')
        self.assertIsNone(self.store.update_folder(self.folder, lounge_id='')['lounge_id'])
        for invalid in ('https://example.com', '１２', '-1', '1/2', '1' * 33, 123, False):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.update_folder(self.folder, name='Bad', lounge_id=invalid)
            self.assertEqual(self.store.snapshot()['folders'][0]['name'], 'Room')

    def test_existing_folder_schema_migration_is_idempotent(self):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE history_folders DROP COLUMN lounge_id')
        self.store = TrackerStore(self.path)
        self.assertIsNone(self.store.snapshot()['folders'][0]['lounge_id'])
        self.store.update_folder(self.folder, lounge_id='00042')
        state = self.store.snapshot()
        self.store.close()
        self.store = TrackerStore(self.path)
        self.assertEqual(state, self.store.snapshot())

    def test_room_counts_removed_players_and_latest_revision_only(self):
        self.score()
        self.store.correct_result('race', list(reversed(self.ids)))
        self.store.delete_player(self.ids[0])
        self.store.create_race('pending', 'Rainbow Road')
        state = self.store.snapshot()
        self.assertEqual({k: state['room'][k] for k in ('points', 'maximum_points', 'scored_races', 'expected_races')},
                         {'points': 82, 'maximum_points': 984, 'scored_races': 1, 'expected_races': 12})
        self.assertIsNone(state['races'][0]['icon_url'])
        self.store.reset(state['session']['id'])
        self.assertEqual(self.store.snapshot()['room']['points'], 0)

    def test_folder_report_revisions_pending_move_reset_and_unassigned(self):
        team = self.store.create_team('Team ◇')
        self.store.update_player(self.ids[0], '選手☆', team['id'])
        self.score()
        self.store.correct_result('race', list(reversed(self.ids)))
        self.store.create_race('pending', 'Rainbow Road')
        report = folder_report(self.store.snapshot(), self.folder)
        self.assertEqual((report['points'], report['scored_races'], report['pending_races']), (82, 1, 1))
        player = next(p for p in report['players'] if p['id'] == self.ids[0])
        self.assertEqual(player['races'], [{'position': 12, 'points': 1, 'penalty_points': 0, 'net_points': 1}, None])
        self.assertEqual([(t['name'], t['points']) for t in report['teams']], [('Unassigned', 81), ('Team ◇', 1)])
        self.assertEqual(report['teams'][1]['gap_above'], 80)
        self.assertTrue(all(t['races'][1] is None for t in report['teams']))
        self.store.reset(self.store.snapshot()['session']['id'])
        self.assertEqual(folder_report(self.store.snapshot(), self.folder), report)
        other = self.store.create_folder('Other')
        with self.assertRaisesRegex(ValueError, 'independent sessions'):
            self.store.move_race('race', other['id'])
        self.assertEqual(folder_report(self.store.snapshot(), self.folder)['points'], 82)
        self.assertEqual(folder_report(self.store.snapshot(), other['id'])['points'], 0)
        with self.assertRaises(KeyError):
            folder_report(self.store.snapshot(), 'missing')

    def test_report_active_roster_preserves_historical_room_totals_when_unloaded(self):
        team = self.store.create_team('Team')
        empty = self.store.create_team('Empty')
        for player_id in self.ids[:2]:
            self.store.update_player(player_id, player_id, team['id'])
        self.score()
        self.store.add_penalty('race', self.ids[0], 4)
        self.store.add_penalty('race', self.ids[1], 2)
        self.store.delete_player(self.ids[0])
        newcomer = self.store.add_player('New player')
        self.store.update_player(newcomer['id'], newcomer['name'], team['id'])
        state = self.store.snapshot()
        report = folder_report(state, self.folder)
        self.assertEqual({p['id']: p['points'] for p in report['players']},
                         {p['id']: p['points'] for p in state['players']})
        teams = {t['id']: t for t in report['teams']}
        self.assertEqual(teams[team['id']]['points'], 10)
        self.assertEqual(teams[empty['id']]['points'], 0)
        self.assertEqual(teams[empty['id']]['races'], [0])
        self.assertIn(newcomer['id'], {p['id'] for p in report['players']})
        self.assertEqual((report['gross_points'], report['penalty_points'], report['net_points']),
                         (82, 6, 76))
        self.assertEqual(len(state['races'][0]['placements']), 12)
        self.store.delete_team(team['id'])
        report = folder_report(self.store.snapshot(), self.folder)
        self.assertNotIn(team['id'], {t['id'] for t in report['teams']})
        self.assertEqual(next(t['points'] for t in report['teams'] if t['id'] is None), 65)
        self.store.reset(state['session']['id'])
        self.assertEqual(folder_report(self.store.snapshot(), self.folder), report)

    def test_text_deterministic_ties_and_room_total(self):
        self.store.create_team('Beta')
        self.store.create_team('Alpha')
        self.score()
        text = team_standings_text(self.store.snapshot())
        self.assertIn('1. Alpha | 0 pts\n1. Beta | 0 pts | 0', text)
        self.assertTrue(text.endswith('Room: 82/984 pts | 1/12 races\n'))
        self.assertEqual(text, team_standings_text(self.store.snapshot()))

    def test_saved_order_drives_png_labels_placements_and_cumulative_points(self):
        from mkw_tracker.report_fonts import draw_text

        team = self.store.create_team('Solo')
        self.store.update_player(self.ids[0], 'Driver', team['id'])
        self.score('a', 'Alpha')
        self.store.create_race('b', 'Bravo')
        self.store.submit_result('b', list(reversed(self.ids)))
        self.store.reorder_history(self.folder, ['b', 'a'], ['a', 'b'])
        state = self.store.snapshot()
        # Races may be created seconds or days apart. Don't accidentally test
        # only the stable-sort tie from creating both during the same second.
        for race in state['races']:
            race['created_at'] = '2020-01-01' if race['id'] == 'a' else '2020-01-02'
        report = folder_report(state, self.folder)
        member = next(p for p in report['players'] if p['id'] == self.ids[0])
        self.assertEqual([r['position'] for r in member['races']], [12, 1])
        series = next(t for t in cumulative_team_series(report) if t['id'] == team['id'])
        self.assertEqual(series['values'], [1, 16])
        with patch('mkw_tracker.reports._draw_text', wraps=draw_text) as draw:
            data = render_folder_png(state, self.folder, highlight_team_id=team['id'],
                                     highlight_session_id=state['session']['id'])
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertEqual([s for s in labels if s.startswith(('R1 ·', 'R2 ·'))],
                         ['R1 · Bravo', 'R2 · Alpha'] * 2)
        self.assertEqual([s for s in labels if s.startswith('Place ')], ['Place 12', 'Place 1'])
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_course_names_have_no_bundled_artwork(self):
        self.assertEqual(COURSE_ICONS, {})
        for course in COURSES:
            self.assertIsNone(icon_filename(course))
        self.assertIsNone(icon_filename('Unknown Acorn Heights'))

    def test_penalties_net_totals_keep_chart_positions(self):
        self.score()
        team = self.store.create_team('Team')
        self.store.update_player(self.ids[0], 'Leader', team['id'])
        state = self.store.snapshot()
        state['races'][0]['penalties'] = [
            {'id': 'a', 'player_id': self.ids[0], 'points': 4, 'reason': 'Test'},
            {'id': 'b', 'player_id': self.ids[0], 'points': 2, 'reason': 'Second'}]
        report = folder_report(state, self.folder)
        self.assertEqual((report['gross_points'], report['penalty_points'], report['net_points']), (82, 6, 76))
        player = next(row for row in report['players'] if row['id'] == self.ids[0])
        self.assertEqual((player['gross_points'], player['points']), (15, 9))
        self.assertEqual(player['races'][0], {'position': 1, 'points': 15, 'penalty_points': 6, 'net_points': 9})
        team_series = next(t for t in cumulative_team_series(report) if t['id'] == team['id'])
        self.assertEqual(team_series['values'], [9])
        self.assertEqual(next(t for t in report['teams'] if t['id'] == team['id'])['points'], 9)

    def test_cumulative_team_prefixes_penalties_absence_and_pending(self):
        report = {'races': [{'revision': 1}] * 3, 'teams': [
            {'id': 'a', 'name': 'Team A', 'races': [15, 12, 10]},
            {'id': 'b', 'name': 'Team B', 'races': [None, 5, -12]}]}
        self.assertEqual([s['values'] for s in cumulative_team_series(report)],
                         [[15, 27, 37], [0, 5, -7]])
        report['races'][1] = {'revision': 0}
        self.assertEqual([s['values'] for s in cumulative_team_series(report)],
                         [[15, None, None], [0, None, None]])
        report['races'][0] = {'revision': 0}
        self.assertEqual(cumulative_team_series(report)[0]['values'], [None, None, None])
        self.assertEqual(cumulative_team_series({'races': [], 'teams': []}), [])

    def test_tied_team_series_keep_report_order_and_all_points(self):
        report = {'races': [{'revision': 1}, {'revision': 1}], 'teams': [
            {'id': 'b', 'name': 'Beta', 'races': [10, 15]},
            {'id': 'a', 'name': 'Alpha', 'races': [10, 15]}]}
        series = cumulative_team_series(report)
        self.assertEqual([team['id'] for team in series], ['b', 'a'])
        self.assertEqual([team['values'] for team in series], [[10, 25], [10, 25]])

    def test_relative_team_series_uses_current_roster_and_unrounded_gross_share(self):
        team = self.store.create_team('Trio')['id']
        empty = self.store.create_team('No members')['id']
        for i in range(3):
            self.store.update_player(self.ids[i], f'Trio {i}', team)
        self.score('one')
        self.score('two')
        self.store.correct_result('two', list(reversed(self.ids)))
        self.store.add_penalty('two', self.ids[0], 5, 'Linked deduction')
        self.store.create_race('pending', 'Rainbow Road')
        self.score('four')

        def values():
            report = folder_report(self.store.snapshot(), self.folder)
            return {row['id']: row['values'] for row in cumulative_team_series(report, relative=True)}

        # The trio earns 37 then 6 with a 5-point deduction; its pace is 20.5/race.
        self.assertEqual(values(), {team: [16.5, -3, None, None], empty: [0, 0, None, None],
                                    None: [-16.5, -2, None, None]})
        self.store.update_player(self.ids[2], 'Not on trio anymore', None)
        self.assertAlmostEqual(values()[team][1], 25 - 2 * 82 * 2 / 12)

    def test_relative_6v6_preserves_gaps_and_shows_subpar_race_falling(self):
        report = {'players': [{'team_id': 'a'}] * 6 + [{'team_id': 'b'}] * 6,
                  'races': [{'revision': 1}] * 4, 'teams': [
                      {'id': 'a', 'name': 'Team A', 'races': [48, 38, 35, 49]},
                      {'id': 'b', 'name': 'Team B', 'races': [34, 44, 47, 33]}]}
        self.assertEqual([s['values'] for s in cumulative_team_series(report, relative=True)],
                         [[7, 4, -2, 6], [-7, -4, 2, -6]])

    def test_scaled_bounds_zero_negative_and_large_totals(self):
        for values in ([], [None], [0, 0], [15, 27, 37], [-22, -5], [-123, 1340], [1340]):
            with self.subTest(values=values):
                lower, upper, ticks = scaled_bounds(values)
                self.assertLess(lower, upper)
                self.assertLessEqual(lower, 0)
                self.assertGreaterEqual(upper, 0)
                self.assertEqual((ticks[0], ticks[-1]), (lower, upper))
                self.assertTrue(all(lower <= value <= upper for value in values if value is not None))
                self.assertTrue(all(a < b for a, b in zip(ticks, ticks[1:])))
                self.assertLessEqual(len(ticks), 10)
        self.assertEqual(scaled_bounds([0]), (0, 5, [0, 1, 2, 3, 4, 5]))
        for values in ([], [None], [0, 0]):
            self.assertEqual(scaled_bounds(values, center_flat=True), (-1, 1, [-1, 0, 1]))

    def test_cumulative_recalculates_latest_revision_and_deleted_race(self):
        team = self.store.create_team('Pair')
        self.store.update_player(self.ids[0], 'One', team['id'])
        self.score('r1')
        self.score('r2')
        self.score('r3')

        def values(state):
            report = folder_report(state, self.folder)
            return next(s['values'] for s in cumulative_team_series(report) if s['id'] == team['id'])

        self.assertEqual(values(self.store.snapshot()), [15, 30, 45])
        self.store.correct_result('r2', list(reversed(self.ids)))
        state = self.store.snapshot()
        self.assertEqual(values(state), [15, 16, 31])
        self.store.delete_race('r2')
        state = self.store.snapshot()
        self.assertEqual(values(state), [15, 30])
        # Stable created-order ties keep insertion order, not ID or course order.
        for race in state['races']:
            race['created_at'] = '2026-01-01'
        self.assertEqual([r['id'] for r in folder_report(state, self.folder)['races']], ['r1', 'r3'])

    def test_markdown_escapes_names_and_uses_short_negative_gaps(self):
        state = self.store.snapshot()
        state['teams'] = [{'id': 'a', 'name': 'A|`x`\n*B*', 'points': 9},
                          {'id': 'b', 'name': 'Next', 'points': 5}]
        state['room'].update(points=82, net_points=78, penalty_points=4)
        plain = team_standings_text(state)
        markdown = team_standings_markdown(state)
        self.assertIn('2. Next | 5 pts | -4', plain)
        self.assertNotIn('behind above', plain)
        self.assertIn('A\\|\\`x\\` \\*B\\*', markdown)
        self.assertIn('| 2 | Next | 5 | -4 |', markdown)
        self.assertIn('Room: 82/984', markdown)
        self.assertIn('Net 78', markdown)

    def test_twelve_race_compact_chart_axes_course_labels_and_legend(self):
        from PIL import Image
        for index in range(12):
            self.score(f'r{index}', COURSES[index])
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            payload = render_folder_png(self.store.snapshot(), self.folder)
        calls = [(call.args[1], call.args[2]) for call in draw_text.call_args_list]
        axes = {int(label): point for point, label in calls if label.lstrip('-').isdigit()}
        self.assertIn(0, axes)
        self.assertGreater(max(axes), 984)
        self.assertLess(axes[max(axes)][1], axes[0][1])
        self.assertTrue(any('Cumulative team net points' in label for _, label in calls))
        self.assertTrue(any('Team standings' in label for _, label in calls))
        self.assertTrue(any('R12' in label for _, label in calls))
        self.assertTrue(any('Players' in label for _, label in calls))
        with Image.open(BytesIO(payload)) as image:
            self.assertLessEqual(image.width, 1400)
            self.assertLessEqual(image.height, 1000)

    def test_relative_team_png_switches_one_chart_only(self):
        team = self.store.create_team('Solo')['id']
        self.store.update_player(self.ids[0], 'Leader', team)
        self.score()
        state = self.store.snapshot()

        def labels(mode):
            with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
                render_folder_png(state, self.folder, mogi_png_graph=mode)
            return [call.args[2] for call in draw_text.call_args_list]

        ordinary, relative = labels('cumulative'), labels('relative')
        self.assertIn('Cumulative team net points', ordinary)
        self.assertNotIn('Cumulative team net points', relative)
        self.assertIn('Team points above/below average', relative)
        self.assertIn('0 · average', relative)
        self.assertTrue(any(label.startswith('+') for label in relative))
        self.assertTrue(any(label.startswith('-') and label[1:].isdigit() for label in relative))
        standings = 'Team standings · Players ranked overall · Net points'
        self.assertEqual(ordinary[ordinary.index(standings):], relative[relative.index(standings):])

    def test_png_grouped_members_overall_ranks_team_totals_and_theme(self):
        from PIL import Image
        teams = [self.store.create_team(name) for name in ('Team Red', 'Team Green')]
        for index in range(4):
            self.store.update_player(self.ids[index], f'Racer {index}', teams[index // 2]['id'])
        self.score()
        state = self.store.snapshot()
        state['races'][0]['penalties'] = [
            {'id': 'penalty', 'player_id': self.ids[0], 'points': 20, 'reason': 'Test'}]
        self.store.update_folder(self.folder, name='Cup final', lounge_id='1146183')
        state['folders'] = self.store.snapshot()['folders']
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            payload = render_folder_png(state, self.folder)
        labels = [call.args[2] for call in draw_text.call_args_list]
        self.assertIn('Cup final | Lounge ID: 1146183', labels)
        self.assertIn('82 / 984 pts', labels)
        self.assertIn('Net 62 pts after 20 pts in deductions.', labels)
        self.assertIn('Team Red', labels)
        self.assertIn('Team Green', labels)
        self.assertIn('Unassigned', labels)
        for player in folder_report(state, self.folder)['players']:
            self.assertIn(player['name'], labels)
            self.assertIn(f"#{player['rank']}", labels)
            self.assertIn(f"{player['net_points']} pts", labels)
        self.assertIn('-5 pts', labels)
        self.assertNotIn('Teams use roster assignments', ' '.join(labels))
        self.assertNotIn('confirmed /', ' '.join(labels))
        self.assertIn('19 pts', labels)  # Team Green: third + fourth.
        self.assertIn('7 pts', labels)   # Team Red: first + second - penalty.
        alternate = render_folder_png(state, self.folder, theme='luigi')
        with Image.open(BytesIO(payload)) as first, Image.open(BytesIO(alternate)) as second:
            self.assertEqual(first.size, second.size)
            self.assertNotEqual(first.getpixel((0, 0)), second.getpixel((0, 0)))
            self.assertNotEqual(first.getpixel((0, first.height - 1)),
                                second.getpixel((0, second.height - 1)))

    def test_png_tied_teams_use_shared_graph_and_card_colors(self):
        from mkw_tracker.themes import resolve_theme, team_color_map
        red = self.store.create_team('Red')
        blue = self.store.create_team('Blue')
        self.store.update_player(self.ids[0], 'Red racer', red['id'])
        self.store.update_player(self.ids[1], 'Blue racer', blue['id'])
        self.score()
        state = self.store.snapshot()
        # Tie Red's first-place points to Blue's second-place points.
        state['races'][0]['penalties'] = [
            {'id': 'tie', 'player_id': self.ids[0], 'points': 3, 'reason': 'Tie'}]
        self.store.create_race('pending', 'Rainbow Road')
        state['races'].append(self.store.snapshot()['races'][-1])
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text, \
                patch('PIL.ImageDraw.ImageDraw.ellipse', autospec=True) as dots, \
                patch('PIL.ImageDraw.ImageDraw.rounded_rectangle', autospec=True) as rectangles:
            render_folder_png(state, self.folder, 'peach')
        labels = [call.args[2] for call in draw_text.call_args_list]
        self.assertIn('Pending race stops all later cumulative totals.', labels)
        self.assertEqual(labels.count('#2'), 2)  # Two tied team ranks; players tie for first.
        color_map = team_color_map(state, state['session']['id'], resolve_theme('peach'))
        expected = [color_map[team['id']] for team in folder_report(state, self.folder)['teams']]
        graph_colors = [call.kwargs['fill'] for call in dots.call_args_list]
        self.assertEqual(graph_colors, expected)
        card_colors = [call.kwargs['fill'] for call in rectangles.call_args_list
                       if call.kwargs['radius'] == 2]
        self.assertEqual(card_colors, graph_colors)

    def test_png_gaps_show_above_and_leader_including_ties_and_negative_totals(self):
        for index in range(3):
            team = self.store.create_team(f'Team {index}')
            self.store.update_player(self.ids[index], f'Racer {index}', team['id'])
        self.score()
        state = self.store.snapshot()
        state['races'][0]['placements'] = state['races'][0]['placements'][:3]
        state['players'] = state['players'][:3]

        def labels():
            with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
                render_folder_png(state, self.folder)
            return [call.args[2] for call in draw_text.call_args_list]

        displayed = labels()
        self.assertIn('Gap: above / leader', displayed)
        self.assertIn('-3 / -3', displayed)
        self.assertIn('-2 / -5', displayed)
        self.assertNotIn('to above', ' '.join(displayed))
        state['races'][0]['penalties'] = [
            {'id': 'tie', 'player_id': self.ids[0], 'points': 3, 'reason': 'Tie'},
            {'id': 'negative', 'player_id': self.ids[2], 'points': 25, 'reason': 'Negative'}]
        displayed = labels()
        self.assertIn('0 / 0', displayed)
        self.assertIn('-27 / -27', displayed)
        state['races'][0]['penalties'].append(
            {'id': 'drop', 'player_id': self.ids[1], 'points': 20, 'reason': 'Drop'})
        displayed = labels()
        self.assertIn('-20 / -20', displayed)
        self.assertIn('-7 / -27', displayed)

    def test_png_uploaded_portrait_preserves_transparency_and_reserves_title_space(self):
        from PIL import Image
        from mkw_tracker.themes import resolve_theme
        theme = resolve_theme('mario')
        portrait = Image.new('RGBA', (36, 48), (40, 140, 200, 128))
        content = BytesIO()
        portrait.save(content, format='PNG')
        theme.update(id='custom-' + '0' * 32, custom=True, icon_name=theme['name'],
                     icon_url='/api/themes/custom-' + '0' * 32 + '/icon.png?v=' + '0' * 16,
                     icon_png=content.getvalue())
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            payload = render_folder_png(self.store.snapshot(), self.folder, theme)
        title = next(call for call in draw_text.call_args_list
                     if call.args[2] == self.store.snapshot()['folders'][0]['name'])
        self.assertGreaterEqual(title.args[1][0], 104)
        with Image.open(BytesIO(theme['icon_png'])) as original, Image.open(BytesIO(payload)) as image:
            icon = original.convert('RGBA')
            icon.thumbnail((72, 72))
            x, y = 20 + (72 - icon.width) // 2, 12 + (72 - icon.height) // 2
            background = Image.new('RGB', icon.size, theme['tokens']['accent'])
            background.paste(icon, (0, 0), icon)
            self.assertEqual(image.crop((x, y, x + icon.width, y + icon.height)).tobytes(),
                             background.tobytes())

    def test_png_missing_character_portrait_is_graceful(self):
        from PIL import Image
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            payload = render_folder_png(self.store.snapshot(), self.folder)
        title = next(call for call in draw_text.call_args_list
                     if call.args[2] == self.store.snapshot()['folders'][0]['name'])
        self.assertEqual(title.args[1][0], 24)
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.width, 1100)

    def test_png_portrait_long_unicode_title_does_not_overlap_points_badge(self):
        state = self.store.snapshot()
        state['folders'][0]['name'] = '日本語の長い大会名 ' * 20
        from PIL import Image
        from mkw_tracker.themes import resolve_theme
        theme = resolve_theme('mario')
        icon = BytesIO()
        Image.new('RGBA', (64, 64), '#205080').save(icon, format='PNG')
        theme.update(id='custom-' + '0' * 32, custom=True, icon_name=theme['name'],
                     icon_url='/api/themes/custom-' + '0' * 32 + '/icon.png?v=' + '0' * 16,
                     icon_png=icon.getvalue())
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, theme)
        calls = draw_text.call_args_list
        end = next(index for index, call in enumerate(calls)
                   if call.args[2] == 'Cumulative team net points')
        header = calls[:end]
        badge = next(call for call in header if call.args[2] == '0 / 984 pts')
        self.assertGreater(len(header), 2)
        for call in header:
            if call is badge:
                continue
            x, _ = call.args[1]
            self.assertGreaterEqual(x, 108)
            self.assertLess(x + call.kwargs['font'].getlength(call.args[2]), badge.args[1][0])

    def test_png_team_colors_include_unscored_roster_and_ignore_overtakes(self):
        from mkw_tracker.themes import resolve_theme, team_color_map
        leader = self.store.create_team('Zulu')
        other = self.store.create_team('Beta')
        self.store.create_team('Alpha unscored')
        self.store.update_player(self.ids[0], 'Leader', leader['id'])
        self.store.update_player(self.ids[1], 'Other', other['id'])
        self.score()

        def drawn_colors():
            state = self.store.snapshot()
            report = folder_report(state, self.folder)
            color_map = team_color_map(state, state['session']['id'], resolve_theme('mario'))
            with patch('PIL.ImageDraw.ImageDraw.ellipse', autospec=True) as dots, \
                    patch('PIL.ImageDraw.ImageDraw.rounded_rectangle', autospec=True) as rectangles:
                render_folder_png(state, self.folder)
            expected = {team['id']: color_map[team['id']] for team in report['teams']}
            graph = {team['id']: call.kwargs['fill']
                     for team, call in zip(report['teams'], dots.call_args_list)}
            cards = [call.kwargs['fill'] for call in rectangles.call_args_list
                     if call.kwargs['radius'] == 2]
            self.assertEqual(graph, expected)
            self.assertEqual(cards, [expected[team['id']] for team in report['teams']])
            return expected

        before = drawn_colors()
        # A zero-scoring Alpha still occupies the first color across all views.
        palette = resolve_theme('mario')['team_colors']
        self.assertEqual(before[other['id']], palette[1])
        self.assertEqual(before[leader['id']], palette[2])
        self.assertEqual(before[None], palette[3])
        self.store.correct_result('race', list(reversed(self.ids)))
        self.assertEqual(drawn_colors(), before)

    def test_png_large_penalties_and_long_names_do_not_overlap(self):
        team = self.store.create_team('長いチーム名' * 20)
        for index in range(8):
            self.store.update_player(self.ids[index], ('選手名' * 45) + str(index), team['id'])
        self.score()
        for player_id in self.ids[:8]:
            self.store.add_penalty('race', player_id, 1000000, 'Maximum legal deduction')
        state = self.store.snapshot()
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, highlight_team_id=team['id'],
                              highlight_session_id=state['session']['id'])
        calls = draw_text.call_args_list
        start = next(index for index, call in enumerate(calls)
                     if call.args[2].startswith('Team standings'))
        bounds = []
        for call in calls[start:]:
            (x, y), label = call.args[1:3]
            left, top, right, bottom = call.kwargs['font'].getbbox(label)
            bounds.append((x + left, y + top, x + right, y + bottom, label))
        for index, first in enumerate(bounds):
            for second in bounds[index + 1:]:
                overlap = (max(first[0], second[0]) < min(first[2], second[2]) and
                           max(first[1], second[1]) < min(first[3], second[3]))
                self.assertFalse(overlap, (first, second))

    def test_png_empty_and_zero_point_teams_remain_readable(self):
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(self.store.snapshot(), self.folder)
        labels = [call.args[2] for call in draw_text.call_args_list]
        self.assertIn('No races in this folder.', labels)
        self.assertIn('Unassigned', labels)
        self.assertIn('選手☆ 0', labels)
        self.assertIn('0 / 984 pts', labels)
        team = self.store.create_team('Zero')
        self.store.update_player(self.ids[0], 'Zero racer', team['id'])
        self.score()
        state = self.store.snapshot()
        state['races'][0]['penalties'] = [
            {'id': 'zero', 'player_id': self.ids[0], 'points': 15, 'reason': 'Zero'}]
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, 'bowser')
        labels = [call.args[2] for call in draw_text.call_args_list]
        self.assertIn('Zero racer', labels)
        self.assertGreaterEqual(labels.count('0 pts'), 2)

    def test_png_highlighted_pair_positions_use_three_column_grid_and_course_text(self):
        from PIL import Image
        team = self.store.create_team('Pair ☆')
        for index in range(2):
            self.store.update_player(self.ids[index], f'選手◇ {index}', team['id'])
        for index in range(6):
            self.score(f'r{index}', COURSES[index])
        self.store.correct_result('r1', list(reversed(self.ids)))
        self.store.add_penalty('r0', self.ids[0], 20, 'Finishing place stays first')
        state = self.store.snapshot()
        plain = render_folder_png(state, self.folder)
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            payload = render_folder_png(state, self.folder, highlight_team_id=team['id'],
                                        highlight_session_id=state['session']['id'])
        calls = draw_text.call_args_list
        start = next(index for index, call in enumerate(calls)
                     if call.args[2] == 'Pair ☆ · Race finishing positions')
        grid = calls[start + 1:]
        labels = [call.args[2] for call in grid]
        headers = [call.args[1] for call in grid if call.args[2].startswith('R')]
        self.assertEqual(len(headers), 6)
        self.assertEqual(len({point[0] for point in headers}), 3)
        self.assertEqual(len({point[1] for point in headers}), 2)
        self.assertEqual(labels.count('選手◇ 0'), 6)
        self.assertEqual(labels.count('選手◇ 1'), 6)
        self.assertEqual(labels.count('Place 1'), 5)
        self.assertIn('Place 12', labels)
        self.assertIn('Place 11', labels)
        with Image.open(BytesIO(plain)) as original, Image.open(BytesIO(payload)) as image:
            self.assertEqual(original.width, image.width)
            self.assertLess(image.height - original.height, 400)

    def test_png_highlighted_roster_includes_unscored_players_and_marks_unknown_pending(self):
        team = self.store.create_team('Roster')
        for index in range(8):
            self.store.update_player(self.ids[index], f'選手☆ {index}', team['id'])
        self.score()
        self.store.create_race('pending', 'Unrecognized course')
        state = self.store.snapshot()
        state['races'][0]['placements'] = state['races'][0]['placements'][1:]
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, highlight_team_id=team['id'],
                              highlight_session_id=state['session']['id'])
        labels = [call.args[2] for call in draw_text.call_args_list]
        grid = labels[labels.index('Roster · Race finishing positions') + 1:]
        for index in range(8):
            self.assertEqual(grid.count(f'選手☆ {index}'), 2)
        self.assertEqual(grid.count('Unknown'), 1)
        self.assertEqual(grid.count('Pending'), 8)
        self.assertNotIn('Place 1', grid)

    def test_png_highlight_requires_explicit_matching_session_and_active_team(self):
        team = self.store.create_team('Selected')
        self.store.update_player(self.ids[0], 'Selected player', team['id'])
        self.score()
        state = self.store.snapshot()
        for team_id, session_id in ((None, state['session']['id']), (team['id'], None),
                                    (team['id'], 'other-session'), ('missing', state['session']['id'])):
            with self.subTest(team_id=team_id, session_id=session_id), \
                    patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
                render_folder_png(state, self.folder, highlight_team_id=team_id,
                                  highlight_session_id=session_id)
                self.assertFalse(any('Race finishing positions' in call.args[2]
                                     for call in draw_text.call_args_list))
        state['archived_teams'].append(dict(state['teams'][0], id='other-team', session_id='other-session'))
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, highlight_team_id='other-team',
                              highlight_session_id=state['session']['id'])
        self.assertFalse(any('Race finishing positions' in call.args[2]
                             for call in draw_text.call_args_list))
        self.store.delete_team(team['id'])
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(self.store.snapshot(), self.folder, highlight_team_id=team['id'],
                              highlight_session_id=state['session']['id'])
        self.assertFalse(any('Race finishing positions' in call.args[2]
                             for call in draw_text.call_args_list))

    def test_png_highlighted_archived_folder_uses_its_current_roster(self):
        team = self.store.create_team('Archived session team')
        self.store.update_player(self.ids[0], 'Current member ☆', team['id'])
        self.store.update_player(self.ids[1], 'Removed member', team['id'])
        self.score()
        self.store.delete_player(self.ids[1])
        session_id = self.store.snapshot()['session']['id']
        self.store.create_session('Other session', session_id)
        state = self.store.snapshot()
        with patch('mkw_tracker.reports._draw_text', autospec=True) as draw_text:
            render_folder_png(state, self.folder, highlight_team_id=team['id'],
                              highlight_session_id=session_id)
        labels = [call.args[2] for call in draw_text.call_args_list]
        grid = labels[labels.index('Archived session team · Race finishing positions') + 1:]
        self.assertIn('Current member ☆', grid)
        self.assertNotIn('Removed member', grid)
        self.assertIn('Place 1', grid)

    def test_png_unicode_signature_dimensions_and_size_limit(self):
        from PIL import Image
        self.score()
        self.store.update_folder(self.folder, name='日本語の大会 ◇ ☆', lounge_id='1146183')
        self.store.create_race('pending', 'Rainbow Road')
        payload = render_folder_png(self.store.snapshot(), self.folder)
        self.assertTrue(payload.startswith(b'\x89PNG\r\n\x1a\n'))
        with Image.open(BytesIO(payload)) as png:
            self.assertEqual(png.width, 1100)
            self.assertGreater(png.height, 450)
        state = self.store.snapshot()
        state['races'] = [dict(state['races'][0], id=f'r{i}') for i in range(156)]
        with self.assertRaisesRegex(ValueError, 'too large'):
            render_folder_png(state, self.folder)


if __name__ == '__main__':
    unittest.main()
