"""Race-based overlay movement must survive polling, corrections and reloads."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.server import Application


class OverlayMovementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'scores.sqlite'
        self.app = Application(self.database, self.directory.name)
        self.ids = []
        self.teams = []
        for index in range(12):
            team = self.app.store.create_team(f'Team {index:02}')
            player = self.app.store.add_player(f'Player {index:02}')
            self.app.store.update_player(player['id'], player['name'], team['id'])
            self.ids.append(player['id'])
            self.teams.append(team['id'])

    def tearDown(self):
        self.app.close()
        self.directory.cleanup()

    def teams_by_id(self):
        return {team['id']: team for team in self.app.overlay_snapshot()['teams']}

    def score(self, race_id, order=None):
        self.app.store.create_race(race_id, 'Rainbow Road')
        self.app.store.submit_result(race_id, order or self.ids)

    def seed_movement(self):
        self.score('first')
        before = self.teams_by_id()
        self.score('second', self.ids[1:] + self.ids[:1])
        return before

    def test_first_race_has_no_previous_rank_or_movement(self):
        self.assertTrue(all(t['rank_change'] is None for t in self.teams_by_id().values()))
        self.score('first')
        self.assertTrue(all(t['previous_rank'] is None and t['rank_change'] is None
                            for t in self.teams_by_id().values()))

    def test_up_down_unchanged_and_dual_gaps(self):
        before = self.seed_movement()
        after = self.teams_by_id()
        self.assertEqual(after[self.teams[0]]['rank_change'], -4)
        self.assertEqual(after[self.teams[1]]['rank_change'], 1)
        self.assertEqual(after[self.teams[5]]['rank_change'], 0)
        for identifier, team in after.items():
            self.assertEqual(team['previous_rank'], before[identifier]['rank'])
            self.assertEqual(team['rank_change'], before[identifier]['rank'] - team['rank'])
        ordered = self.app.overlay_snapshot()['teams']
        self.assertEqual((ordered[1]['gap_above'], ordered[1]['gap_leader']), (5, 5))
        self.assertEqual((ordered[2]['gap_above'], ordered[2]['gap_leader']), (3, 8))
        self.assertEqual(ordered[0]['gap_leader'], 0)
        self.assertTrue(all(p['gap_leader'] >= 0 for p in self.app.overlay_snapshot()['players']))

    def test_pending_and_repeated_polls_do_not_reset_movement(self):
        self.seed_movement()
        before = self.teams_by_id()
        self.app.store.create_race('pending', 'Peach Stadium')
        self.app.store.update_race('second', 'Crown City')
        self.app.store.reorder_history(self.app.store.active_session()['folder_id'],
                                       ['pending', 'second', 'first'], ['first', 'second', 'pending'])
        self.assertEqual(self.teams_by_id(), before)
        self.assertEqual(self.teams_by_id(), before)

    def test_last_confirmed_not_last_created_race_is_baseline_even_with_same_timestamp(self):
        # Revision row order disambiguates equal clocks and delayed review.
        with patch('mkw_tracker.store._now', return_value='2026-01-01T00:00:00+00:00'):
            self.app.store.create_race('created-first', 'Rainbow Road')
            self.score('created-second')
            before = self.teams_by_id()
            self.app.store.submit_result('created-first', self.ids[1:] + self.ids[:1])
        after = self.teams_by_id()
        for identifier in self.teams:
            self.assertEqual(after[identifier]['previous_rank'], before[identifier]['rank'])

    def test_latest_penalty_counts_in_latest_race_contribution(self):
        before = self.seed_movement()
        self.app.store.add_penalty('second', self.ids[1], 100)
        after = self.teams_by_id()
        self.assertEqual(after[self.teams[1]]['rank'], 12)
        self.assertEqual(after[self.teams[1]]['previous_rank'], before[self.teams[1]]['rank'])
        self.assertEqual(after[self.teams[1]]['rank_change'], -10)
        self.assertEqual(after[self.teams[1]]['gap_leader'], 95)

    def test_corrections_recalculate_without_turning_old_race_into_latest(self):
        self.seed_movement()
        self.app.store.correct_result('first', self.ids[::-1])
        before = self.app.overlay_snapshot()
        # Second-race input remains the comparison; revised first-race scores
        # are the baseline, with competition ranks rather than alphabetic rows.
        expected = {player: 12 - index for index, player in enumerate(self.ids)}
        for team in before['teams']:
            member = team['players'][0]['id']
            self.assertEqual(team['previous_rank'], expected[member])
        self.app.store.correct_result('second', self.ids)
        for team in self.app.overlay_snapshot()['teams']:
            member = team['players'][0]['id']
            self.assertEqual(team['previous_rank'], expected[member])

    def test_ties_use_competition_rank(self):
        self.score('first')
        self.score('second', self.ids[::-1])
        teams = self.teams_by_id()
        # Positions 3..10 form tied groups; equal points share a rank.
        self.assertEqual(teams[self.teams[2]]['rank'], teams[self.teams[3]]['rank'])
        for team in teams.values():
            self.assertEqual(team['rank_change'], team['previous_rank'] - team['rank'])
        tied = [t for t in self.app.overlay_snapshot()['teams'] if t['gap_above'] == 0]
        self.assertTrue(tied)

    def test_roster_changes_archival_and_unassigned_use_same_roster_for_both_totals(self):
        self.seed_movement()
        self.app.store.delete_team(self.teams[0])
        self.app.store.delete_player(self.ids[2])
        self.app.store.update_player(self.ids[3], 'Player 03', self.teams[1])
        result = self.app.overlay_snapshot()
        unassigned = next(t for t in result['teams'] if t['id'] is None)
        self.assertEqual(unassigned['players'][0]['id'], self.ids[0])
        self.assertIsNotNone(unassigned['rank_change'])
        self.assertNotIn(self.ids[2], [p['id'] for p in result['players']])
        self.assertEqual(sum(t['points'] for t in result['teams']), sum(p['points'] for p in result['players']))

    def test_deletion_restart_and_session_switch(self):
        self.seed_movement()
        expected = self.teams_by_id()
        self.app.close()
        self.app = Application(self.database, self.directory.name)
        self.assertEqual(self.teams_by_id(), expected)
        old_session = self.app.store.snapshot()['session']
        self.app.store.create_session('Other room', old_session['id'])
        self.assertEqual(self.app.overlay_snapshot()['teams'], [])
        self.app.store.load_session(old_session['folder_id'], self.app.store.snapshot()['session']['id'])
        self.assertEqual(self.teams_by_id(), expected)
        self.app.store.delete_race('second')
        self.assertTrue(all(t['rank_change'] is None for t in self.teams_by_id().values()))


if __name__ == '__main__':
    unittest.main()
