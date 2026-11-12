"""Automatic, single-player WW observations, separate from Mogi confirmation."""

from pathlib import Path
from uuid import uuid4

from .capture import CaptureCoordinator


class WWCaptureCoordinator(CaptureCoordinator):
    result_message = 'Stable WW results update automatically. Unknown values remain editable.'
    loading_message = 'Starting WW digit recognition. No player-name model is loaded.'

    def __init__(self, store, lock, data_dir, reader, **kwargs):
        super().__init__(store, lock, data_dir, reader, **kwargs)
        # Only the JSON latch is mode-specific. UUID evidence has one public URL.
        self.evidence_dir = Path(data_dir).parent / 'evidence'
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        saw_results = self.state.get('in_results', not self.state['armed'])
        self._clear_observations()
        self._saw_result_window = self.state['in_results'] = saw_results

    def _clear_observations(self):
        self._change_key = self._final_key = None
        self._target_key, self._target_count = None, 0
        self._change_count = self._final_count = self._away_count = 0
        self._before = None
        self._saw_result_window = False
        self.state['in_results'] = False

    def observe(self, frame, recording_id=None, offset_ms=None):
        import cv2

        with self.lock:
            if self.stop_event.is_set():
                return
            revision = self._revision
            session_id = self.fixed_session_id or self.store.active_session()['id']
            continuing_prerace = self.state['in_prerace']
        prerace = self.reader.read_prerace(frame)
        # Roster-like scenery is not enough to abandon a known course. A
        # recognized course enters the phase; layout-only frames can retain it.
        is_prerace = bool(prerace) or (continuing_prerace and self.reader.is_prerace_layout(frame))
        proof = None
        with self.lock:
            needs_proof = prerace and (not self.state['in_prerace'] or not self.state.get('course_evidence'))
        if needs_proof:
            proof = {'filename': f'{uuid4()}.png', 'offset_ms': offset_ms}
            if not cv2.imwrite(str(self.evidence_dir / proof['filename']), frame):
                raise OSError('Could not save WW course evidence')
        with self.lock:
            if self.stop_event.is_set() or revision != self._revision:
                if proof:
                    (self.evidence_dir / proof['filename']).unlink(missing_ok=True)
                return
            if is_prerace:
                new_intro = not self.state['in_prerace'] or not self.state['course']
                if not self.state['in_prerace']:
                    self._clear_observations()
                    self.state.update(armed=True, course=None, race_id=None, in_prerace=True,
                                      session_id=session_id, course_evidence=None, lobby_size=None)
                if prerace:
                    self.state['course'] = prerace['course']
                    self.state['lobby_size'] = prerace.get('lobby_size')
                if proof:
                    self.state['course_evidence'] = proof
                self._save()
                if prerace and new_intro:
                    self._emit_overlay_event(False, self.state['session_id'])
                return
            if self.state['in_prerace']:
                if float(frame.mean()) <= 25:
                    return
                self.state['in_prerace'] = False
                if self.state['course']:
                    race = self.store.create_race(
                        str(uuid4()), self.state['course'], recording_id, offset_ms,
                        folder_id=self.folder_id, session_id=self.state['session_id'])
                    self.state['race_id'] = race['id']
                    if self.state.get('course_evidence'):
                        evidence = self.state['course_evidence']
                        self.store.add_evidence(race['id'], evidence['filename'], kind='prerace',
                                                offset_ms=evidence['offset_ms'], label='WW course and roster')
                self._save()
        result = self.reader.read_results(frame)
        table = bool(result) or self.reader.is_results_layout(frame)
        with self.lock:
            if self.stop_event.is_set() or revision != self._revision:
                return
            # A cut can end a result window before its final rating was read.
            # Keep that race pending, but never carry its delta into a later race.
            # Scrolling the highlighted row off-screen is NOT a race boundary.
            if table:
                self._away_count = 0
                if result is not None and not self._saw_result_window:
                    self.state['in_results'] = True
                    self._save()
                    self._saw_result_window = True
            elif self._saw_result_window:
                self._away_count += 1
                if self._away_count >= 5:
                    self.state.update(armed=True, course=None, race_id=None, session_id=session_id,
                                      course_evidence=None, lobby_size=None)
                    self._clear_observations()
                    self._save()
                    return
            if not self.state['armed']:
                return
            if result is None:
                self._final_key, self._final_count = None, 0
            elif result['phase'] == 'change':
                self._final_key, self._final_count = None, 0
                key = (result['position'], result['vr'], result['delta'])
                self._change_count = self._change_count + 1 if key == self._change_key else 1
                self._change_key = key
                target = (result['position'], result['vr'] + result['delta'])
                self._target_count = self._target_count + 1 if target == self._target_key else 1
                self._target_key = target
                # Keep the first stable pair, never a later animation remainder.
                if self._change_count >= 2 and self._before is None:
                    self._before = key
                self.state.setdefault('session_id', session_id)
                return
            else:
                key = (result['position'], result['vr'])
                self._final_count = self._final_count + 1 if key == self._final_key else 1
                self._final_key = key
                self.state.setdefault('session_id', session_id)
                # A brief final table can be corroborated by repeated observed
                # rating + remaining-change sums from THIS result window. This
                # never substitutes an animated rating for a settled final read.
                corroborated = key == self._target_key and self._target_count >= 2
                if self._final_count < 2 and not corroborated:
                    return
            if result is None:
                save_suspected = table
            else:
                save_suspected = False
                if self.state['race_id'] is None:
                    race = self.store.create_race(
                        str(uuid4()), 'Unknown course (capture started mid-race)', recording_id=recording_id,
                        folder_id=self.folder_id, session_id=self.state.get('session_id', session_id))
                    self.state['race_id'] = race['id']
                race = self.store._race(self.state['race_id'])
                # A manual result entered during recognition wins.
                if race['revision']:
                    self.state['armed'] = False
                    self._save()
                    self._emit_overlay_event(True, race['session_id'])
                    return
                expected_revision = race['revision']
        if result is None:
            if save_suspected:
                self._save_suspected_results(frame, revision, offset_ms)
            return
        filename = f'{uuid4()}.png'
        evidence_path = self.evidence_dir / filename
        if not cv2.imwrite(str(evidence_path), frame):
            raise OSError('Could not save WW result evidence')
        with self.lock:
            if self.stop_event.is_set() or revision != self._revision:
                evidence_path.unlink(missing_ok=True)
                return
            current = self.store._race(race['id'])
            if current['revision'] != expected_revision:
                evidence_path.unlink(missing_ok=True)
                self.state['armed'] = False
                self._save()
                self._emit_overlay_event(True, current['session_id'])
                return
            before = delta = None
            if (self._before and self._before[0] == result['position']
                    and self._before[2] is not None
                    and self._before[1] + self._before[2] == result['vr']):
                _, before, delta = self._before
            lobby_size = result.get('lobby_size') or self.state.get('lobby_size')
            if lobby_size is not None and lobby_size < result['position']:
                lobby_size = None
            self.store.set_ww_result(race['id'], position=result['position'], vr_after=result['vr'],
                                     vr_before=before, delta=delta, lobby_size=lobby_size,
                                     source='import' if self.fixed_session_id else 'capture',
                                     derive_from_previous=self.fixed_session_id is None,
                                     expected_revision=expected_revision)
            self.store.add_evidence(race['id'], filename, kind='results', offset_ms=offset_ms,
                                    label='Observed WW final VR and finishing position')
            self.state['armed'] = False
            self._away_count = 0
            self._save()
            self._emit_overlay_event(True, race['session_id'])
