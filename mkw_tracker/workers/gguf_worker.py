"""Local GGUF name transcription through a contained llama-server child."""

import argparse
import base64
import http.client
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

try:
    from ..name_engines import GGUF_PROFILE
    from .paddle_worker import MAX_IMAGE_BYTES, MAX_RESPONSE_BYTES, serve
except ImportError:  # Direct invocation by the contained worker interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mkw_tracker.name_engines import GGUF_PROFILE
    from mkw_tracker.workers.paddle_worker import MAX_IMAGE_BYTES, MAX_RESPONSE_BYTES, serve

PROMPT = ('Transcribe the single player name in this image exactly. It may contain Japanese kana, '
          'Latin letters, digits, Greek letters, punctuation, and symbols. Preserve case and every '
          'visible symbol. Return only the name, without explanation or quotation marks. '
          'Do not translate or normalize the name. Do not add spaces absent from the image.')


def runtime_command(model_dir, port, key_file, alias):
    """Use only the installed local runtime; never fetch models during capture."""
    model_dir = Path(model_dir)
    try:
        with (model_dir / 'runtime.json').open('rb') as source:
            data = source.read(16385)
        if len(data) > 16384:
            raise ValueError('runtime configuration exceeds size limit')
        config = json.loads(data)
        executable = Path(config['executable'])
        backend = config['backend']
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise RuntimeError('GGUF runtime is missing or invalid; rerun tools/setup_neural.py '
                           '--engine qwen-4b-gguf') from error
    if (not executable.is_absolute() or not executable.is_file()
            or not isinstance(backend, str) or backend not in {'cpu', 'vulkan'}):
        raise RuntimeError('GGUF runtime must name an installed executable and CPU or Vulkan backend; rerun setup')
    layers = config.get('gpu_layers')
    if layers is not None and (type(layers) is not int or not 0 <= layers <= 128):
        raise RuntimeError('GGUF gpu_layers must be an integer between 0 and 128')
    model = model_dir / GGUF_PROFILE['model_file']
    projector = model_dir / GGUF_PROFILE['projector_file']
    if not model.is_file() or not projector.is_file():
        raise RuntimeError('Local GGUF model or vision projector is missing; rerun setup for qwen-4b-gguf')
    command = [str(executable), '--model', str(model), '--mmproj', str(projector),
               '--host', '127.0.0.1', '--port', str(port), '--api-key-file', str(key_file), '--alias', alias,
               '--ctx-size', '4096', '--parallel', '1', '--no-webui', '--no-agent',
               '--cors-origins', 'localhost']
    if backend == 'cpu':
        command += ['-ngl', '0', '--device', 'none', '--no-mmproj-offload']
    else:
        # Advisory runtime fit margin, not a hard VRAM reservation or safety cap.
        command += ['-ngl', 'auto' if layers is None else str(layers), '--fit', 'on', '--fit-target', '4096']
        if layers is not None:
            command += ['--no-mmproj-offload']
    return command


def validated_images(request):
    from PIL import Image

    values = request.get('images') if isinstance(request, dict) else None
    if not isinstance(values, list) or not 1 <= len(values) <= 12:
        raise ValueError('images must contain between 1 and 12 PNG images')
    for value in values:
        if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError('Row image exceeds size limit')
        data = base64.b64decode(value, validate=True)
        if len(data) >= MAX_IMAGE_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Expected a bounded PNG row image')
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG' or image.height > 2048 or image.width > 8192:
                raise ValueError('Expected a bounded PNG row image')
            image.load()  # Reject truncated PNGs before sending anything to the runtime.
    return values


class GGUFWorker:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self.process = None
        self.port = None
        self.key = secrets.token_hex(32)
        self.alias = 'mkw-' + secrets.token_hex(16)

    def _request(self, path, payload=None, *, timeout=120):
        # Direct numeric-loopback connections never consult proxies or follow redirects.
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            body = None if payload is None else json.dumps(payload, ensure_ascii=True).encode('utf-8')
            connection.request('GET' if body is None else 'POST', path, body=body,
                               headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError('GGUF server response exceeds size limit')
            if response.status != 200:
                raise RuntimeError(f'GGUF server returned HTTP {response.status}')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise RuntimeError('GGUF server returned an invalid response')
            return result
        finally:
            connection.close()

    def _verify_identity(self):
        models = self._request('/v1/models', timeout=2).get('data')
        if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
                or models[0].get('id') != self.alias):
            raise RuntimeError('GGUF server identity did not match; no images were sent')

    def _start(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError('GGUF server exited; check the reader log or rerun setup with the CPU backend')
            return
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='mkw-gguf-key-') as temporary:
            key_file = Path(temporary) / 'api-key'
            if os.name == 'nt':
                from mkw_tracker.videos import _WindowsDirectory
                with _WindowsDirectory(temporary) as directory:
                    descriptor = directory.open(key_file.name, create=True, private=True)
            else:
                descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='ascii') as output:
                output.write(self.key)
            command = runtime_command(self.model_dir, self.port, key_file, self.alias)
            environment = {name: value for name, value in os.environ.items()
                           if not name.upper().startswith('LLAMA_ARG_') and name.upper() != 'LLAMA_API_KEY'}
            # Inherit the contained worker's process group / Windows Job. No
            # breakaway or new session. The API key never appears in argv/logs.
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                                            stderr=sys.stderr, env=environment)
            self._wait_ready()
        # The runtime has read its key; remove the temporary secret immediately.

    def _wait_ready(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('GGUF server exited while loading; check the reader log or choose CPU setup')
            try:
                self._request('/health', timeout=1)
            except (OSError, http.client.HTTPException, ValueError, RuntimeError):
                time.sleep(.1)
                continue
            self._verify_identity()
            return
        raise TimeoutError('GGUF server did not become ready within 180 seconds')

    def recognize(self, request):
        images = validated_images(request)
        self._start()
        # Authentication and a per-process random alias close the released-port race.
        # Health alone is public and is not proof that this is our model server.
        self._verify_identity()
        rows = []
        for value in images:
            if self.process.poll() is not None:
                raise RuntimeError('GGUF server exited before transcription')
            result = self._request('/v1/chat/completions', {
                'model': self.alias, 'temperature': 0, 'max_tokens': 96, 'stream': False,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + value}},
                    {'type': 'text', 'text': PROMPT}]}],
            })
            choices = result.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise RuntimeError('GGUF server returned invalid transcription choices')
            message = choices[0].get('message')
            name = message.get('content') if isinstance(message, dict) else None
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError('GGUF server returned an empty or invalid name')
            if choices[0].get('finish_reason') != 'stop':
                raise RuntimeError('GGUF transcription did not finish; baseline names remain available for review')
            rows.append({'name': name.strip(), 'confidence': 0.0})
        return {'rows': rows, 'engine': GGUF_PROFILE['engine'], 'confidence_kind': 'unavailable'}

    def close(self):
        process = self.process
        if process is None:
            return
        # Only this exact Popen-owned child is touched. Outer transport retains
        # responsibility for its whole contained tree on cancellation/timeouts.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True)
    arguments = parser.parse_args()
    worker = GGUFWorker(arguments.model_dir)
    try:
        serve(worker, sys.stdin, sys.stdout)
    finally:
        worker.close()


if __name__ == '__main__':
    main()
