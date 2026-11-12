"""Background recognition of local recordings; manual review unless explicitly opted into YOLO."""

import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .diagnostics import log_event
from .capture import CaptureCoordinator
from .name_engines import NAME_ENGINES

VIDEO_EXTENSIONS = frozenset({
    '.mkv', '.mp4', '.mov', '.avi', '.webm', '.m4v', '.mpeg', '.mpg', '.ts', '.mts', '.m2ts',
})
TERMINAL = frozenset({'completed', 'cancelled', 'failed', 'interrupted'})
# OpenCV's stable VideoCapture property IDs keep optional cv2 imports lazy.
POSITION_MILLISECONDS, FRAMES_PER_SECOND, FRAME_COUNT = 0, 5, 7


def _now():
    return datetime.now(timezone.utc).isoformat()


def _reader_factory(engine, tessdata_path, external_settings=None):
    from .neural_ocr import make_name_reader
    from .recognition import ResultReader
    if engine == 'external-vision':
        from .external_vision import ExternalVisionReader
        return ResultReader(tessdata_path, name_reader=ExternalVisionReader(external_settings))
    return ResultReader(tessdata_path, name_reader=make_name_reader(engine))


def _video_factory(path):
    import cv2
    return cv2.VideoCapture(str(path))


class ImportManager:
    def __init__(self, app, *, reader_factory=None, video_factory=None, tessdata_path='.local/tessdata',
                 store=None, capture=None, data_dir=None, coordinator_factory=CaptureCoordinator):
        self.app = app
        self.store, self.lock = store if store is not None else app.store, app.lock
        self._capture = capture
        self.coordinator_factory = coordinator_factory
        self.data_dir = Path(data_dir if data_dir is not None else app.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / 'imports.json'
        self.reader_factory = reader_factory or (lambda engine: _reader_factory(
            engine, getattr(app, 'import_tessdata', tessdata_path), getattr(app, 'external_vision', None)))
        self.video_factory = video_factory or _video_factory
        self.jobs = []
        self.thread = None
        self.cancel_event = threading.Event()
        self.reader = None
        self.video = None
        self.cleanup_failed = False
        self.coordinator = None
        self.closed = False
        if self.state_path.exists():
            self.jobs = json.loads(self.state_path.read_text(encoding='utf-8'))
            for job in self.jobs:
                if job['status'] not in TERMINAL:
                    job.update(status='interrupted', error='Application stopped before this import finished.',
                               finished_at=_now())
            self._save()

    @property
    def capture(self):
        return self._capture if self._capture is not None else self.app.capture

    def _save(self):
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.jobs, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(self.state_path)

    def snapshot(self):
        with self.lock:
            if self.reader is not None:
                active = next((job for job in reversed(self.jobs) if job['status'] not in TERMINAL), None)
                if active is not None:
                    self._update_recognition_status(active, self.reader)
            jobs = json.loads(json.dumps(self.jobs))
            return {'active': next((job for job in reversed(jobs) if job['status'] not in TERMINAL), None),
                    'jobs': list(reversed(jobs))}

    def check_available(self, upload_path=None):
        """Check admission while the caller holds the application lock."""
        if self.store is not self.app.store:
            raise ValueError('Tracker mode changed. Refresh before importing a recording.')
        if self.closed:
            raise RuntimeError('Video imports are shutting down.')
        if self.cleanup_failed:
            raise ValueError('Video import resources could not be closed; restart the application.')
        if self.thread and self.thread.is_alive():
            raise ValueError('A video import is already running. Cancel it before starting another.')
        if self.capture.thread and self.capture.thread.is_alive():
            raise ValueError('Stop live capture before importing a recording.')
        uploads = getattr(self.app, 'active_uploads', set())
        if uploads and (upload_path is None or uploads != {upload_path}):
            raise ValueError('A video upload is already running. Wait for it before importing.')
        if upload_path is not None and upload_path not in uploads:
            raise ValueError('The video upload reservation is no longer active.')

    def start(self, path, folder_id=None, name_engine='auto', stride=.5, *, upload_path=None):
        if not isinstance(path, str) or not path.strip() or '\x00' in path or '://' in path:
            raise ValueError('Choose a readable local video file, not a URL or device.')
        try:
            video_path = Path(path).expanduser().resolve(strict=True)
            readable_file = video_path.is_file() and os.access(video_path, os.R_OK)
        except (OSError, RuntimeError, ValueError):
            raise ValueError('The local video file was not found or could not be read.') from None
        if not readable_file or video_path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError('Choose a readable regular video file with a supported extension.')
        if (type(stride) not in (int, float) or not math.isfinite(stride)
                or not .1 <= stride <= 10):
            raise ValueError('Sampling interval must be between 0.1 and 10 seconds.')
        if not isinstance(name_engine, str) or name_engine not in NAME_ENGINES:
            raise ValueError('Unknown name recognition engine.')
        with self.lock:
            self.check_available(upload_path)
            if upload_path is not None and video_path != upload_path:
                raise ValueError('The video upload reservation does not match the imported file.')
            session = self.store.active_session()
            folder_id = session['folder_id'] if folder_id is None else folder_id
            try:
                folder = self.store.get_folder(folder_id)
            except KeyError:
                raise ValueError('Choose an existing history folder.') from None
            # Keep the original full path on the job while respecting the
            # recording label's 200-character database contract.
            recording = self.store.create_recording(('Video import: ' + video_path.name)[:200])
            job = {'id': str(uuid4()), 'status': 'queued', 'path': str(video_path),
                   'folder_id': folder_id, 'session_id': folder['session_id'], 'recording_id': recording['id'],
                   'name_engine': name_engine, 'resolved_name_engine': None,
                   'name_engine_status': 'selecting', 'stride': stride, 'progress_seconds': 0,
                   'duration_seconds': None, 'percent': 0, 'observed_races': 0,
                   'observed_results': 0, 'error': None, 'created_at': _now(), 'finished_at': None}
            self.jobs.append(job)
            self.cancel_event = threading.Event()
            self._save()
            self.thread = threading.Thread(target=self._run, args=(job, self.cancel_event),
                                           name='video-import', daemon=True)
            self.thread.start()
            return json.loads(json.dumps(job))

    def prune_deleted_races(self):
        with self.lock:
            if self.coordinator is not None:
                self.coordinator.prune_deleted_races()

    @staticmethod
    def _update_recognition_status(job, reader):
        status = getattr(reader, 'recognition_status', {})
        if isinstance(status, dict):
            job.update({key: value for key, value in status.items() if key != 'name_engine'})
            job['resolved_name_engine'] = status.get('name_engine')

    def _run(self, job, cancelled):
        video = reader = coordinator = decoder = None
        outcome, error_message, cleanup_failed = 'completed', None, False
        try:
            with self.lock:
                job['status'] = 'running'
                log_event('imports', 'started')
                self._save()
            decoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix='video-decode')
            video = decoder.submit(self.video_factory, job['path']).result()
            with self.lock:
                self.video = video
            if not decoder.submit(video.isOpened).result():
                raise ValueError('The video could not be opened. Check the file and installed codecs.')
            fps, count = decoder.submit(
                lambda: (video.get(FRAMES_PER_SECOND), video.get(FRAME_COUNT))).result()
            if (not all(type(value) in (int, float) and math.isfinite(value) and value > 0
                        for value in (fps, count))):
                raise ValueError('The video does not have a usable finite duration.')
            duration = count / fps
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('The video does not have a usable finite duration.')
            with self.lock:
                job['duration_seconds'] = duration
                self._save()
            if not cancelled.is_set():
                reader = self.reader_factory(job['name_engine'])
                with self.lock:
                    self.reader = reader
                    log_event('imports', 'reader_ready')
                    self._update_recognition_status(job, reader)
                    self._save()
                if cancelled.is_set():
                    reader.close()
                else:
                    coordinator = self.coordinator_factory(
                        self.store, self.lock, self.data_dir, reader, session_id=job['session_id'],
                        folder_id=job['folder_id'], pending_owner=self.capture)
                    coordinator.stop_event = cancelled
                    with self.lock:
                        self.coordinator = coordinator
            # Decode at most one sample ahead while OCR handles the current
            # frame. One worker owns seeking/decoding; recognition and commits
            # remain chronological and use only one model instance.
            def decode(offset):
                if cancelled.is_set():
                    return None
                if video.set(POSITION_MILLISECONDS, offset * 1000) is False:
                    raise ValueError('The video cannot seek to the requested timestamp.')
                ok, frame = video.read()
                if not ok:
                    raise ValueError(f'The video could not be decoded at {offset:.1f} seconds.')
                return frame

            sample_index, offset = 0, 0.0
            if coordinator is not None:
                next_frame = decoder.submit(decode, offset)
            while coordinator is not None and offset < duration and not cancelled.is_set():
                frame = next_frame.result()
                if cancelled.is_set():
                    break
                following_offset = (sample_index + 1) * job['stride']
                if following_offset < duration:
                    next_frame = decoder.submit(decode, following_offset)
                was_armed = coordinator.state['armed']
                coordinator.observe(frame, job['recording_id'], round(offset * 1000))
                with self.lock:
                    self._update_recognition_status(job, reader)
                    if (was_armed and not coordinator.state['armed']
                            and coordinator.state['race_id'] is not None):
                        job['observed_results'] += 1
                    job['observed_races'] = self.store.count_recording_races(job['recording_id'])
                    job['progress_seconds'] = min(offset + job['stride'], duration)
                    job['percent'] = min(100, job['progress_seconds'] / duration * 100)
                    self._save()
                log_event('imports', 'progress', percent=round(job['percent'], 1),
                          observed_races=job['observed_races'], observed_results=job['observed_results'])
                sample_index += 1
                offset = sample_index * job['stride']
        except Exception as error:
            log_event('imports', 'failed', error=error)
            outcome, error_message = 'failed', str(error)
        finally:
            # Reader shutdown interrupts an outstanding neural worker. Never hold
            # the application lock while waiting for model processes to stop.
            try:
                if reader is not None:
                    reader.close()
            except Exception as error:
                outcome, error_message = 'failed', f'Could not close video recognizer: {error}. Restart the application.'
                log_event('imports', 'cleanup_failed', error=error)
                cleanup_failed = True
            try:
                if video is not None:
                    decoder.submit(video.release).result()
            except Exception as error:
                outcome, error_message = 'failed', f'Could not close video file: {error}. Restart the application.'
                log_event('imports', 'cleanup_failed', error=error)
                cleanup_failed = True
            finally:
                if decoder is not None:
                    decoder.shutdown(wait=True, cancel_futures=True)
                with self.lock:
                    self.cleanup_failed = cleanup_failed
                    if not cleanup_failed:
                        self.reader = self.video = self.coordinator = None
                    if cancelled.is_set() and not cleanup_failed:
                        outcome, error_message = 'cancelled', None
                    # Publish cleanup's outcome atomically. Failed cleanup keeps
                    # ownership even though the job itself has stopped running.
                    job.update(status=outcome, error=error_message, finished_at=_now())
                    self._save()
                    if outcome in ('completed', 'cancelled'):
                        log_event('imports', outcome)

    def cancel(self, job_id):
        with self.lock:
            job = next((job for job in self.jobs if job['id'] == job_id), None)
            if job is None:
                raise KeyError('Video import not found.')
            if job['status'] in TERMINAL:
                return json.loads(json.dumps(job))
            self.cancel_event.set()
            reader = self.reader
        if reader is not None:
            reader.close()
        with self.lock:
            return json.loads(json.dumps(job))

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
            reader, thread = self.reader, self.thread
        if reader is not None:
            try:
                reader.close()
            except Exception as error:
                raise RuntimeError('Video import recognizer could not be closed; restart the application.') from error
        if thread is not None:
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError('Video import is still stopping; the database must remain open.')
        if self.cleanup_failed:
            raise RuntimeError('Video import resources could not be closed; restart the application.')
