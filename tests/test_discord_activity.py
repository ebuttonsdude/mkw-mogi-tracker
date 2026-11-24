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
        self.assertIn('1 races scored', result['details'])
        self.assertIn('Team #1', result['details'])
        self.assertEqual(result['state'], 'Alice 15pts #1 | Bob 15pts #1')

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
        self.assertIn('members', result['state'])
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
