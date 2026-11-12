"""Opt-in, fixed-origin Lounge cards; one bounded worker, never capture I/O.

API/source semantics and attribution: docs/overlay-profiles.md.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import math
import socket
import ssl
import threading
import time
from urllib.parse import urlencode

from .external_vision import _resolve

HOST = 'lounge.mkcentral.com'
_DNS_GATE = threading.Lock()  # Optional cards must never consume capture's DNS gate.
MAX_BYTES = 2 * 1024 * 1024
TTL = 300
RETRY = 60
TIMEOUT = 8
MAX_CACHE = 128
MAX_PENDING = 16


class LoungeUnavailable(Exception):
    """Only fixed, safe failure categories reach the public cache."""


def fetch_details(name, season, room_size, closed):
    """No proxies/redirects; pinned public peer, verified TLS and total deadline."""
    deadline = time.monotonic() + TIMEOUT

    def check(until):
        if closed.is_set() or time.monotonic() >= until:
            raise LoungeUnavailable('Request timed out or stopped')

    game = 'mkworld' if season is not None and season < 2 else f'mkworld{room_size}p'
    query = {'name': name, 'game': game}
    if season is not None:
        query['season'] = season
    family, peer = _resolve(HOST, 443, check, deadline, gate=_DNS_GATE)
    if not ipaddress.ip_address(peer[0]).is_global:
        raise LoungeUnavailable('Unsafe network destination')
    connection = http.client.HTTPConnection(HOST, 443)
    done = threading.Event()
    sockets = []

    def interrupt():
        while not done.wait(.05):
            if closed.is_set() or time.monotonic() >= deadline:
                for sock in list(sockets):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    watcher = threading.Thread(target=interrupt, daemon=True, name='lounge-deadline')
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sockets.append(sock)
        sock.settimeout(max(.01, deadline - time.monotonic()))
        watcher.start()
        check(deadline)
        sock.connect(peer)
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=HOST, do_handshake_on_connect=False)
        sockets.append(sock)
        check(deadline)
        sock.settimeout(max(.01, deadline - time.monotonic()))
        sock.do_handshake()
        connection.sock = sock
        connection.request('GET', '/api/player/details?' + urlencode(query), headers={
            'Accept': 'application/json', 'User-Agent': 'MKW-Mogi-Tracker/0.1 (optional player cards)'})
        with connection.getresponse() as response:
            if response.status == 404:
                raise LoungeUnavailable('Player not found')
            if response.status != 200:
                raise LoungeUnavailable('Lounge service unavailable')
            raw = response.read(MAX_BYTES + 1)
        check(deadline)
        if len(raw) > MAX_BYTES:
            raise LoungeUnavailable('Lounge response too large')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise LoungeUnavailable('Invalid Lounge response')
        return result
    finally:
        done.set()
        if watcher.ident is not None:
            watcher.join(timeout=1)
        connection.close()
        for sock in sockets:
            sock.close()


def number(data, key, *, minimum=None, maximum=None):
    value = data.get(key)
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return value


def safe_text(value, limit=100):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return None
    return value


def weekly(data, now, room_size):
    """Derived verified-table window, never calendar-week or account MMR totals."""
    changes = data.get('mmrChanges')
    if not isinstance(changes, list) or len(changes) > 10000:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get('reason'), str) for row in changes):
        return None
    deleted = {row.get('changeId') for row in changes if row['reason'] == 'TableDelete'
               and type(row.get('changeId')) is int}
    tables = [row for row in changes if row['reason'] == 'Table' and row.get('changeId') not in deleted]
    if (type(data.get('eventsPlayed')) is not int or len(tables) != data['eventsPlayed']
            or any(type(row.get('changeId')) is not int for row in tables)
            or len({row['changeId'] for row in tables}) != len(tables)
            or any(type(row.get('changeId')) is not int for row in changes if row['reason'] == 'TableDelete')):
        return None
    rows = []
    scores = []
    for row in changes:
        if row['reason'] != 'Table':
            continue
        if type(row.get('changeId')) is not int:
            return None
        if row['changeId'] in deleted:
            continue
        try:
            at = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
        except (KeyError, ValueError, TypeError, AttributeError):
            return None
        if at.tzinfo is None:
            # Upstream DateTime fields serialize UTC without a suffix in some builds.
            at = at.replace(tzinfo=timezone.utc)
        if not now - timedelta(days=7) <= at <= now:
            continue
        if data.get('game') == 'mkworld':
            if type(row.get('numPlayers')) is not int:
                return None
            if row['numPlayers'] != room_size:
                continue
        delta = number(row, 'mmrDelta')
        if delta is None:
            return None
        rows.append(delta)
        scores.append(number(row, 'score'))
    total = number({'value': sum(rows)}, 'value')
    average = number({'value': sum(scores) / len(scores)}, 'value') if scores and all(v is not None for v in scores) else None
    return {'events': len(rows), 'positive_mmr_rate': sum(delta > 0 for delta in rows) / len(rows) if rows else None,
            'event_mmr_change': total,
            'average_score': average,
            'from': (now - timedelta(days=7)).isoformat(), 'to': now.isoformat()}


def last_five_form(data, now):
    """Latest verified events, oldest first; unknown history never invents form."""
    changes = data.get('mmrChanges')
    if not isinstance(changes, list) or len(changes) > 10000:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get('reason'), str) for row in changes):
        return None
    relevant = [row for row in changes if row['reason'] in ('Table', 'TableDelete')]
    if any(type(row.get('changeId')) is not int or row['changeId'] <= 0 for row in relevant):
        return None
    deleted = {row['changeId'] for row in relevant if row['reason'] == 'TableDelete'}
    tables = [row for row in relevant if row['reason'] == 'Table' and row['changeId'] not in deleted]
    if (type(data.get('eventsPlayed')) is not int or len(tables) != data['eventsPlayed']
            or len({row['changeId'] for row in tables}) != len(tables)):
        return None
    events = []
    for row in tables:
        try:
            at = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
        except (KeyError, ValueError, TypeError, AttributeError):
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        delta = number(row, 'mmrDelta')
        if delta is None or at > now:
            return None
        events.append((at, 'W' if delta > 0 else 'L'))
    # Upstream sorts by verification time without a stable tie-breaker. Table
    # IDs do not establish event order, so equal timestamps stay unknown.
    if len({at for at, _ in events}) != len(events):
        return None
    return [outcome for _, outcome in sorted(events)[-5:]]


def normalize(data, name, season, room_size, now):
    player_id = data.get('playerId')
    canonical = safe_text(data.get('name'))
    if type(player_id) is not int or player_id <= 0 or not canonical or canonical.replace(' ', '').upper() != name.replace(' ', '').upper():
        raise LoungeUnavailable('Lounge username not found')
    actual_season = data.get('season')
    game = data.get('game')
    expected_game = 'mkworld' if season is not None and season < 2 else f'mkworld{room_size}p'
    if (type(actual_season) is not int or not 0 <= actual_season <= 999
            or (season is not None and actual_season != season) or game != expected_game):
        raise LoungeUnavailable('Unexpected Lounge season or room size')
    return {'player_id': player_id, 'name': canonical, 'season': actual_season, 'game': game,
            'room_size': room_size, 'division': safe_text(data.get('rank')),
            'leaderboard_rank': number(data, 'overallRank', minimum=1), 'mmr': number(data, 'mmr'),
            'peak_mmr': number(data, 'maxMmr'), 'events': number(data, 'eventsPlayed', minimum=0),
            'positive_mmr_rate': number(data, 'winRate', minimum=0, maximum=1),
            'average_score': number(data, 'averageScore'),
            'last_ten_wins': number(data, 'winsLastTen', minimum=0, maximum=10),
            'last_ten_losses': number(data, 'lossesLastTen', minimum=0, maximum=10),
            'last_ten_mmr_change': number(data, 'gainLossLastTen'),
            'last_ten_average_score': number(data, 'averageLastTen'),
            'last_five_form': last_five_form(data, now),
            'weekly': weekly(data, now, room_size), 'fetched_at': now.isoformat()}


class LoungeCache:
    """Polling schedules cache work only; disabled/WW reads never request data.

    No app/store references, unbounded queues, periodic background refresh or
    network on configuration save. Only rendered opted-in cards schedule work.
    """
    def __init__(self, fetch=fetch_details):
        self.fetch = fetch
        self.lock = threading.Condition()
        self.closed = threading.Event()
        self.cache = OrderedDict()
        self.pending = OrderedDict()
        self.active = None
        self.active_cancel = None
        self.allowed = set()
        self.worker = None

    def configure(self, configs):
        allowed = {(name, config['season'], config['room_size']) for config in configs for name in config['players']}
        with self.lock:
            self.allowed = allowed
            self.pending = OrderedDict((key, None) for key in self.pending if key in allowed)
            if self.active not in allowed and self.active_cancel is not None:
                self.active_cancel.set()

    def snapshot(self, config, *, enabled):
        if not enabled:
            return {'enabled': False, 'players': []}
        cards = []
        with self.lock:
            for name in config['players']:
                key = (name, config['season'], config['room_size'])
                entry = self.cache.get(key, {})
                age = time.monotonic() - entry.get('checked', 0)
                due = not entry or age >= (RETRY if entry.get('error') else TTL)
                if due and key in self.allowed and key != self.active and key not in self.pending and len(self.pending) < MAX_PENDING and not self.closed.is_set():
                    self.pending[key] = None
                loading = key in self.pending or key == self.active
                cards.append({'username': name, 'data': deepcopy(entry.get('data')),
                              'status': 'stale' if entry.get('data') and (entry.get('error') or due) else
                                        'ready' if entry.get('data') else 'loading' if loading else 'unavailable',
                              'error': entry.get('error'), 'refreshing': loading})
            if self.pending and (self.worker is None or not self.worker.is_alive()):
                self.worker = threading.Thread(target=self._work, daemon=True, name='lounge-cards')
                self.worker.start()
            self.lock.notify_all()
        return {'enabled': True, 'players': cards, 'metrics': deepcopy(config['metrics'])}

    def _work(self):
        while not self.closed.is_set():
            with self.lock:
                if not self.pending:
                    return
                key, _ = self.pending.popitem(last=False)
                self.active = key
                self.active_cancel = threading.Event()
                cancel = self.active_cancel
            try:
                payload = self.fetch(*key, cancel)
                data = normalize(payload, *key, datetime.now(timezone.utc))
                error = None
            except Exception as failure:
                data = None
                error = str(failure) if isinstance(failure, LoungeUnavailable) else 'Lounge request failed'
            with self.lock:
                previous = self.cache.get(key, {})
                if not self.closed.is_set() and not cancel.is_set():
                    self.cache[key] = {'data': data or previous.get('data'), 'error': error, 'checked': time.monotonic()}
                    self.cache.move_to_end(key)
                    while len(self.cache) > MAX_CACHE:
                        self.cache.popitem(last=False)
                self.active = None
                self.active_cancel = None
                self.lock.notify_all()

    def close(self):
        self.closed.set()
        with self.lock:
            self.pending.clear()
            if self.active_cancel is not None:
                self.active_cancel.set()
            worker = self.worker
        if worker is not None:
            worker.join(timeout=1)
