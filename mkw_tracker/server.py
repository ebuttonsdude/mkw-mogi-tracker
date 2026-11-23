"""Dashboard HTTP API with loopback binding by default."""

import ipaddress
import json
from copy import deepcopy
from collections import deque
import math
import mimetypes
import re
import shutil
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from .diagnostics import log_event
from .store import TrackerStore
from .capture import CaptureCoordinator
from .chapters import format_resolve_edl
from .cameras import CameraManager
from .credentials import OBSConnection
from .external_settings import ExternalVisionSettings
from .external_vision import test_connection
from .reports import render_folder_png, team_standings_markdown, team_standings_text
from .reports import _standings
from .settings import DEFAULT_SETTINGS, merge_settings
from .overlay_profiles import OverlayProfiles
from .lounge import LoungeCache
from .report_fonts import FontLibrary
from .reader_setup import public_preferences
from .themes import character_icon_path, team_color_map
from .theme_library import ThemeLibrary, suggest_colors
from .course_images import CourseImageLibrary, course_from_filename
from .courses import COURSES
from .track_icons import COURSE_ICONS
from .imports import NAME_ENGINES, VIDEO_EXTENSIONS
from .videos import VideoLibrary

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 64 * 1024**3


def format_offset(milliseconds):
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


class Application:
    def __init__(self, database, data_dir, mode="manual"):
        from .imports import ImportManager

        self.store = TrackerStore(database)
        self.lock = threading.RLock()
        self.data_dir = Path(data_dir)
        self.mode = mode
        self.tracker_mode = 'mogi'
        self.tracker_mode_path = self.data_dir / 'tracker-mode.json'
        self.settings_path = self.data_dir / "settings.json"
        self.reader_preferences = public_preferences(Path.cwd())
        self.settings = deepcopy(DEFAULT_SETTINGS)
        self.font_library = FontLibrary(self.data_dir)
        self.overlay_visible = True
        self._overlay_events_seen = {}
        try:
            self.obs_connection = OBSConnection(self.data_dir)
            self.external_vision = ExternalVisionSettings(self.data_dir)
            self.external_test_lock = threading.Lock()
            self.external_test_cancel = threading.Event()
            self.external_test_id = None
            self.external_cancelled_tests = deque(maxlen=32)
            self.theme_library = ThemeLibrary(self.data_dir)
            self.course_images = CourseImageLibrary(self.data_dir)
        except BaseException:
            self.store.close()
            raise
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                self.settings = merge_settings(self.settings, saved, theme_resolver=self.theme_library.resolve)
            except (OSError, ValueError, TypeError) as error:
                self.store.close()
                raise ValueError("Could not read valid standings settings from settings.json") from error
        try:
            self.overlay_profiles = OverlayProfiles(self.data_dir, self.theme_library.resolve)
            self.lounge = LoungeCache()
        except BaseException:
            self.store.close()
            raise
        self.capture = CaptureCoordinator(self.store, self.lock, self.data_dir, reader=None)
        self.capture.set_yolo(self.settings['yolo_mode'])
        self.imports = ImportManager(self)
        self._contexts = {'mogi': (self.store, self.capture, self.imports)}
        from .backups import SessionBackups
        self.backups = SessionBackups(self)
        self.active_uploads = set()
        self.cameras = CameraManager(self)
        self.videos = VideoLibrary(self)
        self.obs_text_path = self.data_dir.resolve() / f"team-standings.{self.settings['standings_format']}"
        self.obs_text_error = None
        self._output_retry = None
        self._closing = self._closed = False
        self.store.on_commit = lambda: self._store_committed(self._contexts['mogi'][0])
        try:
            if self.tracker_mode_path.exists() and mode != 'demo':
                saved_mode = json.loads(self.tracker_mode_path.read_text(encoding='utf-8'))
                if saved_mode not in ('mogi', 'ffa', 'ww'):
                    raise ValueError('Invalid tracker mode')
                self._activate_tracker_mode(saved_mode)
        except BaseException:
            self.close()
            raise
        self.sync_outputs()

    def check_tracker_mode(self, expected):
        if expected not in ('mogi', 'ffa', 'ww') or expected != self.tracker_mode:
            raise ValueError('Tracker mode changed. Refresh before submitting this action.')

    def _activate_tracker_mode(self, mode):
        # Retire the outgoing capture edge before selecting another coordinator.
        # Returning to it must not replay a race that predates newer controls.
        self._sync_overlay_visibility()
        if mode not in self._contexts:
            from .imports import ImportManager
            from .ww_capture import WWCaptureCoordinator
            from .ww_recognition import WWReader
            from .ww_store import WWStore

            directory = self.data_dir / mode
            directory.mkdir(parents=True, exist_ok=True)
            store = (TrackerStore(directory / 'tracker.sqlite3', default_format='ffa') if mode == 'ffa'
                     else WWStore(directory / 'tracker.sqlite3'))
            try:
                if mode == 'ffa':
                    capture = CaptureCoordinator(store, self.lock, directory, None)
                    capture.set_yolo(self.settings['yolo_mode'])
                    imports = ImportManager(self, store=store, capture=capture, data_dir=directory,
                                            tessdata_path=getattr(self, 'import_tessdata', '.local/tessdata'))
                else:
                    capture = WWCaptureCoordinator(store, self.lock, directory, None)
                    imports = ImportManager(self, store=store, capture=capture, data_dir=directory,
                                            reader_factory=lambda engine: WWReader(
                                                name_engine=engine, tessdata_path=getattr(self, 'import_tessdata', '.local/tessdata')),
                                            coordinator_factory=WWCaptureCoordinator)
            except BaseException:
                store.close()
                raise
            self._contexts[mode] = (store, capture, imports)
            store.on_commit = lambda: self._store_committed(store)
        # Legacy CLI capture can replace the initial coordinator. Preserve that
        # actual owner, not a stale constructor-time reference.
        self.imports._capture = self.capture
        self._contexts[self.tracker_mode] = (self.store, self.capture, self.imports)
        self.store, self.capture, self.imports = self._contexts[mode]
        self.tracker_mode = mode
        self._configure_lounge()
        self.obs_text_path = self._output_path()

    def _output_path(self):
        name = {'ww': 'ww-rating', 'ffa': 'player-standings'}.get(self.tracker_mode, 'team-standings')
        return self.data_dir.resolve() / f"{name}.{self.settings['standings_format']}"

    def _mode_switch_warning(self, imports):
        if self.cameras.cleanup_failed or any(thread and thread.is_alive() for thread in (
                self.cameras.thread, self.capture.thread)):
            return 'Stop capture before switching tracker modes.'
        if (self.active_uploads or self.imports.cleanup_failed or imports['active']
                or (self.imports.thread and self.imports.thread.is_alive())):
            return 'Finish imports and uploads before switching tracker modes.'
        return ''

    def switch_tracker_mode(self, mode, expected_session_id):
        with self.lock:
            if mode not in ('mogi', 'ffa', 'ww'):
                raise ValueError('Choose Mogi Teams, Mogi FFA or WW mode')
            self.store._expect_session(expected_session_id)
            warning = self._mode_switch_warning(self.imports.snapshot())
            if warning:
                raise ValueError(warning)
            # Initialize before persisting; a failed setup must leave Mogi usable.
            previous = self.tracker_mode
            self._activate_tracker_mode(mode)
            try:
                temporary = self.tracker_mode_path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(mode), encoding='utf-8')
                temporary.replace(self.tracker_mode_path)
            except OSError:
                self._activate_tracker_mode(previous)
                raise
            self.sync_outputs()
            return {'tracker_mode': mode, 'session': self.store.active_session()}

    def _session_snapshot(self, session_id=None):
        options = {'session_id': session_id, 'include_history': False}
        if self.tracker_mode == 'ww':
            options['include_all_courses'] = False
        return self._with_course_images(self.store.snapshot(**options))

    def _with_course_images(self, snapshot):
        urls = {}
        rows = [snapshot["races"], *(snapshot.get("ww", {}).get(key, []) for key in ("courses", "all_courses"))]
        for group in rows:
            for row in group:
                course = row.get("course")
                if course not in urls:
                    urls[course] = self.course_images.icon_url(course)
                row["icon_url"] = urls[course]
        return snapshot

    def _store_committed(self, store):
        # Runs synchronously after commit, including capture/import writes.
        # Do not introduce a second thread reading the connection on success.
        with self.lock:
            if store is self.store and not self._closing:
                self.sync_outputs()

    def _retry_outputs(self):
        with self.lock:
            self._output_retry = None
            if not self._closing:
                self.sync_outputs()

    def _schedule_output_retry(self):
        if self._output_retry is None and not self._closing:
            self._output_retry = threading.Timer(1, self._retry_outputs)
            self._output_retry.daemon = True
            self._output_retry.start()

    def sync_outputs(self, snapshot=None):
        """Write OBS's file atomically after successful score/roster mutations."""
        with self.lock:
            try:
                snapshot = snapshot if snapshot is not None else self._session_snapshot()
                if self.tracker_mode == 'ww':
                    ww = snapshot['ww']
                    lines = [ww['player_name']] + [
                        f"{label}: {ww[key] if ww[key] is not None else 'Unknown'}"
                        for label, key in [('Current VR', 'current_vr'), ('Peak VR', 'peak_vr'),
                                           ('Lowest VR', 'lowest_vr')]]
                    content = '\n'.join(lines) + '\n'
                else:
                    renderer = team_standings_markdown if self.settings["standings_format"] == "md" else team_standings_text
                    content = renderer(snapshot)
                if not self.obs_text_path.exists() or self.obs_text_path.read_bytes() != content.encode("utf-8"):
                    temporary = self.obs_text_path.with_suffix(".txt.tmp")
                    # newline="\n" keeps the OBS file LF-only on native Windows.
                    temporary.write_text(content, encoding="utf-8", newline="\n")
                    temporary.replace(self.obs_text_path)
                self.obs_text_error = None
            except OSError as error:
                if self.obs_text_error is None:
                    log_event('app', 'output_failed', error=error)
                self.obs_text_error = "Could not update the OBS standings file. Check free space and data-directory permissions."
                self._schedule_output_retry()
            except sqlite3.Error as error:
                # The mutation already committed. Preserve that success, but
                # don't hide a database/query failure as a filesystem warning.
                log_event('app', 'output_failed', error=error)
                self.obs_text_error = "Could not read standings data for OBS. Check the application terminal."
                if getattr(error, 'sqlite_errorcode', 0) & 0xff in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    self._schedule_output_retry()

    def update_settings(self, values, *, confirm_yolo=False, expected_session_id=None):
        with self.lock:
            if 'yolo_mode' in values:
                if self.tracker_mode == 'ww':
                    raise ValueError('YOLO is available in Mogi modes only.')
                if expected_session_id != self.store.active_session()['id']:
                    raise ValueError('Session changed. Refresh before changing YOLO mode.')
                if values['yolo_mode'] is True:
                    if confirm_yolo is not True:
                        raise ValueError('Confirm - yolo is required to enable YOLO mode.')
                    if (self.imports.snapshot()['active'] or any(
                            thread and thread.is_alive() for thread in
                            (self.cameras.thread, self.capture.thread, self.imports.thread))):
                        raise ValueError('Stop capture and finish imports before enabling YOLO mode.')
            merged = merge_settings(self.settings, values, theme_resolver=self.theme_library.resolve)
            overlay_values = values.get('overlay', {})
            if 'highlight_team_id' in overlay_values and overlay_values['highlight_team_id'] is not None:
                try:
                    self.store.get_team(overlay_values['highlight_team_id'])
                except (KeyError, ValueError):
                    raise ValueError('Choose an active team from the selected session to highlight.')
            if overlay_values.get('highlight_player_id') is not None:
                if self.tracker_mode != 'ffa':
                    raise ValueError('Player highlighting is available in Mogi FFA only.')
                try:
                    self.store.get_player(overlay_values['highlight_player_id'])
                except (KeyError, ValueError):
                    raise ValueError('Choose an active player from the selected session to highlight.')
            old_path = self.obs_text_path
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(merged), encoding="utf-8")
            temporary.replace(self.settings_path)
            self._sync_overlay_visibility()
            self.settings = merged
            self.overlay_profiles.revision += 1
            if 'yolo_mode' in values:
                for mode, (_, capture, _) in self._contexts.items():
                    if mode != 'ww':
                        capture.set_yolo(merged['yolo_mode'])
            if 'report_font' in values:
                self.font_library.refresh()
            self.obs_text_path = self._output_path()
            if old_path != self.obs_text_path or self.obs_text_error:
                self.sync_outputs()
            if old_path != self.obs_text_path and not self.obs_text_error:
                try:
                    old_path.unlink(missing_ok=True)
                except OSError:
                    # The selected output is valid; an old generated file is
                    # never advertised as the current OBS path.
                    pass
            return deepcopy(self.settings)

    def _sync_overlay_visibility(self):
        """Consume live phase edges once; manual controls win between edges."""
        event = getattr(self.capture, 'overlay_event', None)
        if event is None or self._overlay_events_seen.get(self.capture) == event:
            return
        self._overlay_events_seen[self.capture] = event
        _, session_id, is_results = event
        if session_id != self.store.active_session()['id']:
            return
        defaults = self.settings['overlay']
        for key, source in self.overlay_profiles.profiles.items():
            options = defaults if key == 'default' else defaults | source['overlay']
            if options['auto_visibility_during_races']:
                visible = not is_results
            elif options['auto_visibility']:
                visible = is_results
            else:
                continue
            if key == 'default':
                self.overlay_visible = visible
            else:
                self.overlay_profiles.visible[key] = visible

    def _configure_lounge(self):
        profiles = self.overlay_profiles.profiles.values()
        self.lounge.configure([p['lounge'] for p in profiles if self.tracker_mode != 'ww'
                               and p['lounge']['enabled'] and p['content']['lounge']])

    def overlay_snapshot(self, snapshot=None, profile='default'):
        with self.lock:
            self._configure_lounge()
            source, display = self.overlay_profiles.effective(profile, self.settings)
            result = self._base_overlay_snapshot(snapshot, display)
            result['profile'] = profile
            result['content'] = source['content']
            if profile != 'default':
                result['visible'] = self.overlay_profiles.visible.get(profile, True)
            # snapshot() only queues bounded work; the worker never takes app.lock.
            lounge_enabled = (self.tracker_mode != 'ww' and source['content']['lounge']
                              and source['lounge']['enabled'])
            result['lounge'] = self.lounge.snapshot(source['lounge'], enabled=lounge_enabled)
            result['lounge']['appearance'] = self.overlay_profiles.appearance(profile)
            return result

    def _base_overlay_snapshot(self, snapshot=None, display=None):
        """Only public scoreboard data, with no capture paths or evidence."""
        with self.lock:
            self._sync_overlay_visibility()
            snapshot = self._session_snapshot() if snapshot is None else snapshot
            theme = self.theme_library.resolve(display['theme'])
            font = self.font_library.browser(display['report_font'])
            if self.tracker_mode == 'ww':
                folder = next(f for f in snapshot['folders'] if f['id'] == snapshot['session']['folder_id'])
                ww = {key: deepcopy(value) for key, value in snapshot['ww'].items() if key != 'all_courses'}
                capture_state = getattr(self.capture, 'state', {})
                course = capture_state.get('course')
                # Detection can precede race creation. Never present a course
                # from another session or fall back to the last historical race.
                ww['current_track'] = (course if isinstance(course, str) and course
                                       and capture_state.get('session_id') == snapshot['session']['id']
                                       else None)
                return {'tracker_mode': 'ww', 'ww': ww, 'theme': theme, 'font': font,
                        'settings': deepcopy(display['overlay']), 'visible': self.overlay_visible,
                        'session': {'id': snapshot['session']['id'], 'name': folder['name']},
                        'room': dict(snapshot['room']), 'teams': [], 'players': []}
            if snapshot['session'].get('format') == 'ffa':
                return dict(self._ffa_overlay(snapshot, theme, display), font=font)
            colors = team_color_map(snapshot, snapshot['session']['id'], theme)
            players = _standings([{'id': p['id'], 'name': p['name'], 'points': p['points'],
                                   'team_id': p['team_id']} for p in snapshot['players']])
            teams = _standings([{'id': t['id'], 'name': t['name'], 'points': t['points']}
                                for t in snapshot['teams']])
            unassigned = [p for p in players if p['team_id'] not in {t['id'] for t in teams}]
            if unassigned:
                teams = _standings(teams + [{'id': None, 'name': 'Unassigned',
                                             'points': sum(p['points'] for p in unassigned)}])
            for team in teams:
                team['color'] = colors[team['id']]
                team['players'] = [p for p in players if p['team_id'] == team['id']
                                   or (team['id'] is None and p in unassigned)]
                for player in team['players']:
                    player.update(color=team['color'], team_name=team['name'])
            # Derive movement from persisted race input, not the overlay's last
            # poll. Pending races and revising an older result are not new races.
            scored = [race for race in snapshot['races']
                      if race['session_id'] == snapshot['session']['id'] and race['revision']]
            previous_ranks = {}
            if len(scored) > 1:
                latest = max(scored, key=lambda race: race['confirmed_order'])
                latest_points = {entry['player_id']: entry['net_points'] for entry in latest['placements']}
                previous_ranks = {team['id']: team['rank'] for team in _standings([
                    {'id': team['id'], 'name': team['name'],
                     'points': team['points'] - sum(latest_points.get(player['id'], 0)
                                                    for player in team['players'])}
                    for team in teams])}
            for team in teams:
                team['previous_rank'] = previous_ranks.get(team['id'])
                team['rank_change'] = (team['previous_rank'] - team['rank']
                                       if team['previous_rank'] is not None else None)
            for rows in (teams, players):
                for row in rows:
                    row['gap_leader'] = rows[0]['points'] - row['points']
            folder = next(f for f in snapshot['folders'] if f['id'] == snapshot['session']['folder_id'])
            overlay_settings = deepcopy(display['overlay'])
            if overlay_settings['highlight_team_id'] not in {t['id'] for t in snapshot['teams']}:
                overlay_settings['highlight_team_id'] = None
            return {'theme': theme, 'font': font, 'settings': overlay_settings, 'visible': self.overlay_visible,
                    'session': {'id': snapshot['session']['id'], 'name': folder['name']},
                    'room': dict(snapshot['room']), 'teams': teams, 'players': players}

    def _ffa_overlay(self, snapshot, theme, display):
        palette = theme['team_colors']
        colors = {p['id']: palette[index % len(palette)] for index, p in enumerate(snapshot['players'])}
        players = _standings([{'id': p['id'], 'name': p['name'], 'points': p['points'],
                               'color': colors[p['id']], 'team_id': None} for p in snapshot['players']])
        scored = [r for r in snapshot['races'] if r['session_id'] == snapshot['session']['id'] and r['revision']]
        previous = {}
        if len(scored) > 1:
            latest = max(scored, key=lambda race: race['confirmed_order'])
            points = {entry['player_id']: entry['net_points'] for entry in latest['placements']}
            previous = {p['id']: p['rank'] for p in _standings([
                dict(p, points=p['points'] - points.get(p['id'], 0)) for p in players])}
        for player in players:
            player['gap_leader'] = players[0]['points'] - player['points']
            player['previous_rank'] = previous.get(player['id'])
            player['rank_change'] = (previous[player['id']] - player['rank'] if player['id'] in previous else None)
        settings = deepcopy(display['overlay'])
        settings.update(layout='players', show_players=False, highlight_team_id=None)
        if settings['highlight_player_id'] not in colors:
            settings['highlight_player_id'] = None
        folder = next(f for f in snapshot['folders'] if f['id'] == snapshot['session']['folder_id'])
        return {'tracker_mode': 'ffa', 'format': 'ffa', 'theme': theme, 'settings': settings,
                'visible': self.overlay_visible, 'session': {'id': snapshot['session']['id'],
                'name': folder['name'], 'format': 'ffa'}, 'room': dict(snapshot['room']),
                'teams': [], 'players': players}

    def start_camera(self, options):
        options = dict(options)
        connection, generation = None, None
        if options.get('capture_mode') == 'obs' and options.get('obs_password') == '':
            options.pop('obs_password')
        if options.get('use_obs_clock') is True or options.get('capture_mode') == 'obs':
            fields = {key.removeprefix('obs_'): options[key] for key in ('obs_url', 'obs_password') if key in options}
            connection, generation = self.obs_connection.prepare(**fields)
            options.update(obs_url=connection['url'], obs_password=connection['password'])
        result = self.cameras.start(**options)
        if connection is not None:
            try:
                self.obs_connection.save(connection, expected_generation=generation)
            except OSError:
                # Capture has started. Report persistence failure without claiming
                # it failed to start or exposing credentials in an HTTP response.
                result['credential_warning'] = self.obs_connection.public()['error']
        return result

    def race_evidence(self, race):
        return [entry for entry in race.get("evidence", [])
                if ((self.data_dir / "ffa" if self.tracker_mode == "ffa" else self.data_dir) / "evidence" / entry["url"].rsplit("/", 1)[-1]).is_file()]

    def snapshot(self):
        result = self._with_course_images(self.store.snapshot())
        for race in result["races"]:
            race["evidence"] = self.race_evidence(race)
            race["evidence_url"] = next((entry["url"] for entry in race["evidence"]
                                         if entry["kind"] == "results"),
                                        race["evidence"][0]["url"] if race["evidence"] else None)
        result.update(mode=self.mode, tracker_mode=self.tracker_mode, capture=self.capture.snapshot() if self.capture else {
            "status": "manual", "message": "Choose a capture input and start capture in the web UI.", "pending": []})
        latched_id = getattr(self.capture, 'state', {}).get('race_id')
        current = next((race for race in result['races'] if race['id'] == latched_id), None)
        if not latched_id:
            # With no capture latch, show the newest pending entry, not the
            # last row of a manually reordered history.
            current = max((race for race in result['races']
                           if race['session_id'] == result['session']['id'] and not race['revision']),
                          key=lambda race: race['entry_order'], default=None)
        result['capture']['current_race'] = current if current and not current['revision'] else None
        result.update(imports=self.imports.snapshot(), videos=self.videos.snapshot(), cameras=self.cameras.snapshot(),
                      obs_connection=self.obs_connection.public(),
                      external_vision=self.external_vision.public(),
                      reader_default=self.reader_preferences['default'],
                      reader_setup_warning=self.reader_preferences['warning'],
                      overlay_profiles=self.overlay_profiles.public(self.settings),
                      overlay_profiles_revision=self.overlay_profiles.revision,
                      overlay_profiles_instance=self.overlay_profiles.instance,
                      settings=deepcopy(self.settings), theme=self.theme_library.resolve(self.settings['theme']),
                      themes=self.theme_library.themes(), display_standings=self._base_overlay_snapshot(result, self.settings),
                      obs_text_path=str(self.obs_text_path),
                      obs_text_error=self.obs_text_error)
        result['mode_switch_warning'] = self._mode_switch_warning(result['imports'])
        return result

    def close(self):
        if self._closed:
            return
        self.lounge.close()
        self.external_test_cancel.set()
        self._contexts[self.tracker_mode] = (self.store, self.capture, self.imports)
        for _, _, imports in self._contexts.values():
            imports.close()
        self.cameras.close()
        for store, capture, _ in self._contexts.values():
            if capture:
                capture.close()
        with self.lock:
            self._closing = True
            retry = self._output_retry
        if retry is not None:
            retry.cancel()
            retry.join()
        for store, _, _ in self._contexts.values():
            store.on_commit = None
            store.close()
        self._closed = True


def make_server(app, port=8765, host="127.0.0.1"):
    if (not isinstance(host, str) or not host
            or any(character.isspace() or ord(character) < 32 for character in host)
            or any(character in host for character in "/@?#[]")):
        raise ValueError("host must be a local IP address or hostname, without a URL or port")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # Prefer IPv4 for names that resolve to both families. Explicit IPv6 still
    # selects IPv6, without relying on platform-specific dual-stack defaults.
    family, _, _, _, address = next(
        (entry for entry in addresses if entry[0] == socket.AF_INET), addresses[0])
    app.sync_outputs()

    class BoundHTTPServer(ThreadingHTTPServer):
        address_family = family

        def __init__(self, *args):
            self._connections = set()
            self._connections_lock = threading.Lock()
            self._closing = False
            super().__init__(*args)

        def handle_error(self, request, client_address):
            # socketserver's default prints a raw traceback and client details.
            log_event('http', 'failed')

        def get_request(self):
            connection, address = super().get_request()
            with self._connections_lock:
                if not self._closing:
                    self._connections.add(connection)
                    return connection, address
            connection.close()
            raise OSError("HTTP server is closing")

        def shutdown_request(self, request):
            try:
                super().shutdown_request(request)
            finally:
                with self._connections_lock:
                    self._connections.discard(request)

        def server_close(self):
            # An inactivity timeout alone cannot stop a client trickling bytes.
            # Interrupt client I/O, then join request threads before closing the
            # application's store. Never acquire the capture/application lock.
            with self._connections_lock:
                self._closing = True
                connections = tuple(self._connections)
            self.socket.close()
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Its owning request thread may already have closed it.
            super().server_close()

        def server_bind(self):
            if self.address_family == socket.AF_INET6:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            super().server_bind()

    class Handler(BaseHTTPRequestHandler):
        server_version = "MKWTracker/0.1"
        # StreamRequestHandler applies this before reading request headers.
        # These are HTTP client deadlines, not OBS/camera capture intervals.
        timeout = 5

        def log_message(self, *_args):
            pass  # Base class messages contain raw request lines/URLs.

        def log_error(self, *_args):
            log_event('http', 'rejected')

        def log_request(self, code='-', size='-'):
            # Status only: never request targets, headers, bodies or addresses.
            if type(code) is int:
                log_event('http', 'response', status=code)
                if 400 <= code < 500:
                    log_event('http', 'rejected', status=code)

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                self.close_connection = True

        def _send(self, status, content, content_type="application/json; charset=utf-8", download=None, *, frameable=False):
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.connection.settimeout(self.timeout)
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                # Only successful overlay HTML opts into same-origin dashboard
                # previews. Dashboard/API/error responses remain unframeable.
                ancestors = "'self'" if frameable else "'none'"
                self.send_header("Content-Security-Policy", f"default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; frame-ancestors {ancestors}; base-uri 'none'; form-action 'self'")
                if download:
                    self.send_header("Content-Disposition", f'attachment; filename="{download}"')
                self.end_headers()
                self.wfile.write(content)
            except (ConnectionError, TimeoutError):
                self.close_connection = True  # Disconnection also makes error responses impossible.

        def _host(self):
            headers = self.headers.get_all("Host", [])
            if len(headers) != 1:
                return False
            port = self.server.server_address[1]
            local_address = self.connection.getsockname()[0]
            hosts = {local_address}
            if not ipaddress.ip_address(self.server.server_address[0]).is_unspecified:
                hosts.add(host)
            if ipaddress.ip_address(local_address).is_loopback:
                hosts.add("localhost")
            authorities = {f"[{name}]" if ":" in name else name.lower() for name in hosts}
            allowed = {f"{name}:{port}" for name in authorities}
            if port == 80:
                allowed.update(authorities)
            # A wildcard listener trusts the actual local destination, not any
            # arbitrary Host supplied by a client or a DNS-rebinding website.
            return headers[0].lower() in allowed

        def _check_write_origin(self):
            if not self._host():
                raise PermissionError("Invalid Host header")
            origins = self.headers.get_all("Origin", [])
            if origins and origins != [f"http://{self.headers['Host']}"]:
                raise PermissionError("Cross-origin changes are not allowed")
            fetch_sites = self.headers.get_all("Sec-Fetch-Site", [])
            if len(fetch_sites) > 1 or "cross-site" in fetch_sites:
                raise PermissionError("Cross-site changes are not allowed")

        def _body(self, *, allow_empty=False):
            self._check_write_origin()
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked request bodies are not supported")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            if allow_empty and length == 0:
                return {}
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("Use application/json")
            image_upload = (urlsplit(self.path).path in ('/api/themes', '/api/themes/suggest', '/api/course-images')
                            or self.command == 'POST' and re.fullmatch(r'/api/overlay-profiles/[a-z0-9-]+/background', urlsplit(self.path).path))
            limit = 6 * 1024 * 1024 if image_upload else 262144
            if not 0 < length <= limit:
                raise ValueError(f"JSON body must be between 1 and {limit} bytes")
            self.connection.settimeout(self.timeout)
            def invalid_constant(_value):
                raise ValueError("Invalid JSON number")
            data = json.loads(self.rfile.read(length), parse_constant=invalid_constant)
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _error(self, error):
            if isinstance(error, (ConnectionError, TimeoutError)):
                log_event('http', 'disconnected')
                return  # Closed or stalled client; no response remains to send.
            if isinstance(error, PermissionError):
                # Closing with an unread POST body can reset the connection on
                # Windows before the client receives its rejection response.
                # Drain only a small declared body, with a separate short bound.
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if 0 < length <= 262144 and not self.headers.get('Transfer-Encoding'):
                        deadline = time.monotonic() + .05
                        while length:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            self.connection.settimeout(remaining)
                            chunk = self.rfile.read1(min(length, 65536))
                            if not chunk:
                                break
                            length -= len(chunk)
                except (OSError, ValueError):
                    pass
                self._send(403, {"error": str(error)})
            elif isinstance(error, KeyError):
                self._send(404, {"error": f"Not found: {error.args[0]}"})
            elif isinstance(error, (ValueError, TypeError, UnicodeError)):
                self._send(400, {"error": str(error)})
            else:
                self._send(500, {"error": "The operation failed; check the application terminal."})
                log_event('http', 'failed', error=error)

        def do_GET(self):
            try:
                if not self._host():
                    raise PermissionError("Invalid Host header")
                parsed = urlsplit(self.path)
                path = unquote(parsed.path)
                if path == "/api/state":
                    with app.lock:
                        snapshot = app.snapshot()
                    self._send(200, snapshot)
                elif path == "/api/fonts":
                    with app.lock:
                        app.font_library.refresh()
                        fonts = app.font_library.choices()
                    self._send(200, {'fonts': fonts})
                elif path.startswith('/api/fonts/'):
                    identifier = path.removeprefix('/api/fonts/')
                    with app.lock:
                        content = app.font_library.read(identifier)
                    self._send(200, content, 'font/otf' if content[:4] == b'OTTO' else 'font/ttf')
                elif path == "/api/backups/sessions":
                    self._send(200, app.backups.catalog())
                elif path == "/api/course-images":
                    with app.lock:
                        images = app.course_images.public()
                    self._send(200, {"courses": list(COURSES), "images": images})
                elif re.fullmatch(r"/api/course-images/[0-9a-f]{32}\.png", path):
                    with app.lock:
                        icon = app.course_images.icon(path.split("/")[3][:-4])
                    self._send(200, icon, "image/png")
                elif path == "/api/themes":
                    with app.lock:
                        themes = app.theme_library.themes()
                    self._send(200, {"themes": themes})
                elif re.fullmatch(r"/api/themes/[a-z0-9-]+/icon\.png", path):
                    with app.lock:
                        icon = app.theme_library.icon(path.split("/")[3])
                    self._send(200, icon, "image/png")
                elif path == "/api/videos":
                    self._send(200, app.videos.snapshot())
                elif re.fullmatch(r'/api/overlay-profiles/[a-z0-9-]+/background\.png', path):
                    key = path.split('/')[3]
                    with app.lock:
                        app.overlay_profiles.get(key)
                        image = app.overlay_profiles.backgrounds.image(key)
                    self._send(200, image, 'image/png')
                elif path == '/api/overlay-profiles':
                    with app.lock:
                        profiles = app.overlay_profiles.public(app.settings)
                        revision = app.overlay_profiles.revision
                        instance = app.overlay_profiles.instance
                    self._send(200, {'profiles': profiles, 'revision': revision, 'instance': instance})
                elif re.fullmatch(r'/api/overlay/[a-z0-9-]+', path):
                    self._send(200, app.overlay_snapshot(profile=path.rsplit('/', 1)[1]))
                elif path == "/api/overlay":
                    self._send(200, app.overlay_snapshot())
                elif path == '/api/cameras':
                    self._send(200, app.cameras.snapshot(refresh=True))
                elif path in {"/api/standings.txt", "/api/standings.md"}:
                    with app.lock:
                        if path.rsplit(".", 1)[-1] != app.settings["standings_format"]:
                            raise ValueError("Use the currently selected standings output format")
                        if app.obs_text_error:
                            raise ValueError(app.obs_text_error)
                        content = app.obs_text_path.read_bytes()
                    self._send(200, content, "text/plain; charset=utf-8", app.obs_text_path.name)
                elif re.fullmatch(r"/api/races/[^/]+/evidence", path):
                    with app.lock:
                        race = app.store.get_race(path.split("/")[3])
                        images = app.race_evidence(race)
                    self._send(200, {"race_id": race["id"], "images": images})
                elif re.fullmatch(r"/api/folders/[^/]+/(?:report|preview)\.png", path):
                    with app.lock:
                        worldwide = app.tracker_mode == 'ww'
                        folder = app.store.get_folder(path.split('/')[3])
                        highlight_session_id = app.store.active_session()['id']
                        snapshot = app._session_snapshot(folder['session_id'])
                        course_images = app.course_images.pngs_for(race['course'] for race in snapshot['races'])
                        theme = app.theme_library.resolve(app.settings['theme'], internal=True)
                        report_font = app.settings['report_font']
                        mogi_png_graph = app.settings['mogi_png_graph']
                        ffa = snapshot['session'].get('format') == 'ffa'
                        highlight_team_id = None if ffa else app.settings['overlay']['highlight_team_id']
                        highlight_player_id = app.settings['overlay']['highlight_player_id'] if ffa else None
                        query = parse_qs(parsed.query, keep_blank_values=True)
                        if not set(query) <= ({'highlight_player_id'} if ffa else {'highlight_team_id'}) or any(len(v) != 1 for v in query.values()):
                            raise ValueError('Unknown PNG report option')
                        if worldwide and query:
                            raise ValueError('WW reports do not have team options')
                        if 'highlight_player_id' in query:
                            highlight_player_id = query['highlight_player_id'][0] or None
                            highlight_session_id = folder['session_id']
                            if highlight_player_id is not None and not any(
                                    p['id'] == highlight_player_id for p in snapshot['players']):
                                raise ValueError('Choose a player from this report session')
                        if 'highlight_team_id' in query:
                            highlight_team_id = query['highlight_team_id'][0] or None
                            highlight_session_id = folder['session_id']
                            teams = snapshot['teams'] + snapshot.get('archived_teams', [])
                            if highlight_team_id is not None and not any(
                                    t['id'] == highlight_team_id and t['active'] and t['session_id'] == highlight_session_id
                                    for t in teams):
                                raise ValueError('Choose a team from this report session')
                    try:
                        if worldwide:
                            from .ww_reports import render_ww_folder_png
                            content = render_ww_folder_png(snapshot, path.split('/')[3], theme=theme,
                                                           font_library=app.font_library, report_font=report_font,
                                                           course_images=course_images)
                        else:
                            content = render_folder_png(snapshot, path.split("/")[3], theme=theme,
                                                        font_library=app.font_library, report_font=report_font,
                                                        highlight_team_id=highlight_team_id, course_images=course_images,
                                                        highlight_player_id=highlight_player_id,
                                                        mogi_png_graph=mogi_png_graph,
                                                        highlight_session_id=highlight_session_id)
                    except RuntimeError as error:
                        raise ValueError(str(error)) from None
                    filename = ('ww-results.png' if worldwide else 'room-results.png') if path.endswith('/report.png') else None
                    self._send(200, content, "image/png", filename)
                elif path == "/api/chapters":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    recording_id = query.get("recording_id", [None])[0]
                    export_format = query.get("format", ["txt"])[0]
                    if export_format not in ("txt", "edl"):
                        raise ValueError("Choose TXT or EDL as the timestamp format.")
                    with app.lock:
                        races = app.store.recording_chapters(recording_id)
                    if export_format == "edl":
                        content = format_resolve_edl(races)
                    else:
                        content = "".join(f"{format_offset(r['offset_ms'])} {r['course']}\n"
                                          for r in races if r['offset_ms'] is not None).encode("utf-8")
                    self._send(200, content, "text/plain; charset=utf-8", f"race-chapters.{export_format}")
                elif re.fullmatch(r'/overlay/[a-z0-9-]+', path):
                    with app.lock:
                        app.overlay_profiles.get(path.rsplit('/', 1)[1])
                    self._send(200, (STATIC / 'overlay.html').read_bytes(), 'text/html; charset=utf-8', frameable=True)
                elif path in {"/", "/license", "/overlay", "/overlay.js", "/overlay.css", "/static/app.js", "/static/style.css",
                              "/static/ww.js", "/static/fonts.js", "/static/overlay-profiles.js"}:
                    name = {"/": "index.html", "/license": "legal.html", "/overlay": "overlay.html"}.get(path, path.rsplit("/", 1)[1])
                    target = STATIC / name
                    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    self._send(200, target.read_bytes(), content_type + "; charset=utf-8", frameable=path == '/overlay')
                elif path.startswith("/static/character-icons/"):
                    target = character_icon_path(path.removeprefix("/static/character-icons/"))
                    if target is None:
                        raise KeyError("character icon")
                    self._send(200, target.read_bytes(), "image/png")
                elif path.startswith("/static/track-icons/"):
                    name = path.removeprefix("/static/track-icons/")
                    if name not in set(COURSE_ICONS.values()):
                        raise KeyError("course icon")
                    target = STATIC / "track-icons" / name
                    self._send(200, target.read_bytes(), "image/png")
                elif re.fullmatch(r"/evidence/[0-9a-f-]{36}\.png", path):
                    target = (app.data_dir / "ffa" if app.tracker_mode == "ffa" else app.data_dir) / "evidence" / path.rsplit("/", 1)[1]
                    if not target.is_file():
                        raise KeyError("evidence")
                    self._send(200, target.read_bytes(), "image/png")
                else:
                    raise KeyError("route")
            except Exception as error:
                self._error(error)

        def _mutate(self):
            path = unquote(urlsplit(self.path).path)
            named_visibility = re.fullmatch(r'/api/overlay/([a-z0-9-]+)/(show|hide|toggle)', path) if self.command == 'POST' else None
            visibility = self.command == 'POST' and path in {
                '/api/overlay/show', '/api/overlay/hide', '/api/overlay/toggle'}
            data = self._body(allow_empty=visibility or bool(named_visibility) or (self.command == 'DELETE'
                and re.fullmatch(r'/api/course-images/[0-9a-f]{32}', path)))
            if named_visibility:
                if not set(data) <= {'expected_tracker_mode'}:
                    raise ValueError('Overlay visibility takes an empty object.')
                key, action = named_visibility.groups()
                with app.lock:
                    app.overlay_profiles.get(key)
                    if 'expected_tracker_mode' in data:
                        app.check_tracker_mode(data['expected_tracker_mode'])
                    app._sync_overlay_visibility()
                    if key == 'default':
                        app.overlay_visible = not app.overlay_visible if action == 'toggle' else action == 'show'
                        return {'visible': app.overlay_visible}
                    visible = app.overlay_profiles.visible.get(key, True)
                    app.overlay_profiles.visible[key] = not visible if action == 'toggle' else action == 'show'
                    return {'visible': app.overlay_profiles.visible[key]}
            if visibility:
                if not set(data) <= {'expected_tracker_mode'}:
                    raise ValueError('Overlay visibility takes an empty body or JSON object')
                with app.lock:
                    # Hotkeys control the shared overlay in either mode. A
                    # dashboard that supplies a mode still gets stale-tab checks.
                    if 'expected_tracker_mode' in data:
                        app.check_tracker_mode(data['expected_tracker_mode'])
                    action = path.rsplit('/', 1)[1]
                    app._sync_overlay_visibility()
                    app.overlay_visible = not app.overlay_visible if action == 'toggle' else action == 'show'
                    return {'visible': app.overlay_visible}
            expected_mode = data.pop('expected_tracker_mode', 'mogi')
            with app.lock:
                app.check_tracker_mode(expected_mode)
                imports = app.imports
                if self.command == 'POST' and path == '/api/tracker-mode':
                    if not set(data) <= {'tracker_mode', 'expected_session_id'}:
                        raise ValueError('Unknown tracker mode option')
                    return app.switch_tracker_mode(data.get('tracker_mode'), data.get('expected_session_id'))
            if re.fullmatch(r'/api/overlay-profiles/[a-z0-9-]+/background', path):
                key = path.split('/')[3]
                if self.command == 'POST':
                    if set(data) != {'image_base64'}:
                        raise ValueError('Provide a background image_base64 value.')
                    with app.lock:
                        token = app.overlay_profiles.background_token(key)
                    png = app.overlay_profiles.backgrounds.prepare(data['image_base64'])
                    with app.lock:
                        app.check_tracker_mode(expected_mode)
                        return app.overlay_profiles.save_background(key, png, token)
                if self.command == 'DELETE' and not data:
                    with app.lock:
                        app.check_tracker_mode(expected_mode)
                        return app.overlay_profiles.remove_background(key)
                raise ValueError('Use POST image_base64 or DELETE with an empty object.')
            if path == '/api/overlay-profiles' and self.command == 'POST':
                if set(data) != {'slug', 'name'}:
                    raise ValueError('Provide source slug and name.')
                with app.lock:
                    app._sync_overlay_visibility()
                    return app.overlay_profiles.create(data['slug'], data['name'], app.settings)
            if path == '/api/overlay-profiles/move' and self.command == 'POST':
                if set(data) != {'block', 'target'}:
                    raise ValueError('Provide a content block and target source.')
                with app.lock:
                    app.overlay_profiles.move(data['block'], data['target'])
                    app._configure_lounge()
                    return {'profiles': app.overlay_profiles.public(app.settings)}
            if re.fullmatch(r'/api/overlay-profiles/[a-z0-9-]+', path):
                key = path.rsplit('/', 1)[1]
                with app.lock:
                    if self.command == 'PATCH':
                        app._sync_overlay_visibility()
                        result = app.overlay_profiles.update(key, data)
                        app._configure_lounge()
                        return result
                    if self.command == 'DELETE' and not data:
                        app.overlay_profiles.delete(key)
                        app._configure_lounge()
                        return {'deleted': key}
                raise ValueError('Use PATCH options or DELETE with an empty object.')
            if path == '/api/external-vision' and self.command == 'PATCH':
                return app.external_vision.configure(data)
            if path == '/api/external-vision/key' and self.command == 'DELETE':
                if data:
                    raise ValueError('Forget API key takes an empty object.')
                return app.external_vision.forget_key()
            if path in {'/api/external-vision/test', '/api/external-vision/test/cancel'} and self.command == 'POST':
                test_id = data.get('test_id')
                if not isinstance(test_id, str) or not re.fullmatch(r'[0-9a-f]{32}', test_id):
                    raise ValueError('Provide a unique test_id for this connection test.')
                if path.endswith('/cancel'):
                    if set(data) != {'test_id'}:
                        raise ValueError('Cancel test takes test_id only.')
                    with app.lock:
                        app.external_cancelled_tests.append(test_id)
                        if app.external_test_id == test_id:
                            app.external_test_cancel.set()
                    return {'cancelled': True}
                if set(data) != {'test_id', 'generation'}:
                    raise ValueError('Save configuration first; Test takes test_id and configuration generation.')
                if not app.external_test_lock.acquire(blocking=False):
                    raise ValueError('An external vision test is already running.')
                try:
                    with app.lock:
                        app.check_tracker_mode(expected_mode)
                        if not app.external_vision.is_current(data['generation']):
                            raise ValueError('External configuration changed; reload before testing.')
                        app.external_test_id = test_id
                        cancel = threading.Event()
                        app.external_test_cancel = cancel
                        if test_id in app.external_cancelled_tests:
                            cancel.set()
                    return test_connection(app.external_vision, cancel, generation=data['generation'])
                except RuntimeError as error:
                    # Transport errors are deliberately fixed, credential-free messages.
                    raise ValueError(str(error)) from None
                finally:
                    with app.lock:
                        app.external_test_id = None
                    app.external_test_lock.release()
            if self.command == 'POST' and path == '/api/course-images/resolve':
                if set(data) != {'course'}:
                    raise ValueError('Provide course only.')
                with app.lock:
                    app.check_tracker_mode(expected_mode)
                    return app.course_images.resolve_course(data['course'])
            if self.command == 'POST' and path == '/api/course-images/match':
                filenames = data.get('filenames')
                if (set(data) != {'filenames'} or not isinstance(filenames, list) or len(filenames) > 256
                        or any(not isinstance(name, str) or len(name) > 255 for name in filenames)):
                    raise ValueError('Provide at most 256 filenames, each at most 255 characters.')
                return {'matches': [{'filename': name, 'course': course_from_filename(name)} for name in filenames]}
            if self.command == 'POST' and path == '/api/course-images':
                if not set(data) <= {'course', 'image_base64', 'overwrite'}:
                    raise ValueError('Unknown course image field.')
                prepared = app.course_images.prepare(data.get('course'), data.get('image_base64'))
                with app.lock:
                    app.check_tracker_mode(expected_mode)
                    return app.course_images.save_prepared(prepared, overwrite=data.get('overwrite', False))
            if self.command == 'POST' and path == '/api/themes/suggest':
                if set(data) != {'image_base64'}:
                    raise ValueError('Provide image_base64 only.')
                return suggest_colors(data['image_base64'])
            if self.command == 'POST' and path == '/api/themes':
                prepared = app.theme_library.prepare(data)
                with app.lock:
                    app.check_tracker_mode(expected_mode)
                    return app.theme_library.save_prepared(prepared, creating=data.get('id') is None)
            # Starting/cancelling a worker must not hold the application lock
            # while it loads or shuts down its recognizer.
            if self.command == 'POST' and path == '/api/cameras/start':
                allowed = {'device_id', 'name_engine', 'use_obs_clock', 'obs_url', 'obs_password',
                           'capture_mode', 'obs_source'}
                if not set(data) <= allowed:
                    raise ValueError('Unknown camera capture option')
                return app.start_camera({**data, 'expected_tracker_mode': expected_mode})
            if self.command == 'POST' and path == '/api/cameras/stop':
                if data:
                    raise ValueError('Stop capture takes an empty JSON object')
                with app.lock:
                    app.check_tracker_mode(expected_mode)
                    return app.cameras.stop()
            if self.command == "POST" and path == "/api/imports":
                allowed = {"path", "folder_id", "name_engine", "stride"}
                if not set(data) <= allowed:
                    raise ValueError("Unknown video import option")
                return imports.start(data.get("path"), data.get("folder_id"),
                                     data.get("name_engine", "auto"), data.get("stride", .5))
            if self.command == "POST" and re.fullmatch(r"/api/imports/[^/]+/cancel", path):
                if data:
                    raise ValueError("Cancel takes an empty JSON object")
                return imports.cancel(path.split("/")[3])
            with app.lock:
                app.check_tracker_mode(expected_mode)
                store = app.store
                if path.startswith('/api/ww/'):
                    if app.tracker_mode != 'ww':
                        raise ValueError('This action requires WW mode')
                    if self.command == 'POST' and path == '/api/ww/player':
                        return store.set_player_name(data.get('name'), data.get('expected_session_id'))
                    if self.command == 'POST' and path == '/api/ww/races':
                        return store.create_race(str(uuid4()), data.get('course'), data.get('recording_id'),
                                                 data.get('offset_ms'), data.get('folder_id'),
                                                 expected_session_id=data.get('expected_session_id'))
                    if self.command == 'PATCH' and re.fullmatch(r'/api/ww/races/[^/]+/result', path):
                        allowed = {'position', 'vr_after', 'vr_before', 'delta', 'lobby_size',
                                   'expected_session_id', 'expected_revision'}
                        if not set(data) <= allowed:
                            raise ValueError('Unknown WW result field')
                        values = {key: data.get(key) for key in allowed if key != 'expected_revision'}
                        if 'expected_revision' in data:
                            values['expected_revision'] = data['expected_revision']
                        return store.set_ww_result(path.split('/')[4], **values)
                    raise KeyError('route')
                if app.tracker_mode == 'ffa' and path.startswith('/api/teams'):
                    raise ValueError('Teams are unavailable in Mogi FFA')
                if app.tracker_mode == 'ww' and (
                        path.startswith(('/api/players', '/api/teams', '/api/penalties', '/api/capture/review',
                                         '/api/capture/dismiss')) or path.endswith(('/penalties', '/results'))):
                    raise ValueError('Mogi identities, teams and placement points are unavailable in WW mode')
                if self.command == "DELETE" and re.fullmatch(r"/api/videos/[^/]+", path):
                    if data:
                        raise ValueError("Video deletion takes an empty JSON object")
                    return app.videos.delete(path.rsplit("/", 1)[1])
                if self.command == "POST" and path == "/api/videos/cleanup":
                    if not set(data) <= {"older_than_days"}:
                        raise ValueError("Unknown video cleanup option")
                    return app.videos.cleanup(data.get("older_than_days", 30))
                if self.command == 'DELETE' and re.fullmatch(r'/api/course-images/[0-9a-f]{32}', path):
                    if data:
                        raise ValueError('Delete course image takes an empty object.')
                    identifier = path.rsplit('/', 1)[1]
                    app.course_images.delete(identifier)
                    return {'deleted': identifier}
                if self.command == 'DELETE' and re.fullmatch(r'/api/themes/[a-z0-9-]+', path):
                    if data:
                        raise ValueError('Delete theme takes an empty object.')
                    identifier = path.rsplit('/', 1)[1]
                    if identifier == app.settings['theme'] and identifier.startswith('custom-'):
                        raise ValueError('Select a different theme before deleting this one.')
                    if any(p['theme'] == identifier for p in app.overlay_profiles.profiles.values()):
                        raise ValueError('Change the overlay source theme before deleting this theme.')
                    app.theme_library.delete(identifier)
                    return {'deleted': identifier}
                if self.command == "PATCH" and path == "/api/settings":
                    if 'yolo_mode' in data:
                        data = dict(data)
                        return app.update_settings(data,
                            confirm_yolo=data.pop('confirm_yolo', False),
                            expected_session_id=data.pop('expected_session_id', None))
                    return app.update_settings(data)
                if self.command == 'DELETE' and path == '/api/obs/credentials':
                    if data:
                        raise ValueError('Clear OBS password takes an empty JSON object')
                    return app.obs_connection.forget_password()
                if self.command == "POST" and path == "/api/sessions":
                    return store.create_session(data.get("name"), data.get("expected_session_id"))
                if self.command == "POST" and re.fullmatch(r"/api/folders/[^/]+/race-order", path):
                    if set(data) != {'race_ids', 'expected_ids'}:
                        raise ValueError('Expected the previous and new race order')
                    return store.reorder_history(path.split("/")[3], data['race_ids'], data['expected_ids'])
                if self.command == "POST" and re.fullmatch(r"/api/folders/[^/]+/load", path):
                    return store.load_session(path.split("/")[3], data.get("expected_session_id"))
                if self.command == "DELETE" and re.fullmatch(r"/api/folders/[^/]+", path):
                    if data:
                        raise ValueError("Session deletion takes an empty JSON object")
                    if (app.imports.cleanup_failed or app.imports.snapshot()["active"] or app.active_uploads
                            or (app.imports.thread and app.imports.thread.is_alive())) or (getattr(app.capture, "thread", None)
                                                           and app.capture.thread.is_alive()):
                        raise ValueError("Stop or cancel capture/import before deleting a session")
                    result = store.delete_folder(path.rsplit("/", 1)[1])
                    app.capture.prune_deleted_races()
                    return result
                if self.command == "DELETE" and re.fullmatch(r"/api/races/[^/]+", path):
                    if data:
                        raise ValueError("Race deletion takes an empty JSON object")
                    active = (app.imports.cleanup_failed or app.imports.snapshot()["active"] or app.active_uploads
                            or (app.imports.thread and app.imports.thread.is_alive())) or (getattr(app.capture, "thread", None)
                                                           and app.capture.thread.is_alive())
                    race = store.get_race(path.rsplit('/', 1)[1])
                    if active and race and race['revision']:
                        raise ValueError("Stop or cancel capture/import before deleting a confirmed race")
                    result = store.delete_race(path.rsplit("/", 1)[1])
                    app.capture.prune_deleted_races()
                    app.imports.prune_deleted_races()
                    return result
                if self.command == "POST" and re.fullmatch(r"/api/races/[^/]+/penalties", path):
                    return store.add_penalty(path.split("/")[3], data.get("player_id"),
                                             data.get("points"), data.get("reason", ""))
                if self.command == "DELETE" and re.fullmatch(r"/api/penalties/[^/]+", path):
                    return store.remove_penalty(path.rsplit("/", 1)[1])
                if self.command == "POST" and path == "/api/players":
                    return store.add_player(data.get("name"), expected_session_id=data.get("expected_session_id"))
                if self.command == "POST" and path == "/api/teams":
                    return store.create_team(data.get("name"), expected_session_id=data.get("expected_session_id"))
                if self.command == "POST" and path == "/api/folders":
                    return store.create_folder(data.get("name"))
                if self.command == "POST" and path == "/api/recordings":
                    return store.create_recording(data.get("label"))
                if self.command == "POST" and path == "/api/races":
                    return store.create_race(str(uuid4()), data.get("course"), data.get("recording_id"),
                                             data.get("offset_ms"), data.get("folder_id"),
                                             expected_session_id=data.get("expected_session_id"))
                if self.command == "POST" and path == "/api/reset":
                    return store.reset(data.get("expected_session_id"))
                if self.command == "PATCH" and re.fullmatch(r"/api/players/[^/]+", path):
                    identifier = path.rsplit("/", 1)[1]
                    try:
                        player = store.get_player(identifier)
                    except ValueError:
                        raise KeyError("player") from None
                    return store.update_player(identifier, data.get("name", player["name"]), data.get("team_id", player["team_id"]))
                if self.command == "DELETE" and re.fullmatch(r"/api/players/[^/]+", path):
                    return store.delete_player(path.rsplit("/", 1)[1])
                if self.command == "DELETE" and re.fullmatch(r"/api/teams/[^/]+", path):
                    return store.delete_team(path.rsplit("/", 1)[1])
                if self.command == "PATCH" and re.fullmatch(r"/api/teams/[^/]+", path):
                    if not data or not set(data) <= {'name', 'color'}:
                        raise ValueError('Provide a team name and/or color')
                    return store.update_team(path.rsplit("/", 1)[1], **data)
                if self.command == "PATCH" and re.fullmatch(r"/api/folders/[^/]+", path):
                    if not data or not set(data) <= {"name", "lounge_id"}:
                        raise ValueError("Provide a folder name and/or Lounge ID")
                    if app.tracker_mode == 'ww' and 'lounge_id' in data:
                        raise ValueError('Lounge IDs are only available in Mogi mode')
                    return store.update_folder(path.rsplit("/", 1)[1], **data)
                if self.command == "PATCH" and re.fullmatch(r"/api/races/[^/]+", path):
                    identifier = path.rsplit("/", 1)[1]
                    if "folder_id" in data:
                        if set(data) != {"folder_id"}:
                            raise ValueError("Move a race with folder_id alone; edit chapter metadata separately")
                        return store.move_race(identifier, data["folder_id"])
                    race = store.get_race(identifier)
                    return store.update_race(identifier, data.get("course", race["course"]), data.get("recording_id", race["recording_id"]), data.get("offset_ms", race["offset_ms"]))
                if self.command == "POST" and re.fullmatch(r"/api/races/[^/]+/results", path):
                    correction = data.get("correction", False)
                    if type(correction) is not bool:
                        raise ValueError("correction must be a boolean")
                    method = store.correct_result if correction else store.submit_result
                    return method(path.split("/")[3], data.get("player_ids"))
                if self.command == "POST" and path in {"/api/capture/review", "/api/capture/dismiss"}:
                    if not app.capture:
                        raise ValueError("No capture review queue")
                    if path.endswith("review"):
                        return app.capture.review(data.get("candidate_id"), data.get("players"))
                    return app.capture.dismiss(data.get("candidate_id"))
                raise KeyError("route")

        def _upload_import(self):
            self._check_write_origin()
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked uploads are not supported")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            if not 0 < length <= MAX_UPLOAD_BYTES:
                raise ValueError("Video upload must be between 1 byte and 64 GiB; use a local path for larger files")
            name = unquote(self.headers.get("X-Filename", ""))
            if (not name or len(name) > 200 or Path(name).name != name
                    or any(char in name for char in '/\\:<>"|?*')
                    or name.endswith((' ', '.')) or any(ord(char) < 32 for char in name)):
                raise ValueError("Provide a plain video filename in X-Filename")
            if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("Unsupported video extension")
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if not set(query) <= {"folder_id", "name_engine", "stride", "expected_tracker_mode"} or any(
                    len(v) != 1 for v in query.values()):
                raise ValueError("Invalid upload options")
            options = {key: values[0] for key, values in query.items()}
            try:
                stride = float(options.get("stride", .5))
            except ValueError:
                raise ValueError("Invalid sampling interval") from None
            if not math.isfinite(stride) or not .1 <= stride <= 10:
                raise ValueError("Sampling interval must be between 0.1 and 10 seconds")
            if options.get("name_engine", "auto") not in NAME_ENGINES:
                raise ValueError("Unknown name recognition engine")
            with app.lock:
                app.check_tracker_mode(options.get('expected_tracker_mode', 'mogi'))
                if options.get("folder_id") and not app.store.folder_exists(options["folder_id"]):
                    raise ValueError("Choose an existing history folder")
            uploads = app.videos.root
            target = uploads / f"{uuid4()}-{name}"
            with app.videos._upload_directory() as descriptor:
                if descriptor is None:
                    raise ValueError("The managed uploads directory changed. Try again.")
                if shutil.disk_usage(uploads).free < length + 512 * 1024**2:
                    raise ValueError("Not enough free disk space for this upload; use the video's local path instead")
                # Register before creation, so concurrent cleanup cannot unlink
                # a partial upload. Keep it protected until import owns the file.
                with app.lock:
                    app.check_tracker_mode(options.get('expected_tracker_mode', 'mogi'))
                    if options.get('folder_id') and not app.store.folder_exists(options['folder_id']):
                        raise ValueError('Choose an existing history folder')
                    app.imports.check_available()
                    app.active_uploads.add(target)
                try:
                    self.connection.settimeout(120)
                    with app.videos._upload(descriptor, target.name) as output:
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Video upload ended before its declared size")
                            output.write(chunk)
                            remaining -= len(chunk)
                        output.flush()
                        app.videos._check_directory(descriptor)
                        return app.imports.start(str(target), options.get("folder_id") or None,
                                                 options.get("name_engine", "auto"), stride, upload_path=target)
                finally:
                    with app.lock:
                        app.active_uploads.discard(target)

        def _backup_upload(self):
            from .transfers import MAX_ARCHIVE
            self._check_write_origin()
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked backup uploads are not supported')
            if self.headers.get('Content-Type', '').split(';', 1)[0] != 'application/zip':
                raise ValueError('Choose a session ZIP archive')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise ValueError('Invalid Content-Length') from None
            if not 0 < length <= MAX_ARCHIVE:
                raise ValueError('Backup archive must be between 1 byte and 128 MiB')
            deadline = time.monotonic() + 120
            data = bytearray()
            while len(data) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Backup upload exceeded its two-minute deadline')
                self.connection.settimeout(min(self.timeout, remaining))
                chunk = self.rfile.read1(min(65536, length - len(data)))
                if not chunk:
                    raise ValueError('Incomplete backup upload')
                data.extend(chunk)
            return app.backups.prepare(bytes(data))

        def do_POST(self):
            try:
                path = urlsplit(self.path).path
                if self.command == 'POST' and path == '/api/backups/preview':
                    return self._send(200, self._backup_upload())
                if self.command == 'POST' and path in ('/api/backups/export', '/api/backups/import'):
                    data = self._body()
                    data.pop('expected_tracker_mode', None)  # Scope explicitly spans all modes.
                    key = 'sessions' if path.endswith('/export') else 'token'
                    if set(data) != {key}:
                        raise ValueError('Unexpected backup options')
                    if key == 'sessions':
                        return self._send(200, app.backups.export(data[key]), 'application/zip', 'mkw-sessions.zip')
                    return self._send(200, app.backups.confirm(data[key]))
                result = (self._upload_import() if self.command == "POST"
                          and urlsplit(self.path).path == "/api/imports/upload" else self._mutate())
                self._send(200, result)
            except Exception as error:
                self._error(error)

        do_PATCH = do_POST
        do_DELETE = do_POST

    server = BoundHTTPServer(address, Handler)
    server.daemon_threads = False
    return server
