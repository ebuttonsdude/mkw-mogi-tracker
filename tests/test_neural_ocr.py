import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from mkw_tracker.diagnostics import configure_logging
from mkw_tracker.neural_ocr import NeuralNameReader, make_name_reader
from mkw_tracker.name_engines import GGUF_PROFILE, NAME_ENGINES, QWEN_PROFILES


GOOD_WORKER = '''
import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'rows': [{'name': '  てがすべってdc☆' + str(os.getpid()) + '  ',
          'confidence': .9} for _ in request['images']]}, ensure_ascii=True), flush=True)
'''
VENV_PYTHON = 'Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'


class NeuralOCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'worker.py'
        self.script.write_text(GOOD_WORKER, encoding='utf-8')
        self.reader = NeuralNameReader(python_path=sys.executable, model_dir=self.root, timeout=2)
        self.reader._script = self.script
        self.images = [np.zeros((12, 80, 3), dtype=np.uint8)]

    def tearDown(self):
        self.reader.close()
        self.temp.cleanup()

    def install_name_readers(self):
        for filename in (f'.local/paddle-venv/{VENV_PYTHON}',
                         '.local/models/paddle-ppocrv5/inference.json',
                         f'.local/vlm-venv/{VENV_PYTHON}', '.local/models/qwen3-vl-8b/config.json'):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_auto_does_not_choose_installed_nf4_profile(self):
        profile = QWEN_PROFILES['qwen-4b-nf4']
        for filename in (f".local/{profile['environment']}/{VENV_PYTHON}",
                         f".local/models/{profile['model_dir']}/config.json"):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        with patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
            self.assertIsNone(make_name_reader('auto'))
            probe.assert_not_called()

    def test_auto_prefers_qwen_with_enough_free_memory_even_when_paddle_is_installed(self):
        self.install_name_readers()
        for free_memory, expected in [('24576\n', 'qwen'), ('22528\n', 'qwen'),
                                      ('22527\n', 'paddle'), ('8192\n', 'paddle')]:
            with self.subTest(free_memory=free_memory), \
                    patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                    patch('mkw_tracker.neural_ocr.subprocess.run',
                          return_value=SimpleNamespace(stdout=free_memory)) as probe:
                reader = make_name_reader('auto')
                self.addCleanup(reader.close)
                self.assertEqual(reader.engine, expected)
                self.assertIsNone(reader._process)
                probe.assert_called_once_with(
                    ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=3, check=True)

    def test_auto_falls_back_when_gpu_probe_is_unavailable_or_invalid(self):
        self.install_name_readers()
        failures = [FileNotFoundError('nvidia-smi'), subprocess.CalledProcessError(1, 'nvidia-smi'),
                    subprocess.TimeoutExpired('nvidia-smi', 3)]
        results = [SimpleNamespace(stdout=value) for value in ('', '\n', 'N/A\n', 'not a number\n')]
        for outcome in failures + results:
            with self.subTest(outcome=outcome), \
                    patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                    patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
                if isinstance(outcome, Exception):
                    probe.side_effect = outcome
                else:
                    probe.return_value = outcome
                reader = make_name_reader('auto')
                self.addCleanup(reader.close)
                self.assertEqual(reader.engine, 'paddle')
                self.assertIsNone(reader._process)

    def test_auto_requires_both_qwen_environment_and_model_before_probing_gpu(self):
        self.install_name_readers()
        for filename in (f'.local/vlm-venv/{VENV_PYTHON}', '.local/models/qwen3-vl-8b/config.json'):
            path = self.root / filename
            path.unlink()
            try:
                with self.subTest(missing=filename), \
                        patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                        patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
                    reader = make_name_reader('auto')
                    self.addCleanup(reader.close)
                    self.assertEqual(reader.engine, 'paddle')
                    probe.assert_not_called()
            finally:
                path.touch()

    def test_auto_uses_tesseract_without_installed_readers_or_sufficient_gpu(self):
        with patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
            self.assertIsNone(make_name_reader('auto'))
            probe.assert_not_called()
            self.install_name_readers()
            (self.root / f'.local/paddle-venv/{VENV_PYTHON}').unlink()
            probe.return_value = SimpleNamespace(stdout='8192\n')
            self.assertIsNone(make_name_reader('auto'))
            (self.root / f'.local/paddle-venv/{VENV_PYTHON}').touch()
            (self.root / '.local/models/paddle-ppocrv5/inference.json').unlink()
            self.assertIsNone(make_name_reader('auto'))

    def test_explicit_engine_choices_skip_automatic_selection(self):
        for engine in (*QWEN_PROFILES, 'paddle', 'tesseract'):
            with self.subTest(engine=engine), \
                    patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root), \
                    patch('mkw_tracker.neural_ocr.subprocess.run') as probe:
                reader = make_name_reader(engine)
                if engine == 'tesseract':
                    self.assertIsNone(reader)
                else:
                    self.addCleanup(reader.close)
                    self.assertEqual(reader.engine, engine)
                    self.assertIsNone(reader._process)
                probe.assert_not_called()

    def test_qwen_profile_paths_are_distinct_environments_with_shared_8b_weights(self):
        self.assertEqual(NAME_ENGINES, {'auto', 'tesseract', 'paddle', *QWEN_PROFILES, GGUF_PROFILE['engine'], 'external-vision'})
        for engine, profile in QWEN_PROFILES.items():
            with self.subTest(engine=engine), patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root):
                reader = make_name_reader(engine)
                self.addCleanup(reader.close)
                self.assertEqual(reader.python_path, self.root / '.local' / profile['environment'] / VENV_PYTHON)
                self.assertEqual(reader.model_dir, self.root / '.local/models' / profile['model_dir'])
                self.assertEqual(reader._script.name, 'qwen_worker.py')

    def test_qwen_profile_is_passed_to_persistent_worker_without_changing_images(self):
        for engine in QWEN_PROFILES:
            with self.subTest(engine=engine):
                reader = NeuralNameReader(engine, python_path=sys.executable, model_dir=self.root, timeout=2)
                reader._script = self.script
                self.addCleanup(reader.close)
                names = reader.recognize(self.images)
                self.assertTrue(names[0]['name'].startswith('て'))
                command = reader._process.args
                if engine == 'qwen':
                    self.assertNotIn('--profile', command)
                else:
                    self.assertEqual(command[-2:], ['--profile', engine])
                reader.close()

    def test_missing_quantized_environment_explains_explicit_setup(self):
        with patch('mkw_tracker.neural_ocr.Path.cwd', return_value=self.root):
            reader = make_name_reader('qwen-nvfp4')
        self.addCleanup(reader.close)
        with patch('mkw_tracker.neural_ocr.subprocess.Popen',
                   side_effect=AssertionError('unexpected worker launch')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'setup_neural.py --engine qwen-nvfp4'):
                reader.recognize(self.images)
            launch.assert_not_called()

    def test_unicode_and_two_requests_reuse_process(self):
        first = self.reader.recognize(self.images)
        process = self.reader._process
        second = self.reader.recognize(self.images)
        self.assertIs(self.reader._process, process)
        self.assertEqual(first, second)
        prefix = 'てがすべってdc☆'
        self.assertTrue(first[0]['name'].startswith(prefix))
        worker_pid = int(first[0]['name'][len(prefix):])
        worker_handle = None
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            worker_handle = kernel.OpenProcess(0x00100000, False, worker_pid)
            self.assertTrue(worker_handle)
            self.addCleanup(kernel.CloseHandle, worker_handle)
        else:
            self.assertEqual(worker_pid, process.pid)
        self.reader.close()
        if worker_handle:
            self.assertEqual(kernel.WaitForSingleObject(worker_handle, 2000), 0)
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stdin.closed)
        self.assertIsNone(self.reader._log)

    def test_close_interrupts_in_flight_model_without_orphan_process(self):
        started = self.root / 'started'
        self.script.write_text('import sys,time,pathlib\nsys.stdin.readline()\n'
                               f'pathlib.Path({str(started)!r}).touch()\ntime.sleep(30)\n', encoding='utf-8')
        self.reader.timeout = 30
        errors = []
        def recognize():
            try:
                self.reader.recognize(self.images)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=recognize)
        thread.start()
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(started.exists())
        process = self.reader._process
        self.reader.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(process.poll())
        self.assertTrue(errors)

    def test_partial_response_times_out_and_next_call_restarts(self):
        self.script.write_text('import sys,time\nsys.stdin.readline()\nsys.stdout.write("{")\n'
                               'sys.stdout.flush()\ntime.sleep(30)\n', encoding='utf-8')
        self.reader.timeout = .2
        with self.assertRaisesRegex(TimeoutError, 'timed out'):
            self.reader.recognize(self.images)
        self.assertIsNone(self.reader._process)
        self.script.write_text(GOOD_WORKER, encoding='utf-8')
        self.reader.timeout = 2
        self.assertTrue(self.reader.recognize(self.images)[0]['name'].startswith('て'))

    def test_protocol_errors_close_worker(self):
        responses = ['not JSON', '[]', '{}', json.dumps({'error': 'model unavailable'}),
                     json.dumps({'rows': [{'name': '', 'confidence': .8}]}),
                     json.dumps({'rows': [{'name': 'name', 'confidence': float('nan')}]}),
                     json.dumps({'rows': [{'name': 'name', 'confidence': True}]}),
                     json.dumps({'rows': [{'name': 5, 'confidence': .8}]})]
        for response in responses:
            with self.subTest(response=response):
                self.script.write_text('import sys\nsys.stdin.readline()\nprint(' + repr(response) + ', flush=True)\n',
                                       encoding='utf-8')
                with self.assertRaises((ValueError, RuntimeError)):
                    self.reader.recognize(self.images)
                self.assertIsNone(self.reader._process)
                self.assertIsNone(self.reader._log)

    def test_eof_and_dead_process_restart(self):
        self.script.write_text('import sys\nsys.stdin.readline()\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            self.reader.recognize(self.images)
        self.script.write_text(GOOD_WORKER, encoding='utf-8')
        self.reader.recognize(self.images)
        old_process = self.reader._process
        old_process.kill()
        old_process.wait()
        self.reader.recognize(self.images)
        self.assertNotEqual(old_process.pid, self.reader._process.pid)

    def test_bad_inputs_do_not_start_process(self):
        for images in ([], [None], self.images * 13, [np.zeros((10, 10))]):
            with self.subTest(count=len(images)), self.assertRaises(ValueError):
                self.reader.recognize(images)
        self.assertIsNone(self.reader._process)
        for options in ({'engine': 'unknown'}, {'timeout': 0}, {'timeout': float('inf')}):
            with self.assertRaises(ValueError):
                NeuralNameReader(**options)

    def test_raw_worker_diagnostics_are_discarded_and_safe_events_logged(self):
        secret = 'Bearer private-token /home/private/model data:image/png;base64,AAAA'
        self.script.write_text('import sys\nprint(' + repr(secret) + ', file=sys.stderr, flush=True)\n' + GOOD_WORKER,
                               encoding='utf-8')
        stdout = io.StringIO()
        session = configure_logging(self.root, verbose=True, stdout=stdout)
        try:
            self.reader.recognize(self.images)
            self.reader.close()
            text = (self.root / 'logs/recognition.log').read_text()
            self.assertIn('Name-reader worker started', text)
            self.assertIn('Name-reader batch processed row_count=1', text)
            self.assertIn('Name-reader worker stopped', text)
            self.assertFalse((self.root / 'logs/paddle-ocr.log').exists())
            for log in (self.root / 'logs').glob('*.log'):
                self.assertNotIn(secret, log.read_text())
            self.assertNotIn(secret, stdout.getvalue())
        finally:
            session.close()


class GGUFReaderSelectionTests(unittest.TestCase):
    def test_explicit_gguf_uses_current_python_and_longer_bounded_default(self):
        with patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')):
            reader = make_name_reader('qwen-4b-gguf')
            self.assertEqual(reader.python_path, Path(sys.executable))
            self.assertEqual(reader._script.name, 'gguf_worker.py')
            self.assertEqual(reader.model_dir.name, GGUF_PROFILE['model_dir'])
            self.assertEqual(reader.timeout, 300)
            self.assertIsNone(reader._process)
            self.assertEqual(NeuralNameReader('qwen-4b-gguf', timeout=12).timeout, 12)
            self.assertEqual(NeuralNameReader('paddle').timeout, 120)
            for timeout in (None, True, 0, float('inf')):
                with self.assertRaises(ValueError):
                    NeuralNameReader('qwen-4b-gguf', timeout=timeout)

    def test_automatic_does_not_select_gguf_or_probe_its_backend(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('mkw_tracker.neural_ocr.Path.cwd', return_value=Path(directory)), \
                patch('subprocess.Popen', side_effect=AssertionError('Unexpected process launch')), \
                patch('subprocess.run', side_effect=AssertionError('Unexpected hardware probe')):
            model = Path(directory) / '.local/models' / GGUF_PROFILE['model_dir']
            model.mkdir(parents=True)
            (model / 'runtime.json').write_text('{}')
            self.assertIsNone(make_name_reader('auto'))


if __name__ == '__main__':
    unittest.main()
