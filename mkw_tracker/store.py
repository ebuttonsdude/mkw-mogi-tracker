"""Persistent race results, session scores, and editable team assignments.

Callers sharing a store across threads must serialize access. Historical result
revisions are retained; totals use only the latest revision in the active session.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import re
from pathlib import Path
import sqlite3
from uuid import uuid4

from .track_icons import icon_url

_UNSET = object()


POINTS = (15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)


def _text(value, field, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a nonempty string of at most {limit} characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} cannot contain control characters")
    return value.strip()


def _now():
    return datetime.now(timezone.utc).isoformat()


class TrackerStore:
    def __init__(self, path, *, default_format="teams"):
        if default_format not in ("teams", "ffa"):
            raise ValueError("Choose teams or ffa session format")
        self.default_format = default_format
        self._db = sqlite3.connect(path, check_same_thread=False)
        self.on_commit = None
        try:
            self._initialize(path)
        except BaseException:
            self._db.close()
            raise

    def _initialize(self, path):
        # Backup the exact legacy database before any schema or data mutation.
        tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'players' in tables:
            columns = {r[1] for r in self._db.execute('PRAGMA table_info(players)')}
            backup = Path(str(path) + '.pre-session-migration.bak')
            if 'session_id' not in columns and str(path) != ':memory:' and not backup.exists():
                target = sqlite3.connect(backup)
                try:
                    self._db.backup(target)
                finally:
                    target.close()
        self._db.row_factory = sqlite3.Row
        with self._db:
            cursor = self._db.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, started_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_session (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    session_id TEXT NOT NULL REFERENCES sessions(id));
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    team_id TEXT REFERENCES teams(id));
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, started_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS races (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    course TEXT NOT NULL, recording_id TEXT REFERENCES recordings(id),
                    offset_ms INTEGER CHECK(offset_ms >= 0), revision INTEGER NOT NULL DEFAULT 0,
                    CHECK(offset_ms IS NULL OR recording_id IS NOT NULL));
                CREATE TABLE IF NOT EXISTS result_revisions (
                    race_id TEXT NOT NULL REFERENCES races(id), revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(race_id, revision));
                CREATE TABLE IF NOT EXISTS placements (
                    race_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    player_id TEXT NOT NULL REFERENCES players(id),
                    position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 12),
                    PRIMARY KEY(race_id, revision, position),
                    UNIQUE(race_id, revision, player_id),
                    FOREIGN KEY(race_id, revision)
                        REFERENCES result_revisions(race_id, revision));
            """)
            cursor.close()
        with self._transaction():
            self._migrate_history()
            self._migrate_sessions()
            self._write("CREATE UNIQUE INDEX IF NOT EXISTS sessions_folder_owner ON sessions(folder_id)")
            self._write("CREATE INDEX IF NOT EXISTS races_recording ON races(recording_id)")
            self._write("CREATE INDEX IF NOT EXISTS races_session ON races(session_id)")
            self._write("CREATE INDEX IF NOT EXISTS races_folder ON races(folder_id)")
            self._write("CREATE INDEX IF NOT EXISTS players_session ON players(session_id)")
            self._write("CREATE INDEX IF NOT EXISTS teams_session ON teams(session_id)")
            self._write("CREATE INDEX IF NOT EXISTS penalties_race ON penalties(race_id)")
            if not self._rows("SELECT * FROM current_session"):
                session_id = self._new_session()
                self._write("INSERT INTO current_session VALUES (1, ?)", (session_id,))

    def _migrate_history(self):
        """Upgrade existing databases without replacing identities or results."""
        self._write("""CREATE TABLE IF NOT EXISTS history_folders (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)""")
        additions = {
            "history_folders": [("lounge_id", "TEXT")],
            "players": [("active", "INTEGER NOT NULL DEFAULT 1")],
            "teams": [("active", "INTEGER NOT NULL DEFAULT 1"), ("color", "TEXT")],
            "sessions": [("folder_id", "TEXT REFERENCES history_folders(id)"),
                         ("format", "TEXT NOT NULL DEFAULT 'teams'")],
            "races": [("folder_id", "TEXT REFERENCES history_folders(id)"),
                      ("created_at", "TEXT"),
                      ("auto_confirmed", "INTEGER NOT NULL DEFAULT 0 CHECK(auto_confirmed IN (0,1))")],
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in self._rows(f"PRAGMA table_info({table})")}
            for name, declaration in columns:
                if name not in existing:
                    self._write(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        for session in self._rows("SELECT * FROM sessions WHERE folder_id IS NULL ORDER BY rowid"):
            folder_id = self._new_folder("Session " + session["started_at"], session["started_at"])
            self._write("UPDATE sessions SET folder_id=? WHERE id=?", (folder_id, session["id"]))
        self._write("""UPDATE races SET folder_id=(
            SELECT folder_id FROM sessions WHERE sessions.id=races.session_id)
            WHERE folder_id IS NULL""")
        self._write("""UPDATE races SET created_at=(
            SELECT started_at FROM sessions WHERE sessions.id=races.session_id)
            WHERE created_at IS NULL""")

    def _migrate_sessions(self):
        self._write("CREATE TABLE IF NOT EXISTS tracker_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._write("""CREATE TABLE IF NOT EXISTS penalties (
            id TEXT PRIMARY KEY, race_id TEXT NOT NULL REFERENCES races(id),
            player_id TEXT NOT NULL REFERENCES players(id), points INTEGER NOT NULL CHECK(points>0),
            reason TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._write("""CREATE TABLE IF NOT EXISTS race_evidence (
            id TEXT PRIMARY KEY, race_id TEXT NOT NULL REFERENCES races(id), filename TEXT NOT NULL,
            kind TEXT NOT NULL, offset_ms INTEGER, label TEXT NOT NULL,
            UNIQUE(race_id, filename, kind))""")
        existing = {r['name'] for r in self._rows('PRAGMA table_info(players)')}
        if 'session_id' in existing:
            return
        for table in ('players', 'teams'):
            self._write(f'ALTER TABLE {table} ADD COLUMN session_id TEXT REFERENCES sessions(id)')
        current = self._rows('SELECT session_id FROM current_session')
        current_id = current[0]['session_id'] if current else None
        # A folder, rather than the old scoring-session field, owns all its races.
        for folder in self._rows('SELECT * FROM history_folders ORDER BY rowid'):
            owners = self._rows('SELECT * FROM sessions WHERE folder_id=? ORDER BY rowid', (folder['id'],))
            if not owners:
                owner = str(uuid4())
                self._write('INSERT INTO sessions(id, started_at, folder_id) VALUES(?,?,?)',
                            (owner, folder['created_at'], folder['id']))
            else:
                owner = next((r['id'] for r in owners if r['id'] == current_id), owners[0]['id'])
                for duplicate in owners:
                    if duplicate['id'] != owner:
                        self._write('UPDATE races SET session_id=? WHERE session_id=?', (owner, duplicate['id']))
                        self._write('DELETE FROM sessions WHERE id=?', (duplicate['id'],))
            self._write('UPDATE races SET session_id=? WHERE folder_id=?', (owner, folder['id']))
        players = self._rows('SELECT * FROM players ORDER BY rowid')
        teams = {r['id']: r for r in self._rows('SELECT * FROM teams ORDER BY rowid')}
        team_map = {}
        # Reserve current, active team IDs even when no player is assigned yet.
        for team in teams.values():
            if current_id and team['active']:
                team_map[(team['id'], current_id)] = team['id']
                self._write('UPDATE teams SET session_id=? WHERE id=?', (current_id, team['id']))
        for player in players:
            owners = {r['session_id'] for r in self._rows(
                'SELECT DISTINCT r.session_id FROM placements p JOIN races r ON r.id=p.race_id WHERE p.player_id=?',
                (player['id'],))}
            if current_id and player['active']:
                owners.add(current_id)
            if not owners and current_id:
                owners.add(current_id)
            ordered = sorted(owners, key=lambda owner: (owner != current_id, owner))
            for index, owner in enumerate(ordered):
                player_id = player['id'] if index == 0 else str(uuid4())
                team_id = None
                if player['team_id']:
                    key = (player['team_id'], owner)
                    if key not in team_map:
                        team = teams[player['team_id']]
                        used = any(k[0] == team['id'] for k in team_map)
                        team_id = str(uuid4()) if used else team['id']
                        active = team['active'] if owner == current_id else 1
                        if used:
                            self._write('INSERT INTO teams(id,name,active,session_id,color) VALUES(?,?,?,?,?)',
                                        (team_id, team['name'], active, owner, team['color']))
                        else:
                            self._write('UPDATE teams SET session_id=?,active=? WHERE id=?', (owner, active, team_id))
                        team_map[key] = team_id
                    team_id = team_map[key]
                active = player['active'] if owner == current_id else 1
                if index == 0:
                    self._write('UPDATE players SET session_id=?,team_id=?,active=? WHERE id=?',
                                (owner, team_id, active, player_id))
                else:
                    self._write('INSERT INTO players(id,name,team_id,active,session_id) VALUES(?,?,?,?,?)',
                                (player_id, player['name'], team_id, active, owner))
                    self._write('UPDATE placements SET player_id=? WHERE player_id=? AND race_id IN '
                                '(SELECT id FROM races WHERE session_id=?)', (player_id, player['id'], owner))
        if current_id:
            self._write('UPDATE teams SET session_id=? WHERE session_id IS NULL', (current_id,))
        if players or teams:
            self._write('INSERT OR REPLACE INTO tracker_metadata VALUES(?,?)', ('migration_notice',
                'Legacy folders are now independent sessions. Referenced historical rosters were restored; '
                'old reset and deletion flags cannot be distinguished. Current deleted identities remain excluded.'))

    def _new_folder(self, name, created_at=None):
        folder_id = str(uuid4())
        self._write("INSERT INTO history_folders (id, name, created_at) VALUES (?, ?, ?)",
                    (folder_id, name, created_at or _now()))
        return folder_id

    def _new_session(self, name=None):
        session_id, started_at = str(uuid4()), _now()
        folder_id = self._new_folder(name or "Session " + started_at, started_at)
        self._write("INSERT INTO sessions (id, started_at, folder_id, format) VALUES (?, ?, ?, ?)",
                    (session_id, started_at, folder_id, self.default_format))
        return session_id

    def _active(self, table, identifier):
        row = self._get(table, identifier)
        if not row["active"] or row["session_id"] != self._session_id():
            raise ValueError("identity is archived; create or select an active identity")
        return row

    def close(self):
        self._db.close()

    @contextmanager
    def _transaction(self, *, readonly=False):
        changes = self._db.total_changes
        self._write("BEGIN" if readonly else "BEGIN IMMEDIATE")
        try:
            yield
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        # Notification failures must not attempt to roll back an already
        # committed write. The application owns output-error handling.
        if not readonly and self._db.total_changes != changes and self.on_commit is not None:
            self.on_commit()

    def _write(self, sql, values=()):
        cursor = self._db.execute(sql, values)
        cursor.close()

    def _rows(self, sql, values=()):
        cursor = self._db.execute(sql, values)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _get(self, table, identifier):
        # Table names are internal constants, never user input.
        identifier = _text(identifier, "id")
        rows = self._rows(f"SELECT * FROM {table} WHERE id = ?", (identifier,))
        if not rows:
            raise KeyError(identifier)
        return rows[0]

    def _session_id(self):
        return self._rows("SELECT session_id FROM current_session")[0]["session_id"]

    def active_session(self):
        """Read current ownership without loading scores or history."""
        return self._get("sessions", self._session_id())

    def get_race(self, race_id):
        """Read one current race, including mode-specific results and evidence."""
        return self._race(race_id)

    def race_exists(self, race_id):
        return bool(self._rows("SELECT 1 FROM races WHERE id=?", (race_id,)))

    def session_exists(self, session_id):
        return bool(self._rows("SELECT 1 FROM sessions WHERE id=?", (session_id,)))

    def get_folder(self, folder_id):
        return self._folder(folder_id)

    def folder_exists(self, folder_id):
        return bool(self._rows("SELECT 1 FROM history_folders WHERE id=?", (folder_id,)))

    def get_player(self, player_id):
        return self._active("players", player_id)

    def get_team(self, team_id):
        return self._active("teams", team_id)

    def recording_chapters(self, recording_id):
        if not self._rows("SELECT 1 FROM recordings WHERE id=?", (recording_id,)):
            raise KeyError("recording")
        return self._rows("""SELECT course,offset_ms FROM races
            WHERE recording_id=? AND offset_ms IS NOT NULL ORDER BY offset_ms,rowid""", (recording_id,))

    def count_recording_races(self, recording_id):
        return self._rows("SELECT COUNT(*) AS count FROM races WHERE recording_id=?", (recording_id,))[0]["count"]

    def add_player(self, name, player_id=None, *, expected_session_id=_UNSET):
        name = _text(name, "name")
        player_id = str(uuid4()) if player_id is None else _text(player_id, "player_id")
        with self._transaction():
            if expected_session_id is not _UNSET:
                self._expect_session(expected_session_id)
            if self._rows("SELECT id FROM players WHERE id=?", (player_id,)):
                raise ValueError("player_id already exists; update the existing player explicitly")
            self._write("INSERT INTO players (id, name, team_id, session_id) VALUES (?, ?, NULL, ?)",
                        (player_id, name, self._session_id()))
        return self._get("players", player_id)

    def create_team(self, name, *, expected_session_id=_UNSET):
        if self.active_session()["format"] == "ffa":
            raise ValueError("Teams are unavailable in Mogi FFA")
        name = _text(name, "name")
        team_id = str(uuid4())
        with self._transaction():
            if expected_session_id is not _UNSET:
                self._expect_session(expected_session_id)
            self._write("INSERT INTO teams (id, name, session_id) VALUES (?, ?, ?)", (team_id, name, self._session_id()))
        return self._get("teams", team_id)

    def update_player(self, player_id, name, team_id):
        name = _text(name, "name")
        with self._transaction():
            player = self._active("players", player_id)
            if team_id is not None:
                if self.active_session()["format"] == "ffa":
                    raise ValueError("Team assignments are unavailable in Mogi FFA")
                team_id = self._active("teams", team_id)["id"]
            self._write("UPDATE players SET name=?, team_id=? WHERE id=?",
                        (name, team_id, player["id"]))
        return self._get("players", player["id"])

    def delete_player(self, player_id):
        with self._transaction():
            player = self._active("players", player_id)
            self._write("UPDATE players SET active=0 WHERE id=?", (player["id"],))
        return self._get("players", player["id"])

    def delete_team(self, team_id):
        with self._transaction():
            team = self._active("teams", team_id)
            self._write("UPDATE players SET team_id=NULL WHERE team_id=? AND active=1", (team["id"],))
            self._write("UPDATE teams SET active=0 WHERE id=?", (team["id"],))
        return self._get("teams", team["id"])

    def rename_team(self, team_id, name):
        return self.update_team(team_id, name=name)

    def update_team(self, team_id, *, name=_UNSET, color=_UNSET):
        if name is _UNSET and color is _UNSET:
            raise ValueError('Provide a team name or color')
        if name is not _UNSET:
            name = _text(name, 'name')
        if color is not _UNSET and color is not None:
            if not isinstance(color, str) or re.fullmatch(r'#[0-9a-fA-F]{6}', color) is None:
                raise ValueError('Team color must be #rrggbb or null for automatic colors')
            color = color.lower()
        with self._transaction():
            team = self._active("teams", team_id)
            self._write('UPDATE teams SET name=?,color=? WHERE id=?',
                        (team['name'] if name is _UNSET else name,
                         team['color'] if color is _UNSET else color, team['id']))
        return self._get("teams", team["id"])

    def create_folder(self, name):
        name = _text(name, "name")
        with self._transaction():
            session_id = self._new_session(name)
            folder_id = self._get("sessions", session_id)["folder_id"]
        return self._folder(folder_id)

    def update_folder(self, folder_id, *, name=_UNSET, lounge_id=_UNSET):
        """Update only supplied metadata; null or empty Lounge ID clears it."""
        if name is not _UNSET:
            name = _text(name, "name")
        if lounge_id is not _UNSET:
            if lounge_id in (None, ""):
                lounge_id = None
            elif (not isinstance(lounge_id, str) or len(lounge_id) > 32
                  or not lounge_id.isascii() or not lounge_id.isdigit()):
                raise ValueError("lounge_id must be up to 32 ASCII digits or null")
        with self._transaction():
            folder = self._get("history_folders", folder_id)
            self._write("UPDATE history_folders SET name=?, lounge_id=? WHERE id=?",
                        (folder["name"] if name is _UNSET else name,
                         folder["lounge_id"] if lounge_id is _UNSET else lounge_id,
                         folder["id"]))
        return self._get("history_folders", folder["id"])

    def rename_folder(self, folder_id, name):
        return self.update_folder(folder_id, name=name)

    def move_race(self, race_id, folder_id):
        with self._transaction():
            race = self._get("races", race_id)
            folder = self._get("history_folders", folder_id)
            if race["folder_id"] != folder["id"]:
                raise ValueError("races cannot move between independent sessions")
        return self._race(race["id"])

    def create_recording(self, label, recording_id=None):
        label = _text(label, "label")
        recording_id = str(uuid4()) if recording_id is None else _text(recording_id, "recording_id")
        with self._transaction():
            previous = self._rows("SELECT * FROM recordings WHERE id=?", (recording_id,))
            if previous:
                if previous[0]["label"] != label:
                    raise ValueError("recording_id already has a different label")
            else:
                self._write("INSERT INTO recordings VALUES (?, ?, ?)", (recording_id, label, _now()))
        return self._get("recordings", recording_id)

    def create_race(self, race_id, course, recording_id=None, offset_ms=None, folder_id=None, *, session_id=None,
                    expected_session_id=_UNSET):
        race_id, course = _text(race_id, "race_id"), _text(course, "course")
        if offset_ms is not None and (type(offset_ms) is not int or not 0 <= offset_ms <= 2**63 - 1):
            raise ValueError("offset_ms must be a nonnegative SQLite integer or null")
        if offset_ms is not None and recording_id is None:
            raise ValueError("offset_ms requires a recording_id")
        with self._transaction():
            if expected_session_id is not _UNSET:
                self._expect_session(expected_session_id)
            if folder_id is not None:
                folder_session = self._folder(folder_id)['session_id']
                if session_id is not None and session_id != folder_session:
                    raise ValueError('folder and session must have the same owner')
                session_id = folder_session
            session = self._get("sessions", self._session_id() if session_id is None
                                else _text(session_id, "session_id"))
            if recording_id is not None:
                recording_id = self._get("recordings", recording_id)["id"]
            if folder_id is not None:
                folder_id = self._get("history_folders", folder_id)["id"]
            existing = self._rows("SELECT * FROM races WHERE id=?", (race_id,))
            if existing:
                old = existing[0]
                if ((old["course"], old["recording_id"], old["offset_ms"]) != (course, recording_id, offset_ms)
                        or (session_id is not None and old["session_id"] != session["id"])):
                    raise ValueError("race_id already has different metadata")
            else:
                self._write("""INSERT INTO races
                    (id, session_id, course, recording_id, offset_ms, revision, folder_id, created_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                    (race_id, session["id"], course, recording_id, offset_ms,
                     folder_id or session["folder_id"], _now()))
        return self._race(race_id)

    def _race(self, race_id):
        race = self._get("races", race_id)
        confirmation = self._rows(
            'SELECT rowid AS input_order FROM result_revisions WHERE race_id=? AND revision=1', (race_id,))
        race['confirmed_order'] = confirmation[0]['input_order'] if confirmation else None
        race["status"] = "scored" if race["revision"] else "pending"
        race["icon_url"] = icon_url(race["course"])
        race["placements"] = self._rows(
            """SELECT p.player_id, p.position, players.name AS player_name,
                players.active AS player_active FROM placements p
                JOIN players ON players.id=p.player_id
                WHERE p.race_id=? AND p.revision=? ORDER BY p.position""",
            (race["id"], race["revision"]))
        for placement in race["placements"]:
            placement["points"] = POINTS[placement["position"] - 1]
        race['penalties'] = self._rows('SELECT * FROM penalties WHERE race_id=? ORDER BY rowid', (race_id,))
        for placement in race['placements']:
            placement['penalty_points'] = sum(p['points'] for p in race['penalties'] if p['player_id'] == placement['player_id'])
            placement['net_points'] = placement['points'] - placement['penalty_points']
        race['evidence'] = self._rows("SELECT filename,kind,offset_ms,label FROM race_evidence WHERE race_id=? ORDER BY CASE kind WHEN 'results' THEN 0 WHEN 'suspected_results' THEN 1 ELSE 2 END,rowid", (race_id,))
        for entry in race['evidence']:
            entry['url'] = '/evidence/' + entry.pop('filename')
        race['evidence_url'] = race['evidence'][0]['url'] if race['evidence'] else None
        return race

    def _races(self, session_id=None):
        """Hydrate an ordered race collection in four queries, not per race."""
        where = " WHERE r.session_id=?" if session_id is not None else ""
        values = (session_id,) if session_id is not None else ()
        races = self._rows("""SELECT r.*, first.rowid AS confirmed_order FROM races r
            LEFT JOIN result_revisions first ON first.race_id=r.id AND first.revision=1"""
            + where + " ORDER BY r.rowid", values)
        by_id = {race['id']: race for race in races}
        for race in races:
            race.update(status="scored" if race['revision'] else "pending", icon_url=icon_url(race['course']),
                        placements=[], penalties=[], evidence=[], evidence_url=None)
        placements = self._rows("""SELECT p.race_id,p.player_id,p.position,players.name AS player_name,
                players.active AS player_active FROM races r
            JOIN placements p ON p.race_id=r.id AND p.revision=r.revision
            JOIN players ON players.id=p.player_id""" + where + " ORDER BY r.rowid,p.position", values)
        penalties = self._rows("SELECT p.* FROM races r JOIN penalties p ON p.race_id=r.id"
                              + where + " ORDER BY r.rowid,p.rowid", values)
        penalty_totals = {}
        for penalty in penalties:
            by_id[penalty['race_id']]['penalties'].append(penalty)
            key = (penalty['race_id'], penalty['player_id'])
            penalty_totals[key] = penalty_totals.get(key, 0) + penalty['points']
        for placement in placements:
            race_id = placement.pop('race_id')
            points = POINTS[placement['position'] - 1]
            penalty = penalty_totals.get((race_id, placement['player_id']), 0)
            placement.update(points=points, penalty_points=penalty, net_points=points - penalty)
            by_id[race_id]['placements'].append(placement)
        evidence = self._rows("""SELECT e.race_id,e.filename,e.kind,e.offset_ms,e.label
            FROM races r JOIN race_evidence e ON e.race_id=r.id""" + where + """
            ORDER BY r.rowid,CASE e.kind WHEN 'results' THEN 0 WHEN 'suspected_results' THEN 1 ELSE 2 END,e.rowid""", values)
        for entry in evidence:
            race = by_id[entry.pop('race_id')]
            entry['url'] = '/evidence/' + entry.pop('filename')
            race['evidence'].append(entry)
            if race['evidence_url'] is None:
                race['evidence_url'] = entry['url']
        return races

    def update_race(self, race_id, course, recording_id=None, offset_ms=None):
        """Correct chapter metadata without changing scoring-session ownership."""
        course = _text(course, "course")
        if offset_ms is not None and (type(offset_ms) is not int or not 0 <= offset_ms <= 2**63 - 1):
            raise ValueError("offset_ms must be a nonnegative SQLite integer or null")
        if offset_ms is not None and recording_id is None:
            raise ValueError("offset_ms requires a recording_id")
        with self._transaction():
            race = self._race(race_id)
            if recording_id is not None:
                recording_id = self._get("recordings", recording_id)["id"]
            self._write("UPDATE races SET course=?, recording_id=?, offset_ms=? WHERE id=?",
                        (course, recording_id, offset_ms, race["id"]))
        return self._race(race["id"])

    def review_result(self, race_id, players, *, auto_confirmed=False):
        """Commit reviewed names, or explicit YOLO observations, atomically."""
        if not isinstance(players, list) or len(players) != 12:
            raise ValueError("review requires exactly 12 players in finishing order")
        if type(auto_confirmed) is not bool:
            raise ValueError("auto_confirmed must be boolean")
        choices = []
        for player in players:
            if not isinstance(player, dict) or set(player) not in ({"player_id"}, {"name"}):
                raise ValueError("each reviewed player needs either player_id or name")
            key = next(iter(player))
            choices.append((key, _text(player[key], key)))
        existing = [value for key, value in choices if key == "player_id"]
        if len(set(existing)) != len(existing):
            raise ValueError("result requires 12 distinct players")
        with self._transaction():
            race = self._race(race_id)
            if race["revision"]:
                raise ValueError("race already scored; use explicit correction")
            # Only bootstrap an untouched session. Include inactive identities so
            # deleting a manual roster cannot unexpectedly enable grouping again.
            session_id = race["session_id"]
            initial_roster = not auto_confirmed and self._get("sessions", session_id)["format"] != "ffa" and not self._rows("""SELECT id FROM players WHERE session_id=?
                UNION ALL SELECT id FROM teams WHERE session_id=?
                UNION ALL SELECT id FROM races WHERE session_id=? AND revision>0 LIMIT 1""",
                (session_id, session_id, session_id))
            initial_teams = {}
            order = []
            for key, value in choices:
                if auto_confirmed and key == "name" and value != "not-detected-fixme":
                    matches = self._rows("SELECT id FROM players WHERE session_id=? AND active=1 AND name=?",
                                         (session_id, value))
                    if len(matches) == 1 and matches[0]['id'] not in order:
                        key, value = "player_id", matches[0]['id']
                if key == "player_id":
                    player = self._get("players", value)
                    if player["session_id"] != race["session_id"] or not player["active"]:
                        raise ValueError("new results require active players")
                    order.append(player["id"])
                else:
                    player_id = str(uuid4())
                    team_id = None
                    if initial_roster:
                        initial = value[0].casefold()
                        if initial not in initial_teams:
                            initial_teams[initial] = str(uuid4())
                            self._write("INSERT INTO teams (id, name, session_id) VALUES (?, ?, ?)",
                                        (initial_teams[initial], value[0].upper(), session_id))
                        team_id = initial_teams[initial]
                    self._write("INSERT INTO players (id, name, team_id, active, session_id) VALUES (?, ?, ?, 1, ?)",
                                (player_id, value, team_id, session_id))
                    order.append(player_id)
            self._write("INSERT INTO result_revisions VALUES (?, 1, ?)", (race["id"], _now()))
            for position, player_id in enumerate(order, 1):
                self._write("INSERT INTO placements VALUES (?, 1, ?, ?)", (race["id"], player_id, position))
            self._write("UPDATE races SET revision=1, auto_confirmed=? WHERE id=?",
                        (int(auto_confirmed), race["id"]))
        return self._race(race["id"])

    def _save_result(self, race_id, player_ids, correction):
        if not isinstance(player_ids, (list, tuple)) or len(player_ids) != 12:
            raise ValueError("result requires exactly 12 player IDs in finishing order")
        player_ids = [_text(player_id, "player_id") for player_id in player_ids]
        if len(set(player_ids)) != 12:
            raise ValueError("result requires 12 distinct players")
        with self._transaction():
            race = self._race(race_id)
            for player_id in player_ids:
                self._get("players", player_id)
            previous = [entry["player_id"] for entry in race["placements"]]
            if correction and not race["revision"]:
                raise ValueError("cannot correct an unscored race")
            if previous == player_ids:
                return race
            if race["revision"] and not correction:
                raise ValueError("race is already scored differently; use explicit correction")
            for player_id in player_ids:
                player = self._get('players', player_id)
                if player['session_id'] != race['session_id']:
                    raise ValueError('player belongs to another session')
                if not player['active'] and player_id not in previous:
                    raise ValueError('new result identities require active players')
            if any(p['player_id'] not in player_ids for p in race['penalties']):
                raise ValueError('remove penalties before removing their player from a result')
            revision = race["revision"] + 1
            self._write("INSERT INTO result_revisions VALUES (?, ?, ?)", (race["id"], revision, _now()))
            for position, player_id in enumerate(player_ids, 1):
                self._write("INSERT INTO placements VALUES (?, ?, ?, ?)",
                            (race["id"], revision, player_id, position))
            self._write("UPDATE races SET revision=? WHERE id=?", (revision, race["id"]))
        return self._race(race["id"])

    def submit_result(self, race_id, player_ids):
        return self._save_result(race_id, player_ids, correction=False)

    def correct_result(self, race_id, player_ids):
        return self._save_result(race_id, player_ids, correction=True)

    def _folder(self, folder_id):
        folder = self._get('history_folders', folder_id)
        session = self._rows('SELECT id, format FROM sessions WHERE folder_id=?', (folder_id,))[0]
        folder.update(session_id=session['id'], format=session['format'])
        return folder

    def _expect_session(self, expected_session_id):
        if expected_session_id != self._session_id():
            raise ValueError('scoring session changed; reload the page and re-enter your draft before continuing')

    def create_session(self, name, expected_session_id):
        name = _text(name, 'name')
        with self._transaction():
            self._expect_session(expected_session_id)
            session_id = self._new_session(name)
            self._write('UPDATE current_session SET session_id=? WHERE singleton=1', (session_id,))
        return self._get('sessions', session_id)

    new_session = create_session

    def load_session(self, folder_id, expected_session_id):
        with self._transaction():
            self._expect_session(expected_session_id)
            session_id = self._folder(folder_id)['session_id']
            self._write('UPDATE current_session SET session_id=? WHERE singleton=1', (session_id,))
        return self._get('sessions', session_id)

    load_folder = load_session

    def reset(self, expected_session_id):
        return self.create_session('Session ' + _now(), expected_session_id)

    def delete_folder(self, folder_id, expected_session_id=None):
        with self._transaction():
            if expected_session_id is not None:
                self._expect_session(expected_session_id)
            session_id = self._folder(folder_id)['session_id']
            race_ids = [r['id'] for r in self._rows('SELECT id FROM races WHERE session_id=?', (session_id,))]
            filenames = [r['filename'] for r in self._rows('SELECT filename FROM race_evidence WHERE race_id IN (SELECT id FROM races WHERE session_id=?)', (session_id,))]
            for table in ('race_evidence', 'penalties', 'placements', 'result_revisions'):
                self._write(f'DELETE FROM {table} WHERE race_id IN (SELECT id FROM races WHERE session_id=?)', (session_id,))
            self._write('DELETE FROM races WHERE session_id=?', (session_id,))
            self._write('DELETE FROM players WHERE session_id=?', (session_id,))
            self._write('DELETE FROM teams WHERE session_id=?', (session_id,))
            if session_id == self._session_id():
                replacement = self._new_session()
                self._write('UPDATE current_session SET session_id=? WHERE singleton=1', (replacement,))
            self._write('DELETE FROM sessions WHERE id=?', (session_id,))
            self._write('DELETE FROM history_folders WHERE id=?', (folder_id,))
        return {'deleted_race_ids': race_ids, 'deleted_evidence_files': filenames, 'folder_id': folder_id}

    def delete_race(self, race_id):
        """Remove one race's contribution and revisions, retaining its roster."""
        with self._transaction():
            race = self._race(race_id)
            filenames = [entry['filename'] for entry in self._rows(
                'SELECT filename FROM race_evidence WHERE race_id=?', (race['id'],))]
            for table in ('race_evidence', 'penalties', 'placements', 'result_revisions'):
                self._write(f'DELETE FROM {table} WHERE race_id=?', (race['id'],))
            self._write('DELETE FROM races WHERE id=?', (race['id'],))
        return {'deleted_race_ids': [race['id']], 'deleted_evidence_files': filenames,
                'session_id': race['session_id'], 'folder_id': race['folder_id']}

    def add_penalty(self, race_id, player_id, points, reason=''):
        if type(points) is not int or not 1 <= points <= 1000000:
            raise ValueError('penalty points must be a positive integer no greater than 1000000')
        if not isinstance(reason, str) or len(reason) > 500 or any(ord(c) < 32 for c in reason):
            raise ValueError('reason must be a string of at most 500 characters without controls')
        with self._transaction():
            race = self._race(race_id)
            if player_id not in [p['player_id'] for p in race['placements']]:
                raise ValueError('penalties require a confirmed participant in this race')
            player = self._get('players', player_id)
            if player['session_id'] != race['session_id']:
                raise ValueError('player belongs to another session')
            identifier = str(uuid4())
            self._write('INSERT INTO penalties VALUES(?,?,?,?,?,?)', (identifier, race_id, player_id, points, reason.strip(), _now()))
        return self._get('penalties', identifier)

    def remove_penalty(self, penalty_id):
        with self._transaction():
            penalty = self._get('penalties', penalty_id)
            self._write('DELETE FROM penalties WHERE id=?', (penalty['id'],))
        return penalty

    def add_evidence(self, race_id, filename, kind='results', offset_ms=None, label=''):
        if not isinstance(filename, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\.png', filename):
            raise ValueError('evidence filename must be a bare UUID PNG filename')
        if kind not in ('results', 'suspected_results', 'prerace'):
            raise ValueError('evidence kind must be results, suspected_results or prerace')
        if offset_ms is not None and (type(offset_ms) is not int or not 0 <= offset_ms <= 2**63-1):
            raise ValueError('evidence offset must be a nonnegative integer or null')
        if not isinstance(label, str) or len(label) > 500 or any(ord(c) < 32 for c in label):
            raise ValueError('invalid evidence label')
        with self._transaction():
            self._get('races', race_id)
            self._write('INSERT OR IGNORE INTO race_evidence VALUES(?,?,?,?,?,?)', (str(uuid4()), race_id, filename, kind, offset_ms, label))
            entry = self._rows('SELECT * FROM race_evidence WHERE race_id=? AND filename=? AND kind=?', (race_id, filename, kind))[0]
        entry['url'] = '/evidence/' + filename
        return entry

    def snapshot(self, *, session_id=None, include_history=True):
        """Read full dashboard history, or just one session for an output/report."""
        with self._transaction(readonly=True):
            session_id = self._session_id() if session_id is None else session_id
            session = self._get("sessions", session_id)
            session_id = session['id']
            players = self._rows("SELECT * FROM players WHERE active=1 AND session_id=? ORDER BY rowid", (session_id,))
            teams = self._rows("SELECT * FROM teams WHERE active=1 AND session_id=? ORDER BY rowid", (session_id,))
            races = self._races(None if include_history else session_id)
            current_races = [r for r in races if r["session_id"] == session_id]
            totals = {player["id"]: [0, 0, 0] for player in players}
            for race in current_races:
                for entry in race["placements"]:
                    if entry["player_id"] in totals:
                        total = totals[entry["player_id"]]
                        total[0] += entry["net_points"]
                        total[1] += 1
                        total[2] += entry['penalty_points']
            team_totals = {team['id']: [0, 0] for team in teams}
            for player in players:
                player["points"], player["races"], player['penalty_points'] = totals[player["id"]]
                player['gross_points'] = player['points'] + player['penalty_points']
                if player['team_id'] in team_totals:
                    total = team_totals[player['team_id']]
                    total[0] += player['points']
                    total[1] += player['gross_points']
            for team in teams:
                team['points'], team['gross_points'] = team_totals[team['id']]
                team['penalty_points'] = team['gross_points'] - team['points']
            room = {"points": sum(e["points"] for r in current_races for e in r["placements"]),
                    "maximum_points": 984, "scored_races": sum(bool(r["revision"]) for r in current_races),
                    "expected_races": 12, "logged_races": len(current_races),
                    "penalty_points": sum(p['points'] for r in current_races for p in r['penalties']),
                    "net_points": sum(e['net_points'] for r in current_races for e in r['placements'])}
            notice = self._rows("SELECT value FROM tracker_metadata WHERE key='migration_notice'")
            archive_where = "active=0 OR session_id<>?" if include_history else "active=0 AND session_id=?"
            folder_where = "" if include_history else " WHERE s.id=?"
            folders = self._rows("""SELECT f.*, s.id AS session_id, s.format, COUNT(r.id) AS race_count
                FROM history_folders f JOIN sessions s ON s.folder_id=f.id LEFT JOIN races r ON r.folder_id=f.id"""
                + folder_where + " GROUP BY f.id ORDER BY f.rowid", () if include_history else (session_id,))
            recordings = self._rows("SELECT * FROM recordings ORDER BY rowid") if include_history else self._rows(
                """SELECT * FROM recordings WHERE id IN
                    (SELECT recording_id FROM races WHERE session_id=?) ORDER BY rowid""", (session_id,))
            return {"format": session["format"], "migration_notice": notice[0]['value'] if notice else None, "room": room, "session": session,
                    "sessions": self._rows("SELECT * FROM sessions ORDER BY rowid") if include_history else [session.copy()],
                    "players": players, "teams": teams,
                    "archived_players": self._rows("SELECT * FROM players WHERE " + archive_where + " ORDER BY rowid", (session_id,)),
                    "archived_teams": self._rows("SELECT * FROM teams WHERE " + archive_where + " ORDER BY rowid", (session_id,)),
                    "folders": folders, "recordings": recordings, "races": races}
