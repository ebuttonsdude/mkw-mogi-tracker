"""WW mode/API isolation with temporary data and no capture hardware."""

import json
import importlib.util
import io
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch
from uuid import uuid4

from mkw_tracker.server import Application, make_server


class WWModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.directory.name) / 'tracker.sqlite3', self.directory.name)
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
            payload = response.read()
            return response.status, (json.loads(payload) if 'application/json' in response.headers['Content-Type']
                                     else payload)

    def context(self):
        return {'expected_tracker_mode': self.app.tracker_mode,
                'expected_session_id': self.app.store.snapshot()['session']['id']}

    def switch(self, mode):
        status, result = self.request('/api/tracker-mode', 'POST', {'tracker_mode': mode, **self.context()})
        self.assertEqual(status, 200, result)
        return result

    def race(self, course='Rainbow Road', **values):
        status, race = self.request('/api/ww/races', 'POST', {'course': course, **self.context(), **values})
        self.assertEqual(status, 200, race)
        return race

    def result(self, race, **values):
        return self.request(f"/api/ww/races/{race['id']}/result", 'PATCH', {
            'position': 18, 'vr_after': 8759, 'expected_revision': race['revision'],
            **self.context(), **values})

    def test_modes_restore_their_own_sessions_results_and_output(self):
        original_store = self.app.store
        race = original_store.create_race('mogi-race', 'Rainbow Road')
        original_store.review_result(race['id'], [{'name': str(i)} for i in range(12)])
        before = original_store.snapshot()
        self.switch('ww')
        self.assertNotEqual(before['session']['id'], self.app.store.snapshot()['session']['id'])
        self.assertEqual(self.app.store.snapshot()['races'], [])
        ww_race = self.race()
        self.assertEqual(self.result(ww_race, vr_before=8805, delta=-46)[0], 200)
        status, overlay = self.request('/api/overlay')
        self.assertEqual(status, 200)
        self.assertEqual(overlay['ww']['current_vr'], 8759)
        self.assertEqual(overlay['teams'], [])
        self.assertEqual(overlay['players'], [])
        ww_session = self.app.store.snapshot()['session']['id']
        self.switch('mogi')
        self.assertEqual(self.app.store.snapshot(), before)
        self.assertEqual(self.request('/api/overlay')[1]['room']['points'], 82)
        self.assertEqual(self.app.obs_text_path.name, 'team-standings.txt')
        self.switch('ww')
        self.assertEqual(self.app.store.snapshot()['session']['id'], ww_session)
        self.assertEqual(self.app.store.snapshot()['ww']['current_vr'], 8759)
        self.assertEqual(self.app.obs_text_path.name, 'ww-rating.txt')

    def test_overlay_hotkeys_accept_bodyless_or_empty_json_posts_in_either_mode(self):
        for mode in ('mogi', 'ww'):
            if self.app.tracker_mode != mode:
                self.switch(mode)
            before = self.app.store.snapshot()
            for data in (None, {}, {'expected_tracker_mode': mode}):
                for action, visible in [('hide', False), ('show', True),
                                        ('toggle', False), ('toggle', True)]:
                    with self.subTest(mode=mode, data=data, action=action):
                        self.assertEqual(self.request('/api/overlay/' + action, 'POST', data),
                                         (200, {'visible': visible}))
                        self.assertEqual(self.request('/api/overlay')[1]['visible'], visible)
                        self.assertEqual(self.request('/api/state')[1]['display_standings']['visible'], visible)
            self.assertEqual(self.app.store.snapshot(), before)

    def test_overlay_hotkey_exception_preserves_origin_and_payload_guards(self):
        self.switch('ww')
        for data in (None, {}):
            for headers in ({'Host': 'malicious.example'}, {'Origin': 'https://other.example'},
                            {'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(data=data, headers=headers):
                    self.assertEqual(self.request('/api/overlay/toggle', 'POST', data,
                                                  headers=headers)[0], 403)
                    self.assertTrue(self.app.overlay_visible)
        for data in ({'visible': False}, {'expected_tracker_mode': 'mogi'},
                     {'expected_tracker_mode': None}, {'expected_tracker_mode': 'invalid'}):
            with self.subTest(data=data):
                self.assertEqual(self.request('/api/overlay/toggle', 'POST', data)[0], 400)
                self.assertTrue(self.app.overlay_visible)
        for raw, headers in ((b'{', {'Content-Type': 'application/json'}),
                             (b'[]', {'Content-Type': 'application/json'}),
                             (b'{}', {'Content-Type': 'text/plain'}),
                             (b'', {'Transfer-Encoding': 'chunked'})):
            with self.subTest(raw=raw, headers=headers):
                self.assertEqual(self.request('/api/overlay/toggle', 'POST', raw=raw, headers=headers)[0], 400)
                self.assertTrue(self.app.overlay_visible)
        self.assertEqual(self.request('/api/overlay/toggle')[0], 404)
        self.assertTrue(self.app.overlay_visible)

    def test_bodyless_overlay_hotkeys_do_not_relax_other_mutation_routes(self):
        for mode in ('mogi', 'ww'):
            if self.app.tracker_mode != mode:
                self.switch(mode)
            before = self.app.store.snapshot()
            for path, method in (('/api/settings', 'PATCH'), ('/api/sessions', 'POST'),
                                 ('/api/cameras/start', 'POST')):
                with self.subTest(mode=mode, path=path):
                    self.assertEqual(self.request(path, method)[0], 400)
            self.assertEqual(self.app.store.snapshot(), before)

    def test_unknown_change_is_not_zero_in_course_statistics(self):
        self.switch('ww')
        self.assertEqual(self.result(self.race(), vr_before=8805, delta=-46)[0], 200)
        self.assertEqual(self.result(self.race(), vr_after=8736, position=16)[0], 200)
        ww = self.request('/api/state')[1]['ww']
        self.assertEqual(ww['courses'][0]['races'], 2)
        self.assertEqual(ww['courses'][0]['known_changes'], 1)
        self.assertEqual(ww['courses'][0]['average_change'], -46)
        self.assertIsNone(ww['total_change'])

    def test_rolling_change_and_toggles_are_exposed_in_overlay_and_state(self):
        self.switch('ww')
        first, second = self.race(), self.race('Mario Circuit')
        self.assertEqual(self.result(first, vr_after=8010, delta=10)[0], 200)
        self.assertEqual(self.result(second, vr_after=8005, delta=-5)[0], 200)
        self.race('Crown City')  # A pending race does not displace recorded results.
        status, settings = self.request('/api/settings', 'PATCH', {
            'overlay': {'ww_show_last_10': False, 'ww_show_character': False, 'show_header': True},
            'expected_tracker_mode': 'ww'})
        self.assertEqual(status, 200, settings)
        overlay = self.request('/api/overlay')[1]
        self.assertEqual(overlay['ww']['last_10_change'], 5)
        self.assertEqual(overlay['ww']['last_10_race_count'], 2)
        self.assertFalse(overlay['settings']['ww_show_last_10'])
        self.assertFalse(overlay['settings']['ww_show_character'])
        self.assertTrue(overlay['settings']['show_header'])
        state = self.request('/api/state')[1]
        self.assertEqual(state['display_standings']['ww']['last_10_change'], 5)
        self.switch('mogi')
        self.assertNotIn('ww', self.request('/api/overlay')[1])

    def test_overlay_current_track_uses_session_bound_detection_not_history(self):
        self.switch('ww')
        self.result(self.race('Rainbow Road'))
        self.assertIsNone(self.request('/api/overlay')[1]['ww']['current_track'])
        session_id = self.app.store.snapshot()['session']['id']
        # The prerace detector knows the course before a race row is created.
        with self.app.lock:
            self.app.capture.state.update(course='Crown City', session_id=session_id,
                                          race_id=None, in_prerace=True)
        overlay = self.request('/api/overlay')[1]
        self.assertEqual(overlay['ww']['current_track'], 'Crown City')
        self.assertNotIn('capture', overlay)
        self.assertEqual(self.request('/api/state')[1]['display_standings']['ww']['current_track'],
                         'Crown City')
        with self.app.lock:
            self.app.capture.state['course'] = 'Boo Cinema'
        self.assertEqual(self.request('/api/overlay')[1]['ww']['current_track'], 'Boo Cinema')
        with self.app.lock:
            self.app.capture.state['course'] = None
        self.assertIsNone(self.request('/api/overlay')[1]['ww']['current_track'])

    def test_overlay_does_not_publish_a_previous_sessions_detected_course(self):
        self.switch('ww')
        session_id = self.app.store.snapshot()['session']['id']
        with self.app.lock:
            self.app.capture.state.update(course='Crown City', session_id=session_id)
            self.app.store.create_session('Next session', session_id)
        self.assertIsNone(self.request('/api/overlay')[1]['ww']['current_track'])
        self.switch('mogi')
        self.assertNotIn('ww', self.request('/api/overlay')[1])

    def test_live_derived_change_updates_public_stats_but_cannot_be_requested_by_editor(self):
        self.switch('ww')
        first, second = self.race('Crown City'), self.race('Boo Cinema')
        self.assertEqual(self.result(first, vr_after=8000)[0], 200)
        with self.app.lock:
            self.app.store.set_ww_result(second['id'], position=5, vr_after=8050,
                                         source='capture', derive_from_previous=True)
        self.assertEqual(self.request('/api/overlay')[1]['ww']['known_change_sum'], 50)
        first = self.app.store._race(first['id'])
        self.assertEqual(self.result(first, vr_after=8020)[0], 200)
        self.assertEqual(self.request('/api/state')[1]['races'][1]['ww_result']['delta'], 30)
        self.assertEqual(self.request('/api/overlay')[1]['ww']['known_change_sum'], 30)
        self.assertEqual(self.result(first, derive_from_previous=True)[0], 400)

    def test_correction_replaces_result_and_stale_revision_is_rejected(self):
        self.switch('ww')
        race = self.race()
        status, accepted = self.result(race)
        self.assertEqual(status, 200, accepted)
        status, corrected = self.result(accepted, vr_after=8760, position=17)
        self.assertEqual(status, 200, corrected)
        status, error = self.result(accepted, vr_after=8700)
        self.assertEqual(status, 400, error)
        ww = self.request('/api/state')[1]['ww']
        self.assertEqual(ww['current_vr'], 8760)
        self.assertEqual(ww['recorded_races'], 1)

    def test_stale_mogi_actions_cannot_mutate_ww_or_start_capture(self):
        stale = self.context()
        self.switch('ww')
        before = self.app.store.snapshot()
        with patch.object(self.app, 'start_camera') as start, patch.object(self.app.imports, 'start') as imported:
            for path, values in [('/api/recordings', {'label': 'Wrong mode'}),
                                 ('/api/sessions', {'name': 'Wrong mode'}),
                                 ('/api/cameras/start', {'capture_mode': 'obs', 'obs_source': 'Game'}),
                                 ('/api/imports', {'path': '/not/a/video.mkv'}),
                                 ('/api/cameras/stop', {})]:
                with self.subTest(path=path):
                    self.assertEqual(self.request(path, 'POST', {**stale, **values})[0], 400)
            start.assert_not_called()
            imported.assert_not_called()
        self.assertEqual(self.app.store.snapshot(), before)
        self.assertEqual(self.request('/api/recordings', 'POST', {'label': 'Legacy Mogi'})[0], 400)

    def test_stale_upload_is_rejected_without_creating_a_copy(self):
        self.switch('ww')
        status, error = self.request('/api/imports/upload?expected_tracker_mode=mogi', 'POST',
                                     raw=b'not-a-video', headers={'X-Filename': 'sample.mp4'})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.app.active_uploads, set())
        self.assertFalse(self.app.videos.root.exists())

    def test_upload_rechecks_mode_before_reserving_or_creating_file(self):
        def disk_space(_):
            self.app.switch_tracker_mode('ww', self.app.store.snapshot()['session']['id'])
            return Mock(free=1024**4)

        with patch('mkw_tracker.server.shutil.disk_usage', side_effect=disk_space):
            status, error = self.request('/api/imports/upload?expected_tracker_mode=mogi', 'POST',
                                         raw=b'not-a-video', headers={'X-Filename': 'sample.mp4'})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.app.active_uploads, set())
        self.assertEqual(list(self.app.videos.root.iterdir()), [])

    def test_busy_owners_and_failed_cleanup_block_mode_switch(self):
        alive = Mock(is_alive=lambda: True)
        cases = [(self.app.cameras, 'thread', alive), (self.app.capture, 'thread', alive),
                 (self.app.imports, 'thread', alive), (self.app.cameras, 'cleanup_failed', True),
                 (self.app.imports, 'cleanup_failed', True),
                 (self.app, 'active_uploads', {Path(self.directory.name) / 'upload.mp4'})]
        for owner, field, value in cases:
            with self.subTest(owner=type(owner).__name__, field=field), patch.object(owner, field, value):
                status, error = self.request('/api/tracker-mode', 'POST', {
                    'tracker_mode': 'ww', **self.context()})
                self.assertEqual(status, 400, error)
                self.assertEqual(self.app.tracker_mode, 'mogi')
        with patch.object(self.app.imports, 'snapshot', return_value={'active': {'status': 'queued'}}):
            self.assertEqual(self.request('/api/tracker-mode', 'POST', {
                'tracker_mode': 'ww', **self.context()})[0], 400)

    def test_mode_switch_requires_current_session_and_valid_mode(self):
        for mode, session in [('ww', None), ('ww', 'stale'), ('other', self.context()['expected_session_id'])]:
            with self.subTest(mode=mode, session=session):
                status, _ = self.request('/api/tracker-mode', 'POST', {
                    'tracker_mode': mode, 'expected_session_id': session, 'expected_tracker_mode': 'mogi'})
                self.assertEqual(status, 400)
        self.assertEqual(self.app.tracker_mode, 'mogi')

    def test_ww_rejects_mogi_scoring(self):
        self.switch('ww')
        race = self.race()
        for path, values in [('/api/players', {'name': 'Player'}), ('/api/teams', {'name': 'Team'}),
                             (f"/api/races/{race['id']}/results", {'player_ids': []}),
                             (f"/api/races/{race['id']}/penalties", {'points': 5}),
                             ('/api/capture/review', {'candidate_id': 'none'})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, 'POST', {**self.context(), **values})[0], 400)
        self.assertEqual(self.app.store.snapshot()['players'], [])

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is optional')
    def test_ww_png_report_and_preview_render_without_mogi_team_options(self):
        self.switch('ww')
        race = self.race()
        self.assertEqual(self.result(race)[0], 200)
        for name in ('report', 'preview'):
            with self.subTest(name=name):
                status, content = self.request(f"/api/folders/{race['folder_id']}/{name}.png")
                self.assertEqual(status, 200, content)
                self.assertTrue(content.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(self.request(f"/api/folders/{race['folder_id']}/report.png?highlight_team_id=team")[0], 400)
        self.assertEqual(self.request('/api/folders/missing/report.png')[0], 404)

    def test_ww_chapters_and_evidence_are_recording_local(self):
        self.switch('ww')
        status, recording = self.request('/api/recordings', 'POST', {'label': 'Edited video', **self.context()})
        self.assertEqual(status, 200)
        race = self.race(recording_id=recording['id'], offset_ms=12345)
        self.race('Unknown offset', recording_id=recording['id'])
        filename = f'{uuid4()}.png'
        (Path(self.directory.name) / 'evidence' / filename).write_bytes(b'fixture-evidence')
        self.app.store.add_evidence(race['id'], filename, kind='results', label='WW result')
        self.assertEqual(self.request('/api/chapters?recording_id=' + recording['id']),
                         (200, b'00:00:12.345 Rainbow Road\n'))
        images = self.request(f"/api/races/{race['id']}/evidence")[1]['images']
        self.assertEqual(self.request(images[0]['url']), (200, b'fixture-evidence'))
        self.switch('mogi')
        self.assertEqual(self.request('/api/chapters?recording_id=' + recording['id'])[0], 404)
        self.assertEqual(self.request(f"/api/races/{race['id']}/evidence")[0], 404)

    def test_import_context_stays_bound_when_inactive(self):
        mogi_imports, mogi_capture, mogi_store = self.app.imports, self.app.capture, self.app.store
        self.switch('ww')
        ww_imports, ww_capture, ww_store = self.app.imports, self.app.capture, self.app.store
        self.assertIs(mogi_imports.store, mogi_store)
        self.assertIs(mogi_imports.capture, mogi_capture)
        with self.assertRaisesRegex(ValueError, 'mode changed'):
            mogi_imports.check_available()
        self.assertIs(ww_imports.capture, ww_capture)
        self.assertIs(ww_imports.store, ww_store)
        self.assertNotEqual(mogi_imports.state_path, ww_imports.state_path)
        self.switch('mogi')
        self.assertIs(ww_imports.capture, ww_capture)
        with self.assertRaisesRegex(ValueError, 'mode changed'):
            ww_imports.check_available()

    def test_camera_rechecks_mode_after_device_discovery_before_start(self):
        def discover():
            self.app.switch_tracker_mode('ww', self.app.store.snapshot()['session']['id'])
            return [{'id': '/dev/video0', 'available': True}]

        with patch('mkw_tracker.cameras.CAMERA_BACKEND_AVAILABLE', True), \
                patch.object(self.app.cameras, 'enumerator', side_effect=discover), \
                patch.object(self.app.cameras, 'reader_factory') as factory:
            with self.assertRaisesRegex(ValueError, 'mode changed'):
                self.app.cameras.start(device_id='/dev/video0', expected_tracker_mode='mogi')
            factory.assert_not_called()
            self.assertIsNone(self.app.cameras.thread)

    def test_camera_selects_reader_from_reserved_mode_after_switches(self):
        mogi_imports = self.app.imports
        self.switch('ww')
        ww_imports = self.app.imports
        for mode in ('ww', 'mogi'):
            with self.subTest(mode=mode):
                if self.app.tracker_mode != mode:
                    self.switch(mode)
                capture = self.app.capture
                selected = ww_imports if mode == 'ww' else mogi_imports
                inactive = mogi_imports if mode == 'ww' else ww_imports
                reader, client = Mock(), Mock()

                def fake_start(*args):
                    capture.thread = Mock(is_alive=lambda: False)

                with patch.object(selected, 'reader_factory', return_value=reader) as factory, \
                        patch.object(inactive, 'reader_factory') as wrong_factory, \
                        patch.object(capture, 'start', side_effect=fake_start), \
                        patch('mkw_tracker.obs.ObsClient', return_value=client):
                    self.app.cameras.start(capture_mode='obs', obs_source='Game', expected_tracker_mode=mode)
                    self.app.cameras.thread.join(3)
                    self.assertFalse(self.app.cameras.thread.is_alive())
                    factory.assert_called_once_with('auto')
                    wrong_factory.assert_not_called()
                    reader.close.assert_called_once()

    def test_ww_result_rejects_stale_session_after_new_session(self):
        self.switch('ww')
        race = self.race()
        old_context = self.context()
        self.assertEqual(self.request('/api/sessions', 'POST', {'name': 'Next', **old_context})[0], 200)
        status, error = self.request(f"/api/ww/races/{race['id']}/result", 'PATCH', {
            'position': 1, 'vr_after': 9000, 'expected_revision': 0, **old_context})
        self.assertEqual(status, 400, error)
        state = self.app.store.snapshot()
        self.assertIsNone(state['races'][0]['ww_result'])
        self.assertIsNone(state['ww']['current_vr'])

    def test_mode_and_both_histories_survive_restart(self):
        self.app.store.create_race('original-mogi', 'Mario Circuit')
        self.switch('ww')
        race = self.race()
        self.assertEqual(self.result(race)[0], 200)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.app = Application(Path(self.directory.name) / 'tracker.sqlite3', self.directory.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.assertEqual(self.app.tracker_mode, 'ww')
        self.assertEqual(self.request('/api/state')[1]['ww']['current_vr'], 8759)
        self.switch('mogi')
        self.assertEqual([race['id'] for race in self.app.store.snapshot()['races']], ['original-mogi'])


class WWCLIModeTests(unittest.TestCase):
    def test_saved_ww_cli_capture_uses_ww_reader_and_preserves_mogi_pending_queue(self):
        from mkw_tracker.__main__ import main
        from mkw_tracker.ww_capture import WWCaptureCoordinator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / 'tracker.sqlite3', root)
            try:
                race = app.store.create_race('mogi-pending', 'Rainbow Road')
                candidate = {'id': str(uuid4()), 'race_id': race['id'], 'course': race['course'],
                             'rows': [], 'reasons': ['Needs review'],
                             'screenshot_url': f'/evidence/{uuid4()}.png'}
                app.capture.state.update(race_id=race['id'], session_id=race['session_id'],
                                         pending=[candidate], armed=False)
                app.capture._save()
                app.switch_tracker_mode('ww', race['session_id'])
            finally:
                app.close()
            pending_before = (root / 'capture.json').read_bytes()
            reader, client = Mock(), Mock()
            server = Mock(server_address=('127.0.0.1', 9876))
            started = []

            def record_start(coordinator, obs, source):
                started.append((coordinator, obs, source))

            tessdata = root / 'custom-tessdata'
            arguments = ['mkw-tracker', '--data-dir', str(root), '--obs-source', 'Game',
                         '--tessdata', str(tessdata)]
            with patch('sys.argv', arguments), patch('sys.stdout', new_callable=io.StringIO), \
                    patch('mkw_tracker.__main__.make_server', return_value=server), \
                    patch('mkw_tracker.obs.ObsClient', return_value=client), \
                    patch('mkw_tracker.ww_recognition.WWReader', return_value=reader) as ww_reader, \
                    patch('mkw_tracker.neural_ocr.make_name_reader') as names, \
                    patch.object(WWCaptureCoordinator, 'start', autospec=True, side_effect=record_start):
                main()
                ww_reader.assert_called_once_with(name_engine='auto', tessdata_path=tessdata)
                names.assert_not_called()
            self.assertEqual(len(started), 1)
            coordinator, actual_client, source = started[0]
            self.assertIsInstance(coordinator, WWCaptureCoordinator)
            self.assertIs(coordinator.reader, reader)
            self.assertIs(actual_client, client)
            self.assertEqual(source, 'Game')
            self.assertEqual(coordinator.state_path, root / 'ww' / 'capture.json')
            self.assertEqual((root / 'capture.json').read_bytes(), pending_before)
            self.assertEqual(json.loads(pending_before)['pending'], [candidate])
            reader.close.assert_called_once()
            server.server_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
