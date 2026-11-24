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

    def test_public_conkdor_image_is_fixed_across_modes_and_local_themes(self):
        snapshot = self.snapshot()
        snapshot['ww'] = {'last_10_change': None}
        snapshot['theme'] = {'icon_url': '/api/themes/private/icon.png', 'name': 'Private theme'}
        for race in snapshot['races']:
            race['ww_result'] = None
        images = []
        for mode in ('mogi', 'ffa', 'ww'):
            activity = build_activity(mode, snapshot, {})
            assets = activity['assets']
            self.assertEqual(assets['large_image'],
                             'https://mario.wiki.gallery/images/5/5d/MK_World_map_Conkdor.png')
            self.assertEqual(assets['large_text'], 'Conkdor')
            self.assertNotIn('private', str(activity).lower())
            images.append(assets['large_image'])
        self.assertEqual(len(set(images)), 1)
        # Callers cannot mutate the next payload's asset dictionary.
        activity['assets']['large_image'] = 'changed'
        self.assertEqual(build_activity('mogi', snapshot, {})['assets']['large_image'], images[0])

    def test_team_ties_and_pending(self):
        result = build_activity('mogi', self.snapshot(), {'highlight_team_id': 't'})
        self.assertEqual(result['details'], 'Mogi Teams · 1 race')
        self.assertEqual(result['state'], 'Team Position #1 · 30 pts · Leading')
        snapshot = self.snapshot()
        snapshot['teams'].append({'id': 'tie', 'name': 'Tie', 'points': 30})
        self.assertEqual(build_activity('mogi', snapshot, {'highlight_team_id': 't'})['state'],
                         'Team Position #1 · 30 pts · Leading')

    def test_team_gap_uses_net_points_and_competition_positions(self):
        snapshot = self.snapshot()
        snapshot['teams'] += [{'id': 'first', 'name': 'First', 'points': 60},
                              {'id': 'tied', 'name': 'Tied', 'points': 30}]
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertEqual(result['state'], 'Team Position #2 · 30 pts · 30 pts to #1')
        snapshot['teams'][0]['points'] = -10
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertEqual(result['state'], 'Team Position #3 · -10 pts · 40 pts to #2')
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
        snapshot['teams'][0]['name'] = 'PrivateTeam日本語' * 100
        result = build_activity('mogi', snapshot, {'highlight_team_id': 't'})
        self.assertNotIn('PrivateTeam', result['state'])
        self.assertTrue(result['state'].startswith('Team Position #'))
        self.assertNotIn('members', result['state'])
        self.assertIn('30 pts', result['state'])
        for text in (result['details'], result['state']):
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
