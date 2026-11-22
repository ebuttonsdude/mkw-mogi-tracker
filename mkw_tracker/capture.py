"""Capture lifecycle and review queue. Recognition never silently creates scores."""

import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .diagnostics import log_event


class RecordingClock:
    """Keep score resets independent from OBS recording/file identity."""

    def __init__(self, store):
        self.store = store
        self.active = False
        self.recording_id = None
        self.last_duration = None
        self.valid = False
        self.saw_inactive = False
        self.message = "No active recording."
        self.invalid_message = self.message

    def disconnected(self):
        self.valid = False
        self.saw_inactive = False
        self.message = "OBS connection lost. Recording timing needs verification."
        self.invalid_message = self.message

    def update(self, status, events=()):
        if status.get("clockUnavailable"):
            self.disconnected()
            self.message = "No OBS recording clock. Intro offsets are unknown; enter them manually."
            return None, None
        active = status.get("outputActive") is True
        duration = status.get("outputDuration")
        if not active:
            self.active = False
            self.valid = False
            self.last_duration = None
            self.saw_inactive = True
            self.message = "No active OBS recording; results can still be captured."
            return None, None
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            self.valid = False
            self.message = "OBS returned an invalid recording clock."
            self.invalid_message = self.message
            return self.recording_id, None
        duration = int(duration)
        split = any(event.get("eventType") == "RecordFileChanged" for event in events)
        started = any(event.get("eventType") == "RecordStateChanged"
                      and event.get("eventData", {}).get("outputState") == "OBS_WEBSOCKET_OUTPUT_STARTED"
                      for event in events)
        rollback = self.last_duration is not None and duration < self.last_duration
        if not self.active or split or rollback or started:
            label = "OBS " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            self.recording_id = self.store.create_recording(label, fps=status.get("recordingFps"))["id"]
            self.valid = (self.saw_inactive or started) and not split and (not rollback or started)
            if split or rollback:
                self.message = "Recording file/clock changed; verify file-local chapter offsets."
            elif not self.valid:
                self.message = "Recording was already active on connection; verify chapter timing or start a fresh recording."
            self.invalid_message = self.message
        self.active = True
        self.last_duration = duration
        self.saw_inactive = False
        if status.get("outputPaused") is True:
            self.message = "OBS recording is paused; no chapter timestamp will be assigned."
            return self.recording_id, None
        if self.valid:
            self.message = "Using OBS recording time. Intro markers are sampled and can be corrected."
        else:
            self.message = self.invalid_message
        return self.recording_id, duration if self.valid else None


class CaptureCoordinator:
    @property
    def result_message(self):
        return ('YOLO active: detected Mogi results are scored automatically.'
                if self.yolo_policy[0] else 'Detected results require review.')
    loading_message = 'Loading the local name recognizer. Start recording when capture is connected.'

    def __init__(self, store, lock, data_dir, reader, *, session_id=None, folder_id=None, pending_owner=None):
        self.store, self.lock, self.reader = store, lock, reader
        self._revision = 0
        self._yolo_enabled = False
        self._yolo_generation = 0
        self.fixed_session_id, self.folder_id = session_id, folder_id
        self.pending_owner = pending_owner
        # Live display events are deliberately not persisted or shared with
        # video-import coordinators. Restarting never replays an old race.
        self.overlay_event = None
        self._overlay_event_sequence = 0
        self.data_dir = Path(data_dir)
        self.evidence_dir = self.data_dir / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "capture.json"
        self.clock = RecordingClock(store)
        self.state = {"armed": True, "course": None, "race_id": None, "in_prerace": False, "pending": []}
        if pending_owner is not None:
            # Imported videos have independent phase latches, but one durable
            # review queue. Never reload or overwrite the live capture latch.
            self.state["pending"] = pending_owner.state["pending"]
        elif self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state.setdefault("in_prerace", False)
        self.status = "manual"
        self.message = "Choose a capture input and start capture in the web UI. Saved reviews and manual entries remain available."
        self.stop_event = threading.Event()
        self.thread = None
        self.client = None
        self.sample_timing = {}
        if pending_owner is None:
            self.prune_deleted_races()
            # Older builds kept the screenshot link only in pending cards.
            # Backfill known mappings; never guess from orphan image files.
            for candidate in self.state["pending"]:
                url = candidate.get("screenshot_url", "")
                if re.fullmatch(r"/evidence/[0-9a-f-]{36}\.png", url):
                    self.store.add_evidence(candidate["race_id"], url.rsplit("/", 1)[-1],
                                            kind="results", label="Finishing results")

    @property
    def yolo_policy(self):
        if self.pending_owner is not None:
            return self.pending_owner.yolo_policy
        return self._yolo_enabled, self._yolo_generation

    def _emit_overlay_event(self, visible, session_id):
        if self.fixed_session_id is None and self.pending_owner is None:
            self._overlay_event_sequence += 1
            self.overlay_event = (self._overlay_event_sequence, session_id, visible)

    def _partial_candidate(self):
        identifier = self.state.get('partial_candidate_id')
        return next((candidate for candidate in self.state['pending']
                     if candidate['id'] == identifier), None)

    def set_yolo(self, enabled):
        """Change future observations only; invalidate in-flight recognition."""
        with self.lock:
            if enabled != self._yolo_enabled:
                self._yolo_enabled = enabled
                self._yolo_generation += 1

    def prune_deleted_races(self):
        """Repair the JSON queue after a folder deletion or interrupted cleanup."""
        with self.lock:
            pending = self.state["pending"]
            referenced_ids = {candidate.get("race_id") for candidate in pending}
            referenced_ids.add(self.state.get("race_id"))
            race_ids = {identifier for identifier in referenced_ids
                        if identifier and self.store.race_exists(identifier)}
            retained = [candidate for candidate in pending if candidate.get("race_id") in race_ids]
            changed = len(retained) != len(pending)
            pending[:] = retained
            if ((self.state.get("race_id") and self.state["race_id"] not in race_ids)
                    or (self.state.get("session_id") and not self.store.session_exists(self.state["session_id"]))):
                self._revision += 1
                self.state.update(armed=False, course=None, race_id=None, in_prerace=False)
                self.state.pop("session_id", None)
                self.state.pop("course_evidence", None)
                changed = True
            if changed:
                self._save()

    def _save(self):
        if self.pending_owner is not None:
            self.pending_owner._save()
            return
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def snapshot(self):
        recognition = getattr(self.reader, "recognition_status", {})
        return {**(recognition if isinstance(recognition, dict) else {}), "status": self.status, "message": self.message,
                "pending": json.loads(json.dumps(self.state["pending"])),
                "yolo_active": self.yolo_policy[0],
                "timing_message": self.clock.message, "sample_timing": dict(self.sample_timing)}

    def observe(self, frame, recording_id=None, offset_ms=None):
        """Recognize outside the shared lock; commit state and scores under it.

        One capture worker processes frames in order. Replay callers may invoke
        this directly, without taking the application lock themselves.
        """
        import cv2

        with self.lock:
            if self.stop_event.is_set():
                return
            # Imports share the review queue, not the live phase latch. A
            # reviewed/dismissed candidate must also stop its import owner.
            if (self.state['armed'] and self.state.get('partial_candidate_id')
                    and self._partial_candidate() is None):
                self.state.update(armed=False, course=None)
                self._save()
            revision = self._revision
            yolo, yolo_generation = self.yolo_policy
            session_id = self.fixed_session_id or self.store.active_session()["id"]
        prerace = self.reader.read_prerace(frame)
        is_prerace = bool(prerace) or self.reader.is_prerace_layout(frame)
        if yolo and not self.state["armed"]:
            # After auto-confirmation, scenery resembling a roster must not
            # rearm the same results window. Require a recognized course intro.
            is_prerace = bool(prerace)
        course_evidence = None
        with self.lock:
            needs_course_evidence = prerace and (not self.state["in_prerace"]
                                                or not self.state.get("course_evidence"))
        if needs_course_evidence and not self.stop_event.is_set():
            filename = f"{uuid4()}.png"
            if not cv2.imwrite(str(self.evidence_dir / filename), frame):
                raise OSError("Could not save course evidence")
            course_evidence = {"filename": filename, "offset_ms": offset_ms}
        with self.lock:
            if self.stop_event.is_set() or revision != self._revision:
                if course_evidence:
                    (self.evidence_dir / course_evidence["filename"]).unlink(missing_ok=True)
                return
            if is_prerace:
                new_intro = not self.state['in_prerace'] or not self.state['course']
                if not self.state["in_prerace"]:
                    # Keep ownership even if scores reset before the intro.
                    self.state.update(armed=True, course=None, race_id=None,
                                      in_prerace=True, session_id=session_id, course_evidence=None)
                    self.state.pop('partial_candidate_id', None)
                if prerace:
                    self.state["course"] = prerace["course"]
                if course_evidence:
                    self.state["course_evidence"] = course_evidence
                self._save()
                if prerace and new_intro:
                    self._emit_overlay_event(False, self.state['session_id'])
                return
            if self.state["in_prerace"] and float(frame.mean()) > 25:
                self.state["in_prerace"] = False
                self._save()
            if self.state["armed"] and self.state["course"] and self.state["race_id"] is None:
                # Do not mark the black transition between screens.
                if float(frame.mean()) > 25:
                    race = self.store.create_race(
                        str(uuid4()), self.state["course"], recording_id, offset_ms,
                        folder_id=self.folder_id,
                        session_id=self.state.get("session_id", session_id))
                    self.state["race_id"] = race["id"]
                    if self.state.get("course_evidence"):
                        proof = self.state["course_evidence"]
                        self.store.add_evidence(race["id"], proof["filename"], kind="prerace",
                                                offset_ms=proof["offset_ms"], label="Course and pre-race roster")
                    self._save()
            if not self.state["armed"]:
                return
            previous = self._partial_candidate()
            minimum_rows = len(previous['rows']) if previous else 0
            # Enabling YOLO does not authorize scoring an existing manual draft.
            auto_confirm = yolo and previous is None
        result = self.reader.read(frame, allow_partial=True) if auto_confirm else self.reader.read(frame)
        rows = result.get("rows", [])
        positions = [r.get("position") for r in rows]
        valid_partial = (bool(rows) and all(type(p) is int and 1 <= p <= 12 for p in positions)
                         and len(set(positions)) == len(positions))
        partial = False
        if (result.get("phase") != "finishing_results"
                or (not valid_partial if auto_confirm else positions != list(range(1, 13)))):
            read_partial = getattr(self.reader, 'read_partial', None)
            fallback = (read_partial(frame, minimum_rows=minimum_rows)
                        if not auto_confirm and callable(read_partial) else None)
            if isinstance(fallback, dict) and fallback.get('phase') == 'partial_results':
                partial_rows = fallback.get('rows', [])
                partial_positions = [row.get('position') for row in partial_rows]
                if (len(partial_positions) >= 3 and len(partial_positions) > minimum_rows
                        and all(type(p) is int and 1 <= p <= 12 for p in partial_positions)
                        and partial_positions == sorted(set(partial_positions))):
                    result, rows, partial = fallback, partial_rows, True
            if not partial:
                if result.get("suspected_results"):
                    self._save_suspected_results(frame, revision, offset_ms)
                return
        if self.stop_event.is_set():
            return
        identifier = str(uuid4())
        evidence = self.evidence_dir / f"{identifier}.png"
        if not cv2.imwrite(str(evidence), frame):
            raise OSError("Could not save result evidence")
        with self.lock:
            if (self.stop_event.is_set() or revision != self._revision
                    or (yolo, yolo_generation) != self.yolo_policy
                    or (yolo and not self.fixed_session_id
                        and self.store.active_session()["id"] != session_id)):
                evidence.unlink()
                return
            previous = self._partial_candidate()
            if self.state.get('partial_candidate_id') and previous is None:
                # A shared import review was dismissed/confirmed during OCR.
                evidence.unlink()
                self.state.update(armed=False, course=None)
                self._save()
                return
            if self.state["race_id"] is None:
                # An already-running race has no known intro timestamp.
                race = self.store.create_race(
                    str(uuid4()), "Unknown course (capture started mid-race)",
                    recording_id=recording_id, folder_id=self.folder_id, session_id=session_id)
                self.state["race_id"] = race["id"]
                self._save()  # Persist ownership before the scoring transaction.
            race = self.store.get_race(self.state["race_id"])
            if previous and race['revision']:
                evidence.unlink()
                self.state.update(armed=False, course=None)
                self._save()
                return
            self.store.add_evidence(race["id"], evidence.name, kind="results",
                                    offset_ms=offset_ms, label=("Partial finishing results — review observed rows"
                                                               if partial else "Finishing results"))
            candidate = {"id": previous['id'] if previous else identifier,
                         "race_id": race["id"], "course": race["course"], 'partial': partial,
                         "rows": rows, "reasons": list(result.get("reasons", [])),
                         "screenshot_url": f"/evidence/{identifier}.png"}
            if partial:
                candidate['reasons'].append(
                    f'Partial screenshot: {len(rows)} positions observed. Unknown slots are blank; '
                    'review all 12 identities before confirming. No missing player is inferred.')
            candidate["reasons"].append("Confirm identities against the screenshot before counting points.")
            if race["offset_ms"] is None:
                candidate["reasons"].append("Course-intro recording offset is unknown; add it in the chapter editor.")
            if auto_confirm and race["session_id"] == session_id:
                # Slots keep their proven rank. Never shift sparse detections up;
                # each missing rank/name enrolls a fresh distinct placeholder.
                by_position = {row["position"]: row for row in rows}
                players = [{"name": by_position.get(position, {}).get("name", "").strip()
                            or "not-detected-fixme"} for position in range(1, 13)]
                if not race["revision"]:
                    self.store.review_result(race["id"], players, auto_confirmed=True)
            else:
                # A session switch cannot transfer a latched draft or authorize
                # partial auto-scoring in its old session.
                if auto_confirm:
                    by_position = {row["position"]: row for row in rows}
                    candidate["rows"] = [by_position.get(position, {"position": position, "name": ""})
                                         for position in range(1, 13)]
                if previous:
                    previous.clear()
                    previous.update(candidate)
                else:
                    self.state["pending"].append(candidate)
            if partial:
                self.state['partial_candidate_id'] = candidate['id']
            else:
                self.state.update(armed=False, course=None)
            self._save()
            if previous is None:
                self._emit_overlay_event(True, race['session_id'])

    def _save_suspected_results(self, frame, revision, offset_ms):
        """Keep one manual-entry fallback, without treating it as finishing order."""
        import cv2

        with self.lock:
            if self.stop_event.is_set() or revision != self._revision:
                return
            race_id = self.state["race_id"]
            race = self.store.get_race(race_id) if race_id and self.store.race_exists(race_id) else None
            if (race is None or race["status"] != "pending"
                    or any(item["kind"] in {"results", "suspected_results"} for item in race["evidence"])):
                return
        evidence = self.evidence_dir / f"{uuid4()}.png"
        if not cv2.imwrite(str(evidence), frame):
            raise OSError("Could not save suspected results evidence")
        with self.lock:
            race = self.store.get_race(race_id) if self.store.race_exists(race_id) else None
            if (self.stop_event.is_set() or revision != self._revision
                    or self.state["race_id"] != race_id or race is None or race["status"] != "pending"
                    or any(item["kind"] in {"results", "suspected_results"} for item in race["evidence"])):
                evidence.unlink(missing_ok=True)
                return
            try:
                self.store.add_evidence(
                    race_id, evidence.name, kind="suspected_results", offset_ms=offset_ms,
                    label="Suspected results: finishing order unverified; may be partial or cumulative standings; "
                          "manual verification required")
            except Exception:
                evidence.unlink(missing_ok=True)
                raise

    def review(self, candidate_id, players):
        candidate = self._candidate(candidate_id)
        race = self.store.get_race(candidate["race_id"])
        # Recover safely if a process stopped after the DB commit but before queue cleanup.
        if race["status"] == "scored":
            result = race
        else:
            result = self.store.review_result(candidate["race_id"], players)
        self.state["pending"].remove(candidate)
        if self.state.get('partial_candidate_id') == candidate_id:
            self._revision += 1
            self.state.update(armed=False, course=None)
        self._save()
        return result

    def dismiss(self, candidate_id):
        candidate = self._candidate(candidate_id)
        self.state["pending"].remove(candidate)
        if self.state.get('partial_candidate_id') == candidate_id:
            self._revision += 1
            self.state.update(armed=False, course=None)
        self._save()
        return {"dismissed": candidate_id}

    def _candidate(self, candidate_id):
        for candidate in self.state["pending"]:
            if candidate["id"] == candidate_id:
                return candidate
        raise KeyError("capture candidate not found")

    def start(self, client, source, interval=0.25):
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("capture interval must be a positive finite number")
        self.client = client
        log_event('capture', 'starting')

        def loop():
            while not self.stop_event.is_set():
                try:
                    client.connect()
                    with self.lock:
                        self.status = "loading"
                        self.message = self.loading_message
                    self.reader.warmup()
                    with self.lock:
                        if self.stop_event.is_set():
                            break
                        self.status = "connected"
                        self.message = f"Reading {source}. {self.result_message}"
                    log_event('capture', 'ready')
                    while not self.stop_event.is_set():
                        iteration_started = time.monotonic()
                        before = client.record_status()
                        frame = client.screenshot(source)
                        status = client.record_status()
                        events = client.drain_events()
                        with self.lock:
                            if self.stop_event.is_set():
                                break
                            recording_id, offset = self.clock.update(status, events)
                            if (before["outputActive"] != status["outputActive"]
                                    or before["outputPaused"] != status["outputPaused"]
                                    or before["outputDuration"] > status["outputDuration"]
                                    or any(e.get("eventType") == "RecordFileChanged"
                                           or (e.get("eventType") == "RecordStateChanged"
                                               and e.get("eventData", {}).get("outputState") in {
                                                   "OBS_WEBSOCKET_OUTPUT_STARTED", "OBS_WEBSOCKET_OUTPUT_STOPPED"})
                                           for e in events)):
                                offset = None
                        recognition_started = time.monotonic()
                        self.observe(frame, recording_id, offset)
                        iteration_finished = time.monotonic()
                        elapsed = iteration_finished - iteration_started
                        with self.lock:
                            self.sample_timing = {
                                "sample_count": self.sample_timing.get("sample_count", 0) + 1,
                                "requested_interval_ms": round(interval * 1000, 1),
                                "acquisition_ms": round((recognition_started - iteration_started) * 1000, 1),
                                "recognition_ms": round((iteration_finished - recognition_started) * 1000, 1),
                                "cycle_ms": round(max(interval, elapsed) * 1000, 1),
                            }
                        log_event('capture', 'sample', **self.sample_timing)
                        self.stop_event.wait(max(0, interval - elapsed))
                except Exception as error:
                    if self.stop_event.is_set():
                        break
                    log_event('capture', 'unavailable', error=error)
                    with self.lock:
                        self.status = "error"
                        self.message = f"Capture unavailable: {error}"
                        self.clock.disconnected()
                    client.close()
                    self.stop_event.wait(3)
            client.close()
            log_event('capture', 'stopped')

        self.thread = threading.Thread(target=loop, name="obs-capture", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.client:
            self.client.close()
        if self.reader:
            self.reader.close()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise RuntimeError("Capture is still stopping; database remains open until process exit")
