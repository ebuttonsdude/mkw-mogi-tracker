"""Bounded JSONL subprocess transport for optional local name recognition."""
from __future__ import annotations

import base64
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .diagnostics import log_event
from .name_engines import GGUF_PROFILE, QWEN_PROFILES

_DEFAULT_TIMEOUT = object()


def venv_executable(environment, name):
    """Resolve an interpreter or console script inside a project-local environment."""
    if sys.platform == 'win32':
        return Path(environment) / 'Scripts' / f'{name}.exe'
    return Path(environment) / 'bin' / name


class _WindowsWorkerJob:
    """Contain the venv redirector and all interpreters before any worker code runs."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('minimum_ws', ctypes.c_size_t),
                        ('maximum_ws', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64 * 4), ('faults', wintypes.DWORD),
                        ('total', wintypes.DWORD), ('active', wintypes.DWORD), ('terminated', wintypes.DWORD)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                        ('thread_id', wintypes.DWORD), ('process_id', wintypes.DWORD),
                        ('priority', wintypes.LONG), ('delta', wintypes.LONG), ('flags', wintypes.DWORD)]

        self.ctypes = ctypes
        self.accounting_type = Accounting
        self.thread_type = ThreadEntry
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, arguments, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ('QueryInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ('CreateToolhelp32Snapshot', [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            ('Thread32First', [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            ('Thread32Next', [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            ('OpenThread', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ('ResumeThread', [wintypes.HANDLE], wintypes.DWORD),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise error

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def resume(self, process):
        # Popen closes the primary thread handle. Find that still-suspended thread
        # using documented Win32 APIs instead of resuming before job assignment.
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            entry = self.thread_type()
            entry.size = self.ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, self.ctypes.byref(entry))
            while found:
                if entry.process_id == process.pid:
                    thread = self.kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise self.ctypes.WinError(self.ctypes.get_last_error())
                    try:
                        if self.kernel.ResumeThread(thread) == 0xffffffff:
                            raise self.ctypes.WinError(self.ctypes.get_last_error())
                    finally:
                        self.kernel.CloseHandle(thread)
                    return
                found = self.kernel.Thread32Next(snapshot, self.ctypes.byref(entry))
            raise RuntimeError('Cannot find suspended OCR worker thread')
        finally:
            self.kernel.CloseHandle(snapshot)

    def close(self):
        if not self.handle:
            return
        try:
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            deadline = time.monotonic() + 2
            accounting = self.accounting_type()
            while True:
                if not self.kernel.QueryInformationJobObject(
                        self.handle, 1, self.ctypes.byref(accounting), self.ctypes.sizeof(accounting), None):
                    raise self.ctypes.WinError(self.ctypes.get_last_error())
                if not accounting.active:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('OCR worker process tree did not exit')
                time.sleep(.01)
        finally:
            # KILL_ON_JOB_CLOSE is also the fallback if terminate/query fails.
            if not self.kernel.CloseHandle(self.handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            self.handle = None


class _WorkerTransport:
    """One pipe owner per process; only this thread can block on worker I/O."""

    def __init__(self, process):
        self.process = process
        self.requests = queue.Queue(maxsize=1)
        self.responses = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='ocr-worker-io', daemon=True)
        self.thread.start()

    def _publish(self, value):
        while not self.stopped.is_set():
            try:
                self.responses.put(value, timeout=.05)
                return
            except queue.Full:
                pass

    def _run(self):
        try:
            while not self.stopped.is_set():
                try:
                    payload = self.requests.get(timeout=.05)
                except queue.Empty:
                    continue
                offset = 0
                while offset < len(payload) and not self.stopped.is_set():
                    count = self.process.stdin.write(payload[offset:offset + 65536])
                    if not count:
                        raise RuntimeError('OCR worker stopped accepting requests')
                    offset += count
                while not self.stopped.is_set():
                    data = self.process.stdout.read(65536)
                    if not data:
                        raise RuntimeError('OCR worker exited before responding')
                    self._publish(data)
                    if b'\n' in data:
                        break
        except Exception as exc:
            self._publish(exc)


def make_name_reader(engine='auto'):
    if engine == 'tesseract':
        return None
    root = Path.cwd()
    from .reader_setup import read
    removed = read(root)['removed']
    if engine in removed:
        raise ValueError('Reader removed; reinstall it through setup')
    if engine == 'auto':
        gpu_ready = False
        if 'qwen' not in removed and venv_executable(root / '.local' / 'vlm-venv', 'python').is_file() \
                and (root / '.local/models/qwen3-vl-8b/config.json').is_file():
            try:
                capacity = subprocess.run(
                    ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=3, check=True)
                gpu_ready = int(capacity.stdout.splitlines()[0]) >= 22528
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
        engine = ('qwen' if gpu_ready else 'paddle'
                  if 'paddle' not in removed and venv_executable(root / '.local' / 'paddle-venv', 'python').is_file()
                  and (root / '.local/models/paddle-ppocrv5/inference.json').is_file()
                  else 'tesseract')
    if engine == 'tesseract':
        return None
    return NeuralNameReader(engine)


class NeuralNameReader:
    def __init__(self, engine='paddle', python_path=None, model_dir=None, timeout=_DEFAULT_TIMEOUT):
        if engine not in {'paddle', GGUF_PROFILE['engine']} and engine not in QWEN_PROFILES:
            raise ValueError('Unknown neural OCR engine')
        if timeout is _DEFAULT_TIMEOUT:
            timeout = 300 if engine == GGUF_PROFILE['engine'] else 120
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('OCR timeout must be a positive finite number')
        root = Path.cwd()
        from .reader_setup import read
        if engine in read(root)['removed']:
            raise ValueError('Reader removed; reinstall it through setup')
        self.engine = engine
        profile = QWEN_PROFILES.get(engine)
        if engine == GGUF_PROFILE['engine']:
            interpreter = sys.executable
            model_name = GGUF_PROFILE['model_dir']
            script = 'gguf_worker.py'
        else:
            environment = 'paddle-venv' if engine == 'paddle' else profile['environment']
            interpreter = venv_executable(root / '.local' / environment, 'python')
            model_name = 'paddle-ppocrv5' if engine == 'paddle' else profile['model_dir']
            script = 'paddle_worker.py' if engine == 'paddle' else 'qwen_worker.py'
        self.python_path = Path(python_path or interpreter)
        self.model_dir = Path(model_dir or root / '.local/models' / model_name)
        self.timeout = float(timeout)
        self._script = Path(__file__).parent / 'workers' / script
        self._process = None
        self._posix_owner = None
        self._cleanup_pending = False
        self._transport = None
        self._log = None
        self._job = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def _posix_status(self):
        """Check our unreaped child without releasing its numeric identity.

        All callers hold _lock. Never coerce a process PID: mocked or invalid
        identities must not become killpg(0), killpg(1), or a caller-group signal.
        """
        process = self._process
        owner = self._posix_owner
        if process is None or owner is None or owner[0] is not process:
            return 'unowned'
        pid = owner[1]
        if (type(pid) is not int or pid <= 1 or type(process.pid) is not int
                or process.pid != pid or pid in (os.getpid(), os.getpgrp())):
            return 'unowned'
        if process.returncode is not None:
            return 'reaped'
        try:
            status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if os.getpgid(pid) != pid or os.getsid(pid) != pid:
                return 'unowned'
        except (ChildProcessError, ProcessLookupError):
            return 'reaped'
        except (AttributeError, OSError):
            return 'unowned'
        if status is not None and status.si_pid != pid:
            return 'unowned'
        return 'running' if status is None else 'exited'

    def _start(self):
        if self._process is not None:
            if sys.platform == 'win32':
                running = self._process.poll() is None
            else:
                running = self._posix_status() == 'running'
            if running and not self._cleanup_pending:
                return
        self._close()
        if sys.platform != 'win32' and any(
                not hasattr(os, name) for name in
                ('waitid', 'P_PID', 'WEXITED', 'WNOHANG', 'WNOWAIT', 'getpgid', 'getsid')):
            raise RuntimeError('Neural OCR workers require POSIX waitid/WNOWAIT support or Windows; '
                               'use Tesseract on this runtime')
        if self.engine in QWEN_PROFILES and not self.python_path.is_file():
            raise RuntimeError(f'{QWEN_PROFILES[self.engine]["label"]} environment is missing; '
                               f'run tools/setup_neural.py --engine {self.engine}')
        # Third-party stderr can contain paths, credentials and image payloads.
        # Keep the existing owned-file cleanup, but never persist those bytes.
        self._log = open(os.devnull, 'ab', buffering=0)
        log_event('recognition', 'starting')
        try:
            options = {}
            if sys.platform == 'win32':
                self._job = _WindowsWorkerJob()
                options['creationflags'] = 0x00000004  # CREATE_SUSPENDED
            else:
                options['start_new_session'] = True
            command = [str(self.python_path), str(self._script), '--model-dir', str(self.model_dir)]
            if self.engine in QWEN_PROFILES and self.engine != 'qwen':
                command += ['--profile', self.engine]
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, bufsize=0, **options,
            )
            if sys.platform != 'win32':
                self._posix_owner = (self._process, self._process.pid)
                if self._posix_status() == 'unowned':
                    raise RuntimeError('Cannot establish OCR worker process ownership')
            if self._job is not None:
                self._job.assign(self._process)
            if self._closed.is_set():
                raise RuntimeError('OCR reader is closed')
            if self._job is not None:
                self._job.resume(self._process)
            self._transport = _WorkerTransport(self._process)
            log_event('recognition', 'started')
        except Exception:
            self._close()
            raise

    def _exchange(self, payload):
        deadline = time.monotonic() + self.timeout
        response = bytearray()
        self._transport.requests.put_nowait(payload)
        while True:
            if self._closed.is_set():
                raise RuntimeError('OCR reader is closed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'{self.engine} OCR worker timed out after {self.timeout:g}s')
            try:
                data = self._transport.responses.get(timeout=min(remaining, .05))
            except queue.Empty:
                continue
            if isinstance(data, Exception):
                raise RuntimeError(f'{self.engine} OCR worker: {data}') from data
            response.extend(data)
            if len(response) > 1024 * 1024:
                raise RuntimeError(f'{self.engine} OCR response exceeds size limit')
            if b'\n' in response:
                line, extra = response.split(b'\n', 1)
                if extra.strip():
                    raise RuntimeError(f'{self.engine} OCR worker returned extra response data')
                return json.loads(line.decode('utf-8'))

    def recognize(self, images):
        import cv2

        if not isinstance(images, (list, tuple)) or not 1 <= len(images) <= 12:
            raise ValueError('Name recognition requires between 1 and 12 BGR row images')
        encoded = []
        for image in images:
            if (not hasattr(image, 'ndim') or image.ndim != 3 or image.shape[2] != 3
                    or image.shape[0] == 0 or image.shape[1] == 0
                    or image.dtype.name != 'uint8'):
                raise ValueError('Name recognition requires nonempty uint8 BGR images')
            success, png = cv2.imencode('.png', image)
            if not success or png.nbytes >= 4 * 1024 * 1024:
                raise ValueError('Each encoded row image must be smaller than 4 MiB')
            encoded.append(base64.b64encode(png).decode('ascii'))
        payload = (json.dumps({'images': encoded}, ensure_ascii=True) + '\n').encode('utf-8')
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('OCR reader is closed')
            try:
                self._start()
                response = self._exchange(payload)
                if not isinstance(response, dict):
                    raise ValueError('OCR response must be an object')
                if 'error' in response:
                    raise RuntimeError(f'{self.engine} OCR worker: {response["error"]}')
                rows = response.get('rows')
                if not isinstance(rows, list) or len(rows) != len(images):
                    raise ValueError('OCR response has an unexpected number of rows')
                result = []
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip():
                        raise ValueError('OCR response contains an invalid or empty name')
                    confidence = row.get('confidence')
                    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                        raise ValueError('OCR response contains an invalid confidence')
                    result.append({'name': row['name'].strip(), 'confidence': float(confidence)})
                log_event('recognition', 'result', row_count=len(result))
                return result
            except Exception as error:
                log_event('recognition', 'failed', error=error)
                self._close()
                raise

    def _close(self):
        self._cleanup_pending = True
        errors = []

        def attempt(action):
            try:
                action()
            except Exception as error:
                errors.append(error)

        # A Windows venv executable may only be a redirector. Reap its entire job
        # even when the direct Popen process already exited, before closing pipes.
        if self._job is not None:
            attempt(self._job.close)
            if self._job.handle is None:
                self._job = None
        process = self._process
        transport = self._transport
        if process is not None and sys.platform != 'win32' and self._posix_status() == 'unowned':
            # Keep the resource record and block reuse. Unverified ownership is
            # not evidence of exit, and must never trigger fallback signalling.
            raise RuntimeError('OCR worker ownership could not be verified; cleanup remains pending')
        if transport is not None:
            transport.stopped.set()
        if process is not None:
            if sys.platform != 'win32':
                # waitid(WNOWAIT) and the ownership lock keep the session leader
                # unreaped until the one group signal has completed.
                status = self._posix_status()
                if status in ('running', 'exited'):
                    try:
                        os.killpg(self._posix_owner[1], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError as error:
                        errors.append(error)
                    attempt(lambda: process.wait(timeout=2))
                    if process.returncode is not None:
                        self._process = None
                        self._posix_owner = None
                elif status == 'reaped':
                    self._process = None
                    self._posix_owner = None
                else:
                    raise RuntimeError('OCR worker ownership could not be verified; cleanup remains pending')
            else:
                def reap():
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        except Exception as error:
                            errors.append(error)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        attempt(process.kill)
                        process.wait(timeout=2)

                attempt(reap)
                if process.poll() is not None:
                    attempt(process._handle.Close)
                    self._process = None
        if transport is not None:
            attempt(lambda: transport.thread.join(timeout=2))
            if transport.thread.is_alive():
                errors.append(RuntimeError('OCR pipe owner did not exit'))
            else:
                self._transport = None
        if process is not None:
            if process.stdin:
                attempt(process.stdin.close)
            if process.stdout:
                attempt(process.stdout.close)
        if self._log is not None:
            attempt(self._log.close)
            if self._log.closed:
                self._log = None
        if errors:
            log_event('recognition', 'cleanup_failed', error=errors[0])
            raise errors[0]
        if process is not None:
            log_event('recognition', 'stopped')
        self._cleanup_pending = False

    def close(self):
        self._closed.set()
        # Interrupt a model load/inference instead of waiting for its request timeout.
        if self._lock.acquire(blocking=False):
            try:
                self._close()
            finally:
                self._lock.release()
            return
        # _exchange observes _closed within 50ms. Do not use terminate here:
        # Popen may reap the leader before the lock owner tears down its group.
        with self._lock:
            self._close()
