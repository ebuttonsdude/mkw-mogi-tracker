"""Opt-in crop-only vision transport. No processes, proxies or redirects."""

import base64
import http.client
import ipaddress
import json
import secrets
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .diagnostics import log_event

MAX_PNG_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
REQUEST_TIMEOUT = 30
ATTEMPT_TIMEOUT = 300
PROMPT = 'Transcribe only the player name in this crop. Preserve Unicode and symbols. Return only the name.'


def _lan(address):
    networks = (('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16') if address.version == 4
                else ('fc00::/7',))
    return any(address in ipaddress.ip_network(network) for network in networks)


def _safe_address(address, *, allow_loopback=False):
    if (address.is_unspecified or address.is_multicast or address.is_link_local
            or getattr(address, 'ipv4_mapped', None) or getattr(address, 'sixtofour', None)
            or getattr(address, 'teredo', None)):
        return False
    if address.is_loopback:
        return allow_loopback
    return not address.is_reserved and (address.is_global or _lan(address))


def destination(url):
    """Validate and normalize a base URL without resolving or contacting it."""
    if (not isinstance(url, str) or not 1 <= len(url) <= 2048
            or any(ord(c) <= 32 or ord(c) >= 127 for c in url)):
        raise ValueError('Use a bounded HTTP(S) base or chat-completions URL.')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme not in ('http', 'https') or not host or parsed.username is not None
                or parsed.password is not None or '?' in url or '#' in url
                or '%' in url or '\\' in url or parsed.port == 0):
            raise ValueError
        path = parsed.path.rstrip('/')
        if not path:
            path = '/v1/chat/completions'
        elif path.endswith('/v1'):
            path += '/chat/completions'
        elif not path.endswith('/chat/completions'):
            raise ValueError
        parsed = parsed._replace(path=path)
        try:
            address = ipaddress.ip_address('127.0.0.1' if host == 'localhost' else host)
        except ValueError:
            # Avoid alternative numeric spellings interpreted by OS resolvers.
            if (parsed.scheme != 'https' or len(host) > 253 or not re.search('[a-zA-Z]', host)
                    or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                           for label in host.split('.'))):
                raise ValueError
            return parsed, host, False  # Every DNS name requires consent, even a trusted LAN name.
        if not _safe_address(address, allow_loopback=True):
            raise ValueError
        if parsed.scheme == 'http' and not (address.is_loopback or _lan(address)):
            raise ValueError
        return parsed, str(address), address.is_loopback
    except (ValueError, TypeError):
        raise ValueError('Use an HTTP(S) base URL ending /v1 or a full /chat/completions URL. '
                         'Hostnames require HTTPS. HTTP requires a numeric loopback/private LAN address. '
                         'Credentials, query, fragment and unsafe addresses are not allowed.') from None


# libc DNS resolution cannot be cancelled safely. Allow only one outstanding
# resolver thread, including after timeout, so slow DNS cannot accumulate work.
_DNS_GATE = threading.Lock()


def _resolve(host, port, check, deadline, *, gate=None):
    gate = _DNS_GATE if gate is None else gate
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        return family, (host, port)
    if not gate.acquire(blocking=False):
        raise RuntimeError('External vision DNS is still busy; try again after it finishes.')
    done = threading.Event()
    result = []

    def resolve():
        try:
            result.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            pass
        finally:
            gate.release()
            done.set()

    resolver = threading.Thread(target=resolve, daemon=True)
    try:
        resolver.start()
    except BaseException:
        gate.release()
        raise
    while not done.wait(.025):
        check(deadline)
    check(deadline)
    if not result or len(result) > 64:
        raise RuntimeError('External vision DNS returned no usable bounded address list.')
    for family, _, _, _, peer in result:
        if family not in (socket.AF_INET, socket.AF_INET6) or not _safe_address(ipaddress.ip_address(peer[0])):
            raise RuntimeError('External vision DNS returned an unsafe peer address; no connection made.')
    # Pin the socket to the checked numeric answer. No second resolution in HTTP/TLS.
    family, _, _, _, peer = next((entry for entry in result if entry[0] == socket.AF_INET), result[0])
    return family, peer


class ExternalVisionReader:
    engine = 'external-vision'

    def __init__(self, settings):
        self.settings = settings
        self.values, self.generation = settings.prepare()
        if not self.values['url']:
            raise ValueError('Configure the External vision endpoint in Settings first.')
        _, _, local = destination(self.values['url'])
        if not local and not self.values['acknowledged']:
            raise ValueError('Acknowledge name-image disclosure and possible costs in Settings first.')
        self.closed = threading.Event()
        self.lock = threading.Lock()
        log_event('recognition', 'external_started')

    def _valid(self):
        return not self.closed.is_set() and self.settings.is_current(self.generation)

    def _check(self, deadline):
        if not self._valid():
            raise RuntimeError('External vision cancelled or configuration changed.')
        if time.monotonic() >= deadline:
            raise RuntimeError('External vision deadline exceeded.')

    def warmup(self):
        # Starting live capture/import must not generate an extra paid request.
        self._check(time.monotonic() + 1)

    def _request(self, png, deadline):
        self._check(deadline)
        parsed, host, _ = destination(self.values['url'])
        payload = {'model': self.values['model'], 'temperature': 0, 'max_tokens': 96, 'stream': False,
                   'messages': [{'role': 'user', 'content': [
                       {'type': 'image_url', 'image_url': {
                           'url': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')}},
                       {'type': 'text', 'text': PROMPT}]}]}
        body = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.values['api_key']:
            headers['Authorization'] = 'Bearer ' + self.values['api_key']
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        family, peer = _resolve(host, port, self._check, deadline)
        self._check(deadline)
        connection = http.client.HTTPConnection(parsed.hostname, port)
        finished = threading.Event()
        watcher = None
        sockets = []
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.settimeout(min(2, max(.01, deadline - time.monotonic())))

            def interrupt():
                while not finished.wait(.025):
                    if not self._valid() or time.monotonic() >= deadline:
                        try:
                            sockets[-1].shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        # Keep watching through the raw-to-TLS socket handoff.

            watcher = threading.Thread(target=interrupt, daemon=True)
            watcher.start()
            self._check(deadline)
            sock.connect(peer)
            self._check(deadline)
            if parsed.scheme == 'https':
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=parsed.hostname, do_handshake_on_connect=False)
                sockets.append(sock)
                sock.settimeout(min(2, max(.01, deadline - time.monotonic())))
                sock.do_handshake()
            self._check(deadline)
            sock.settimeout(max(.01, deadline - time.monotonic()))
            # HTTPConnection formats the Host header from the original hostname;
            # the already-connected verified socket cannot perform DNS or proxy lookup.
            connection.sock = sock
            connection.request('POST', parsed.path, body=body, headers=headers)
            response = connection.getresponse()
            with response:
                if response.status != 200:
                    raise RuntimeError('External vision rejected the request; check endpoint, model and authentication.')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            self._check(deadline)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            value = json.loads(raw)
            choices = value.get('choices') if isinstance(value, dict) else None
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError
            choice = choices[0]
            message = choice.get('message')
            name = message.get('content') if isinstance(message, dict) else None
            if (choice.get('finish_reason') != 'stop' or not isinstance(name, str)
                    or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name)
                    or (self.values['api_key'] and self.values['api_key'] in name)):
                raise ValueError
            name.encode('utf-8')
            return {'name': name.strip(), 'confidence': 0.0}
        except (OSError, http.client.HTTPException, ValueError, TypeError, RecursionError):
            self._check(deadline)
            raise RuntimeError('External vision failed: unavailable, slow, incompatible or malformed response. '
                               'Check the server and vision model; no automatic retry was sent.') from None
        finally:
            finished.set()
            if watcher is not None:
                watcher.join()
            connection.close()
            for sock in reversed(sockets):
                sock.close()

    def recognize(self, images):
        import cv2

        if not isinstance(images, (list, tuple)) or not 1 <= len(images) <= 12:
            raise ValueError('External vision accepts 1 to 12 name crops per attempt.')
        with self.lock:
            deadline = time.monotonic() + ATTEMPT_TIMEOUT
            encoded = []
            # Validate every crop before sending any of the attempt.
            for image in images:
                self._check(deadline)
                if (not hasattr(image, 'ndim') or image.ndim != 3 or image.shape[2] != 3
                        or not 0 < image.shape[0] <= 512 or not 0 < image.shape[1] <= 2048
                        or image.dtype.name != 'uint8'):
                    raise ValueError('External vision requires bounded uint8 BGR name crops, not frames.')
                scale = min(1, 512 / image.shape[1], 128 / image.shape[0])
                if scale < 1:
                    image = cv2.resize(image, (max(1, round(image.shape[1] * scale)),
                                               max(1, round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
                success, png = cv2.imencode('.png', image)
                if not success or png.nbytes > MAX_PNG_BYTES:
                    raise ValueError('External vision PNG exceeds 256 KiB.')
                encoded.append(png.tobytes())
            try:
                rows = [self._request(png, min(deadline, time.monotonic() + REQUEST_TIMEOUT)) for png in encoded]
                log_event('recognition', 'result', row_count=len(rows))
                return rows
            except Exception as error:
                log_event('recognition', 'external_failed', error=error)
                self.close()  # Fail closed for later recognition frames, not an automatic retry loop.
                raise

    def close(self):
        if not self.closed.is_set():
            log_event('recognition', 'external_stopped')
        self.closed.set()  # The socket watcher interrupts I/O; never signal an external server.


def test_connection(settings, cancel_event=None, *, generation=None):
    import cv2
    import numpy as np

    image = np.full((64, 240, 3), 255, dtype=np.uint8)
    word = 'V' + secrets.token_hex(3).upper()
    cv2.putText(image, word, (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    reader = ExternalVisionReader(settings)
    if generation is not None and reader.generation != generation:
        reader.close()
        raise ValueError('External configuration changed; reload before testing.')
    if cancel_event is not None:
        reader.closed = cancel_event
    try:
        rows = reader.recognize([image])
        return {'ok': rows[0]['name'] == word, 'message': (
            'Synthetic name read correctly. Verify real names manually; this does not prove model accuracy.'
            if rows[0]['name'] == word else
            'Response received, but the synthetic name was not read correctly. Check vision support and model accuracy.')}
    finally:
        reader.close()
