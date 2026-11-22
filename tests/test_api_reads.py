"""Narrow API reads preserve fresh state without loading lifetime history."""

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from uuid import uuid4

from mkw_tracker.server import Application, make_server


class APIReadTests(unittest.TestCase):
    def test_license_page_is_fixed_public_text_with_host_and_frame_protection(self):
        with urlopen(self.base + '/license', timeout=5) as response:
            content = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn('text/html', response.headers['Content-Type'])
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertIn('GNU AGPLv3', content)
        self.assertIn('NO WARRANTY', content)
        self.assertIn('NVIDIA-Reader-Exception.txt', content)
        self.assertIn('https://github.com/ebuttonsdude/mkw-mogi-tracker', content)
        self.assertNotIn(str(self.root), content)
        self.assertEqual(self.request('/license', headers={'Host': 'evil.example'})[0], 403)
        for path in ('/license/../credentials.py', '/static/legal.html', '/source.tar.gz'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.app = Application(self.root / 'tracker.sqlite3', self.root)
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

    def request(self, path, method='GET', data=None, *, raw=None, headers=None):
        body = raw if raw is not None else None if data is None else json.dumps(data).encode()
        request_headers = {'Content-Type': 'application/json'} if data is not None else {}
        request_headers.update(headers or {})
        request = Request(self.base + path, body, request_headers, method=method)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, (json.loads(body) if 'application/json' in response.headers['Content-Type'] else body)

    @contextmanager
    def no_full_history(self):
        original = self.app.store.snapshot

        def scoped_only(*args, **kwargs):
            if kwargs.get('include_history') is not False:
                raise AssertionError('route loaded lifetime history')
            return original(*args, **kwargs)

        with patch.object(self.app.store, 'snapshot', side_effect=scoped_only), patch('traceback.print_exception'):
            yield

    def test_evidence_lookup_reflects_new_evidence_without_history_snapshot(self):
        with self.app.lock:
            race = self.app.store.create_race('evidence-race', 'Crown City')
        path = f"/api/races/{race['id']}/evidence"
        with self.no_full_history():
            self.assertEqual(self.request(path), (200, {'race_id': race['id'], 'images': []}))
            filename = f'{uuid4()}.png'
            (self.root / 'evidence' / filename).write_bytes(b'controlled evidence')
            with self.app.lock:
                self.app.store.add_evidence(race['id'], filename, kind='results', label='New proof')
            status, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual([(image['url'], image['label']) for image in body['images']],
                             [('/evidence/' + filename, 'New proof')])
            self.assertEqual(self.request('/api/races/missing/evidence')[0], 404)

    def test_race_patch_preserves_latest_metadata_without_history_snapshot(self):
        with self.app.lock:
            recording = self.app.store.create_recording('Recording')
            race = self.app.store.create_race('editable-race', 'Crown City', recording['id'], 1000)
            self.app.store.update_race(race['id'], 'Rainbow Road', recording['id'], 2500)
        with self.no_full_history():
            status, updated = self.request('/api/races/' + race['id'], 'PATCH', {'course': 'Mario Circuit'})
        self.assertEqual(status, 200)
        self.assertEqual((updated['course'], updated['recording_id'], updated['offset_ms']),
                         ('Mario Circuit', recording['id'], 2500))

    def test_player_patch_uses_latest_assignment_and_archived_player_stays_missing(self):
        with self.app.lock:
            player = self.app.store.add_player('Original')
            team = self.app.store.create_team('Assigned')
            self.app.store.update_player(player['id'], 'Current', team['id'])
        with self.no_full_history():
            status, updated = self.request('/api/players/' + player['id'], 'PATCH', {'name': 'Renamed 選手'})
            self.assertEqual(status, 200)
            self.assertEqual((updated['name'], updated['team_id']), ('Renamed 選手', team['id']))
            with self.app.lock:
                self.app.store.delete_player(player['id'])
            self.assertEqual(self.request('/api/players/' + player['id'], 'PATCH', {'name': 'Archived'})[0], 404)

    def test_delete_guard_reads_recent_confirmation_without_history_snapshot(self):
        with self.app.lock:
            race = self.app.store.create_race('confirmed-race', 'Crown City')
            self.app.store.review_result(race['id'], [{'name': str(i)} for i in range(12)])
            self.app.imports.cleanup_failed = True
        try:
            with self.no_full_history():
                self.assertEqual(self.request('/api/races/' + race['id'], 'DELETE', {})[0], 400)
                with self.app.lock:
                    self.app.imports.cleanup_failed = False
                status, deleted = self.request('/api/races/' + race['id'], 'DELETE', {})
                self.assertEqual(status, 200)
                self.assertEqual(deleted['deleted_race_ids'], [race['id']])
                self.assertEqual(self.request('/api/races/' + race['id'], 'DELETE', {})[0], 404)
        finally:
            self.app.imports.cleanup_failed = False

    def test_chapters_read_recent_edits_across_sessions_without_history_snapshot(self):
        with self.app.lock:
            recording = self.app.store.create_recording('Shared recording')
            old = self.app.store.active_session()
            self.app.store.create_race('old-race', 'Crown City', recording['id'], 2000)
            self.app.store.create_session('Current', old['id'])
            self.app.store.create_race('current-race', 'Mario Circuit', recording['id'], 1000)
            self.app.store.update_race('old-race', 'Rainbow Road', recording['id'], 500)
        with self.no_full_history():
            self.assertEqual(self.request('/api/chapters?recording_id=' + recording['id']),
                             (200, b'00:00:00.500 Rainbow Road\n00:00:01.000 Mario Circuit\n'))
            self.assertEqual(self.request('/api/chapters?recording_id=missing')[0], 404)
            self.assertEqual(self.request('/api/chapters')[0], 404)

    def test_marker_download_uses_current_recording_data_and_never_omits_an_unknown_time(self):
        with self.app.lock:
            store = self.app.store
            rec = store.create_recording('Shared recording')['id']
            store.create_race('first', 'Mario Circuit', rec, 0)
            store.create_session('After the break', store.active_session()['id'])
            store.create_race('second', 'Rainbow Road', rec)
            other = store.create_recording('Other recording')['id']
            store.create_race('excluded', 'Other race', other)
        url = '/api/chapters?recording_id=' + rec
        with self.no_full_history():
            status, body = self.request(url + '&format=edl')
            self.assertEqual(status, 400)
            self.assertIn('Rainbow Road', body['error'])
            self.assertEqual(self.request(url + '&format=txt'), (200, b'00:00:00.000 Mario Circuit\n'))
            self.assertEqual(self.request(url + '&format=csv')[0], 400)
            with self.app.lock:
                store.update_race('second', "Bowser's Castle?", rec, 45849)
            with urlopen(self.base + url + '&format=edl', timeout=5) as response:
                self.assertEqual(response.headers.get_filename(), 'race-chapters.edl')
                self.assertEqual(response.headers.get_content_charset(), 'utf-8')
                body = response.read().decode('utf-8')
            self.assertEqual(body.count('|C:ResolveColorBlue'), 2)
            self.assertLess(body.index('|M:Mario Circuit'), body.index("|M:Bowser's Castle?"))
            self.assertIn("01:00:45:51 01:00:45:52\r\n |C:ResolveColorBlue |M:Bowser's Castle? |D:1", body)

    def test_upload_admission_uses_fresh_folder_lookup_without_history_snapshot(self):
        with self.app.lock:
            folder = self.app.store.create_folder('Upload destination')
        job = {'id': 'fake-job', 'status': 'queued'}
        with self.no_full_history(), patch.object(self.app.imports, 'start', return_value=job) as start:
            status, body = self.request('/api/imports/upload?folder_id=' + folder['id'], 'POST',
                                        raw=b'fake decoder input', headers={'X-Filename': 'fixture.mkv'})
            self.assertEqual((status, body), (200, job))
            self.assertEqual(start.call_args.args[1], folder['id'])
            self.assertEqual(self.app.active_uploads, set())

    def test_upload_rechecks_folder_deleted_between_admission_steps(self):
        with self.app.lock:
            folder = self.app.store.create_folder('Delete before admission')
        original = self.app.videos._directory

        @contextmanager
        def remove_folder_before_registration(**kwargs):
            with original(**kwargs) as descriptor:
                with self.app.lock:
                    self.app.store.delete_folder(folder['id'])
                yield descriptor

        with self.no_full_history(), patch.object(self.app.videos, '_directory', remove_folder_before_registration), \
                patch.object(self.app.imports, 'start') as start:
            status, body = self.request('/api/imports/upload?folder_id=' + folder['id'], 'POST',
                                        raw=b'fake decoder input', headers={'X-Filename': 'fixture.mkv'})
        self.assertEqual(status, 400)
        self.assertIn('existing history folder', body['error'])
        start.assert_not_called()
        self.assertEqual(self.app.active_uploads, set())
        self.assertEqual(list(self.app.videos.root.iterdir()), [])

    def test_overlay_and_archived_report_read_only_the_needed_session(self):
        with self.app.lock:
            old = self.app.store.active_session()
            self.app.store.create_race('old-race', 'Crown City')
            current = self.app.store.create_session('Current', old['id'])
            self.app.store.create_race('current-race', 'Mario Circuit')
            team = self.app.store.create_team('Current team')
            self.app.update_settings({'overlay': {'highlight_team_id': team['id']}})
        with self.no_full_history():
            status, overlay = self.request('/api/overlay')
            self.assertEqual(status, 200)
            self.assertEqual(overlay['room']['logged_races'], 1)
            with patch('mkw_tracker.server.render_folder_png', return_value=b'controlled png') as render:
                self.assertEqual(self.request('/api/folders/' + old['folder_id'] + '/report.png'),
                                 (200, b'controlled png'))
            snapshot, folder_id = render.call_args.args
            self.assertEqual(snapshot['session']['id'], old['id'])
            self.assertEqual([race['id'] for race in snapshot['races']], ['old-race'])
            self.assertEqual(folder_id, old['folder_id'])
            self.assertEqual(render.call_args.kwargs['highlight_session_id'], current['id'])

    def state(self):
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        return state

    def mutate(self, path, method, data):
        status, body = self.request(path, method, data)
        self.assertEqual(status, 200, body)
        return body

    def test_state_immediately_reflects_result_correction_and_penalty_mutations(self):
        with self.app.lock:
            players = [self.app.store.add_player(f'Player {index}')['id'] for index in range(12)]
        session = self.state()['session']['id']
        race = self.mutate('/api/races', 'POST', {'course': 'Crown City', 'expected_session_id': session})
        self.assertEqual(self.state()['room']['points'], 0)
        route = '/api/races/' + race['id']
        for correction, order in ((False, players), (True, players[::-1])):
            with self.subTest(correction=correction):
                self.mutate(route + '/results', 'POST', {'player_ids': order, 'correction': correction})
                state = self.state()
                self.assertEqual(state['room']['points'], 82)
                self.assertEqual(state['room']['scored_races'], 1)
                self.assertEqual(state['races'][0]['placements'][0]['player_id'], order[0])
                self.assertEqual(state['races'][0]['revision'], 2 if correction else 1)
        penalty = self.mutate(route + '/penalties', 'POST', {
            'player_id': players[-1], 'points': 2, 'reason': 'Controlled penalty'})
        state = self.state()
        self.assertEqual((state['room']['points'], state['room']['net_points']), (82, 80))
        self.assertEqual(state['races'][0]['placements'][0]['net_points'], 13)
        self.mutate('/api/penalties/' + penalty['id'], 'DELETE', {})
        self.assertEqual(self.state()['room']['net_points'], 82)
        self.mutate(route, 'DELETE', {})
        state = self.state()
        self.assertEqual(state['races'], [])
        self.assertEqual(state['room']['points'], 0)

    def test_state_immediately_reflects_roster_and_team_mutations(self):
        session = self.state()['session']['id']
        team = self.mutate('/api/teams', 'POST', {'name': 'New team', 'expected_session_id': session})
        self.assertEqual(self.state()['teams'][0]['id'], team['id'])
        player = self.mutate('/api/players', 'POST', {'name': 'New player', 'expected_session_id': session})
        self.assertEqual(self.state()['players'][0]['id'], player['id'])
        player_route, team_route = '/api/players/' + player['id'], '/api/teams/' + team['id']
        for values in ({'team_id': team['id']}, {'name': 'Renamed 選手'}):
            with self.subTest(player=values):
                self.mutate(player_route, 'PATCH', values)
                changed = self.state()['players'][0]
                for key, value in values.items():
                    self.assertEqual(changed[key], value)
        for values in ({'name': 'Renamed team'}, {'color': '#abcdef'}):
            with self.subTest(team=values):
                self.mutate(team_route, 'PATCH', values)
                changed = self.state()['teams'][0]
                for key, value in values.items():
                    self.assertEqual(changed[key], value)
        self.mutate(player_route, 'DELETE', {})
        state = self.state()
        self.assertEqual(state['players'], [])
        self.assertEqual(state['archived_players'][0]['id'], player['id'])
        self.mutate(team_route, 'DELETE', {})
        state = self.state()
        self.assertEqual(state['teams'], [])
        self.assertEqual(state['archived_teams'][0]['id'], team['id'])

    def test_state_immediately_reflects_sessions_folders_and_retained_archives(self):
        initial = self.state()['session']
        race = self.mutate('/api/races', 'POST', {
            'course': 'Retained course', 'expected_session_id': initial['id']})
        folder = self.mutate('/api/folders', 'POST', {'name': 'Additional folder'})
        self.assertIn(folder['id'], [entry['id'] for entry in self.state()['folders']])
        self.mutate('/api/folders/' + folder['id'], 'PATCH', {'name': 'Renamed folder', 'lounge_id': '12345'})
        changed = next(entry for entry in self.state()['folders'] if entry['id'] == folder['id'])
        self.assertEqual((changed['name'], changed['lounge_id']), ('Renamed folder', '12345'))
        current = self.mutate('/api/sessions', 'POST', {'name': 'Current', 'expected_session_id': initial['id']})
        state = self.state()
        self.assertEqual(state['session']['id'], current['id'])
        self.assertEqual(state['room']['logged_races'], 0)
        self.assertEqual(state['races'][0]['id'], race['id'])
        self.assertEqual(state['races'][0]['session_id'], initial['id'])
        self.mutate('/api/folders/' + initial['folder_id'] + '/load', 'POST', {'expected_session_id': current['id']})
        state = self.state()
        self.assertEqual(state['session'], initial)
        self.assertEqual(state['room']['logged_races'], 1)
        self.mutate('/api/folders/' + folder['id'], 'DELETE', {})
        state = self.state()
        self.assertNotIn(folder['id'], [entry['id'] for entry in state['folders']])
        self.assertNotIn(folder['session_id'], [entry['id'] for entry in state['sessions']])

    def test_state_immediately_reflects_recording_chapter_and_evidence_mutations(self):
        session = self.state()['session']['id']
        recording = self.mutate('/api/recordings', 'POST', {'label': 'New recording'})
        self.assertEqual(self.state()['recordings'], [recording])
        race = self.mutate('/api/races', 'POST', {
            'course': 'Crown City', 'expected_session_id': session,
            'recording_id': recording['id'], 'offset_ms': 1000})
        self.assertEqual(self.state()['races'][0]['offset_ms'], 1000)
        self.mutate('/api/races/' + race['id'], 'PATCH', {'course': 'Rainbow Road', 'offset_ms': 2500})
        changed = self.state()['races'][0]
        self.assertEqual((changed['course'], changed['recording_id'], changed['offset_ms']),
                         ('Rainbow Road', recording['id'], 2500))
        filename = f'{uuid4()}.png'
        (self.root / 'evidence' / filename).write_bytes(b'controlled evidence')
        with self.app.lock:
            self.app.store.add_evidence(race['id'], filename, kind='results', label='Fresh evidence')
        changed = self.state()['races'][0]
        self.assertEqual(changed['evidence_url'], '/evidence/' + filename)
        self.assertEqual(changed['evidence'][0]['label'], 'Fresh evidence')

    def test_state_immediately_reflects_settings_visibility_and_tracker_mode(self):
        initial = self.state()['session']
        for values in ({'theme': 'luigi'}, {'standings_format': 'md'}, {'overlay': {'title': 'Fresh title'}}):
            with self.subTest(settings=values):
                self.mutate('/api/settings', 'PATCH', values)
                state = self.state()
                for key, value in values.items():
                    if key == 'overlay':
                        self.assertEqual(state['settings']['overlay']['title'], value['title'])
                        self.assertEqual(state['display_standings']['settings']['title'], value['title'])
                    else:
                        self.assertEqual(state['settings'][key], value)
                self.assertEqual(state['theme']['id'], state['settings']['theme'])
        for action, visible in (('hide', False), ('show', True), ('toggle', False)):
            with self.subTest(visibility=action):
                self.mutate('/api/overlay/' + action, 'POST', {})
                self.assertEqual(self.state()['display_standings']['visible'], visible)
        self.mutate('/api/tracker-mode', 'POST', {'tracker_mode': 'ww', 'expected_session_id': initial['id']})
        state = self.state()
        self.assertEqual(state['tracker_mode'], 'ww')
        self.assertEqual(state['races'], [])
        self.assertIn('ww', state)
        self.mutate('/api/ww/player', 'POST', {
            'name': 'Worldwide 選手', 'expected_tracker_mode': 'ww', 'expected_session_id': state['session']['id']})
        self.assertEqual(self.state()['ww']['player_name'], 'Worldwide 選手')
        self.mutate('/api/tracker-mode', 'POST', {
            'tracker_mode': 'mogi', 'expected_tracker_mode': 'ww', 'expected_session_id': state['session']['id']})
        state = self.state()
        self.assertEqual(state['tracker_mode'], 'mogi')
        self.assertEqual(state['session'], initial)
        self.assertNotIn('ww', state)

    def test_state_immediately_reflects_capture_and_import_progress_without_database_writes(self):
        self.state()  # Prime the same read path before changing only runtime state.
        for status, percent in (('running', 10), ('running', 75), ('completed', 100)):
            with self.subTest(status=status, percent=percent):
                with self.app.lock:
                    self.app.capture.status = 'connected' if status == 'running' else 'manual'
                    self.app.capture.message = f'Controlled sample {percent}'
                    self.app.capture.sample_timing = {'sample_count': percent}
                    self.app.cameras.status.update(state=status, message=f'Controlled camera {percent}')
                    self.app.imports.jobs[:] = [{'id': 'controlled-import', 'status': status,
                                                'percent': percent, 'progress_seconds': percent * 3}]
                state = self.state()
                self.assertEqual(state['capture']['message'], f'Controlled sample {percent}')
                self.assertEqual(state['capture']['sample_timing']['sample_count'], percent)
                self.assertEqual(state['cameras']['status']['message'], f'Controlled camera {percent}')
                self.assertEqual(state['imports']['jobs'][0]['percent'], percent)
                self.assertEqual(state['imports']['jobs'][0]['progress_seconds'], percent * 3)
                if status == 'completed':
                    self.assertIsNone(state['imports']['active'])
                else:
                    self.assertEqual(state['imports']['active']['id'], 'controlled-import')


if __name__ == '__main__':
    unittest.main()
