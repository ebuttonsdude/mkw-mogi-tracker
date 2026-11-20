"""Single-player worldwide results in a database separate from Mogi scores."""

from .store import TrackerStore, _UNSET, _now, _text


def _integer(value, field, minimum, maximum, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def ww_course_stats(races):
    courses = {}
    for race in races:
        result = race["ww_result"]
        if result is None:
            continue
        course = courses.setdefault(race["course"], {"course": race["course"], "races": 0,
            "known_changes": 0, "total_change": 0, "position_sum": 0, "wins": 0,
            "best_position": 24})
        course["races"] += 1
        course["position_sum"] += result["position"]
        course["wins"] += result["position"] == 1
        course["best_position"] = min(course["best_position"], result["position"])
        if result["delta"] is not None:
            course["known_changes"] += 1
            course["total_change"] += result["delta"]
    for course in courses.values():
        course["average_change"] = (course["total_change"] / course["known_changes"]
                                     if course["known_changes"] else None)
        course["average_position"] = course.pop("position_sum") / course["races"]
        if not course["known_changes"]:
            course["total_change"] = None
    return sorted(courses.values(), key=lambda course: (course["average_position"], course["course"]))


def ww_summary(races, player_name="Local player"):
    """Summarize ordered observations without inferring missing race changes."""
    recorded = [race for race in races if race["ww_result"] is not None]
    changes = [race["ww_result"]["delta"] for race in recorded if race["ww_result"]["delta"] is not None]
    last_ten = recorded[-10:]
    last_ten_changes = [race["ww_result"]["delta"] for race in last_ten
                        if race["ww_result"]["delta"] is not None]
    observations = [result[key] for race in recorded for result in [race["ww_result"]]
                    for key in ("vr_before", "vr_after") if result[key] is not None]
    starting_vr = races[0]["ww_result"]["vr_before"] if races and races[0]["ww_result"] else None
    graph = []
    if starting_vr is not None:
        graph.append({"race_id": None, "course": "Start", "vr": starting_vr, "gap_before": False})
    previous_vr = starting_vr
    for race in races:
        result = race["ww_result"]
        vr = result["vr_after"] if result else None
        gap = (result is None or result["vr_before"] is None or previous_vr is None
               or result["vr_before"] != previous_vr)
        graph.append({"race_id": race["id"], "course": race["course"], "vr": vr, "gap_before": gap})
        previous_vr = vr
    return {
        "player_name": player_name,
        "starting_vr": starting_vr,
        "current_vr": recorded[-1]["ww_result"]["vr_after"] if recorded else None,
        "peak_vr": max(observations) if observations else None,
        "lowest_vr": min(observations) if observations else None,
        "total_change": sum(changes) if races and len(changes) == len(races) else None,
        "known_change_sum": sum(changes) if changes else None,
        "last_10_change": (sum(last_ten_changes)
                           if last_ten and len(last_ten_changes) == len(last_ten) else None),
        "last_10_race_count": len(last_ten),
        "known_changes": len(changes), "recorded_races": len(recorded),
        "pending_races": len(races) - len(recorded),
        "graph": graph, "courses": ww_course_stats(races),
    }


class WWStore(TrackerStore):
    """Reuse chapter/session infrastructure, never Mogi placement scoring.

    Callers must provide a separate database and serialize shared access, as for
    TrackerStore. Revisions retain observations; opted-in live results derive
    their change from the latest preceding recorded race in the same session.
    """

    def _initialize(self, path):
        super()._initialize(path)
        with self._transaction():
            self._write("""CREATE TABLE IF NOT EXISTS ww_session_players (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                name TEXT NOT NULL)""")
            self._write("""CREATE TABLE IF NOT EXISTS ww_results (
                race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 24),
                lobby_size INTEGER CHECK(lobby_size BETWEEN 2 AND 24),
                vr_before INTEGER CHECK(vr_before BETWEEN 1 AND 99999),
                vr_after INTEGER NOT NULL CHECK(vr_after BETWEEN 1 AND 99999),
                delta INTEGER CHECK(delta BETWEEN -99998 AND 99998),
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                derive_from_previous INTEGER NOT NULL DEFAULT 0 CHECK(derive_from_previous IN (0,1)),
                PRIMARY KEY(race_id, revision),
                CHECK(lobby_size IS NULL OR position <= lobby_size),
                CHECK(vr_before IS NULL OR delta IS NULL OR vr_before + delta = vr_after))""")
            columns = {column["name"] for column in self._rows("PRAGMA table_info(ww_results)")}
            if "derive_from_previous" not in columns:
                # Existing capture/import revisions keep their original policy.
                self._write("ALTER TABLE ww_results ADD COLUMN derive_from_previous "
                            "INTEGER NOT NULL DEFAULT 0 CHECK(derive_from_previous IN (0,1))")

    def set_player_name(self, name, expected_session_id):
        name = _text(name, "player name")
        try:
            name.encode("utf-8")
        except UnicodeError:
            raise ValueError("player name must be valid UTF-8 text") from None
        with self._transaction():
            self._expect_session(expected_session_id)
            session_id = self._session_id()
            self._write("""INSERT INTO ww_session_players(session_id,name) VALUES(?,?)
                ON CONFLICT(session_id) DO UPDATE SET name=excluded.name""", (session_id, name))
        return {"session_id": session_id, "name": name}

    def set_ww_result(self, race_id, *, position, vr_after, vr_before=None, delta=None,
                      lobby_size=None, source="manual", derive_from_previous=False, expected_session_id=_UNSET,
                      expected_revision=_UNSET):
        if expected_revision is not _UNSET:
            _integer(expected_revision, "expected_revision", 0, 2**63 - 1)
        position = _integer(position, "position", 1, 24)
        lobby_size = _integer(lobby_size, "lobby_size", 2, 24, optional=True)
        if lobby_size is not None and position > lobby_size:
            raise ValueError("position cannot exceed lobby_size")
        vr_after = _integer(vr_after, "vr_after", 1, 99999)
        vr_before = _integer(vr_before, "vr_before", 1, 99999, optional=True)
        delta = _integer(delta, "delta", -99998, 99998, optional=True)
        if vr_before is not None:
            if delta is not None and vr_before + delta != vr_after:
                raise ValueError("vr_before plus delta must equal vr_after")
            delta = vr_after - vr_before
        elif delta is not None:
            vr_before = _integer(vr_after - delta, "vr_before", 1, 99999)
        if not isinstance(source, str) or source not in {"manual", "capture", "import", "automatic"}:
            raise ValueError("source must be manual, capture, import or automatic")
        if type(derive_from_previous) is not bool or (derive_from_previous and source != "capture"):
            raise ValueError("derive_from_previous must be a boolean and is only available for live capture")
        values = {"position": position, "lobby_size": lobby_size, "vr_before": vr_before,
                  "vr_after": vr_after, "delta": delta, "source": source,
                  "derive_from_previous": derive_from_previous}
        with self._transaction():
            if expected_session_id is not _UNSET:
                self._expect_session(expected_session_id)
            race = self._race(race_id)
            if expected_session_id is not _UNSET and race["session_id"] != expected_session_id:
                raise ValueError("race belongs to another WW session; load its session before editing")
            if expected_revision is not _UNSET and race["revision"] != expected_revision:
                raise ValueError("WW result changed; refresh the result before saving")
            # Compare stored observations, not a live result's derived view.
            previous_rows = self._rows("SELECT * FROM ww_results WHERE race_id=? AND revision=?",
                                       (race["id"], race["revision"]))
            previous = previous_rows[0] if previous_rows else None
            if previous is not None and all(previous[key] == value for key, value in values.items()):
                return race
            revision = race["revision"] + 1
            created_at = _now()
            self._write("INSERT INTO result_revisions VALUES(?,?,?)", (race["id"], revision, created_at))
            self._write("""INSERT INTO ww_results
                (race_id,revision,position,lobby_size,vr_before,vr_after,delta,source,created_at,derive_from_previous)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (race["id"], revision, position, lobby_size, vr_before, vr_after, delta, source,
                 created_at, derive_from_previous))
            self._write("UPDATE races SET revision=? WHERE id=?", (revision, race["id"]))
        return self._race(race["id"])

    def _race(self, race_id):
        race = super()._race(race_id)
        results = self._rows("SELECT * FROM ww_results WHERE race_id=? AND revision=?",
                             (race["id"], race["revision"]))
        race["ww_result"] = results[0] if results else None
        if results:
            result = race["ww_result"]
            result["derive_from_previous"] = bool(result["derive_from_previous"])
            if result["derive_from_previous"] and result["source"] == "capture":
                previous = self._rows("""SELECT result.vr_after FROM races AS previous
                    JOIN ww_results AS result ON result.race_id=previous.id AND result.revision=previous.revision
                    WHERE previous.session_id=? AND previous.history_order < ?
                    ORDER BY previous.history_order DESC LIMIT 1""", (race["session_id"], race["history_order"]))
                if previous:
                    # Final VR is observed, so this is one lookup, never a
                    # recursive chain. Corrections/deletions update the view.
                    result["vr_before"] = previous[0]["vr_after"]
                    result["delta"] = result["vr_after"] - result["vr_before"]
        race["status"] = "recorded" if results else "pending"
        return race

    def _ww_races(self, session_id=None):
        """Read latest observations in saved race order without hydrating evidence."""
        where = " WHERE race.session_id=?" if session_id is not None else ""
        rows = self._rows("""SELECT result.*, race.session_id AS owner, race.course AS course
            FROM races AS race JOIN ww_results AS result
            ON result.race_id=race.id AND result.revision=race.revision""" + where + " ORDER BY race.history_order,race.rowid",
            (session_id,) if session_id is not None else ())
        previous = {}
        races = []
        for result in rows:
            owner, course = result.pop('owner'), result.pop('course')
            result['derive_from_previous'] = bool(result['derive_from_previous'])
            if result['derive_from_previous'] and result['source'] == 'capture' and owner in previous:
                result['vr_before'] = previous[owner]
                result['delta'] = result['vr_after'] - result['vr_before']
            previous[owner] = result['vr_after']
            races.append({'id': result['race_id'], 'session_id': owner, 'course': course, 'ww_result': result})
        return races

    def _races(self, session_id=None):
        races = super()._races(session_id)
        results = {race['id']: race['ww_result'] for race in self._ww_races(session_id)}
        for race in races:
            race['ww_result'] = results.get(race['id'])
            race['status'] = 'recorded' if race['ww_result'] is not None else 'pending'
        return races

    def snapshot(self, *, session_id=None, include_history=True, include_all_courses=True):
        snapshot = super().snapshot(session_id=session_id, include_history=include_history)
        session_id = snapshot["session"]["id"]
        where = " WHERE session_id=?" if not include_history else ""
        names = {row["session_id"]: row["name"]
                 for row in self._rows("SELECT session_id,name FROM ww_session_players" + where,
                                       (session_id,) if not include_history else ())}
        races = [race for race in snapshot["races"] if race["session_id"] == session_id]
        snapshot["tracker_mode"] = "ww"
        snapshot["ww_player_names"] = names
        snapshot["ww"] = ww_summary(races, names.get(session_id, "Local player"))
        if include_all_courses:
            snapshot["ww"]["all_courses"] = ww_course_stats(snapshot["races"] if include_history else self._ww_races())
        snapshot["room"].update(maximum_points=None, expected_races=None)
        return snapshot

    def _mogi_only(self, *args, **kwargs):
        raise ValueError("Player rosters, teams, placement points and penalties are unavailable in WW mode")

    add_player = create_team = update_player = delete_player = _mogi_only
    delete_team = rename_team = update_team = _mogi_only
    review_result = submit_result = correct_result = _save_result = _mogi_only
    add_penalty = remove_penalty = _mogi_only
