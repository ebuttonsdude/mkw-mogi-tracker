import json
import os
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote
from unittest.mock import Mock, patch
from uuid import uuid4

from mkw_tracker.__main__ import seed_demo
from mkw_tracker.server import Application, make_server
from mkw_tracker.store import TrackerStore


class StoreReviewTests(unittest.TestCase):
    def setUp(self):
        self.store = TrackerStore(':memory:')
        self.store.create_race('race', 'Course')

    def tearDown(self):
        self.store.close()

    def test_review_enrolls_unicode_names_and_scores_atomically(self):
        names = [{'name': f'選手 {i}'} for i in range(12)]
        result = self.store.review_result('race', names)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(sum(p['points'] for p in self.store.snapshot()['players']), 82)
        with self.assertRaises(ValueError):
            self.store.review_result('race', names)
        self.assertEqual(len(self.store.snapshot()['players']), 12)

    def test_bad_existing_identity_rolls_back_new_players(self):
        before = self.store.snapshot()
        with self.assertRaises(KeyError):
            self.store.review_result('race', [{'name': str(i)} for i in range(11)] + [{'player_id': 'absent'}])
        self.assertEqual(self.store.snapshot(), before)

    def test_review_duplicate_ids_and_malformed_names(self):
        p = self.store.add_player('A')
        for choices in [[{'player_id': p['id']}] * 12,
                        [{'name': ''}] * 12, [{'name': 'A', 'player_id': 'x'}] * 12]:
            with self.assertRaises(ValueError):
                self.store.review_result('race', choices)
        self.assertEqual(self.store.snapshot()['races'][0]['status'], 'pending')
        self.assertEqual(len(self.store.snapshot()['players']), 1)

    def test_chapter_correction_preserves_session_and_results(self):
        result = self.store.review_result('race', [{'name': str(i)} for i in range(12)])
        recording = self.store.create_recording('Video')
        changed = self.store.update_race('race', 'Correct course', recording['id'], 12345)
        self.assertEqual(changed['placements'], result['placements'])
        self.assertEqual(changed['session_id'], result['session_id'])
        self.assertEqual(changed['offset_ms'], 12345)
        before = self.store.snapshot()
        with self.assertRaises(ValueError):
            self.store.update_race('race', 'Oops', None, 300)
        self.assertEqual(self.store.snapshot(), before)


class SettingsPersistenceTests(unittest.TestCase):
    def test_format_persists_across_application_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'scores.sqlite3'
            app = Application(database, directory)
            try:
                app.update_settings({'standings_format': 'md'})
            finally:
                app.close()
            app = Application(database, directory)
            try:
                self.assertEqual(app.settings['standings_format'], 'md')
                self.assertTrue(app.obs_text_path.name.endswith('.md'))
                self.assertTrue(app.obs_text_path.is_file())
            finally:
                app.close()

    def test_malformed_settings_fail_clearly_without_an_open_database(self):
        for value in ('not-json', '{"standings_format":[]}', '[]'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / 'settings.json').write_text(value)
                with self.assertRaisesRegex(ValueError, 'settings.json'):
                    Application(path / 'scores.sqlite3', path)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Application(':memory:', self.directory.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.directory.cleanup()

    def request(self, path, method='GET', data=None, headers=None):
        if method == 'POST' and path in {'/api/players', '/api/teams', '/api/races'} and data is not None:
            data = {'expected_session_id': self.app.store.snapshot()['session']['id'], **data}
        body = None if data is None else json.dumps(data).encode()
        hdr = {'Content-Type': 'application/json'} if body else {}
        hdr.update(headers or {})
        request = Request(self.base + path, body, hdr, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if 'application/json' in response.headers['Content-Type'] else raw
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_empty_dashboard_static_and_host_boundaries(self):
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state['players'], [])
        self.assertEqual(state['capture']['status'], 'manual')
        for path in ['/', '/static/app.js', '/static/style.css']:
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/static/../../mkw_tracker/store.py')[0], 404)
        self.assertEqual(self.request('/api/state', headers={'Host': 'malicious.example'})[0], 403)

    def test_failed_import_cleanup_protects_session_and_confirmed_race(self):
        race = self.app.store.create_race('cleanup-protected', 'Rainbow Road')
        self.app.store.review_result(race['id'], [{'name': str(i)} for i in range(12)])
        before = self.app.store.snapshot()
        self.app.imports.cleanup_failed = True
        try:
            self.assertEqual(self.request(f"/api/races/{race['id']}", 'DELETE', {})[0], 400)
            self.assertEqual(self.request(f"/api/folders/{race['folder_id']}", 'DELETE', {})[0], 400)
            self.assertEqual(self.app.store.snapshot(), before)
        finally:
            self.app.imports.cleanup_failed = False

    def test_overlay_visibility_controls_are_transient_and_origin_guarded(self):
        self.assertTrue(self.request('/api/overlay')[1]['visible'])
        for action, expected in [('hide', False), ('show', True), ('toggle', False), ('toggle', True)]:
            self.assertEqual(self.request('/api/overlay/' + action, 'POST', {}), (200, {'visible': expected}))
            self.assertEqual(self.request('/api/overlay')[1]['visible'], expected)
            self.assertEqual(self.request('/api/state')[1]['display_standings']['visible'], expected)
        self.assertEqual(self.request('/api/overlay/hide', 'POST', {'visible': False})[0], 400)
        self.assertEqual(self.request('/api/overlay/hide', 'POST', {}, {'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('/api/overlay/hide')[0], 404)
        self.assertTrue(self.app.overlay_visible)

    def test_png_team_override_is_scoped_to_report_session_and_does_not_change_settings(self):
        seed_demo(self.app.store)
        before = self.app.store.snapshot()
        folder = before['session']['folder_id']
        team = self.app.store.create_team('Report team')['id']
        self.app.update_settings({'overlay': {'highlight_team_id': team}})
        route = '/api/folders/' + folder + '/preview.png'
        with patch('mkw_tracker.server.render_folder_png', return_value=b'png') as renderer:
            self.assertEqual(self.request(route), (200, b'png'))
            self.assertEqual(renderer.call_args.kwargs['highlight_team_id'], team)
            self.assertEqual(self.request(route + '?highlight_team_id='), (200, b'png'))
            self.assertIsNone(renderer.call_args.kwargs['highlight_team_id'])
            self.assertEqual(self.request(route + '?highlight_team_id=' + team)[0], 200)
            self.assertEqual(self.request(route + '?highlight_team_id=missing')[0], 400)
            self.assertEqual(self.request(route + '?highlight_team_id=&highlight_team_id=')[0], 400)
            self.assertEqual(self.request(route + '?unexpected=1')[0], 400)
            self.app.store.create_session('Another room', before['session']['id'])
            other = self.app.store.create_team('Other team')['id']
            self.assertEqual(self.request(route + '?highlight_team_id=' + other)[0], 400)
            self.assertEqual(self.request(route + '?highlight_team_id=' + team)[0], 200)
            self.assertEqual(renderer.call_args.kwargs['highlight_session_id'], before['session']['id'])
            self.app.store.load_session(folder, self.app.store.snapshot()['session']['id'])
            self.app.store.delete_team(team)
            self.assertEqual(self.request(route + '?highlight_team_id=' + team)[0], 400)
        self.assertEqual(self.app.settings['overlay']['highlight_team_id'], team)

    def test_pending_race_can_be_deleted_during_capture_or_import(self):
        for manager in (self.app.imports, self.app.capture):
            race = self.app.store.create_race(str(uuid4()), 'OCR false start')
            with patch.object(manager, 'thread', Mock(is_alive=lambda: True)):
                self.assertEqual(self.request('/api/races/' + race['id'], 'DELETE', {})[0], 200)
                self.assertEqual(self.request('/api/races/' + race['id'], 'DELETE', {})[0], 404)

    def test_api_scoring_reset_correction_and_chapter_export(self):
        ids = [self.request('/api/players', 'POST', {'name': f'Player {i}'})[1]['id'] for i in range(12)]
        recording = self.request('/api/recordings', 'POST', {'label': 'Recording'})[1]
        race = self.request('/api/races', 'POST', {'course': 'Course', 'recording_id': recording['id'], 'offset_ms': 12345})[1]
        route = f"/api/races/{race['id']}/results"
        for _ in range(2):
            self.assertEqual(self.request(route, 'POST', {'player_ids': ids})[0], 200)
        self.assertEqual(sum(p['points'] for p in self.request('/api/state')[1]['players']), 82)
        team = self.request('/api/teams', 'POST', {'name': 'My team'})[1]
        self.request('/api/players/' + ids[0], 'PATCH', {'team_id': team['id']})
        self.assertEqual(self.request('/api/state')[1]['teams'][0]['points'], 15)
        self.assertEqual(self.request(route, 'POST', {'player_ids': ids[::-1]})[0], 400)
        self.assertEqual(self.request(route, 'POST', {'player_ids': ids[::-1], 'correction': True})[0], 200)
        self.request('/api/races/' + race['id'], 'PATCH', {'offset_ms': 42000})
        self.assertEqual(self.request('/api/chapters?recording_id=' + recording['id'])[1], b'00:00:42.000 Course\n')
        old_session = self.request('/api/state')[1]['session']['id']
        self.assertEqual(self.request('/api/reset', 'POST', {'expected_session_id': old_session})[0], 200)
        self.assertEqual(self.request('/api/reset', 'POST', {'expected_session_id': old_session})[0], 400)
        self.request(route, 'POST', {'player_ids': ids[::-1]})
        state = self.request('/api/state')[1]
        self.assertEqual(sum(p['points'] for p in state['players']), 0)
        self.assertEqual(state['players'], [])
        self.assertEqual(state['teams'], [])
        self.assertEqual(len(state['archived_players']), 12)
        self.assertEqual(len(state['folders']), 2)
        self.assertEqual(len(state['races']), 1)
        self.assertEqual(state['races'][0]['offset_ms'], 42000)

    def test_delete_and_history_folder_routes_preserve_race_records(self):
        player = self.request('/api/players', 'POST', {'name': 'Typo'})[1]
        team = self.request('/api/teams', 'POST', {'name': 'Tem'})[1]
        self.request('/api/players/' + player['id'], 'PATCH', {'team_id': team['id']})
        renamed = self.request('/api/teams/' + team['id'], 'PATCH', {'name': 'Team'})
        self.assertEqual(renamed[1]['name'], 'Team')
        self.assertEqual(self.request('/api/teams/' + team['id'], 'DELETE', {})[0], 200)
        self.assertIsNone(self.request('/api/state')[1]['players'][0]['team_id'])
        self.assertEqual(self.request('/api/players/' + player['id'], 'DELETE', {})[0], 200)
        state = self.request('/api/state')[1]
        self.assertEqual(state['players'], [])
        self.assertEqual(state['teams'], [])
        self.assertEqual(state['archived_players'][0]['name'], 'Typo')
        self.assertEqual(self.request('/api/players/missing', 'DELETE', {})[0], 404)
        folder = self.request('/api/folders', 'POST', {'name': 'Mogi'})[1]
        self.assertEqual(self.request('/api/folders/' + folder['id'], 'PATCH', {'name': 'Friday mogi'})[1]['name'], 'Friday mogi')
        race = self.request('/api/races', 'POST', {'course': 'Course'})[1]
        moved = self.request('/api/races/' + race['id'], 'PATCH', {'folder_id': folder['id']})
        self.assertEqual(moved[0], 400)
        self.assertEqual(self.request('/api/state')[1]['races'][0]['folder_id'], race['folder_id'])
        folder_race = self.request('/api/races', 'POST', {'course': 'Other session', 'folder_id': folder['id']})[1]
        self.assertEqual(folder_race['session_id'], folder['session_id'])
        self.assertEqual(self.request('/api/races/' + race['id'], 'PATCH', {'folder_id': 'missing'})[0], 404)
        self.assertEqual(self.request('/api/races/' + race['id'], 'PATCH', {'folder_id': folder['id'], 'course': 'Other'})[0], 400)
        self.assertEqual(self.request('/api/state')[1]['races'][0]['course'], 'Course')

    def test_cross_origin_and_invalid_bodies_cannot_mutate(self):
        for headers in [{'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.request('/api/players', 'POST', {'name': 'Oops'}, headers)[0], 403)
        self.assertEqual(self.request('/api/players', 'POST', {'name': ''})[0], 400)
        self.assertEqual(self.request('/api/races', 'POST', {'course': 'X', 'offset_ms': True})[0], 400)
        self.assertEqual(self.request('/api/state')[1]['players'], [])

    def test_demo_has_reference_scores_and_unknown_last_result(self):
        with self.app.lock:
            seed_demo(self.app.store)
        state = self.request('/api/state')[1]
        self.assertEqual(len(state['players']), 12)
        self.assertEqual(sum(p['points'] for p in state['players']), 164)
        self.assertEqual([r['status'] for r in state['races']], ['scored', 'scored', 'pending'])
        self.assertEqual(next(p for p in state['players'] if p['name'].startswith('ST avid'))['points'], 5)

    def test_lounge_icons_and_folder_png_routes(self):
        folder = self.request('/api/state')[1]['folders'][0]
        route = '/api/folders/' + folder['id']
        self.assertEqual(self.request(route, 'PATCH', {'lounge_id': '1146183'})[0], 200)
        self.assertEqual(self.request(route, 'PATCH', {'name': 'Results'})[1]['lounge_id'], '1146183')
        for value in ['https://example.com', '12/3', '１２', -1]:
            self.assertEqual(self.request(route, 'PATCH', {'lounge_id': value})[0], 400)
        self.assertEqual(self.request(route, 'PATCH', {})[0], 400)
        race = self.request('/api/races', 'POST', {'course': 'Rainbow Road'})[1]
        icon = race['icon_url']
        self.assertIsNone(icon)
        self.assertEqual(self.request('/static/track-icons/../app.js')[0], 404)
        self.assertEqual(self.request('/static/track-icons/not-an-icon.png')[0], 404)
        self.assertEqual(self.request('/api/folders/missing/report.png')[0], 404)
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.assertEqual(self.request(route + '/report.png')[0], 400)
        else:
            self.assertEqual(self.request(route + '/report.png')[1][:8], b'\x89PNG\r\n\x1a\n')
        self.assertIsNone(self.request(route, 'PATCH', {'lounge_id': None})[1]['lounge_id'])

    def test_obs_file_updates_without_a_browser_refresh(self):
        ids = [self.request('/api/players', 'POST', {'name': str(index)})[1]['id'] for index in range(12)]
        team = self.request('/api/teams', 'POST', {'name': 'Team'})[1]
        self.request('/api/players/' + ids[0], 'PATCH', {'team_id': team['id']})
        race = self.request('/api/races', 'POST', {'course': 'Crown City'})[1]
        self.request(f"/api/races/{race['id']}/results", 'POST', {'player_ids': ids})
        path = self.app.obs_text_path
        content = path.read_text()
        self.assertIn('Team | 15 pts', content)
        self.assertIn('Room: 82/984 pts | 1/12 races', content)
        self.assertEqual(self.request('/api/standings.txt')[1].decode(), content)
        before = path.stat().st_mtime_ns
        with patch.object(self.app, 'sync_outputs', side_effect=AssertionError('GET must not update output')):
            self.assertEqual(self.request('/api/state')[0], 200)
            self.assertEqual(self.request('/api/standings.txt')[1].decode(), content)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.request('/api/teams/' + team['id'], 'PATCH', {'name': 'Renamed'})
        self.assertIn('Renamed', path.read_text())
        self.request('/api/players/' + ids[0], 'DELETE', {})
        self.assertIn('Renamed | 0 pts', path.read_text())
        self.assertIn('Room: 82/984', path.read_text())
        session = self.request('/api/state')[1]['session']['id']
        self.request('/api/reset', 'POST', {'expected_session_id': session})
        self.assertIn('No teams', path.read_text())
        self.assertIn('Room: 0/984', path.read_text())
        self.assertFalse(path.with_suffix('.txt.tmp').exists())

    def test_import_api_validation_and_delegation(self):
        self.assertEqual(self.request('/api/imports', 'POST', {'path': '/missing.mp4'})[0], 400)
        self.assertEqual(self.request('/api/imports', 'POST', {'path': 'https://example.com/video.mp4'})[0], 400)
        self.assertEqual(self.request('/api/imports', 'POST', {'unexpected': True})[0], 400)
        job = {'id': 'import-id', 'status': 'queued'}
        with patch.object(self.app.imports, 'start', return_value=job) as start:
            from mkw_tracker.name_engines import QWEN_PROFILES
            for engine in QWEN_PROFILES:
                self.assertEqual(self.request('/api/imports', 'POST',
                                             {'path': '/video.mkv', 'name_engine': engine, 'stride': .5})[1], job)
                start.assert_called_with('/video.mkv', None, engine, .5)
        with patch.object(self.app.imports, 'cancel', return_value=job) as cancel:
            self.assertEqual(self.request('/api/imports/import-id/cancel', 'POST', {})[0], 200)
            cancel.assert_called_once_with('import-id')
        self.assertEqual(self.request('/api/imports/missing/cancel', 'POST', {})[0], 404)

    def test_obs_write_failure_does_not_lose_committed_scores_and_recovers(self):
        path = self.app.obs_text_path
        initial = path.read_text()
        with patch.object(Path, 'replace', side_effect=OSError('simulated output failure')):
            status, team = self.request('/api/teams', 'POST', {'name': 'Committed team'})
            self.assertEqual(status, 200)
            self.assertEqual(path.read_text(), initial)
            snapshot = self.request('/api/state')[1]
            self.assertEqual(snapshot['teams'][0]['id'], team['id'])
            self.assertIsNotNone(snapshot['obs_text_error'])
            self.assertEqual(self.request('/api/standings.txt')[0], 400)
        # Output retries no longer depend on a GET request from an open browser.
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with self.app.lock:
                if self.app.obs_text_error is None:
                    break
            time.sleep(.02)
        self.assertIsNone(self.request('/api/state')[1]['obs_text_error'])
        self.assertIn('Committed team', path.read_text())

    def test_video_upload_is_local_streamed_and_origin_guarded(self):
        def upload(filename, *, query='', origin=None):
            headers = {'Content-Type': 'application/octet-stream', 'X-Filename': quote(filename)}
            if origin:
                headers['Origin'] = origin
            request = Request(self.base + '/api/imports/upload' + query, b'video fixture', headers, method='POST')
            try:
                with urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())

        with patch.object(self.app.imports, 'start', return_value={'id': 'uploaded'}) as start:
            self.assertEqual(upload('../bad.mp4')[0], 400)
            self.assertEqual(upload('bad.txt')[0], 400)
            self.assertEqual(upload('video.mp4', origin='https://other.example')[0], 403)
            self.assertEqual(upload('video.mp4', query='?stride=nan')[0], 400)
            self.assertEqual(upload('video.mp4', query='?folder_id=missing')[0], 400)
            self.assertEqual(upload('video.mp4', query='?name_engine=cloud')[0], 400)
            start.assert_not_called()
            self.assertEqual(upload('録画.mp4', query='?name_engine=tesseract&stride=1')[0], 200)
            path = Path(start.call_args.args[0])
            self.assertEqual(path.parent, Path(self.directory.name) / 'uploads')
            self.assertEqual(path.read_bytes(), b'video fixture')
            self.assertEqual(start.call_args.args[1:], (None, 'tesseract', 1.0))
            from mkw_tracker.name_engines import QWEN_PROFILES
            for engine in QWEN_PROFILES:
                self.assertEqual(upload('profile.mp4', query=f'?name_engine={engine}&stride=1')[0], 200)
                self.assertEqual(start.call_args.args[1:], (None, engine, 1.0))
        with patch.object(self.app.imports, 'start', side_effect=ValueError('Cannot start')):
            self.assertEqual(upload('rejected.mp4')[0], 400)
        self.assertEqual(len(list((Path(self.directory.name) / 'uploads').iterdir())), 1 + len(QWEN_PROFILES))

    def test_loadable_sessions_penalty_and_delete_routes(self):
        original = self.request('/api/state')[1]['session']
        ids = [self.request('/api/players', 'POST', {'name': str(index)})[1]['id'] for index in range(12)]
        race = self.request('/api/races', 'POST', {'course': 'Crown City'})[1]
        route = '/api/races/' + race['id']
        self.request(route + '/results', 'POST', {'player_ids': ids})
        status, penalty = self.request(route + '/penalties', 'POST', {'player_id': ids[0], 'points': 4, 'reason': 'Rule'})
        self.assertEqual(status, 200)
        state = self.request('/api/state')[1]
        self.assertEqual(state['players'][0]['points'], 11)
        self.assertEqual((state['room']['points'], state['room']['net_points']), (82, 78))
        self.assertEqual(state['room']['logged_races'], 1)
        self.assertEqual(self.request(route + '/penalties', 'POST', {'player_id': ids[0], 'points': -4})[0], 400)
        self.assertEqual(self.request('/api/penalties/' + penalty['id'], 'DELETE', {})[0], 200)
        self.assertEqual(self.request('/api/state')[1]['players'][0]['points'], 15)
        new = self.request('/api/sessions', 'POST', {'name': 'New room', 'expected_session_id': original['id']})[1]
        state = self.request('/api/state')[1]
        self.assertEqual((state['players'], state['teams'], state['room']['logged_races']), ([], [], 0))
        self.assertEqual(self.request('/api/folders/' + original['folder_id'] + '/load', 'POST',
                                      {'expected_session_id': original['id']})[0], 400)
        self.assertEqual(self.request('/api/folders/' + original['folder_id'] + '/load', 'POST',
                                      {'expected_session_id': new['id']})[0], 200)
        self.assertEqual([p['id'] for p in self.request('/api/state')[1]['players']], ids)
        with patch.object(self.app.imports, 'snapshot', return_value={'active': {'status': 'running'}, 'jobs': []}):
            self.assertEqual(self.request('/api/folders/' + original['folder_id'], 'DELETE', {})[0], 400)
        self.assertEqual(self.request('/api/folders/' + original['folder_id'], 'DELETE', {})[0], 200)
        state = self.request('/api/state')[1]
        self.assertNotIn(original['folder_id'], [folder['id'] for folder in state['folders']])
        self.assertNotEqual(state['session']['id'], original['id'])
        self.assertEqual((state['players'], state['teams'], state['races']), ([], [], []))

    def test_standings_format_routes_and_durable_settings(self):
        self.request('/api/teams', 'POST', {'name': 'A|B'})
        saved = self.request('/api/settings', 'PATCH', {'standings_format': 'md'})[1]
        self.assertEqual(saved['standings_format'], 'md')
        snapshot = self.request('/api/state')[1]
        self.assertTrue(snapshot['obs_text_path'].endswith('.md'))
        self.assertEqual(snapshot['settings']['standings_format'], 'md')
        self.assertIn('A\\|B', self.request('/api/standings.md')[1].decode())
        self.assertEqual(self.request('/api/standings.txt')[0], 400)
        self.assertFalse((Path(self.directory.name) / 'team-standings.txt').exists())
        self.assertEqual(json.loads((Path(self.directory.name) / 'settings.json').read_text()), saved)
        for value in ('html', None, [], True):
            self.assertEqual(self.request('/api/settings', 'PATCH', {'standings_format': value})[0], 400)
        self.assertEqual(self.request('/api/settings', 'PATCH', {'other': 'txt'})[0], 400)
        self.assertEqual(self.request('/api/settings', 'PATCH', {'standings_format': 'txt'})[0], 200)
        self.assertEqual(self.request('/api/standings.txt')[0], 200)
        self.assertFalse((Path(self.directory.name) / 'team-standings.md').exists())

    def test_evidence_routes_and_course_correction_preserve_results(self):
        race = self.request('/api/races', 'POST', {'course': 'Wrong track'})[1]
        route = '/api/races/' + race['id']
        self.assertEqual(self.request(route + '/evidence')[1]['images'], [])
        filename = str(uuid4()) + '.png'
        (Path(self.directory.name) / 'evidence' / filename).write_bytes(b'local evidence fixture')
        with self.app.lock:
            self.app.store.add_evidence(race['id'], filename, label='Results')
        self.assertEqual(self.request(route + '/evidence')[1]['images'][0]['url'], '/evidence/' + filename)
        self.request(route, 'PATCH', {'course': 'Rainbow Road'})
        state = self.request('/api/state')[1]
        self.assertEqual(state['races'][0]['course'], 'Rainbow Road')
        self.assertIsNone(state['races'][0]['icon_url'])
        self.assertEqual(len(state['races'][0]['evidence']), 1)
        (Path(self.directory.name) / 'evidence' / filename).unlink()
        self.assertEqual(self.request(route + '/evidence')[1]['images'], [])
        self.assertEqual(self.request('/api/races/missing/evidence')[0], 404)

    def test_race_deletion_recomputes_scores_chapters_and_clears_review_latch(self):
        with self.app.lock:
            seed_demo(self.app.store)
            state = self.app.store.snapshot()
            race = state['races'][0]
            self.app.store.add_penalty(race['id'], race['placements'][0]['player_id'], 7)
            recording = self.app.store.create_recording('Retained video')
            self.app.store.update_race(race['id'], race['course'], recording['id'], 500)
            evidence = str(uuid4()) + '.png'
            evidence_path = Path(self.directory.name) / 'evidence' / evidence
            evidence_path.write_bytes(b'proof')
            self.app.store.add_evidence(race['id'], evidence)
            pending = state['races'][-1]
            self.app.capture.state.update(race_id=race['id'], course=race['course'],
                                          session_id=race['session_id'], armed=False, in_prerace=False,
                                          pending=[{'id': 'delete', 'race_id': race['id']},
                                                   {'id': 'retain', 'race_id': pending['id']}])
        route = '/api/races/' + quote(race['id'])
        self.assertEqual(self.request(route, 'DELETE', {'path': 'not allowed'})[0], 400)
        self.assertEqual(self.request(route, 'DELETE', {}, {'Origin': 'https://other.example'})[0], 403)
        for manager in (self.app.imports, self.app.capture):
            with patch.object(manager, 'thread', Mock(is_alive=lambda: True)):
                self.assertEqual(self.request(route, 'DELETE', {})[0], 400)
        status, deleted = self.request(route, 'DELETE', {})
        self.assertEqual(status, 200)
        self.assertEqual(deleted['deleted_race_ids'], [race['id']])
        after = self.request('/api/state')[1]
        self.assertEqual(after['session'], state['session'])
        self.assertEqual(len(after['players']), 12)
        self.assertEqual(len(after['teams']), len(state['teams']))
        self.assertEqual(after['room']['points'], 82)
        self.assertEqual(after['room']['penalty_points'], 0)
        self.assertEqual(after['room']['logged_races'], 2)
        self.assertEqual(after['capture']['pending'], [{'id': 'retain', 'race_id': pending['id']}])
        self.assertIsNone(self.app.capture.state['race_id'])
        self.assertFalse(self.app.capture.state['armed'])
        self.assertEqual(self.request('/api/chapters?recording_id=' + recording['id'])[1], b'')
        self.assertIn('Room: 82/984', self.app.obs_text_path.read_text())
        self.assertEqual(self.request(route + '/evidence')[0], 404)
        self.assertEqual(self.request(route, 'DELETE', {})[0], 404)
        self.assertTrue(evidence_path.is_file())  # Not part of video-file cleanup.
        self.assertEqual(self.request('/api/races/' + quote(pending['id']), 'DELETE', {})[0], 200)
        self.assertEqual(self.request('/api/state')[1]['capture']['pending'], [])
        self.assertEqual(self.app.store._rows('PRAGMA foreign_key_check'), [])

    def test_delete_populated_unloaded_folder_leaves_selected_session_unchanged(self):
        with self.app.lock:
            seed_demo(self.app.store)
            old = self.app.store.snapshot()['session']
            self.app.store.create_session('Selected room', old['id'])
            ids = [self.app.store.add_player(f'Selected {i}')['id'] for i in range(12)]
            team = self.app.store.create_team('Selected team')
            self.app.store.update_player(ids[0], 'Selected 0', team['id'])
            self.app.store.create_race('selected-race', 'Rainbow Road')
            self.app.store.submit_result('selected-race', ids)
        before = self.request('/api/state')[1]
        self.assertEqual(self.request('/api/folders/' + old['folder_id'], 'DELETE', {})[0], 200)
        after = self.request('/api/state')[1]
        for key in ('session', 'players', 'teams', 'room'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['races'], [r for r in before['races'] if r['session_id'] != old['id']])

    def test_video_library_delete_cleanup_and_external_original_boundary(self):
        root = Path(self.directory.name)
        uploads = root / 'uploads'
        uploads.mkdir()
        paths = [uploads / name for name in ('old.mp4', 'new.mkv', 'active.mp4')]
        for path in paths:
            path.write_bytes(b'video')
        os.utime(paths[0], (time.time() - 31 * 86400,) * 2)
        original = root / 'original.mp4'
        original.write_bytes(b'never delete')
        (uploads / 'linked.mp4').symlink_to(original)
        self.app.active_uploads.add(paths[2])
        before = self.app.store.snapshot()
        listing = self.request('/api/videos')[1]
        self.assertEqual(listing, self.request('/api/state')[1]['videos'])
        self.assertEqual((listing['total_files'], listing['managed_bytes']), (3, 15))
        entries = {file['name']: file for file in listing['files']}
        self.assertFalse(entries['active.mp4']['can_delete'])
        delete_route = '/api/videos/' + entries['new.mkv']['id']
        self.assertEqual(self.request(delete_route, 'DELETE', {'path': str(original)})[0], 400)
        self.assertEqual(self.request(delete_route, 'DELETE', {}, {'Origin': 'https://bad.example'})[0], 403)
        self.assertEqual(self.request('/api/videos/cleanup', 'POST', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for invalid in (-1, True, 1.5, '30', 36501):
            self.assertEqual(self.request('/api/videos/cleanup', 'POST', {'older_than_days': invalid})[0], 400)
        self.assertEqual(self.request('/api/videos/cleanup', 'POST', {'path': str(original)})[0], 400)
        self.assertEqual(self.request('/api/videos/' + entries['active.mp4']['id'], 'DELETE', {})[0], 400)
        result = self.request('/api/videos/cleanup', 'POST', {})[1]
        self.assertEqual((result['deleted_files'], result['skipped'], result['errors']), (1, 2, []))
        self.assertFalse(paths[0].exists())
        self.assertEqual(self.request(delete_route, 'DELETE', {})[1]['deleted_files'], 1)
        self.assertEqual(self.request(delete_route, 'DELETE', {})[0], 404)
        self.app.active_uploads.clear()
        self.assertEqual(self.request('/api/videos/cleanup', 'POST', {'older_than_days': 0})[1]['deleted_files'], 1)
        self.assertEqual(original.read_bytes(), b'never delete')
        self.assertTrue((uploads / 'linked.mp4').is_symlink())
        self.assertEqual(self.app.store.snapshot(), before)

    def test_upload_is_protected_until_import_takes_ownership_and_rejects_root_symlink(self):
        headers = {'Content-Type': 'application/octet-stream', 'X-Filename': 'video.mp4'}
        def on_start(path, *_args, **kwargs):
            self.assertEqual(self.app.active_uploads, {Path(path)})
            self.assertEqual(kwargs, {'upload_path': Path(path)})
            listing = self.app.videos.snapshot()
            self.assertFalse(listing['files'][0]['can_delete'])
            self.assertEqual(self.app.videos.cleanup(0)['skipped'], 1)
            with self.assertRaises(ValueError):
                self.app.videos.delete(listing['files'][0]['id'])
            return {'id': 'test'}
        with patch.object(self.app.imports, 'start', side_effect=on_start):
            with urlopen(Request(self.base + '/api/imports/upload', b'video', headers, method='POST'), timeout=5) as response:
                self.assertEqual(response.status, 200)
        self.assertEqual(self.app.active_uploads, set())
        uploads = Path(self.directory.name) / 'uploads'
        saved = uploads.with_name('saved-uploads')
        uploads.rename(saved)
        uploads.symlink_to(saved, target_is_directory=True)
        try:
            with self.assertRaises(HTTPError) as context:
                urlopen(Request(self.base + '/api/imports/upload', b'video', headers, method='POST'), timeout=5)
            with context.exception as response:
                self.assertEqual(response.status, 400)
                response.read()
            self.assertEqual(len(list(saved.iterdir())), 1)
        finally:
            uploads.unlink()
            saved.rename(uploads)

    def test_missing_upload_directory_descriptor_cannot_create_a_cwd_file(self):
        @contextmanager
        def vanished():
            yield None
        token = str(uuid4())
        accidental = Path.cwd() / f'{token}-video.mp4'
        headers = {'Content-Type': 'application/octet-stream', 'X-Filename': 'video.mp4'}
        with patch.object(self.app.videos, '_directory', vanished), patch.object(self.app.imports, 'start') as start:
            with patch('mkw_tracker.server.uuid4', return_value=token):
                with self.assertRaises(HTTPError) as context:
                    urlopen(Request(self.base + '/api/imports/upload', b'video', headers, method='POST'), timeout=5)
                with context.exception as response:
                    self.assertEqual(response.status, 400)
                    response.read()
            start.assert_not_called()
        self.assertFalse(accidental.exists())
        self.assertEqual(self.app.active_uploads, set())

    def test_cleanup_cannot_delete_a_partly_streamed_upload(self):
        connection = HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            with patch.object(self.app.imports, 'start', return_value={'id': 'streamed'}):
                connection.putrequest('POST', '/api/imports/upload')
                connection.putheader('Content-Type', 'application/octet-stream')
                connection.putheader('X-Filename', 'streamed.mp4')
                connection.putheader('Content-Length', '12')
                connection.endheaders(b'first!')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    listing = self.app.videos.snapshot()
                    if listing['files']:
                        break
                    time.sleep(.01)
                self.assertEqual(listing['total_files'], 1)
                entry = listing['files'][0]
                self.assertTrue(entry['active'])
                self.assertEqual(self.request('/api/videos/' + entry['id'], 'DELETE', {})[0], 400)
                self.assertEqual(self.request('/api/videos/cleanup', 'POST', {'older_than_days': 0})[1]['skipped'], 1)
                connection.send(b'last!!')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
            self.assertEqual(self.app.active_uploads, set())
            self.assertEqual(Path(entry['path']).read_bytes(), b'first!last!!')
        finally:
            connection.close()

    def test_character_theme_overlay_settings_and_private_scoreboard_contract(self):
        with self.app.lock:
            ids = [self.app.store.add_player(f'選手 {i}')['id'] for i in range(12)]
            for index in range(6):
                team = self.app.store.create_team(f'Team {index}')
                for player in ids[index * 2:index * 2 + 2]:
                    self.app.store.update_player(player, f'選手 {ids.index(player)}', team['id'])
            self.app.store.create_race('first', 'Rainbow Road')
            self.app.store.submit_result('first', ids)
            self.app.store.add_penalty('first', ids[0], 4)
        before = self.app.store.snapshot()
        snapshot = self.request('/api/state')[1]
        self.assertEqual(len(snapshot['themes']), 50)
        self.assertEqual(snapshot['theme']['id'], 'mario')
        status, overlay = self.request('/api/overlay')
        self.assertEqual(status, 200)
        base_fields = {'theme', 'font', 'settings', 'session', 'room', 'teams', 'players', 'visible'}
        self.assertEqual(set(overlay), base_fields | {'profile', 'content', 'lounge'})
        self.assertEqual(overlay['profile'], 'default')
        self.assertEqual(overlay['lounge'], {'enabled': False, 'players': [],
                                           'appearance': {'background': 'aurora', 'border': 'rainbow', 'shape': 'pill', 'image_url': None}})
        self.assertEqual(overlay['font']['id'], '')
        self.assertTrue(all(face['url'].startswith('/api/fonts/') for face in overlay['font']['faces']))
        self.assertEqual(len(overlay['teams']), 6)
        self.assertEqual(sum(len(t['players']) for t in overlay['teams']), 12)
        self.assertEqual((overlay['teams'][0]['points'], overlay['teams'][0]['rank']), (23, 1))
        self.assertEqual(overlay['players'][0]['points'], 12)
        self.assertEqual(overlay['players'][0]['rank'], 1)
        self.assertEqual(sum(t['points'] for t in overlay['teams']), 78)
        self.assertEqual(overlay['room']['points'], 82)
        self.assertEqual(snapshot['display_standings'], {key: overlay[key] for key in base_fields})
        for key in ('imports', 'path', 'evidence', 'recordings', 'archived_players'):
            self.assertNotIn(key, overlay)
        old_color = overlay['theme']['tokens']['accent']
        self.assertEqual(self.request('/api/settings', 'PATCH', {'theme': 'rosalina',
                                  'overlay': {'width': 700, 'opacity': .4, 'title': '選手☆ Cup'}})[0], 200)
        changed = self.request('/api/overlay')[1]
        self.assertNotEqual(changed['theme']['tokens']['accent'], old_color)
        self.assertEqual(changed['settings']['width'], 700)
        self.assertEqual(changed['settings']['height'], 600)
        self.assertEqual(changed['settings']['title'], '選手☆ Cup')
        self.request('/api/settings', 'PATCH', {'standings_format': 'md'})
        self.assertEqual(self.request('/api/overlay')[1]['theme']['id'], 'rosalina')
        self.assertEqual(self.app.store.snapshot(), before)
        for bad in ({'theme': 'not-a-character'}, {'overlay': {'width': True}},
                    {'overlay': {'show_players': 'yes'}}, {'overlay': {'path': '/tmp/out'}},
                    {'overlay': {'opacity': 10 ** 400}}):
            self.assertEqual(self.request('/api/settings', 'PATCH', bad)[0], 400)
        self.assertEqual(self.request('/api/settings', 'PATCH', {'theme': 'luigi'},
                         {'Origin': 'https://bad.example'})[0], 403)
        self.assertEqual(self.request('/api/overlay', headers={'Host': 'bad.example'})[0], 403)
        for path in ('/overlay', '/overlay.js', '/overlay.css'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/overlay/../../settings.json')[0], 404)

    def test_overlay_ranks_ties_and_unassigned_members_and_live_changes(self):
        a = self.app.store.add_player('A')
        b = self.app.store.add_player('B')
        team = self.app.store.create_team('Zero team')
        self.app.store.update_player(a['id'], 'A', team['id'])
        result = self.request('/api/overlay')[1]
        self.assertEqual([p['rank'] for p in result['players']], [1, 1])
        self.assertEqual([t['rank'] for t in result['teams']], [1, 1])
        unassigned = next(t for t in result['teams'] if t['id'] is None)
        self.assertEqual(unassigned['players'][0]['id'], b['id'])
        self.assertTrue(all(p.get('color') and p.get('team_name') for p in result['players']))
        old_session = result['session']['id']
        self.request('/api/sessions', 'POST', {'name': 'New overlay session', 'expected_session_id': old_session})
        result = self.request('/api/overlay')[1]
        self.assertEqual((result['teams'], result['players']), ([], []))
        self.assertEqual(result['session']['name'], 'New overlay session')

    def test_png_route_uses_persisted_selected_theme(self):
        folder = self.app.store.snapshot()['session']['folder_id']
        with patch('mkw_tracker.server.render_folder_png', return_value=b'PNG fixture') as render:
            self.request('/api/settings', 'PATCH', {'theme': 'cow'})
            self.assertEqual(self.request('/api/folders/' + folder + '/report.png')[1], b'PNG fixture')
            self.assertEqual(render.call_args.kwargs['theme']['id'], 'cow')
        self.assertIn('tokens', render.call_args.kwargs['theme'])

    def test_saved_graph_style_applies_to_both_mogi_modes_but_not_ww(self):
        session = self.app.store.active_session()
        with patch('mkw_tracker.server.render_folder_png', return_value=b'png') as render:
            for mode in ('mogi', 'ffa'):
                if mode == 'ffa':
                    session = self.app.switch_tracker_mode(mode, session['id'])['session']
                for style in ('cumulative', 'relative'):
                    status, saved = self.request('/api/settings', 'PATCH', {
                        'mogi_png_graph': style, 'expected_tracker_mode': mode})
                    self.assertEqual(status, 200, saved)
                    self.assertEqual(saved['mogi_png_graph'], style)
                    for name in ('preview', 'report'):
                        with self.subTest(mode=mode, style=style, endpoint=name):
                            url = f"/api/folders/{session['folder_id']}/{name}.png"
                            self.assertEqual(self.request(url), (200, b'png'))
                            self.assertEqual(render.call_args.kwargs['mogi_png_graph'], style)
        session = self.app.switch_tracker_mode('ww', session['id'])['session']
        with patch('mkw_tracker.ww_reports.render_ww_folder_png', return_value=b'ww') as render_ww:
            for name in ('preview', 'report'):
                self.assertEqual(self.request(f"/api/folders/{session['folder_id']}/{name}.png"), (200, b'ww'))
                self.assertNotIn('mogi_png_graph', render_ww.call_args.kwargs)

    def test_png_preview_is_inline_and_download_still_attached(self):
        folder = self.app.store.snapshot()['session']['folder_id']
        before = self.app.store.snapshot()
        with patch('mkw_tracker.server.render_folder_png', return_value=b'PNG fixture') as render:
            for name in ('preview', 'report'):
                with urlopen(self.base + f'/api/folders/{folder}/{name}.png', timeout=5) as response:
                    self.assertEqual(response.read(), b'PNG fixture')
                    self.assertEqual(response.headers['Content-Type'], 'image/png')
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    disposition = response.headers.get('Content-Disposition')
                    if name == 'preview':
                        self.assertIsNone(disposition)
                    else:
                        self.assertEqual(disposition, 'attachment; filename="room-results.png"')
            self.assertEqual(render.call_count, 2)
        self.assertEqual(self.app.store.snapshot(), before)
        self.assertEqual(self.request('/api/folders/missing/preview.png')[0], 404)
        self.assertEqual(self.request(f'/api/folders/{folder}/preview.png',
                                      headers={'Host': 'bad.example'})[0], 403)
        with patch('mkw_tracker.server.render_folder_png', side_effect=RuntimeError('Pillow is required')):
            status, result = self.request(f'/api/folders/{folder}/preview.png')
            self.assertEqual(status, 400)
            self.assertEqual(result, {'error': 'Pillow is required'})

    def test_old_character_icon_routes_are_unavailable(self):
        themes = self.request('/api/state')[1]['themes']
        self.assertFalse(any(theme['icon_url'] for theme in themes))
        for path in ('../app.js', 'Character_Unknown.png', '%2e%2e%2fthemes.py',
                     '/etc/passwd', 'Character_BabyMario.png/extra'):
            self.assertEqual(self.request('/static/character-icons/' + path)[0], 404)

    def test_team_color_and_highlight_routes_preserve_score_and_session_boundaries(self):
        team = self.request('/api/teams', 'POST', {'name': 'My team'})[1]
        route = '/api/teams/' + team['id']
        self.assertEqual(self.request(route, 'PATCH', {'color': '#12AB34'})[1]['color'], '#12ab34')
        self.assertEqual(self.request(route, 'PATCH', {'name': 'Renamed'})[1]['color'], '#12ab34')
        self.assertEqual(self.request('/api/settings', 'PATCH', {'overlay': {
            'highlight_team_id': team['id'], 'alternating_backgrounds': True}})[0], 200)
        result = self.request('/api/overlay')[1]
        self.assertEqual(result['settings']['highlight_team_id'], team['id'])
        self.assertEqual(result['teams'][0]['color'], '#12ab34')
        original = self.app.store.snapshot()['session']
        new = self.request('/api/sessions', 'POST', {'name': 'Other session', 'expected_session_id': original['id']})[1]
        self.assertIsNone(self.request('/api/overlay')[1]['settings']['highlight_team_id'])
        self.assertEqual(self.request('/api/settings', 'PATCH', {'overlay': {'highlight_team_id': team['id']}})[0], 400)
        self.request('/api/folders/' + original['folder_id'] + '/load', 'POST', {'expected_session_id': new['id']})
        self.assertEqual(self.request('/api/overlay')[1]['settings']['highlight_team_id'], team['id'])
        self.assertEqual(self.request(route, 'PATCH', {'color': None})[1]['color'], None)
        for values in ({'color': 'red'}, {'color': '#abc'}, {}, {'other': True}):
            self.assertEqual(self.request(route, 'PATCH', values)[0], 400)
        self.request(route, 'DELETE', {})
        self.assertIsNone(self.request('/api/overlay')[1]['settings']['highlight_team_id'])
        self.assertEqual(self.app.store.snapshot()['room']['points'], 0)

    def test_camera_routes_enumerate_only_on_refresh_and_preserve_origin_guards(self):
        devices = [{'id': '/dev/video10', 'path': '/dev/video10', 'name': 'OBS Virtual Camera',
                    'available': True, 'error': None}]
        with patch.object(self.app.cameras, 'enumerator', return_value=devices) as enumerate_devices:
            self.assertEqual(self.request('/api/state')[1]['cameras']['devices'], [])
            enumerate_devices.assert_not_called()
            self.assertEqual(self.request('/api/cameras')[1]['devices'], devices)
            enumerate_devices.assert_called_once()
            self.assertEqual(self.request('/api/state')[1]['cameras']['devices'], devices)
        options = {'device_id': '/dev/video10', 'name_engine': 'auto', 'use_obs_clock': True,
                   'obs_url': 'ws://127.0.0.1:4455', 'obs_password': 'private-test-token'}
        with patch.object(self.app.cameras, 'start', return_value={'state': 'starting'}) as start:
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 200)
            start.assert_called_once_with(**options, expected_tracker_mode='mogi')
            self.assertEqual(self.request('/api/cameras/start', 'POST', options,
                             {'Origin': 'https://other.example'})[0], 403)
            self.assertEqual(self.request('/api/cameras/start', 'POST', {'arbitrary': 'path'})[0], 400)
        self.assertNotIn('private-test-token', json.dumps(self.request('/api/state')[1]))
        self.assertEqual(self.request('/api/cameras/start', 'POST', {'device_id': '/tmp/video10'})[0], 400)
        self.assertEqual(self.request('/api/cameras/stop', 'POST', {'unexpected': True})[0], 400)
        self.assertEqual(self.request('/api/cameras/stop', 'POST', {})[1]['state'], 'stopped')
        self.assertEqual(self.request('/api/cameras', headers={'Host': 'bad.example'})[0], 403)

    def test_removed_display_font_and_traversal_are_unavailable(self):
        self.assertEqual(self.request('/static/fonts/RacersDelight.otf')[0], 404)
        self.assertEqual(self.request('/static/fonts/../../settings.json')[0], 404)

    def test_client_disconnect_is_not_retried_as_a_second_error_response(self):
        handler = Mock()
        for error in (BrokenPipeError('browser navigated away'), ConnectionResetError('browser closed')):
            self.server.RequestHandlerClass._error(handler, error)
        handler._send.assert_not_called()

    def test_obs_screenshot_start_reuses_saved_password_without_clock_flag(self):
        self.app.obs_connection.save(self.app.obs_connection.resolve(
            url='ws://localhost:4456', password='private-token'))
        with patch.object(self.app.cameras, 'start', return_value={
                'state': 'starting', 'capture_mode': 'obs', 'obs_source': 'Desktop'}) as start:
            status, result = self.request('/api/cameras/start', 'POST', {
                'capture_mode': 'obs', 'obs_source': 'Desktop', 'obs_password': ''})
            self.assertEqual(status, 200)
            self.assertEqual(start.call_args.kwargs['obs_password'], 'private-token')
            self.assertEqual(start.call_args.kwargs['obs_url'], 'ws://localhost:4456')
            self.assertNotIn('private-token', json.dumps(result))
        self.assertNotIn('private-token', json.dumps(self.request('/api/state')[1]))

    def test_obs_screenshot_forget_wins_and_failed_start_does_not_save(self):
        options = {'capture_mode': 'obs', 'obs_source': 'Desktop', 'obs_password': 'private-token'}
        with patch.object(self.app.cameras, 'start', side_effect=ValueError('Busy')):
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 400)
        self.assertFalse(self.app.obs_connection.path.exists())
        def start(**_options):
            self.app.obs_connection.forget_password()
            return {'state': 'starting'}
        with patch.object(self.app.cameras, 'start', side_effect=start):
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 200)
        self.assertFalse(self.app.obs_connection.public()['password_saved'])

    def test_obs_password_is_private_reused_and_can_be_forgotten(self):
        options = {'device_id': '/dev/video10', 'use_obs_clock': True,
                   'obs_url': 'ws://localhost:4456', 'obs_password': 'private☆token'}
        with patch.object(self.app.cameras, 'start', return_value={'state': 'starting'}) as start:
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 200)
            self.assertEqual(self.request('/api/cameras/start', 'POST', {
                'device_id': '/dev/video10', 'use_obs_clock': True})[0], 200)
            self.assertEqual(start.call_args.kwargs['obs_password'], options['obs_password'])
            self.assertEqual(start.call_args.kwargs['obs_url'], options['obs_url'])
        state = self.request('/api/state')[1]
        self.assertTrue(state['obs_connection']['password_saved'])
        self.assertNotIn('private☆token', json.dumps(state, ensure_ascii=False))
        self.assertNotIn('private☆token', json.dumps(self.request('/api/cameras')[1], ensure_ascii=False))
        for path in ('/api/obs/credentials', '/obs-connection.json', '/static/../obs-connection.json'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/api/obs/credentials', 'DELETE', {}, {'Origin': 'https://bad.example'})[0], 403)
        self.assertTrue(self.app.obs_connection.public()['password_saved'])
        self.assertEqual(self.request('/api/obs/credentials', 'DELETE', {})[1]['password_saved'], False)
        self.assertEqual(self.app.obs_connection.resolve()['password'], '')

    def test_bad_password_is_rejected_before_start_and_failed_start_does_not_save(self):
        options = {'device_id': '/dev/video10', 'use_obs_clock': True, 'obs_password': 'private-token-\ud800'}
        with patch.object(self.app.cameras, 'start') as start:
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 400)
            start.assert_not_called()
        self.assertFalse(self.app.obs_connection.public()['password_saved'])
        options['obs_password'] = 'private-token'
        with patch.object(self.app.cameras, 'start', side_effect=ValueError('Busy')):
            self.assertEqual(self.request('/api/cameras/start', 'POST', options)[0], 400)
        self.assertFalse(self.app.obs_connection.path.exists())

    def test_password_save_failure_reports_warning_without_losing_started_capture(self):
        options = {'device_id': '/dev/video10', 'use_obs_clock': True, 'obs_password': 'private-token'}
        with patch.object(self.app.cameras, 'start', return_value={'state': 'starting'}), \
                patch.object(Path, 'replace', side_effect=OSError('private-token')), \
                patch.object(self.app.obs_connection, '_save_windows', side_effect=OSError('private-token')):
            status, result = self.request('/api/cameras/start', 'POST', options)
        self.assertEqual(status, 200)
        self.assertEqual(result['state'], 'starting')
        self.assertTrue(result['credential_warning'])
        self.assertNotIn('private-token', json.dumps(result))
        self.assertFalse(self.app.obs_connection.public()['password_saved'])

    def test_forget_during_camera_start_wins_over_deferred_save(self):
        self.app.obs_connection.save(self.app.obs_connection.resolve(password='private-token'))
        def start(**_options):
            self.app.obs_connection.forget_password()
            return {'state': 'starting'}
        with patch.object(self.app.cameras, 'start', side_effect=start):
            self.assertEqual(self.request('/api/cameras/start', 'POST', {
                'device_id': '/dev/video10', 'use_obs_clock': True})[0], 200)
        self.assertFalse(self.app.obs_connection.public()['password_saved'])

    def test_current_pending_race_snapshot_follows_latch_until_standard_review(self):
        store = self.app.store
        race = store.create_race('current', 'Mario Circuit')
        store.create_race('other', 'Rainbow Road')
        store.reorder_history(race['folder_id'], ['other', 'current'], ['current', 'other'])
        self.assertEqual(self.request('/api/state')[1]['capture']['current_race']['id'], 'other')
        self.app.capture.state['race_id'] = race['id']
        self.assertEqual(self.request('/api/state')[1]['capture']['current_race']['id'], race['id'])
        self.app.capture.state['pending'].append({'id': 'candidate', 'race_id': race['id']})
        state = self.request('/api/state')[1]
        self.assertEqual(state['capture']['pending'][0]['race_id'], race['id'])
        self.assertEqual(state['room']['points'], 0)
        ids = [store.add_player(str(index))['id'] for index in range(12)]
        store.submit_result(race['id'], ids)
        self.assertIsNone(self.request('/api/state')[1]['capture']['current_race'])
        self.assertEqual(store.snapshot()['room']['points'], 82)


if __name__ == '__main__':
    unittest.main()
