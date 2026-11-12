"""Explicitly started camera or OBS screenshot capture."""

import os
import re
import stat
import struct
import sys
import threading
import time
from pathlib import Path

from .diagnostics import log_event

try:
    import fcntl
except ImportError:  # Non-POSIX platforms (e.g. native Windows) have no fcntl module.
    fcntl = None

from .imports import NAME_ENGINES

QUERYCAP = 0x80685600
CAPTURE = 0x00000001 | 0x00001000
DEVICE_CAPS = 0x80000000
DEVICE_PATTERN = re.compile(r'/dev/video(?:0|[1-9][0-9]*)\Z')

# Direct camera capture needs the Linux V4L2 backend. Importing this module must
# stay possible on other platforms so manual scoring and OBS WebSocket capture
# work without a camera; an unavailable backend is reported, not simulated.
CAMERA_BACKEND_AVAILABLE = sys.platform == 'linux' and fcntl is not None

BACKEND_UNAVAILABLE_MESSAGE = ('Camera capture requires a Linux V4L2 device; it is unavailable on this platform. '
                                'Use OBS WebSocket capture instead.')


def _failure(stage, error):
    """Expose the failing operation and exception class, never its payload."""
    kind = type(error).__name__
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,60}', kind):
        kind = 'Error'
    return f'{stage} ({kind})'


def _capture_capabilities(fd):
    capability = bytearray(104)
    fcntl.ioctl(fd, QUERYCAP, capability, True)
    _, capabilities, device_caps = struct.unpack_from('III', capability, 80)
    effective = device_caps if capabilities & DEVICE_CAPS else capabilities
    return bool(effective & CAPTURE)


def enumerate_cameras(sys_dir='/sys/class/video4linux', dev_dir='/dev'):
    """QUERYCAP inspects devices without starting a capture stream."""
    if not CAMERA_BACKEND_AVAILABLE:
        return []
    devices = []
    for entry in sorted(Path(sys_dir).glob('video*'), key=lambda path: path.name):
        if not re.fullmatch(r'video(?:0|[1-9][0-9]*)', entry.name):
            continue
        path = Path(dev_dir) / entry.name
        try:
            name = (entry / 'name').read_text().strip() or entry.name
        except OSError:
            name = entry.name
        item = {'id': str(path), 'path': str(path), 'name': name,
                'available': False, 'error': None}
        fd = None
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                raise ValueError('Not a camera character device')
            if not _capture_capabilities(fd):
                continue
            item['available'] = True
        except (OSError, ValueError):
            item['error'] = 'Camera is inaccessible; check permissions and whether OBS virtual camera is running.'
        finally:
            if fd is not None:
                os.close(fd)
        devices.append(item)
    return devices


class _CameraCleanupError(RuntimeError):
    """A constructor may fail before returning its unreleased camera handle."""


class _PinnedCamera:
    """Keep the validated device inode pinned through OpenCV's open."""

    def __init__(self, device):
        import cv2

        self.fd = os.open(device, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.video = None
        try:
            if not stat.S_ISCHR(os.fstat(self.fd).st_mode):
                raise ValueError('Not a camera character device')
            if not _capture_capabilities(self.fd):
                raise ValueError('Selected device is not a video capture input')
            # Opening the pinned descriptor avoids following a substituted path.
            self.video = cv2.VideoCapture(f'/proc/self/fd/{self.fd}', cv2.CAP_V4L2)
            if not self.video.isOpened():
                raise ValueError('Camera could not be opened. Start the OBS virtual camera and check permissions.')
            self.video.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            self.release()
            raise

    def read(self):
        return self.video.read()

    def release(self):
        failure = None
        try:
            if self.video is not None:
                self.video.release()
        except Exception as error:
            failure = error
        finally:
            self.video = None
            try:
                if self.fd is not None:
                    os.close(self.fd)
            except Exception as error:
                failure = failure or error
            finally:
                self.fd = None
        if failure is not None:
            raise _CameraCleanupError(_failure('camera release', failure)) from None


class CameraClient:
    """Drain frames continuously; only its worker opens, reads and releases."""

    def __init__(self, device, *, camera_factory=None, clock=None):
        self.device = device
        self.camera_factory = camera_factory or _PinnedCamera
        self.clock = clock
        self.thread = None
        self.halt = threading.Event()
        self.ready = threading.Event()
        self.frame_lock = threading.Lock()
        self.frame = None
        self.frame_at = None
        self.error = None
        self.clock_connected = False
        self.clock_retry_at = 0
        self.release_failed = False

    def connect(self):
        if self.release_failed:
            raise RuntimeError('Camera resource could not be released; restart the application.')
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Previous camera stream is still stopping')
        self.halt = threading.Event()
        self.ready = threading.Event()
        self.frame = self.frame_at = self.error = None
        self.thread = threading.Thread(target=self._drain, name='camera-frames', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Camera did not become ready; stop capture before retrying')
        if self.error:
            raise RuntimeError(self.error)
        # Camera recognition remains useful if OBS timing is unavailable.
        self.clock_connected = False
        if self.clock:
            try:
                self.clock.connect()
                self.clock_connected = True
            except Exception:
                self.clock.close()
        return self

    def _drain(self):
        camera = None
        stage = 'camera open'
        try:
            camera = self.camera_factory(self.device)
            self.ready.set()
            stage = 'camera read'
            while not self.halt.is_set():
                ok, frame = camera.read()
                if not ok or frame is None:
                    raise RuntimeError('Camera stopped delivering frames')
                with self.frame_lock:
                    self.frame, self.frame_at = frame, time.monotonic()
        except Exception as error:
            if isinstance(error, _CameraCleanupError):
                self.release_failed = True
            self.error = f'Camera unavailable during {_failure(stage, error)}. Check the device and restart capture.'
        finally:
            self.ready.set()
            if camera is not None:
                try:
                    camera.release()
                except Exception as error:
                    self.release_failed = True
                    self.error = f'Camera resource could not be released during {_failure("camera release", error)}. Restart the application.'

    def screenshot(self, _source):
        requested = time.monotonic()
        deadline = requested + 5
        while not self.halt.is_set():
            with self.frame_lock:
                frame, captured_at = self.frame, self.frame_at
                if frame is not None and captured_at >= requested:
                    return frame.copy()
            if self.error:
                raise RuntimeError(self.error)
            if time.monotonic() >= deadline:
                raise RuntimeError('Camera frames are stale or unavailable')
            self.halt.wait(.02)
        raise RuntimeError('Camera capture is stopping')

    def record_status(self):
        if self.clock and not self.clock_connected and time.monotonic() >= self.clock_retry_at:
            self.clock_retry_at = time.monotonic() + 3
            try:
                self.clock.connect()
                self.clock_connected = True
            except Exception:
                self.clock.close()
        if self.clock_connected:
            try:
                return self.clock.record_status()
            except Exception:
                self.clock_connected = False
                self.clock.close()
        return {'outputActive': False, 'outputPaused': False, 'outputDuration': 0,
                'clockUnavailable': True}

    def drain_events(self):
        return self.clock.drain_events() if self.clock_connected else []

    def request_stop(self):
        self.halt.set()

    def close(self):
        self.request_stop()
        if self.clock:
            self.clock.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(.2)



class CameraManager:
    def __init__(self, app, *, enumerator=None, reader_factory=None, client_factory=None):
        self.app = app
        self.enumerator = enumerator or enumerate_cameras
        self.reader_factory = reader_factory
        self.client_factory = client_factory or CameraClient
        self.thread = None
        self.cancel = threading.Event()
        self.client = None
        self.closed = False
        self.cleanup_failed = False
        self.devices = []
        self.status = {'state': 'stopped', 'device_id': None, 'name_engine': None,
                       'capture_mode': 'obs', 'obs_source': None,
                       'message': 'Enter the exact OBS source or scene name, then start capture.'}

    def snapshot(self, refresh=False):
        if refresh:
            devices = self.enumerator()
            with self.app.lock:
                self.devices = devices
        with self.app.lock:
            status = dict(self.status)
            capture = self.app.capture
            status['busy'] = bool((self.thread and self.thread.is_alive())
                                  or (capture.thread and capture.thread.is_alive()))
            if self.thread and self.thread.is_alive() and status['state'] not in {'starting', 'stopping', 'error'}:
                status['state'] = 'error' if capture.status == 'error' else 'running'
                status['message'] = capture.message
            elif not (self.thread and self.thread.is_alive()) and capture.thread and capture.thread.is_alive():
                status.update(state='running', message='CLI capture is running. Stop it before choosing a camera.')
            # In OBS mode the camera backend is irrelevant, so keep the source
            # prompt instead of reporting a Linux-only limitation.
            if (status['state'] == 'stopped' and not CAMERA_BACKEND_AVAILABLE
                    and status['capture_mode'] == 'camera'):
                status['message'] = BACKEND_UNAVAILABLE_MESSAGE
            devices = [dict(device) for device in self.devices]
        return {'devices': devices, 'status': status}

    def start(self, device_id=None, name_engine='auto', use_obs_clock=False,
              obs_url='ws://127.0.0.1:4455', obs_password='',
              capture_mode='camera', obs_source=None, expected_tracker_mode=None):
        if capture_mode not in ('camera', 'obs'):
            raise ValueError('Unknown capture mode')
        if capture_mode == 'camera' and not CAMERA_BACKEND_AVAILABLE:
            raise ValueError(BACKEND_UNAVAILABLE_MESSAGE)
        if capture_mode == 'obs':
            if not isinstance(obs_source, str) or not obs_source.strip() or len(obs_source) > 200:
                raise ValueError('Enter the exact OBS source or scene name, at most 200 characters')
            try:
                obs_source.encode('utf-8')
            except UnicodeError:
                raise ValueError('OBS source name must be valid UTF-8 text') from None
        elif not isinstance(device_id, str) or not DEVICE_PATTERN.fullmatch(device_id):
            raise ValueError('Choose an available /dev/videoN camera')
        if not isinstance(name_engine, str) or name_engine not in NAME_ENGINES:
            raise ValueError('Unknown name recognition engine')
        if type(use_obs_clock) is not bool:
            raise ValueError('OBS recording clock must be enabled or disabled')
        clock = None
        if use_obs_clock or capture_mode == 'obs':
            from .obs import ObsClient
            clock = ObsClient(obs_url, obs_password)
        if capture_mode == 'camera':
            devices = self.enumerator()
            with self.app.lock:
                self.devices = devices
            if not any(device['id'] == device_id and device['available'] for device in devices):
                raise ValueError('Camera is unavailable. Refresh devices after starting the OBS virtual camera.')
        with self.app.lock:
            if expected_tracker_mode is not None:
                self.app.check_tracker_mode(expected_tracker_mode)
            capture = self.app.capture
            if self.closed:
                raise ValueError('Camera capture is shutting down')
            if ((capture.thread and capture.thread.is_alive())
                    or (self.thread and self.thread.is_alive())
                    or (self.app.imports.thread and self.app.imports.thread.is_alive())
                    or getattr(self.app.imports, 'cleanup_failed', False)
                    or self.app.imports.snapshot()['active'] or getattr(self.app, 'active_uploads', set())):
                raise ValueError('Stop capture and finish or cancel imports/uploads before starting capture')
            self.cancel = threading.Event()
            self.status = {'state': 'starting',
                           'device_id': device_id if capture_mode == 'camera' else None,
                           'name_engine': name_engine, 'capture_mode': capture_mode,
                           'obs_source': obs_source if capture_mode == 'obs' else None,
                           'message': 'Opening capture and loading the local recognizer.'}
            reader_factory = self.reader_factory or self.app.imports.reader_factory
            self.thread = threading.Thread(target=self._run, args=(obs_source if capture_mode == 'obs' else device_id,
                                                                 name_engine, clock, capture_mode,
                                                                 capture, reader_factory),
                                           name='camera-capture', daemon=True)
            # Busy throughout setup, recognition and resource shutdown, including
            # a blocked device read or a recognizer factory still loading.
            capture.thread = self.thread
            self.thread.start()
            return dict(self.status)

    def _run(self, device, engine, clock, capture_mode, capture, reader_factory):
        reader, client, loop = None, None, None
        error = None
        is_camera = capture_mode == 'camera'
        label = 'camera' if is_camera else 'OBS'
        stage = 'recognizer setup'
        try:
            reader = reader_factory(engine)
            if self.cancel.is_set():
                return
            stage = f'{label} client setup'
            client = self.client_factory(device, clock=clock) if is_camera else clock
            with self.app.lock:
                self.client = client
                capture.reader = reader
                capture.stop_event = self.cancel
                capture.clock.disconnected()
                capture.clock.message = ('Waiting for OBS recording timing.' if clock else
                                         'No OBS recording clock. Intro offsets are unknown; enter them manually.')
                stage = 'capture startup'
                capture.start(client, device)
                loop = capture.thread
                capture.thread = self.thread
                self.status.update(state='running', message=f'{label} recognition is starting. {capture.result_message}')
            while loop.is_alive() and not self.cancel.wait(.1):
                pass
        except Exception as failure:
            log_event('capture', 'failed', error=failure)
            error = f'Could not start {label} recognition during {_failure(stage, failure)}. Check the capture connection and local OCR dependencies.'
        finally:
            self.cancel.set()
            if client and is_camera:
                client.request_stop()
            if reader:
                try:
                    reader.close()
                except Exception as failure:
                    log_event('capture', 'cleanup_failed', error=failure)
                    self.cleanup_failed = True
                    error = f'Could not close camera recognizer during {_failure("recognizer close", failure)}. Restart the application.'
            if loop:
                loop.join(6)
            # Do not free resources under a blocked read. Remain busy until its
            # owning thread exits; UI stop remains nonblocking in the meantime.
            if client:
                try:
                    client.close()
                except Exception as failure:
                    log_event('capture', 'cleanup_failed', error=failure)
                    self.cleanup_failed = True
                    error = f'{label} is still stopping during {_failure("capture close", failure)}; other capture remains blocked.'
                while is_camera and ((client.thread and client.thread.is_alive()) or client.release_failed or self.cleanup_failed):
                    with self.app.lock:
                        self.status.update(state='stopping', message='Waiting for capture resources to close. Other capture is blocked; restart if this persists.')
                    if client.thread and client.thread.is_alive():
                        client.thread.join(.2)
                    else:
                        time.sleep(.2)
            while self.cleanup_failed:
                with self.app.lock:
                    self.status.update(state='error', message=error)
                time.sleep(.2)
            if loop:
                loop.join()
            with self.app.lock:
                capture.reader = capture.client = None
                capture.status = 'manual'
                capture.message = 'Capture stopped. Saved reviews remain available.'
                self.client = None
                self.status.update(state='error' if error else 'stopped', message=error or f'{label} capture stopped.')

    def stop(self):
        with self.app.lock:
            self.cancel.set()
            capture = self.app.capture
            capture.stop_event.set()
            if self.client and self.status.get('capture_mode') == 'camera':
                self.client.request_stop()
            if self.thread and self.thread.is_alive():
                self.status.update(state='stopping', message='Stopping capture and recognizer.')
            elif capture.thread and capture.thread.is_alive():
                # CLI capture uses the same busy lifetime while closing outside
                # the application lock. The request itself returns promptly.
                self.status.update(state='stopping', message='Stopping CLI capture.')
                original = capture.thread
                self.thread = threading.Thread(target=self._stop_cli, args=(original,),
                                               name='camera-stop-cli', daemon=True)
                capture.thread = self.thread
                self.thread.start()
            else:
                self.status.update(state='stopped', message='Capture stopped.')
            return dict(self.status)

    def _stop_cli(self, original):
        capture = self.app.capture
        try:
            if capture.client:
                capture.client.close()
            if capture.reader:
                capture.reader.close()
            original.join(12)
            while original.is_alive():
                with self.app.lock:
                    self.status.update(state='stopping', message='Waiting for CLI capture to finish. Other capture is blocked.')
                original.join(.2)
            with self.app.lock:
                capture.reader = capture.client = None
                capture.status = 'manual'
                self.status.update(state='stopped', message='Capture stopped.')
        except Exception:
            with self.app.lock:
                self.status.update(state='error', message='Capture could not release resources. Restart the application.')
            # Retain the busy lifetime if cleanup failed; no overlapping OCR.
            while True:
                time.sleep(.2)

    def close(self):
        with self.app.lock:
            self.closed = True
        self.stop()
        if self.thread:
            self.thread.join(12)
            if self.thread.is_alive():
                raise RuntimeError('Camera is still stopping; keep the database open until capture exits')
