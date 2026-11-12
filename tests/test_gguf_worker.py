"""GGUF protocol tests use fake files, HTTP and process handles; no models run."""

import base64
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from mkw_tracker.name_engines import GGUF_PROFILE
from mkw_tracker.workers import gguf_worker as gguf


def png():
    output = io.BytesIO()
    Image.new('RGB', (80, 20)).save(output, format='PNG')
    return base64.b64encode(output.getvalue()).decode('ascii')


class GGUFWorkerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')))
        self.stack.enter_context(patch('os.kill', side_effect=AssertionError('Unexpected signal')))
        if hasattr(os, 'killpg'):
            self.stack.enter_context(patch('os.killpg', side_effect=AssertionError('Unexpected group signal')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / 'Tracker 日本語'
        self.root.mkdir()
        self.worker = gguf.GGUFWorker(self.root)

    def runtime(self, backend='cpu'):
        executable = self.root / 'llama server.exe'
        executable.touch()
        for key in ('model_file', 'projector_file'):
            (self.root / GGUF_PROFILE[key]).touch()
        (self.root / 'runtime.json').write_text(json.dumps({'executable': str(executable), 'backend': backend}))
        return executable

    def ready(self):
        self.worker.process = Mock()
        self.worker.process.poll.return_value = None
        self.stack.enter_context(patch.object(self.worker, '_start'))
        self.stack.enter_context(patch.object(self.worker, '_verify_identity'))

    def test_cpu_command_is_local_loopback_and_disables_gpu(self):
        executable = self.runtime()
        command = gguf.runtime_command(self.root, 49100, 'private-key', 'unique-model')
        self.assertEqual(command[0], str(executable))
        for flag, value in (('--host', '127.0.0.1'), ('--port', '49100'), ('--api-key-file', 'private-key'),
                            ('--alias', 'unique-model'), ('--ctx-size', '4096'), ('--parallel', '1'),
                            ('--device', 'none'), ('-ngl', '0'), ('--cors-origins', 'localhost')):
            self.assertEqual(command[command.index(flag) + 1], value)
        for flag in ('--no-mmproj-offload', '--no-webui', '--no-agent'):
            self.assertIn(flag, command)
        self.assertNotIn('--hf-repo', command)

    def test_vulkan_command_uses_auto_offload(self):
        self.runtime('vulkan')
        command = gguf.runtime_command(self.root, 49100, 'key', 'alias')
        self.assertEqual(command[command.index('-ngl') + 1], 'auto')
        self.assertNotIn('--device', command)
        self.assertNotIn('--no-mmproj-offload', command)
        self.assertEqual(command[command.index('--fit-target') + 1], '4096')

    def test_explicit_vulkan_layer_budget_is_bounded_and_keeps_vision_on_cpu(self):
        self.runtime('vulkan')
        path = self.root / 'runtime.json'
        config = json.loads(path.read_text())
        config['gpu_layers'] = 4
        path.write_text(json.dumps(config))
        command = gguf.runtime_command(self.root, 49100, 'key-file', 'alias')
        self.assertEqual(command[command.index('-ngl') + 1], '4')
        self.assertIn('--no-mmproj-offload', command)
        for value in (True, -1, 129, '4', 4.5):
            config['gpu_layers'] = value
            path.write_text(json.dumps(config))
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'gpu_layers'):
                gguf.runtime_command(self.root, 49100, 'key-file', 'alias')

    def test_invalid_or_missing_runtime_and_weights_fail_before_launch(self):
        for data in ('{}', '[]', 'invalid', json.dumps({'executable': 'relative.exe', 'backend': 'cpu'}),
                     json.dumps({'executable': '/missing/server', 'backend': 'cuda'}), ' ' * 16385):
            with self.subTest(data=data[:80]):
                (self.root / 'runtime.json').write_text(data)
                with self.assertRaises(RuntimeError):
                    gguf.runtime_command(self.root, 1, 'key', 'alias')
        self.runtime()
        (self.root / GGUF_PROFILE['projector_file']).unlink()
        with self.assertRaisesRegex(RuntimeError, 'projector'):
            gguf.runtime_command(self.root, 1, 'key', 'alias')

    def test_all_images_validate_before_server_start(self):
        for request in ({}, {'images': []}, {'images': [png()] * 13}, {'images': [None]},
                        {'images': ['not base64']}, {'images': [png(), base64.b64encode(b'not PNG').decode()]}):
            with self.subTest(request=str(request)[:80]), patch.object(self.worker, '_start') as start:
                with self.assertRaises((ValueError, TypeError)):
                    self.worker.recognize(request)
                start.assert_not_called()

    def test_image_dimensions_are_bounded(self):
        data = io.BytesIO()
        Image.new('L', (8193, 1)).save(data, format='PNG')
        with self.assertRaisesRegex(ValueError, 'bounded PNG'):
            gguf.validated_images({'images': [base64.b64encode(data.getvalue()).decode()]})

    def test_unicode_transcription_preserves_symbols_and_marks_confidence_unavailable(self):
        self.ready()
        response = {'choices': [{'message': {'content': '  てがすべってdc☆ α  '}, 'finish_reason': 'stop'}]}
        with patch.object(self.worker, '_request', return_value=response) as request:
            result = self.worker.recognize({'images': [png(), png()]})
        self.assertEqual(result['rows'], [{'name': 'てがすべってdc☆ α', 'confidence': 0.0}] * 2)
        self.assertEqual(result['engine'], GGUF_PROFILE['engine'])
        self.assertEqual(result['confidence_kind'], 'unavailable')
        self.assertEqual(request.call_count, 2)
        body = request.call_args.args[1]
        self.assertEqual(body['model'], self.worker.alias)
        self.assertEqual((body['temperature'], body['max_tokens'], body['stream']), (0, 96, False))
        self.assertTrue(body['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertIn('Do not translate or normalize', body['messages'][0]['content'][1]['text'])

    def test_identity_mismatch_never_sends_pixels(self):
        self.worker.process = Mock()
        self.worker.process.poll.return_value = None
        with patch.object(self.worker, '_start'), \
                patch.object(self.worker, '_request', return_value={'data': [{'id': 'somebody-else'}]}) as request:
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                self.worker.recognize({'images': [png()]})
        request.assert_called_once_with('/v1/models', timeout=2)

    def test_models_authentication_failure_never_sends_pixels(self):
        with patch.object(self.worker, '_start'), \
                patch.object(self.worker, '_request', side_effect=RuntimeError('HTTP 401')) as request:
            with self.assertRaisesRegex(RuntimeError, '401'):
                self.worker.recognize({'images': [png()]})
        request.assert_called_once_with('/v1/models', timeout=2)

    def test_dead_server_and_invalid_or_truncated_names_fail_closed(self):
        self.ready()
        self.worker.process.poll.return_value = 1
        with patch.object(self.worker, '_request') as request, self.assertRaisesRegex(RuntimeError, 'exited'):
            self.worker.recognize({'images': [png()]})
        request.assert_not_called()
        self.worker.process.poll.return_value = None
        for response in ({}, {'choices': []}, {'choices': [{'message': {'content': 'name'}, 'finish_reason': 'length'}]},
                         {'choices': [{'message': {'content': ''}, 'finish_reason': 'stop'}]}):
            with self.subTest(response=response), patch.object(self.worker, '_request', return_value=response):
                with self.assertRaises(RuntimeError):
                    self.worker.recognize({'images': [png()]})

    def test_http_is_direct_authenticated_bounded_and_does_not_follow_redirects(self):
        self.worker.port = 49100
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"data": []}'
        with patch.object(gguf.http.client, 'HTTPConnection', return_value=connection) as factory:
            self.assertEqual(self.worker._request('/v1/models'), {'data': []})
        factory.assert_called_once_with('127.0.0.1', 49100, timeout=120)
        self.assertEqual(connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer ' + self.worker.key)
        response.read.assert_called_once_with(gguf.MAX_RESPONSE_BYTES + 1)
        connection.close.assert_called_once()
        for status, data in ((302, b'{}'), (200, b'x' * (gguf.MAX_RESPONSE_BYTES + 1)),
                             (200, b'[]'), (200, b'not json')):
            response.status, response.read.return_value = status, data
            with self.subTest(status=status, size=len(data)), \
                    patch.object(gguf.http.client, 'HTTPConnection', return_value=connection):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.worker._request('/v1/models')

    def test_start_inherits_containment_and_sanitizes_runtime_environment(self):
        self.runtime()
        process = Mock()
        process.poll.return_value = None
        listener = Mock()
        listener.__enter__ = Mock(return_value=listener)
        listener.__exit__ = Mock(return_value=False)
        listener.getsockname.return_value = ('127.0.0.1', 49100)
        with patch.object(gguf.socket, 'socket', return_value=listener), \
                patch.object(gguf.subprocess, 'Popen', return_value=process) as launch, \
                patch.dict(os.environ, {'LLAMA_ARG_HOST': '0.0.0.0', 'llama_arg_port': '42',
                                        'LLAMA_API_KEY': 'inherited', 'PATH': 'keep'}, clear=True), \
                patch.object(self.worker, '_request', return_value={'status': 'ok'}), \
                patch.object(self.worker, '_verify_identity') as verify:
            self.worker._start()
        self.assertEqual(launch.call_args.kwargs['env'], {'PATH': 'keep'})
        self.assertNotIn('start_new_session', launch.call_args.kwargs)
        self.assertNotIn('creationflags', launch.call_args.kwargs)
        self.assertIs(launch.call_args.kwargs['stdout'], gguf.sys.stderr)
        verify.assert_called_once()
        self.assertIs(self.worker.process, process)
        command = launch.call_args.args[0]
        self.assertNotIn(self.worker.key, command)
        key_path = Path(command[command.index('--api-key-file') + 1])
        self.assertFalse(key_path.exists())

    def test_close_only_uses_owned_process_handle_and_is_idempotent(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('owned-child', 5), 0]
        self.worker.process = process
        self.worker.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(self.worker.process)
        self.worker.close()
        process.terminate.assert_called_once()

    def test_jsonl_errors_remain_ascii_safe_and_recover_for_next_request(self):
        worker = Mock()
        worker.recognize.side_effect = [ValueError('bad input'), {'rows': [{'name': '日本☆', 'confidence': 0.0}]}]
        output = io.StringIO()
        gguf.serve(worker, io.StringIO('{}\n{}\n'), output)
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0]), {'error': 'bad input'})
        self.assertEqual(json.loads(lines[1])['rows'][0]['name'], '日本☆')
        self.assertTrue(output.getvalue().isascii())


FAKE_SERVER = r"""
import http.server, json, os, pathlib, sys
arguments = sys.argv[1:]
def argument(name):
    return arguments[arguments.index(name) + 1]
key = pathlib.Path(argument('--api-key-file')).read_text()
alias = argument('--alias')
root = pathlib.Path(argument('--model')).parent
(root / 'child.pid').write_text(str(os.getpid()))
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + key:
            self.send_error(401)
            return
        self.send_response(200)
        self.end_headers()
        data = {'data': [{'id': alias}]} if self.path == '/v1/models' else {'status': 'ok'}
        self.wfile.write(json.dumps(data).encode())
    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer ' + key:
            self.send_error(401)
            return
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert request['model'] == alias
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'choices': [{'message': {'content': '\u65e5\u672c star'},
                                                'finish_reason': 'stop'}]}).encode())
http.server.HTTPServer(('127.0.0.1', int(argument('--port'))), Handler).serve_forever()
"""


def isolated_pid_namespace():
    try:
        return (os.environ.get('HOST_PID_NS') is not None
                and os.readlink('/proc/self/ns/pid') != os.environ['HOST_PID_NS']
                and Path('/proc/1/comm').read_text().strip() == 'sh')
    except OSError:
        return False


@unittest.skipUnless(isolated_pid_namespace(), 'requires independently verified separate PID namespace')
class GGUFContainedIntegrationTests(unittest.TestCase):
    def test_worker_protocol_and_descendant_cleanup_with_fake_server(self):
        import numpy as np
        from mkw_tracker.neural_ocr import NeuralNameReader

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'fake-llama-server'
            executable.write_text('#!' + gguf.sys.executable + '\n' + FAKE_SERVER)
            executable.chmod(0o700)
            for key in ('model_file', 'projector_file'):
                (root / GGUF_PROFILE[key]).touch()
            (root / 'runtime.json').write_text(json.dumps({'executable': str(executable), 'backend': 'cpu'}))
            reader = NeuralNameReader(GGUF_PROFILE['engine'], model_dir=root, timeout=10)
            try:
                result = reader.recognize([np.zeros((20, 80, 3), dtype=np.uint8)])
                self.assertEqual(result, [{'name': '日本 star', 'confidence': 0.0}])
                child_pid = int((root / 'child.pid').read_text())
                worker_pid = reader._process.pid
                self.assertEqual(os.getpgid(child_pid), worker_pid)
                self.assertEqual(os.getsid(child_pid), worker_pid)
                self.assertEqual(reader.recognize([np.zeros((20, 80, 3), dtype=np.uint8)]), result)
                self.assertEqual(int((root / 'child.pid').read_text()), child_pid)
            finally:
                reader.close()
            deadline = time.monotonic() + 2
            state = Path(f'/proc/{child_pid}/stat')
            while state.exists() and time.monotonic() < deadline:
                if state.read_text().split(') ', 1)[1].startswith('Z '):
                    break
                time.sleep(.01)
            self.assertTrue(not state.exists() or state.read_text().split(') ', 1)[1].startswith('Z '))
            self.assertIsNone(reader._process)


if __name__ == '__main__':
    unittest.main()
