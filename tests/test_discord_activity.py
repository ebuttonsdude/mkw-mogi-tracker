import unittest

from mkw_tracker.discord_activity import build_activity


class DiscordActivityTests(unittest.TestCase):
    def snapshot(self):
        return {'session': {'id': 's'}, 'races': [
            {'session_id': 's', 'revision': 1}, {'session_id': 's', 'revision': None},
            {'session_id': 'other', 'revision': 1}],
            'players': [{'id': 'a', 'name': 'Alice', 'points': 15, 'team_id': 't'},
                        {'id': 'b', 'name': 'Bob', 'points': 15, 'team_id': 't'}],
            'teams': [{'id': 't', 'name': 'Team', 'points': 30}]}

    def test_team_ties_and_pending(self):
        result = build_activity('mogi', self.snapshot(), {'highlight_team_id': 't'})
        self.assertEqual(result['details'], 'Mogi Teams · 1 race')
        self.assertEqual(result['state'], 'Team #1 · 30 pts · Leading')
        snapshot = self.snapshot()
        snapshot['teams'].append({'id': 'tie', 'name': 'Tie', 'points': 30})
        self.assertEqual(build_activity('mogi', snapshot, {'highlight_team_id': 't'})['state'],
                         'Team #1 · 30 pts · Leading')

    def test_team_gap_uses_net_points_and_competition_positions(self):
        snapshot = self.snapshot()
        snapshot['teams'] += [{'id': 'first', 'name': 'First', 'points': 60},
                              {'id': 'tied', 'name': 'Tied', 'points': 30}]
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertEqual(result['state'], 'Team #2 · 30 pts · -30 to #1')
        snapshot['teams'][0]['points'] = -10
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertEqual(result['state'], 'Team #3 · -10 pts · -40 to #2')
        self.assertEqual(build_activity('mogi', snapshot, {})['state'], 'No team highlighted')

    def test_ffa_and_missing_identity(self):
        result = build_activity('ffa', self.snapshot(), {'highlight_player_id': 'a'}, lounge_rank='Gold')
        self.assertIn('Lounge: Gold', result['state'])
        self.assertEqual(build_activity('ffa', self.snapshot(), {})['state'], 'No player highlighted')

    def test_unicode_limits_and_overflow(self):
        snapshot = self.snapshot()
        snapshot['players'] *= 6
        for p in snapshot['players']:
            p['name'] = '日本語' * 100
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        snapshot['teams'][0]['name'] = '日本語' * 100
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertNotIn('members', result['state'])
        self.assertIn('30 pts', result['state'])
        for text in result.values():
            self.assertLessEqual(len(text.encode('utf-8')), 128)

    def test_ww_saved_order_unknown_and_foreign_session(self):
        snapshot = self.snapshot()
        snapshot['ww'] = {'last_10_change': None}
        snapshot['races'] = [{'session_id': 's', 'ww_result': {'delta': -12}},
                             {'session_id': 'other', 'ww_result': {'delta': 900}},
                             {'session_id': 's', 'ww_result': None}]
        result = build_activity('ww', snapshot, {}, current_course='Rainbow Road')
        self.assertIn('Last 10 VR: unknown | Last: -12', result['details'])
        self.assertEqual(result['state'], 'Race 2 | Rainbow Road')
