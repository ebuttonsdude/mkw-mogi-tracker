"""Named sources and opt-in network boundaries; all data and HTTP are disposable."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mkw_tracker import lounge
from mkw_tracker.lounge import LoungeCache, last_five_form, normalize, weekly
from mkw_tracker.overlay_profiles import DEFAULT_LOUNGE, OverlayProfiles
from mkw_tracker.server import Application, make_server
from mkw_tracker.settings import DEFAULT_SETTINGS
from mkw_tracker.themes import resolve_theme


def payload(name='Example', **values):
    return dict({'playerId': 123, 'name': name, 'season': 3, 'game': 'mkworld12p',
                 'mmr': 7000, 'rank': 'Gold', 'eventsPlayed': 0, 'mmrChanges': []}, **values)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = OverlayProfiles(self.root, resolve_theme)

    def test_independent_persistence_and_default_inheritance(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['overlay']['title'] = 'Original'
        self.profiles.create('cards', 'Player cards', settings)
        self.profiles.update('cards', {'overlay': {'width': 800}, 'theme': 'peach',
                                       'content': {'standings': False}, 'lounge': {'players': ['Example']}})
        settings['overlay']['title'] = 'New default'
        self.assertEqual(self.profiles.effective('default', settings)[1]['overlay']['title'], 'New default')
        self.assertEqual(self.profiles.effective('cards', settings)[1]['overlay']['title'], 'Original')
        restored = OverlayProfiles(self.root, resolve_theme)
        self.assertEqual(restored.effective('cards', settings)[1]['overlay']['width'], 800)
        self.assertFalse(restored.get('cards')['lounge']['enabled'])
        self.assertEqual(restored.effective('cards', settings)[1]['theme'], 'peach')
        restored.update('cards', {'lounge': {'metrics': {'mmr': False}}})
        self.assertFalse(restored.get('cards')['lounge']['metrics']['mmr'])
        self.assertTrue(restored.get('cards')['lounge']['metrics']['division'])
        # Existing options are independent; genuinely future options inherit.
        settings['overlay']['auto_visibility'] = True
        settings['overlay']['transition_style'] = 'iris'
        settings['overlay']['future_option'] = True
        self.assertFalse(restored.effective('cards', settings)[1]['overlay']['auto_visibility'])
        self.assertEqual(restored.effective('cards', settings)[1]['overlay']['transition_style'], 'slide')
        self.assertTrue(restored.effective('cards', settings)[1]['overlay']['future_option'])
        with self.assertRaises(ValueError):
            restored.delete('default')
        restored.delete('cards')
        self.assertEqual(len(restored.public(settings)), 1)

    def test_legacy_sources_keep_their_visibility_direction_when_default_changes(self):
        saved = {'default': {'name': 'Default'}, 'manual': {'overlay': {'auto_visibility': False}},
                 'standings': {'overlay': {'auto_visibility': True}}}
        self.profiles.path.write_text(json.dumps(saved))
        profiles = OverlayProfiles(self.root, resolve_theme)
        settings = deepcopy(DEFAULT_SETTINGS)
        settings['overlay']['auto_visibility_during_races'] = True
        for key, old in [('manual', False), ('standings', True)]:
            overlay = profiles.effective(key, settings)[1]['overlay']
            self.assertEqual(overlay['auto_visibility'], old)
            self.assertFalse(overlay['auto_visibility_during_races'])
        profiles.update('standings', {'overlay': {'auto_visibility_during_races': True}})
        restored = OverlayProfiles(self.root, resolve_theme)
        overlay = restored.effective('standings', DEFAULT_SETTINGS)[1]['overlay']
        self.assertTrue(overlay['auto_visibility_during_races'])
        self.assertFalse(overlay['auto_visibility'])

    def test_lounge_appearance_partial_merge_isolated_persistence_and_legacy_defaults(self):
        self.profiles.create('cards', 'Cards', DEFAULT_SETTINGS)
        self.profiles.update('cards', {'lounge': {
            'appearance': {'background': 'carbon', 'shape': 'rounded'}, 'metrics': {'username': False}}})
        self.profiles.update('cards', {'lounge': {'appearance': {'border': 'division'}}})
        expected = {'background': 'carbon', 'shape': 'rounded', 'border': 'division'}
        self.assertEqual(self.profiles.get('cards')['lounge']['appearance'], expected)
        self.assertEqual(self.profiles.get('default')['lounge']['appearance'], DEFAULT_LOUNGE['appearance'])
        restored = OverlayProfiles(self.root, resolve_theme)
        self.assertEqual(restored.get('cards')['lounge']['appearance'], expected)
        self.assertFalse(restored.get('cards')['lounge']['metrics']['username'])
        # Previously saved source files lack both of these newly introduced options.
        saved = json.loads(restored.path.read_text())
        del saved['cards']['lounge']['appearance']
        del saved['cards']['lounge']['metrics']['username']
        restored.path.write_text(json.dumps(saved))
        legacy = OverlayProfiles(self.root, resolve_theme)
        self.assertEqual(legacy.get('cards')['lounge']['appearance'], DEFAULT_LOUNGE['appearance'])
        self.assertTrue(legacy.get('cards')['lounge']['metrics']['username'])
        legacy.update('cards', {'lounge': {'appearance': {'background': 'transparent', 'border': 'none'}}})
        self.assertEqual(legacy.get('cards')['lounge']['appearance'],
                         {'background': 'transparent', 'border': 'none', 'shape': 'pill'})

    def test_form_toggle_is_opt_in_independent_and_restored_for_old_profiles(self):
        self.profiles.create('cards', 'Cards', DEFAULT_SETTINGS)
        self.assertFalse(self.profiles.get('cards')['lounge']['metrics']['last_five_form'])
        self.profiles.update('cards', {'lounge': {'metrics': {'last_five_form': True}}})
        self.assertTrue(self.profiles.get('cards')['lounge']['metrics']['last_five_form'])
        self.assertFalse(self.profiles.get('default')['lounge']['metrics']['last_five_form'])
        restored = OverlayProfiles(self.root, resolve_theme)
        self.assertTrue(restored.get('cards')['lounge']['metrics']['last_five_form'])
        saved = json.loads(restored.path.read_text())
        del saved['cards']['lounge']['metrics']['last_five_form']
        restored.path.write_text(json.dumps(saved))
        legacy = OverlayProfiles(self.root, resolve_theme)
        self.assertFalse(legacy.get('cards')['lounge']['metrics']['last_five_form'])
        self.assertTrue(legacy.get('cards')['lounge']['metrics']['mmr'])

    def test_lounge_appearance_invalid_values_leave_disk_and_memory_unchanged(self):
        self.profiles.create('cards', 'Cards', DEFAULT_SETTINGS)
        before = self.profiles.get('cards')
        saved = self.profiles.path.read_bytes()
        for appearance in (None, [], 'carbon', {'other': 'theme'}, {'background': '../asset'},
                           {'border': 'url(evil)'}, {'shape': 'circle'}, {'background': True},
                           {'border': []}, {'shape': None}):
            with self.subTest(appearance=appearance), self.assertRaises(ValueError):
                self.profiles.update('cards', {'lounge': {'appearance': appearance}})
            self.assertEqual(self.profiles.get('cards'), before)
            self.assertEqual(self.profiles.path.read_bytes(), saved)

    def test_thin_overlay_height_bounds_and_unchanged_default(self):
        self.assertEqual(DEFAULT_SETTINGS['overlay']['height'], 600)
        self.profiles.create('strip', 'Thin strip', DEFAULT_SETTINGS)
        self.profiles.update('strip', {'overlay': {'height': 40}})
        self.assertEqual(self.profiles.effective('strip', DEFAULT_SETTINGS)[1]['overlay']['height'], 40)
        for height in (39, 2161, True):
            with self.subTest(height=height), self.assertRaises(ValueError):
                self.profiles.update('strip', {'overlay': {'height': height}})
        self.assertEqual(self.profiles.effective('default', DEFAULT_SETTINGS)[1]['overlay']['height'], 600)

    def test_move_is_atomic_and_can_return_to_legacy_source(self):
        self.profiles.create('graph', 'Graph', DEFAULT_SETTINGS)
        self.profiles.move('ww_graph', 'graph')
        self.assertFalse(self.profiles.get('default')['content']['ww_graph'])
        self.assertTrue(self.profiles.get('graph')['content']['ww_graph'])
        self.profiles.move('ww_graph', 'default')
        self.assertTrue(self.profiles.get('default')['content']['ww_graph'])
        with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.profiles.move('standings', 'graph')
        self.assertTrue(self.profiles.get('default')['content']['standings'])

    def test_invalid_configuration_rejected_without_mutation(self):
        for key in ('../x', '/tmp/test', '', 'UPPER', 'a--b', 'a' * 49):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.profiles.create(key, 'Test', DEFAULT_SETTINGS)
        self.profiles.create('cards', 'Cards', DEFAULT_SETTINGS)
        original = self.profiles.get('cards')
        for data in ({'url': 'http://localhost'}, {'content': {'unknown': True}},
                     {'content': {'header': 1}}, {'overlay': {'width': 1}},
                     {'lounge': {'players': ['a'] * 9}}, {'lounge': {'players': ['A', 'a']}},
                     {'lounge': {'players': ['\n']}}, {'lounge': {'enabled': 1}},
                     {'lounge': {'season': True}}, {'lounge': {'metrics': {'mmr': 1}}},
                     {'lounge': {'metrics': {'unknown': True}}}, {'lounge': {'room_size': 11}},
                     {'lounge': {'url': 'https://evil.invalid'}}, {'report_font': '../secret'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.profiles.update('cards', data)
        self.assertEqual(self.profiles.get('cards'), original)


class LoungeTests(unittest.TestCase):
    def test_form_sorts_verified_events_oldest_first_and_ignores_deleted_and_bonus(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        rows = [dict(reason='Table', changeId=i + 1, time=(now - timedelta(days=7 - i)).isoformat(),
                     mmrDelta=delta) for i, delta in enumerate((10, -5, 0, 3, -2, 8, 4))]
        changes = [rows[i] for i in (3, 6, 0, 5, 1, 4, 2)]
        changes += [dict(reason='TableDelete', changeId=7), dict(reason='Bonus', mmrDelta=100)]
        data = payload(eventsPlayed=6, mmrChanges=changes)
        self.assertEqual(last_five_form(data, now), ['L', 'L', 'W', 'L', 'W'])
        self.assertEqual(normalize(data, 'Example', 3, 12, now)['last_five_form'], ['L', 'L', 'W', 'L', 'W'])
        # Legacy event aggregates combine room sizes, unlike the derived week.
        data.update(game='mkworld', season=1)
        for i, row in enumerate(rows):
            row['numPlayers'] = 12 if i % 2 else 24
        self.assertEqual(normalize(data, 'Example', 1, 12, now)['last_five_form'], ['L', 'L', 'W', 'L', 'W'])

    def test_form_empty_short_and_naive_utc_history(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.assertEqual(last_five_form(payload(), now), [])
        data = payload(eventsPlayed=2, mmrChanges=[
            dict(reason='Table', changeId=2, time='2026-11-08T15:00:00+01:00', mmrDelta=0),
            dict(reason='Table', changeId=1, time='2026-11-08T13:00:00', mmrDelta=4)])
        self.assertEqual(last_five_form(data, now), ['W', 'L'])

    def test_form_rejects_incomplete_invalid_and_ambiguous_history(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        row = dict(reason='Table', changeId=1, time='2026-11-08T13:00:00Z', mmrDelta=4)
        good = payload(eventsPlayed=1, mmrChanges=[row])
        cases = [dict(good, mmrChanges=None), dict(good, mmrChanges={}),
                 dict(good, mmrChanges=[None]), dict(good, mmrChanges=[dict(row, reason=None)]),
                 dict(good, eventsPlayed=2), dict(good, eventsPlayed=True),
                 dict(good, eventsPlayed=2, mmrChanges=[row, row]),
                 dict(good, eventsPlayed=2, mmrChanges=[row, dict(row, changeId=2)]),
                 dict(good, mmrChanges=[row, dict(reason='TableDelete', changeId=True)]),
                 dict(good, mmrChanges=[dict(reason='Bonus')] * 10001)]
        for field, values in {'changeId': (None, True, 0, -1, '1'),
                              'time': (None, 'invalid', '2026-11-10T00:00:00Z'),
                              'mmrDelta': (None, True, '4', float('inf'), float('nan'))}.items():
            cases.extend(dict(good, mmrChanges=[dict(row, **{field: value})]) for value in values)
        for data in cases:
            with self.subTest(data=data):
                self.assertIsNone(last_five_form(data, now))

    def test_missing_and_zero_fields_are_distinct_and_names_are_manual(self):
        now = datetime.now(timezone.utc)
        result = normalize(payload('Shortcat', winRate=0, averageScore=None), 'Short cat', 3, 12, now)
        self.assertEqual(result['positive_mmr_rate'], 0)
        self.assertIsNone(result['average_score'])
        self.assertIsNone(result['leaderboard_rank'])
        self.assertEqual(result['weekly']['events'], 0)
        self.assertIsNone(result['weekly']['positive_mmr_rate'])
        for invalid in (payload('Different'), payload(game='mkworld24p'), payload(season=2)):
            with self.assertRaises(lounge.LoungeUnavailable):
                normalize(invalid, 'Example', 3, 12, now)

    def test_weekly_is_verified_utc_trailing_window_without_deleted_or_bonus(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        def event(id, days, delta, **kw):
            return dict(changeId=id, time=(now - timedelta(days=days)).isoformat(),
                        reason='Table', mmrDelta=delta, score=90, numPlayers=12, **kw)
        changes = [event(1, 1, 100), event(2, 7, 0), event(3, 8, 90), event(4, 1, 25),
                   {'reason': 'TableDelete', 'changeId': 4}, {'reason': 'Bonus', 'mmrDelta': 1000}]
        data = payload(eventsPlayed=3, mmrChanges=changes)
        result = weekly(data, now, 12)
        self.assertEqual(result['events'], 2)
        self.assertEqual(result['positive_mmr_rate'], .5)
        self.assertEqual(result['event_mmr_change'], 100)
        self.assertEqual(result['average_score'], 90)
        self.assertEqual(result['to'], now.isoformat())
        self.assertIsNone(weekly(dict(data, eventsPlayed=4), now, 12))
        self.assertIsNone(weekly(dict(data, mmrChanges=None), now, 12))
        changes[0]['time'] = 'broken'
        self.assertIsNone(weekly(data, now, 12))

    def test_cache_nonblocking_deduplicated_stale_and_disable_fenced(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def fetch(name, season, room, cancel):
            calls.append(name)
            started.set()
            release.wait(2)
            return payload(name)
        cache = LoungeCache(fetch)
        self.addCleanup(cache.close)
        config = dict(DEFAULT_LOUNGE, players=['Example'], enabled=True, season=3)
        cache.configure([config])
        self.assertFalse(cache.snapshot(config, enabled=False)['enabled'])
        self.assertEqual(calls, [])
        first = cache.snapshot(config, enabled=True)
        self.assertEqual(first['players'][0]['status'], 'loading')
        self.assertTrue(started.wait(1))
        for _ in range(20):
            cache.snapshot(config, enabled=True)
        self.assertEqual(calls, ['Example'])
        release.set()
        cache.worker.join(2)
        good = cache.snapshot(config, enabled=True)['players'][0]
        self.assertEqual(good['status'], 'ready')
        key = ('Example', 3, 12)
        cache.cache[key]['checked'] -= lounge.TTL + 1
        cache.fetch = lambda *args: (_ for _ in ()).throw(RuntimeError('private URL/token'))
        cache.snapshot(config, enabled=True)
        cache.worker.join(2)
        stale = cache.snapshot(config, enabled=True)['players'][0]
        self.assertEqual(stale['status'], 'stale')
        self.assertEqual(stale['data'], good['data'])
        self.assertEqual(stale['error'], 'Lounge request failed')
        # Disable cancels the exact active network operation and queued keys.
        started.clear()
        release.clear()
        cache.fetch = fetch
        cache.cache[key]['checked'] -= lounge.RETRY + 1
        cache.snapshot(config, enabled=True)
        self.assertTrue(started.wait(1))
        cache.configure([])
        self.assertTrue(cache.active_cancel.is_set())
        release.set()
        cache.worker.join(2)
        self.assertEqual(cache.cache[key]['error'], 'Lounge request failed')
        self.assertFalse(cache.snapshot(config, enabled=False)['enabled'])

    def test_malformed_remote_fields_remain_serializable_unknowns(self):
        now = datetime.now(timezone.utc)
        changes = [{'changeId': i, 'reason': 'Table', 'time': now.isoformat(),
                    'mmrDelta': 1e308, 'score': 1e308} for i in (1, 2)]
        result = normalize(payload(rank='\ud800', maxMmr=10 ** 400, eventsPlayed=2,
                                   mmrChanges=changes), 'Example', 3, 12, now)
        self.assertIsNone(result['division'])
        self.assertIsNone(result['peak_mmr'])
        self.assertIsNone(result['weekly']['event_mmr_change'])
        self.assertIsNone(result['weekly']['average_score'])
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')

    def test_lounge_dns_does_not_occupy_capture_resolver_gate(self):
        from mkw_tracker.external_vision import _resolve, _DNS_GATE
        started, release = threading.Event(), threading.Event()
        result = []
        def dns(host, *_args, **_kwargs):
            if host == lounge.HOST:
                started.set()
                release.wait(2)
            return [(2, 1, 6, '', ('8.8.8.8', 443))]
        def check(deadline):
            if time.monotonic() >= deadline:
                raise TimeoutError
        def resolve_lounge():
            result.append(_resolve(lounge.HOST, 443, check, time.monotonic() + 3, gate=lounge._DNS_GATE))
        with patch('mkw_tracker.external_vision.socket.getaddrinfo', side_effect=dns):
            thread = threading.Thread(target=resolve_lounge)
            thread.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertFalse(_DNS_GATE.locked())
                self.assertEqual(_resolve('recognition.example', 443, check, time.monotonic() + 1)[1][0], '8.8.8.8')
            finally:
                release.set()
                thread.join(3)
        self.assertEqual(len(result), 1)

    def test_transport_fixed_host_tls_encoded_query_status_and_byte_limit(self):
        sock = MagicMock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"ok": true}'
        connection = MagicMock()
        connection.getresponse.return_value = response
        context = MagicMock()
        context.wrap_socket.return_value = sock
        with patch('mkw_tracker.lounge._resolve', return_value=(2, ('8.8.8.8', 443))), \
                patch('mkw_tracker.lounge.socket.socket', return_value=sock), \
                patch('mkw_tracker.lounge.ssl.create_default_context', return_value=context), \
                patch('mkw_tracker.lounge.http.client.HTTPConnection', return_value=connection) as constructor:
            closed = threading.Event()
            self.assertEqual(lounge.fetch_details('https://evil.invalid/?a=b#x', 3, 12, closed), {'ok': True})
            constructor.assert_called_with(lounge.HOST, 443)
            context.wrap_socket.assert_called_with(sock, server_hostname=lounge.HOST, do_handshake_on_connect=False)
            target = connection.request.call_args.args[1]
            self.assertTrue(target.startswith('/api/player/details?'))
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(target).query)
            self.assertEqual(query, {'name': ['https://evil.invalid/?a=b#x'], 'season': ['3'], 'game': ['mkworld12p']})
            self.assertEqual(response.read.call_args.args, (lounge.MAX_BYTES + 1,))
            for status in (302, 403, 404, 429, 500):
                response.status = status
                with self.subTest(status=status), self.assertRaises(lounge.LoungeUnavailable) as error:
                    lounge.fetch_details('Example', 3, 12, closed)
                self.assertEqual(str(error.exception), 'Player not found' if status == 404 else 'Lounge service unavailable')
            response.status = 200
            response.read.return_value = b'x' * 17
            with patch('mkw_tracker.lounge.MAX_BYTES', 16), self.assertRaisesRegex(lounge.LoungeUnavailable, 'too large'):
                lounge.fetch_details('Example', 3, 12, closed)
            connection.close.assert_called()
            sock.close.assert_called()

    def test_transport_total_deadline_and_cancel_interrupt_owned_socket(self):
        for cancel_request in (False, True):
            with self.subTest(cancel=cancel_request):
                interrupted = threading.Event()
                sock = MagicMock()
                sock.shutdown.side_effect = lambda *_: interrupted.set()
                response = MagicMock()
                response.__enter__.return_value = response
                response.status = 200
                closed = threading.Event()
                def read(_limit):
                    if cancel_request:
                        closed.set()
                    self.assertTrue(interrupted.wait(1), 'owned socket was not interrupted')
                    return b'{}'
                response.read.side_effect = read
                connection = MagicMock()
                connection.getresponse.return_value = response
                context = MagicMock()
                context.wrap_socket.return_value = sock
                with patch('mkw_tracker.lounge._resolve', return_value=(2, ('8.8.8.8', 443))), \
                        patch('mkw_tracker.lounge.socket.socket', return_value=sock), \
                        patch('mkw_tracker.lounge.ssl.create_default_context', return_value=context), \
                        patch('mkw_tracker.lounge.http.client.HTTPConnection', return_value=connection), \
                        patch('mkw_tracker.lounge.TIMEOUT', .1):
                    with self.assertRaises(lounge.LoungeUnavailable):
                        lounge.fetch_details('Example', 3, 12, closed)
                    sock.shutdown.assert_called()
                    sock.close.assert_called()

    def test_network_rejects_private_peers_before_socket_and_does_not_resolve_user_urls(self):
        with patch('mkw_tracker.lounge._resolve', return_value=(2, ('127.0.0.1', 443))) as resolve, \
                patch('mkw_tracker.lounge.socket.socket') as socket:
            with self.assertRaises(lounge.LoungeUnavailable):
                lounge.fetch_details('https://evil.invalid/?q=x', 3, 12, threading.Event())
            self.assertEqual(resolve.call_args.args[:2], (lounge.HOST, 443))
            socket.assert_not_called()


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'scores.sqlite', self.root)
        self.app.lounge.fetch = lambda name, *_: payload(name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.app.close()
        self.temporary.cleanup()

    def test_only_overlay_html_allows_same_origin_preview_frames(self):
        self.app.overlay_profiles.create('preview', 'Preview', self.app.settings)
        for path in ('/overlay', '/overlay/preview'):
            with self.subTest(path=path), urlopen(self.base + path) as response:
                self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        for path in ('/', '/api/state', '/api/overlay', '/overlay.js'):
            with self.subTest(path=path), urlopen(self.base + path) as response:
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as missing:
            urlopen(self.base + '/overlay/missing')
        try:
            self.assertIn("frame-ancestors 'none'", missing.exception.headers['Content-Security-Policy'])
        finally:
            missing.exception.close()

    def request(self, path, method='GET', data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=body, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urlopen(request, timeout=3) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_routes_independent_settings_visibility_assignment_and_guards(self):
        self.assertEqual(self.request('/api/overlay-profiles', 'POST', {'slug': 'cards', 'name': 'Cards'})[0], 200)
        self.assertEqual(self.request('/overlay/cards')[0], 200)
        self.assertEqual(self.request('/overlay/missing')[0], 404)
        self.assertEqual(self.request('/api/overlay/missing')[0], 404)
        self.assertEqual(self.request('/api/overlay-profiles/cards', 'PATCH', {'overlay': {'width': 777}})[0], 200)
        self.assertEqual(self.request('/api/overlay/cards')[1]['settings']['width'], 777)
        self.assertEqual(self.request('/api/overlay-profiles/cards', 'PATCH', {
            'lounge': {'appearance': {'background': 'theme', 'border': 'accent'},
                       'metrics': {'username': False}}})[0], 200)
        card = self.request('/api/overlay/cards')[1]['lounge']
        self.assertEqual(card['appearance'], {'background': 'theme', 'border': 'accent', 'shape': 'pill', 'image_url': None})
        self.assertFalse(self.app.overlay_profiles.get('cards')['lounge']['metrics']['username'])
        self.assertEqual(self.request('/api/overlay')[1]['lounge']['appearance'], dict(DEFAULT_LOUNGE['appearance'], image_url=None))

        self.assertEqual(self.request('/api/overlay')[1]['settings']['width'], 560)
        self.assertEqual(self.request('/api/overlay/cards/hide', 'POST', {})[1], {'visible': False})
        self.assertFalse(self.request('/api/overlay/cards')[1]['visible'])
        self.assertTrue(self.request('/api/overlay')[1]['visible'])
        self.assertEqual(self.request('/api/overlay/show', 'POST', {})[0], 200)
        self.assertFalse(self.request('/api/overlay/cards')[1]['visible'])
        self.assertEqual(self.request('/api/overlay-profiles/move', 'POST', {'block': 'standings', 'target': 'cards'})[0], 200)
        self.assertFalse(self.request('/api/overlay')[1]['content']['standings'])
        self.assertEqual(self.request('/api/overlay-profiles/cards', 'PATCH', {'name': 'X'}, {'Origin': 'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request('/api/overlay-profiles/default', 'DELETE', {})[0], 400)
        self.assertEqual(self.request('/api/overlay-profiles/cards', 'DELETE', {})[0], 200)
        self.assertEqual(self.request('/overlay/cards')[0], 404)

    def test_race_visibility_is_per_source_and_manual_actions_win_until_next_edge(self):
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        for key in ('always', 'race'):
            self.assertEqual(self.request('/api/overlay-profiles', 'POST', {'slug': key, 'name': key})[0], 200)
        self.assertEqual(self.request('/api/overlay-profiles/always', 'PATCH', {
            'overlay': {'auto_visibility': False, 'transition_style': 'dissolve'}})[0], 200)
        session = self.app.store.active_session()['id']
        self.app.capture._emit_overlay_event(False, session)
        self.assertFalse(self.request('/api/overlay/race')[1]['visible'])
        self.assertFalse(self.request('/api/overlay')[1]['visible'])
        self.assertTrue(self.request('/api/overlay/always')[1]['visible'])
        self.assertEqual(self.request('/api/overlay/always')[1]['settings']['transition_style'], 'dissolve')
        self.request('/api/overlay/race/show', 'POST')
        self.assertTrue(self.request('/api/overlay/race')[1]['visible'])
        self.assertFalse(self.request('/api/overlay')[1]['visible'])
        self.request('/api/overlay/always/hide', 'POST')
        self.app.capture._emit_overlay_event(True, session)
        self.assertTrue(self.request('/api/overlay')[1]['visible'])
        self.assertTrue(self.request('/api/overlay/race')[1]['visible'])
        self.assertFalse(self.request('/api/overlay/always')[1]['visible'])
        # Consume an unpolled edge BEFORE manual control, including the default alias.
        self.app.capture._emit_overlay_event(False, session)
        self.request('/api/overlay/race/show', 'POST')
        self.assertTrue(self.request('/api/overlay/race')[1]['visible'])
        self.request('/api/overlay/default/show', 'POST')
        self.assertTrue(self.request('/api/overlay')[1]['visible'])

    def test_named_race_overlay_waits_for_next_edge_and_honors_manual_visibility(self):
        self.assertEqual(self.request('/api/overlay-profiles', 'POST', {'slug': 'hud', 'name': 'Race HUD'})[0], 200)
        session = self.app.store.active_session()['id']
        self.app.capture._emit_overlay_event(True, session)  # Previous, unpolled results.
        config = '/api/overlay-profiles/hud'
        self.assertEqual(self.request(config, 'PATCH', {'overlay': {'auto_visibility_during_races': True}})[0], 200)
        self.assertEqual(self.request(config, 'PATCH', {'overlay': {
            'auto_visibility': True, 'auto_visibility_during_races': True}})[0], 400)
        hud = '/api/overlay/hud'
        self.assertTrue(self.request(hud)[1]['visible'])
        self.request(hud + '/hide', 'POST')
        self.app.capture._emit_overlay_event(False, session)
        self.assertTrue(self.request(hud)[1]['visible'])
        self.request(hud + '/hide', 'POST')
        self.assertFalse(self.request(hud)[1]['visible'])
        self.request(hud + '/show', 'POST')
        self.app.capture._emit_overlay_event(True, session)
        self.assertFalse(self.request(hud)[1]['visible'])
        self.assertTrue(self.request('/api/overlay')[1]['visible'])

    def test_new_sources_and_enabling_do_not_replay_old_intro(self):
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        session = self.app.store.active_session()['id']
        self.app.capture._emit_overlay_event(False, session)
        self.request('/api/overlay-profiles', 'POST', {'slug': 'new', 'name': 'New'})
        self.assertTrue(self.request('/api/overlay/new')[1]['visible'])
        self.assertFalse(self.request('/api/overlay')[1]['visible'])
        self.request('/api/overlay-profiles/new', 'PATCH', {'overlay': {'auto_visibility': False}})
        self.app.capture._emit_overlay_event(False, session)
        self.request('/api/overlay-profiles/new', 'PATCH', {'overlay': {'auto_visibility': True}})
        self.assertTrue(self.request('/api/overlay/new')[1]['visible'])
        self.app.capture._emit_overlay_event(False, session)
        self.assertFalse(self.request('/api/overlay/new')[1]['visible'])

    def test_mode_round_trip_does_not_replay_an_unpolled_outgoing_event(self):
        self.app.update_settings({'overlay': {'auto_visibility': True}})
        self.app.capture._emit_overlay_event(False, self.app.store.active_session()['id'])
        self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
        self.assertEqual(self.request('/api/overlay-profiles', 'POST', {
            'slug': 'new', 'name': 'New', 'expected_tracker_mode': 'ww'})[0], 200)
        self.assertEqual(self.request('/api/overlay/new/show', 'POST')[1], {'visible': True})
        self.app.switch_tracker_mode('mogi', self.app.store.active_session()['id'])
        self.assertTrue(self.request('/api/overlay/new')[1]['visible'])
        self.app.capture._emit_overlay_event(False, self.app.store.active_session()['id'])
        self.assertFalse(self.request('/api/overlay/new')[1]['visible'])

    def test_default_source_theme_font_are_source_only_and_dashboard_never_fetches(self):
        self.app.store.create_team('Dashboard team')
        self.app.overlay_profiles.update('default', {'theme': 'peach', 'report_font': 'source.ttf',
            'content': {'lounge': True}, 'lounge': {'enabled': True, 'players': ['Example'], 'season': 3}})
        with patch.object(self.app.font_library, 'browser', side_effect=lambda font: {'test_font': font}):
            with self.app.lock:
                dashboard = self.app.snapshot()['display_standings']
            self.assertEqual(dashboard['theme']['id'], self.app.settings['theme'])
            self.assertEqual(dashboard['font']['test_font'], self.app.settings['report_font'])
            self.assertIsNone(self.app.lounge.worker)
            overlay = self.app.overlay_snapshot()
            self.assertEqual(overlay['theme']['id'], 'peach')
            self.assertEqual(overlay['font']['test_font'], 'source.ttf')
            self.assertNotEqual(overlay['teams'][0]['color'], dashboard['teams'][0]['color'])
            self.assertEqual(overlay['visible'], dashboard['visible'])

    def test_profile_theme_cannot_be_deleted_and_restart_remains_valid(self):
        base = resolve_theme()
        theme = self.app.theme_library.save({'name': 'Custom source', 'tokens': base['tokens'],
                                             'team_colors': base['team_colors']})
        self.request('/api/overlay-profiles', 'POST', {'slug': 'cards', 'name': 'Cards'})
        self.request('/api/overlay-profiles/cards', 'PATCH', {'theme': theme['id']})
        status, error = self.request('/api/themes/' + theme['id'], 'DELETE', {})
        self.assertEqual(status, 400)
        self.assertIn('source', error['error'])
        # Read the saved config through an independently opened temporary app.
        restored = Application(self.root / 'reload.sqlite', self.root)
        try:
            self.assertEqual(restored.overlay_snapshot(profile='cards')['theme']['id'], theme['id'])
        finally:
            restored.close()
        self.request('/api/overlay-profiles/cards', 'PATCH', {'theme': None})
        self.assertEqual(self.request('/api/themes/' + theme['id'], 'DELETE', {})[0], 200)
        restored = Application(self.root / 'reload.sqlite', self.root)
        restored.close()

    def test_lounge_off_shared_lock_no_scoring_identity_and_never_ww(self):
        started, release = threading.Event(), threading.Event()
        def fetch(name, *args):
            started.set()
            release.wait(2)
            return payload(name)
        self.app.lounge.fetch = fetch
        self.request('/api/overlay-profiles', 'POST', {'slug': 'cards', 'name': 'Cards'})
        self.request('/api/overlay-profiles/cards', 'PATCH', {'content': {'lounge': True},
                     'lounge': {'enabled': True, 'players': ['Example'], 'season': 3}})
        self.assertFalse(started.is_set())  # configuration is not a network action
        self.request('/api/overlay/cards')
        self.assertTrue(started.wait(1))
        before = time.monotonic()
        self.assertEqual(self.request('/api/state')[0], 200)
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(self.app.store.snapshot()['players'], [])
        with self.app.lock:
            self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
        self.assertTrue(self.app.lounge.active_cancel.is_set())
        self.assertFalse(self.request('/api/overlay/cards')[1]['lounge']['enabled'])
        release.set()
        self.app.lounge.worker.join(2)
        self.assertFalse(self.app.lounge.cache)


if __name__ == '__main__':
    unittest.main()
