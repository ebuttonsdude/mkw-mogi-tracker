"""FFA uses Mogi scoring without team identities or existing-session conversion."""

from pathlib import Path
import tempfile
import threading
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest
from unittest.mock import Mock

from mkw_tracker.server import Application, make_server
from mkw_tracker.store import TrackerStore


class FFAStoreTests(unittest.TestCase):
    def test_format_persists_and_initial_review_never_creates_teams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ffa.sqlite'
            store = TrackerStore(path, default_format='ffa')
            try:
                session = store.active_session()
                self.assertEqual(session['format'], 'ffa')
                store.create_race('r1', 'Rainbow Road')
                self.assertEqual(store.snapshot()['room']['points'], 0)
                store.review_result('r1', [{'name': f'A 日本 {i}'} for i in range(12)])
                snapshot = store.snapshot()
                self.assertEqual(snapshot['teams'], [])
                self.assertTrue(all(p['team_id'] is None for p in snapshot['players']))
                self.assertEqual(snapshot['room']['points'], 82)
                with self.assertRaises(ValueError):
                    store.create_team('Synthetic')
                folder = store.get_folder(session['folder_id'])
                self.assertEqual(folder['format'], 'ffa')
                new = store.create_session('Second FFA', session['id'])
                self.assertEqual(new['format'], 'ffa')
                store.load_session(folder['id'], new['id'])
                self.assertEqual(store.snapshot()['room']['points'], 82)
            finally:
                store.close()
            reopened = TrackerStore(path, default_format='ffa')
            try:
                self.assertEqual(reopened.active_session()['format'], 'ffa')
            finally:
                reopened.close()

    def test_legacy_team_sessions_are_not_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite'
            store = TrackerStore(path)
            team = store.create_team('Keep me')
            original = store.active_session()['id']
            store.close()
            # A different creation default never mutates existing sessions.
            store = TrackerStore(path, default_format='ffa')
            try:
                self.assertEqual(store.active_session()['id'], original)
                self.assertEqual(store.active_session()['format'], 'teams')
                self.assertEqual(store.snapshot()['teams'][0]['id'], team['id'])
            finally:
                store.close()


class FFAModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.app = Application(self.path / 'tracker.sqlite3', self.path)

    def tearDown(self):
        self.app.close()
        self.directory.cleanup()

    def switch(self, mode):
        return self.app.switch_tracker_mode(mode, self.app.store.active_session()['id'])

    def test_modes_resume_without_changing_existing_team_session(self):
        team = self.app.store.create_team('Existing')
        original = self.app.store.snapshot()
        self.switch('ffa')
        session = self.app.store.active_session()
        self.assertEqual(session['format'], 'ffa')
        self.assertEqual(self.app.store.snapshot()['teams'], [])
        self.assertEqual(self.app.obs_text_path.name, 'player-standings.txt')
        self.assertIsInstance(self.app.store, TrackerStore)
        self.switch('ww')
        self.switch('mogi')
        self.assertEqual(self.app.store.snapshot(), original)
        self.assertEqual(self.app.store.get_team(team['id'])['name'], 'Existing')
        self.switch('ffa')
        self.assertEqual(self.app.store.active_session()['id'], session['id'])
        with self.assertRaises(ValueError):
            self.app.check_tracker_mode('mogi')
        self.app.close()
        self.app = Application(self.path / 'tracker.sqlite3', self.path)
        self.assertEqual(self.app.tracker_mode, 'ffa')
        self.assertEqual(self.app.store.active_session()['id'], session['id'])

    def test_individual_overlay_highlight_gaps_and_movement(self):
        self.switch('ffa')
        store = self.app.store
        ids = [store.add_player(f'Player {i}')['id'] for i in range(12)]
        store.create_race('r1', 'Rainbow Road')
        store.submit_result('r1', ids)
        first = self.app.overlay_snapshot()
        self.assertEqual(first['teams'], [])
        self.assertEqual(first['settings']['layout'], 'players')
        self.assertTrue(all(p['rank_change'] is None for p in first['players']))
        self.assertEqual(first['players'][1]['gap_leader'], 3)
        self.app.update_settings({'overlay': {'highlight_player_id': ids[0]}})
        self.assertEqual(self.app.overlay_snapshot()['settings']['highlight_player_id'], ids[0])
        store.create_race('r2', 'Rainbow Road')
        store.submit_result('r2', ids[::-1])
        second = self.app.overlay_snapshot()
        self.assertTrue(any(p['rank_change'] for p in second['players']))
        self.assertEqual(sum(p['points'] for p in second['players']), 164)
        store.create_race('pending', 'Rainbow Road')
        self.assertEqual(second['players'], self.app.overlay_snapshot()['players'])
        self.assertIn('Player', self.app.obs_text_path.read_text())
        self.assertNotIn('Unassigned', self.app.obs_text_path.read_text())

    def test_older_corrections_and_latest_penalties_preserve_baseline(self):
        self.switch('ffa')
        store = self.app.store
        ids = [store.add_player(f'Player {i}')['id'] for i in range(12)]
        for race, order in [('first', ids), ('second', ids[1:] + ids[:1])]:
            store.create_race(race, 'Rainbow Road')
            store.submit_result(race, order)
        store.correct_result('first', ids[::-1])
        previous = {p['id']: p['previous_rank'] for p in self.app.overlay_snapshot()['players']}
        self.assertEqual(previous, {identifier: 12 - i for i, identifier in enumerate(ids)})
        store.add_penalty('second', ids[0], 100)
        after = self.app.overlay_snapshot()
        self.assertEqual(previous, {p['id']: p['previous_rank'] for p in after['players']})
        self.assertEqual(after['room']['points'], 164)
        self.assertEqual(sum(p['points'] for p in after['players']), 64)
        self.app.update_settings({'overlay': {'highlight_player_id': ids[0]}})
        self.switch('mogi')
        self.app.update_settings({'overlay': {'opacity': .7}})
        self.switch('ww')
        self.app.update_settings({'overlay': {'ww_show_graph': False}})
        self.switch('ffa')
        self.assertEqual(self.app.overlay_snapshot()['settings']['highlight_player_id'], ids[0])

    def test_http_guards_report_highlights_and_context_evidence(self):
        self.switch('ffa')
        store = self.app.store
        session = store.active_session()
        player = store.add_player('日本 FFA')
        server = make_server(self.app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'

        def request(path, method='GET', data=None):
            payload = None if data is None else json.dumps(data).encode()
            try:
                response = urlopen(Request(base + path, payload, {'Content-Type': 'application/json'}, method=method))
            except HTTPError as error:
                response = error
            with response:
                return response.status, response.read()

        try:
            self.assertEqual(request('/api/teams', 'POST', {
                'name': 'No team', 'expected_tracker_mode': 'ffa', 'expected_session_id': session['id']})[0], 400)
            self.assertEqual(request('/api/players', 'POST', {
                'name': 'Stale', 'expected_session_id': session['id']})[0], 400)
            folder = session['folder_id']
            self.assertEqual(request(f'/api/folders/{folder}/preview.png?highlight_player_id={player["id"]}')[0], 200)
            self.assertEqual(request(f'/api/folders/{folder}/preview.png?highlight_player_id=missing')[0], 400)
            self.assertEqual(request(f'/api/folders/{folder}/preview.png?highlight_team_id=')[0], 400)
            filename = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.png'
            directory = self.path / 'ffa' / 'evidence'
            (directory / filename).write_bytes(b'ffa evidence')
            self.assertEqual(request('/evidence/' + filename), (200, b'ffa evidence'))
            self.assertEqual(self.app.race_evidence({'evidence': [{'url': '/evidence/' + filename}]}),
                             [{'url': '/evidence/' + filename}])
            self.switch('mogi')
            self.assertEqual(request('/evidence/' + filename)[0], 404)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_switch_rejects_active_upload_and_bad_highlight(self):
        self.app.active_uploads.add('reserved')
        with self.assertRaises(ValueError):
            self.switch('ffa')
        self.app.active_uploads.clear()
        self.switch('ffa')
        with self.assertRaises(ValueError):
            self.app.update_settings({'overlay': {'highlight_player_id': 'not-a-player'}})
        self.app.capture.thread = Mock()
        self.app.capture.thread.is_alive.return_value = True
        try:
            with self.assertRaises(ValueError):
                self.switch('mogi')
        finally:
            self.app.capture.thread = None
