"""First reviewed roster grouping, without changing identity or scoring rules."""

import unittest

from mkw_tracker.store import TrackerStore


class InitialTeamTests(unittest.TestCase):
    def setUp(self):
        self.store = TrackerStore(':memory:')
        self.store.create_race('first', 'Course')
        self.names = ['Alice', 'amy', 'Bob', 'ben', 'Émile', 'émilie',
                      '選手一', '選手二', '☆Star', '7Seven', 'Charlie', 'chris']
        self.choices = [{'name': name} for name in self.names]

    def tearDown(self):
        self.store.close()

    def test_first_confirmation_groups_reviewed_initials_and_remains_editable(self):
        pending = self.store.snapshot()
        self.assertEqual(pending['players'], [])
        self.assertEqual(pending['teams'], [])
        self.assertEqual(pending['room']['points'], 0)
        self.store.review_result('first', self.choices)
        state = self.store.snapshot()
        teams = {team['id']: team['name'] for team in state['teams']}
        self.assertEqual(list(teams.values()), ['A', 'B', 'É', '選', '☆', '7', 'C'])
        self.assertEqual([teams.get(p['team_id']) for p in state['players']],
                         ['A', 'A', 'B', 'B', 'É', 'É', '選', '選', '☆', '7', 'C', 'C'])
        self.assertEqual([p['name'] for p in state['players']], self.names)
        self.assertEqual(state['room']['points'], 82)
        self.assertEqual(state['teams'][0]['points'], 27)
        first = state['players'][0]
        self.store.update_player(first['id'], first['name'], None)
        self.store.rename_team(state['teams'][0]['id'], 'Custom')
        self.store.create_race('second', 'Next')
        self.store.review_result('second', [{'player_id': p['id']} for p in state['players']])
        changed = self.store.snapshot()
        self.assertIsNone(changed['players'][0]['team_id'])
        self.assertEqual(changed['teams'][0]['name'], 'Custom')
        self.assertEqual(changed['room']['points'], 164)

    def test_symbol_digit_emoji_and_japanese_initials_share_teams(self):
        names = ['☆Star', '☆Moon', '7Seven', '7Eight', '!Bang', '!Boom',
                 '😀One', '😀Two', 'あ一', 'あ二', 'aOne', 'AOther']
        self.store.review_result('first', [{'name': name} for name in names])
        state = self.store.snapshot()
        teams = {team['id']: team['name'] for team in state['teams']}
        self.assertEqual(list(teams.values()), ['☆', '7', '!', '😀', 'あ', 'A'])
        self.assertEqual([teams[player['team_id']] for player in state['players']],
                         ['☆', '☆', '7', '7', '!', '!', '😀', '😀', 'あ', 'あ', 'A', 'A'])
        self.assertEqual([player['name'] for player in state['players']], names)
        self.assertEqual(len({player['id'] for player in state['players']}), 12)
        self.assertEqual(sum(team['points'] for team in state['teams']), 82)

    def test_existing_manual_or_deleted_setup_disables_grouping(self):
        for setup in ('player', 'team', 'deleted_player', 'deleted_team', 'assigned_player'):
            with self.subTest(setup=setup):
                self.store.reset(self.store.snapshot()['session']['id'])
                self.store.create_race(setup, 'Course')
                if 'player' in setup:
                    player = self.store.add_player('Alice')
                    if setup == 'deleted_player':
                        self.store.delete_player(player['id'])
                    if setup == 'assigned_player':
                        team = self.store.create_team('Manual')
                        self.store.update_player(player['id'], player['name'], team['id'])
                else:
                    team = self.store.create_team('Manual')
                    if setup == 'deleted_team':
                        self.store.delete_team(team['id'])
                before = self.store.snapshot()
                self.store.review_result(setup, self.choices)
                after = self.store.snapshot()
                self.assertEqual(after['teams'], before['teams'])
                self.assertTrue(all(p['team_id'] is None for p in after['players'][-12:]))
                if setup == 'assigned_player':
                    self.assertEqual(after['players'][0]['team_id'], team['id'])

    def test_later_new_names_do_not_create_more_teams(self):
        self.store.review_result('first', self.choices)
        self.store.create_race('second', 'Next')
        self.store.review_result('second', [{'name': f'Zed {i}'} for i in range(12)])
        state = self.store.snapshot()
        self.assertEqual(len(state['teams']), 7)
        self.assertTrue(all(p['team_id'] is None for p in state['players'][12:]))

    def test_invalid_identity_rolls_back_generated_teams(self):
        before = self.store.snapshot()
        with self.assertRaises(KeyError):
            self.store.review_result('first', self.choices[:11] + [{'player_id': 'missing'}])
        self.assertEqual(self.store.snapshot(), before)

    def test_archived_first_review_groups_only_its_own_session(self):
        original = self.store.snapshot()['session']
        current = self.store.reset(original['id'])
        manual = self.store.create_team('Current manual team')
        self.store.review_result('first', self.choices)
        state = self.store.snapshot()
        self.assertEqual(state['session']['id'], current['id'])
        self.assertEqual([t['id'] for t in state['teams']], [manual['id']])
        self.assertEqual(state['players'], [])
        self.assertEqual(state['room']['points'], 0)
        self.assertEqual(len(state['archived_teams']), 7)
        self.assertTrue(all(t['session_id'] == original['id'] for t in state['archived_teams']))


if __name__ == '__main__':
    unittest.main()
