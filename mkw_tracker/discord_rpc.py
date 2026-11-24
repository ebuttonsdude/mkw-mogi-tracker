"""Opt-in Discord desktop IPC; never connects from the request thread."""
from copy import deepcopy
import ctypes
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time
import uuid

MAX_FRAME = 65536


class WindowsPipe:
    """Byte-mode PIPE_NOWAIT avoids outstanding or blocking native operations.

    This is synchronous polling on our worker, not overlapped I/O. Microsoft
    documents immediate empty reads and partial byte writes in this mode:
    https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-type-read-and-wait-modes
    """
    def __init__(self, path, api=None):
        self.api = api if api is not None else ctypes.WinDLL('kernel32', use_last_error=True)
        self.handle = None
        signatures = {
            'CreateFileW': ([ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                             ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                             ctypes.c_void_p], ctypes.c_void_p),
            'SetNamedPipeHandleState': ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                                         ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            'ReadFile': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p], ctypes.c_int),
            'WriteFile': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p], ctypes.c_int),
            'CloseHandle': ([ctypes.c_void_p], ctypes.c_int),
            'GetLastError': ([], ctypes.c_uint32),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        handle = self.api.CreateFileW(path, 0xC0000000, 0, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError('Discord pipe unavailable')
        self.handle = handle
        mode = ctypes.c_uint32(1)  # PIPE_READMODE_BYTE | PIPE_NOWAIT
        if not self.api.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            self.close()
            raise OSError('Discord pipe cannot use bounded I/O')

    def recv(self, count):
        buffer = ctypes.create_string_buffer(count)
        deadline = time.monotonic() + .25
        while time.monotonic() < deadline:
            transferred = ctypes.c_uint32()
            success = self.api.ReadFile(self.handle, buffer, count, ctypes.byref(transferred), None)
            if success and transferred.value:
                return buffer.raw[:transferred.value]
            if not success and self.api.GetLastError() not in (232,):  # ERROR_NO_DATA
                raise OSError('Discord pipe disconnected')
            time.sleep(.005)
        raise OSError('Discord pipe read timed out')

    def sendall(self, data):
        deadline = time.monotonic() + .25
        offset = 0
        while offset < len(data) and time.monotonic() < deadline:
            buffer = ctypes.create_string_buffer(data[offset:])
            transferred = ctypes.c_uint32()
            if not self.api.WriteFile(self.handle, buffer, len(data) - offset,
                                      ctypes.byref(transferred), None):
                raise OSError('Discord pipe write failed')
            offset += transferred.value
            if not transferred.value:
                time.sleep(.005)
        if offset != len(data):
            raise OSError('Discord pipe write timed out')

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


class DiscordIPC:
    def __init__(self, application_id):
        self.socket = None
        roots = dict.fromkeys(filter(None, [os.environ.get(key) for key in
                                          ('XDG_RUNTIME_DIR', 'TMPDIR', 'TMP', 'TEMP')] + ['/tmp']))
        try:
            deadline = time.monotonic() + 2
            candidates = ([rf'\\?\pipe\discord-ipc-{index}' for index in range(10)] if os.name == 'nt'
                          else [str(Path(root) / f'discord-ipc-{index}')
                                for root in roots for index in range(10)])
            for path in candidates:
                if time.monotonic() >= deadline:
                    break
                if os.name == 'nt':
                    try:
                        self.socket = WindowsPipe(path)
                    except OSError:
                        continue
                else:
                    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.settimeout(.25)
                    try:
                        connection.connect(path)
                    except OSError:
                        connection.close()
                        continue
                    self.socket = connection
                break
            if self.socket is None:
                raise OSError('Discord desktop is unavailable')
            self.send(0, {'v': 1, 'client_id': application_id})
            reply = self.receive()
            if reply.get('evt') != 'READY':
                raise OSError('Discord handshake failed')
        except BaseException:
            self.close()
            raise

    def send(self, opcode, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(body) > MAX_FRAME:
            raise ValueError('Discord frame too large')
        self.socket.sendall(struct.pack('<II', opcode, len(body)) + body)

    def _read(self, count, deadline):
        result = bytearray()
        while len(result) < count:
            if time.monotonic() >= deadline:
                raise OSError('Discord read timed out')
            part = self.socket.recv(count - len(result))
            if not part:
                raise OSError('Discord disconnected')
            result.extend(part)
        return bytes(result)

    def receive(self, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + 1
        for _ in range(8):
            opcode, length = struct.unpack('<II', self._read(8, deadline))
            if length > MAX_FRAME:
                raise OSError('Discord frame too large')
            body = self._read(length, deadline)
            if opcode == 3:
                self.socket.sendall(struct.pack('<II', 4, length) + body)
                continue
            if opcode != 1:
                raise OSError('Discord closed connection')
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise OSError('Invalid Discord response')
            return payload
        raise OSError('Too many Discord control frames')

    def activity(self, activity):
        nonce = uuid.uuid4().hex
        args = {'pid': os.getpid()}
        if activity is not None:
            args['activity'] = activity
        self.send(1, {'cmd': 'SET_ACTIVITY', 'args': args, 'nonce': nonce})
        deadline = time.monotonic() + 1
        for _ in range(8):
            reply = self.receive(deadline)
            if reply.get('nonce') == nonce:
                if reply.get('evt') == 'ERROR':
                    raise OSError('Discord rejected activity')
                return
        raise OSError('Discord activity acknowledgement missing')

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class DiscordPresence:
    """One coalescing worker, with generation fences and bounded retries."""
    def __init__(self, connector=DiscordIPC, interval=15, retry=15):
        self._connector = connector
        self._interval, self._retry = interval, retry
        self._condition = threading.Condition()
        self._enabled = self._closed = False
        self._application_id = ''
        self._activity = None
        self._generation = 0
        self._worker = None
        self._status = 'Disabled'

    def configure(self, enabled, application_id):
        with self._condition:
            if self._closed:
                return
            if (enabled, application_id) == (self._enabled, self._application_id):
                return
            self._enabled, self._application_id = enabled, application_id
            self._generation += 1
            self._activity = None
            self._status = 'Waiting for Discord desktop' if enabled else 'Disabled'
            if enabled and (self._worker is None or not self._worker.is_alive()):
                self._worker = threading.Thread(target=self._run, name='discord-presence', daemon=True)
                self._worker.start()
            self._condition.notify_all()

    def publish(self, activity):
        with self._condition:
            if not self._closed:
                self._activity = deepcopy(activity)
                self._condition.notify_all()

    def status(self):
        with self._condition:
            return self._status

    def close(self):
        with self._condition:
            self._closed = True
            self._enabled = False
            self._generation += 1
            self._status = 'Disabled'
            self._condition.notify_all()
            worker = self._worker
        if worker:
            worker.join(timeout=15)

    def _run(self):
        connection = None
        generation = -1
        due = 0
        try:
            while True:
                with self._condition:
                    enabled = self._enabled and not self._closed
                    current = self._generation
                    application_id = self._application_id
                    activity = deepcopy(self._activity)
                if generation != current:
                    if connection:
                        try:
                            connection.activity(None)
                        except (OSError, ValueError):
                            pass
                        connection.close()
                        connection = None
                    generation, due = current, 0
                if not enabled:
                    return
                if activity is not None and time.monotonic() >= due:
                    try:
                        if connection is None:
                            connection = self._connector(application_id)
                        with self._condition:
                            if generation != self._generation or self._closed:
                                continue
                            activity = deepcopy(self._activity)
                        if activity is None:
                            continue
                        connection.activity(activity)
                        with self._condition:
                            if generation == self._generation:
                                self._status = 'Connected'
                        due = time.monotonic() + self._interval
                    except (OSError, ValueError):
                        if connection:
                            connection.close()
                            connection = None
                        with self._condition:
                            if generation == self._generation:
                                self._status = 'Waiting for Discord desktop'
                        due = time.monotonic() + self._retry
                with self._condition:
                    if generation == self._generation and not self._closed:
                        self._condition.wait(timeout=max(.01, min(1, due - time.monotonic())) if activity is not None else 1)
        finally:
            if connection:
                try:
                    connection.activity(None)
                except (OSError, ValueError):
                    pass
                connection.close()
            # Serialize worker retirement with configure, so re-enabling cannot
            # mistake a retiring worker for a live publisher.
            with self._condition:
                self._worker = None
                if self._enabled and not self._closed:
                    self._worker = threading.Thread(target=self._run, name='discord-presence', daemon=True)
                    self._worker.start()
