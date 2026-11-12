"""Small synchronous OBS WebSocket v5 client for local capture and recording state."""

import base64
import binascii
import hashlib
import ipaddress
import json
import math
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import cv2
import numpy as np
import websocket

from .diagnostics import log_event


class ObsError(RuntimeError):
    """An OBS connection, protocol, or request error safe to display in the UI."""


class ObsClient:
    def __init__(self, url='ws://127.0.0.1:4455', password='', *,
                 timeout=5.0, connection_factory=None):
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
            valid = (parsed.scheme in ('ws', 'wss') and local
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment
                     and parsed.path in ('', '/') and parsed.port is not None)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError('OBS endpoint must be a loopback WebSocket URL with a port')
        if not isinstance(password, str):
            raise ValueError('OBS password must be text')
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('OBS timeout must be a positive finite number')
        self.url = url
        self._password = password
        self.timeout = timeout
        self._factory = connection_factory or websocket.create_connection
        self._socket = None
        self._events = []
        self._lock = threading.RLock()

    def connect(self):
        with self._lock:
            self.close()
            try:
                log_event('obs', 'connecting')
                self._socket = self._factory(self.url, timeout=self.timeout)
                deadline = time.monotonic() + self.timeout
                op, hello = self._receive(deadline)
                if op != 0 or type(hello.get('rpcVersion')) is not int or hello['rpcVersion'] < 1:
                    raise ObsError('OBS did not send a supported Hello message')
                identify = {'rpcVersion': 1, 'eventSubscriptions': 64}
                if 'authentication' in hello:
                    auth = hello['authentication']
                    if (not isinstance(auth, dict)
                            or not all(isinstance(auth.get(k), str) and auth[k]
                                       for k in ('salt', 'challenge'))):
                        raise ObsError('OBS sent an invalid authentication challenge')
                    secret = base64.b64encode(hashlib.sha256(
                        (self._password + auth['salt']).encode()).digest()).decode()
                    identify['authentication'] = base64.b64encode(hashlib.sha256(
                        (secret + auth['challenge']).encode()).digest()).decode()
                self._send({'op': 1, 'd': identify})
                op, identified = self._receive(deadline)
                if op != 2 or type(identified.get('negotiatedRpcVersion')) is not int or identified['negotiatedRpcVersion'] != 1:
                    raise ObsError('OBS did not confirm protocol version 1')
            except ObsError as error:
                log_event('obs', 'failed', error=error)
                self.close()
                raise
            except Exception as error:
                log_event('obs', 'failed', error=error)
                self.close()
                raise ObsError('Could not connect to OBS; check the local WebSocket server and password') from None
        log_event('obs', 'connected')
        return self

    def close(self):
        with self._lock:
            connection, self._socket = self._socket, None
            self._events.clear()
            if connection is not None:
                log_event('obs', 'closed')
                try:
                    connection.close()
                except Exception:
                    pass

    def _send(self, message):
        if self._socket is None:
            raise ObsError('OBS is not connected')
        try:
            self._socket.settimeout(self.timeout)
            self._socket.send(json.dumps(message))
        except Exception:
            self.close()
            raise ObsError('OBS connection failed while sending a message') from None

    def _receive(self, deadline):
        if self._socket is None:
            raise ObsError('OBS is not connected')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise ObsError('OBS response timed out')
        try:
            self._socket.settimeout(remaining)
            raw = self._socket.recv()
        except Exception:
            self.close()
            raise ObsError('OBS connection closed or response timed out') from None
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            self.close()
            raise ObsError('OBS sent invalid JSON or disconnected') from None
        if (not isinstance(message, dict) or type(message.get('op')) is not int
                or not isinstance(message.get('d'), dict)):
            self.close()
            raise ObsError('OBS sent an invalid protocol message')
        return message['op'], message['d']

    def request(self, request_type, data=None):
        if not isinstance(request_type, str) or not request_type:
            raise ValueError('OBS request type must be nonempty text')
        if data is not None and not isinstance(data, dict):
            raise ValueError('OBS request data must be an object')
        with self._lock:
            request_id = str(uuid4())
            payload = {'requestType': request_type, 'requestId': request_id}
            if data is not None:
                payload['requestData'] = data
            self._send({'op': 6, 'd': payload})
            deadline = time.monotonic() + self.timeout
            while True:
                op, message = self._receive(deadline)
                if op == 5:
                    if (not isinstance(message.get('eventType'), str)
                            or not isinstance(message.get('eventData', {}), dict)):
                        self.close()
                        raise ObsError('OBS sent an invalid event')
                    if message['eventType'] in ('RecordStateChanged', 'RecordFileChanged'):
                        if len(self._events) >= 1024:
                            self.close()
                            raise ObsError('OBS recording event queue overflowed')
                        self._events.append(message)
                    continue
                if (op != 7 or message.get('requestId') != request_id
                        or message.get('requestType') != request_type):
                    self.close()
                    raise ObsError('OBS sent an unexpected response')
                status = message.get('requestStatus')
                if (not isinstance(status, dict) or type(status.get('result')) is not bool
                        or type(status.get('code')) is not int):
                    self.close()
                    raise ObsError('OBS sent an invalid request status')
                if not status['result']:
                    # Do not echo arbitrary server text, which may contain credentials.
                    raise ObsError(f'OBS request failed with code {status["code"]}')
                response = message.get('responseData', {})
                if not isinstance(response, dict):
                    self.close()
                    raise ObsError('OBS sent invalid response data')
                return response

    def drain_events(self):
        """Return recording events collected during requests, then clear the queue."""
        with self._lock:
            events, self._events = self._events, []
            return events

    def screenshot(self, source):
        if not isinstance(source, str) or not source.strip():
            raise ValueError('OBS source name must be nonempty text')
        # Native-resolution JPEG avoids the multi-second PNG transfer observed
        # on live OBS. Keep high quality for small glyphs; evidence is saved as PNG.
        response = self.request('GetSourceScreenshot', {
            'sourceName': source, 'imageFormat': 'jpeg', 'imageCompressionQuality': 95,
        })
        data = response.get('imageData')
        if not isinstance(data, str) or not data.startswith('data:image/jpeg;base64,'):
            raise ObsError('OBS returned an invalid JPEG screenshot')
        try:
            encoded = base64.b64decode(data.split(',', 1)[1], validate=True)
            frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        except (binascii.Error, ValueError, cv2.error):
            raise ObsError('OBS screenshot could not be decoded') from None
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ObsError('OBS screenshot has invalid dimensions')
        return frame

    def record_status(self):
        response = self.request('GetRecordStatus')
        duration = response.get('outputDuration')
        if (type(response.get('outputActive')) is not bool
                or type(response.get('outputPaused')) is not bool
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
                or not isinstance(response.get('outputTimecode'), str)):
            raise ObsError('OBS returned invalid recording status')
        return response
